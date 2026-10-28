"""Primitivas geométricas: superficie cuadrática en forma de grafo y dominio en planta.

Convenciones
------------
* Coordenadas locales del objeto, en metros. Z es vertical.
* La superficie es un grafo z = f(x, y) sobre el plano XY ("planta").
* La normal unitaria apunta siempre hacia +Z (hacia el extradós).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import cached_property

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class QuadraticSurface:
    """Superficie z = f(x, y) con f polinomio de segundo grado:

        f(x, y) = c0 + c1·x + c2·y + c3·x² + c4·x·y + c5·y²

    Es un paraboloide hiperbólico de eje vertical si y sólo si su hessiano es
    indefinido:  det(Hf) = 4·c3·c5 − c4² < 0.

    Esta familia contiene la hypar canónica de Candela z = k·x·y y cualquier
    hypar obtenida al sumarle un plano (inclinación), porque los términos
    lineales no alteran la parte cuadrática: las direcciones de las generatrices
    y la curvatura gaussiana en el vértice no cambian.
    """

    c0: float = 0.0
    c1: float = 0.0
    c2: float = 0.0
    c3: float = 0.0
    c4: float = 0.0
    c5: float = 0.0

    # --- valores y derivadas --------------------------------------------------

    def z(self, x: float, y: float) -> float:
        return self.c0 + self.c1 * x + self.c2 * y + self.c3 * x * x + self.c4 * x * y + self.c5 * y * y

    def gradient(self, x: float, y: float) -> Vec2:
        return (self.c1 + 2.0 * self.c3 * x + self.c4 * y, self.c2 + self.c4 * x + 2.0 * self.c5 * y)

    @property
    def hessian(self) -> tuple[float, float, float]:
        """(f_xx, f_xy, f_yy), constantes en toda la superficie."""
        return (2.0 * self.c3, self.c4, 2.0 * self.c5)

    @property
    def hessian_det(self) -> float:
        return 4.0 * self.c3 * self.c5 - self.c4 * self.c4

    def is_hyperbolic(self, tol: float = 1e-18) -> bool:
        return self.hessian_det < -tol

    def quadratic_form(self, d: Vec2) -> float:
        """Q(d) = c3·dx² + c4·dx·dy + c5·dy²  (= ½·dᵀ·Hf·d)."""
        dx, dy = d
        return self.c3 * dx * dx + self.c4 * dx * dy + self.c5 * dy * dy

    # --- geometría diferencial --------------------------------------------------

    def metric_factor(self, x: float, y: float) -> float:
        """W = √(1 + f_x² + f_y²) = dA_superficie / dA_planta = 1 / cos(pendiente)."""
        fx, fy = self.gradient(x, y)
        return math.sqrt(1.0 + fx * fx + fy * fy)

    def unit_normal(self, x: float, y: float) -> Vec3:
        fx, fy = self.gradient(x, y)
        w = math.sqrt(1.0 + fx * fx + fy * fy)
        return (-fx / w, -fy / w, 1.0 / w)

    def slope(self, x: float, y: float) -> float:
        """Ángulo de la pendiente máxima respecto de la horizontal (rad)."""
        fx, fy = self.gradient(x, y)
        return math.atan(math.hypot(fx, fy))

    def curvatures(self, x: float, y: float) -> tuple[float, float]:
        """(K, H): curvatura gaussiana y media respecto de la normal hacia +Z.

            K = (f_xx·f_yy − f_xy²) / W⁴
            H = ((1+f_y²)·f_xx − 2·f_x·f_y·f_xy + (1+f_x²)·f_yy) / (2·W³)

        Para una hypar K < 0 en todo punto (superficie anticlástica).
        """
        fx, fy = self.gradient(x, y)
        fxx, fxy, fyy = self.hessian
        w2 = 1.0 + fx * fx + fy * fy
        w = math.sqrt(w2)
        k_gauss = (fxx * fyy - fxy * fxy) / (w2 * w2)
        h_mean = ((1.0 + fy * fy) * fxx - 2.0 * fx * fy * fxy + (1.0 + fx * fx) * fyy) / (2.0 * w2 * w)
        return k_gauss, h_mean

    def principal_curvatures(self, x: float, y: float) -> tuple[float, float]:
        k_gauss, h_mean = self.curvatures(x, y)
        disc = math.sqrt(max(h_mean * h_mean - k_gauss, 0.0))
        return h_mean + disc, h_mean - disc

    def asymptotic_directions(self) -> tuple[Vec2, Vec2] | None:
        """Direcciones en planta de las dos familias de generatrices rectas.

        A lo largo de una recta en planta p(t) = p0 + t·d se cumple
            f(p(t)) = f(p0) + t·∇f(p0)·d + t²·Q(d),
        así que la curva levantada es una RECTA en 3D si y sólo si Q(d) = 0.
        Para un hessiano indefinido hay exactamente dos direcciones nulas:
        son las generatrices y no dependen del punto (forma de grafo cuadrático).

        Devuelve vectores unitarios ordenados por ángulo en [0, π) o None si la
        superficie no es hiperbólica (plano o paraboloide elíptico/cilíndrico).
        """
        if not self.is_hyperbolic():
            return None
        a, b, c = self.c3, self.c4, self.c5
        psi = 0.5 * math.atan2(b, a - c)
        e1 = (math.cos(psi), math.sin(psi))
        e2 = (-math.sin(psi), math.cos(psi))
        lam1 = a * e1[0] ** 2 + b * e1[0] * e1[1] + c * e1[1] ** 2
        lam2 = a + c - lam1
        if lam1 < 0.0:
            e1, e2, lam1, lam2 = e2, e1, lam2, lam1
        # λ1 > 0 > λ2  ->  d = √(-λ2)·e1 ± √(λ1)·e2  anula Q.
        sp, sn = math.sqrt(lam1), math.sqrt(-lam2)
        dirs = []
        for sign in (1.0, -1.0):
            dx = sn * e1[0] + sign * sp * e2[0]
            dy = sn * e1[1] + sign * sp * e2[1]
            n = math.hypot(dx, dy)
            dx, dy = dx / n, dy / n
            ang = math.atan2(dy, dx)
            if ang < -1e-12 or ang >= math.pi - 1e-12:
                dx, dy = -dx, -dy
            dirs.append((dx, dy))
        dirs.sort(key=lambda d: math.atan2(d[1], d[0]) % math.pi)
        return dirs[0], dirs[1]

    def critical_point(self) -> Vec2 | None:
        """Punto donde ∇f = 0 (el "vértice" o punto de silla de la hypar)."""
        fxx, fxy, fyy = self.hessian
        det = fxx * fyy - fxy * fxy
        if abs(det) < 1e-300:
            return None
        rx, ry = -self.c1, -self.c2
        return ((rx * fyy - fxy * ry) / det, (fxx * ry - fxy * rx) / det)


@dataclass(frozen=True)
class QuadDomain:
    """Cuadrilátero convexo en planta con esquinas p00, p10, p11, p01 en sentido
    antihorario, y la aplicación bilineal (s, t) ∈ [0,1]² -> (x, y).

    Un rectángulo es el caso particular afín. Los cuadriláteros generales se
    necesitarán para paraguas y sistemas modulares (v0.2).
    """

    p00: Vec2
    p10: Vec2
    p11: Vec2
    p01: Vec2

    @classmethod
    def rectangle(cls, span_x: float, span_y: float, cx: float = 0.0, cy: float = 0.0) -> "QuadDomain":
        a, b = 0.5 * span_x, 0.5 * span_y
        return cls((cx - a, cy - b), (cx + a, cy - b), (cx + a, cy + b), (cx - a, cy + b))

    @property
    def corners(self) -> tuple[Vec2, Vec2, Vec2, Vec2]:
        return (self.p00, self.p10, self.p11, self.p01)

    @property
    def edges(self) -> tuple[tuple[Vec2, Vec2], ...]:
        """Aristas en sentido antihorario: (p00→p10, p10→p11, p11→p01, p01→p00)."""
        c = self.corners
        return tuple((c[i], c[(i + 1) % 4]) for i in range(4))

    def map(self, s: float, t: float) -> Vec2:
        (x00, y00), (x10, y10), (x11, y11), (x01, y01) = self.corners
        u0, u1 = (1.0 - s) * (1.0 - t), s * (1.0 - t)
        u2, u3 = s * t, (1.0 - s) * t
        return (u0 * x00 + u1 * x10 + u2 * x11 + u3 * x01, u0 * y00 + u1 * y10 + u2 * y11 + u3 * y01)

    def jacobian(self, s: float, t: float) -> float:
        (x00, y00), (x10, y10), (x11, y11), (x01, y01) = self.corners
        xs = (1.0 - t) * (x10 - x00) + t * (x11 - x01)
        ys = (1.0 - t) * (y10 - y00) + t * (y11 - y01)
        xt = (1.0 - s) * (x01 - x00) + s * (x11 - x10)
        yt = (1.0 - s) * (y01 - y00) + s * (y11 - y10)
        return xs * yt - xt * ys

    @property
    def area(self) -> float:
        c = self.corners
        return 0.5 * sum(c[i][0] * c[(i + 1) % 4][1] - c[(i + 1) % 4][0] * c[i][1] for i in range(4))

    @property
    def perimeter(self) -> float:
        return sum(math.dist(a, b) for a, b in self.edges)

    @property
    def centroid(self) -> Vec2:
        c = self.corners
        a = self.area
        cx = cy = 0.0
        for i in range(4):
            (x0, y0), (x1, y1) = c[i], c[(i + 1) % 4]
            cross = x0 * y1 - x1 * y0
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        return (cx / (6.0 * a), cy / (6.0 * a))

    @property
    def size(self) -> float:
        xs = [p[0] for p in self.corners]
        ys = [p[1] for p in self.corners]
        return max(max(xs) - min(xs), max(ys) - min(ys))

    def contains(self, p: Vec2, tol: float = 1e-12) -> bool:
        for (ax, ay), (bx, by) in self.edges:
            if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) < -tol:
                return False
        return True

    def validate(self) -> None:
        """Exige un cuadrilátero convexo, no degenerado y antihorario."""
        c = self.corners
        tol = 1e-12 * max(self.size, 1.0) ** 2
        for i in range(4):
            a, b, d = c[i], c[(i + 1) % 4], c[(i + 2) % 4]
            cross = (b[0] - a[0]) * (d[1] - b[1]) - (b[1] - a[1]) * (d[0] - b[0])
            if cross <= tol:
                raise ValueError("El dominio en planta debe ser un cuadrilátero convexo en sentido antihorario.")

    def clip_line(self, origin: Vec2, direction: Vec2, tol: float | None = None) -> tuple[float, float] | None:
        """Recorta la recta origin + t·direction con el cuadrilátero (Cyrus-Beck).

        Devuelve (t_min, t_max) o None si la recta no lo cruza. Las rectas que
        coinciden con una arista se conservan (son generatrices de borde).
        """
        if tol is None:
            tol = 1e-9 * max(self.size, 1.0)
        span = _clip_halfplanes(self.edges, origin, direction, tol)
        if span is None or span[1] - span[0] <= tol:
            return None
        return span

    def projection_range(self, m: Vec2) -> tuple[float, float]:
        """Rango de m·p sobre el dominio (exacto: extremos en los vértices)."""
        vals = [m[0] * x + m[1] * y for x, y in self.corners]
        return min(vals), max(vals)

    def boundary_pieces(self) -> list[tuple]:
        """Contorno antihorario como piezas ("segment", A, B)."""
        return [("segment", a, b) for a, b in self.edges]

    def boundary_tangents(self, p: Vec2, tol: float) -> list[Vec2]:
        """Tangentes unitarias en planta de las aristas que pasan por p (a menos de tol)."""
        return _segment_tangents(self.edges, p, tol)


def _segment_tangents(edges, p: Vec2, tol: float) -> list[Vec2]:
    out = []
    for (ax, ay), (bx, by) in edges:
        ex, ey = bx - ax, by - ay
        ln2 = ex * ex + ey * ey
        u = min(1.0, max(0.0, ((p[0] - ax) * ex + (p[1] - ay) * ey) / ln2))
        if math.hypot(p[0] - ax - u * ex, p[1] - ay - u * ey) <= tol:
            ln = math.sqrt(ln2)
            out.append((ex / ln, ey / ln))
    return out


def _clip_halfplanes(edges, origin: Vec2, direction: Vec2, tol: float) -> tuple[float, float] | None:
    """Intervalo de t donde origin + t·direction está a la izquierda de todas las
    aristas dirigidas (intersección de semiplanos, Cyrus-Beck)."""
    t_min, t_max = -math.inf, math.inf
    for (ax, ay), (bx, by) in edges:
        ex, ey = bx - ax, by - ay
        ln = math.hypot(ex, ey)
        nx, ny = -ey / ln, ex / ln  # normal interior (polígono antihorario)
        num = nx * (origin[0] - ax) + ny * (origin[1] - ay)
        den = nx * direction[0] + ny * direction[1]
        if abs(den) < 1e-14:
            if num < -tol:
                return None
            continue
        t_hit = -num / den
        if den > 0.0:
            t_min = max(t_min, t_hit)
        else:
            t_max = min(t_max, t_hit)
    return (t_min, t_max) if t_max > t_min else None


@dataclass(frozen=True)
class SectorDomain:
    """Sector en planta con vértice `center`, entre los rayos hacia `a` y `b`
    (en sentido antihorario, ángulo < 180°).

    El borde exterior puede ser:
    * la cuerda a→b (planta poligonal),
    * con `arc=True`, el arco de circunferencia de centro `center`
      (planta circular; exige |a − center| = |b − center|),
    * con `cut=g`, la curva g(x, y) = 0, con g > 0 en el interior: cada rayo
      desde el vértice termina en la primera raíz de g. Con g = f − plano es
      el corte de la superficie por un plano inclinado (tipo Los Manantiales).
      Como g es cuadrática, sobre cada rayo la raíz es exacta (fórmula
      cuadrática) y su derivada se obtiene por derivación implícita.

    Aplicación (s, t) ∈ [0,1]² → center + s·(Q(t) − center), con Q(t) el punto
    del borde exterior en el ángulo φ0 + t·Δφ (o sobre la cuerda). En s = 0 la
    fila colapsa en el vértice: el mallado usa ahí un abanico de triángulos,
    nunca caras degeneradas.
    """

    center: Vec2
    a: Vec2
    b: Vec2
    arc: bool = False
    cut: QuadraticSurface | None = None
    _memo: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    @property
    def radius(self) -> float:
        return math.dist(self.center, self.a)

    @property
    def start_angle(self) -> float:
        return math.atan2(self.a[1] - self.center[1], self.a[0] - self.center[0])

    @property
    def sweep(self) -> float:
        """Ángulo del sector, en (0, π)."""
        cx, cy = self.center
        ua = (self.a[0] - cx, self.a[1] - cy)
        ub = (self.b[0] - cx, self.b[1] - cy)
        return math.atan2(ua[0] * ub[1] - ua[1] * ub[0], ua[0] * ub[0] + ua[1] * ub[1])

    @property
    def outer_kind(self) -> str:
        return "cut" if self.cut is not None else "arc" if self.arc else "chord"

    # --- borde por corte g = 0 ---------------------------------------------------------

    def _ray_coefficients(self, phi: float) -> tuple[float, float, float]:
        """g(center + ρ·d) = A·ρ² + B·ρ + C, con d = (cos φ, sin φ)."""
        g = self.cut
        d = (math.cos(phi), math.sin(phi))
        gx, gy = g.gradient(*self.center)
        return g.quadratic_form(d), gx * d[0] + gy * d[1], g.z(*self.center)

    def _ray_root(self, phi: float) -> float:
        """Primera raíz positiva de g sobre el rayo (forma numéricamente estable)."""
        a_, b_, c_ = self._ray_coefficients(phi)
        disc = b_ * b_ - 4.0 * a_ * c_
        if disc < 0.0:
            raise ValueError("El plano de corte no cierra el lóbulo (un rayo no lo alcanza).")
        q = -0.5 * (b_ + math.copysign(math.sqrt(disc), b_))
        roots = [q / a_ if a_ != 0.0 else math.inf, c_ / q if q != 0.0 else math.inf]
        pos = [r for r in roots if r > 0.0]
        if not pos or min(pos) == math.inf:
            raise ValueError("El plano de corte no cierra el lóbulo (un rayo no lo alcanza).")
        return min(pos)

    def _ray_root_derivative(self, phi: float, rho: float) -> float:
        """dρ/dφ por derivación implícita de A(φ)ρ² + B(φ)ρ + C = 0."""
        g = self.cut
        a_, b_, _ = self._ray_coefficients(phi)
        da = (g.c5 - g.c3) * math.sin(2.0 * phi) + g.c4 * math.cos(2.0 * phi)
        gx, gy = g.gradient(*self.center)
        db = -gx * math.sin(phi) + gy * math.cos(phi)
        return -(da * rho * rho + db * rho) / (2.0 * a_ * rho + b_)

    # --- aplicación -----------------------------------------------------------------------

    def _cut_outer(self, t: float) -> tuple[Vec2, Vec2]:
        """Punto y derivada del borde de corte en t (memorizado: los mismos t se
        repiten en la malla, la cuadratura y las medidas)."""
        hit = self._memo.get(t)
        if hit is None:
            phi = self.start_angle + t * self.sweep
            c, s = math.cos(phi), math.sin(phi)
            rho = self._ray_root(phi)
            drho = self._ray_root_derivative(phi, rho)
            k = self.sweep
            hit = ((self.center[0] + rho * c, self.center[1] + rho * s),
                   (k * (drho * c - rho * s), k * (drho * s + rho * c)))
            self._memo[t] = hit
        return hit

    def outer(self, t: float) -> Vec2:
        if t <= 0.0:
            return self.a
        if t >= 1.0:
            return self.b
        if self.cut is not None:
            return self._cut_outer(t)[0]
        if not self.arc:
            return (self.a[0] + t * (self.b[0] - self.a[0]), self.a[1] + t * (self.b[1] - self.a[1]))
        phi = self.start_angle + t * self.sweep
        r = self.radius
        return (self.center[0] + r * math.cos(phi), self.center[1] + r * math.sin(phi))

    def _outer_derivative(self, t: float) -> Vec2:
        if self.cut is not None:
            return self._cut_outer(min(max(t, 0.0), 1.0))[1]
        if not self.arc:
            return (self.b[0] - self.a[0], self.b[1] - self.a[1])
        phi = self.start_angle + t * self.sweep
        k = self.radius * self.sweep
        return (-k * math.sin(phi), k * math.cos(phi))

    def map(self, s: float, t: float) -> Vec2:
        qx, qy = self.outer(t)
        cx, cy = self.center
        return (cx + s * (qx - cx), cy + s * (qy - cy))

    def jacobian(self, s: float, t: float) -> float:
        qx, qy = self.outer(t)
        dx, dy = self._outer_derivative(t)
        return s * ((qx - self.center[0]) * dy - (qy - self.center[1]) * dx)

    @property
    def corners(self) -> tuple[Vec2, Vec2, Vec2]:
        return (self.center, self.a, self.b)

    # --- medidas ---------------------------------------------------------------------------

    @cached_property
    def _cut_measures(self) -> tuple[float, Vec2, float]:
        """(área, centroide, longitud del borde) por cuadratura sobre el borde."""
        from .quadrature import gauss_legendre

        xs, ws = gauss_legendre(24)
        cx, cy = self.center
        area = mx = my = length = 0.0
        panels = 4
        for p in range(panels):
            for x, w in zip(xs, ws):
                t = (p + 0.5 * (x + 1.0)) / panels
                wt = 0.5 * w / panels
                qx, qy = self.outer(t)
                dx, dy = self._outer_derivative(t)
                cross = (qx - cx) * dy - (qy - cy) * dx
                area += 0.5 * cross * wt
                mx += cross * (cx / 2.0 + (qx - cx) / 3.0) * wt
                my += cross * (cy / 2.0 + (qy - cy) / 3.0) * wt
                length += math.hypot(dx, dy) * wt
        return area, (mx / area, my / area), length

    @property
    def area(self) -> float:
        if self.cut is not None:
            return self._cut_measures[0]
        if self.arc:
            return 0.5 * self.radius ** 2 * self.sweep
        (cx, cy), (ax, ay), (bx, by) = self.corners
        return 0.5 * ((ax - cx) * (by - cy) - (ay - cy) * (bx - cx))

    @property
    def outer_length(self) -> float:
        if self.cut is not None:
            return self._cut_measures[2]
        return self.radius * self.sweep if self.arc else math.dist(self.a, self.b)

    @property
    def perimeter(self) -> float:
        return math.dist(self.center, self.a) + math.dist(self.center, self.b) + self.outer_length

    @property
    def centroid(self) -> Vec2:
        if self.cut is not None:
            return self._cut_measures[1]
        c, a, b = self.corners
        if not self.arc:
            return ((c[0] + a[0] + b[0]) / 3.0, (c[1] + a[1] + b[1]) / 3.0)
        half = 0.5 * self.sweep
        d = 2.0 * self.radius * math.sin(half) / (3.0 * half)
        phi = self.start_angle + half
        return (c[0] + d * math.cos(phi), c[1] + d * math.sin(phi))

    @cached_property
    def size(self) -> float:
        reach = max(math.dist(self.center, self.outer(i / 16.0)) for i in range(17))
        return max(reach, math.dist(self.a, self.b))

    def _straight_edges(self) -> list[tuple[Vec2, Vec2]]:
        edges = [(self.center, self.a), (self.b, self.center)]
        if self.outer_kind == "chord":
            edges.insert(1, (self.a, self.b))
        return edges

    def contains(self, p: Vec2, tol: float = 1e-12) -> bool:
        for (ax, ay), (bx, by) in self._straight_edges():
            if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) < -tol:
                return False
        rho = math.dist(p, self.center)
        if self.arc and self.cut is None:
            return rho <= self.radius + tol
        if self.cut is not None and rho > 0.0:
            phi = math.atan2(p[1] - self.center[1], p[0] - self.center[0])
            return rho <= self._ray_root(phi) + tol * max(1.0, rho)
        return True

    def validate(self) -> None:
        if not (1e-9 < self.sweep < math.pi - 1e-9):
            raise ValueError("El sector debe tener un ángulo entre 0° y 180°, en sentido antihorario.")
        if self.arc and abs(math.dist(self.center, self.b) - self.radius) > 1e-9 * max(self.radius, 1.0):
            raise ValueError("Sector circular con radios distintos.")
        if self.cut is not None:
            scale = max(self.radius, 1.0)
            if self.cut.z(*self.center) <= 0.0:
                raise ValueError("El vértice del sector queda fuera del corte (g ≤ 0 en el centro).")
            for p in (self.a, self.b):
                phi = math.atan2(p[1] - self.center[1], p[0] - self.center[0])
                if abs(self._ray_root(phi) - math.dist(self.center, p)) > 1e-7 * scale:
                    raise ValueError("Los extremos del sector no están sobre la curva de corte.")
            for i in range(33):  # el borde debe existir en todas las direcciones
                self._ray_root(self.start_angle + self.sweep * i / 32.0)

    def clip_line(self, origin: Vec2, direction: Vec2, tol: float | None = None) -> tuple[float, float] | None:
        if tol is None:
            tol = 1e-9 * max(self.size, 1.0)
        span = _clip_halfplanes(self._straight_edges(), origin, direction, tol)
        if span is None:
            return None
        t_min, t_max = span
        if self.arc and self.cut is None:
            wx, wy = origin[0] - self.center[0], origin[1] - self.center[1]
            dd = direction[0] ** 2 + direction[1] ** 2
            dw = (wx * direction[0] + wy * direction[1]) / dd
            disc = dw * dw - (wx * wx + wy * wy - self.radius ** 2) / dd
            if disc < 0.0:
                return None
            root = math.sqrt(disc)
            t_min, t_max = max(t_min, -dw - root), min(t_max, -dw + root)
        elif self.cut is not None:
            # Acota primero con un disco que contiene al lóbulo: una recta paralela a
            # una lima (las limas también son generatrices) deja abierta la cuña.
            wx, wy = origin[0] - self.center[0], origin[1] - self.center[1]
            dd = direction[0] ** 2 + direction[1] ** 2
            dw = (wx * direction[0] + wy * direction[1]) / dd
            disc = dw * dw - (wx * wx + wy * wy - (1.5 * self.size) ** 2) / dd
            if disc < 0.0:
                return None
            t_min, t_max = max(t_min, -dw - math.sqrt(disc)), min(t_max, -dw + math.sqrt(disc))
            if t_max - t_min <= tol:
                return None
            # g sobre la recta es cuadrática (lineal sobre una generatriz): raíces exactas.
            g = self.cut
            al = g.quadratic_form(direction)
            gx, gy = g.gradient(*origin)
            be = gx * direction[0] + gy * direction[1]
            ga = g.z(*origin)
            roots = []
            if abs(al) > 1e-14:
                disc = be * be - 4.0 * al * ga
                if disc >= 0.0:
                    sq = math.sqrt(disc)
                    roots = [(-be - sq) / (2.0 * al), (-be + sq) / (2.0 * al)]
            elif be != 0.0:
                roots = [-ga / be]
            cuts = sorted([t_min, t_max] + [r for r in roots if t_min < r < t_max])
            best = None
            for lo, hi in zip(cuts, cuts[1:]):
                if hi - lo > tol:
                    mid = 0.5 * (lo + hi)
                    if self.contains((origin[0] + mid * direction[0], origin[1] + mid * direction[1]), 1e-9):
                        if best is None or hi - lo > best[1] - best[0]:
                            best = (lo, hi)
            if best is None:
                return None
            t_min, t_max = best
        if t_max - t_min <= tol:
            return None
        return t_min, t_max

    def projection_range(self, m: Vec2) -> tuple[float, float]:
        vals = [m[0] * x + m[1] * y for x, y in self.corners]
        if self.cut is not None:
            from .metrics import _golden

            def proj(t):
                q = self.outer(t)
                return m[0] * q[0] + m[1] * q[1]

            vals += [_golden(proj), -_golden(lambda t: -proj(t))]
        elif self.arc:
            base = m[0] * self.center[0] + m[1] * self.center[1]
            nm = math.hypot(*m)
            for sign in (1.0, -1.0):  # extremos interiores del arco
                rel = (math.atan2(sign * m[1], sign * m[0]) - self.start_angle) % (2.0 * math.pi)
                if rel <= self.sweep:
                    vals.append(base + sign * self.radius * nm)
        return min(vals), max(vals)

    def boundary_pieces(self) -> list[tuple]:
        """Contorno antihorario: ("segment", A, B) o ("curve", Q) con Q(τ), τ ∈ [0, 1]."""
        outer = ("segment", self.a, self.b) if self.outer_kind == "chord" else ("curve", self.outer)
        return [("segment", self.center, self.a), outer, ("segment", self.b, self.center)]

    def boundary_tangents(self, p: Vec2, tol: float) -> list[Vec2]:
        """Tangentes unitarias en planta de las piezas del contorno que pasan por p.

        Exactas: el arco es perpendicular al radio y la curva de corte g = 0 es
        perpendicular a ∇g."""
        out = _segment_tangents(self._straight_edges(), p, tol)
        if self.cut is not None:
            gx, gy = self.cut.gradient(*p)
            gn = math.hypot(gx, gy)
            if gn > 0.0 and abs(self.cut.z(*p)) <= tol * gn:
                out.append((-gy / gn, gx / gn))
        elif self.arc:
            rx, ry = p[0] - self.center[0], p[1] - self.center[1]
            r = math.hypot(rx, ry)
            if r > 0.0 and abs(r - self.radius) <= tol:
                out.append((-ry / r, rx / r))
        return out
