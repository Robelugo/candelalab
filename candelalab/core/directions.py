"""Direcciones principales de los esfuerzos de membrana N dibujadas sobre el cascarón (sin bpy).

En cada nodo de la lámina, el tensor de membrana (Nx, Ny, Nxy) en los ejes
locales del plano tangente (los de frd.local_frame: e₁ = proyección del X
global, e₂ = n × e₁) tiene dos direcciones principales ortogonales:
    θ₁ = ½·atan2(2·Nxy, Nx − Ny)        ángulo de N₁ (la mayor) desde e₁ hacia e₂
    d₁ = cos θ₁·e₁ + sin θ₁·e₂,   d₂ = n × d₁
En un estado isótropo (N₁ = N₂) todas las direcciones son principales: la cruz
queda en los ejes locales.

Se dibuja una cruz por celda de una rejilla en planta (el nodo más cercano al
centro de cada celda), con un brazo según d₁ y otro según d₂ de longitud
proporcional a |N₁| y |N₂|. Tracción: puntas hacia fuera; compresión: puntas
hacia dentro, para que el signo se lea también sin color.

Cada brazo sigue la superficie: su planta es la recta q₀ + u·(d_x, d_y), cuya
longitud sobre la superficie es u hasta segundo orden, y su altura la de la
cara del cascarón (extradós e intradós con el capitel, o la superficie de
referencia), separada `lift`. Así no se hunde en un cascarón curvo. Los brazos
se recortan en el borde de la planta.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .capitals import capital_drop, offset_direction
from .frd import local_frame, principal2
from .geometry import Vec3
from .meshing import MeshData
from .model import EXTRADOS, INTRADOS

TENSION, COMPRESSION = 0, 1   # parte de cada cara = índice de material
CELLS = 16                    # celdas a lo largo del lado mayor de la planta (separación automática)
FILL = 0.9                    # con escala automática, el brazo mayor mide FILL·separación
MIN_FRACTION = 0.02           # no se dibujan los brazos con |N| menor que esta fracción del máximo
WIDTH = 0.06                  # ancho de los brazos / separación
SEGMENTS = 8                  # tramos de un brazo entero (siguen la curvatura)


@dataclass(frozen=True)
class Cross:
    node: int                          # nodo de la lámina (modelo FE, base 0)
    plan: tuple[float, float]          # punto en planta de la referencia
    values: tuple[float, float]        # N₁ ≥ N₂ (kN/m)
    directions: tuple[Vec3, Vec3]      # d₁, d₂: unitarias, en el plano tangente
    angle: float                       # θ₁ en grados


def principal_axes(nx: float, ny: float, nxy: float, n: Vec3):
    """(N₁, N₂, d₁, d₂, θ₁ en grados) del tensor plano en los ejes locales de la normal n."""
    e1, e2 = local_frame(n)
    theta = 0.5 * math.atan2(2.0 * nxy, nx - ny)
    c, s = math.cos(theta), math.sin(theta)
    d1 = tuple(c * a + s * b for a, b in zip(e1, e2))
    d2 = tuple(c * b - s * a for a, b in zip(e1, e2))   # n × d₁, porque e₂ = n × e₁
    n1, n2 = principal2(nx, ny, nxy)
    return n1, n2, d1, d2, math.degrees(theta)


def auto_spacing(plan: dict) -> float:
    xs, ys = [p[0] for p in plan.values()], [p[1] for p in plan.values()]
    return max(max(xs) - min(xs), max(ys) - min(ys)) / CELLS or 1.0


def sample_nodes(plan: dict, spacing: float) -> list[int]:
    """En cada celda de una rejilla en planta de lado `spacing`, centrada en la planta,
    el nodo más cercano al centro de la celda."""
    xs, ys = [p[0] for p in plan.values()], [p[1] for p in plan.values()]
    grid = []
    for lo, hi in ((min(xs), max(xs)), (min(ys), max(ys))):
        count = max(1, math.ceil((hi - lo) / spacing - 1e-9))
        grid.append((0.5 * (lo + hi) - 0.5 * count * spacing, count))
    best: dict[tuple[int, int], tuple[float, int]] = {}
    for v, (x, y) in plan.items():
        cell, dist = [], 0.0
        for c, (origin, count) in zip((x, y), grid):
            k = min(count - 1, max(0, math.floor((c - origin) / spacing)))
            cell.append(k)
            dist += (c - origin - (k + 0.5) * spacing) ** 2
        key = (cell[0], cell[1])
        if key not in best or (dist, v) < best[key]:
            best[key] = (dist, v)
    return sorted(v for _d, v in best.values())


def principal_crosses(res, item: int, spacing: float = 0.0) -> tuple[list[Cross], float]:
    """Cruces del resultado `item` en los nodos muestreados, y la separación usada."""
    forces = res._section(item)
    plan = {v: res.plan[v] for v, f in forces.items() if f is not None and v in res.plan}
    if not plan:
        return [], spacing
    spacing = spacing if spacing > 0.0 else auto_spacing(plan)
    out = []
    for v in sample_nodes(plan, spacing):
        nx, ny, nxy = forces[v][2:5]
        n1, n2, d1, d2, angle = principal_axes(nx, ny, nxy, res.normals[v])
        out.append(Cross(v, plan[v], (n1, n2), (d1, d2), angle))
    return out, spacing


def direction_glyphs(res, item: int, solid: bool = True, spacing: float = 0.0, scale: float = 0.0,
                     lift: float | None = None) -> tuple[MeshData | None, dict]:
    """Malla de las cruces (partes TENSION / COMPRESSION) y su leyenda.

    `scale`: kN/m que representa un metro de brazo (0 = automática). `lift`:
    separación de la cara (por defecto, un cuarto del espesor). Sin malla, la
    leyenda lleva el motivo en "reason"."""
    it = res.items[item]
    if it.kind == "BUCKLE" or it.stress is None:
        return None, {"reason": res.missing_reason(item, "N_MAX")}
    model = res.model
    if model is None:
        return None, {"reason": "Faltan los datos de la geometría con que se asociaron los resultados."}
    crosses, spacing = principal_crosses(res, item, spacing)
    peak = max((abs(n) for c in crosses for n in c.values), default=0.0)
    if peak <= 0.0:
        return None, {"reason": "N es nula en todos los puntos muestreados."}
    if scale <= 0.0:
        scale = peak / (FILL * spacing)
    th = model.thickness
    if lift is None:
        lift = 0.25 * th.thickness
    if solid:
        s0, s1 = th.offset_of(INTRADOS), th.offset_of(EXTRADOS)
        faces = [(1.0, lambda q: s1 + lift), (-1.0, lambda q: s0 - capital_drop(model, q[0], q[1]) - lift)]
    else:
        faces = [(1.0, lambda q: lift), (-1.0, lambda q: -lift)]
    width = WIDTH * spacing
    md, drawn, omitted = MeshData(), 0, 0
    for c in crosses:
        arms = 0
        for value, d in zip(c.values, c.directions):
            length = abs(value) / scale
            if abs(value) < MIN_FRACTION * peak:
                omitted += 1
                continue
            lo, hi = arm_range(model, c.plan, d, 0.5 * length)
            if hi - lo <= 1e-9:
                continue
            for sign, offset in faces:
                _arm(md, model, c.plan, d, lo, hi, value >= 0.0, width, sign, offset)
            arms += 1
        drawn += arms > 0
    if not md.faces:
        return None, {"reason": "Ningún brazo alcanza el 2 % del máximo de |N|."}
    return md, {"count": drawn, "spacing": spacing, "scale": scale, "max": peak, "omitted": omitted,
                "faces": "extradós e intradós" if solid else "ambos lados de la superficie"}


def _inside(model, q) -> bool:
    return any(p.domain.contains(q, 1e-9) for p in model.patches)


def arm_range(model, q0, d: Vec3, half: float) -> tuple[float, float]:
    """(u_min, u_max) de un brazo ±half a lo largo de la planta de d, recortado en el borde."""
    ends = []
    for sign in (1.0, -1.0):
        inside, outside = 0.0, None
        for k in range(1, SEGMENTS + 1):
            u = half * k / SEGMENTS
            if not _inside(model, (q0[0] + sign * u * d[0], q0[1] + sign * u * d[1])):
                outside = u
                break
            inside = u
        if outside is not None:
            for _ in range(40):
                mid = 0.5 * (inside + outside)
                if _inside(model, (q0[0] + sign * mid * d[0], q0[1] + sign * mid * d[1])):
                    inside = mid
                else:
                    outside = mid
        ends.append(sign * inside)
    return ends[1], ends[0]


def face_point(model, q, sign: float, offset, near: int = 0) -> tuple[Vec3, Vec3, int]:
    """Punto de una cara sobre el punto en planta q de la referencia, normal exterior de esa cara
    (`sign` +1 arriba, −1 abajo) y parche usado. `offset(q)` es el desfase de la cara; fuera de
    la planta (el borde de una cinta) se prolonga el parche `near`."""
    k = next((i for i, p in enumerate(model.patches) if p.domain.contains(q, 1e-9)), near)
    surf = model.patches[k].surface
    z, n = surf.z(*q), surf.unit_normal(*q)
    m = offset_direction(model, q[0], q[1], k)
    s = offset(q)
    return (q[0] + s * m[0], q[1] + s * m[1], z + s * m[2]), (sign * n[0], sign * n[1], sign * n[2]), k


def _unit(v):
    ln = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / ln for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _along(a, b):
    return _unit(tuple(y - x for x, y in zip(a, b)))


def _arm(md: MeshData, model, q0, d, lo: float, hi: float, tension: bool, width: float, sign: float, offset):
    """Un brazo sobre una cara: cinta a lo largo de la superficie y una punta en cada extremo.

    Todos los vértices, también los bordes de la cinta y las esquinas de las
    puntas, son puntos de la cara: se desplazan en planta según la proyección de
    un vector tangente, lo que mide lo mismo sobre la superficie hasta segundo
    orden, y así la cinta se dobla con la superficie (por ejemplo, al cruzar una
    lima). Las caras miran hacia fuera de la cara del cascarón."""
    part = TENSION if tension else COMPRESSION
    heads = []
    for end in (lo, hi):
        a = min(3.0 * width, 0.4 * abs(end))
        heads.append((end, end - math.copysign(a, end), 0.5 * a))  # (extremo, base de la punta, semiancho)
    start, stop = (heads[0][1], heads[1][1]) if tension else (lo, hi)
    count = max(2, math.ceil(SEGMENTS * (stop - start) / (hi - lo)))

    def at(u):
        return (q0[0] + u * d[0], q0[1] + u * d[1])

    def shifted(q, side, w, near):
        return face_point(model, (q[0] + w * side[0], q[1] + w * side[1]), sign, offset, near)[0]

    plan = [at(start + (stop - start) * k / count) for k in range(count + 1)]
    center = [face_point(model, q, sign, offset) for q in plan]
    base = len(md.vertices)
    for k, (q, (_p, n, patch)) in enumerate(zip(plan, center)):
        side = _unit(_cross(n, _along(center[max(0, k - 1)][0], center[min(count, k + 1)][0])))  # n × tangente
        md.vertices += [shifted(q, side, -0.5 * width, patch), shifted(q, side, 0.5 * width, patch)]
    for k in range(count):
        i = base + 2 * k
        md.faces.append((i, i + 2, i + 3, i + 1))
        md.face_parts.append(part)
    for end, inner, half in heads:
        if end == inner:
            continue
        (pe, n, ke), (pi, _n, ki) = face_point(model, at(end), sign, offset), face_point(model, at(inner), sign, offset)
        side = _unit(_cross(n, _along(pi, pe)))                       # n × (hacia fuera del brazo)
        (qw, kw), tip, turn = ((at(inner), ki), pe, 1.0) if tension else ((at(end), ke), pi, -1.0)
        i = len(md.vertices)
        md.vertices += [shifted(qw, side, -turn * half, kw), tip, shifted(qw, side, turn * half, kw)]
        md.faces.append((i, i + 1, i + 2))
        md.face_parts.append(part)
