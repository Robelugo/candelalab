"""Adaptador núcleo -> Blender: escribe una Evaluation en mallas, curvas y objetos.

Este módulo no contiene geometría: sólo traduce MeshData / Ruling a datablocks
de Blender. El objeto del cascarón es dueño de objetos hijos auxiliares
(generatrices, normales, apoyos, vigas, nervios, cimbra) que se identifican por la propiedad
personalizada "candela_role" y se crean/borran según los parámetros.
"""

import json
import math
import os
import traceback

import bpy

from . import instancing, materials
from .core.fea import fe_model_from_params
from .core.frd import QUANTITIES, load_results, parse_dat, parse_frd, results_key
from .core.frd import colors as result_colors
from .core.meshing import PART_EXTRADOS, PART_INTRADOS, PART_SURFACE
from .core.pipeline import Evaluation, evaluate
from .props import settings_to_dict, updates_suspended

ROLE = "candela_role"
ROLE_RULINGS = "RULINGS"
ROLE_NORMALS = "NORMALS"
ROLE_SUPPORTS = "SUPPORTS"
ROLE_BEAMS = "BEAMS"
ROLE_PLACEMENTS = "PLACEMENTS"
ROLE_RIBS = "RIBS"
ROLE_FORMWORK = "FORMWORK"  # un juego de cimbra: nunca se instancia
ROLE_DEFORMED = "DEFORMED"  # deformada de los resultados FEA (un módulo)
INSTANCEABLE = (ROLE_RULINGS, ROLE_NORMALS, ROLE_BEAMS, ROLE_RIBS)  # columnas nunca: cada una tiene su longitud
ANALYSIS_ATTR = "candela_analysis"
FEA_ATTR = "candela_fea"            # color del resultado mostrado
FEA_VALUE_ATTR = "candela_fea_valor"  # su valor (mm, MPa), para la hoja de cálculo de Blender
PART_ATTR = "candela_part"

_report_cache: dict = {}


def is_shell(obj) -> bool:
    return obj is not None and obj.type == 'MESH' and obj.candela.is_shell


def report_of(obj) -> dict:
    """Informe guardado en el objeto (se analiza una vez por cambio)."""
    raw = obj.candela.report_json
    hit = _report_cache.get(obj.name)
    if hit is not None and hit[0] == raw:
        return hit[1]
    try:
        rep = json.loads(raw) if raw else {}
    except ValueError:
        rep = {}
    _report_cache[obj.name] = (raw, rep)
    return rep


def params_key(params: dict) -> str:
    """Huella de un juego de parámetros (para saber si un resumen guardado está al día)."""
    import hashlib
    return hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()


def fea_summary(obj) -> dict | None:
    """Resumen del modelo FEA guardado, o None si no hay o los parámetros cambiaron."""
    raw = obj.candela.fea_summary_json
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if data.get("params") == params_key(settings_to_dict(obj.candela)) else None


def store_fea_summary(obj, fe) -> None:
    obj.candela.fea_summary_json = json.dumps({"params": params_key(settings_to_dict(obj.candela)),
                                               "summary": fe.summary, "checks": fe.checks})


def rebuild(obj) -> Evaluation | None:
    """Regenera toda la geometría del cascarón a partir de sus parámetros."""
    settings = obj.candela
    try:
        ev = evaluate(settings_to_dict(settings))
    except ValueError as exc:  # parámetros incompatibles: se conserva la última geometría válida
        settings.report_json = json.dumps({"error": str(exc)})
        return None
    except Exception as exc:  # error de programa: se informa con traza en la consola
        traceback.print_exc()
        settings.report_json = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        return None

    if obj.data.users > 1:  # nunca modificar una malla compartida con otro objeto
        obj.data = obj.data.copy()
    _write_shell_mesh(obj.data, ev)
    materials.assign(obj.data, ["concrete"])

    p = ev.params
    _sync_rulings(obj, ev if p["show_rulings"] and ev.display_rulings else None)
    _sync_normals(obj, ev if p["show_normals"] and ev.normal_glyphs else None)
    _sync_mesh_child(obj, ROLE_SUPPORTS, "Apoyos", ev.supports_mesh, "support")
    _sync_mesh_child(obj, ROLE_BEAMS, "Vigas de borde", ev.beams_mesh, "beam")
    _sync_mesh_child(obj, ROLE_RIBS, "Nervios", ev.ribs_mesh, "rib")
    _sync_formwork(obj, ev.formwork_mesh)
    _sync_instancing(obj, ev)

    settings.report_json = json.dumps(ev.report)
    apply_fea_results(obj)
    return ev


# --- resultados FEA (.frd) --------------------------------------------------------------------

_fea_cache: dict = {}  # nombre del objeto -> (clave, ruta, fecha, FeaResults): el .frd no se guarda en el .blend


def load_fea_results(obj, path):
    """Lee el .frd (y el .dat del mismo nombre, si existe) y lo asocia al cascarón.
    Lanza ValueError u OSError con un mensaje legible."""
    s = obj.candela
    params = settings_to_dict(s)
    res = _read_results(params, path)
    key = results_key(params)
    if s.fea_export_key and s.fea_export_key != key:
        res.summary["checks"].insert(0, {"level": "WARNING", "code": "FRD_CHANGED",
                                         "message": "Los datos FEA cambiaron desde la última exportación: "
                                                    "comprueba que el .frd sea del modelo actual."})
    with updates_suspended():
        s.fea_results_path = path
        s.fea_results_key = key
        s.fea_results_summary = json.dumps(res.summary)
    _fea_cache[obj.name] = (key, path, os.path.getmtime(path), res)
    _set_quietly(s, "fea_result_item", "0")
    apply_fea_results(obj)
    return res


def _read_results(params, path):
    with open(path, encoding="ascii", errors="replace") as fh:
        frd = parse_frd(fh.read())
    dat = None
    dat_path = os.path.splitext(path)[0] + ".dat"
    if os.path.exists(dat_path):
        with open(dat_path, encoding="ascii", errors="replace") as fh:
            dat = parse_dat(fh.read())
    fe, _rep = fe_model_from_params(params)
    module = evaluate({**params, "grid_count_x": 1, "grid_count_y": 1, "formwork_enabled": False,
                       "show_rulings": False, "show_normals": False}).shell_mesh
    return load_results(frd, dat, fe, module.plan_points, module.vertices)


def _set_quietly(settings, key, value):
    with updates_suspended():
        setattr(settings, key, value)


def clear_fea_results(obj):
    s = obj.candela
    with updates_suspended():
        for key in ("fea_results_path", "fea_results_key", "fea_results_summary", "fea_results_legend"):
            setattr(s, key, "")
    _fea_cache.pop(obj.name, None)
    _clear_fea_display(obj)


def fea_results_current(obj) -> bool:
    s = obj.candela
    return bool(s.fea_results_summary) and s.fea_results_key == results_key(settings_to_dict(s))


def apply_fea_results(obj):
    """Colorea el cascarón con el resultado elegido y dibuja la deformada.

    Sólo si los resultados corresponden a los parámetros actuales; si no, se
    quita la vista (el panel lo indica). El .frd se relee si no está en memoria
    (por ejemplo, tras abrir el .blend)."""
    s = obj.candela
    if not fea_results_current(obj):
        _clear_fea_display(obj)
        return
    legend = {}
    try:
        res = _cached_results(obj)
    except (OSError, ValueError) as exc:
        _clear_fea_display(obj)
        s.fea_results_legend = json.dumps({"error": f"No se pudo releer {s.fea_results_path}: {exc}"})
        return
    try:
        item = min(int(s.fea_result_item or 0), len(res.items) - 1)
    except ValueError:
        item = 0
    it = res.items[item]
    quantity = s.fea_result_quantity
    title, unit, signed = QUANTITIES[quantity]
    if it.kind == "BUCKLE" and quantity in ("DISP", "DISP_Z"):
        unit = "(normalizado)"
    values = res.values(item, quantity)
    cols, legend = result_colors(values, signed)
    legend.update(title=title, unit=unit, item=res.labels()[item], kind=it.kind, factor=it.factor,
                  factor_source=it.factor_source, available=any(v is not None for v in values),
                  section=quantity.startswith(("N_", "M_")))
    if not legend["available"]:
        legend["reason"] = res.missing_reason(item, quantity)

    mesh = obj.data
    n_mod, n_obj = len(values), len(mesh.vertices)
    if n_mod == 0 or n_obj % n_mod:
        _clear_fea_display(obj)
        s.fea_results_legend = json.dumps({"error": "La malla del cascarón no coincide con la de los resultados."})
        return
    reps = n_obj // n_mod
    if s.fea_result_show and legend["available"]:
        _write_point_colors(mesh, FEA_ATTR, cols * reps)
        attr = mesh.attributes.get(FEA_VALUE_ATTR) or mesh.attributes.new(FEA_VALUE_ATTR, 'FLOAT', 'POINT')
        attr.data.foreach_set("value", [math.nan if v is None else v for v in values] * reps)
    else:
        _remove_attrs(mesh)
    if s.fea_result_deformed:
        legend["scale"] = _sync_deformed(obj, res.displacement(item), cols if legend["available"] else None,
                                         s.fea_result_scale)
    else:
        remove_child(obj, ROLE_DEFORMED)
    mesh.update()
    s.fea_results_legend = json.dumps(legend)


def _cached_results(obj):
    s = obj.candela
    path = s.fea_results_path
    hit = _fea_cache.get(obj.name)
    mtime = os.path.getmtime(path) if os.path.exists(path) else None
    if hit is not None and hit[0] == s.fea_results_key and hit[1] == path and hit[2] == mtime:
        return hit[3]
    if mtime is None:
        raise OSError("el archivo no existe")
    res = _read_results(settings_to_dict(s), path)
    _fea_cache[obj.name] = (s.fea_results_key, path, mtime, res)
    return res


def _write_point_colors(mesh, name, cols):
    old = mesh.color_attributes.get(name)
    if old is not None:
        mesh.color_attributes.remove(old)
    col = mesh.color_attributes.new(name, 'FLOAT_COLOR', 'POINT')
    col.data.foreach_set("color", [c for rgba in cols for c in rgba])
    mesh.color_attributes.active_color = col
    try:
        mesh.color_attributes.render_color_index = mesh.color_attributes.active_color_index
    except AttributeError:
        pass


def _remove_attrs(mesh):
    old = mesh.color_attributes.get(FEA_ATTR)
    if old is not None:
        mesh.color_attributes.remove(old)
    val = mesh.attributes.get(FEA_VALUE_ATTR)
    if val is not None:
        mesh.attributes.remove(val)


def _clear_fea_display(obj):
    if obj.data is not None:
        _remove_attrs(obj.data)
    remove_child(obj, ROLE_DEFORMED)


def _sync_deformed(obj, disp, cols, scale):
    """Copia de un módulo del cascarón desplazada scale·U (escala automática si scale = 0)."""
    mesh = obj.data
    n = len(disp)
    co = [0.0] * (3 * len(mesh.vertices))
    mesh.vertices.foreach_get("co", co)
    base = [tuple(co[3 * i:3 * i + 3]) for i in range(n)]
    offset = (0.0, 0.0, 0.0)
    points = find_child(obj, ROLE_PLACEMENTS)
    if points is not None and len(points.data.vertices) and len(mesh.vertices) == n:
        offset = tuple(points.data.vertices[0].co)  # retícula instanciada: se dibuja en el primer módulo
    if scale <= 0.0:
        peak = max((math.sqrt(sum(c * c for c in d)) for d in disp if d), default=0.0)
        size = max(max(p[i] for p in base) - min(p[i] for p in base) for i in range(2))
        scale = 0.05 * size / peak if peak > 0.0 else 1.0
    verts = [tuple(p[i] + offset[i] + scale * (d[i] if d else 0.0) for i in range(3)) for p, d in zip(base, disp)]
    faces = [tuple(p.vertices) for p in mesh.polygons if all(v < n for v in p.vertices)]
    child = _ensure_child(obj, ROLE_DEFORMED, "Deformada", 'MESH')
    child.data.clear_geometry()
    child.data.from_pydata(verts, [], faces)
    materials.assign(child.data, ["concrete"])
    if cols is not None:
        _write_point_colors(child.data, FEA_ATTR, cols)
    child.data.update()
    return scale


def _sync_instancing(obj, ev: Evaluation):
    """Con una retícula instanciada, la geometría escrita es la de un módulo y un
    modificador de Geometry Nodes la repite en las colocaciones."""
    targets = [obj] + [c for c in (find_child(obj, r) for r in INSTANCEABLE) if c is not None]
    if not ev.report.get("grid", {}).get("instanced"):
        for target in targets:
            instancing.detach(target)
        remove_child(obj, ROLE_PLACEMENTS)
        return
    points = _ensure_child(obj, ROLE_PLACEMENTS, "Colocaciones", 'MESH')
    points.hide_render = True
    instancing.write_placements(points.data, ev.placements)
    for target in targets:
        instancing.attach(target, points)


# --- malla del cascarón -------------------------------------------------------------


def _write_mesh(mesh, md, smooth_parts=()):
    mesh.clear_geometry()
    mesh.from_pydata(md.vertices, [], md.faces)
    smooth = [part in smooth_parts for part in md.face_parts]
    try:
        mesh.polygons.foreach_set("use_smooth", smooth)
    except (AttributeError, TypeError):
        attr = mesh.attributes.get("sharp_face") or mesh.attributes.new("sharp_face", 'BOOLEAN', 'FACE')
        attr.data.foreach_set("value", [not s for s in smooth])
    if md.sharp_edges:
        wanted = {(a, b) if a < b else (b, a) for a, b in md.sharp_edges}
        ev = [0] * (2 * len(mesh.edges))
        mesh.edges.foreach_get("vertices", ev)
        flags = []
        for i in range(len(mesh.edges)):
            a, b = ev[2 * i], ev[2 * i + 1]
            flags.append(((a, b) if a < b else (b, a)) in wanted)
        try:
            mesh.edges.foreach_set("use_edge_sharp", flags)
        except (AttributeError, TypeError):
            attr = mesh.attributes.get("sharp_edge") or mesh.attributes.new("sharp_edge", 'BOOLEAN', 'EDGE')
            attr.data.foreach_set("value", flags)


def _write_shell_mesh(mesh, ev: Evaluation):
    md = ev.shell_mesh
    _write_mesh(mesh, md, smooth_parts=(PART_EXTRADOS, PART_INTRADOS, PART_SURFACE))
    attr = mesh.attributes.get(PART_ATTR) or mesh.attributes.new(PART_ATTR, 'INT', 'FACE')
    attr.data.foreach_set("value", md.face_parts)

    old = mesh.color_attributes.get(ANALYSIS_ATTR)
    if old is not None:
        mesh.color_attributes.remove(old)
    if ev.analysis_colors:
        col = mesh.color_attributes.new(ANALYSIS_ATTR, 'FLOAT_COLOR', 'POINT')
        flat = [c for rgba in ev.analysis_colors for c in rgba]
        col.data.foreach_set("color", flat)
        mesh.color_attributes.active_color = col
        try:
            mesh.color_attributes.render_color_index = mesh.color_attributes.active_color_index
        except AttributeError:
            pass
    mesh.update()


# --- objetos hijos ----------------------------------------------------------------------


def find_child(owner, role):
    for child in owner.children:
        if child.get(ROLE) == role:
            return child
    return None


def remove_child(owner, role):
    child = find_child(owner, role)
    if child is None:
        return
    data = child.data
    bpy.data.objects.remove(child, do_unlink=True)
    if data is not None and data.users == 0:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)


def remove_all_children(owner):
    for role in (ROLE_RULINGS, ROLE_NORMALS, ROLE_SUPPORTS, ROLE_BEAMS, ROLE_RIBS, ROLE_PLACEMENTS, ROLE_FORMWORK,
                 ROLE_DEFORMED):
        remove_child(owner, role)


def _ensure_child(owner, role, label, kind):
    child = find_child(owner, role)
    if child is not None:
        if child.data is not None and child.data.users > 1:
            child.data = child.data.copy()
        return child
    name = f"{owner.name} · {label}"
    data = bpy.data.curves.new(name, 'CURVE') if kind == 'CURVE' else bpy.data.meshes.new(name)
    child = bpy.data.objects.new(name, data)
    for col in owner.users_collection:
        col.objects.link(child)
    child.parent = owner
    child[ROLE] = role
    child.hide_select = True
    return child


def _sync_mesh_child(owner, role, label, md, material_key):
    if md is None:
        remove_child(owner, role)
        return
    child = _ensure_child(owner, role, label, 'MESH')
    _write_mesh(child.data, md)
    materials.assign(child.data, [material_key])
    child.data.update()


def _sync_formwork(owner, md):
    """Tablas, vigas y puntales en un hijo con tres materiales (índice = parte)."""
    if md is None:
        remove_child(owner, ROLE_FORMWORK)
        return
    child = _ensure_child(owner, ROLE_FORMWORK, "Cimbra", 'MESH')
    _write_mesh(child.data, md)
    materials.assign(child.data, ["board", "joist", "prop"])
    child.data.polygons.foreach_set("material_index", md.face_parts)
    child.data.update()


def _write_polylines(curve, polylines, radius):
    curve.dimensions = '3D'
    curve.bevel_depth = radius
    curve.bevel_resolution = 1
    curve.use_fill_caps = True
    curve.splines.clear()
    for points, mat_index in polylines:
        sp = curve.splines.new('POLY')
        sp.points.add(len(points) - 1)
        flat = []
        for x, y, z in points:
            flat.extend((x, y, z, 1.0))
        sp.points.foreach_set("co", flat)
        sp.material_index = mat_index
    curve.update_tag()


def _sync_rulings(owner, ev):
    if ev is None:
        remove_child(owner, ROLE_RULINGS)
        return
    child = _ensure_child(owner, ROLE_RULINGS, "Generatrices", 'CURVE')
    materials.assign(child.data, ["ruling_a", "ruling_b"])
    _write_polylines(child.data, [(r.points, r.family) for r in ev.display_rulings], ev.params["ruling_radius"])


def _sync_normals(owner, ev):
    if ev is None:
        remove_child(owner, ROLE_NORMALS)
        return
    child = _ensure_child(owner, ROLE_NORMALS, "Normales", 'CURVE')
    child.hide_render = True
    materials.assign(child.data, ["normal"])
    radius = 0.5 * ev.params["ruling_radius"]
    _write_polylines(child.data, [((a, b), 0) for a, b in ev.normal_glyphs], radius)


# --- operaciones de alto nivel -----------------------------------------------------------


def create_shell(context, name="CandelaLab Hypar", params=None):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)
    obj.location = context.scene.cursor.location
    settings = obj.candela
    with updates_suspended():
        settings.is_shell = True
    if params:
        from .props import apply_dict
        apply_dict(settings, params)
    rebuild(obj)
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj
