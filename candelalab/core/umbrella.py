"""Paraguas de cuatro paraboloides hiperbólicos (Félix Candela).

Planta rectangular Lx × Ly con la columna en el centro. Cada cuadrante es la
hypar de bordes rectos que interpola bilinealmente sus cuatro esquinas: el
centro (cabeza de columna, altura z_c), los dos puntos medios de borde y la
esquina exterior (los tres a la altura del borde z_b). Con a = Lx/2, b = Ly/2,
Δ = z_b − z_c (flecha) y (sx, sy) los signos del cuadrante:

    z = z_c + Δ·(sx·x/a + sy·y/b − sx·sy·x·y/(a·b)) + p·x + q·y

* Es una hypar de eje vertical (c3 = c5 = 0, c4 = −sx·sy·Δ/(a·b) ≠ 0) y sus
  cuatro bordes son generatrices: los semiejes x = 0 e y = 0, compartidos por
  dos cuadrantes (limas, continuidad C0 exacta), y el perímetro, recto (y
  horizontal si no hay inclinación).
* Δ > 0: paraguas invertido (centro bajo, limas valle; el agua baja por la
  columna). Δ < 0: paraguas "normal" (centro alto, limas cumbrera).
* p = tan αx, q = tan αy suman un plano: siguen siendo hypars con las mismas
  generatrices y las limas siguen siendo continuas (paraguas inclinados para
  iluminación cenital).
* Las generatrices son paralelas a los ejes: la retícula de malla alineada
  con ellos es exacta (cada celda es un trozo de la misma hypar).
"""

from __future__ import annotations

import math

from .defaults import COMMON_DEFAULTS
from .geometry import QuadDomain, QuadraticSurface
from .meshing import (PART_EDGE, PART_EXTRADOS, PART_INTRADOS, PART_SURFACE, MeshData, _grid_quads,
                      boundary_loop)
from .model import (INTRADOS, Crease, ShellModel, ShellPatch, SupportPoint, ThicknessSpec, offset_point,
                    register_generator, register_mesher)

DEFAULTS = {
    **COMMON_DEFAULTS,
    "shell_type": "UMBRELLA",
    "span_x": 10.0,
    "span_y": 10.0,
    "umbrella_center_height": 4.0,
    "umbrella_edge_height": 5.0,
    "tilt_x": 0.0,
    "tilt_y": 0.0,
    "umbrella_column": True,
    "support_type": "COLUMN",
    "support_size": 0.4,
    "res_x": 12,  # divisiones por cuadrante
    "res_y": 12,
}

# Puntos de partida (tipologías, no reproducciones de obras).
PRESETS = {
    "INVERTED": ("Invertido", "Centro bajo y limas valle: el agua baja por la columna",
                 {"span_x": 10.0, "span_y": 10.0, "umbrella_center_height": 4.0, "umbrella_edge_height": 5.0,
                  "tilt_x": 0.0, "tilt_y": 0.0, "umbrella_column": True, "support_type": "COLUMN",
                  "support_size": 0.4}),
    "UPRIGHT": ("Normal", "Centro alto y limas cumbrera",
                {"span_x": 10.0, "span_y": 10.0, "umbrella_center_height": 5.0, "umbrella_edge_height": 4.0,
                 "tilt_x": 0.0, "tilt_y": 0.0, "umbrella_column": True, "support_type": "COLUMN",
                 "support_size": 0.4}),
    "TILTED": ("Inclinado", "Invertido e inclinado para dejar entrar luz cenital entre paraguas",
               {"span_x": 12.0, "span_y": 10.0, "umbrella_center_height": 4.5, "umbrella_edge_height": 5.5,
                "tilt_x": math.radians(9.0), "tilt_y": 0.0, "umbrella_column": True, "support_type": "COLUMN",
                "support_size": 0.4}),
}

# Cuadrantes en sentido antihorario: (+,+), (−,+), (−,−), (+,−)
QUADRANTS = ((1, 1), (-1, 1), (-1, -1), (1, -1))


def quadrant_surface(sx: int, sy: int, a: float, b: float, zc: float, delta: float,
                     p: float, q: float) -> QuadraticSurface:
    return QuadraticSurface(zc, delta * sx / a + p, delta * sy / b + q, 0.0, -delta * sx * sy / (a * b), 0.0)


@register_generator("UMBRELLA", DEFAULTS)
def build_umbrella(d: dict) -> ShellModel:
    lx, ly = float(d["span_x"]), float(d["span_y"])
    if lx <= 0.0 or ly <= 0.0:
        raise ValueError("Las luces deben ser positivas.")
    a, b = 0.5 * lx, 0.5 * ly
    zc, zb = float(d["umbrella_center_height"]), float(d["umbrella_edge_height"])
    delta = zb - zc
    p, q = math.tan(float(d["tilt_x"])), math.tan(float(d["tilt_y"]))

    patches = tuple(
        ShellPatch(f"cuadrante {'+' if sx > 0 else '−'}x {'+' if sy > 0 else '−'}y",
                   quadrant_surface(sx, sy, a, b, zc, delta, p, q),
                   QuadDomain.rectangle(a, b, sx * 0.5 * a, sy * 0.5 * b))
        for sx, sy in QUADRANTS)
    # Limas: semiejes compartidos (cuadrante k y k+1)
    creases = (Crease(0, 1, (0.0, 0.0), (0.0, b)), Crease(1, 2, (0.0, 0.0), (-a, 0.0)),
               Crease(2, 3, (0.0, 0.0), (0.0, -b)), Crease(3, 0, (0.0, 0.0), (a, 0.0)))
    thickness = ThicknessSpec(float(d["thickness"]), d["thickness_mode"], d["thickness_reference"])
    outline = QuadDomain.rectangle(lx, ly)
    model = ShellModel("UMBRELLA", patches, thickness, (), {}, creases, outline.perimeter,
                       math.hypot(lx, ly), outline, (2, 2))

    supports = ()
    if d["umbrella_column"]:
        top = offset_point(model, 0.0, 0.0, 0, [0, 1, 2, 3], thickness.offset_of(INTRADOS))
        supports = (SupportPoint("C", top, d["support_type"]),)

    s0 = patches[0].surface
    hyperbolic = s0.is_hyperbolic()
    definition = {
        "family": "umbrella_hyperbolic_paraboloids",
        "is_hyperbolic": hyperbolic,
        "quadrant_equation": "z = zc + Δ·(sx·x/a + sy·y/b − sx·sy·x·y/(a·b)) + p·x + q·y",
        "span_x": lx,
        "span_y": ly,
        "center_height": zc,
        "edge_height": zb,
        "delta": delta,
        "kind": "invertido" if delta > 0 else "normal" if delta < 0 else "plano",
        "k": delta / (a * b),
        "slope_x": p,
        "slope_y": q,
        "valley_slope_deg": [math.degrees(math.atan(abs(delta) / a)), math.degrees(math.atan(abs(delta) / b))],
        "coefficients": {f"c{i}": v for i, v in enumerate((s0.c0, s0.c1, s0.c2, s0.c3, s0.c4, s0.c5))},
        "ruling_directions_deg": [0.0, 90.0] if hyperbolic else [],
        "corner_heights": {n: model.patch_at(x, y).surface.z(x, y)
                           for n, (x, y) in zip(("SO", "SE", "NE", "NO"), outline.corners)},
    }
    return ShellModel("UMBRELLA", patches, thickness, supports, definition, creases, outline.perimeter,
                      math.hypot(lx, ly), outline, (2, 2))


# --- mallado ----------------------------------------------------------------------
# Una sola rejilla de (2·nu + 1) × (2·nv + 1) vértices sobre todo el rectángulo,
# con los ejes como líneas de rejilla: los cuatro cuadrantes quedan soldados por
# construcción. Cada vértice sabe qué cuadrantes concurren en él (1, 2 en una
# lima, 4 en la columna) para desfasarlo con inglete.


def _layout(model: ShellModel, nu: int, nv: int):
    if nu < 1 or nv < 1:
        raise ValueError("La resolución de malla debe ser >= 1 en cada dirección.")
    a, b = 0.5 * model.definition["span_x"], 0.5 * model.definition["span_y"]
    plan, owner, incident = [], [], []
    for j in range(2 * nv + 1):
        for i in range(2 * nu + 1):
            x, y = a * (i - nu) / nu, b * (j - nv) / nv
            sxs = (1,) if i > nu else (-1,) if i < nu else (1, -1)
            sys_ = (1,) if j > nv else (-1,) if j < nv else (1, -1)
            inc = [QUADRANTS.index((sx, sy)) for sx in sxs for sy in sys_]
            plan.append((x, y))
            owner.append(inc[0])
            incident.append(inc)
    return plan, owner, incident


def _creases(nu: int, nv: int) -> list[tuple[int, int]]:
    w = 2 * nu + 1
    edges = [(j * w + nu, (j + 1) * w + nu) for j in range(2 * nv)]      # eje y (x = 0)
    edges += [(nv * w + i, nv * w + i + 1) for i in range(2 * nu)]       # eje x (y = 0)
    return edges


def umbrella_surface(model: ShellModel, nu: int, nv: int, offset: float) -> MeshData:
    plan, owner, incident = _layout(model, nu, nv)
    verts = [offset_point(model, x, y, owner[v], incident[v], offset) for v, (x, y) in enumerate(plan)]
    faces = _grid_quads(2 * nu, 2 * nv)
    w = 2 * nu + 1
    node_sets = {
        "ymin": [i for i in range(w)],
        "xmax": [j * w + 2 * nu for j in range(2 * nv + 1)],
        "ymax": [2 * nv * w + i for i in range(w)],
        "xmin": [j * w for j in range(2 * nv + 1)],
        "axis_x": [nv * w + i for i in range(w)],
        "axis_y": [j * w + nu for j in range(2 * nv + 1)],
        "center": [nv * w + nu],
    }
    grids = [[[j * w + i for i in range(w)] for j in range(2 * nv + 1)]]
    return MeshData(verts, faces, [PART_SURFACE] * len(faces), list(plan), _creases(nu, nv), (2 * nu, 2 * nv),
                    list(owner), node_sets, grids)


def umbrella_solid(model: ShellModel, nu: int, nv: int) -> MeshData:
    s0, s1 = model.thickness.offsets
    plan, owner, incident = _layout(model, nu, nv)
    n = len(plan)
    bottom = [offset_point(model, x, y, owner[v], incident[v], s0) for v, (x, y) in enumerate(plan)]
    top = [offset_point(model, x, y, owner[v], incident[v], s1) for v, (x, y) in enumerate(plan)]
    faces = _grid_quads(2 * nu, 2 * nv, base=n)
    parts = [PART_EXTRADOS] * len(faces)
    bottom_faces = _grid_quads(2 * nu, 2 * nv, base=0, flip=True)
    faces += bottom_faces
    parts += [PART_INTRADOS] * len(bottom_faces)
    loop = boundary_loop(2 * nu, 2 * nv)
    sharp = []
    for k, a in enumerate(loop):
        b = loop[(k + 1) % len(loop)]
        faces.append((a, b, n + b, n + a))
        parts.append(PART_EDGE)
        sharp += [(a, b), (n + a, n + b)]
    for a, b in _creases(nu, nv):
        sharp += [(a, b), (n + a, n + b)]
    return MeshData(bottom + top, faces, parts, plan + plan, sharp, (2 * nu, 2 * nv), owner + owner)


register_mesher("UMBRELLA", umbrella_solid, umbrella_surface)
