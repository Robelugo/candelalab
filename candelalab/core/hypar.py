"""Generador del paraboloide hiperbólico individual (MVP).

Parametrización
---------------
Planta: rectángulo Lx × Ly centrado en el origen, a = Lx/2, b = Ly/2.

Sea θ el ángulo entre los bordes del rectángulo y las generatrices. En el
sistema girado de las generatrices

    u =  x·cosθ + y·sinθ
    v = −x·sinθ + y·cosθ

la superficie es

    z(x, y) = H + p·x + q·y + k·u·v

    H  altura del centro            p = tan(αx), q = tan(αy)  inclinación
    k  coeficiente de alabeo [1/m]  (curvatura gaussiana en el vértice: −k²)

Desarrollando u·v:  c3 = −k·sin2θ/2,  c4 = k·cos2θ,  c5 = k·sin2θ/2, y el
hessiano tiene det = −k² < 0 para todo θ: siempre es una hypar.

* θ = 0°  -> z = H + p·x + q·y + k·x·y. Los cuatro bordes son generatrices
  (bordes rectos). Es la hypar "de cuatro esquinas" que Candela usó en
  paraguas y bóvedas por arista.
* θ = 45° -> z = H + p·x + q·y + (k/2)·(y² − x²). Los bordes están en los
  planos principales y son parábolas (silla de montar de bordes curvos).
* Cualquier otro θ da bordes parabólicos oblicuos: toda sección vertical de
  una hypar es una parábola, salvo en la dirección de una generatriz.

Alabeo f [m]: máxima separación vertical, en el contorno, entre la superficie
y su plano tangente en el centro: f = |k|·max|u·v| sobre el rectángulo. Como
u·v es armónica, su máximo está en el contorno. Para θ = 0 es simplemente la
subida/bajada de las esquinas: f = k·a·b. El signo de f elige el sentido del
alabeo (qué par de esquinas sube).

Definición por esquinas (sólo bordes rectos, θ = 0): con las alturas de las
cuatro esquinas, la interpolación bilineal ES la hypar:
    H = (zSO + zSE + zNE + zNO)/4
    p = (zSE + zNE − zSO − zNO)/(4a)
    q = (zNO + zNE − zSO − zSE)/(4b)
    k = (zNE − zSE − zNO + zSO)/(4ab)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .defaults import COMMON_DEFAULTS
from .elements import select_supports
from .geometry import QuadDomain, QuadraticSurface
from .meshing import single_patch_solid, single_patch_surface
from .model import ShellModel, ShellPatch, ThicknessSpec, register_generator, register_mesher

EDGE_MODE_ANGLES = {"RULED": 0.0, "PRINCIPAL": math.pi / 4.0}

DEFAULTS = {
    **COMMON_DEFAULTS,
    "shell_type": "HYPAR",
    "definition": "PARAMS",
    "span_x": 10.0,
    "span_y": 10.0,
    "rise": 2.5,
    "warp": 2.5,
    "edge_mode": "RULED",
    "edge_angle": 0.0,        # rad, sólo con edge_mode = CUSTOM
    "tilt_x": 0.0,            # rad
    "tilt_y": 0.0,            # rad
    "z_sw": 5.0, "z_se": 0.0, "z_ne": 5.0, "z_nw": 0.0,
    "support_layout": "LOW_CORNERS",
}

MIN_WARP = 1e-6  # m; por debajo la superficie se considera plana


@dataclass(frozen=True)
class HyparParams:
    span_x: float = 10.0
    span_y: float = 10.0
    rise: float = 2.5
    warp: float = 2.5
    edge_angle: float = 0.0  # θ en rad
    slope_x: float = 0.0     # p
    slope_y: float = 0.0     # q

    def validate(self) -> None:
        if self.span_x <= 0.0 or self.span_y <= 0.0:
            raise ValueError("Las luces deben ser positivas.")
        if not (-1e-12 <= self.edge_angle <= math.pi / 2.0 + 1e-12):
            raise ValueError("El ángulo de bordes debe estar entre 0° y 90°.")

    @classmethod
    def from_dict(cls, d: dict) -> "HyparParams":
        sx, sy = float(d["span_x"]), float(d["span_y"])
        if d.get("definition", "PARAMS") == "CORNERS":
            a, b = 0.5 * sx, 0.5 * sy
            zsw, zse, zne, znw = (float(d[k]) for k in ("z_sw", "z_se", "z_ne", "z_nw"))
            k = (zne - zse - znw + zsw) / (4.0 * a * b)
            return cls(sx, sy, rise=(zsw + zse + zne + znw) / 4.0, warp=k * a * b, edge_angle=0.0,
                       slope_x=(zse + zne - zsw - znw) / (4.0 * a),
                       slope_y=(znw + zne - zsw - zse) / (4.0 * b))
        mode = d.get("edge_mode", "RULED")
        theta = EDGE_MODE_ANGLES[mode] if mode in EDGE_MODE_ANGLES else float(d.get("edge_angle", 0.0))
        return cls(sx, sy, rise=float(d["rise"]), warp=float(d["warp"]), edge_angle=theta,
                   slope_x=math.tan(float(d["tilt_x"])), slope_y=math.tan(float(d["tilt_y"])))


def max_abs_quadratic_on_boundary(c3: float, c4: float, c5: float, domain: QuadDomain) -> float:
    """max |c3·x² + c4·x·y + c5·y²| sobre el contorno del dominio (exacto).

    Sobre cada arista A + τ·D la forma es α·τ² + β·τ + γ; se evalúan los
    extremos y el punto crítico interior.
    """
    q = QuadraticSurface(0.0, 0.0, 0.0, c3, c4, c5)
    best = 0.0
    for (ax, ay), (bx, by) in domain.edges:
        dx, dy = bx - ax, by - ay
        alpha = q.quadratic_form((dx, dy))
        beta = 2.0 * c3 * ax * dx + c4 * (ax * dy + ay * dx) + 2.0 * c5 * ay * dy
        gamma = q.quadratic_form((ax, ay))
        cands = [0.0, 1.0]
        if abs(alpha) > 1e-300:
            tau = -beta / (2.0 * alpha)
            if 0.0 < tau < 1.0:
                cands.append(tau)
        best = max(best, max(abs(alpha * t * t + beta * t + gamma) for t in cands))
    return best


def hypar_surface(p: HyparParams) -> tuple[QuadraticSurface, QuadDomain, float]:
    """Devuelve (superficie, dominio en planta, k)."""
    p.validate()
    domain = QuadDomain.rectangle(p.span_x, p.span_y)
    s2, c2 = math.sin(2.0 * p.edge_angle), math.cos(2.0 * p.edge_angle)
    u3, u4, u5 = -0.5 * s2, c2, 0.5 * s2
    m = max_abs_quadratic_on_boundary(u3, u4, u5, domain)
    k = 0.0 if abs(p.warp) < MIN_WARP else p.warp / m
    surf = QuadraticSurface(p.rise, p.slope_x, p.slope_y, k * u3, k * u4, k * u5)
    return surf, domain, k


@register_generator("HYPAR", DEFAULTS)
def build_hypar(d: dict) -> ShellModel:
    params = HyparParams.from_dict(d)
    surf, domain, k = hypar_surface(params)
    domain.validate()
    patch = ShellPatch("hypar", surf, domain)
    thickness = ThicknessSpec(float(d["thickness"]), d["thickness_mode"], d["thickness_reference"])
    supports = select_supports(patch, thickness, d["support_layout"], d["support_type"])
    dirs = surf.asymptotic_directions()
    definition = {
        "family": "hyperbolic_paraboloid",
        "is_hyperbolic": surf.is_hyperbolic(),
        "equation": "z = c0 + c1*x + c2*y + c3*x^2 + c4*x*y + c5*y^2",
        "coefficients": {f"c{i}": v for i, v in enumerate((surf.c0, surf.c1, surf.c2, surf.c3, surf.c4, surf.c5))},
        "span_x": params.span_x,
        "span_y": params.span_y,
        "rise": params.rise,
        "warp": params.warp,
        "k": k,
        "edge_angle_deg": math.degrees(params.edge_angle),
        "slope_x": params.slope_x,
        "slope_y": params.slope_y,
        "ruling_directions_deg": [math.degrees(math.atan2(v[1], v[0])) for v in dirs] if dirs else [],
        "corner_heights": {n: surf.z(x, y) for n, (x, y) in zip(("SO", "SE", "NE", "NO"), domain.corners)},
    }
    return ShellModel("HYPAR", (patch,), thickness, supports, definition,
                      outline_perimeter=domain.perimeter,
                      characteristic_span=math.dist(domain.p00, domain.p11),
                      outline=domain)


register_mesher("HYPAR", single_patch_solid, single_patch_surface)
