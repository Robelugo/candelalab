"""Engrosamientos locales (capiteles) en los apoyos (sin bpy).

Bajo cada apoyo el espesor crece con un perfil suave, de pendiente nula en el
centro y en el borde:

    Δt(r) = Δ·(1 − (r/R)²)²   si r < R,   0 si no

con r la distancia en planta al apoyo. El extradós no cambia: baja el
intradós, según la misma dirección del desfase (vertical o normal). En la
cabeza de un paraguas es el capitel que reparte la carga a la columna; en las
esquinas o limas apoyadas de una hypar o una bóveda, el engrosamiento del arranque.

Volumen añadido, exacto con el mismo modelo que el resto del cascarón:
    VERTICAL: ∬ Δt dx dy
    NORMAL:   ∬ [F(s0) − F(s0 − Δt)]·W dx dy,   F(s) = s − H·s² + K·s³/3  (Steiner)
              + ∫ σ·tan(δ/2)·[s0² − (s0 − Δt)²] dℓ   a lo largo de las limas
El segundo término es la cuña de inglete de las limas que pasan bajo el
capitel (la misma corrección que metrics.crease_corrections hace con el espesor).
Se integra en polares alrededor del apoyo, sólo dentro de la planta: cada rayo
se recorta con los bordes exactos de los paños (rectas, arcos, curvas de
corte), y los límites del abanico que entra en la planta se ajustan por
bisección. En un apoyo interior el resultado es πΔR²/3 (con espesor vertical);
en una esquina recta, la cuarta parte.

Las dimensiones las fija el usuario: no están dimensionadas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import Vec2, Vec3
from .model import INTRADOS, VERTICAL, miter_direction
from .quadrature import gauss_legendre


@dataclass(frozen=True)
class Capital:
    name: str
    center: Vec2
    radius: float
    extra: float      # Δ: espesor añadido en el centro

    def drop(self, x: float, y: float) -> float:
        r2 = ((x - self.center[0]) ** 2 + (y - self.center[1]) ** 2) / (self.radius * self.radius)
        return self.extra * (1.0 - r2) ** 2 if r2 < 1.0 else 0.0


def make_capitals(model, radius: float, extra: float) -> tuple[Capital, ...]:
    if radius <= 0.0 or extra <= 0.0:
        raise ValueError("El radio y el espesor añadido de los capiteles deben ser positivos.")
    caps = tuple(Capital(sp.name, (sp.position[0], sp.position[1]), radius, extra) for sp in model.supports)
    for i, a in enumerate(caps):
        for b in caps[i + 1:]:
            if math.dist(a.center, b.center) < a.radius + b.radius:
                raise ValueError(f"Los capiteles de {a.name} y {b.name} se solapan: reduce el radio.")
    return caps


def capital_drop(model, x: float, y: float) -> float:
    """Espesor añadido en el punto (los capiteles no se solapan)."""
    return max((c.drop(x, y) for c in model.capitals), default=0.0)


def offset_direction(model, x: float, y: float, owner: int | None = None) -> Vec3:
    """Dirección del desfase del intradós en el punto (vertical, normal o inglete en una lima)."""
    if model.thickness.mode == VERTICAL:
        return (0.0, 0.0, 1.0)
    tol = 1e-9
    normals = [p.surface.unit_normal(x, y) for p in model.patches if p.domain.contains((x, y), tol)]
    if not normals:
        normals = [model.patches[owner or 0].surface.unit_normal(x, y)]
    return miter_direction(normals)


def thicken_solid(md, model) -> int:
    """Baja los vértices del intradós de la malla del sólido según los capiteles. Devuelve cuántos."""
    from .meshing import PART_INTRADOS
    bottom = {v for f, part in zip(md.faces, md.face_parts) if part == PART_INTRADOS for v in f}
    moved = 0
    for v in bottom:
        x, y = md.plan_points[v]
        d = capital_drop(model, x, y)
        if d > 0.0:
            m = offset_direction(model, x, y, md.vertex_patch[v] if md.vertex_patch else None)
            px, py, pz = md.vertices[v]
            md.vertices[v] = (px - d * m[0], py - d * m[1], pz - d * m[2])
            moved += 1
    return moved


def capital_volume(model, cap: Capital, order: int = 8, panels: int = 12) -> float:
    """Volumen añadido por un capitel, sólo dentro de la planta del cascarón."""
    th = model.thickness
    s0 = th.offset_of(INTRADOS)
    xs, ws = gauss_legendre(order)
    cx, cy = cap.center
    radius = cap.radius

    def reach(theta: float) -> float:
        """Distancia desde el apoyo hasta el borde de la planta en la dirección theta (≤ R)."""
        d = (math.cos(theta), math.sin(theta))
        spans = []
        for p in model.patches:
            seg = p.domain.clip_line((cx, cy), d)
            if seg is not None and seg[1] > 1e-12:
                spans.append((max(seg[0], 0.0), seg[1]))
        end = 0.0
        for a, b in sorted(spans):
            if a > end + 1e-9 * radius:
                break
            end = max(end, b)
        return min(end, radius)

    def integrand(x: float, y: float) -> float:
        dt = cap.drop(x, y)
        if dt <= 0.0:
            return 0.0
        if th.mode == VERTICAL:
            return dt
        surf = model.patch_at(x, y).surface
        k_g, h_m = surf.curvatures(x, y)

        def f(s):
            return s - h_m * s * s + k_g * s ** 3 / 3.0

        return (f(s0) - f(s0 - dt)) * surf.metric_factor(x, y)

    def ring(theta: float) -> float:
        rmax = reach(theta)
        if rmax <= 0.0:
            return 0.0
        total = 0.0
        for k in range(2):  # dos tramos radiales: el perfil es un polinomio en r
            a, b = rmax * k / 2, rmax * (k + 1) / 2
            for xi, wi in zip(xs, ws):
                r = a + 0.5 * (xi + 1.0) * (b - a)
                total += 0.5 * (b - a) * wi * r * integrand(cx + r * math.cos(theta), cy + r * math.sin(theta))
        return total

    # Abanico de direcciones que entran en la planta: muestreo y bisección de sus límites.
    n = 720
    inside = [reach(2.0 * math.pi * k / n) > 1e-9 * radius for k in range(n)]
    if all(inside):
        intervals = [(0.0, 2.0 * math.pi)]
    else:
        def edge(lo, hi, lo_in):
            for _ in range(50):
                mid = 0.5 * (lo + hi)
                if (reach(mid) > 1e-9 * radius) == lo_in:
                    lo = mid
                else:
                    hi = mid
            return 0.5 * (lo + hi)

        start = next(k for k in range(n) if not inside[k])  # recorrer desde un rayo de fuera
        intervals, a = [], None
        for j in range(1, n + 1):
            k, prev = (start + j) % n, (start + j - 1) % n
            t0, t1 = 2.0 * math.pi * (start + j - 1) / n, 2.0 * math.pi * (start + j) / n
            if inside[k] and not inside[prev]:
                a = edge(t0, t1, False)
            elif not inside[k] and inside[prev] and a is not None:
                intervals.append((a, edge(t0, t1, True)))
                a = None
    total = 0.0
    for lo, hi in intervals:
        for k in range(panels):
            a, b = lo + (hi - lo) * k / panels, lo + (hi - lo) * (k + 1) / panels
            for xi, wi in zip(xs, ws):
                total += 0.5 * (b - a) * wi * ring(a + 0.5 * (xi + 1.0) * (b - a))
    if th.mode != VERTICAL:
        total += _crease_wedges(model, cap, s0, xs, ws)
    return total


def _crease_wedges(model, cap: Capital, s0: float, xs, ws) -> float:
    """Cuñas de inglete añadidas en el tramo de cada lima que pasa bajo el capitel."""
    from .metrics import crease_wedge
    out = 0.0
    cx, cy = cap.center
    for cr in model.creases:
        (x0, y0), (x1, y1) = cr.start, cr.end
        length = math.dist(cr.start, cr.end)
        dx, dy = x0 - cx, y0 - cy
        ex, ey = (x1 - x0) / length, (y1 - y0) / length
        b = dx * ex + dy * ey                                  # |p0 + s·e − c|² = R², s en metros
        disc = b * b - (dx * dx + dy * dy - cap.radius ** 2)
        if disc <= 0.0:
            continue
        lo = max(0.0, (-b - math.sqrt(disc)) / length)
        hi = min(1.0, (-b + math.sqrt(disc)) / length)
        for k in range(2):
            a, c = lo + (hi - lo) * k / 2, lo + (hi - lo) * (k + 1) / 2
            for xi, wi in zip(xs, ws):
                tau = a + 0.5 * (xi + 1.0) * (c - a)
                sigma, tan_half, _cos, density = crease_wedge(model, cr, tau)
                dt = cap.drop(x0 + tau * (x1 - x0), y0 + tau * (y1 - y0))
                dl = length * density * 0.5 * (c - a) * wi
                out += sigma * tan_half * (s0 * s0 - (s0 - dt) ** 2) * dl
    return out
