"""Retícula modular: copias de un módulo sobre una malla rectangular.

El módulo (un paraguas, una hypar o una bóveda) se evalúa UNA vez. La
retícula es una lista de colocaciones: desplazamiento en planta (centrado en
el origen del objeto), escalonamiento vertical por columna/fila y, según el
patrón, reflexión del módulo en columnas o filas alternas.

Reflejar y trasladar no cambian áreas, volúmenes, curvaturas ni pendientes,
así que esos totales son exactamente N veces los del módulo. Lo que depende
de la posición se recalcula: cotas, columnas hasta el desplante (cambian con
el escalonamiento) y el salto vertical entre bordes contiguos, que en las
retículas de paraguas inclinados de Candela es la banda de luz cenital.

Representación (grid_instancing):
    REAL       la geometría de todos los módulos se copia en una sola malla
    INSTANCES  se entrega UN módulo y las colocaciones; Blender lo instancia con
               Geometry Nodes (memoria y tiempo independientes del número de copias)
    AUTO       instancias cuando la retícula superaría AUTO_INSTANCE_VERTICES vértices
Los totales e informes son idénticos en ambos casos.

Patrones (Candela usaba paraguas inclinados en filas):
    UNIFORM    todos iguales: con inclinación, dientes de sierra (lucernarios)
    MIRROR_X   columnas alternas reflejadas (x → −x)
    MIRROR_Y   filas alternas reflejadas (y → −y)
    MIRROR_XY  ambas
"""

from __future__ import annotations

import copy
import dataclasses
import math

from .checks import INFO, WARNING, run_checks
from .colormaps import sequential
from .elements import support_markers
from .meshing import MeshData

PATTERNS = ("UNIFORM", "MIRROR_X", "MIRROR_Y", "MIRROR_XY")
HEAVY_VERTICES = 300_000
AUTO_INSTANCE_VERTICES = 50_000

# Puntos de partida para retículas de paraguas (tipologías).
PRESETS = {
    "INVERTED": ("Invertidos", "Paraguas invertidos en retícula con juntas de 5 cm",
                 {"umbrella_center_height": 4.0, "umbrella_edge_height": 5.0, "tilt_x": 0.0, "tilt_y": 0.0,
                  "span_x": 10.0, "span_y": 10.0, "grid_count_x": 4, "grid_count_y": 3, "grid_gap_x": 0.05,
                  "grid_gap_y": 0.05, "grid_pattern": "UNIFORM", "grid_step_x": 0.0, "grid_step_y": 0.0}),
    "SAWTOOTH": ("Lucernarios", "Paraguas inclinados iguales: cada borde alto queda sobre el bajo del vecino",
                 {"umbrella_center_height": 4.5, "umbrella_edge_height": 5.5, "tilt_x": math.radians(9.0),
                  "tilt_y": 0.0, "span_x": 12.0, "span_y": 10.0, "grid_count_x": 4, "grid_count_y": 2,
                  "grid_gap_x": 0.05, "grid_gap_y": 0.05, "grid_pattern": "UNIFORM",
                  "grid_step_x": 0.0, "grid_step_y": 0.0}),
    "ALTERNATE": ("Alternos", "Inclinación reflejada en columnas alternas: bordes altos y bajos enfrentados",
                  {"umbrella_center_height": 4.5, "umbrella_edge_height": 5.5, "tilt_x": math.radians(9.0),
                   "tilt_y": 0.0, "span_x": 12.0, "span_y": 10.0, "grid_count_x": 4, "grid_count_y": 2,
                   "grid_gap_x": 0.05, "grid_gap_y": 0.05, "grid_pattern": "MIRROR_X",
                   "grid_step_x": 0.0, "grid_step_y": 0.0}),
}


def is_grid(p: dict) -> bool:
    return int(p.get("grid_count_x", 1)) * int(p.get("grid_count_y", 1)) > 1


def module_extent(model) -> tuple[float, float, float, float]:
    """(xmin, xmax, ymin, ymax) de la planta del módulo."""
    if model.outline is not None:
        xs = [c[0] for c in model.outline.corners]
        ys = [c[1] for c in model.outline.corners]
        return min(xs), max(xs), min(ys), max(ys)
    pts = []
    for patch in model.patches:
        for piece in patch.domain.boundary_pieces():
            if piece[0] == "segment":
                pts += [piece[1], piece[2]]
            else:
                pts += [piece[1](i / 64.0) for i in range(65)]
    return min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)


def placements(p: dict, extent) -> list[dict]:
    nx, ny = int(p["grid_count_x"]), int(p["grid_count_y"])
    xmin, xmax, ymin, ymax = extent
    px = (xmax - xmin) + float(p["grid_gap_x"])
    py = (ymax - ymin) + float(p["grid_gap_y"])
    pattern = p["grid_pattern"]
    out = []
    for j in range(ny):
        for i in range(nx):
            out.append({
                "index": (i, j),
                "offset": ((i - 0.5 * (nx - 1)) * px, (j - 0.5 * (ny - 1)) * py,
                           i * float(p["grid_step_x"]) + j * float(p["grid_step_y"])),
                "mirror": (pattern in ("MIRROR_X", "MIRROR_XY") and i % 2 == 1,
                           pattern in ("MIRROR_Y", "MIRROR_XY") and j % 2 == 1),
            })
    return out


def _tp(pt, mirror, offset):
    return ((-pt[0] if mirror[0] else pt[0]) + offset[0], (-pt[1] if mirror[1] else pt[1]) + offset[1],
            pt[2] + offset[2])


def transform_mesh(md: MeshData, mirror, offset) -> MeshData:
    """Copia reflejada/trasladada. Con un número impar de reflexiones se invierte
    el orden de las caras (conservando el primer vértice y, con él, la diagonal)
    para que las normales sigan apuntando hacia afuera."""
    flip = mirror[0] != mirror[1]
    faces = [(f[0],) + tuple(reversed(f[1:])) for f in md.faces] if flip else list(md.faces)
    plan = [((-x if mirror[0] else x) + offset[0], (-y if mirror[1] else y) + offset[1]) for x, y in md.plan_points]
    return MeshData([_tp(v, mirror, offset) for v in md.vertices], faces, list(md.face_parts), plan,
                    list(md.sharp_edges), md.grid, list(md.vertex_patch))


def joint_steps(model, extent, places, nx: int, ny: int, samples: int = 9) -> dict:
    """Máximo salto vertical de la superficie de referencia entre bordes enfrentados
    de módulos contiguos (en juntas paralelas a Y y a X)."""
    if model.outline is None:
        return {"x": None, "y": None}
    xmin, xmax, ymin, ymax = extent
    us = [k / (samples - 1) for k in range(samples)]
    cache: dict = {}

    def edge(mirror, side):
        """Alturas locales del borde `side` (en coordenadas del mundo) para una reflexión dada.
        Sólo hay cuatro reflexiones posibles: se calcula una vez por combinación."""
        key = (mirror, side)
        if key not in cache:
            cache[key] = [height(mirror, side, u) for u in us]
        return cache[key]

    def height(mirror, side, u):
        mx, my = mirror
        if side in ("right", "left"):
            xw = xmax if side == "right" else xmin            # borde en coordenadas del mundo (sin reflejar)
            x = -xw if mx else xw
            yw = ymin + u * (ymax - ymin)
            y = -yw if my else yw
        else:
            yw = ymax if side == "top" else ymin
            y = -yw if my else yw
            xw = xmin + u * (xmax - xmin)
            x = -xw if mx else xw
        return model.patch_at(x, y).surface.z(x, y)

    def step(a, side_a, b, side_b):
        dz = a["offset"][2] - b["offset"][2]
        return max(abs(ha - hb + dz) for ha, hb in zip(edge(a["mirror"], side_a), edge(b["mirror"], side_b)))

    by_index = {pl["index"]: pl for pl in places}
    step_x = step_y = 0.0
    for (i, j), pl in by_index.items():
        if (i + 1, j) in by_index:
            step_x = max(step_x, step(pl, "right", by_index[(i + 1, j)], "left"))
        if (i, j + 1) in by_index:
            step_y = max(step_y, step(pl, "top", by_index[(i, j + 1)], "bottom"))
    return {"x": step_x if nx > 1 else None, "y": step_y if ny > 1 else None}


def use_instances(base, p: dict, n: int) -> bool:
    mode = p.get("grid_instancing", "AUTO")
    if mode == "INSTANCES":
        return True
    if mode == "REAL":
        return False
    return n * len(base.shell_mesh.vertices) > AUTO_INSTANCE_VERTICES


def assemble(base, p: dict):
    """Evaluation de la retícula a partir de la Evaluation de un módulo.

    Con instancias, shell_mesh / generatrices / normales / vigas son las del
    módulo (Blender las instancia en las colocaciones); los apoyos sí se
    ensamblan, porque cada columna tiene su propia longitud hasta el desplante.
    """
    model = base.model
    extent = module_extent(model)
    places = placements(p, extent)
    n = len(places)
    nx, ny = int(p["grid_count_x"]), int(p["grid_count_y"])
    instanced = use_instances(base, p, n)

    supports = MeshData() if base.supports_mesh is not None else None
    ground = float(p["ground_level"])
    local_supports: dict = {}  # la columna depende sólo del escalón (dz): una por altura distinta
    for pl in places:
        if supports is not None:  # la columna llega al desplante aunque el módulo esté escalonado
            dz = pl["offset"][2]
            if dz not in local_supports:
                local_supports[dz] = support_markers(model.supports, float(p["support_size"]), ground - dz, model)
            supports.extend(transform_mesh(local_supports[dz], pl["mirror"], pl["offset"]))

    if instanced:
        shell, beams, ribs = base.shell_mesh, base.beams_mesh, base.ribs_mesh
        rulings, glyphs, colors = list(base.display_rulings), list(base.normal_glyphs), base.analysis_colors
        dzs = [pl["offset"][2] for pl in places]
        gm = base.report["geometry"]
        z_range = (gm["z_concrete_min"] + min(dzs), gm["z_concrete_max"] + max(dzs))
        display = (n * len(shell.vertices), n * len(shell.faces))
    else:
        shell = MeshData()
        beams = MeshData() if base.beams_mesh is not None else None
        ribs = MeshData() if base.ribs_mesh is not None else None
        rulings, glyphs, colors = [], [], [] if base.analysis_colors else None
        for pl in places:
            mir, off = pl["mirror"], pl["offset"]
            shell.extend(transform_mesh(base.shell_mesh, mir, off))
            if beams is not None:
                beams.extend(transform_mesh(base.beams_mesh, mir, off))
            if ribs is not None:
                ribs.extend(transform_mesh(base.ribs_mesh, mir, off))
            rulings += [dataclasses.replace(r, points=tuple(_tp(q, mir, off) for q in r.points))
                        for r in base.display_rulings]
            glyphs += [(_tp(a, mir, off), _tp(b, mir, off)) for a, b in base.normal_glyphs]
            if colors is not None:
                colors += base.analysis_colors
        zs = [v[2] for v in shell.vertices] + [v[2] for extra in (beams, ribs) if extra is not None
                                               for v in extra.vertices]
        z_range = (min(zs), max(zs))
        display = (len(shell.vertices), len(shell.faces))

    report = grid_report(base.report, p, places, extent, z_range, display,
                         joint_steps(model, extent, places, nx, ny), instanced)
    # Un solo juego de cimbra, que se traslada de módulo en módulo: se muestra en el primero.
    formwork = (transform_mesh(base.formwork_mesh, places[0]["mirror"], places[0]["offset"])
                if base.formwork_mesh is not None else None)
    if colors is not None and p["analysis"] == "HEIGHT":
        if instanced:  # todas las copias comparten la malla: el mapa de alturas es del módulo
            report["analysis"] = dict(base.report["analysis"], per_module=True)
        else:  # la altura es absoluta: se normaliza en toda la retícula
            zs = [v[2] for v in shell.vertices]
            lo, hi = min(zs), max(zs)
            colors = [sequential((z - lo) / (hi - lo if hi > lo else 1.0)) for z in zs]
            report["analysis"] = {"mode": "HEIGHT", "min": lo, "max": hi, "unit": "m"}
    return dataclasses.replace(base, shell_mesh=shell, display_rulings=rulings, normal_glyphs=glyphs,
                               beams_mesh=beams, supports_mesh=supports, analysis_colors=colors, report=report,
                               module=base, placements=places, formwork_mesh=formwork, ribs_mesh=ribs)


def grid_report(base: dict, p: dict, places, extent, z_range, display, steps: dict, instanced: bool) -> dict:
    n = len(places)
    rep = copy.deepcopy(base)
    g, c, f, m = rep["geometry"], rep["concrete"], rep["formwork"], rep["mesh"]
    for k in ("plan_area", "area_reference", "area_intrados", "area_extrados", "area_mid"):
        g[k] *= n
    cr = g["creases"]
    for k in ("count", "length", "valleys", "ridges", "mixed", "valley_length", "ridge_length", "miter_volume"):
        if cr.get(k) is not None:
            cr[k] *= n
    dzs = [pl["offset"][2] for pl in places]
    g["z_ref_min"] += min(dzs)
    g["z_ref_max"] += max(dzs)
    g["z_concrete_min"], g["z_concrete_max"] = z_range
    cx, cy, cz = base["geometry"]["centroid"]
    pts = [_tp((cx, cy, cz), pl["mirror"], pl["offset"]) for pl in places]  # módulos de igual masa
    g["centroid"] = [sum(q[i] for q in pts) / n for i in range(3)]
    for k in ("volume_shell", "volume_beams", "volume_ribs", "volume_capitals", "volume_total", "mass_kg",
              "weight_kN"):
        c[k] *= n
    for k in ("area_intrados", "area_edges", "area_beams", "beam_plan_area", "area_ribs", "rib_plan_area"):
        f[k] *= n
    for fam in f["rulings"]:
        fam["count"] *= n
        fam["total_length"] *= n
    for k in ("vertices", "faces", "edges", "boundary_edges", "nonmanifold_edges"):
        m[k] *= n
    m["volume_mesh"] *= n
    m["display_vertices"], m["display_faces"] = display

    if rep.get("cutlist"):  # el despiece es de un juego por módulo: no se multiplica
        rep["cutlist"]["uses"] = n
        rep["cutlist"]["level_range"] = [min(dzs), max(dzs)]
    rep["supports"] = [{"name": f"{s['name']}[{pl['index'][0]},{pl['index'][1]}]", "kind": s["kind"],
                        "position": list(_tp(s["position"], pl["mirror"], pl["offset"]))}
                       for pl in places for s in base["supports"]]
    xmin, xmax, ymin, ymax = extent
    nx, ny = int(p["grid_count_x"]), int(p["grid_count_y"])
    pitch = ((xmax - xmin) + float(p["grid_gap_x"]), (ymax - ymin) + float(p["grid_gap_y"]))
    rep["grid"] = {
        "count": [nx, ny],
        "modules": n,
        "pitch": list(pitch),
        "gap": [float(p["grid_gap_x"]), float(p["grid_gap_y"])],
        "pattern": p["grid_pattern"],
        "step": [float(p["grid_step_x"]), float(p["grid_step_y"])],
        "footprint": [(nx - 1) * pitch[0] + (xmax - xmin), (ny - 1) * pitch[1] + (ymax - ymin)],
        "joint_step_x": steps["x"],
        "joint_step_y": steps["y"],
        "instanced": instanced,
    }

    carried = [ch for ch in base["checks"] if ch["code"] in ("BEAMS_INVALID", "CUTLIST_INVALID", "RIBS_INVALID",
                                                              "CAPITAL_INVALID")]
    checks = run_checks(rep, p, [])
    gr = rep["grid"]
    parts = [f"Retícula de {n} módulos ({nx} × {ny}), paso {pitch[0]:.2f} × {pitch[1]:.2f} m, "
             f"huella {gr['footprint'][0]:.1f} × {gr['footprint'][1]:.1f} m."]
    for axis, key in (("X", "joint_step_x"), ("Y", "joint_step_y")):
        if gr[key] is not None and gr[key] > 1e-6:
            parts.append(f"Salto entre bordes contiguos en juntas {axis}: hasta {gr[key]:.2f} m "
                         "(posible banda de luz cenital).")
    checks.append({"level": INFO, "code": "GRID", "message": " ".join(parts)})
    module_vertices = display[0] // n
    if instanced:
        checks.append({"level": INFO, "code": "GRID_INSTANCED",
                       "message": f"Retícula instanciada con Geometry Nodes: {n} copias de un módulo de "
                                  f"{module_vertices:,} vértices (equivale a {display[0]:,} sin duplicarlos)."})
    elif display[0] > HEAVY_VERTICES:
        checks.append({"level": WARNING, "code": "GRID_HEAVY",
                       "message": f"La retícula tiene {display[0]:,} vértices de geometría real; usa la "
                                  "instanciación (Geometry Nodes) o reduce la resolución del módulo."})
    order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    rep["checks"] = sorted(carried + checks, key=lambda ch: order[ch["level"]])
    return rep
