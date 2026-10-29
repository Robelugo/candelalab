"""Modelo de elementos finitos con cargas y apoyos para un programa externo (sin bpy).

CandelaLab NO resuelve el modelo ni interpreta resultados: arma la entrada de
un análisis (geometría, sección, material, apoyos, cargas y pasos) y la
escribe en formato CalculiX/Abaqus (.inp) y en JSON. Todo valor de material y
de carga es un dato del usuario.

Malla
-----
Lámina en la superficie media. Con elementos cuadráticos (S8R, y S6 en el
abanico de la clave de una bóveda) se malla a doble resolución y cada bloque
de 2 × 2 celdas da un elemento: esquinas y nodos intermedios están sobre la
superficie exacta, también en limas y bordes curvos. Con elementos lineales
(S4/S3) se usan las caras de la malla tal cual. Las normales apuntan a +Z.

Espesor: constante con espesor NORMAL. Con espesor VERTICAL el espesor real
varía y se escribe por nodo (*NODAL THICKNESS) como t·cos α (primer orden).

Apoyos
------
* PINNED: traslaciones (1–3) del nodo más cercano al centro del apoyo. Es una
  articulación real; impedir las traslaciones de varios nodos impediría
  también el giro (casi un empotramiento, que dependería de la malla);
* FIXED: traslaciones y giros (1–6) de los nodos de su huella (cuadrado de
  lado «Tamaño»; si no cae ninguno, el más cercano);
* COLUMN: viga vertical (B32/B31, sección cuadrada) desde el desplante hasta
  el nodo del cascarón sobre su eje, con la base empotrada o articulada; o,
  sin columna, empotramiento de la huella.
Las vigas de borde (por lado o en el borde libre de una bóveda) y los nervios
de las limas se modelan como un alma de lámina (espesor = ancho) colgada bajo
la línea de nodos, desde la superficie media hasta su fondo: tienen su
excentricidad vertical real y comparten nodos con el cascarón. Los nervios van
centrados en la lima, como en la geometría; las vigas, bajo el borde (sin el
medio ancho hacia dentro).

Cargas (N, m; Z vertical)
-------------------------
* PP: peso propio (*DLOAD GRAV con la densidad: el programa integra la masa);
* CM: carga muerta adicional por m² de superficie, vertical;
* CV: carga viva por m² en planta, vertical, en toda la planta y en media
  planta (x > 0), porque los cascarones son sensibles a cargas asimétricas;
* PRESION: presión normal uniforme (positiva hacia el intradós).
Las cargas repartidas se convierten en fuerzas nodales consistentes,
∫ q·Nᵢ dA sobre cada elemento (como haría el programa con una carga
distribuida); en elementos cuadráticos las esquinas pueden llevar fuerzas de
signo contrario, y es correcto. Las combinaciones usan factores del usuario.

Salida de resultados
--------------------
CalculiX expande las láminas en sólidos. Con OUTPUT=3D (su valor por defecto
para láminas) escribe los nodos expandidos, con números nuevos, en las caras
superior e inferior: se ve la flexión. Con OUTPUT=2D promedia en el espesor
sobre los nodos originales: sólo membrana. Se declara en el primer paso; frd.py
lee ambas.

Comprobaciones (geométricas)
----------------------------
* Movimientos de sólido rígido que los apoyos no impiden (mecanismo).
* Conectividad del modelo.
* Totales de cada caso, para cotejarlos con las reacciones del programa, y la
  parte aplicada en los propios nodos de apoyo, que CalculiX suma a la
  reacción (RF).
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field

from . import SCHEMA_VERSION
from .capitals import capital_drop, offset_direction
from .elements import EDGE_KEYS, intrados_height
from .geometry import Vec3
from .model import MID, VERTICAL, mesher_for

GRAVITY = 9.80665
COLUMN_ELEMENTS = 4
COMBINED = "COMB"

DISCLAIMER = ("Modelo de entrada generado por CandelaLab. No se ha resuelto ni verificado: materiales, "
              "cargas, factores y condiciones de apoyo son datos del usuario y deben revisarse. El pandeo "
              "lineal sobreestima la carga crítica de cascarones delgados (sensibles a imperfecciones) y "
              "no es una capacidad. Ningún resultado de este modelo indica por sí solo seguridad.")


@dataclass
class LoadCase:
    name: str
    title: str
    gravity: float = 0.0                                          # múltiplo de g sobre la masa
    forces: dict[int, list[float]] = field(default_factory=dict)  # nodo -> [Fx, Fy, Fz] (N)
    area: float | None = None                                     # área cargada (m²)


@dataclass
class FEModel:
    nodes: list[Vec3]
    blocks: list[tuple[str, str, list[tuple[int, ...]]]]   # (tipo, conjunto, conectividades 0-based)
    elsets: dict[str, list[str]]                           # conjuntos formados por otros conjuntos
    nsets: dict[str, list[int]]
    shells: list[tuple[str, float]]                        # (conjunto, espesor)
    nodal_thickness: dict[int, float]
    beams: list[tuple[str, tuple[float, float], Vec3]]     # (conjunto, (a, b), dirección local 1)
    boundary: list[tuple[str, int, int]]                   # (conjunto de nodos, gdl inicial, gdl final)
    material: dict
    cases: list[LoadCase]
    combos: list[tuple[str, str, list[tuple[str, float]]]]
    buckling: list[str]
    buckling_modes: int
    summary: dict
    checks: list[dict]
    plan: dict[int, tuple[float, float]] = field(default_factory=dict)  # punto en planta de cada nodo de lámina
    normals: dict[int, Vec3] = field(default_factory=dict)               # normal (+Z) de cada nodo de lámina


# --- funciones de forma y cuadratura ----------------------------------------------------------


def _q4(x, y):
    return [0.25 * (1 - x) * (1 - y), 0.25 * (1 + x) * (1 - y), 0.25 * (1 + x) * (1 + y), 0.25 * (1 - x) * (1 + y)]


def _q8(x, y):
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    n = [0.25 * (1 + x * a) * (1 + y * b) * (x * a + y * b - 1) for a, b in corners]
    return n + [0.5 * (1 - x * x) * (1 - y), 0.5 * (1 + x) * (1 - y * y),
                0.5 * (1 - x * x) * (1 + y), 0.5 * (1 - x) * (1 - y * y)]


def _t3(x, y):
    return [1 - x - y, x, y]


def _t6(x, y):
    l1, l2, l3 = 1 - x - y, x, y
    return [l1 * (2 * l1 - 1), l2 * (2 * l2 - 1), l3 * (2 * l3 - 1), 4 * l1 * l2, 4 * l2 * l3, 4 * l3 * l1]


_G3 = [(-math.sqrt(0.6), 5 / 9), (0.0, 8 / 9), (math.sqrt(0.6), 5 / 9)]
_QUAD_RULE = [(a, b, wa * wb) for a, wa in _G3 for b, wb in _G3]
_a1, _b1, _w1 = 0.059715871789770, 0.470142064105115, 0.132394152788506
_a2, _b2, _w2 = 0.797426985353087, 0.101286507323456, 0.125939180544827
_TRI_RULE = [(1 / 3, 1 / 3, 0.5 * 0.225)] + [(x, y, 0.5 * w) for a, b, w in ((_a1, _b1, _w1), (_a2, _b2, _w2))
                                              for x, y in ((b, b), (a, b), (b, a))]  # Dunavant, grado 5
_SHAPES = {"S4": (_q4, _QUAD_RULE), "S8R": (_q8, _QUAD_RULE), "S3": (_t3, _TRI_RULE), "S6": (_t6, _TRI_RULE)}


def _table(etype):
    """(peso, N, ∂N/∂ξ, ∂N/∂η) en cada punto de Gauss. Las derivadas por diferencias
    centrales son exactas: las funciones son a lo sumo cuadráticas en cada variable."""
    shape, rule = _SHAPES[etype]
    h = 1e-3
    out = []
    for x, y, w in rule:
        dx = [(a - b) / (2 * h) for a, b in zip(shape(x + h, y), shape(x - h, y))]
        dy = [(a - b) / (2 * h) for a, b in zip(shape(x, y + h), shape(x, y - h))]
        out.append((w, shape(x, y), dx, dy))
    return out


_TABLES = {k: _table(k) for k in _SHAPES}


def element_terms(etype: str, pts: list[Vec3]):
    """[(N, a)] por punto de Gauss: funciones de forma y vector área a = (x_ξ × x_η)·peso."""
    out = []
    for w, n, dx, dy in _TABLES[etype]:
        xs = [sum(d * p[i] for d, p in zip(dx, pts)) for i in range(3)]
        ys = [sum(d * p[i] for d, p in zip(dy, pts)) for i in range(3)]
        a = (xs[1] * ys[2] - xs[2] * ys[1], xs[2] * ys[0] - xs[0] * ys[2], xs[0] * ys[1] - xs[1] * ys[0])
        out.append((n, (a[0] * w, a[1] * w, a[2] * w)))
    return out


# --- construcción del modelo --------------------------------------------------------------------


def build_fe_model(model, p: dict, report: dict | None = None, grid_modules: int = 1) -> FEModel:
    th = model.thickness
    t = th.thickness
    quad = p["fea_element_order"] == "QUADRATIC"
    refine = max(1, int(p.get("fea_refine", 1)))
    nu, nv = int(p["res_x"]) * refine, int(p["res_y"]) * refine
    md = mesher_for(model.kind)[1](model, 2 * nu if quad else nu, 2 * nv if quad else nv, th.offset_of(MID))
    nodes: list[Vec3] = list(md.vertices)
    checks: list[dict] = []

    def add_node(pt) -> int:
        nodes.append(tuple(pt))
        return len(nodes) - 1

    # Cascarón
    shell_conns = _shell_elements(md, quad, nodes)
    shell_types = {}
    for etype, conn in shell_conns:
        shell_types.setdefault(etype, []).append(conn)
    blocks = [(etype, f"ECASC_{etype}", conns) for etype, conns in shell_types.items()]
    elsets = {"ECASCARON": [f"ECASC_{etype}" for etype in shell_types]}
    shells = [("ECASCARON", t)]
    shell_nodes = sorted({v for _e, c in shell_conns for v in c})
    nodal = {}
    if th.mode == VERTICAL:  # espesor real ≈ t·cos α (primer orden)
        nodal = {v: t * model.patches[md.vertex_patch[v]].surface.unit_normal(*md.plan_points[v])[2]
                 for v in shell_nodes}
    if model.capitals:
        # Capitel: el espesor crece Δt y la superficie media baja Δt/2 (el extradós no cambia).
        for v in shell_nodes:
            x, y = md.plan_points[v]
            d = capital_drop(model, x, y)
            base = nodal.get(v, t)
            if d > 0.0:
                m = offset_direction(model, x, y, md.vertex_patch[v])
                nodes[v] = tuple(nodes[v][i] - 0.5 * d * m[i] for i in range(3))
                base += d * (base / t)  # con espesor vertical, Δt también se mide en vertical
            nodal[v] = base

    # Vigas de borde y nervios: almas de lámina bajo una línea de nodos del cascarón
    beams = []
    web_elems = []
    below, middle = {}, {}  # por (nodo superior, peralte): las almas que se cruzan comparten nodos

    def add_web(chain, width, depth, name, kind, outward=None):
        for v in chain:
            if (v, depth) not in below:
                x, y, z = nodes[v]
                zi = intrados_height(model, *md.plan_points[v])
                below[(v, depth)] = add_node((x, y, (zi if zi is not None else z - 0.5 * t) - depth))
        conns = []
        step = 2 if quad else 1
        for k in range(0, len(chain) - step, step):
            a, b = chain[k], chain[k + step]
            corners = [a, b, below[(b, depth)], below[(a, depth)]]
            mids = []
            if quad:
                for v in (a, b):
                    if (v, depth) not in middle:
                        middle[(v, depth)] = add_node(_lerp(nodes[v], nodes[below[(v, depth)]], 0.5))
                m = chain[k + 1]
                mids = [m, middle[(b, depth)], below[(m, depth)], middle[(a, depth)]]
            side = outward or (nodes[b][1] - nodes[a][1], nodes[a][0] - nodes[b][0], 0.0)  # derecha del recorrido
            conns.append(_facing(corners, mids, side, nodes))
        blocks.append(("S8R" if quad else "S4", name, conns))
        shells.append((name, width))
        web_elems.extend((kind, "S8R" if quad else "S4", c, width) for c in conns)

    w, depth = float(p["beam_width"]), float(p["beam_depth"])
    if model.outline is not None:
        outward = {"ymin": (0.0, -1.0, 0.0), "xmax": (1.0, 0.0, 0.0), "ymax": (0.0, 1.0, 0.0), "xmin": (-1.0, 0.0, 0.0)}
        for key in EDGE_KEYS:
            if p[f"beam_{key}"]:
                add_web(md.node_sets[key], w, depth, f"EVIGA_{key.upper()}", "edge_beams", outward[key])
    elif p.get("beam_free_edge") and "free_edge" in md.node_sets:
        loop = md.node_sets["free_edge"]
        add_web(loop + loop[:1], w, depth, "EVIGA_BORDE", "edge_beams")
    if p.get("rib_creases") and model.creases:
        rib_w, rib_d = float(p["rib_width"]), float(p["rib_depth"])
        for name in sorted(k for k in md.node_sets if k.startswith(("axis_", "groin_"))):
            add_web(md.node_sets[name], rib_w, rib_d, f"ENERVIO_{name.upper()}", "ribs")

    # Apoyos
    nsets: dict[str, list[int]] = {}
    boundary: list[tuple[str, int, int]] = []
    columns = []
    ground = float(p["ground_level"])
    size = float(p["support_size"])
    column_mode = p["fea_columns"]
    for sp in model.supports:
        name = _setname(sp.name)
        foot = _footprint(md, shell_nodes, nodes, sp.position, size)
        if sp.kind == "COLUMN" and column_mode != "HEAD":
            top = min(foot, key=lambda v: math.hypot(md.plan_points[v][0] - sp.position[0],
                                                    md.plan_points[v][1] - sp.position[1]))
            x, y, z = nodes[top]
            if z - ground > 1e-6:
                m = COLUMN_ELEMENTS * (2 if quad else 1)
                ids = [add_node((x, y, ground + (z - ground) * k / m)) for k in range(m)] + [top]
                step = 2 if quad else 1
                columns += [tuple(ids[k:k + step + 1]) for k in range(0, m, step)]
                nsets[f"NB_{name}"] = [ids[0]]
                boundary.append((f"NB_{name}", 1, 6 if column_mode == "FIXED_BASE" else 3))
                continue
            checks.append(_c("WARNING", "FEA_COLUMN_SKIPPED",
                             f"El apoyo {sp.name} está en el desplante o por debajo: se empotra sin columna."))
        if sp.kind == "PINNED":
            # Articulación real: un solo nodo. Impedir las traslaciones de varios nodos de la huella
            # también impide el giro (casi un empotramiento, y más cuanto más fina la malla).
            foot = [min(foot, key=lambda v: math.hypot(md.plan_points[v][0] - sp.position[0],
                                                       md.plan_points[v][1] - sp.position[1]))]
        nsets[f"NS_{name}"] = foot
        boundary.append((f"NS_{name}", 1, 3 if sp.kind == "PINNED" else 6))
    if columns:
        blocks.append(("B32" if quad else "B31", "ECOLUMNAS", columns))
        beams.append(("ECOLUMNAS", (size, size), (1.0, 0.0, 0.0)))
    nsets["NAPOYOS"] = sorted({v for name, _a, _b in boundary for v in nsets[name]})
    elsets["EALL"] = ["ECASCARON"] + [b[1] for b in blocks if b[1].startswith(("EVIGA_", "ENERVIO_"))] + \
        (["ECOLUMNAS"] if columns else [])

    # Cargas
    rho = float(p["concrete_density"])
    terms = [(etype, conn, element_terms(etype, [nodes[v] for v in conn])) for etype, conn in shell_conns]
    q_dead, q_live, q_press = (1000.0 * float(p[k]) for k in ("fea_dead_load", "fea_live_load", "fea_pressure"))
    shell_area = sum(math.sqrt(_dot(a, a)) for _e, _c, tm in terms for _n, a in tm)
    plan_area = sum(a[2] for _e, _c, tm in terms for _n, a in tm)

    weight_shell = 0.0
    for _e, conn, tm in terms:
        for n, a in tm:
            tk = sum(ni * nodal[v] for ni, v in zip(n, conn)) if nodal else t
            weight_shell += rho * GRAVITY * tk * math.sqrt(_dot(a, a))
    weight_webs = {"edge_beams": 0.0, "ribs": 0.0}
    for kind, etype, conn, width in web_elems:
        weight_webs[kind] += sum(rho * GRAVITY * width * math.sqrt(_dot(a, a))
                                 for _n, a in element_terms(etype, [nodes[v] for v in conn]))
    weight_cols = sum(rho * GRAVITY * size * size * math.dist(nodes[c[0]], nodes[c[-1]]) for c in columns)

    cases = [LoadCase("PP", "Peso propio", gravity=1.0)]
    if q_dead:
        cases.append(_surface_case("CM", "Carga muerta adicional (por m2 de superficie)", terms,
                                   lambda n, a: (0.0, 0.0, -q_dead * math.sqrt(_dot(a, a))), shell_area))
    if q_live:
        cases.append(_surface_case("CV", "Carga viva (por m2 en planta)", terms,
                                   lambda n, a: (0.0, 0.0, -q_live * a[2]), plan_area))
        if p["fea_live_half"]:
            half = [tr for tr in terms if sum(nodes[v][0] for v in tr[1]) / len(tr[1]) > 0.0]
            cases.append(_surface_case("CV_MEDIA", "Carga viva en media planta (x > 0)", half,
                                       lambda n, a: (0.0, 0.0, -q_live * a[2]),
                                       sum(a[2] for _e, _c, tm in half for _n, a in tm)))
    if q_press:
        cases.append(_surface_case("PRESION", "Presion normal (positiva hacia el intrados)", terms,
                                   lambda n, a: (-q_press * a[0], -q_press * a[1], -q_press * a[2]), shell_area))

    gd, gl = float(p["fea_factor_dead"]), float(p["fea_factor_live"])
    names = {c.name for c in cases}
    dead = [("PP", gd)] + ([("CM", gd)] if "CM" in names else [])
    combos = [(COMBINED, "Combinacion: gG*(PP + CM) + gQ*CV", dead + ([("CV", gl)] if "CV" in names else []))]
    if "CV_MEDIA" in names:
        combos.append((COMBINED + "_MEDIA", "Combinacion con carga viva en media planta",
                       dead + [("CV_MEDIA", gl)]))
    buckling = [c[0] for c in combos] if p["fea_buckling"] else []

    plan = {v: md.plan_points[v] for v in shell_nodes}
    normals = {v: model.patches[md.vertex_patch[v]].surface.unit_normal(*md.plan_points[v]) for v in shell_nodes}
    fe = FEModel(nodes, blocks, elsets, nsets, shells, nodal, beams, boundary,
                 {"name": "CONCRETO", "E": 1e9 * float(p["fea_young_gpa"]), "nu": float(p["fea_poisson"]),
                  "density": rho},
                 cases, combos, buckling, int(p["fea_buckling_modes"]), {}, checks, plan, normals)
    _compact(fe)

    # Resumen y comprobaciones
    weights = {"shell": weight_shell, **weight_webs, "columns": weight_cols}
    totals = {c.name: _case_total(c, sum(weights.values())) for c in cases}
    on_supports = _support_loads(fe, rho)
    for name, _title, parts in combos:
        totals[name] = [sum(f * totals[case][i] for case, f in parts) for i in range(3)]
        on_supports[name] = [sum(f * on_supports[case][i] for case, f in parts) for i in range(3)]
    counts = {}
    for etype, _set, conns in fe.blocks:
        counts[etype] = counts.get(etype, 0) + len(conns)
    free = rigid_body_modes(fe)
    components = _components(fe)
    fe.summary = {
        "schema": SCHEMA_VERSION,
        "nodes": len(fe.nodes),
        "elements": counts,
        "shell_area": shell_area,
        "plan_area": plan_area,
        "weight_kN": {k: v / 1000.0 for k, v in weights.items()},
        "totals_kN": {k: [v / 1000.0 for v in vec] for k, vec in totals.items()},
        "support_loads_kN": {k: [v / 1000.0 for v in vec] for k, vec in on_supports.items()},
        "loaded_area": {c.name: c.area for c in cases if c.area is not None},
        "supports": [{"set": name, "dofs": [a, b], "nodes": len(fe.nsets[name])} for name, a, b in boundary],
        "rigid_body_modes": free,
        "components": components,
        "grid_modules": grid_modules,
        "output": p["fea_output"],
        "thickness": th.thickness,
        "steps": [s["name"] for s in analysis_steps(fe)],
    }
    fe.checks = checks + _fe_checks(fe, p, report, grid_modules, th)
    return fe


def _shell_elements(md, quad: bool, nodes):
    out = []
    if quad:
        for g in md.grids:
            for r in range(0, len(g) - 2, 2):
                for c in range(0, len(g[0]) - 2, 2):
                    cs = [g[r][c], g[r][c + 2], g[r + 2][c + 2], g[r + 2][c]]
                    ms = [g[r][c + 1], g[r + 1][c + 2], g[r + 2][c + 1], g[r + 1][c]]
                    edges = [(cs[k], ms[k]) for k in range(4) if cs[k] != cs[(k + 1) % 4]]  # clave colapsada
                    out.append(_upward([e[0] for e in edges], [e[1] for e in edges], nodes))
    else:
        out = [_upward(list(f), [], nodes) for f in md.faces]
    return out


def _element_type(corners, mids) -> str:
    return {(4, 4): "S8R", (3, 3): "S6", (4, 0): "S4", (3, 0): "S3"}[(len(corners), len(mids))]


def _reverse(corners, mids):
    return [corners[0]] + corners[:0:-1], mids[::-1]


def _upward(corners, mids, nodes):
    """Esquinas antihorarias vistas desde arriba: normal hacia +Z (extradós)."""
    area = sum(nodes[a][0] * nodes[b][1] - nodes[b][0] * nodes[a][1]
               for a, b in zip(corners, corners[1:] + corners[:1]))
    if area < 0.0:
        corners, mids = _reverse(corners, mids)
    return _element_type(corners, mids), tuple(corners + mids)


def _facing(corners, mids, direction, nodes):
    """Orienta un elemento del alma con la normal hacia `direction` (afuera)."""
    c = [nodes[v] for v in corners]
    n = _cross(_sub(c[2], c[0]), _sub(c[3], c[1]))
    if _dot(n, direction) < 0.0:
        corners, mids = _reverse(corners, mids)
    return tuple(corners + mids)


def _footprint(md, candidates, nodes, position, size) -> list[int]:
    h = 0.5 * size + 1e-9
    x0, y0 = position[0], position[1]
    inside = [v for v in candidates
              if abs(md.plan_points[v][0] - x0) <= h and abs(md.plan_points[v][1] - y0) <= h]
    return inside or [min(candidates, key=lambda v: math.dist(nodes[v], position))]


def _surface_case(name, title, terms, load, area) -> LoadCase:
    case = LoadCase(name, title, area=area)
    for _e, conn, tm in terms:
        for n, a in tm:
            f = load(n, a)
            for ni, v in zip(n, conn):
                acc = case.forces.setdefault(v, [0.0, 0.0, 0.0])
                for i in range(3):
                    acc[i] += ni * f[i]
    return case


def _case_total(case: LoadCase, weight: float) -> list[float]:
    tot = [sum(f[i] for f in case.forces.values()) for i in range(3)]
    tot[2] -= case.gravity * weight
    return tot


def _support_loads(fe: FEModel, rho: float) -> dict[str, list[float]]:
    """Carga de cada caso aplicada en los propios nodos de apoyo (N).

    CalculiX la suma a la reacción: el total RF de NAPOYOS vale −carga total +
    esta carga. La de gravedad es la consistente: ∫ ρ·g·t·Nᵢ dA en láminas y
    almas, y ρ·g·A·L repartido (1/6, 2/3, 1/6) o (1/2, 1/2) en vigas. En
    elementos cuadráticos la de una esquina va hacia arriba."""
    supp = set(fe.nsets.get("NAPOYOS", []))
    thick = {child: (name, t) for name, t in fe.shells for child in fe.elsets.get(name, [name])}
    sections = {name: a * b for name, (a, b), _n in fe.beams}
    weight = 0.0
    for etype, elset, conns in fe.blocks:
        for conn in conns:
            if supp.isdisjoint(conn):
                continue
            if elset in thick:
                parent, t = thick[elset]
                nodal = fe.nodal_thickness if parent == "ECASCARON" else {}
                for n, a in element_terms(etype, [fe.nodes[v] for v in conn]):
                    tk = sum(ni * nodal[v] for ni, v in zip(n, conn)) if nodal else t
                    share = sum(ni for ni, v in zip(n, conn) if v in supp)
                    weight += rho * GRAVITY * tk * math.sqrt(_dot(a, a)) * share
            elif elset in sections:
                w = rho * GRAVITY * sections[elset] * math.dist(fe.nodes[conn[0]], fe.nodes[conn[-1]])
                shares = (1 / 6, 2 / 3, 1 / 6) if len(conn) == 3 else (0.5, 0.5)
                weight += w * sum(s for s, v in zip(shares, conn) if v in supp)
    out = {}
    for case in fe.cases:
        vec = [sum(f[i] for v, f in case.forces.items() if v in supp) for i in range(3)]
        vec[2] -= case.gravity * weight
        out[case.name] = vec
    return out


def _compact(fe: FEModel) -> None:
    """Quita los nodos que no usa ningún elemento (los centros de los bloques 2 × 2)."""
    used = sorted({v for _t, _s, conns in fe.blocks for c in conns for v in c})
    new = {v: i for i, v in enumerate(used)}
    fe.nodes = [fe.nodes[v] for v in used]
    fe.blocks = [(t, s, [tuple(new[v] for v in c) for c in conns]) for t, s, conns in fe.blocks]
    fe.nsets = {k: [new[v] for v in vs] for k, vs in fe.nsets.items()}
    fe.nodal_thickness = {new[v]: x for v, x in fe.nodal_thickness.items() if v in new}
    fe.plan = {new[v]: xy for v, xy in fe.plan.items() if v in new}
    fe.normals = {new[v]: n for v, n in fe.normals.items() if v in new}
    for case in fe.cases:
        case.forces = {new[v]: f for v, f in case.forces.items()}


def rigid_body_modes(fe: FEModel) -> int:
    """Movimientos de sólido rígido que los apoyos no impiden (0 = ninguno).

    Un movimiento rígido es u(p) = a + ω × (p − c); restringir la traslación i
    de un nodo impone a_i + ω·((p − c) × e_i) = 0 y restringir su giro i,
    ω_i = 0. El número de movimientos libres es 6 − rango del sistema."""
    if not fe.nodes:
        return 6
    c = [sum(p[i] for p in fe.nodes) / len(fe.nodes) for i in range(3)]
    size = max(max(p[i] for p in fe.nodes) - min(p[i] for p in fe.nodes) for i in range(3)) or 1.0
    gram = [[0.0] * 6 for _ in range(6)]
    for name, first, last in fe.boundary:
        for v in fe.nsets[name]:
            r = [(fe.nodes[v][i] - c[i]) / size for i in range(3)]
            for dof in range(first, last + 1):
                row = [0.0] * 6
                if dof <= 3:
                    e = [0.0, 0.0, 0.0]
                    e[dof - 1] = 1.0
                    row[:3] = e
                    row[3:] = _cross(r, e)
                else:
                    row[dof - 1] = 1.0
                for i in range(6):
                    for j in range(6):
                        gram[i][j] += row[i] * row[j]
    return 6 - _rank(gram)


def _rank(m, tol=1e-9) -> int:
    a = [row[:] for row in m]
    scale = max((abs(a[i][i]) for i in range(len(a))), default=0.0) or 1.0
    rank = 0
    for col in range(len(a)):
        piv = max(range(rank, len(a)), key=lambda r: abs(a[r][col]), default=None)
        if piv is None or abs(a[piv][col]) <= tol * scale:
            continue
        a[rank], a[piv] = a[piv], a[rank]
        for r in range(len(a)):
            if r != rank:
                f = a[r][col] / a[rank][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[rank])]
        rank += 1
    return rank


def _components(fe: FEModel) -> int:
    parent = list(range(len(fe.nodes)))

    def find(v):
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for _t, _s, conns in fe.blocks:
        for c in conns:
            r0 = find(c[0])
            for v in c[1:]:
                rv = find(v)
                if rv != r0:
                    parent[rv] = r0
    return len({find(v) for v in range(len(fe.nodes))})


def _c(level, code, message):
    return {"level": level, "code": code, "message": message}


def _fe_checks(fe: FEModel, p, report, grid_modules, th) -> list[dict]:
    s = fe.summary
    out = []
    if not fe.boundary:
        out.append(_c("ERROR", "FEA_NO_SUPPORTS", "No hay apoyos: el modelo no se puede resolver. Elige una "
                                                  "disposición de apoyos en «Concepto estructural»."))
    elif s["rigid_body_modes"]:
        out.append(_c("WARNING", "FEA_MECHANISM",
                      f"Los apoyos dejan {s['rigid_body_modes']} movimiento(s) de sólido rígido sin impedir: el "
                      "modelo es un mecanismo y el programa no podrá resolverlo. Por ejemplo, dos apoyos "
                      "articulados permiten girar alrededor de la recta que los une. Usa apoyos empotrados, "
                      "más apoyos o columnas empotradas."))
    if s["components"] > 1:
        out.append(_c("WARNING", "FEA_DISCONNECTED", f"El modelo tiene {s['components']} partes sin conectar."))
    if report is not None:
        conc = report["concrete"]
        w = s["weight_kN"]
        unit = conc["density"] * GRAVITY / 1000.0
        shell = (conc["volume_shell"] + conc.get("volume_capitals", 0.0)) * unit
        msg = (f"Peso propio del modelo FE: cascarón {w['shell']:.2f} kN (analítico {shell:.2f} kN, "
               f"{100 * (w['shell'] - shell) / shell:+.2f} %)")
        if w["edge_beams"]:
            msg += (f"; vigas de borde {w['edge_beams']:.2f} kN (el alma va desde la superficie media; "
                    f"cuantificadas {conc['volume_beams'] * unit:.2f} kN)")
        if w["ribs"]:
            msg += (f"; nervios {w['ribs']:.2f} kN (alma desde la superficie media; cuantificados "
                    f"{conc.get('volume_ribs', 0.0) * unit:.2f} kN)")
        if w["columns"]:
            msg += f"; columnas {w['columns']:.2f} kN"
        out.append(_c("INFO", "FEA_WEIGHT", msg + "."))
    if not (p["fea_dead_load"] or p["fea_live_load"] or p["fea_pressure"]):
        out.append(_c("INFO", "FEA_ONLY_SELF_WEIGHT", "Sólo hay peso propio: introduce la carga muerta adicional "
                                                      "y la carga viva de tu normativa."))
    if any(name.startswith("NS_") and last == 3 for name, _first, last in fe.boundary):
        out.append(_c("INFO", "FEA_PINNED_POINT", "Cada apoyo articulado es un nodo del cascarón (una articulación "
                                                  "real): la reacción se concentra en un punto, así que las "
                                                  "tensiones junto al apoyo no son representativas."))
    if any(n.startswith("NB_") for n in fe.nsets):
        out.append(_c("INFO", "FEA_COLUMN_HEAD", "Cada columna se une al cascarón en un nodo: las tensiones "
                                                 "locales alrededor de la cabeza no son representativas."))
    if th.mode == VERTICAL:
        out.append(_c("INFO", "FEA_NODAL_THICKNESS", "Espesor vertical: el espesor real por nodo es t·cos α "
                                                     "(aproximación de primer orden)."))
    if grid_modules > 1:
        out.append(_c("INFO", "FEA_GRID", f"Retícula de {grid_modules} módulos: se exporta un módulo (el primero, "
                                          "sin escalón). Las juntas separan los módulos."))
    if fe.buckling:
        out.append(_c("INFO", "FEA_BUCKLING", "Pandeo lineal incluido: sus factores sobreestiman la carga crítica "
                                              "de cascarones delgados, muy sensibles a imperfecciones. No son "
                                              "una capacidad; hace falta un análisis no lineal con imperfecciones."))
    return out


# --- escritura ------------------------------------------------------------------------------------


def analysis_steps(fe: FEModel) -> list[dict]:
    """Pasos del análisis en el orden del .inp (y del .frd): casos, combinaciones y pandeo."""
    steps = [{"name": c.name, "title": c.title, "kind": "STATIC", "parts": [(c.name, 1.0)]} for c in fe.cases]
    steps += [{"name": n, "title": t, "kind": "STATIC", "parts": parts} for n, t, parts in fe.combos]
    combos = {n: parts for n, _t, parts in fe.combos}
    steps += [{"name": f"PANDEO_{n}", "title": f"Pandeo lineal bajo {n}", "kind": "BUCKLE", "parts": combos[n]}
              for n in fe.buckling]
    return steps


def to_inp(fe: FEModel, title: str) -> str:
    """Archivo de entrada CalculiX/Abaqus (unidades: N, m, kg, s, Pa)."""
    out = ["*HEADING", _ascii(title)[:200]]
    note = out.append

    def comment(text):
        for line in _wrap(_ascii(text), 96):
            note("** " + line)

    comment(DISCLAIMER)
    comment("Unidades: N, m, kg, s, Pa. Z vertical; normal de la lamina hacia +Z (extrados). "
            "Calcular con: ccx -i <archivo sin .inp>")
    comment("Totales de carga por paso (kN) y reaccion RF esperada en NAPOYOS (*NODE PRINT, TOTALS). "
            "CalculiX suma a la reaccion la carga aplicada en los propios nodos de apoyo, asi que "
            "RF = -carga + esa carga:")
    on_supports = fe.summary.get("support_loads_kN", {})
    for name, vec in fe.summary["totals_kN"].items():
        rf = [s - c for s, c in zip(on_supports.get(name, (0.0, 0.0, 0.0)), vec)]
        comment(f"  {name}: carga Fx={vec[0]:.3f} Fy={vec[1]:.3f} Fz={vec[2]:.3f}; "
                f"RF Fx={rf[0]:.3f} Fy={rf[1]:.3f} Fz={rf[2]:.3f}")
    for ch in fe.checks:
        comment(f"[{ch['level']}] {ch['message']}")

    note("*NODE, NSET=NALL")
    out += [f"{i}, {x:.15g}, {y:.15g}, {z:.15g}" for i, (x, y, z) in enumerate(fe.nodes, 1)]
    eid = 0
    for etype, elset, conns in fe.blocks:
        note(f"*ELEMENT, TYPE={etype}, ELSET={elset}")
        for c in conns:
            eid += 1
            note(f"{eid}, " + ", ".join(str(v + 1) for v in c))
    for name, members in fe.elsets.items():
        note(f"*ELSET, ELSET={name}")
        out += _chunks(members)
    for name, ids in fe.nsets.items():
        note(f"*NSET, NSET={name}")
        out += _chunks([str(v + 1) for v in ids])

    m = fe.material
    out += [f"*MATERIAL, NAME={m['name']}", "*ELASTIC", f"{m['E']:.6g}, {m['nu']:.6g}", "*DENSITY", f"{m['density']:.6g}"]
    for elset, thick in fe.shells:
        nodal = ", NODAL THICKNESS" if fe.nodal_thickness and elset == "ECASCARON" else ""
        out += [f"*SHELL SECTION, ELSET={elset}, MATERIAL={m['name']}{nodal}", f"{thick:.6g}"]
    if fe.nodal_thickness:
        note("*NODAL THICKNESS")
        out += [f"{v + 1}, {x:.12g}" for v, x in sorted(fe.nodal_thickness.items())]
    for elset, (a, b), n1 in fe.beams:
        out += [f"*BEAM SECTION, ELSET={elset}, MATERIAL={m['name']}, SECTION=RECT", f"{a:.6g}, {b:.6g}",
                ", ".join(f"{c:.6g}" for c in n1)]
    note("*BOUNDARY")
    out += [f"{name}, {a}, {b}" for name, a, b in fe.boundary]

    cases = {c.name: c for c in fe.cases}
    combos = {name: parts for name, _t, parts in fe.combos}

    def loads(parts):
        g = sum(f * cases[c].gravity for c, f in parts)
        out.append("*DLOAD, OP=NEW")
        if g:
            out.append(f"EALL, GRAV, {g * GRAVITY:.8g}, 0., 0., -1.")
        out.append("*CLOAD, OP=NEW")
        acc: dict[int, list[float]] = {}
        for c, f in parts:
            for v, vec in cases[c].forces.items():
                a = acc.setdefault(v, [0.0, 0.0, 0.0])
                for i in range(3):
                    a[i] += f * vec[i]
        for v in sorted(acc):
            out.extend(f"{v + 1}, {i + 1}, {acc[v][i]:.12g}" for i in range(3) if acc[v][i] != 0.0)

    output = f", OUTPUT={fe.summary.get('output', '3D')}"
    for k, step in enumerate(analysis_steps(fe)):
        first = output if k == 0 else ""  # OUTPUT sólo cuenta en el primer paso
        if step["kind"] == "STATIC":
            comment(f"Paso {step['name']}: {step['title']}")
            out += ["*STEP", "*STATIC"]
            loads(step["parts"])
            out += ["*NODE PRINT, NSET=NAPOYOS, TOTALS=ONLY", "RF", f"*NODE FILE{first}", "U, RF",
                    f"*EL FILE{first}", "S", "*END STEP"]
        else:
            comment(f"{step['title']}: factores de carga de los primeros modos (ver advertencia)")
            out += ["*STEP", "*BUCKLE", f"{fe.buckling_modes}"]
            loads(step["parts"])
            out += [f"*NODE FILE{first}", "U", "*END STEP"]
    return "\n".join(out) + "\n"


def fe_document(fe: FEModel) -> dict:
    """El mismo modelo, independiente del programa (índices base 1, como en el .inp)."""
    eid = 0
    elements = []
    for etype, elset, conns in fe.blocks:
        for c in conns:
            eid += 1
            elements.append({"id": eid, "type": etype, "set": elset, "nodes": [v + 1 for v in c]})
    return {
        "format": "CalculiX/Abaqus: S8R/S6/S4/S3 (lámina), B32/B31 (viga). Índices base 1.",
        "units": {"length": "m", "force": "N", "stress": "Pa", "density": "kg/m3"},
        "disclaimer": DISCLAIMER,
        "nodes": [list(p) for p in fe.nodes],
        "elements": elements,
        "element_sets": fe.elsets,
        "node_sets": {k: [v + 1 for v in vs] for k, vs in fe.nsets.items()},
        "material": fe.material,
        "shell_sections": [{"set": s, "thickness": t} for s, t in fe.shells],
        "nodal_thickness": {str(v + 1): x for v, x in fe.nodal_thickness.items()},
        "beam_sections": [{"set": s, "shape": "RECT", "dimensions": list(d), "direction_1": list(n)}
                          for s, d, n in fe.beams],
        "boundary": [{"set": s, "dofs": [a, b]} for s, a, b in fe.boundary],
        "load_cases": [{"name": c.name, "title": c.title, "gravity_factor": c.gravity, "loaded_area": c.area,
                        "nodal_forces": [[v + 1, *f] for v, f in sorted(c.forces.items())]} for c in fe.cases],
        "combinations": [{"name": n, "title": t, "factors": dict(parts)} for n, t, parts in fe.combos],
        "buckling": {"combinations": fe.buckling, "modes": fe.buckling_modes},
        "summary": fe.summary,
        "checks": fe.checks,
    }


def fe_model_from_params(params: dict) -> tuple[FEModel, dict]:
    """Evalúa un módulo (sin retícula ni cimbra) y arma su modelo FE."""
    from .grid import is_grid
    from .pipeline import evaluate, resolve_params

    full = resolve_params(params)
    modules = int(full["grid_count_x"]) * int(full["grid_count_y"]) if is_grid(full) else 1
    ev = evaluate({**full, "grid_count_x": 1, "grid_count_y": 1, "formwork_enabled": False,
                   "show_rulings": False, "show_normals": False})
    return build_fe_model(ev.model, ev.params, ev.report, modules), ev.report


# --- utilidades -----------------------------------------------------------------------------------

_ASCII = {"²": "2", "³": "3", "×": "x", "·": "*", "≥": ">=", "≤": "<=", "→": "->", "–": "-", "—": "-",
          "°": " grados", "«": '"', "»": '"', "γ": "g", "α": "alfa", "ω": "w", "Δ": "D"}


def _ascii(text: str) -> str:
    for a, b in _ASCII.items():
        text = text.replace(a, b)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width) or [""]


def _chunks(items: list[str], n: int = 16) -> list[str]:
    return [", ".join(items[i:i + n]) for i in range(0, len(items), n)]


def _setname(name: str) -> str:
    s = "".join(ch if ch.isalnum() else "_" for ch in _ascii(name)).upper()
    return s or "APOYO"


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _lerp(a, b, f):
    return (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]))
