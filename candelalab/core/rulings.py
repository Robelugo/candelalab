"""Generatrices rectas (las dos familias de rectas contenidas en la hypar).

Sobre la superficie de referencia, y sobre cualquier capa si el espesor es
VERTICAL, cada generatriz es un segmento recto exacto (2 puntos).

Con espesor NORMAL, las capas desfasadas no son superficies regladas: la
normal gira a lo largo de la generatriz (la hypar no es desarrollable), así que
la "generatriz" desfasada es una curva. Se muestrea y se informa su desviación
máxima respecto de la cuerda: es la tolerancia real si se quisiera cimbrar esa
cara con piezas rectas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import Vec3
from .model import VERTICAL, ShellPatch

FAMILY_NAMES = ("A", "B")


@dataclass(frozen=True)
class Ruling:
    family: int
    points: tuple[Vec3, ...]
    length: float
    plan_length: float
    straightness_dev: float


def ruling_lines(patch: ShellPatch, spacing: float, max_per_family: int = 400, anchor=None):
    """Rectas en planta recortadas al dominio: lista de (familia, origen, dir, t0, t1)
    y el espaciado efectivo de cada familia.

    Las rectas de cada familia están separadas `spacing` (medido en planta,
    perpendicular a ellas) y una pasa por `anchor` (por defecto, el centroide
    del dominio). Con un ancla común, las generatrices de parches vecinos
    quedan alineadas.
    """
    dirs = patch.surface.asymptotic_directions()
    if dirs is None or spacing <= 0.0:
        return [], [0.0, 0.0]
    out = []
    steps = []
    for fam, d in enumerate(dirs):
        step, lines = family_lines(patch.domain, d, spacing, anchor, max_count=max_per_family)
        steps.append(step)
        out += [(fam, origin, d, t0, t1) for _k, origin, t0, t1 in lines]
    return out, steps


def family_lines(dom, d, spacing: float, anchor=None, phase: float = 0.0, max_count: int | None = None,
                 limit: int | None = None):
    """Rectas de dirección `d` recortadas al dominio, separadas `spacing` en planta.

    La recta k pasa a (k + phase)·spacing de `anchor`, medido en perpendicular.
    Devuelve (step, [(k, origen, t0, t1)]): rectas contiguas tienen k
    consecutivos y t es la coordenada a lo largo de d, común a toda la familia.
    Con `max_count` la separación crece si no caben; con `limit`, se rechaza.
    """
    dx, dy = d
    mx, my = -dy, dx
    cmin, cmax = dom.projection_range((mx, my))
    width = cmax - cmin
    step = spacing if max_count is None or width / spacing <= max_count else width / max_count
    if limit is not None and width / step > limit:
        raise ValueError(f"Harían falta unas {width / step:,.0f} piezas por familia (máximo {limit:,}); "
                         "aumenta la separación.")
    tol = 1e-9 * max(1.0, width)
    cx, cy = dom.centroid
    ax, ay = anchor if anchor is not None else (cx, cy)
    c0 = mx * ax + my * ay + phase * step
    a0 = dx * cx + dy * cy
    out = []
    for k in range(math.ceil((cmin - c0 - tol) / step), math.floor((cmax - c0 + tol) / step) + 1):
        c = c0 + k * step
        origin = (c * mx + a0 * dx, c * my + a0 * dy)
        seg = dom.clip_line(origin, d)
        if seg is None or seg[1] - seg[0] <= tol:
            continue
        out.append((k, origin, seg[0], seg[1]))
    return step, out


def compute_rulings(patch: ShellPatch, spacing: float, offset: float = 0.0, mode: str = "NORMAL",
                    samples: int = 9, max_per_family: int = 400, anchor=None) -> tuple[list[Ruling], list[float]]:
    lines, steps = ruling_lines(patch, spacing, max_per_family, anchor)
    straight = offset == 0.0 or mode == VERTICAL
    result = []
    for fam, (ox, oy), (dx, dy), t0, t1 in lines:
        ts = (t0, t1) if straight else tuple(t0 + (t1 - t0) * i / (samples - 1) for i in range(samples))
        pts = tuple(patch.point(ox + t * dx, oy + t * dy, offset, mode) for t in ts)
        length = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        dev = 0.0 if straight else _max_distance_to_chord(pts)
        result.append(Ruling(fam, pts, length, t1 - t0, dev))
    return result, steps


def model_rulings(model, spacing: float, offset: float = 0.0, mode: str = "NORMAL",
                  samples: int = 9) -> tuple[list[Ruling], list[float]]:
    """Generatrices de todos los parches (familias A/B locales a cada parche).

    Todas se anclan en el origen del modelo (la clave o la columna). Una
    generatriz sobre un pliegue compartido aparece en los dos parches; se
    conserva una sola (en obra es una sola pieza).
    """
    out: list[Ruling] = []
    steps: list[float] = [0.0, 0.0]
    seen = set()
    for patch in model.patches:
        rulings, st = compute_rulings(patch, spacing, offset, mode, samples, anchor=(0.0, 0.0))
        for r in rulings:
            ends = tuple(sorted(tuple(round(c, 6) for c in p) for p in (r.points[0], r.points[-1])))
            if ends not in seen:
                seen.add(ends)
                out.append(r)
        steps = [max(a, b) for a, b in zip(steps, st)] if st else steps
    return out, steps


def ruling_stats(rulings: list[Ruling], steps: list[float]) -> list[dict]:
    stats = []
    for fam in (0, 1):
        sel = [r for r in rulings if r.family == fam]
        stats.append({
            "family": FAMILY_NAMES[fam],
            "count": len(sel),
            "spacing": steps[fam] if fam < len(steps) else 0.0,
            "total_length": sum(r.length for r in sel),
            "max_length": max((r.length for r in sel), default=0.0),
            "straightness_dev_max": max((r.straightness_dev for r in sel), default=0.0),
        })
    return stats


def _max_distance_to_chord(pts: tuple[Vec3, ...]) -> float:
    a, b = pts[0], pts[-1]
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    lab2 = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
    if lab2 == 0.0:
        return 0.0
    worst = 0.0
    for p in pts[1:-1]:
        ap = (p[0] - a[0], p[1] - a[1], p[2] - a[2])
        t = (ap[0] * ab[0] + ap[1] * ab[1] + ap[2] * ab[2]) / lab2
        d = (ap[0] - t * ab[0], ap[1] - t * ab[1], ap[2] - t * ab[2])
        worst = max(worst, math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2))
    return worst
