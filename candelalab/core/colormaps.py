"""Rampas de color para mapas de análisis (colores en espacio lineal para Blender)."""

from __future__ import annotations

RGBA = tuple[float, float, float, float]

# Paradas en sRGB (aprox. viridis): secuencial, perceptualmente ordenada.
VIRIDIS = ((0.267, 0.005, 0.329), (0.229, 0.322, 0.546), (0.128, 0.567, 0.551),
           (0.369, 0.789, 0.383), (0.993, 0.906, 0.144))
# Pendiente: verde -> amarillo -> naranja hasta el límite de colado; rojo por encima.
SLOPE_OK = ((0.20, 0.62, 0.32), (0.93, 0.83, 0.25), (0.96, 0.55, 0.15))
SLOPE_OVER = (0.80, 0.08, 0.30)
# Divergente (azul - gris - rojo) para valores con signo, centrada en 0.
DIVERGING = ((0.23, 0.30, 0.75), (0.55, 0.69, 0.99), (0.87, 0.87, 0.87), (0.96, 0.60, 0.48), (0.71, 0.02, 0.15))


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _ramp(stops, t: float) -> RGBA:
    t = min(max(t, 0.0), 1.0)
    pos = t * (len(stops) - 1)
    i = min(int(pos), len(stops) - 2)
    f = pos - i
    a, b = stops[i], stops[i + 1]
    rgb = [srgb_to_linear(a[k] + f * (b[k] - a[k])) for k in range(3)]
    return (rgb[0], rgb[1], rgb[2], 1.0)


def sequential(t: float) -> RGBA:
    return _ramp(VIRIDIS, t)


def diverging(t: float) -> RGBA:
    """t = 0.5 es el cero; < 0.5 azul (negativo), > 0.5 rojo (positivo)."""
    return _ramp(DIVERGING, t)


def slope_color(slope: float, limit: float) -> RGBA:
    if slope > limit:
        return (srgb_to_linear(SLOPE_OVER[0]), srgb_to_linear(SLOPE_OVER[1]), srgb_to_linear(SLOPE_OVER[2]), 1.0)
    return _ramp(SLOPE_OK, slope / limit if limit > 0 else 1.0)
