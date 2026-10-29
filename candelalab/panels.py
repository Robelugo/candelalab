"""Interfaz: panel "CandelaLab" en la barra lateral (N) del visor 3D."""

import json
import os
import textwrap

import bpy

from . import build
from .core.grid import PRESETS as GRID_PRESETS
from .core.radial import PRESETS as RADIAL_PRESETS
from .core.umbrella import PRESETS as UMBRELLA_PRESETS

LEVEL_ICONS = {"ERROR": 'CANCEL', "WARNING": 'ERROR', "INFO": 'INFO'}
MAX_TYPES_SHOWN = 8


def _shell(context):
    obj = context.active_object
    return obj if build.is_shell(obj) else None


def _wrapped(layout, context, text, icon='NONE'):
    scale = context.preferences.system.ui_scale or 1.0  # 0 sin ventana (modo background)
    width = max(20, int(context.region.width / (6.5 * scale)))
    col = layout.column(align=True)
    for i, line in enumerate(textwrap.wrap(text, width) or [""]):
        col.label(text=line, icon=icon if i == 0 else 'BLANK1' if icon != 'NONE' else 'NONE')


def _kv(layout, key, value):
    row = layout.row()
    row.label(text=key)
    row.label(text=value)


def _m(v, unit="m", digits=3):
    return f"{v:.{digits}f} {unit}"


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CandelaLab"


class _Sub(_Base):
    bl_parent_id = "CANDELA_PT_main"

    @classmethod
    def poll(cls, context):
        return _shell(context) is not None


class CANDELA_PT_main(_Base, bpy.types.Panel):
    bl_label = "CandelaLab"

    def draw(self, context):
        layout = self.layout
        obj = _shell(context)
        if obj is None:
            layout.label(text="Diseñador de cascarones laminares")
            layout.operator("candela.add_hypar", icon='ADD')
            layout.operator("candela.add_radial", icon='MESH_CIRCLE')
            layout.operator("candela.add_umbrella", icon='MOD_SIMPLEDEFORM')
            layout.operator("candela.add_umbrella_grid", icon='MOD_ARRAY')
            layout.operator("candela.import_json", icon='IMPORT')
            if context.active_object is not None and context.active_object.type == 'MESH':
                layout.label(text="El objeto activo no es un cascarón.", icon='INFO')
            return
        s = obj.candela
        layout.prop(s, "shell_type")
        row = layout.row(align=True)
        row.operator("candela.rebuild", icon='FILE_REFRESH')
        row.operator("candela.fit_ground", icon='TRIA_DOWN_BAR', text="Desplante")
        row = layout.row(align=True)
        row.operator("candela.freeze", icon='FREEZE', text="Congelar")
        row.operator("candela.delete", icon='TRASH', text="Eliminar")

        rep = build.report_of(obj)
        if "error" in rep:
            box = layout.box()
            _wrapped(box, context, rep["error"], icon='CANCEL')
            return
        if "concrete" in rep:
            box = layout.box()
            g, c = rep["geometry"], rep["concrete"]
            col = box.column(align=True)
            _kv(col, "Área (referencia)", _m(g["area_reference"], "m²", 2))
            _kv(col, "Volumen concreto", _m(c["volume_total"], "m³", 3))
            _kv(col, "Peso propio", _m(c["weight_kN"], "kN", 1))
            if rep.get("grid"):
                _kv(col, "Módulos", "{} ({} × {})".format(rep["grid"]["modules"], *rep["grid"]["count"]))
            levels = [ch["level"] for ch in rep.get("checks", [])]
            row = box.row()
            row.label(text=f"{levels.count('ERROR')} errores", icon='CANCEL')
            row.label(text=f"{levels.count('WARNING')} avisos", icon='ERROR')
            box.label(text=f"Regenerado en {rep.get('timing_ms', 0):.0f} ms", icon='TIME')


class CANDELA_PT_geometry(_Sub, bpy.types.Panel):
    bl_label = "Geometría"

    def draw(self, context):
        layout = self.layout
        s = _shell(context).candela
        layout.use_property_split = True
        layout.use_property_decorate = False
        radial = s.shell_type == 'RADIAL'

        if radial:
            row = layout.row(align=True)
            row.use_property_split = False
            for key, (label, _desc, _p) in RADIAL_PRESETS.items():
                row.operator("candela.radial_preset", text=label).preset = key
            col = layout.column(align=True)
            col.prop(s, "radial_count")
            col.prop(s, "radial_radius")
            layout.prop(s, "radial_plan")
            col = layout.column(align=True)
            col.prop(s, "crown_height")
            col.prop(s, "edge_height")
            col.prop(s, "support_height")
            if s.radial_plan == 'INCLINED':
                layout.prop(s, "edge_overhang")
        elif s.shell_type == 'UMBRELLA':
            row = layout.row(align=True)
            row.use_property_split = False
            for key, (label, _desc, _p) in UMBRELLA_PRESETS.items():
                row.operator("candela.umbrella_preset", text=label).preset = key
            col = layout.column(align=True)
            col.prop(s, "span_x")
            col.prop(s, "span_y")
            col = layout.column(align=True)
            col.prop(s, "umbrella_center_height")
            col.prop(s, "umbrella_edge_height")
            col = layout.column(align=True)
            col.prop(s, "tilt_x")
            col.prop(s, "tilt_y")
        else:
            _draw_hypar(layout, s)
        _draw_common(context, layout, s, radial)


def _draw_hypar(layout, s):
    layout.row().prop(s, "definition", expand=True)
    col = layout.column(align=True)
    col.prop(s, "span_x")
    col.prop(s, "span_y")
    if s.definition == 'PARAMS':
        col = layout.column(align=True)
        col.prop(s, "rise")
        col.prop(s, "warp")
        col = layout.column(align=True)
        col.prop(s, "tilt_x")
        col.prop(s, "tilt_y")
        layout.row().prop(s, "edge_mode", expand=True)
        sub = layout.row()
        sub.enabled = s.edge_mode == 'CUSTOM'
        sub.prop(s, "edge_angle")
    else:
        grid = layout.grid_flow(columns=2, even_columns=True, align=True)
        for key in ("z_nw", "z_ne", "z_sw", "z_se"):  # dispuestas como en planta
            grid.prop(s, key)


def _draw_common(context, layout, s, radial):
    box = layout.box()
    box.label(text="Espesor", icon='MOD_SOLIDIFY')
    box.prop(s, "thickness")
    box.row().prop(s, "thickness_mode", expand=True)
    box.prop(s, "thickness_reference")

    box = layout.box()
    box.label(text="Malla", icon='MESH_GRID')
    box.row().prop(s, "output", expand=True)
    col = box.column(align=True)
    if radial:
        col.prop(s, "res_x", text="Divisiones radiales")
        col.prop(s, "res_y", text="Divisiones por lóbulo")
    elif s.shell_type == 'UMBRELLA':
        col.prop(s, "res_x", text="Divisiones X por cuadrante")
        col.prop(s, "res_y", text="Divisiones Y por cuadrante")
    else:
        col.prop(s, "res_x")
        col.prop(s, "res_y")

    rep = build.report_of(_shell(context))
    d = rep.get("definition")
    if d and d.get("family") == "radial_hyperbolic_paraboloids":
        box = layout.box()
        box.use_property_split = False
        box.label(text="Definición matemática (por lóbulo)", icon='FORCE_HARMONIC')
        box.label(text="z = H + c1·x' + c3·x'² + c5·y'²")
        lc = d["lobe_coefficients"]
        col = box.column(align=True)
        col.label(text=f"H={lc['H']:.4g}  c1={lc['c1']:.4g}")
        col.label(text=f"c3={lc['c3']:.4g}  c5={lc['c5']:.4g}")
        col = box.column(align=True)
        _kv(col, "Pendiente de las limas", f"{d['groin_slope_deg']:.1f}°")
        _kv(col, "Borde libre", d["edge_cut"].split(" (")[0])
        if d["cut_plane_slope_deg"] < 90.0:
            _kv(col, "Inclinación del plano", f"{d['cut_plane_slope_deg']:.1f}°")
            _kv(col, "Vértice del borde (r, z)", f"{d['lobe_tip_distance']:.2f}, {d['edge_height']:.2f} m")
        cr = rep.get("geometry", {}).get("creases") or {}
        if cr.get("count"):
            _kv(col, "Limas valle / cumbrera", f"{cr['valley_length']:.1f} / {cr['ridge_length']:.1f} m")
            _kv(col, "Ángulo entre normales", f"{cr['normal_angle_min']:.1f}–{cr['normal_angle_max']:.1f}°")
    elif d and d.get("family") == "umbrella_hyperbolic_paraboloids":
        box = layout.box()
        box.use_property_split = False
        box.label(text="Definición matemática (por cuadrante)", icon='FORCE_HARMONIC')
        box.label(text="z = zc + Δ(sx·x/a + sy·y/b − sx·sy·xy/ab) + px + qy")
        col = box.column(align=True)
        _kv(col, "Tipo", d["kind"])
        _kv(col, "Flecha Δ", f"{d['delta']:.3f} m")
        _kv(col, "k = Δ/(a·b)", f"{d['k']:.5f} 1/m")
        _kv(col, "Pendiente de las limas X / Y", "{:.1f}° / {:.1f}°".format(*d["valley_slope_deg"]))
        ch = d["corner_heights"]
        _kv(col, "Esquinas NO / NE", f"{ch['NO']:.3f} / {ch['NE']:.3f} m")
        _kv(col, "Esquinas SO / SE", f"{ch['SO']:.3f} / {ch['SE']:.3f} m")
    elif d:
        box = layout.box()
        box.use_property_split = False
        box.label(text="Definición matemática", icon='FORCE_HARMONIC')
        c = d["coefficients"]
        box.label(text="z = c0 + c1·x + c2·y + c3·x² + c4·xy + c5·y²")
        col = box.column(align=True)
        col.label(text=f"c0={c['c0']:.4g}  c1={c['c1']:.4g}  c2={c['c2']:.4g}")
        col.label(text=f"c3={c['c3']:.4g}  c4={c['c4']:.4g}  c5={c['c5']:.4g}")
        col = box.column(align=True)
        _kv(col, "k (alabeo)", f"{d['k']:.5f} 1/m")
        _kv(col, "θ bordes/generatrices", f"{d['edge_angle_deg']:.1f}°")
        dirs = d.get("ruling_directions_deg") or []
        if dirs:
            _kv(col, "Generatrices A / B", f"{dirs[0]:.1f}° / {dirs[1]:.1f}°")
        ch = d["corner_heights"]
        _kv(col, "Esquinas NO / NE", f"{ch['NO']:.3f} / {ch['NE']:.3f} m")
        _kv(col, "Esquinas SO / SE", f"{ch['SO']:.3f} / {ch['SE']:.3f} m")


class CANDELA_PT_structure(_Sub, bpy.types.Panel):
    bl_label = "Concepto estructural"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = _shell(context).candela
        layout.use_property_split = True
        layout.use_property_decorate = False
        radial = s.shell_type == 'RADIAL'
        box = layout.box()
        box.label(text="Apoyos", icon='TRIA_DOWN')
        box.prop(s, {"RADIAL": "radial_supports", "UMBRELLA": "umbrella_column"}.get(s.shell_type, "support_layout"))
        box.prop(s, "support_type")
        box.prop(s, "support_size")
        box.prop(s, "ground_level")

        box = layout.box()
        box.label(text="Capiteles", icon='SPHERECURVE')
        box.prop(s, "capital_enabled")
        col = box.column(align=True)
        col.enabled = s.capital_enabled
        col.prop(s, "capital_radius")
        col.prop(s, "capital_extra")
        caps = build.report_of(_shell(context)).get("capitals") or []
        if s.capital_enabled and caps:
            _kv(box.column(), "Espesor en el apoyo", f"{caps[0]['thickness_max'] * 1000:.0f} mm")
            _wrapped(box, context, "Perfil Δ·(1 − (r/R)²)²: suave en el centro y en el borde. Columnas, "
                                   "nervios y vigas cuelgan del intradós engrosado.", icon='INFO')
        box = layout.box()
        box.label(text="Vigas de borde", icon='MOD_BUILD')
        row = box.row(align=True)
        if radial:
            row.prop(s, "beam_free_edge", toggle=True)
        else:
            for key in ("beam_xmin", "beam_xmax", "beam_ymin", "beam_ymax"):
                row.prop(s, key, toggle=True)
        col = box.column(align=True)
        col.prop(s, "beam_width")
        col.prop(s, "beam_depth")

        box = layout.box()
        box.label(text="Rigidizadores", icon='MOD_WIREFRAME')
        box.prop(s, "rib_creases")
        col = box.column(align=True)
        col.enabled = s.rib_creases
        col.prop(s, "rib_width")
        col.prop(s, "rib_depth")
        if s.shell_type == 'HYPAR':
            _wrapped(box, context, "Una hypar de un solo paño no tiene limas: se rigidiza con vigas de borde.",
                     icon='INFO')
        layout.prop(s, "concrete_density")
        _wrapped(layout, context, "Secciones de vigas, nervios y columnas: datos del usuario, no dimensionadas.",
                 icon='INFO')


class CANDELA_PT_formwork(_Sub, bpy.types.Panel):
    bl_label = "Cimbra"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.prop(_shell(context).candela, "formwork_enabled", text="")

    def draw(self, context):
        layout = self.layout
        obj = _shell(context)
        s = obj.candela
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        col.enabled = s.formwork_enabled
        col.row().prop(s, "formwork_board_family", expand=True)
        sub = col.column(align=True)
        sub.prop(s, "formwork_board_width")
        sub.prop(s, "formwork_board_thickness")
        sub = col.column(align=True)
        sub.prop(s, "formwork_joist_spacing")
        sub.prop(s, "formwork_joist_width")
        sub.prop(s, "formwork_joist_depth")
        col.prop(s, "formwork_prop_spacing")
        sub = col.column(align=True)
        sub.prop(s, "formwork_stock_length")
        sub.prop(s, "formwork_tolerance")
        col.prop(s, "formwork_show")
        layout.operator("candela.export_cutlist", icon='EXPORT')

        cl = build.report_of(obj).get("cutlist")
        if not s.formwork_enabled or not cl:
            _wrapped(layout, context, "Activa la casilla del encabezado para calcular el despiece: tablas sobre "
                                      "una familia de generatrices, vigas sobre la otra y puntales hasta el "
                                      "desplante.", icon='INFO')
            return
        bo, jo, pr = cl["boards"], cl["joists"], cl["props"]
        uses = cl.get("uses", 1)
        box = layout.box()
        box.use_property_split = False
        box.label(text="Despiece" + (f" (un juego, {uses} usos)" if uses > 1 else ""), icon='MOD_LATTICE')
        col = box.column(align=True)
        for label, fam in (("Tablas", bo), ("Vigas", jo)):
            _kv(col, f"{label} ({fam['family'] or '—'})",
                f"{fam['count']} · {len(fam['types'])} tipos · {fam['length_total']:.1f} m")
            st = fam["stock"]
            if st:
                _kv(col, f"   piezas de {st['length']:.2f} m",
                    f"{st['count']} · desperdicio {st['waste_pct']:.1f} % · {st['splices']} empalmes")
        if pr["count"]:
            _kv(col, "Puntales", f"{pr['count']} · {pr['min']:.2f}–{pr['max']:.2f} m")
        _kv(col, "Madera (tablas y vigas)", f"{cl['wood_volume']:.2f} m³ · {cl['board_feet']:,.0f} PT")

        box = layout.box()
        box.use_property_split = False
        box.label(text="Ajuste de las tablas a la hypar", icon='MOD_SIMPLEDEFORM')
        col = box.column(align=True)
        _kv(col, "Giro máximo", f"{bo['twist_max_deg']:.1f}° ({bo['twist_rate_max_deg_m']:.1f}°/m)")
        _kv(col, "Esquina fuera de plano", f"{bo['corner_lift_max'] * 1000:.1f} mm")
        _kv(col, "Holgura entre tablas", f"{bo['gap_max'] * 1000:.1f} mm")
        _kv(col, "Esviaje máximo de corte", f"{max(bo['skew_max_deg'], jo['skew_max_deg']):.1f}°")
        if not cl["exact"]:
            _kv(col, "Pieza recta vs intradós", f"{max(bo['deviation_max'], jo['deviation_max']) * 1000:.2f} mm")

        box = layout.box()
        box.use_property_split = False
        box.label(text="Tipos: × cantidad, eje · corte, esviajes", icon='SORTSIZE')
        col = box.column(align=True)
        for fam in (bo, jo):
            for t in fam["types"][:MAX_TYPES_SHOWN]:
                _kv(col, f"{t['type']} × {t['count']}",
                    f"{t['length']:.3f} · {t['cut_length']:.3f} m · {t['skew'][0]}°/{t['skew'][1]}°")
            if len(fam["types"]) > MAX_TYPES_SHOWN:
                col.label(text=f"… y {len(fam['types']) - MAX_TYPES_SHOWN} tipos más (ver CSV)")
        for t in pr["types"][:MAX_TYPES_SHOWN]:
            _kv(col, f"{t['type']} × {t['count']}", f"{t['length']:.2f} m")
        if len(pr["types"]) > MAX_TYPES_SHOWN:
            col.label(text=f"… y {len(pr['types']) - MAX_TYPES_SHOWN} largos más (ver CSV)")
        _wrapped(layout, context, "Secciones, separaciones y largos son datos del usuario: el despiece no "
                                  "dimensiona la cimbra ni verifica su resistencia o rigidez.", icon='LIGHT')


class CANDELA_PT_fea(_Sub, bpy.types.Panel):
    bl_label = "Análisis (FEA)"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = _shell(context)
        s = obj.candela
        layout.use_property_split = True
        layout.use_property_decorate = False
        _wrapped(layout, context, "Prepara un modelo para CalculiX o Abaqus: CandelaLab no lo resuelve.",
                 icon='INFO')
        box = layout.box()
        box.label(text="Malla de lámina (superficie media)", icon='MESH_GRID')
        box.row().prop(s, "fea_element_order", expand=True)
        box.prop(s, "fea_refine")
        box = layout.box()
        box.label(text="Material (datos del usuario)", icon='MATERIAL')
        col = box.column(align=True)
        col.prop(s, "fea_young_gpa")
        col.prop(s, "fea_poisson")
        col.prop(s, "concrete_density")
        box = layout.box()
        box.label(text="Apoyos (de «Concepto estructural»)", icon='TRIA_DOWN')
        box.prop(s, "fea_columns")
        box = layout.box()
        box.label(text="Cargas, además del peso propio", icon='FORCE_FORCE')
        col = box.column(align=True)
        col.prop(s, "fea_dead_load")
        col.prop(s, "fea_live_load")
        col.prop(s, "fea_pressure")
        box.prop(s, "fea_live_half")
        col = box.column(align=True)
        col.prop(s, "fea_factor_dead")
        col.prop(s, "fea_factor_live")
        row = layout.row()
        row.prop(s, "fea_buckling")
        sub = row.row()
        sub.enabled = s.fea_buckling
        sub.prop(s, "fea_buckling_modes")
        layout.row().prop(s, "fea_output", expand=True)
        row = layout.row(align=True)
        row.operator("candela.fea_check", icon='VIEWZOOM')
        row.operator("candela.export_fea", icon='EXPORT', text="Exportar .inp")

        data = build.fea_summary(obj)
        if data is None:
            _wrapped(layout, context, "Pulsa «Revisar modelo» para ver nodos, apoyos y totales de carga."
                     if not s.fea_summary_json else "El resumen está desactualizado: pulsa «Revisar modelo».",
                     icon='INFO')
        else:
            sm = data["summary"]
            box = layout.box()
            box.use_property_split = False
            box.label(text="Modelo", icon='CHECKMARK')
            col = box.column(align=True)
            _kv(col, "Nodos", f"{sm['nodes']:,}")
            _kv(col, "Elementos", ", ".join(f"{n} {t}" for t, n in sm["elements"].items()))
            _kv(col, "Apoyos", f"{len(sm['supports'])} ({sum(x['nodes'] for x in sm['supports'])} nodos)")
            _kv(col, "Movimientos libres", str(sm["rigid_body_modes"]))
            col = box.column(align=True)
            col.label(text="Totales Fz (kN): deben igualar las reacciones")
            for name, vec in sm["totals_kN"].items():
                _kv(col, f"   {name}", f"{vec[2]:.2f}")
            for ch in data["checks"]:
                _wrapped(box, context, ch["message"], icon=LEVEL_ICONS[ch["level"]])
        _wrapped(layout, context, "Material, cargas, factores y apoyos son datos tuyos. Ningún resultado del "
                                  "análisis indica por sí solo seguridad: revísalo con un ingeniero.", icon='LIGHT')


class CANDELA_PT_fea_results(_Sub, bpy.types.Panel):
    bl_label = "Resultados (FEA)"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = _shell(context)
        s = obj.candela
        row = layout.row(align=True)
        row.operator("candela.fea_load", icon='IMPORT')
        row.operator("candela.fea_clear", icon='X', text="")
        if not s.fea_results_summary:
            _wrapped(layout, context, "Exporta el .inp, calcúlalo con CalculiX (ccx -i nombre) y carga el .frd. "
                                      "El .dat del mismo nombre aporta los factores de pandeo y las reacciones.",
                     icon='INFO')
            return
        summary = json.loads(s.fea_results_summary)
        layout.label(text=os.path.basename(s.fea_results_path) + f" · salida {summary['output']}", icon='FILE')
        if not build.fea_results_current(obj):
            _wrapped(layout, context, "Los parámetros cambiaron desde que se cargaron: estos resultados no "
                                      "corresponden a la geometría actual. Exporta, calcula y carga de nuevo.",
                     icon='ERROR')
            return
        legend = json.loads(s.fea_results_legend) if s.fea_results_legend else {}
        if legend.get("error"):
            _wrapped(layout, context, legend["error"], icon='CANCEL')
            return
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(s, "fea_result_item")
        layout.prop(s, "fea_result_quantity")
        layout.prop(s, "fea_result_show")
        row = layout.row()
        row.prop(s, "fea_result_deformed")
        sub = row.row()
        sub.enabled = s.fea_result_deformed
        sub.prop(s, "fea_result_scale")
        layout.prop(s, "fea_dirs_show")
        col = layout.column(align=True)
        col.enabled = s.fea_dirs_show
        col.prop(s, "fea_dirs_spacing")
        col.prop(s, "fea_dirs_scale")

        box = layout.box()
        box.use_property_split = False
        if legend:
            box.label(text=f"{legend['title']} ({legend['unit']})", icon='COLOR')
            if legend.get("available"):
                col = box.column(align=True)
                _kv(col, "Mínimo / máximo", f"{legend['min']:.4g} / {legend['max']:.4g}")
                col.label(text="Azul negativo · rojo positivo · gris sin dato" if legend["signed"]
                          else "Escala de bajo (violeta) a alto (amarillo)")
                if legend.get("section"):
                    _wrapped(box, context, "Por metro de lámina, de las tensiones de ambas caras. Ejes locales: "
                                           "x = proyección del X global. N > 0 tracción; M > 0 tracciona el "
                                           "extradós.", icon='INFO')
            else:
                _wrapped(box, context, legend.get("reason", "Sin este dato en el resultado elegido."), icon='INFO')
            if legend.get("scale") is not None and s.fea_result_deformed:
                _kv(box.column(), "Escala de la deformada", f"× {legend['scale']:.4g}")
            dirs = legend.get("directions")
            if dirs and s.fea_dirs_show:
                if dirs.get("count"):
                    col = box.column(align=True)
                    _kv(col, "Cruces de N", f"{dirs['count']}, cada {dirs['spacing']:.3g} m")
                    _kv(col, "Escala de las cruces", f"1 m = {dirs['scale']:.4g} kN/m")
                    col.label(text="Rojo, puntas hacia fuera: tracción")
                    col.label(text="Azul, puntas hacia dentro: compresión")
                    if dirs.get("omitted"):
                        _wrapped(box, context, f"{dirs['omitted']} brazos con |N| menor que el 2 % del máximo "
                                               f"({dirs['max']:.4g} kN/m) no se dibujan.", icon='INFO')
                else:
                    _wrapped(box, context, "Direcciones de N: " + dirs.get("reason", "sin datos."), icon='INFO')
            if legend.get("kind") == "BUCKLE":
                _wrapped(box, context, f"Factor de pandeo lineal {legend['factor']:.4g} (de {legend['factor_source']}): "
                                       "sobreestima la carga crítica de un cascarón real; no es una capacidad.",
                         icon='ERROR')
        row = box.row(align=True)
        row.operator("candela.viewport_colors", text="Ver mapa").mode = 'ATTRIBUTE'
        row.operator("candela.viewport_colors", text="Ver material").mode = 'MATERIAL'
        box.operator("candela.fea_export_section", icon='EXPORT')

        if summary.get("reactions"):
            box = layout.box()
            box.use_property_split = False
            box.label(text="Reacciones del .dat frente a cargas (Fz, kN)", icon='CHECKMARK')
            col = box.column(align=True)
            for r in summary["reactions"]:
                _kv(col, r["step"], f"{r['reaction_kN'][2]:.2f} / {-r['load_kN'][2]:.2f}")
        for ch in summary.get("checks", []):
            _wrapped(layout, context, ch["message"], icon=LEVEL_ICONS[ch["level"]])
        _wrapped(layout, context, "Resultados de un análisis lineal con los datos que exportaste. Interpretarlos "
                                  "y verificar el diseño corresponde a un ingeniero.", icon='LIGHT')


class CANDELA_PT_modularity(_Sub, bpy.types.Panel):
    bl_label = "Modularidad"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = _shell(context)
        s = obj.candela
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.label(text="Retículas de paraguas:")
        row = layout.row(align=True)
        row.use_property_split = False
        for key, (label, _desc, _p) in GRID_PRESETS.items():
            row.operator("candela.grid_preset", text=label).preset = key
        col = layout.column(align=True)
        col.prop(s, "grid_count_x")
        col.prop(s, "grid_count_y")
        col = layout.column(align=True)
        col.prop(s, "grid_gap_x")
        col.prop(s, "grid_gap_y")
        layout.prop(s, "grid_pattern")
        col = layout.column(align=True)
        col.prop(s, "grid_step_x")
        col.prop(s, "grid_step_y")
        layout.prop(s, "grid_instancing")
        gr = build.report_of(obj).get("grid")
        if not gr:
            _wrapped(layout, context, "Un solo módulo. Aumenta los módulos en X o Y para crear una retícula; "
                                      "funciona con cualquier tipo de cascarón.", icon='INFO')
            return
        box = layout.box()
        box.use_property_split = False
        col = box.column(align=True)
        _kv(col, "Módulos", "{} ({} × {})".format(gr["modules"], *gr["count"]))
        _kv(col, "Paso", "{:.2f} × {:.2f} m".format(*gr["pitch"]))
        _kv(col, "Huella total", "{:.1f} × {:.1f} m".format(*gr["footprint"]))
        _kv(col, "Representación", "instancias (Geometry Nodes)" if gr.get("instanced") else "geometría real")
        for axis, key in (("X", "joint_step_x"), ("Y", "joint_step_y")):
            if gr[key] is not None:
                _kv(col, f"Salto en juntas {axis}", f"{gr[key]:.3f} m")
        _wrapped(box, context, "Los totales de concreto, cimbra y peso son de toda la retícula; el despiece "
                               "de cimbra es de un juego que se reutiliza; el JSON exporta un módulo y sus "
                               "colocaciones.", icon='INFO')


class CANDELA_PT_visual(_Sub, bpy.types.Panel):
    bl_label = "Visualización"

    def draw(self, context):
        layout = self.layout
        obj = _shell(context)
        s = obj.candela
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(obj, "display_type", text="Mostrar como")
        layout.prop(obj, "show_wire", text="Malla (alambre)")

        box = layout.box()
        box.prop(s, "show_rulings")
        col = box.column()
        col.enabled = s.show_rulings
        col.prop(s, "ruling_spacing")
        col.prop(s, "ruling_layer")
        col.prop(s, "ruling_radius")
        if s.show_rulings and s.thickness_mode == 'NORMAL' and s.ruling_layer != s.thickness_reference:
            _wrapped(col, context, "Capa desfasada normalmente: no es reglada; se dibujan curvas muestreadas.",
                     icon='INFO')

        box = layout.box()
        box.prop(s, "show_normals")
        col = box.column(align=True)
        col.enabled = s.show_normals
        col.prop(s, "normal_length")
        col.prop(s, "normal_stride")

        layout.prop(s, "show_supports")

        box = layout.box()
        box.prop(s, "analysis")
        if s.analysis != 'NONE':
            legend = build.report_of(obj).get("analysis") or {}
            if legend:
                unit = legend.get("unit", "")
                box.label(text=f"Rango: {legend['min']:.4g} … {legend['max']:.4g} {unit}")
                if legend.get("per_module"):
                    box.label(text="Alturas de un módulo (retícula instanciada)", icon='INFO')
                if legend.get("mode") == "SLOPE":
                    box.label(text=f"Rojo: pendiente > {legend['limit']:.0f}°")
            row = box.row(align=True)
            row.operator("candela.viewport_colors", text="Ver mapa").mode = 'ATTRIBUTE'
            row.operator("candela.viewport_colors", text="Ver material").mode = 'MATERIAL'


class CANDELA_PT_report(_Sub, bpy.types.Panel):
    bl_label = "Constructibilidad y métricas"

    def draw(self, context):
        layout = self.layout
        obj = _shell(context)
        s = obj.candela
        rep = build.report_of(obj)
        row = layout.row()
        row.use_property_split = True
        row.prop(s, "pour_slope_limit")
        if "geometry" not in rep:
            layout.label(text="Sin datos: pulsa Regenerar.", icon='INFO')
            return
        g, t, c, f, m = (rep[k] for k in ("geometry", "thickness", "concrete", "formwork", "mesh"))

        box = layout.box()
        box.label(text="Geometría (analítica)", icon='SURFACE_DATA')
        col = box.column(align=True)
        _kv(col, "Área en planta", _m(g["plan_area"], "m²", 2))
        _kv(col, "Área referencia", _m(g["area_reference"], "m²", 3))
        _kv(col, "Área intradós / extradós", f"{g['area_intrados']:.3f} / {g['area_extrados']:.3f} m²")
        _kv(col, "Cotas z (concreto)", f"{g['z_concrete_min']:.3f} … {g['z_concrete_max']:.3f} m")
        _kv(col, "Pendiente máxima", f"{g['slope_max_deg']:.1f}°")
        _kv(col, "K gaussiana", f"{g['gauss_min']:.4g} … {g['gauss_max']:.4g} 1/m²")
        _kv(col, "Radio de curvatura mín.", _m(g["radius_min"], "m", 3))

        box = layout.box()
        box.label(text="Espesor", icon='MOD_SOLIDIFY')
        col = box.column(align=True)
        _kv(col, "Modo / exacta", f"{t['mode'].lower()} / {t['reference'].lower()}")
        _kv(col, "Espesor normal", f"{t['normal_min'] * 1000:.1f} … {t['normal_max'] * 1000:.1f} mm")
        _kv(col, "Espesor vertical", f"{t['vertical_min'] * 1000:.1f} … {t['vertical_max'] * 1000:.1f} mm")
        if t["mode"] == "VERTICAL":
            col.label(text="(t normal ≈ t vertical · cos α, primer orden)")
        if rep.get("capitals"):
            _kv(col, "En los capiteles", f"hasta {rep['capitals'][0]['thickness_max'] * 1000:.0f} mm")

        box = layout.box()
        box.label(text="Concreto (cuantificación)", icon='MOD_PHYSICS')
        col = box.column(align=True)
        _kv(col, "Volumen cascarón", _m(c["volume_shell"], "m³", 4))
        if c["volume_beams"]:
            _kv(col, "Volumen vigas", _m(c["volume_beams"], "m³", 4))
        if c.get("volume_ribs"):
            _kv(col, "Volumen nervios", _m(c["volume_ribs"], "m³", 4))
        if c.get("volume_capitals"):
            _kv(col, "Volumen capiteles", _m(c["volume_capitals"], "m³", 4))
        _kv(col, "Masa", f"{c['mass_kg']:,.0f} kg")
        _kv(col, "Peso propio", f"{c['weight_kN']:.2f} kN ({c['weight_per_plan_area']:.2f} kN/m²)")
        _kv(col, "Centroide", "({:.2f}, {:.2f}, {:.2f})".format(*g["centroid"]))

        box = layout.box()
        box.label(text="Cimbra (preliminar)", icon='MOD_LATTICE')
        col = box.column(align=True)
        _kv(col, "Área intradós", _m(f["area_intrados"], "m²", 2))
        _kv(col, "Área de cantos", _m(f["area_edges"], "m²", 3))
        if f["area_beams"]:
            _kv(col, "Área vigas (≈)", _m(f["area_beams"], "m²", 2))
        if f.get("area_ribs"):
            _kv(col, "Área nervios (≈)", _m(f["area_ribs"], "m²", 2))
        for fam in f["rulings"]:
            _kv(col, f"Piezas {fam['family']} @ {fam['spacing']:.2f} m",
                f"{fam['count']} · {fam['total_length']:.1f} m (máx {fam['max_length']:.2f})")
        col.label(text="Generatrices del intradós: " + ("rectas exactas" if f["rulings_exact"] else "aprox."))
        if not rep.get("cutlist"):
            col.label(text="Despiece de piezas: panel Cimbra", icon='INFO')

        box = layout.box()
        box.label(text="Verificación de malla", icon='CHECKMARK')
        col = box.column(align=True)
        _kv(col, "Vértices / caras", f"{m['display_vertices']} / {m['display_faces']}")
        _kv(col, "Cerrada / consistente", f"{'sí' if m['closed'] else 'no'} / {'sí' if m['consistent'] else 'no'}")
        if m["volume_rel_error"] is not None:
            _kv(col, "Vol. malla vs analítico", f"{100 * m['volume_rel_error']:+.4f} %")
        _kv(col, "Área malla vs analítica", f"{100 * m['area_rel_error']:+.4f} %")
        _kv(col, "Error de cuerda máx.", f"{m['chord_error_max'] * 1000:.3f} mm")
        _kv(col, "Alabeo de cuadriláteros", f"{m['quad_warp_max'] * 1000:.3f} mm")

        box = layout.box()
        box.label(text="Comprobaciones", icon='VIEWZOOM')
        for ch in rep.get("checks", []):
            _wrapped(box, context, ch["message"], icon=LEVEL_ICONS[ch["level"]])

        _wrapped(layout, context, "Resultados exclusivamente geométricos. Nada aquí indica seguridad "
                                  "estructural: se requiere análisis por elementos finitos, de pandeo y "
                                  "revisión por un ingeniero.", icon='LIGHT')


class CANDELA_UL_variants(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False, icon='PRESET')


class CANDELA_PT_variants(_Sub, bpy.types.Panel):
    bl_label = "Variantes"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = _shell(context).candela
        row = layout.row()
        row.template_list("CANDELA_UL_variants", "", s, "variants", s, "variant_index", rows=3)
        col = row.column(align=True)
        col.operator("candela.variant_add", icon='ADD', text="")
        col.operator("candela.variant_remove", icon='REMOVE', text="")
        row = layout.row(align=True)
        row.operator("candela.variant_apply", icon='IMPORT', text="Cargar")
        row.operator("candela.variant_update", icon='FILE_TICK', text="Sobrescribir")


class CANDELA_PT_export(_Sub, bpy.types.Panel):
    bl_label = "Exportar"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.operator("candela.export_json", icon='EXPORT')
        layout.operator("candela.import_json", icon='IMPORT')
        layout.operator("candela.export_cutlist", icon='EXPORT')
        layout.operator("candela.export_fea", icon='EXPORT')
        _wrapped(layout, context, "El JSON incluye parámetros, ecuación, métricas, la superficie media, las "
                                  "generatrices, el despiece de cimbra si está activado y, si lo pides, el modelo "
                                  "FEA. El .inp (CalculiX/Abaqus) lleva apoyos, cargas, combinaciones y pandeo; "
                                  "se configura en «Análisis (FEA)». El CSV del despiece se abre en una hoja de "
                                  "cálculo. Para el sólido usa Archivo > Exportar (OBJ, STL, glTF).", icon='INFO')


classes = (CANDELA_PT_main, CANDELA_PT_geometry, CANDELA_PT_structure, CANDELA_PT_formwork, CANDELA_PT_fea,
           CANDELA_PT_fea_results, CANDELA_PT_modularity, CANDELA_PT_visual,
           CANDELA_PT_report, CANDELA_UL_variants, CANDELA_PT_variants, CANDELA_PT_export)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
