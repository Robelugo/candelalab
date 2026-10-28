"""Bóveda radial de N paraboloides hiperbólicos (cúpula por aristas).

Es la familia de Los Manantiales (Xochimilco, 1958: ocho lóbulos) y, con
N = 4 y planta poligonal, de las bóvedas por arista de Bacardí (Cuautitlán).

Parametrización
---------------
N lóbulos alrededor de la clave (origen). El lóbulo k tiene su eje a
θk = 2πk/N y está limitado por dos limas (valles o cumbreras) a θk ± β, con
β = π/N. En coordenadas locales del lóbulo (x' sobre su eje):

    f(x', y') = H + c1·x' + c3·x'² + c5·y'²        con  c5 = −c3 / tan²β

* Es una hypar de eje vertical: det(Hf) = 4·c3·c5 = −4·c3²/tan²β < 0.
* Las limas son GENERATRICES: en la dirección d = (cos β, sin β) se tiene
  Q(d) = c3·cos²β + c5·sin²β = 0. Así cada lima es una recta en 3D que baja
  de la clave (H) al apoyo, y es la misma recta para los dos lóbulos que la
  comparten (continuidad C0 exacta). Toda la bóveda sigue pudiendo cimbrarse
  con piezas rectas.

Con R la distancia en planta de la clave a cada apoyo (extremo de lima):
    c1 = (z_apoyo − H) / (R·cos β)
    c3 = (z_borde − H − c1·X) / X²
X es la distancia de la clave al borde libre medida sobre el eje del lóbulo:
    planta poligonal: X = R·cos β (apotema; el borde libre es la sección por un
                      plano vertical: una parábola plana)
    planta circular:  X = R (el borde libre es la intersección con el cilindro
                      r = R: una curva alabeada)
    plano inclinado:  X = R·cos β + v (tipo Los Manantiales, v = vuelo)

Borde por plano inclinado
-------------------------
El plano contiene la recta que une los dos apoyos del lóbulo (x' = R·cos β,
z = z_apoyo) y pasa por el vértice del borde (x' = X, z = z_borde):

    plano(x') = z_apoyo + m·(x' − R·cos β),     m = (z_borde − z_apoyo) / v

El lóbulo es la parte de la hypar con g = f − plano > 0 (lado de la clave).
* El borde libre es la sección de la hypar por un plano no vertical: un arco
  de HIPÉRBOLA contenido en ese plano (curva plana: su cimbra es plana). En
  planta, g = 0 también es una hipérbola (la parte cuadrática de g es la de f,
  indefinida) y el lóbulo vuela v más allá de la línea de apoyos.
* En las limas Q(d) = 0, así que g es lineal sobre ellas y la única raíz es
  el apoyo: cada borde arranca exactamente en sus dos apoyos.

c3 = 0: lóbulos planos (pirámide de N caras, limas = cumbreras); no es una hypar.
Al subir el borde los lóbulos se abomban entre las limas y éstas pasan de
cumbreras a valles (tipo Los Manantiales); una misma lima puede cambiar de
una a otra donde las normales de los dos lóbulos coinciden. El informe las
clasifica a partir de las normales (metrics.crease_corrections).
"""

from __future__ import annotations

import math

from .defaults import COMMON_DEFAULTS
from .geometry import QuadraticSurface, SectorDomain, Vec2
from .meshing import PART_EDGE, PART_EXTRADOS, PART_INTRADOS, PART_SURFACE, MeshData
from .model import (INTRADOS, Crease, ShellModel, ShellPatch, SupportPoint, ThicknessSpec, offset_point,
                    register_generator, register_mesher)

DEFAULTS = {
    **COMMON_DEFAULTS,
    "shell_type": "RADIAL",
    "radial_count": 8,
    "radial_plan": "CIRCLE",
    "radial_radius": 10.0,
    "crown_height": 5.0,
    "edge_height": 1.5,
    "support_height": 0.0,
    "edge_overhang": 3.0,  # vuelo del borde con corte inclinado (m)
    "radial_supports": "GROINS",
    "res_x": 20,  # divisiones radiales
    "res_y": 24,  # divisiones por lóbulo
}


# Puntos de partida para explorar (tipologías, no reproducciones de obras).
PRESETS = {
    "DOME": ("Cúpula", "8 lóbulos sobre planta circular; limas cumbrera junto a la clave y valle hacia el borde",
             {"radial_count": 8, "radial_plan": "CIRCLE", "radial_radius": 10.0, "crown_height": 5.0,
              "edge_height": 1.5, "support_height": 0.0, "res_x": 20, "res_y": 24}),
    "LOBES": ("Lóbulos (tipo Manantiales)", "8 lóbulos cortados por planos inclinados que vuelan sobre los apoyos",
              {"radial_count": 8, "radial_plan": "INCLINED", "radial_radius": 18.0, "crown_height": 5.0,
               "edge_height": 10.0, "edge_overhang": 6.0, "support_height": 0.0, "res_x": 24, "res_y": 32}),
    "GROINED": ("Bóveda por arista", "4 hypars sobre planta cuadrada de 30 m, apoyada en las esquinas",
                {"radial_count": 4, "radial_plan": "POLYGON", "radial_radius": 15.0 * math.sqrt(2.0),
                 "crown_height": 8.0, "edge_height": 6.0, "support_height": 0.0, "res_x": 24, "res_y": 32}),
}


def lobe_coefficients(n: int, radius: float, plan: str, crown: float, edge: float,
                      support: float, overhang: float = 0.0) -> tuple[float, float, float, float]:
    """(c1, c3, c5, X) del lóbulo en coordenadas locales."""
    beta = math.pi / n
    x_groin = radius * math.cos(beta)
    x_tip = {"CIRCLE": radius, "POLYGON": x_groin, "INCLINED": x_groin + overhang}[plan]
    c1 = (support - crown) / x_groin
    c3 = (edge - crown - c1 * x_tip) / (x_tip * x_tip)
    c5 = -c3 / math.tan(beta) ** 2
    return c1, c3, c5, x_tip


def rotated_lobe(crown: float, c1: float, c3: float, c5: float, theta: float) -> QuadraticSurface:
    """Gira f(x', y') = H + c1·x' + c3·x'² + c5·y'² un ángulo θ alrededor de Z."""
    c, s = math.cos(theta), math.sin(theta)
    return QuadraticSurface(crown, c1 * c, c1 * s,
                            c3 * c * c + c5 * s * s,
                            2.0 * (c3 - c5) * s * c,
                            c3 * s * s + c5 * c * c)


@register_generator("RADIAL", DEFAULTS)
def build_radial(d: dict) -> ShellModel:
    n = int(d["radial_count"])
    radius = float(d["radial_radius"])
    if n < 3:
        raise ValueError("Una bóveda radial necesita al menos 3 lóbulos.")
    if radius <= 0.0:
        raise ValueError("El radio debe ser positivo.")
    plan = d["radial_plan"]
    if plan not in ("CIRCLE", "POLYGON", "INCLINED"):
        raise ValueError(f"Tipo de borde desconocido: {plan}")
    crown, edge, support = float(d["crown_height"]), float(d["edge_height"]), float(d["support_height"])
    overhang = float(d["edge_overhang"]) if plan == "INCLINED" else 0.0
    if plan == "INCLINED" and overhang <= 1e-6:
        raise ValueError("Con borde por plano inclinado el vuelo debe ser positivo.")
    c1, c3, c5, x_tip = lobe_coefficients(n, radius, plan, crown, edge, support, overhang)
    beta = math.pi / n
    x_groin = radius * math.cos(beta)
    slope = (edge - support) / overhang if plan == "INCLINED" else None

    # Extremos de lima compartidos (mismos flotantes para los dos lóbulos vecinos).
    ends: list[Vec2] = [(radius * math.cos(2 * math.pi * k / n - beta), radius * math.sin(2 * math.pi * k / n - beta))
                        for k in range(n)]
    patches = []
    for k in range(n):
        theta = 2 * math.pi * k / n
        cut = None
        if slope is not None:
            # g = f − plano, en coordenadas del lóbulo y girado como la propia hypar
            cut = rotated_lobe(crown - support + slope * x_groin, c1 - slope, c3, c5, theta)
        dom = SectorDomain((0.0, 0.0), ends[k], ends[(k + 1) % n], arc=plan == "CIRCLE", cut=cut)
        try:
            dom.validate()
        except ValueError as exc:
            if cut is None:
                raise
            raise ValueError("El plano inclinado no delimita un lóbulo válido con estas alturas y este vuelo "
                             f"(sube la clave o el borde, o cambia el vuelo). Detalle: {exc}") from exc
        if cut is not None and abs(math.dist(dom.center, dom.outer(0.5)) - x_tip) > 1e-7 * x_tip:
            raise ValueError("El plano inclinado corta el lóbulo antes de su vértice: con estas alturas el "
                             "borde no llega al vuelo pedido. Aumenta la altura de borde o reduce el vuelo.")
        patches.append(ShellPatch(f"lóbulo {k}", rotated_lobe(crown, c1, c3, c5, theta), dom))
    creases = tuple(Crease((k - 1) % n, k, (0.0, 0.0), ends[k]) for k in range(n))
    thickness = ThicknessSpec(float(d["thickness"]), d["thickness_mode"], d["thickness_reference"])
    model = ShellModel("RADIAL", tuple(patches), thickness, (), {}, creases,
                       outline_perimeter=n * patches[0].domain.outer_length,
                       characteristic_span=2.0 * max(radius, x_tip))

    s0 = thickness.offset_of(INTRADOS)
    layout = d["radial_supports"]
    kind = d["support_type"]
    if layout == "GROINS":
        supports = tuple(SupportPoint(f"L{k}", offset_point(model, *ends[k], k, [(k - 1) % n, k], s0), kind)
                         for k in range(n))
    elif layout == "CENTER":
        supports = (SupportPoint("C", offset_point(model, 0.0, 0.0, 0, list(range(n)), s0), kind),)
    else:
        supports = ()

    surf0 = patches[0].surface
    dirs = surf0.asymptotic_directions()
    definition = {
        "family": "radial_hyperbolic_paraboloids",
        "is_hyperbolic": surf0.is_hyperbolic(),
        "lobe_equation": "z = H + c1*x' + c3*x'^2 + c5*y'^2  (x' sobre el eje del lóbulo)",
        "count": n,
        "plan": {"CIRCLE": "circular", "POLYGON": "poligonal", "INCLINED": "planos inclinados"}[plan],
        "edge_cut": {"CIRCLE": "cilindro r = R (curva alabeada)",
                     "POLYGON": "plano vertical (parábola plana)",
                     "INCLINED": "plano inclinado (hipérbola plana)"}[plan],
        "cut_plane_slope_deg": math.degrees(math.atan(slope)) if slope is not None else 90.0,
        "edge_overhang": overhang,
        "radius": radius,
        "crown_height": crown,
        "edge_height": edge,
        "support_height": support,
        "lobe_coefficients": {"H": crown, "c1": c1, "c3": c3, "c5": c5},
        "lobe_tip_distance": x_tip,
        "groin_slope_deg": math.degrees(math.atan(abs(c1))),
        "coefficients": {f"c{i}": v for i, v in enumerate((surf0.c0, surf0.c1, surf0.c2, surf0.c3, surf0.c4, surf0.c5))},
        "ruling_directions_deg": [math.degrees(math.atan2(v[1], v[0])) for v in dirs] if dirs else [],
    }
    return ShellModel("RADIAL", tuple(patches), thickness, supports, definition, creases,
                      model.outline_perimeter, model.characteristic_span)


# --- mallado ----------------------------------------------------------------------
# Un único vértice en la clave, una fila de vértices por lima (compartida por los
# dos lóbulos vecinos) y vértices interiores por lóbulo. La malla queda soldada:
# sin vértices duplicados en las limas, cerrada y orientable.


def _layout(model: ShellModel, nr: int, nt: int):
    if nr < 1 or nt < 1:
        raise ValueError("La resolución de malla debe ser >= 1 en cada dirección.")
    n = len(model.patches)

    def vid(k: int, i: int, j: int) -> int:
        if i == 0:
            return 0
        if j == nt:
            k, j = (k + 1) % n, 0
        if j == 0:
            return 1 + k * nr + (i - 1)
        return 1 + n * nr + (k * nr + (i - 1)) * (nt - 1) + (j - 1)

    total = 1 + n * nr * nt
    plan: list[Vec2] = [(0.0, 0.0)] * total
    owner = [0] * total
    incident: list[list[int]] = [list(range(n))] * total
    plan[0] = model.patches[0].domain.center
    for k, patch in enumerate(model.patches):
        for i in range(1, nr + 1):
            for j in range(nt):
                v = vid(k, i, j)
                plan[v] = patch.domain.map(i / nr, j / nt)
                owner[v] = k
                incident[v] = [(k - 1) % n, k] if j == 0 else [k]
    return vid, plan, owner, incident


def _faces(n: int, nr: int, nt: int, vid, base: int = 0, flip: bool = False) -> list[tuple[int, ...]]:
    out = []
    for k in range(n):
        for j in range(nt):  # abanico de triángulos en la clave
            a, b = vid(k, 1, j), vid(k, 1, j + 1)
            out.append((base, base + b, base + a) if flip else (base, base + a, base + b))
        for i in range(2, nr + 1):
            for j in range(nt):
                a, b = vid(k, i - 1, j), vid(k, i, j)
                c, d = vid(k, i, j + 1), vid(k, i - 1, j + 1)
                q = (a, d, c, b) if flip else (a, b, c, d)  # misma diagonal arriba y abajo
                out.append(tuple(base + v for v in q))
    return out


def _creases(n: int, nr: int, vid) -> list[list[int]]:
    return [[0] + [vid(k, i, 0) for i in range(1, nr + 1)] for k in range(n)]


def radial_surface(model: ShellModel, nr: int, nt: int, offset: float) -> MeshData:
    n = len(model.patches)
    vid, plan, owner, incident = _layout(model, nr, nt)
    verts = [offset_point(model, x, y, owner[v], incident[v], offset) for v, (x, y) in enumerate(plan)]
    faces = _faces(n, nr, nt, vid)
    chains = _creases(n, nr, vid)
    sharp = [(c[i], c[i + 1]) for c in chains for i in range(nr)]
    loop = [vid(k, nr, j) for k in range(n) for j in range(nt)]
    node_sets = {"center": [0], "free_edge": loop}
    node_sets.update({f"groin_{k}": c for k, c in enumerate(chains)})
    grids = [[[vid(k, i, j) for j in range(nt + 1)] for i in range(nr + 1)] for k in range(n)]
    return MeshData(verts, faces, [PART_SURFACE] * len(faces), list(plan), sharp, (nr, nt),
                    list(owner), node_sets, grids)


def radial_solid(model: ShellModel, nr: int, nt: int) -> MeshData:
    n = len(model.patches)
    s0, s1 = model.thickness.offsets
    vid, plan, owner, incident = _layout(model, nr, nt)
    m = len(plan)
    bottom = [offset_point(model, x, y, owner[v], incident[v], s0) for v, (x, y) in enumerate(plan)]
    top = [offset_point(model, x, y, owner[v], incident[v], s1) for v, (x, y) in enumerate(plan)]
    faces = _faces(n, nr, nt, vid, base=m)
    parts = [PART_EXTRADOS] * len(faces)
    bottom_faces = _faces(n, nr, nt, vid, base=0, flip=True)
    faces += bottom_faces
    parts += [PART_INTRADOS] * len(bottom_faces)
    loop = [vid(k, nr, j) for k in range(n) for j in range(nt)]
    sharp = []
    for idx, a in enumerate(loop):
        b = loop[(idx + 1) % len(loop)]
        faces.append((a, b, m + b, m + a))
        parts.append(PART_EDGE)
        sharp += [(a, b), (m + a, m + b)]
    for c in _creases(n, nr, vid):
        for i in range(nr):
            sharp += [(c[i], c[i + 1]), (m + c[i], m + c[i + 1])]
    return MeshData(bottom + top, faces, parts, plan + plan, sharp, (nr, nt), owner + owner)


register_mesher("RADIAL", radial_solid, radial_surface)
