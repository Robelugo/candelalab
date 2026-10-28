"""Elementos del concepto estructural: apoyos, vigas de borde y rigidizadores.

Son geometría conceptual para visualizar, cuantificar material y exportar
nodos de apoyo. NO son resultados de dimensionamiento: el tamaño de columnas y
vigas lo fija el usuario y debe verificarse con análisis.
"""

from __future__ import annotations

import math

from .capitals import capital_drop
from .geometry import QuadDomain, SectorDomain, Vec2, Vec3
from .meshing import PART_EDGE, PART_EXTRADOS, PART_INTRADOS, PART_SURFACE, MeshData
from .model import INTRADOS, VERTICAL, ShellPatch, SupportPoint, ThicknessSpec

SUPPORT_LAYOUTS = ("NONE", "LOW_CORNERS", "ALL_CORNERS", "LOW_EDGE_MIDPOINTS", "CENTER")
SUPPORT_KINDS = ("PINNED", "FIXED", "COLUMN")

# Orden de aristas del dominio: p00→p10, p10→p11, p11→p01, p01→p00
EDGE_KEYS = ("ymin", "xmax", "ymax", "xmin")


# --- apoyos --------------------------------------------------------------------


def intrados_point(patch: ShellPatch, thickness: ThicknessSpec, x: float, y: float) -> Vec3:
    return patch.point(x, y, thickness.offset_of(INTRADOS), thickness.mode)


def support_candidates(patch: ShellPatch, thickness: ThicknessSpec) -> dict[str, Vec3]:
    """Puntos notables del intradós: esquinas, puntos medios de borde y centro."""
    p00, p10, p11, p01 = patch.domain.corners
    plan: dict[str, Vec2] = {
        "SO": p00, "SE": p10, "NE": p11, "NO": p01,
        "S": _mid(p00, p10), "E": _mid(p10, p11), "N": _mid(p11, p01), "O": _mid(p01, p00),
        "C": patch.domain.centroid,
    }
    return {name: intrados_point(patch, thickness, x, y) for name, (x, y) in plan.items()}


def select_supports(patch: ShellPatch, thickness: ThicknessSpec, layout: str, kind: str) -> tuple[SupportPoint, ...]:
    if layout not in SUPPORT_LAYOUTS:
        raise ValueError(f"Disposición de apoyos desconocida: {layout}")
    cand = support_candidates(patch, thickness)
    corners = ["SO", "SE", "NE", "NO"]
    mids = ["S", "E", "N", "O"]

    def lowest(names: list[str], count: int) -> list[str]:
        return sorted(names, key=lambda n: (round(cand[n][2], 9), n))[:count]

    names = {
        "NONE": [],
        "ALL_CORNERS": corners,
        "LOW_CORNERS": lowest(corners, 2),
        "LOW_EDGE_MIDPOINTS": lowest(mids, 2),
        "CENTER": ["C"],
    }[layout]
    return tuple(SupportPoint(n, cand[n], kind) for n in names)


def intrados_height(model, x: float, y: float) -> float | None:
    """Altura del intradós sobre el punto en planta (None fuera de la planta).

    Exacta si el intradós es la referencia o el espesor es VERTICAL; con
    espesor NORMAL y otra referencia, aproximación de primer orden f + s0·W.
    """
    for patch in model.patches:
        if patch.domain.contains((x, y), 1e-9):
            return patch_intrados_height(patch, model.thickness, x, y, capital_drop(model, x, y))
    return None


def patch_intrados_height(patch: ShellPatch, th: ThicknessSpec, x: float, y: float, drop: float = 0.0) -> float:
    """Altura del intradós de un parche (misma aproximación que intrados_height), bajado `drop`
    donde hay capitel."""
    s0 = th.offset_of(INTRADOS) - drop
    surf = patch.surface
    if s0 == 0.0 or th.mode == VERTICAL:
        return surf.z(x, y) + s0
    return surf.z(x, y) + s0 * surf.metric_factor(x, y)


def support_markers(supports: tuple[SupportPoint, ...], size: float, ground_z: float = 0.0,
                    model=None) -> MeshData:
    """Símbolos sólidos de apoyo (todas las normales hacia afuera).

    PINNED: pirámide con el vértice en el punto de apoyo.
    FIXED:  bloque bajo el punto de apoyo.
    COLUMN: prisma desde el nivel de desplante hasta el intradós.

    Bloques y columnas terminan en la cota más baja del intradós dentro de su
    huella (muestreo 5×5, exacto en parches bilineales como el paraguas), de
    modo que nunca atraviesan el cascarón cuando el apoyo está en una cumbrera.
    """
    md = MeshData()
    h = 0.5 * size
    for sp in supports:
        x, y, z = sp.position
        if sp.kind == "PINNED":
            md.extend(_pyramid(x, y, z, h, size))
            continue
        top = z
        if model is not None:
            heights = [intrados_height(model, x + u * h, y + v * h)
                       for u in (-1.0, -0.5, 0.0, 0.5, 1.0) for v in (-1.0, -0.5, 0.0, 0.5, 1.0)]
            top = min([z] + [hh for hh in heights if hh is not None])
        if sp.kind == "COLUMN" and top - ground_z > 1e-6:
            md.extend(_box(x, y, ground_z, top, h))
        else:
            md.extend(_box(x, y, top - size, top, h))
    return md


# --- vigas de borde --------------------------------------------------------------


def beam_strips(outline: QuadDomain, edges: dict[str, bool],
                width: float) -> list[tuple[str, tuple[Vec2, Vec2, Vec2, Vec2]]]:
    """Franjas en planta (A, B, B', A') antihorarias, con inglete a 45° donde
    se encuentran dos vigas y corte recto donde termina una sola."""
    corners = outline.corners
    active = [edges.get(k, False) for k in EDGE_KEYS]
    strips = []
    for i, key in enumerate(EDGE_KEYS):
        if not active[i]:
            continue
        a, b = corners[i], corners[(i + 1) % 4]
        ex, ey = b[0] - a[0], b[1] - a[1]
        ln = (ex * ex + ey * ey) ** 0.5
        dx, dy = ex / ln, ey / ln
        nx, ny = -dy, dx  # hacia el interior
        miter_a = width if active[(i - 1) % 4] else 0.0
        miter_b = width if active[(i + 1) % 4] else 0.0
        a_in = (a[0] + width * nx + miter_a * dx, a[1] + width * ny + miter_a * dy)
        b_in = (b[0] + width * nx - miter_b * dx, b[1] + width * ny - miter_b * dy)
        strips.append((key, (a, b, b_in, a_in)))
    return strips


def build_edge_beams(model, edges: dict[str, bool], width: float, depth: float,
                     segments: tuple[int, int]) -> tuple[MeshData, float, float]:
    """Vigas colgadas bajo el intradós del contorno rectangular del modelo
    (model.outline). Devuelve (malla, volumen, área en planta).

    Con varios parches (paraguas) la altura se toma del parche que contiene cada
    punto; `segments` debe dejar un vértice en cada pliegue que llega al borde.

    La cara superior sigue la altura del intradós sobre cada punto en planta y
    la inferior está `depth` más abajo, así que el sólido es un prisma vertical
    y su volumen es exactamente depth · área_en_planta.

    La altura del intradós es exacta si es la superficie de referencia o si el
    espesor es VERTICAL; con espesor NORMAL y otra referencia se usa la
    aproximación de primer orden z ≈ f + s0·W.
    """
    if width <= 0.0 or depth <= 0.0:
        raise ValueError("El ancho y el peralte de las vigas deben ser positivos.")
    outline, thickness = model.outline, model.thickness
    xs = [p[0] for p in outline.corners]
    ys = [p[1] for p in outline.corners]
    if 2.0 * width >= min(max(xs) - min(xs), max(ys) - min(ys)):
        raise ValueError("Las vigas de borde son más anchas que la mitad de la luz menor.")

    def z_top(x: float, y: float) -> float:
        z = intrados_height(model, x, y)
        return z if z is not None else model.patch_at(x, y).surface.z(x, y) + thickness.offset_of(INTRADOS)

    md = MeshData()
    plan_area = 0.0
    for key, (a, b, b_in, a_in) in beam_strips(outline, edges, width):
        n_seg = segments[0] if key in ("ymin", "ymax") else segments[1]
        n_seg = max(1, n_seg)
        outer = [_lerp2(a, b, k / n_seg) for k in range(n_seg + 1)]
        inner = [_lerp2(a_in, b_in, k / n_seg) for k in range(n_seg + 1)]
        ring = outer + inner  # 0..n = exterior, n+1..2n+1 = interior
        m = len(ring)
        top = [(x, y, z_top(x, y)) for x, y in ring]
        bot = [(x, y, zt - depth) for x, y, zt in top]
        part = MeshData(bot + top, [], [], ring + ring, [], (n_seg, 1))
        o = lambda k: k            # noqa: E731
        i_ = lambda k: n_seg + 1 + k  # noqa: E731
        for k in range(n_seg):
            part.faces.append((m + o(k), m + o(k + 1), m + i_(k + 1), m + i_(k)))   # superior
            part.faces.append((o(k), i_(k), i_(k + 1), o(k + 1)))                   # inferior (misma diagonal)
            part.faces.append((o(k), o(k + 1), m + o(k + 1), m + o(k)))             # cara exterior
            part.faces.append((i_(k + 1), i_(k), m + i_(k), m + i_(k + 1)))         # cara interior
            part.face_parts += [PART_EXTRADOS, PART_INTRADOS, PART_EDGE, PART_EDGE]
        part.faces.append((o(n_seg), i_(n_seg), m + i_(n_seg), m + o(n_seg)))       # testero B
        part.faces.append((i_(0), o(0), m + o(0), m + i_(0)))                       # testero A
        part.face_parts += [PART_EDGE, PART_EDGE]
        md.extend(part)
        plan_area += _quad_area((a, b, b_in, a_in))
    return md, depth * plan_area, plan_area


# --- rigidizadores -----------------------------------------------------------------
# Nervios y vigas son prismas verticales sobre una franja en planta: la cara
# superior sigue el intradós y la inferior está `depth` más abajo, así que el
# volumen es exactamente depth · área de la franja.


def build_crease_ribs(model, width: float, depth: float, segments: int) -> tuple[MeshData, float, float]:
    """Nervios bajo las limas, centrados en cada una. Devuelve (malla, volumen, área en planta).

    Cada nervio se arma en dos mitades, una por parche, para que su cara
    superior siga el intradós de su lado: el pliegue queda en el eje del
    nervio. Donde concurren varias limas (clave, cabeza de columna) cada mitad
    se recorta por la bisectriz con la lima vecina, de modo que los nervios se
    unen sin solaparse y el área (y el volumen) es exacta: con ancho w y
    semiángulo α hasta la vecina, a cada mitad le falta un triángulo de área
    w²/(8·tan α).
    """
    if width <= 0.0 or depth <= 0.0:
        raise ValueError("El ancho y el peralte de los nervios deben ser positivos.")
    creases = model.creases
    for c in creases:
        if 4.0 * width > math.dist(c.start, c.end):
            raise ValueError("Los nervios son demasiado anchos para la longitud de las limas.")
    angle = [math.atan2(c.end[1] - c.start[1], c.end[0] - c.start[0]) for c in creases]

    def half_angle(i: int, side: int) -> float:
        """Semiángulo hasta la lima vecina que sale del mismo punto, por el lado `side`."""
        best = math.pi
        for j, c in enumerate(creases):
            if j != i and math.dist(c.start, creases[i].start) < 1e-9:
                delta = (side * (angle[j] - angle[i])) % (2.0 * math.pi)
                if delta > 1e-9:
                    best = min(best, delta)
        return 0.5 * best

    md = MeshData()
    area = 0.0
    h = 0.5 * width
    m = max(2, segments)
    for i, c in enumerate(creases):
        length = math.dist(c.start, c.end)
        u = ((c.end[0] - c.start[0]) / length, (c.end[1] - c.start[1]) / length)
        for side in (1, -1):
            n = (-side * u[1], side * u[0])  # hacia el lado del parche
            probe = (c.start[0] + 0.5 * length * u[0] + 0.25 * width * n[0],
                     c.start[1] + 0.5 * length * u[1] + 0.25 * width * n[1])
            patch = next((p for p in model.patches if p.domain.contains(probe, 1e-12)), None)
            if patch is None:
                continue  # lima de borde: no hay cascarón de este lado
            origin = (c.start[0] + h * n[0], c.start[1] + h * n[1])
            seg = patch.domain.clip_line(origin, u)
            if seg is None:
                raise ValueError("Los nervios son demasiado anchos para los paños junto a las limas.")
            outer_len = seg[1]
            alpha = half_angle(i, side)
            kink = h / math.tan(alpha) if alpha < 0.5 * math.pi - 1e-9 else 0.0
            fractions = sorted({k / m for k in range(m + 1)} | ({kink / outer_len} if 0.0 < kink < outer_len else set()))

            def offset(a, n=n):
                return min(h, a * math.tan(alpha)) if kink > 0.0 else h

            inner = [(c.start[0] + f * length * u[0], c.start[1] + f * length * u[1]) for f in fractions]
            outer = [(c.start[0] + f * outer_len * u[0] + offset(f * outer_len) * n[0],
                      c.start[1] + f * outer_len * u[1] + offset(f * outer_len) * n[1]) for f in fractions]
            shared = [offset(f * outer_len) < h - 1e-12 for f in fractions]  # tramo recortado: cara compartida

            def z_top(x, y, patch=patch):
                return patch_intrados_height(patch, model.thickness, x, y, capital_drop(model, x, y))

            part, a = _strip_prism(inner, outer, z_top, depth, inner_part=PART_SURFACE,
                                   outer_parts=[PART_SURFACE if s0 and s1 else PART_EDGE
                                                for s0, s1 in zip(shared, shared[1:])],
                                   start_part=PART_EDGE, end_part=PART_EDGE, flip=side < 0)
            md.extend(part)
            area += a
    return md, depth * area, area


def build_free_edge_beams(model, width: float, depth: float, samples: int) -> tuple[MeshData, float, float]:
    """Viga bajo el borde libre de una bóveda radial: franja de ancho `width` hacia
    el interior desde el borde (arco, cuerda o curva de corte), recortada en las
    limas. Devuelve (malla, volumen, área en planta).

    El área se calcula con una poligonal fina del borde (256 tramos por lóbulo);
    la malla usa `samples` tramos."""
    if width <= 0.0 or depth <= 0.0:
        raise ValueError("El ancho y el peralte de las vigas deben ser positivos.")
    md = MeshData()
    area = 0.0
    for patch in model.patches:
        dom = patch.domain
        if not isinstance(dom, SectorDomain):
            raise ValueError("La viga de borde libre sólo existe en bóvedas radiales.")
        outer, inner = _edge_band(dom, width, max(2, samples))

        def z_top(x, y, patch=patch):
            return patch_intrados_height(patch, model.thickness, x, y, capital_drop(model, x, y))

        part, _a = _strip_prism(outer, inner, z_top, depth, inner_part=PART_EDGE,
                                outer_parts=[PART_EDGE] * (len(outer) - 1),
                                start_part=PART_SURFACE, end_part=PART_SURFACE, flip=False)
        md.extend(part)
        fine_outer, fine_inner = _edge_band(dom, width, 256)
        area += abs(_polygon_area(fine_outer + fine_inner[::-1]))
    return md, depth * area, area


def _edge_band(dom: SectorDomain, width: float, m: int) -> tuple[list[Vec2], list[Vec2]]:
    """Borde exterior del lóbulo (de a a b) y su paralela hacia dentro a distancia width,
    cortada por las dos limas (los radios del sector).

    En una esquina convexa (planta poligonal) la paralela cruza la lima antes de
    llegar al extremo: los puntos que quedan al otro lado se llevan al punto de
    cruce, y esos tramos de la franja se vuelven triángulos."""
    outer = [dom.outer(k / m) for k in range(m + 1)]
    inner = []
    for k, q in enumerate(outer):
        p0, p1 = outer[max(k - 1, 0)], outer[min(k + 1, m)]
        tx, ty = p1[0] - p0[0], p1[1] - p0[1]
        ln = math.hypot(tx, ty)
        inner.append((q[0] - width * ty / ln, q[1] + width * tx / ln))  # normal izquierda: hacia el interior
    c = dom.center

    def side(p, ray):  # > 0: a la izquierda del radio centro→ray
        return (ray[0] - c[0]) * (p[1] - c[1]) - (ray[1] - c[1]) * (p[0] - c[0])

    for ray, idx, sign in ((dom.a, list(range(m + 1)), 1.0), (dom.b, list(range(m, -1, -1)), -1.0)):
        first = next((k for k, i in enumerate(idx) if sign * side(inner[i], ray) > 0.0), None)
        if first is None:
            raise ValueError("La viga del borde libre es demasiado ancha para los lóbulos.")
        j = idx[first]
        prev = idx[first - 1] if first > 0 else idx[first + 1]
        hit = _line_hit(inner[prev], inner[j], c, ray)
        for i in idx[:first]:
            inner[i] = hit
        if first == 0:
            inner[j] = hit  # la paralela empieza dentro: se prolonga hasta la lima
    for p in inner:
        if not dom.contains(p, 1e-9 * max(1.0, dom.size)):
            raise ValueError("La viga del borde libre es demasiado ancha para los lóbulos.")
    return outer, inner


def _line_hit(p: Vec2, q: Vec2, a: Vec2, b: Vec2) -> Vec2:
    """Intersección de la recta p-q con la recta a-b."""
    d1 = (q[0] - p[0], q[1] - p[1])
    d2 = (b[0] - a[0], b[1] - a[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-14:
        return p
    t = ((a[0] - p[0]) * d2[1] - (a[1] - p[1]) * d2[0]) / den
    return (p[0] + t * d1[0], p[1] + t * d1[1])


def _polygon_area(pts: list[Vec2]) -> float:
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                     for i in range(len(pts)))


def _strip_prism(first: list[Vec2], second: list[Vec2], z_top, depth: float, inner_part: int,
                 outer_parts: list[int], start_part: int, end_part: int, flip: bool) -> tuple[MeshData, float]:
    """Prisma vertical cerrado sobre la franja entre dos poligonales con el mismo
    número de puntos (`second` a la izquierda de `first`; con flip, a la derecha).
    Si empiezan en el mismo punto, el primer tramo es un triángulo.
    Devuelve (malla, área en planta)."""
    n = len(first)
    tip = math.dist(first[0], second[0]) < 1e-12
    plan: list[Vec2] = []
    idx_f, idx_s = [], []
    for k in range(n):
        idx_f.append(len(plan))
        plan.append(first[k])
        if k == 0 and tip:
            idx_s.append(idx_f[0])
        elif k > 0 and math.dist(second[k], second[k - 1]) < 1e-12:
            idx_s.append(idx_s[k - 1])  # punto repetido (franja recortada en una esquina): triángulo
        else:
            idx_s.append(len(plan))
            plan.append(second[k])
    top = [(x, y, z_top(x, y)) for x, y in plan]
    nv = len(plan)
    verts = top + [(x, y, z - depth) for x, y, z in top]
    faces, parts = [], []

    def add(face, part):
        face = tuple(dict.fromkeys(face))  # quita vértices repetidos (punta)
        if len(face) >= 3:
            faces.append(tuple(reversed(face)) if flip else face)
            parts.append(part)

    for k in range(n - 1):
        q = (idx_f[k], idx_f[k + 1], idx_s[k + 1], idx_s[k])
        add(q, PART_EXTRADOS)                                   # cara superior (contra el intradós)
        add(tuple(nv + v for v in reversed(q)), PART_INTRADOS)  # fondo
        a, b = idx_f[k], idx_f[k + 1]
        add((nv + a, nv + b, b, a), inner_part)                 # lado de `first`
        a, b = idx_s[k + 1], idx_s[k]
        add((nv + a, nv + b, b, a), outer_parts[k])             # lado de `second`
    a, b = idx_f[-1], idx_s[-1]
    add((nv + a, nv + b, b, a), end_part)
    if not tip:
        a, b = idx_s[0], idx_f[0]
        add((nv + a, nv + b, b, a), start_part)
    boundary = [first[k] for k in range(n)] + [second[k] for k in range(n - 1, -1 if not tip else 0, -1)]
    return MeshData(verts, faces, parts, plan + plan, [], (0, 0)), abs(_polygon_area(boundary))


# --- utilidades -------------------------------------------------------------------


def _mid(a: Vec2, b: Vec2) -> Vec2:
    return (0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1]))


def _lerp2(a: Vec2, b: Vec2, t: float) -> Vec2:
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _quad_area(q: tuple[Vec2, Vec2, Vec2, Vec2]) -> float:
    return 0.5 * sum(q[i][0] * q[(i + 1) % 4][1] - q[(i + 1) % 4][0] * q[i][1] for i in range(4))


def _box(x: float, y: float, z0: float, z1: float, h: float) -> MeshData:
    v = [(x - h, y - h, z0), (x + h, y - h, z0), (x + h, y + h, z0), (x - h, y + h, z0),
         (x - h, y - h, z1), (x + h, y - h, z1), (x + h, y + h, z1), (x - h, y + h, z1)]
    f = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return MeshData(v, f, [PART_EDGE] * 6, [(x, y)] * 8, [], (0, 0))


def _pyramid(x: float, y: float, z: float, h: float, height: float) -> MeshData:
    zb = z - height
    v = [(x - h, y - h, zb), (x + h, y - h, zb), (x + h, y + h, zb), (x - h, y + h, zb), (x, y, z)]
    f = [(3, 2, 1, 0), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    return MeshData(v, f, [PART_EDGE] * 5, [(x, y)] * 5, [], (0, 0))
