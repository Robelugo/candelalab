"""Operadores de CandelaLab."""

import atexit
import json
import os
import tempfile
import time
from dataclasses import dataclass

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import build
from .core import ccx
from .core.export import build_document, cutlist_csv
from .core.fea import analysis_steps, fe_model_from_params, to_inp
from .core.frd import results_key, section_csv
from .core.pipeline import evaluate
from .core.radial import PRESETS as RADIAL_PRESETS
from .core.grid import PRESETS as GRID_PRESETS
from .core.umbrella import PRESETS as UMBRELLA_PRESETS
from .props import apply_dict, preferences, settings_to_dict, updates_suspended


def _active_shell(context):
    obj = context.active_object
    return obj if build.is_shell(obj) else None


class _ShellOperator:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_shell(context) is not None


class CANDELA_OT_add_hypar(bpy.types.Operator):
    """Crear un paraboloide hiperbólico paramétrico en el cursor 3D"""
    bl_idname = "candela.add_hypar"
    bl_label = "Paraboloide hiperbólico"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = build.create_shell(context)
        rep = build.report_of(obj)
        if "error" in rep:
            self.report({'ERROR'}, rep["error"])
            return {'CANCELLED'}
        return {'FINISHED'}


class CANDELA_OT_add_radial(bpy.types.Operator):
    """Crear una bóveda radial (cúpula) de N paraboloides hiperbólicos en el cursor 3D"""
    bl_idname = "candela.add_radial"
    bl_label = "Bóveda radial (cúpula)"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Tipo", items=[(k, v[0], v[1]) for k, v in RADIAL_PRESETS.items()], default='DOME')

    def execute(self, context):
        params = {"shell_type": "RADIAL", **RADIAL_PRESETS[self.preset][2]}
        obj = build.create_shell(context, name="CandelaLab Bóveda", params=params)
        rep = build.report_of(obj)
        if "error" in rep:
            self.report({'ERROR'}, rep["error"])
            return {'CANCELLED'}
        return {'FINISHED'}


class CANDELA_OT_radial_preset(_ShellOperator, bpy.types.Operator):
    """Aplicar un punto de partida a la bóveda radial activa"""
    bl_idname = "candela.radial_preset"
    bl_label = "Aplicar tipo"

    preset: EnumProperty(name="Tipo", items=[(k, v[0], v[1]) for k, v in RADIAL_PRESETS.items()])

    def execute(self, context):
        obj = _active_shell(context)
        apply_dict(obj.candela, {"shell_type": "RADIAL", **RADIAL_PRESETS[self.preset][2]})
        build.rebuild(obj)
        return {'FINISHED'}


class CANDELA_OT_add_umbrella(bpy.types.Operator):
    """Crear un paraguas de cuatro paraboloides hiperbólicos sobre una columna, en el cursor 3D"""
    bl_idname = "candela.add_umbrella"
    bl_label = "Paraguas (4 hypars)"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Tipo", items=[(k, v[0], v[1]) for k, v in UMBRELLA_PRESETS.items()],
                         default='INVERTED')

    def execute(self, context):
        params = {"shell_type": "UMBRELLA", "res_x": 12, "res_y": 12, **UMBRELLA_PRESETS[self.preset][2]}
        obj = build.create_shell(context, name="CandelaLab Paraguas", params=params)
        rep = build.report_of(obj)
        if "error" in rep:
            self.report({'ERROR'}, rep["error"])
            return {'CANCELLED'}
        return {'FINISHED'}


class CANDELA_OT_umbrella_preset(_ShellOperator, bpy.types.Operator):
    """Aplicar un punto de partida al paraguas activo"""
    bl_idname = "candela.umbrella_preset"
    bl_label = "Aplicar tipo"

    preset: EnumProperty(name="Tipo", items=[(k, v[0], v[1]) for k, v in UMBRELLA_PRESETS.items()])

    def execute(self, context):
        obj = _active_shell(context)
        apply_dict(obj.candela, {"shell_type": "UMBRELLA", **UMBRELLA_PRESETS[self.preset][2]})
        build.rebuild(obj)
        return {'FINISHED'}


GRID_MODULE = {"shell_type": "UMBRELLA", "res_x": 8, "res_y": 8, "umbrella_column": True,
               "support_type": "COLUMN", "support_size": 0.4}


class CANDELA_OT_add_umbrella_grid(bpy.types.Operator):
    """Crear una retícula modular de paraguas en el cursor 3D"""
    bl_idname = "candela.add_umbrella_grid"
    bl_label = "Retícula de paraguas"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Tipo", items=[(k, v[0], v[1]) for k, v in GRID_PRESETS.items()],
                         default='INVERTED')

    def execute(self, context):
        obj = build.create_shell(context, name="CandelaLab Retícula",
                                 params={**GRID_MODULE, **GRID_PRESETS[self.preset][2]})
        rep = build.report_of(obj)
        if "error" in rep:
            self.report({'ERROR'}, rep["error"])
            return {'CANCELLED'}
        return {'FINISHED'}


class CANDELA_OT_grid_preset(_ShellOperator, bpy.types.Operator):
    """Convertir el cascarón activo en una retícula de paraguas del tipo elegido"""
    bl_idname = "candela.grid_preset"
    bl_label = "Aplicar retícula"

    preset: EnumProperty(name="Tipo", items=[(k, v[0], v[1]) for k, v in GRID_PRESETS.items()])

    def execute(self, context):
        obj = _active_shell(context)
        params = dict(GRID_PRESETS[self.preset][2], shell_type="UMBRELLA")
        if obj.candela.shell_type != 'UMBRELLA':  # al cambiar de tipo, también el módulo de partida
            params = {**GRID_MODULE, **params}
        apply_dict(obj.candela, params)
        build.rebuild(obj)
        return {'FINISHED'}


class CANDELA_OT_rebuild(_ShellOperator, bpy.types.Operator):
    """Regenerar la geometría a partir de los parámetros"""
    bl_idname = "candela.rebuild"
    bl_label = "Regenerar"

    def execute(self, context):
        obj = _active_shell(context)
        if build.rebuild(obj) is None:
            self.report({'ERROR'}, build.report_of(obj).get("error", "Error al regenerar"))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Regenerado en {build.report_of(obj)['timing_ms']:.0f} ms")
        return {'FINISHED'}


class CANDELA_OT_fit_ground(_ShellOperator, bpy.types.Operator):
    """Desplazar verticalmente el cascarón para que su punto más bajo de concreto toque el nivel de desplante"""
    bl_idname = "candela.fit_ground"
    bl_label = "Apoyar en desplante"

    def execute(self, context):
        obj = _active_shell(context)
        s = obj.candela
        rep = build.report_of(obj)
        if "geometry" not in rep:
            return {'CANCELLED'}
        delta = rep["geometry"]["z_concrete_min"] - s.ground_level
        with updates_suspended():
            if s.shell_type == 'RADIAL':
                s.crown_height -= delta
                s.edge_height -= delta
                s.support_height -= delta
            elif s.shell_type == 'UMBRELLA':
                s.umbrella_center_height -= delta
                s.umbrella_edge_height -= delta
            elif s.definition == 'CORNERS':
                s.z_sw -= delta
                s.z_se -= delta
                s.z_ne -= delta
                s.z_nw -= delta
            else:
                s.rise -= delta
        build.rebuild(obj)
        return {'FINISHED'}


class CANDELA_OT_freeze(_ShellOperator, bpy.types.Operator):
    """Convertir en malla estática: elimina el vínculo paramétrico, las generatrices y las normales.
Vigas, nervios, apoyos y cimbra se conservan como objetos independientes"""
    bl_idname = "candela.freeze"
    bl_label = "Congelar como malla"

    def execute(self, context):
        obj = _active_shell(context)
        if build.report_of(obj).get("grid", {}).get("instanced"):
            with updates_suspended():  # congelar = geometría real de toda la retícula
                obj.candela.grid_instancing = 'REAL'
            build.rebuild(obj)
        build.remove_child(obj, build.ROLE_PLACEMENTS)
        build.remove_child(obj, build.ROLE_RULINGS)
        build.remove_child(obj, build.ROLE_NORMALS)
        build.remove_child(obj, build.ROLE_DEFORMED)
        build.remove_child(obj, build.ROLE_DIRECTIONS)
        for role in (build.ROLE_SUPPORTS, build.ROLE_BEAMS, build.ROLE_RIBS, build.ROLE_FORMWORK):
            child = build.find_child(obj, role)
            if child is not None:
                mw = child.matrix_world.copy()
                child.parent = None
                child.matrix_world = mw
                child.hide_select = False
                del child[build.ROLE]
        with updates_suspended():
            obj.candela.is_shell = False
            obj.candela.report_json = ""
        return {'FINISHED'}


class CANDELA_OT_delete(_ShellOperator, bpy.types.Operator):
    """Eliminar el cascarón y todos sus objetos auxiliares"""
    bl_idname = "candela.delete"
    bl_label = "Eliminar cascarón"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        obj = _active_shell(context)
        build.remove_all_children(obj)
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        return {'FINISHED'}


class CANDELA_OT_viewport_colors(bpy.types.Operator):
    """Cambiar el color del modo Sólido del visor entre atributo (mapa de análisis) y material"""
    bl_idname = "candela.viewport_colors"
    bl_label = "Color del visor"
    bl_options = {'REGISTER'}

    mode: EnumProperty(items=[('ATTRIBUTE', "Mapa de análisis", ""), ('MATERIAL', "Material", "")])

    def execute(self, context):
        color_type = 'VERTEX' if self.mode == 'ATTRIBUTE' else 'MATERIAL'
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                shading = area.spaces.active.shading
                if shading.type not in {'SOLID'}:
                    shading.type = 'SOLID'
                shading.color_type = color_type
        return {'FINISHED'}


# --- variantes ---------------------------------------------------------------------------


class CANDELA_OT_variant_add(_ShellOperator, bpy.types.Operator):
    """Guardar los parámetros actuales como una variante de diseño"""
    bl_idname = "candela.variant_add"
    bl_label = "Guardar variante"

    def execute(self, context):
        s = _active_shell(context).candela
        item = s.variants.add()
        item.name = f"Variante {len(s.variants)}"
        item.data = json.dumps(settings_to_dict(s))
        s.variant_index = len(s.variants) - 1
        return {'FINISHED'}


class CANDELA_OT_variant_apply(_ShellOperator, bpy.types.Operator):
    """Cargar la variante seleccionada"""
    bl_idname = "candela.variant_apply"
    bl_label = "Cargar variante"

    def execute(self, context):
        obj = _active_shell(context)
        s = obj.candela
        if not (0 <= s.variant_index < len(s.variants)):
            return {'CANCELLED'}
        apply_dict(s, json.loads(s.variants[s.variant_index].data))
        build.rebuild(obj)
        return {'FINISHED'}


class CANDELA_OT_variant_update(_ShellOperator, bpy.types.Operator):
    """Sobrescribir la variante seleccionada con los parámetros actuales"""
    bl_idname = "candela.variant_update"
    bl_label = "Actualizar variante"

    def execute(self, context):
        s = _active_shell(context).candela
        if not (0 <= s.variant_index < len(s.variants)):
            return {'CANCELLED'}
        s.variants[s.variant_index].data = json.dumps(settings_to_dict(s))
        return {'FINISHED'}


class CANDELA_OT_variant_remove(_ShellOperator, bpy.types.Operator):
    """Eliminar la variante seleccionada"""
    bl_idname = "candela.variant_remove"
    bl_label = "Eliminar variante"

    def execute(self, context):
        s = _active_shell(context).candela
        if not (0 <= s.variant_index < len(s.variants)):
            return {'CANCELLED'}
        s.variants.remove(s.variant_index)
        s.variant_index = min(s.variant_index, len(s.variants) - 1)
        return {'FINISHED'}


# --- exportar / importar --------------------------------------------------------------------


class CANDELA_OT_export_json(_ShellOperator, bpy.types.Operator, ExportHelper):
    """Exportar parámetros, ecuación, métricas, superficie media (nodos y cuadriláteros) y generatrices"""
    bl_idname = "candela.export_json"
    bl_label = "Exportar JSON"
    bl_options = {'REGISTER'}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    include_mesh: BoolProperty(name="Incluir superficie media", default=True)
    include_fea: BoolProperty(name="Incluir modelo FEA", default=False,
                              description="Nodos, elementos, apoyos y cargas del panel Análisis (FEA)")

    def execute(self, context):
        obj = _active_shell(context)
        ev = evaluate(settings_to_dict(obj.candela))
        world = [list(row) for row in obj.matrix_world]
        doc = build_document(ev, world_matrix=world, include_mesh=self.include_mesh, include_fea=self.include_fea)
        doc["object_name"] = obj.name
        with open(self.filepath, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
        self.report({'INFO'}, f"Exportado: {self.filepath}")
        return {'FINISHED'}


class CANDELA_OT_export_cutlist(_ShellOperator, bpy.types.Operator, ExportHelper):
    """Exportar el despiece de cimbra (resumen, tipos y cada pieza con sus coordenadas) a CSV"""
    bl_idname = "candela.export_cutlist"
    bl_label = "Exportar despiece (CSV)"
    bl_options = {'REGISTER'}

    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={'HIDDEN'})
    decimal_comma: BoolProperty(name="Coma decimal", default=True,
                                description="Coma decimal y «;» entre campos (hojas de cálculo en español)")

    def execute(self, context):
        obj = _active_shell(context)
        params = settings_to_dict(obj.candela)
        params.update(formwork_enabled=True, formwork_show=False)  # se calcula aunque el panel esté apagado
        ev = evaluate(params)
        if ev.cutlist is None:
            msg = next((c["message"] for c in ev.report["checks"] if c["code"] == "CUTLIST_INVALID"),
                       "No hay despiece de cimbra.")
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}
        with open(self.filepath, "w", encoding="utf-8-sig", newline="") as fh:  # BOM: Excel detecta UTF-8
            fh.write(cutlist_csv(ev, "," if self.decimal_comma else "."))
        self.report({'INFO'}, f"Despiece exportado: {self.filepath}")
        return {'FINISHED'}


def _build_fea(op, obj):
    try:
        fe, _rep = fe_model_from_params(settings_to_dict(obj.candela))
    except ValueError as exc:
        op.report({'ERROR'}, str(exc))
        return None
    build.store_fea_summary(obj, fe)
    for ch in fe.checks:
        if ch["level"] in ("ERROR", "WARNING"):
            op.report({'WARNING'}, ch["message"])
    return fe


class CANDELA_OT_fea_check(_ShellOperator, bpy.types.Operator):
    """Armar el modelo de elementos finitos y mostrar su resumen: nodos, elementos, apoyos y totales de carga"""
    bl_idname = "candela.fea_check"
    bl_label = "Revisar modelo"
    bl_options = {'REGISTER'}

    def execute(self, context):
        fe = _build_fea(self, _active_shell(context))
        if fe is None:
            return {'CANCELLED'}
        s = fe.summary
        self.report({'INFO'}, f"Modelo FEA: {s['nodes']} nodos, {sum(s['elements'].values())} elementos")
        return {'FINISHED'}


class CANDELA_OT_export_fea(_ShellOperator, bpy.types.Operator, ExportHelper):
    """Exportar el modelo de elementos finitos con apoyos, cargas, combinaciones y pandeo (CalculiX/Abaqus)"""
    bl_idname = "candela.export_fea"
    bl_label = "Exportar CalculiX (.inp)"
    bl_options = {'REGISTER'}

    filename_ext = ".inp"
    filter_glob: StringProperty(default="*.inp", options={'HIDDEN'})

    def execute(self, context):
        obj = _active_shell(context)
        fe = _build_fea(self, obj)
        if fe is None:
            return {'CANCELLED'}
        with open(self.filepath, "w", encoding="ascii", newline="\n") as fh:
            fh.write(to_inp(fe, f"CandelaLab {obj.name} ({obj.candela.shell_type})"))
        with updates_suspended():
            obj.candela.fea_export_key = results_key(settings_to_dict(obj.candela))
        self.report({'INFO'}, f"Modelo FEA exportado: {self.filepath}")
        return {'FINISHED'}


class CANDELA_OT_fea_load(_ShellOperator, bpy.types.Operator, ImportHelper):
    """Cargar resultados de CalculiX (.frd; también lee el .dat del mismo nombre) y mostrarlos sobre el cascarón"""
    bl_idname = "candela.fea_load"
    bl_label = "Cargar resultados (.frd)"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".frd"
    filter_glob: StringProperty(default="*.frd", options={'HIDDEN'})

    def execute(self, context):
        obj = _active_shell(context)
        try:
            res = build.load_fea_results(obj, self.filepath)
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _report_results(self, res)
        return {'FINISHED'}


def _report_results(op, res):
    for ch in res.summary["checks"]:
        if ch["level"] != "INFO":
            op.report({'WARNING'}, ch["message"])
    op.report({'INFO'}, f"Resultados {res.output}: {len(res.items)} conjuntos sobre {res.summary['vertices']} vértices")


# --- Calcular con CalculiX ---------------------------------------------------------------------


@dataclass
class _Run:
    obj: str            # nombre del cascarón
    proc: object        # subprocess.Popen
    log: object         # archivo abierto con la salida de ccx
    folder: str
    job: str
    key: str            # huella de los parámetros exportados
    steps: int
    started: float
    cancel: bool = False

    @property
    def log_path(self):
        return os.path.join(self.folder, self.job + "_ccx.log")

    def output(self) -> str:
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""


_runs: dict[str, _Run] = {}  # cálculos en curso, por nombre del cascarón


def run_status(obj) -> str | None:
    """Progreso del cálculo en curso de este cascarón, o None si no hay ninguno."""
    run = _runs.get(obj.name)
    if run is None:
        return None
    step, _done = ccx.progress(run.output())
    return f"Calculando: paso {max(step, 1)} de {run.steps} · {time.monotonic() - run.started:.0f} s"


def stop_runs():
    for run in list(_runs.values()):
        ccx.stop(run.proc)
        run.log.close()
    _runs.clear()


@persistent
def _stop_on_load(_dummy):
    stop_runs()


def work_dir(settings) -> tuple[str, bool]:
    """Carpeta de cálculo y si es la temporal (relativa a un .blend sin guardar)."""
    raw = settings.fea_work_dir or "//candelalab_fea"
    if raw.startswith("//") and not bpy.data.filepath:
        return os.path.join(tempfile.gettempdir(), "candelalab_fea"), True
    return os.path.normpath(bpy.path.abspath(raw)), False


def _redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class CANDELA_OT_fea_run(_ShellOperator, bpy.types.Operator):
    """Exportar el modelo, calcularlo con CalculiX y cargar los resultados al terminar (Blender sigue respondiendo)"""
    bl_idname = "candela.fea_run"
    bl_label = "Calcular con CalculiX"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _active_shell(context)
        return obj is not None and obj.name not in _runs

    def _start(self, context):
        obj = _active_shell(context)
        prefs = preferences(context)
        exe = ccx.find_ccx(bpy.path.abspath(prefs.ccx_path) if prefs else "")
        if exe is None:
            self.report({'ERROR'}, "No se encontró CalculiX: indica dónde está ccx en Preferencias > Complementos > "
                                   "CandelaLab.")
            return None
        fe = _build_fea(self, obj)
        if fe is None:
            return None
        if fe.summary["rigid_body_modes"]:
            self.report({'ERROR'}, "Los apoyos no impiden todos los movimientos de sólido rígido: CalculiX no puede "
                                   "resolver el modelo (ver «Revisar modelo»).")
            return None
        folder, temporary = work_dir(obj.candela)
        job = ccx.job_name(obj.name)
        if any(r.folder == folder and r.job == job for r in _runs.values()):
            self.report({'ERROR'}, f"Ya hay un cálculo «{job}» en curso en {folder}.")
            return None
        try:
            os.makedirs(folder, exist_ok=True)
            ccx.clear_outputs(folder, job)
            with open(os.path.join(folder, job + ".inp"), "w", encoding="ascii", newline="\n") as fh:
                fh.write(to_inp(fe, f"CandelaLab {obj.name} ({obj.candela.shell_type})"))
            proc, log = ccx.start(exe, folder, job, prefs.ccx_threads if prefs else 0)
        except OSError as exc:
            self.report({'ERROR'}, f"No se pudo lanzar CalculiX: {exc}")
            return None
        key = results_key(settings_to_dict(obj.candela))
        with updates_suspended():
            obj.candela.fea_export_key = key
        if temporary:
            self.report({'WARNING'}, f"El .blend no está guardado: el cálculo se guarda en {folder}.")
        return _Run(obj.name, proc, log, folder, job, key, len(analysis_steps(fe)), time.monotonic())

    def _finish(self, run):
        _runs.pop(run.obj, None)
        run.log.close()
        if run.cancel:
            self.report({'WARNING'}, "Cálculo detenido.")
            return {'CANCELLED'}
        out = run.output()
        frd = os.path.join(run.folder, run.job + ".frd")
        message = ccx.error(out)
        if run.proc.returncode or message or not ccx.progress(out)[1] or not os.path.exists(frd):
            self.report({'ERROR'}, f"CalculiX no terminó: {message or f'código {run.proc.returncode}'}. "
                                   f"Salida completa: {run.log_path}")
            return {'CANCELLED'}
        obj = bpy.data.objects.get(run.obj)
        if obj is None or not build.is_shell(obj):
            self.report({'WARNING'}, f"El cascarón «{run.obj}» ya no existe. Resultados en {frd}.")
            return {'CANCELLED'}
        if results_key(settings_to_dict(obj.candela)) != run.key:
            self.report({'WARNING'}, f"Los datos del modelo cambiaron durante el cálculo: los resultados no se "
                                     f"cargan. Están en {frd}.")
            return {'CANCELLED'}
        try:
            res = build.load_fea_results(obj, frd)
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _report_results(self, res)
        self.report({'INFO'}, f"CalculiX terminó en {time.monotonic() - run.started:.1f} s: {frd}")
        return {'FINISHED'}

    def execute(self, context):
        """Desde un script: espera a que termine."""
        run = self._start(context)
        if run is None:
            return {'CANCELLED'}
        run.proc.wait()
        return self._finish(run)

    def invoke(self, context, event):
        run = self._start(context)
        if run is None:
            return {'CANCELLED'}
        _runs[run.obj] = run
        self._run = run
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        _redraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        run = self._run
        if run.cancel:
            ccx.stop(run.proc)
        _redraw(context)
        if run.proc.poll() is None:
            return {'PASS_THROUGH'}
        context.window_manager.event_timer_remove(self._timer)
        result = self._finish(run)
        _redraw(context)
        return result


class CANDELA_OT_fea_stop(_ShellOperator, bpy.types.Operator):
    """Detener el cálculo de CalculiX en curso de este cascarón"""
    bl_idname = "candela.fea_stop"
    bl_label = "Detener"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = _active_shell(context)
        return obj is not None and obj.name in _runs

    def execute(self, context):
        _runs[_active_shell(context).name].cancel = True
        return {'FINISHED'}


class CANDELA_OT_fea_export_section(_ShellOperator, bpy.types.Operator, ExportHelper):
    """Exportar N y M por unidad de longitud de cada nodo de la lámina, para el resultado elegido (CSV)"""
    bl_idname = "candela.fea_export_section"
    bl_label = "Exportar N y M (CSV)"
    bl_options = {'REGISTER'}

    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={'HIDDEN'})
    decimal_comma: BoolProperty(name="Coma decimal", default=True,
                                description="Coma decimal y «;» entre campos (hojas de cálculo en español)")

    @classmethod
    def poll(cls, context):
        obj = _active_shell(context)
        return obj is not None and build.fea_results_current(obj)

    def execute(self, context):
        obj = _active_shell(context)
        try:
            res = build._cached_results(obj)
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        item = min(int(obj.candela.fea_result_item or 0), len(res.items) - 1)
        if res.items[item].stress is None:
            self.report({'ERROR'}, res.missing_reason(item, "N_MAX"))
            return {'CANCELLED'}
        with open(self.filepath, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(section_csv(res, item, "," if self.decimal_comma else "."))
        self.report({'INFO'}, f"Esfuerzos exportados: {self.filepath}")
        return {'FINISHED'}


class CANDELA_OT_fea_clear(_ShellOperator, bpy.types.Operator):
    """Quitar los resultados FEA del cascarón (colores y deformada)"""
    bl_idname = "candela.fea_clear"
    bl_label = "Quitar resultados"

    def execute(self, context):
        build.clear_fea_results(_active_shell(context))
        return {'FINISHED'}


class CANDELA_OT_import_json(bpy.types.Operator, ImportHelper):
    """Importar parámetros desde un JSON de CandelaLab (en el cascarón activo o en uno nuevo)"""
    bl_idname = "candela.import_json"
    bl_label = "Importar parámetros"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    as_new: BoolProperty(name="Crear cascarón nuevo", default=False)

    def execute(self, context):
        with open(self.filepath, encoding="utf-8") as fh:
            doc = json.load(fh)
        params = doc.get("parameters", doc)
        obj = _active_shell(context)
        if obj is None or self.as_new:
            obj = build.create_shell(context, params=params)
        else:
            apply_dict(obj.candela, params)
            build.rebuild(obj)
        return {'FINISHED'}


classes = (
    CANDELA_OT_add_hypar, CANDELA_OT_add_radial, CANDELA_OT_radial_preset, CANDELA_OT_add_umbrella,
    CANDELA_OT_umbrella_preset, CANDELA_OT_add_umbrella_grid, CANDELA_OT_grid_preset, CANDELA_OT_rebuild,
    CANDELA_OT_fit_ground, CANDELA_OT_freeze, CANDELA_OT_delete,
    CANDELA_OT_viewport_colors, CANDELA_OT_variant_add, CANDELA_OT_variant_apply, CANDELA_OT_variant_update,
    CANDELA_OT_variant_remove, CANDELA_OT_export_json, CANDELA_OT_export_cutlist, CANDELA_OT_fea_check,
    CANDELA_OT_export_fea, CANDELA_OT_fea_load, CANDELA_OT_fea_run, CANDELA_OT_fea_stop,
    CANDELA_OT_fea_export_section, CANDELA_OT_fea_clear, CANDELA_OT_import_json,
)


def menu_add(self, context):
    self.layout.operator(CANDELA_OT_add_hypar.bl_idname, text="Hypar (CandelaLab)", icon='MOD_SIMPLEDEFORM')
    self.layout.operator(CANDELA_OT_add_radial.bl_idname, text="Bóveda radial (CandelaLab)", icon='MESH_CIRCLE')
    self.layout.operator(CANDELA_OT_add_umbrella.bl_idname, text="Paraguas (CandelaLab)", icon='MOD_SIMPLEDEFORM')
    self.layout.operator(CANDELA_OT_add_umbrella_grid.bl_idname, text="Retícula de paraguas (CandelaLab)",
                         icon='MOD_ARRAY')


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_add)
    bpy.app.handlers.load_pre.append(_stop_on_load)  # abrir otro archivo o salir detiene los cálculos en curso
    atexit.register(stop_runs)


def unregister():
    atexit.unregister(stop_runs)
    stop_runs()
    if _stop_on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_stop_on_load)
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_add)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
