"""Prueba de integración dentro de Blender (sin interfaz).

    blender --background --factory-startup --python tests/blender_smoke_test.py

Termina con código 0 si todo pasa y 1 si algo falla.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import candelalab  # noqa: E402
from candelalab import build  # noqa: E402

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())


class FakeOpProps:
    def __init__(self, idname):
        mod, name = idname.split(".")
        object.__setattr__(self, "_rna", getattr(getattr(bpy.ops, mod), name).get_rna_type())

    def __setattr__(self, key, value):
        prop = self._rna.properties.get(key)
        if prop is None or (prop.type == 'ENUM' and value not in prop.enum_items.keys()):
            raise AttributeError(f"propiedad de operador inválida: {key}={value!r}")


class FakeLayout:
    """Sustituto de UILayout que valida propiedades, operadores e iconos."""

    def __init__(self):
        self.__dict__.update(use_property_split=False, use_property_decorate=False, enabled=True)

    @staticmethod
    def _icon(icon):
        if icon not in ICONS:
            raise ValueError(f"icono inexistente: {icon}")

    @staticmethod
    def _kw(fn, kw):
        """Valida nombres y tipos de los argumentos contra la API real de UILayout."""
        params = bpy.types.UILayout.bl_rna.functions[fn].parameters
        for key, value in kw.items():
            if key not in params:
                raise TypeError(f"{fn}(): parámetro desconocido {key!r}")
            kind = params[key].type
            if kind == 'STRING' and not isinstance(value, str):
                raise TypeError(f"{fn}(): {key} debe ser str, no {type(value).__name__}")
            if kind == 'BOOLEAN' and not isinstance(value, bool):
                raise TypeError(f"{fn}(): {key} debe ser bool")
            if kind == 'ENUM' and key == "icon":
                FakeLayout._icon(value)

    def row(self, **kw):
        self._kw("row", kw)
        return FakeLayout()

    def column(self, **kw):
        self._kw("column", kw)
        return FakeLayout()

    def box(self):
        return FakeLayout()

    def grid_flow(self, **kw):
        self._kw("grid_flow", kw)
        return FakeLayout()

    def label(self, **kw):
        self._kw("label", kw)

    def prop(self, data, prop, **kw):
        if prop not in data.bl_rna.properties:
            raise AttributeError(f"propiedad inexistente: {prop}")
        self._kw("prop", kw)

    def operator(self, idname, **kw):
        self._kw("operator", kw)
        return FakeOpProps(idname)

    def template_list(self, list_type, list_id, data, prop, active_data, active_prop, **kw):
        assert list_type in dir(bpy.types) and prop in data.bl_rna.properties


def draw_all_panels(obj, label):
    from types import SimpleNamespace
    from candelalab import panels
    ctx = SimpleNamespace(active_object=obj, region=SimpleNamespace(width=320),
                          preferences=bpy.context.preferences)
    ok = True
    for cls in panels.classes:
        try:
            if issubclass(cls, bpy.types.UIList):
                for i, item in enumerate(obj.candela.variants if obj is not None else []):
                    cls.draw_item(None, ctx, FakeLayout(), obj.candela, item, 0, obj.candela, "variant_index", i)
                continue
            if hasattr(cls, "poll") and not cls.poll(ctx):
                continue
            if hasattr(cls, "draw_header"):
                cls.draw_header(SimpleNamespace(layout=FakeLayout()), ctx)
            cls.draw(SimpleNamespace(layout=FakeLayout()), ctx)
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"       {cls.__name__}: {type(exc).__name__}: {exc}")
    check(ok, f"dibujo de todos los paneles ({label})")


def main():
    print(f"Blender {bpy.app.version_string}")
    candelalab.register()
    try:
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)

        res = bpy.ops.candela.add_hypar()
        check(res == {'FINISHED'}, "operador add_hypar")
        obj = bpy.context.active_object
        s = obj.candela
        check(build.is_shell(obj), "el objeto activo es un cascarón")
        n = (s.res_x + 1) * (s.res_y + 1)
        check(len(obj.data.vertices) == 2 * n, f"vértices del sólido = {2 * n}")
        check(obj.data.attributes.get("candela_part") is not None, "atributo candela_part")
        check(build.find_child(obj, build.ROLE_RULINGS) is not None, "hijo: generatrices")
        check(build.find_child(obj, build.ROLE_SUPPORTS) is not None, "hijo: apoyos")
        rep = build.report_of(obj)
        check(rep["mesh"]["closed"] and rep["mesh"]["consistent"], "sólido cerrado y consistente")
        rulings = build.find_child(obj, build.ROLE_RULINGS).data
        check(len(rulings.splines) == 42, f"42 generatrices dibujadas (hay {len(rulings.splines)})")
        draw_all_panels(obj, "por defecto")
        draw_all_panels(None, "sin cascarón activo")

        # Actualización en vivo al cambiar parámetros
        s.span_x = 14.0
        xs = [v.co.x for v in obj.data.vertices]
        check(abs(max(xs) - min(xs) - 14.0) < 0.2, "span_x actualiza la malla en vivo")
        s.warp = -1.0
        check(build.report_of(obj)["definition"]["warp"] == -1.0, "warp actualiza el informe")

        # Espesor vertical: el volumen de la malla de Blender (float32) coincide con t·A_planta
        s.thickness_mode = 'VERTICAL'
        rep = build.report_of(obj)
        check(abs(rep["concrete"]["volume_shell"] - s.thickness * 14 * 10) < 1e-9, "V = t·A_planta (vertical)")
        s.thickness_mode = 'NORMAL'

        # Mapa de análisis
        s.analysis = 'SLOPE'
        check(obj.data.color_attributes.get(build.ANALYSIS_ATTR) is not None, "atributo de color de pendiente")
        s.analysis = 'NONE'
        check(obj.data.color_attributes.get(build.ANALYSIS_ATTR) is None, "atributo de color eliminado")

        # Vigas y normales
        s.beam_xmin = True
        s.beam_ymax = True
        check(build.find_child(obj, build.ROLE_BEAMS) is not None, "hijo: vigas de borde")
        check(build.report_of(obj)["concrete"]["volume_beams"] > 0, "volumen de vigas > 0")
        s.beam_xmin = False
        s.beam_ymax = False
        check(build.find_child(obj, build.ROLE_BEAMS) is None, "vigas eliminadas al desactivar")
        s.show_normals = True
        check(build.find_child(obj, build.ROLE_NORMALS) is not None, "hijo: normales")
        s.show_normals = False

        # Columnas
        s.support_type = 'COLUMN'
        s.rise = 6.0
        check(len(build.find_child(obj, build.ROLE_SUPPORTS).data.vertices) == 16, "2 columnas")

        # Apoyar en desplante
        s.ground_level = 0.0
        bpy.ops.candela.fit_ground()
        zmin = build.report_of(obj)["geometry"]["z_concrete_min"]
        check(abs(zmin) < 1e-9, f"apoyar en desplante (z_min = {zmin:.2e})")

        # Variantes
        bpy.ops.candela.variant_add()
        saved_span = s.span_x
        s.span_x = 20.0
        bpy.ops.candela.variant_apply()
        check(abs(s.span_x - saved_span) < 1e-6, "variante restaura los parámetros")

        # Definición por esquinas
        s.definition = 'CORNERS'
        s.z_ne = 7.0
        ch = build.report_of(obj)["definition"]["corner_heights"]
        check(abs(ch["NE"] - 7.0) < 1e-9, "definición por esquinas")
        s.analysis = 'SLOPE'
        s.beam_ymin = True
        s.show_normals = True
        s.thickness_mode = 'VERTICAL'
        draw_all_panels(obj, "esquinas, mapa, vigas, normales, variantes")
        s.analysis = 'CURVATURE'
        s.thickness_mode = 'NORMAL'
        s.ruling_layer = 'INTRADOS'
        s.thickness_reference = 'MID'
        draw_all_panels(obj, "curvatura, referencia media")
        saved = s.report_json
        s.report_json = json.dumps({"error": "prueba"})
        draw_all_panels(obj, "estado de error")
        s.report_json = saved
        s.analysis = 'NONE'
        s.beam_ymin = False
        s.show_normals = False
        s.ruling_layer = 'EXTRADOS'
        s.thickness_reference = 'INTRADOS'
        s.definition = 'PARAMS'

        # Rendimiento
        for r in (32, 96):
            s.res_x = s.res_y = r
            t0 = time.perf_counter()
            build.rebuild(obj)
            print(f"  ..   regeneración {r}×{r}: {1000 * (time.perf_counter() - t0):.0f} ms "
                  f"(núcleo {build.report_of(obj)['timing_ms']:.0f} ms)")
        s.res_x = s.res_y = 32

        # Exportar / importar
        path = os.path.join(tempfile.gettempdir(), "candelalab_smoke.json")
        bpy.ops.candela.export_json(filepath=path)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        check(len(doc["midsurface"]["nodes"]) == 33 * 33, "JSON: nodos de superficie media")
        check(doc["world_matrix"] is not None and len(doc["rulings"]) > 0, "JSON: matriz y generatrices")
        bpy.ops.candela.import_json(filepath=path, as_new=True)
        new = bpy.context.active_object
        check(new != obj and build.is_shell(new), "importar JSON como cascarón nuevo")
        check(abs(new.candela.span_x - s.span_x) < 1e-6, "importar reproduce los parámetros")

        # Errores controlados: viga demasiado ancha no rompe la regeneración
        s.beam_xmin = True
        s.beam_width = 100.0
        rep = build.report_of(obj)
        check(any(c["code"] == "BEAMS_INVALID" for c in rep["checks"]), "viga inválida -> error informado")
        s.beam_xmin = False

        # Duplicado: no comparte malla ni hijos con el original
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.duplicate()
        dup = bpy.context.active_object
        dup.candela.span_y = 4.0
        check(obj.data != dup.data, "duplicado con malla propia")
        check(abs(build.report_of(obj)["geometry"]["plan_area"] - s.span_x * s.span_y) < 1e-9,
              "el original no cambia al editar el duplicado")

        # Congelar y eliminar
        bpy.context.view_layer.objects.active = new
        bpy.ops.candela.freeze()
        check(not new.candela.is_shell, "congelar")
        bpy.context.view_layer.objects.active = obj
        name = obj.name
        n_before = len(bpy.data.objects)
        bpy.ops.candela.delete()
        check(name not in bpy.data.objects, "eliminar cascarón")
        check(len(bpy.data.objects) == n_before - 1 - 2, "eliminar también sus hijos (generatrices, apoyos)")

        # Bóveda radial
        res = bpy.ops.candela.add_radial(preset='DOME')
        check(res == {'FINISHED'}, "operador add_radial")
        vault = bpy.context.active_object
        v = vault.candela
        n_layer = 1 + v.radial_count * v.res_x * v.res_y
        check(len(vault.data.vertices) == 2 * n_layer, f"bóveda: vértices soldados = {2 * n_layer}")
        rep = build.report_of(vault)
        check(rep["mesh"]["closed"] and rep["mesh"]["consistent"], "bóveda: sólido cerrado y consistente")
        check(len(build.find_child(vault, build.ROLE_SUPPORTS).data.polygons) == 8 * 5, "bóveda: 8 apoyos")
        check(not [c for c in rep["checks"] if c["level"] != "INFO"], "bóveda por defecto sin avisos")
        draw_all_panels(vault, "bóveda radial")
        v.radial_count = 5
        v.radial_plan = 'POLYGON'
        check(build.report_of(vault)["geometry"]["patches"] == 5, "bóveda: cambia a 5 lóbulos en vivo")
        for key in ("LOBES", "GROINED"):
            bpy.ops.candela.radial_preset(preset=key)
            rep = build.report_of(vault)
            check(rep["mesh"]["closed"] and "error" not in rep, f"tipo {key}")
            if key == "LOBES":
                check(v.radial_plan == 'INCLINED' and rep["definition"]["cut_plane_slope_deg"] < 90.0,
                      "lóbulos con borde por plano inclinado")
                draw_all_panels(vault, "bóveda con planos inclinados")
                n_verts = len(vault.data.vertices)
                v.edge_height = -30.0  # el plano ya no cierra el lóbulo
                check("error" in build.report_of(vault) and len(vault.data.vertices) == n_verts,
                      "corte imposible: error informado y se conserva la última geometría")
                draw_all_panels(vault, "bóveda en estado de error")
                v.edge_height = 10.0
                check("error" not in build.report_of(vault), "se recupera al corregir la altura")
        check(abs(build.report_of(vault)["geometry"]["plan_area"] - 900.0) < 1e-6, "bóveda por arista: 30×30 m")
        v.radial_supports = 'CENTER'
        v.support_type = 'COLUMN'
        v.analysis = 'SLOPE'
        v.show_normals = True
        v.beam_xmin = True  # no aplica: debe informarse, no fallar
        check(any(c["code"] == "BEAMS_UNSUPPORTED" for c in build.report_of(vault)["checks"]), "vigas no aplicables")
        draw_all_panels(vault, "bóveda con columna, mapa y normales")
        v.ground_level = -1.0
        bpy.ops.candela.fit_ground()
        check(abs(build.report_of(vault)["geometry"]["z_concrete_min"] + 1.0) < 1e-9, "bóveda: apoyar en desplante")
        v.shell_type = 'HYPAR'
        check(build.report_of(vault)["geometry"]["patches"] == 1, "cambiar de tipo en vivo")
        v.shell_type = 'RADIAL'
        path = os.path.join(tempfile.gettempdir(), "candelalab_smoke_vault.json")
        bpy.ops.candela.export_json(filepath=path)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        check(len(doc["patches"]) == 4 and "groin_0" in doc["midsurface"]["node_sets"], "bóveda: JSON")
        os.remove(path)

        # Paraguas de cuatro hypars
        res = bpy.ops.candela.add_umbrella(preset='INVERTED')
        check(res == {'FINISHED'}, "operador add_umbrella")
        umb = bpy.context.active_object
        u = umb.candela
        n_layer = (2 * u.res_x + 1) * (2 * u.res_y + 1)
        check(len(umb.data.vertices) == 2 * n_layer, f"paraguas: rejilla soldada = {2 * n_layer} vértices")
        rep = build.report_of(umb)
        check(rep["mesh"]["closed"] and rep["mesh"]["consistent"], "paraguas: sólido cerrado y consistente")
        check(rep["geometry"]["creases"]["valleys"] == 4, "paraguas invertido: 4 limas valle")
        check(rep["mesh"]["chord_error_max"] < 1e-9, "paraguas: malla exacta (error de cuerda 0)")
        col = build.find_child(umb, build.ROLE_SUPPORTS)
        check(col is not None and len(col.data.polygons) == 6, "paraguas: columna central")
        draw_all_panels(umb, "paraguas")
        u.beam_xmin = u.beam_xmax = u.beam_ymin = u.beam_ymax = True
        check(build.find_child(umb, build.ROLE_BEAMS) is not None
              and build.report_of(umb)["concrete"]["volume_beams"] > 0, "paraguas: vigas de borde")
        draw_all_panels(umb, "paraguas con vigas")
        for key in ("UPRIGHT", "TILTED"):
            bpy.ops.candela.umbrella_preset(preset=key)
            rep = build.report_of(umb)
            check(rep["mesh"]["closed"] and "error" not in rep, f"paraguas {key}")
        check(build.report_of(umb)["definition"]["slope_x"] > 0, "paraguas inclinado")
        u.ground_level = 0.5
        bpy.ops.candela.fit_ground()
        zmin = build.report_of(umb)["geometry"]["z_concrete_min"]
        # Las propiedades de Blender son float32: el desplazamiento se guarda con ~1e-7 m de redondeo.
        check(abs(zmin - 0.5) < 1e-5, f"paraguas: apoyar en desplante (z_min − 0.5 = {zmin - 0.5:.1e} m)")
        u.umbrella_column = False
        check(build.find_child(umb, build.ROLE_SUPPORTS) is None, "paraguas sin columna")
        draw_all_panels(umb, "paraguas inclinado sin columna")
        draw_all_panels(umb, "modularidad con un solo módulo")

        # Retícula modular de paraguas
        res = bpy.ops.candela.add_umbrella_grid(preset='SAWTOOTH')
        check(res == {'FINISHED'}, "operador add_umbrella_grid")
        grd = bpy.context.active_object
        gs = grd.candela
        rep = build.report_of(grd)
        n_mod = gs.grid_count_x * gs.grid_count_y
        per_module = 2 * (2 * gs.res_x + 1) * (2 * gs.res_y + 1)
        check(len(grd.data.vertices) == n_mod * per_module, f"retícula: {n_mod} módulos × {per_module} vértices")
        check(rep["mesh"]["closed"] and rep["mesh"]["consistent"], "retícula: malla cerrada y consistente")
        step = rep["grid"]["joint_step_x"]
        check(abs(step - 2 * 6.0 * math.tan(gs.tilt_x)) < 1e-5, f"lucernarios: salto entre paraguas = {step:.4f} m")
        check(len(build.find_child(grd, build.ROLE_SUPPORTS).data.polygons) == 6 * n_mod, "retícula: una columna por módulo")
        draw_all_panels(grd, "retícula de paraguas")
        bpy.ops.candela.grid_preset(preset='ALTERNATE')
        check(abs(build.report_of(grd)["grid"]["joint_step_x"]) < 1e-5, "alternos: bordes altos enfrentados")
        gs.grid_count_y = 3
        gs.grid_step_y = 0.6
        sup = build.find_child(grd, build.ROLE_SUPPORTS)
        check(abs(min(v.co.z for v in sup.data.vertices) - gs.ground_level) < 1e-5,
              "escalonamiento: todas las columnas llegan al desplante")
        gs.analysis = 'HEIGHT'
        check(len(grd.data.color_attributes[build.ANALYSIS_ATTR].data) == len(grd.data.vertices),
              "retícula: mapa de alturas global")
        gs.analysis = 'NONE'
        draw_all_panels(grd, "retícula escalonada")
        gs.grid_count_x, gs.grid_count_y = 8, 6
        t0 = time.perf_counter()
        build.rebuild(grd)
        print(f"  ..   retícula 8×6 = 48 paraguas ({len(grd.data.vertices):,} vértices): "
              f"{1000 * (time.perf_counter() - t0):.0f} ms (núcleo {build.report_of(grd)['timing_ms']:.0f} ms)")
        path = os.path.join(tempfile.gettempdir(), "candelalab_smoke_grid.json")
        bpy.ops.candela.export_json(filepath=path)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        check(len(doc["grid"]["placements"]) == 48 and "module_report" in doc, "retícula: JSON con colocaciones")
        os.remove(path)

        # Instanciación con Geometry Nodes
        from candelalab import instancing
        check(not instancing.is_instanced(grd), "retícula 48 × 578 vértices (< 50 000): geometría real en AUTO")
        gs.grid_count_x, gs.grid_count_y = 10, 10
        gs.res_x = gs.res_y = 12
        gs.grid_pattern = 'MIRROR_X'
        gs.beam_ymin = True
        rep = build.report_of(grd)
        check(rep["grid"]["instanced"] and instancing.is_instanced(grd), "10×10 paraguas: instancias automáticas")
        module_verts = 2 * 25 * 25
        check(len(grd.data.vertices) == module_verts, f"la malla del objeto es un solo módulo ({module_verts} vértices)")
        check(rep["mesh"]["display_vertices"] == 100 * module_verts, "el informe cuenta las 100 copias")
        pts = build.find_child(grd, build.ROLE_PLACEMENTS)
        check(pts is not None and len(pts.data.vertices) == 100, "colocaciones: 100 puntos")
        for role in (build.ROLE_RULINGS, build.ROLE_BEAMS):
            check(instancing.is_instanced(build.find_child(grd, role)), f"hijo {role} instanciado")
        sup = build.find_child(grd, build.ROLE_SUPPORTS)
        check(not instancing.is_instanced(sup) and len(sup.data.polygons) == 600, "columnas: 100, geometría real")

        def instances_of(owner_name):
            dg = bpy.context.evaluated_depsgraph_get()
            rows = []
            for inst in dg.object_instances:  # sólo válidos durante la iteración
                if inst.is_instance and inst.parent is not None and inst.parent.original.name == owner_name:
                    rows.append((round(inst.matrix_world.determinant(), 6),
                                 tuple(round(c, 6) for c in inst.matrix_world.translation)))
            return rows

        rows = instances_of(grd.name)
        check(len(rows) == 100, f"depsgraph: 100 instancias del módulo (hay {len(rows)})")
        dets = sorted({r[0] for r in rows})
        check(dets == [-1.0, 1.0] and sum(r[0] < 0 for r in rows) == 50, "espejo en columnas: 50 instancias reflejadas")
        offsets = sorted(tuple(round(v.co[i] + grd.location[i], 6) for i in range(3)) for v in pts.data.vertices)
        check(sorted(r[1] for r in rows) == offsets, "las instancias están en las colocaciones")
        check(len(instances_of(build.find_child(grd, build.ROLE_RULINGS).name)) == 100, "generatrices instanciadas")
        t0 = time.perf_counter()
        build.rebuild(grd)
        t_inst = 1000 * (time.perf_counter() - t0)
        gs.grid_instancing = 'REAL'
        check(not instancing.is_instanced(grd) and build.find_child(grd, build.ROLE_PLACEMENTS) is None,
              "geometría real: sin modificador ni colocaciones")
        check(len(grd.data.vertices) == 100 * module_verts, "geometría real: 100 módulos en la malla")
        t0 = time.perf_counter()
        build.rebuild(grd)
        t_real = 1000 * (time.perf_counter() - t0)
        print(f"  ..   10×10 paraguas (125 000 vértices): instancias {t_inst:.0f} ms, geometría real {t_real:.0f} ms")
        same = all(abs(build.report_of(grd)["concrete"][k] - rep["concrete"][k]) < 1e-9
                   for k in ("volume_total", "weight_kN"))
        check(same, "instancias y geometría real: mismos totales")
        gs.grid_instancing = 'AUTO'
        draw_all_panels(grd, "retícula instanciada")
        with candelalab.props.updates_suspended():
            gs.grid_count_x = gs.grid_count_y = 40
        t0 = time.perf_counter()
        build.rebuild(grd)
        t_big = 1000 * (time.perf_counter() - t0)
        n_big = len(instances_of(grd.name))
        print(f"  ..   40×40 = 1600 paraguas (2 000 000 vértices equivalentes): {t_big:.0f} ms, {n_big} instancias")
        check(n_big == 1600, "1600 instancias evaluadas")
        with candelalab.props.updates_suspended():
            gs.grid_count_x = gs.grid_count_y = 10
        build.rebuild(grd)

        # Congelar una retícula instanciada = geometría real
        bpy.ops.candela.add_umbrella_grid(preset='INVERTED')
        frz = bpy.context.active_object
        frz.candela.grid_instancing = 'INSTANCES'
        bpy.ops.candela.freeze()
        check(not frz.candela.is_shell and not instancing.is_instanced(frz)
              and len(frz.data.vertices) == 12 * 2 * 17 * 17, "congelar retícula instanciada: geometría real")

        # Despiece de cimbra con las generatrices
        import csv
        from candelalab.core.pipeline import evaluate
        from candelalab.props import settings_to_dict
        bpy.ops.candela.add_umbrella(preset='INVERTED')
        cim = bpy.context.active_object
        cs = cim.candela
        check(build.find_child(cim, build.ROLE_FORMWORK) is None and build.report_of(cim)["cutlist"] is None,
              "cimbra desactivada por defecto")
        t0 = time.perf_counter()
        cs.formwork_enabled = True
        t_cut = 1000 * (time.perf_counter() - t0)
        cl = build.report_of(cim).get("cutlist") or {}
        fw = build.find_child(cim, build.ROLE_FORMWORK)
        n_pieces = cl["boards"]["count"] + cl["joists"]["count"] + cl["props"]["count"]
        print(f"  ..   despiece del paraguas: {n_pieces} piezas en {t_cut:.0f} ms (regeneración completa)")
        check(fw is not None and cl["boards"]["count"] > 0 and cl["joists"]["count"] > 0, "despiece: hijo «Cimbra»")
        check([m.name for m in fw.data.materials] == ["CandelaLab · Tabla", "CandelaLab · Viga de cimbra",
                                                      "CandelaLab · Puntal"], "cimbra: tres materiales")
        core = evaluate(settings_to_dict(cs)).formwork_mesh
        idx = [0] * len(fw.data.polygons)
        fw.data.polygons.foreach_get("material_index", idx)
        check(idx == core.face_parts, "cimbra: la malla de Blender es la del núcleo, parte = material")
        prop_z = [fw.data.vertices[v].co.z for p in fw.data.polygons if p.material_index == 2 for v in p.vertices]
        check(abs(min(prop_z) - cs.ground_level) < 1e-5, "los puntales llegan al desplante")
        cs.formwork_board_family = 'B'
        check(build.report_of(cim)["cutlist"]["boards"]["family"] == "B", "tablas sobre la familia B")
        cs.formwork_board_family = 'A'
        cs.formwork_show = False
        check(build.find_child(cim, build.ROLE_FORMWORK) is None and build.report_of(cim)["cutlist"],
              "sin mostrar: sin hijo, con despiece")
        cs.formwork_show = True
        draw_all_panels(cim, "cimbra")

        csv_path = os.path.join(tempfile.gettempdir(), "candelalab_despiece.csv")
        check(bpy.ops.candela.export_cutlist(filepath=csv_path) == {'FINISHED'}, "exportar despiece CSV")
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh, delimiter=";"))
        ids = [r[0] for r in rows if r and r[0][:1] in "TVP" and r[0][1:3].isdigit() and "-" in r[0]]
        check(len(ids) == n_pieces and len(set(ids)) == n_pieces, f"CSV: {n_pieces} piezas con identificador único")
        json_path = os.path.join(tempfile.gettempdir(), "candelalab_cimbra.json")
        bpy.ops.candela.export_json(filepath=json_path, include_mesh=False)
        with open(json_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        check(len(doc["cutlist"]["pieces"]) + len(doc["cutlist"]["props_list"]) == n_pieces, "JSON: despiece completo")

        cs.grid_count_x, cs.grid_count_y = 4, 3
        cs.grid_instancing = 'INSTANCES'
        fw = build.find_child(cim, build.ROLE_FORMWORK)
        rep = build.report_of(cim)
        check(rep["cutlist"]["uses"] == 12 and rep["cutlist"]["boards"]["count"] == cl["boards"]["count"],
              "retícula: un juego de cimbra, 12 usos")
        check(not instancing.is_instanced(fw) and len(fw.data.polygons) == len(core.faces),
              "retícula: la cimbra no se instancia (un juego)")
        first = rep["supports"][0]["position"]
        cx = sum(v.co.x for v in fw.data.vertices) / len(fw.data.vertices)
        cy = sum(v.co.y for v in fw.data.vertices) / len(fw.data.vertices)
        check(abs(cx - first[0]) < 0.5 and abs(cy - first[1]) < 0.5, "la cimbra se muestra en el primer módulo")
        bpy.ops.candela.freeze()
        check(fw.name in bpy.data.objects and fw.parent is None and build.ROLE not in fw,
              "congelar: la cimbra queda como objeto independiente")

        # Modelo FEA con cargas y apoyos
        from candelalab.core.fea import fe_model_from_params
        bpy.ops.candela.add_umbrella(preset='INVERTED')
        fob = bpy.context.active_object
        fs = fob.candela
        fs.support_type = 'COLUMN'
        fs.fea_dead_load, fs.fea_live_load = 1.0, 0.5
        check(build.fea_summary(fob) is None, "FEA: sin resumen hasta revisar")
        draw_all_panels(fob, "FEA sin revisar")
        t0 = time.perf_counter()
        check(bpy.ops.candela.fea_check() == {'FINISHED'}, "FEA: revisar modelo")
        t_fea = 1000 * (time.perf_counter() - t0)
        data = build.fea_summary(fob)
        check(data is not None and data["summary"]["rigid_body_modes"] == 0
              and data["summary"]["elements"].get("B32") == 4, "FEA: columna empotrada, sin mecanismo")
        print(f"  ..   modelo FEA del paraguas: {data['summary']['nodes']} nodos en {t_fea:.0f} ms")
        draw_all_panels(fob, "FEA revisado")
        inp_path = os.path.join(tempfile.gettempdir(), "candelalab_fea.inp")
        check(bpy.ops.candela.export_fea(filepath=inp_path) == {'FINISHED'}, "FEA: exportar .inp")
        with open(inp_path, encoding="ascii") as fh:
            text = fh.read()
        steps = text.count("*STEP")
        check(steps == text.count("*END STEP") == 8, f"FEA: 6 pasos estáticos y 2 de pandeo ({steps})")
        fe, _rep = fe_model_from_params(settings_to_dict(fs))
        node_block = text.split("*NODE, NSET=NALL\n")[1].split("\n*")[0]
        check(len(node_block.splitlines()) == len(fe.nodes), "FEA: los nodos del .inp son los del núcleo")
        fs.fea_live_load = 2.0
        check(build.fea_summary(fob) is None, "FEA: el resumen caduca al cambiar un dato")
        json_path = os.path.join(tempfile.gettempdir(), "candelalab_fea.json")
        bpy.ops.candela.export_json(filepath=json_path, include_mesh=False, include_fea=True)
        with open(json_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        check(len(doc["fea"]["nodes"]) == len(fe.nodes) and doc["fea"]["boundary"] == [{"set": "NB_C", "dofs": [1, 6]}],
              "FEA: el JSON lleva el mismo modelo")
        bpy.ops.candela.delete()

        # Rigidizadores: nervios en las limas y viga del borde libre
        from candelalab.core.pipeline import evaluate as core_evaluate
        bpy.ops.candela.add_umbrella(preset='INVERTED')
        rib = bpy.context.active_object
        rb = rib.candela
        check(build.find_child(rib, build.ROLE_RIBS) is None, "rigidizadores: desactivados por defecto")
        rb.rib_creases = True
        ribs = build.find_child(rib, build.ROLE_RIBS)
        core_ribs = core_evaluate(settings_to_dict(rb)).ribs_mesh
        check(ribs is not None and len(ribs.data.polygons) == len(core_ribs.faces)
              and ribs.data.materials[0].name == "CandelaLab · Nervio", "rigidizadores: hijo «Nervios»")
        conc = build.report_of(rib)["concrete"]
        check(conc["volume_ribs"] > 0 and abs(conc["volume_total"] - conc["volume_shell"] - conc["volume_beams"]
                                              - conc["volume_ribs"]) < 1e-9, "rigidizadores: volumen en el total")
        draw_all_panels(rib, "nervios")
        rb.grid_count_x, rb.grid_count_y = 3, 3
        rb.grid_instancing = 'INSTANCES'
        check(instancing.is_instanced(build.find_child(rib, build.ROLE_RIBS)), "rigidizadores: instanciados en la retícula")
        bpy.ops.candela.delete()
        bpy.ops.candela.add_radial(preset='DOME')
        vault = bpy.context.active_object
        vault.candela.beam_free_edge = True
        vault.candela.rib_creases = True
        check(build.find_child(vault, build.ROLE_BEAMS) is not None and build.find_child(vault, build.ROLE_RIBS) is not None
              and not any(c["level"] == "ERROR" for c in build.report_of(vault)["checks"]),
              "bóveda: viga en el borde libre y nervios en las limas")
        draw_all_panels(vault, "bóveda con rigidizadores")
        bpy.ops.candela.delete()

        # Capiteles: engrosamiento bajo la columna del paraguas
        bpy.ops.candela.add_umbrella(preset='INVERTED')
        cob = bpy.context.active_object
        cbs = cob.candela
        cbs.support_type = 'COLUMN'
        cbs.res_x = cbs.res_y = 16
        z_low = min(v.co.z for v in cob.data.vertices)
        cbs.capital_enabled = True
        rep = build.report_of(cob)
        z_cap = min(v.co.z for v in cob.data.vertices)
        # Espesor normal: el intradós baja Δ según el inglete de los 4 paños (≥ Δ en vertical)
        check(rep["concrete"]["volume_capitals"] > 0 and z_low - 0.2 < z_cap < z_low - 0.10 + 1e-6,
              f"capiteles: el intradós baja bajo la columna ({1000 * (z_low - z_cap):.1f} mm)")
        col_top = max(v.co.z for v in build.find_child(cob, build.ROLE_SUPPORTS).data.vertices)
        check(abs(col_top - z_cap) < 1e-5, "capiteles: la columna llega al intradós engrosado")
        check(abs(rep["mesh"]["volume_rel_error"]) < 0.01 and not any(c["level"] == "ERROR" for c in rep["checks"]),
              "capiteles: malla y volumen analítico concuerdan")
        draw_all_panels(cob, "capiteles")
        bpy.ops.candela.delete()

        # Resultados FEA: .frd sintético con el formato de CalculiX (no hay CalculiX en el equipo de desarrollo)
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        from frd_fixture import dat_text, frd_text, synthetic
        from candelalab.core.fea import analysis_steps
        from candelalab.core.pipeline import evaluate
        bpy.ops.candela.add_hypar()
        rob = bpy.context.active_object
        rs = rob.candela
        rs.support_layout, rs.support_type = 'ALL_CORNERS', 'FIXED'
        rs.res_x = rs.res_y = 8
        rs.fea_live_load = 1.0
        bpy.ops.candela.export_fea(filepath=os.path.join(tempfile.gettempdir(), "clab_res.inp"))
        params = settings_to_dict(rs)
        fe, _r = fe_model_from_params(params)
        model = evaluate(params).model
        nodes, face, nid = {}, {}, 500000
        for v, (x, y) in fe.plan.items():  # salida 3D: nodos expandidos en las caras, con números nuevos
            n = model.patch_at(x, y).surface.unit_normal(x, y)
            for f in (-1, 0, 1):
                nid += 3
                nodes[nid] = tuple(fe.nodes[v][i] + f * 0.5 * rs.thickness * n[i] for i in range(3))
                face[nid] = f
        steps = synthetic(fe, nodes, lambda n_, p, k: [0.0, 0.0, -1e-3 * k],
                          lambda n_, p, k: [1e6 * (2 + face[n_]), 0, 0, 0, 0, 0], buckling_factors=(1.0, 2.0))
        static = [st["name"] for st in analysis_steps(fe) if st["kind"] == "STATIC"]
        frd_path = os.path.join(tempfile.gettempdir(), "clab_res.frd")
        with open(frd_path, "w", encoding="ascii") as fh:
            fh.write(frd_text(nodes, steps))
        with open(frd_path[:-4] + ".dat", "w", encoding="ascii") as fh:
            fh.write(dat_text(buckling=[[8.0, 9.5], [7.0, 7.5]],
                              totals=[[1000 * (sp - c) for sp, c in zip(fe.summary["support_loads_kN"][st],
                                                                        fe.summary["totals_kN"][st])]
                                      for st in static]))
        draw_all_panels(rob, "sin resultados")
        check(bpy.ops.candela.fea_load(filepath=frd_path) == {'FINISHED'}, "resultados: cargar el .frd (salida 3D)")
        mesh = rob.data
        check(mesh.color_attributes.get("candela_fea") is not None
              and mesh.color_attributes.active_color.name == "candela_fea", "resultados: colores en el cascarón")
        rs.fea_result_quantity = 'S_MAX'
        vals = [0.0] * len(mesh.vertices)
        mesh.attributes["candela_fea_valor"].data.foreach_get("value", vals)
        half = len(vals) // 2
        check(all(abs(v - 1.0) < 1e-3 for v in vals[:half]) and all(abs(v - 3.0) < 1e-3 for v in vals[half:]),
              "resultados: intradós 1 MPa y extradós 3 MPa (se ve la flexión)")
        summary = json.loads(rs.fea_results_summary)
        check(summary["output"] == "3D" and any(c["code"] == "FRD_REACTIONS" and c["level"] == "INFO"
                                                for c in summary["checks"]), "resultados: reacciones del .dat = cargas")
        rs.fea_result_quantity = 'N_MAX'
        mesh.attributes["candela_fea_valor"].data.foreach_get("value", vals)
        check(all(math.isfinite(v) and v > 0 for v in vals) and json.loads(rs.fea_results_legend)["section"],
              "esfuerzos: N₁ en todo el cascarón (tracción)")
        rs.fea_dirs_show = True
        dirs = build.find_child(rob, build.ROLE_DIRECTIONS)
        info = json.loads(rs.fea_results_legend).get("directions", {})
        check(dirs is not None and info.get("count", 0) > 20 and [m.name for m in dirs.data.materials]
              == ["CandelaLab · Tracción", "CandelaLab · Compresión"], "direcciones de N: hijo con cruces")
        mats = [0] * len(dirs.data.polygons)
        dirs.data.polygons.foreach_get("material_index", mats)
        # σxx global proyectada en el plano tangente: N₁ > 0 según la proyección de X, N₂ = 0 (se omite).
        # En una esquina la recta de N₁ puede salir de la planta por ambos lados: esa cruz no tiene brazos.
        check(set(mats) == {0} and info["count"] <= info["omitted"] <= info["count"] + 4
              and dirs.data.color_attributes.get("candela_fea") is not None,
              "direcciones de N: tracción uniaxial, sin brazo de N₂")
        zs = [v.co.z for v in mesh.vertices]
        gz = [v.co.z for v in dirs.data.vertices]
        check(min(zs) - 0.05 < min(gz) and max(gz) < max(zs) + 0.05, "direcciones de N: sobre las caras del cascarón")
        rs.output = 'SURFACE'  # misma malla FE, otra malla mostrada: se reasocia
        legend = json.loads(rs.fea_results_legend)
        check("error" not in legend and mesh.color_attributes.get("candela_fea") is not None
              and len(mesh.color_attributes["candela_fea"].data) == len(mesh.vertices)
              and build.find_child(rob, build.ROLE_DIRECTIONS) is not None,
              "resultados: se reasocian al mostrar sólo la superficie")
        rs.output = 'SOLID'
        n_mod = len(mesh.vertices)
        rs.grid_count_x = 2
        first = [v.co for v in mesh.vertices[:n_mod]]
        cx = sum(c.x for c in first) / n_mod
        dirs = build.find_child(rob, build.ROLE_DIRECTIONS)
        gx = sum(v.co.x for v in dirs.data.vertices) / len(dirs.data.vertices)
        check(abs(gx - cx) < 0.05 * rs.span_x and cx < -0.25 * rs.span_x,
              f"direcciones de N: en el primer módulo de la retícula (x = {gx:.2f}, módulo {cx:.2f})")
        rs.grid_instancing = 'INSTANCES'
        dirs = build.find_child(rob, build.ROLE_DIRECTIONS)
        gx = sum(v.co.x for v in dirs.data.vertices) / len(dirs.data.vertices)
        check(abs(gx - cx) < 0.05 * rs.span_x and not instancing.is_instanced(dirs),
              "direcciones de N: en la primera colocación de la retícula instanciada")
        rs.grid_count_x, rs.grid_instancing = 1, 'AUTO'
        rs.fea_result_quantity = 'M_X'
        mesh.attributes["candela_fea_valor"].data.foreach_get("value", vals)
        t_ = rs.thickness
        check(all(0.0 <= v <= t_ * t_ / 12 * 2e6 / 1000 + 1e-9 for v in vals) and max(vals) > 0.9 * t_ * t_ / 6 * 1e3,
              "esfuerzos: Mx = t²/12·(σext − σint) ≥ 0 (tracciona el extradós)")
        draw_all_panels(rob, "esfuerzos N, M")
        nm_path = os.path.join(tempfile.gettempdir(), "clab_nm.csv")
        check(bpy.ops.candela.fea_export_section(filepath=nm_path) == {'FINISHED'}, "esfuerzos: exportar N y M (CSV)")
        with open(nm_path, encoding="utf-8-sig", newline="") as fh:
            nm_rows = [r for r in csv.reader(fh, delimiter=";") if r and r[0].isdigit()]
        check(len(nm_rows) == len(fe.plan), f"esfuerzos: una fila por nodo de la lámina ({len(nm_rows)})")
        os.remove(nm_path)
        buckle = next(i for i, it in enumerate(summary["items"]) if it["kind"] == "BUCKLE")
        rs.fea_result_item = str(buckle)
        legend = json.loads(rs.fea_results_legend)
        check(legend["kind"] == "BUCKLE" and legend["factor"] == 8.0 and legend["factor_source"] == ".dat",
              "resultados: modo de pandeo con el factor del .dat")
        check(build.find_child(rob, build.ROLE_DIRECTIONS) is None and "pandeo" in legend["directions"]["reason"],
              "direcciones de N: un modo de pandeo no trae esfuerzos")
        draw_all_panels(rob, "modo de pandeo")
        rs.fea_result_item = "0"
        rs.fea_result_quantity = 'DISP'
        rs.fea_result_deformed = True
        dfm = build.find_child(rob, build.ROLE_DEFORMED)
        scale = json.loads(rs.fea_results_legend)["scale"]
        check(dfm is not None and len(dfm.data.vertices) == len(mesh.vertices)
              and abs(dfm.data.vertices[0].co.z - (mesh.vertices[0].co.z - scale * 1e-3)) < 1e-5,
              "resultados: deformada con su escala")
        draw_all_panels(rob, "resultados")
        build._fea_cache.clear()  # como al abrir el .blend: el .frd se relee
        rs.fea_result_quantity = 'VON_MISES'
        check(mesh.color_attributes.get("candela_fea") is not None, "resultados: se releen del archivo")
        rs.show_rulings = False
        check(mesh.color_attributes.get("candela_fea") is not None, "resultados: un cambio de vista no los invalida")
        rs.thickness = 0.06
        check(mesh.color_attributes.get("candela_fea") is None and build.find_child(rob, build.ROLE_DEFORMED) is None
              and build.find_child(rob, build.ROLE_DIRECTIONS) is None, "resultados: se ocultan si cambia la geometría")
        draw_all_panels(rob, "resultados desactualizados")
        rs.thickness = 0.04
        check(mesh.color_attributes.get("candela_fea") is not None and build.find_child(rob, build.ROLE_DIRECTIONS)
              is not None, "resultados: vuelven al restaurar el parámetro")
        bpy.ops.candela.fea_clear()
        check(not rs.fea_results_summary and mesh.color_attributes.get("candela_fea") is None
              and build.find_child(rob, build.ROLE_DIRECTIONS) is None, "resultados: quitar")
        bpy.ops.candela.delete()

        # Guardar y reabrir .blend
        blend = os.path.join(tempfile.gettempdir(), "candelalab_smoke.blend")
        grd_name = grd.name
        bpy.ops.wm.save_as_mainfile(filepath=blend)
        bpy.ops.wm.open_mainfile(filepath=blend)
        shells = [o for o in bpy.data.objects if build.is_shell(o)]
        check(len(shells) == 4 and all(build.report_of(o).get("geometry") for o in shells), "persistencia en .blend")
        check(len(instances_of(grd_name)) == 100, "tras reabrir: las 100 instancias siguen evaluándose")
    except Exception:
        traceback.print_exc()
        failures.append("excepción")
    finally:
        candelalab.unregister()

    print(f"\n{'FALLÓ' if failures else 'OK'}: {len(failures)} fallos")
    sys.exit(1 if failures else 0)


main()
