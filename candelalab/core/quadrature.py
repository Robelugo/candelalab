"""Cuadratura de Gauss-Legendre en Python puro (1D y regla compuesta 2D)."""

from __future__ import annotations

import math
from functools import lru_cache


@lru_cache(maxsize=None)
def gauss_legendre(n: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Nodos y pesos de Gauss-Legendre en [-1, 1].

    La regla de n puntos integra exactamente polinomios de grado <= 2n - 1.
    Raíces de P_n por Newton a partir de la aproximación de Tricomi.
    """
    if n < 1:
        raise ValueError("n debe ser >= 1")
    nodes: list[float] = []
    weights: list[float] = []
    for i in range(1, n + 1):
        x = math.cos(math.pi * (i - 0.25) / (n + 0.5))
        for _ in range(100):
            p_prev, p = 1.0, x
            for k in range(2, n + 1):
                p_prev, p = p, ((2 * k - 1) * x * p - (k - 1) * p_prev) / k
            dp = n * (x * p - p_prev) / (x * x - 1.0) if n > 1 else 1.0
            dx = p / dp
            x -= dx
            if abs(dx) < 1e-15:
                break
        # Derivada en la raíz convergida para el peso.
        p_prev, p = 1.0, x
        for k in range(2, n + 1):
            p_prev, p = p, ((2 * k - 1) * x * p - (k - 1) * p_prev) / k
        dp = n * (x * p - p_prev) / (x * x - 1.0) if n > 1 else 1.0
        nodes.append(x)
        weights.append(2.0 / ((1.0 - x * x) * dp * dp))
    return tuple(nodes), tuple(weights)


@lru_cache(maxsize=None)
def unit_square_rule(panels: int = 8, order: int = 8) -> tuple[tuple[float, float, float], ...]:
    """Regla tensorial compuesta en [0, 1]²: tupla de (s, t, peso).

    `panels` subintervalos por dirección, cada uno con Gauss-Legendre de `order`
    puntos. Para integrandos analíticos (todos los de una hypar lo son) el error
    decae exponencialmente con `order`.
    """
    xs, ws = gauss_legendre(order)
    h = 1.0 / panels
    line = [((p + 0.5 * (x + 1.0)) * h, 0.5 * w * h) for p in range(panels) for x, w in zip(xs, ws)]
    return tuple((s, t, ws_ * wt_) for s, ws_ in line for t, wt_ in line)
