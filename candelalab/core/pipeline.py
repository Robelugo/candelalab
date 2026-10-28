"""Evaluación completa: parámetros (dict) -> geometría + informe.

Es la única función que la capa de Blender necesita llamar. Al no depender de
bpy, todo el flujo se prueba fuera de Blender y el mismo resultado se puede
reproducir desde un JSON exportado. Funciona igual para un parche (hypar) que
para N parches (bóveda radial): el mallado lo aporta cada familia.
"""

from __future__ import annotations

import dataclasses
import math
import time
from dataclasses import dataclass, field

from . import SCHEMA_VERSION
from . import hypar, radial, umbrella  # noqa: F401  (registran sus generadores y malladores)
from .capitals import capital_volume, make_capitals, thicken_solid
from .checks import run_checks
from .colormaps import sequential, slope_color
from .elements import EDGE_KEYS, build_crease_ribs, build_edge_beams, build_free_edge_beams, support_markers
from .geometry import SectorDomain
from .formwork import Cutlist, compute_cutlist, formwork_mesh
from .geometry import Vec3
from .grid import assemble, is_grid
from .meshing import PART_EDGE, PART_INTRADOS, MeshData, max_quad_warp, mesh_area, mesh_volume, topology_report
from .metrics import chord_error, model_extrema, model_integrals, thickness_report
from .model import INTRADOS, VERTICAL, ShellModel, build_model, generator_defaults, mesher_for
from .rulings import Ruling, model_rulings, ruling_stats

GRAVITY = 9.80665


@dataclass
class Evaluation:
    params: dict
    model: ShellModel
    shell_mesh: MeshData
    display_rulings: list[Ruling] = field(default_factory=list)
    normal_glyphs: list[tuple[Vec3, Vec3]] = field(default_factory=list)
    beams_mesh: MeshData | None = None
    supports_mesh: MeshData | None = None
    analysis_colors: list[tuple[float, float, float, float]] | None = None
    report: dict = field(default_factory=dict)
    module: "Evaluation | None" = None             # en una retícula: la evaluación del módulo
    placements: list[dict] = field(default_factory=list)
    cutlist: Cutlist | None = None                  # despiece de cimbra (un juego, en coordenadas del módulo)
    formwork_mesh: MeshData | None = None
    ribs_mesh: MeshData | None = None               # nervios bajo las limas (rigidizadores)


def resolve_params(params: dict) -> dict:
    kind = params.get("shell_type", "HYPAR")
    merged = generator_defaults(kind)
    merged.update(params)
    return merged


def supports_edge_beams(model: ShellModel) -> bool:
    """Vigas por lado (contorno rectangular) o en el borde libre (bóveda radial)."""
    return model.outline is not None or supports_free_edge_beam(model)


def supports_free_edge_beam(model: ShellModel) -> bool:
    return model.outline is None and all(isinstance(p.domain, SectorDomain) for p in model.patches)


def evaluate(params: dict) -> Evaluation:
    """Evalúa un módulo y, si se pide una retícula, la ensambla a partir de él."""
    t_start = time.perf_counter()
    p = resolve_params(params)
    ev = _evaluate_module(p)
    if is_grid(p):
        ev = assemble(ev, p)
    ev.report["timing_ms"] = 1000.0 * (time.perf_counter() - t_start)
    return ev


def _evaluate_module(p: dict) -> Evaluation:
    t_start = time.perf_counter()
    model = build_model(p["shell_type"], p)
    th = model.thickness
    nu, nv = int(p["res_x"]), int(p["res_y"])
    mesh_solid, mesh_surface = mesher_for(model.kind)
    errors: list[tuple[str, str]] = []

    # Capiteles: engrosamientos bajo los apoyos (bajan el intradós)
    if p["capital_enabled"] and model.supports:
        try:
            model = dataclasses.replace(model, capitals=make_capitals(
                model, float(p["capital_radius"]), float(p["capital_extra"])))
        except ValueError as exc:
            errors.append(("CAPITAL_INVALID", str(exc)))

    solid = mesh_solid(model, nu, nv)
    if model.capitals:
        thicken_solid(solid, model)
    shell = solid if p["output"] == "SOLID" else mesh_surface(model, nu, nv, 0.0)

    # Vigas de borde (cascarones con contorno rectangular)
    beam_edges = {k: bool(p[f"beam_{k}"]) for k in EDGE_KEYS}
    beams_mesh, beam_volume, beam_plan_area = None, 0.0, 0.0
    if any(beam_edges.values()) and model.outline is not None:
        try:
            gx, gy = model.grid_factor
            beams_mesh, beam_volume, beam_plan_area = build_edge_beams(
                model, beam_edges, float(p["beam_width"]), float(p["beam_depth"]), (nu * gx, nv * gy))
        except ValueError as exc:
            errors.append(("BEAMS_INVALID", str(exc)))
    elif p["beam_free_edge"] and supports_free_edge_beam(model):
        try:
            beams_mesh, beam_volume, beam_plan_area = build_free_edge_beams(
                model, float(p["beam_width"]), float(p["beam_depth"]), 2 * nv)
        except ValueError as exc:
            errors.append(("BEAMS_INVALID", str(exc)))

    # Rigidizadores: nervios bajo las limas
    ribs_mesh, rib_volume, rib_plan_area = None, 0.0, 0.0
    if p["rib_creases"] and model.creases:
        try:
            ribs_mesh, rib_volume, rib_plan_area = build_crease_ribs(
                model, float(p["rib_width"]), float(p["rib_depth"]), max(nu, nv))
        except ValueError as exc:
            errors.append(("RIBS_INVALID", str(exc)))

    supports_mesh = None
    if p["show_supports"] and model.supports:
        supports_mesh = support_markers(model.supports, float(p["support_size"]), float(p["ground_level"]), model)

    # Generatrices: las de visualización en la capa elegida; las de cimbra siempre en el intradós.
    spacing = float(p["ruling_spacing"])
    display_rulings: list[Ruling] = []
    if p["show_rulings"]:
        display_rulings, _ = model_rulings(model, spacing, th.offset_of(p["ruling_layer"]), th.mode)
    formwork_rulings, steps = model_rulings(model, spacing, th.offset_of(INTRADOS), th.mode)

    # Despiece de cimbra: tablas y vigas sobre las generatrices del intradós, y puntales.
    cutlist, cimbra = None, None
    if p["formwork_enabled"]:
        try:
            cutlist = compute_cutlist(model, p)
        except ValueError as exc:
            errors.append(("CUTLIST_INVALID", str(exc)))
        else:
            cimbra = formwork_mesh(cutlist, p) if p["formwork_show"] else None

    normal_glyphs: list[tuple[Vec3, Vec3]] = []
    if p["show_normals"]:
        stride = max(1, int(p["normal_stride"]))
        length = float(p["normal_length"])
        top = mesh_surface(model, nu, nv, th.offsets[1] if p["output"] == "SOLID" else 0.0)
        seen = set()
        for grid in top.grids:
            for row in grid[::stride]:
                for v in row[::stride]:
                    if v in seen:
                        continue
                    seen.add(v)
                    a = top.vertices[v]
                    x, y = top.plan_points[v]
                    n = model.patches[top.vertex_patch[v]].surface.unit_normal(x, y)
                    normal_glyphs.append((a, (a[0] + length * n[0], a[1] + length * n[1], a[2] + length * n[2])))

    # --- métricas analíticas ---
    slope_limit = float(p["pour_slope_limit"])
    integ = model_integrals(model, slope_limit)
    ext = model_extrema(model)
    thick = thickness_report(th, ext)
    creases = integ["creases"]

    concrete_z = [v[2] for v in solid.vertices]
    for extra in (beams_mesh, ribs_mesh):
        if extra is not None:
            concrete_z += [v[2] for v in extra.vertices]

    t = th.thickness
    cap_volumes = [capital_volume(model, c) for c in model.capitals]
    cap_volume = sum(cap_volumes)
    volume_total = integ["volume"] + cap_volume + beam_volume + rib_volume
    density = float(p["concrete_density"])
    mass = density * volume_total
    weight = mass * GRAVITY / 1000.0

    mesh_vol = mesh_volume(solid)
    mesh_in = mesh_area(solid, {PART_INTRADOS})
    edge_area = t * model.outline_perimeter if th.mode == VERTICAL else mesh_area(solid, {PART_EDGE})
    beam_form = mesh_area(beams_mesh, {PART_INTRADOS, PART_EDGE}) if beams_mesh is not None else 0.0
    rib_form = mesh_area(ribs_mesh, {PART_INTRADOS, PART_EDGE}) if ribs_mesh is not None else 0.0
    topo = topology_report(solid)

    report = {
        "schema": SCHEMA_VERSION,
        "shell_type": p["shell_type"],
        "definition": model.definition,
        "geometry": {
            "patches": len(model.patches),
            "plan_area": integ["plan_area"],
            "area_reference": integ["area_reference"],
            "area_intrados": integ["area_intrados"],
            "area_extrados": integ["area_extrados"],
            "area_mid": integ["area_mid"],
            "z_ref_min": ext["z_ref_min"],
            "z_ref_max": ext["z_ref_max"],
            "z_concrete_min": min(concrete_z),
            "z_concrete_max": max(concrete_z),
            "slope_max_deg": math.degrees(ext["slope_max"]),
            "steep_area_fraction": integ["steep_area_fraction"],
            "gauss_min": ext["gauss_min"],
            "gauss_max": ext["gauss_max"],
            "radius_min": ext["radius_min"],
            "centroid": list(integ["centroid"]),
            "span_thickness_ratio": model.characteristic_span / t,
            "creases": {
                "count": len(model.creases),
                "length": creases["length"],
                "valleys": creases["valleys"],
                "ridges": creases["ridges"],
                "mixed": creases["mixed"],
                "valley_length": creases["valley_length"],
                "ridge_length": creases["ridge_length"],
                "normal_angle_min": creases["delta_min"] if model.creases else None,
                "normal_angle_max": creases["delta_max"] if model.creases else None,
                "miter_volume": creases["volume"],
            },
        },
        "thickness": {
            "value": t,
            "mode": th.mode,
            "reference": th.reference,
            **thick,
            "offset_fold_factor": ext["offset_fold_factor"],
        },
        "concrete": {
            "volume_shell": integ["volume"],
            "volume_beams": beam_volume,
            "volume_ribs": rib_volume,
            "volume_capitals": cap_volume,
            "volume_total": volume_total,
            "density": density,
            "mass_kg": mass,
            "weight_kN": weight,
            "weight_per_plan_area": weight / integ["plan_area"],
        },
        "formwork": {
            "area_intrados": integ["area_intrados"],
            "area_edges": edge_area,
            "area_beams": beam_form,
            "beam_plan_area": beam_plan_area,
            "area_ribs": rib_form,
            "rib_plan_area": rib_plan_area,
            "rulings": ruling_stats(formwork_rulings, steps),
            "rulings_exact": th.is_exact_layer(INTRADOS),
        },
        "cutlist": cutlist.summary if cutlist is not None else None,
        "mesh": {
            "output": p["output"],
            **topo,
            "display_vertices": len(shell.vertices),
            "display_faces": len(shell.faces),
            "volume_mesh": mesh_vol,
            "volume_rel_error": ((mesh_vol - integ["volume"] - cap_volume) / (integ["volume"] + cap_volume)
                                 if integ["volume"] > 0 else None),
            "area_intrados_mesh": mesh_in,
            "area_rel_error": (mesh_in - integ["area_intrados"]) / integ["area_intrados"],
            "quad_warp_max": max_quad_warp(solid, {PART_INTRADOS}),
            "chord_error_max": max(chord_error(patch, nu, nv) for patch in model.patches),
        },
        "supports": [{"name": s.name, "kind": s.kind, "position": list(s.position)} for s in model.supports],
        "capitals": [{"support": c.name, "center": list(c.center), "radius": c.radius, "extra": c.extra,
                      "thickness_max": t + c.extra, "volume": v, "fold_factor": _capital_fold(model, c),
                      "mesh_size": math.sqrt(integ["plan_area"] / max(1, sum(1 for q in solid.face_parts
                                                                              if q == PART_INTRADOS)))}
                     for c, v in zip(model.capitals, cap_volumes)],
        "beams_supported": supports_edge_beams(model),
        "analysis": None,
    }

    colors = None
    if p["analysis"] != "NONE":
        colors, legend = _analysis_colors(p["analysis"], model, shell, slope_limit)
        report["analysis"] = legend

    report["checks"] = run_checks(report, p, errors)
    report["timing_ms"] = 1000.0 * (time.perf_counter() - t_start)

    return Evaluation(p, model, shell, display_rulings, normal_glyphs, beams_mesh, supports_mesh, colors, report,
                      cutlist=cutlist, formwork_mesh=cimbra, ribs_mesh=ribs_mesh)


def _capital_fold(model: ShellModel, cap) -> float | None:
    """Factor 1 − s·κ del desfase normal más profundo (s0 − Δ) en el centro del capitel (< 0: se pliega)."""
    th = model.thickness
    if th.mode == VERTICAL:
        return None
    s = th.offset_of(INTRADOS) - cap.extra
    x, y = cap.center
    k1, k2 = model.patch_at(x, y).surface.principal_curvatures(x, y)
    return min(1.0 - s * k1, 1.0 - s * k2)


def _analysis_colors(mode: str, model: ShellModel, mesh: MeshData, slope_limit: float):
    surfs = [model.patches[i].surface for i in mesh.vertex_patch]
    pts = mesh.plan_points
    if mode == "SLOPE":
        vals = [s.slope(x, y) for s, (x, y) in zip(surfs, pts)]
        cols = [slope_color(v, slope_limit) for v in vals]
        return cols, {"mode": mode, "min": math.degrees(min(vals)), "max": math.degrees(max(vals)),
                      "unit": "°", "limit": math.degrees(slope_limit)}
    if mode == "CURVATURE":
        vals = [abs(s.curvatures(x, y)[0]) for s, (x, y) in zip(surfs, pts)]
        lo, hi = min(vals), max(vals)
        span = hi - lo if hi > lo else 1.0
        return [sequential((v - lo) / span) for v in vals], {"mode": mode, "min": -hi, "max": -lo, "unit": "1/m²"}
    if mode == "HEIGHT":
        vals = [v[2] for v in mesh.vertices]
        lo, hi = min(vals), max(vals)
        span = hi - lo if hi > lo else 1.0
        return [sequential((v - lo) / span) for v in vals], {"mode": mode, "min": lo, "max": hi, "unit": "m"}
    raise ValueError(f"Modo de análisis desconocido: {mode}")
