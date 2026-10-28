"""Modelo de datos de un cascarón: parches de superficie, espesor y apoyos.

Un `ShellModel` es una lista de `ShellPatch` (superficie exacta + dominio en
planta) más una especificación de espesor y apoyos. El MVP usa un solo parche;
el paraguas (v0.2) será 4 parches, y los sistemas modulares N parches. El
mallado, las métricas y la exportación trabajan sobre esta estructura, no sobre
"la hypar", y por eso no habrá que reescribirlos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .geometry import QuadDomain, QuadraticSurface, SectorDomain, Vec3

# Modos de espesor
VERTICAL = "VERTICAL"  # desfase en Z: intradós y extradós también son hypars exactas
NORMAL = "NORMAL"      # desfase según la normal: espesor real constante; las capas desfasadas NO son hypars

# Capas
INTRADOS = "INTRADOS"  # cara inferior (cimbra)
MID = "MID"            # superficie media (análisis por elementos finitos)
EXTRADOS = "EXTRADOS"  # cara superior

LAYERS = (INTRADOS, MID, EXTRADOS)


@dataclass(frozen=True)
class ThicknessSpec:
    """Espesor t y su relación con la superficie matemática de referencia.

    La superficie de referencia es la hypar exacta. Las otras caras se obtienen
    desfasándola una distancia s:
        VERTICAL: P(x, y, s) = (x, y, f(x, y) + s)
        NORMAL:   P(x, y, s) = (x, y, f(x, y)) + s·n(x, y)
    """

    thickness: float
    mode: str = NORMAL
    reference: str = INTRADOS

    def __post_init__(self) -> None:
        if self.thickness <= 0.0:
            raise ValueError("El espesor debe ser positivo.")
        if self.mode not in (VERTICAL, NORMAL):
            raise ValueError(f"Modo de espesor desconocido: {self.mode}")
        if self.reference not in LAYERS:
            raise ValueError(f"Capa de referencia desconocida: {self.reference}")

    @property
    def offsets(self) -> tuple[float, float]:
        """(s0, s1): desfases del intradós y del extradós respecto de la referencia."""
        t = self.thickness
        return {INTRADOS: (0.0, t), MID: (-0.5 * t, 0.5 * t), EXTRADOS: (-t, 0.0)}[self.reference]

    def offset_of(self, layer: str) -> float:
        s0, s1 = self.offsets
        return {INTRADOS: s0, MID: 0.5 * (s0 + s1), EXTRADOS: s1}[layer]

    def is_exact_layer(self, layer: str) -> bool:
        """¿Es esa capa una hypar exacta (y por tanto doblemente reglada)?"""
        return self.mode == VERTICAL or layer == self.reference


@dataclass(frozen=True)
class ShellPatch:
    name: str
    surface: QuadraticSurface
    domain: QuadDomain | SectorDomain

    def point(self, x: float, y: float, offset: float = 0.0, mode: str = NORMAL) -> Vec3:
        z = self.surface.z(x, y)
        if offset == 0.0:
            return (x, y, z)
        if mode == VERTICAL:
            return (x, y, z + offset)
        nx, ny, nz = self.surface.unit_normal(x, y)
        return (x + offset * nx, y + offset * ny, z + offset * nz)

    def point_st(self, s: float, t: float, offset: float = 0.0, mode: str = NORMAL) -> Vec3:
        x, y = self.domain.map(s, t)
        return self.point(x, y, offset, mode)


@dataclass(frozen=True)
class SupportPoint:
    name: str
    position: Vec3  # punto de contacto en el intradós
    kind: str       # PINNED / FIXED / COLUMN (concepto; no son condiciones de análisis)


@dataclass(frozen=True)
class Crease:
    """Arista compartida entre dos parches (lima o cumbrera), recta en planta.

    Ambos parches deben coincidir sobre ella (continuidad C0); las normales
    difieren, de ahí el pliegue."""

    patch_a: int
    patch_b: int
    start: tuple[float, float]
    end: tuple[float, float]


@dataclass(frozen=True)
class ShellModel:
    kind: str
    patches: tuple[ShellPatch, ...]
    thickness: ThicknessSpec
    supports: tuple[SupportPoint, ...] = ()
    definition: dict = field(default_factory=dict)  # datos derivados para trazabilidad
    creases: tuple[Crease, ...] = ()
    outline_perimeter: float = 0.0     # perímetro en planta del contorno libre
    characteristic_span: float = 0.0   # luz característica (diagonal, diámetro)
    # Contorno rectangular (si lo hay): permite vigas de borde. grid_factor = cuántos
    # parches hay a lo largo de cada lado, para que la viga tenga un vértice en cada pliegue.
    outline: QuadDomain | None = None
    grid_factor: tuple[int, int] = (1, 1)
    capitals: tuple = ()  # engrosamientos en los apoyos (capitals.Capital): bajan el intradós

    def patch_at(self, x: float, y: float) -> ShellPatch:
        """Parche que contiene el punto en planta (el primero, si está en un pliegue)."""
        for patch in self.patches:
            if patch.domain.contains((x, y), 1e-9):
                return patch
        return self.patches[0]


def offset_point(model: ShellModel, x: float, y: float, owner: int, incident: list[int], s: float) -> Vec3:
    """Punto de la capa desfasada s sobre el punto en planta (x, y).

    `owner` da la altura; `incident` son los parches que concurren en el punto
    (uno en el interior, dos en un pliegue, más en un vértice). Con espesor
    NORMAL el desfase usa el inglete de sus normales.
    """
    z = model.patches[owner].surface.z(x, y)
    if s == 0.0:
        return (x, y, z)
    if model.thickness.mode == VERTICAL:
        return (x, y, z + s)
    m = miter_direction([model.patches[q].surface.unit_normal(x, y) for q in incident])
    return (x + s * m[0], y + s * m[1], z + s * m[2])


def miter_direction(normals: list[Vec3]) -> Vec3:
    """Dirección de desfase m tal que m·nᵢ = 1 para cada normal incidente.

    Desfasar p + s·m deja el punto a distancia s de TODOS los planos tangentes
    que concurren en p: es el inglete exacto de un pliegue. Con una sola
    normal, m = n. Con dos, m = (n1 + n2)/(1 + n1·n2) (solución de norma
    mínima). Con tres o más (vértice donde concurren varios pliegues), mínimos
    cuadrados; en un vértice con simetría de revolución la solución es exacta.
    """
    uniq: list[Vec3] = []
    for n in normals:
        if all(n[0] * u[0] + n[1] * u[1] + n[2] * u[2] < 1.0 - 1e-12 for u in uniq):
            uniq.append(n)
    if len(uniq) == 1:
        return uniq[0]
    if len(uniq) == 2:
        (a, b) = uniq
        d = 1.0 + a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
        return ((a[0] + b[0]) / d, (a[1] + b[1]) / d, (a[2] + b[2]) / d)
    # (NᵀN) m = Nᵀ·1  por Cramer
    m = [[sum(n[i] * n[j] for n in uniq) for j in range(3)] for i in range(3)]
    rhs = [sum(n[i] for n in uniq) for i in range(3)]

    def det3(q):
        return (q[0][0] * (q[1][1] * q[2][2] - q[1][2] * q[2][1])
                - q[0][1] * (q[1][0] * q[2][2] - q[1][2] * q[2][0])
                + q[0][2] * (q[1][0] * q[2][1] - q[1][1] * q[2][0]))

    det = det3(m)
    if abs(det) < 1e-14:  # normales coplanares: dirección media escalada
        sx, sy, sz = (sum(n[i] for n in uniq) for i in range(3))
        ln = math.sqrt(sx * sx + sy * sy + sz * sz)
        avg = (sx / ln, sy / ln, sz / ln)
        k = len(uniq) / sum(avg[0] * n[0] + avg[1] * n[1] + avg[2] * n[2] for n in uniq)
        return (avg[0] * k, avg[1] * k, avg[2] * k)
    out = []
    for col in range(3):
        q = [row[:] for row in m]
        for r in range(3):
            q[r][col] = rhs[r]
        out.append(det3(q) / det)
    return (out[0], out[1], out[2])


# --- Registro de generadores ---------------------------------------------------
# Cada familia de cascarón (HYPAR, UMBRELLA, ...) registra una función que recibe
# un diccionario plano de parámetros y devuelve un ShellModel. Las mismas claves
# son las propiedades de Blender, las variantes guardadas y el JSON exportado.

Generator = Callable[[dict], ShellModel]
_GENERATORS: dict[str, Generator] = {}
_DEFAULTS: dict[str, dict] = {}


def register_generator(kind: str, defaults: dict) -> Callable[[Generator], Generator]:
    def decorator(fn: Generator) -> Generator:
        _GENERATORS[kind] = fn
        _DEFAULTS[kind] = dict(defaults)
        return fn

    return decorator


def generator_defaults(kind: str) -> dict:
    return dict(_DEFAULTS[kind])


def build_model(kind: str, params: dict) -> ShellModel:
    if kind not in _GENERATORS:
        raise KeyError(f"Tipo de cascarón no registrado: {kind}")
    merged = generator_defaults(kind)
    merged.update(params)
    return _GENERATORS[kind](merged)


# Cada familia sabe cómo mallar su topología (un parche, abanico radial...).
# solid(model, nu, nv) -> MeshData;  surface(model, nu, nv, offset) -> MeshData
_MESHERS: dict[str, tuple[Callable, Callable]] = {}


def register_mesher(kind: str, solid: Callable, surface: Callable) -> None:
    _MESHERS[kind] = (solid, surface)


def mesher_for(kind: str) -> tuple[Callable, Callable]:
    return _MESHERS[kind]
