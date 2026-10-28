"""Mallado estructurado de parches de cascarón y medidas sobre la malla.

La malla es una muestra de la superficie exacta: cada vértice está sobre la
superficie (o sobre su desfase exacto). Las métricas de diseño se calculan con
las ecuaciones (ver metrics.py); las medidas de malla sirven para verificar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geometry import Vec2, Vec3
from .model import ShellPatch, ThicknessSpec

# Códigos de cara (atributo "candela_part" en Blender)
PART_EXTRADOS = 0
PART_INTRADOS = 1
PART_EDGE = 2
PART_SURFACE = 3
PART_NAMES = {PART_EXTRADOS: "extradós", PART_INTRADOS: "intradós", PART_EDGE: "canto", PART_SURFACE: "superficie"}


@dataclass
class MeshData:
    vertices: list[Vec3] = field(default_factory=list)
    faces: list[tuple[int, ...]] = field(default_factory=list)
    face_parts: list[int] = field(default_factory=list)
    plan_points: list[Vec2] = field(default_factory=list)  # punto en planta de la referencia, por vértice
    sharp_edges: list[tuple[int, int]] = field(default_factory=list)
    grid: tuple[int, int] = (0, 0)
    vertex_patch: list[int] = field(default_factory=list)  # parche que define cada vértice
    node_sets: dict[str, list[int]] = field(default_factory=dict)  # conjuntos con nombre (exportación)
    grids: list[list[list[int]]] = field(default_factory=list)  # filas de índices por parche (muestreo)

    def extend(self, other: "MeshData") -> None:
        base = len(self.vertices)
        self.vertices.extend(other.vertices)
        self.faces.extend(tuple(i + base for i in f) for f in other.faces)
        self.face_parts.extend(other.face_parts)
        self.plan_points.extend(other.plan_points)
        self.sharp_edges.extend((a + base, b + base) for a, b in other.sharp_edges)
        self.vertex_patch.extend(other.vertex_patch or [0] * len(other.vertices))


def grid_plan_points(patch: ShellPatch, nu: int, nv: int) -> list[Vec2]:
    """(nu+1)·(nv+1) puntos en planta, índice = j·(nu+1) + i."""
    return [patch.domain.map(i / nu, j / nv) for j in range(nv + 1) for i in range(nu + 1)]


def boundary_loop(nu: int, nv: int) -> list[int]:
    """Índices del contorno de la rejilla en sentido antihorario visto desde +Z."""
    w = nu + 1
    loop = [i for i in range(nu)]                              # j = 0
    loop += [j * w + nu for j in range(nv)]                    # i = nu
    loop += [nv * w + i for i in range(nu, 0, -1)]             # j = nv
    loop += [j * w for j in range(nv, 0, -1)]                  # i = 0
    return loop


def _grid_quads(nu: int, nv: int, base: int = 0, flip: bool = False) -> list[tuple[int, int, int, int]]:
    """Cuadriláteros de la rejilla. Al invertir se conserva el primer vértice,
    de modo que ambas caras de un sólido se trianguen por la misma diagonal
    (a-c): los cuadriláteros de una hypar son alabeados y, con diagonales
    distintas arriba y abajo, el volumen encerrado deja de ser t·A."""
    w = nu + 1
    quads = []
    for j in range(nv):
        for i in range(nu):
            a = base + j * w + i
            b, c, d = a + 1, a + w + 1, a + w  # antihorario en planta -> normal hacia +Z
            quads.append((a, d, c, b) if flip else (a, b, c, d))
    return quads


def build_surface(patch: ShellPatch, nu: int, nv: int, offset: float = 0.0, mode: str = "NORMAL") -> MeshData:
    """Malla abierta de una sola capa (normal hacia +Z)."""
    _check_res(nu, nv)
    plan = grid_plan_points(patch, nu, nv)
    verts = [patch.point(x, y, offset, mode) for x, y in plan]
    faces = _grid_quads(nu, nv)
    w = nu + 1
    node_sets = {  # aristas del dominio en orden antihorario (rectángulo: ymin, xmax, ymax, xmin)
        "ymin": [i for i in range(nu + 1)],
        "xmax": [j * w + nu for j in range(nv + 1)],
        "ymax": [nv * w + i for i in range(nu + 1)],
        "xmin": [j * w for j in range(nv + 1)],
    }
    grids = [[[j * w + i for i in range(nu + 1)] for j in range(nv + 1)]]
    return MeshData(verts, faces, [PART_SURFACE] * len(faces), list(plan), [], (nu, nv),
                    [0] * len(verts), node_sets, grids)


def build_solid(patch: ShellPatch, thickness: ThicknessSpec, nu: int, nv: int) -> MeshData:
    """Sólido cerrado y orientable: intradós + extradós + cantos.

    Vértices 0..n-1 = intradós (s0), n..2n-1 = extradós (s1). Los cantos unen
    cada punto del contorno con su desfase, es decir, son verticales en modo
    VERTICAL y siguen la normal en modo NORMAL (consistente con la fórmula de
    Steiner usada para el volumen analítico).
    """
    _check_res(nu, nv)
    s0, s1 = thickness.offsets
    plan = grid_plan_points(patch, nu, nv)
    n = len(plan)
    bottom = [patch.point(x, y, s0, thickness.mode) for x, y in plan]
    top = [patch.point(x, y, s1, thickness.mode) for x, y in plan]

    faces: list[tuple[int, ...]] = []
    parts: list[int] = []
    for q in _grid_quads(nu, nv, base=n):
        faces.append(q)
        parts.append(PART_EXTRADOS)
    for q in _grid_quads(nu, nv, base=0, flip=True):
        faces.append(q)
        parts.append(PART_INTRADOS)

    loop = boundary_loop(nu, nv)
    sharp = []
    for k, a in enumerate(loop):
        b = loop[(k + 1) % len(loop)]
        faces.append((a, b, n + b, n + a))  # normal hacia afuera
        parts.append(PART_EDGE)
        sharp.append((a, b))
        sharp.append((n + a, n + b))
    return MeshData(bottom + top, faces, parts, plan + plan, sharp, (nu, nv), [0] * (2 * n))


def single_patch_solid(model, nu: int, nv: int) -> MeshData:
    return build_solid(model.patches[0], model.thickness, nu, nv)


def single_patch_surface(model, nu: int, nv: int, offset: float) -> MeshData:
    return build_surface(model.patches[0], nu, nv, offset, model.thickness.mode)


def _check_res(nu: int, nv: int) -> None:
    if nu < 1 or nv < 1:
        raise ValueError("La resolución de malla debe ser >= 1 en cada dirección.")


# --- medidas -------------------------------------------------------------------


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def mesh_volume(md: MeshData) -> float:
    """Volumen encerrado (teorema de la divergencia, triangulación en abanico)."""
    v = md.vertices
    total = 0.0
    for f in md.faces:
        p0 = v[f[0]]
        for i in range(1, len(f) - 1):
            total += _dot(p0, _cross(v[f[i]], v[f[i + 1]]))
    return total / 6.0


def mesh_area(md: MeshData, parts: set[int] | None = None) -> float:
    v = md.vertices
    total = 0.0
    for f, part in zip(md.faces, md.face_parts):
        if parts is not None and part not in parts:
            continue
        p0 = v[f[0]]
        for i in range(1, len(f) - 1):
            total += 0.5 * _norm(_cross(_sub(v[f[i]], p0), _sub(v[f[i + 1]], p0)))
    return total


def min_face_area(md: MeshData) -> float:
    v = md.vertices
    best = math.inf
    for f in md.faces:
        p0 = v[f[0]]
        a = sum(0.5 * _norm(_cross(_sub(v[f[i]], p0), _sub(v[f[i + 1]], p0))) for i in range(1, len(f) - 1))
        best = min(best, a)
    return best


def max_quad_warp(md: MeshData, parts: set[int] | None = None) -> float:
    """Máxima separación entre las dos triangulaciones posibles de cada cuadrilátero.

    Para un cuadrilátero alabeado a-b-c-d es la distancia, medida según su
    normal media, entre los puntos medios de las diagonales ac y bd. Es 0 para
    cuadriláteros planos; importa para paneles planos y elementos S4 de FEA.
    """
    v = md.vertices
    worst = 0.0
    for f, part in zip(md.faces, md.face_parts):
        if len(f) != 4 or (parts is not None and part not in parts):
            continue
        a, b, c, d = (v[i] for i in f)
        n = _cross(_sub(c, a), _sub(d, b))
        ln = _norm(n)
        if ln == 0.0:
            continue
        gap = _sub(((a[0] + c[0]) * 0.5, (a[1] + c[1]) * 0.5, (a[2] + c[2]) * 0.5),
                   ((b[0] + d[0]) * 0.5, (b[1] + d[1]) * 0.5, (b[2] + d[2]) * 0.5))
        worst = max(worst, abs(_dot(gap, n)) / ln)
    return worst


def topology_report(md: MeshData) -> dict:
    """Comprueba que la malla sea una 2-variedad con orientación consistente.

    closed: cada arista pertenece a exactamente 2 caras.
    consistent: ninguna arista dirigida se repite (normales coherentes).
    """
    directed: dict[tuple[int, int], int] = {}
    undirected: dict[tuple[int, int], int] = {}
    for f in md.faces:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            directed[(a, b)] = directed.get((a, b), 0) + 1
            key = (a, b) if a < b else (b, a)
            undirected[key] = undirected.get(key, 0) + 1
    boundary = sum(1 for c in undirected.values() if c == 1)
    nonmanifold = sum(1 for c in undirected.values() if c > 2)
    repeated = sum(1 for c in directed.values() if c > 1)
    return {
        "vertices": len(md.vertices),
        "faces": len(md.faces),
        "edges": len(undirected),
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
        "closed": boundary == 0 and nonmanifold == 0,
        "consistent": repeated == 0,
    }
