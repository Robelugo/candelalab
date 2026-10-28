"""Despiece de cimbra a partir de las generatrices (sin bpy).

Candela cimbraba sus hypars con madera recta: el intradós contiene dos familias
de rectas, así que se arma con piezas rectas.

* Tablas (duela) a lo largo de una familia, una junto a otra, separadas en
  planta su propio ancho.
* Vigas (largueros) a lo largo de la otra familia, bajo las tablas.
* Puntales verticales bajo las vigas, hasta el nivel de desplante.

Se calcula geometría exacta de las generatrices:

* Largo del eje de cada pieza y largo de corte. El largo de corte es la arista
  más larga, con los extremos cortados contra el borde o la lima.
* Esviaje de cada extremo: ángulo del corte en el plano tangente (0° = a
  escuadra). Dos generatrices de una hypar no se cortan a 90° aunque en planta
  lo parezca.
* Alabeo de cada pieza. La hypar no es desarrollable: su plano tangente gira
  alrededor de cada generatriz un ángulo φ entre los extremos. Una tabla de
  ancho b tiene que torcerse, y una esquina queda b·sen φ fuera del plano de
  las otras tres.
* Holgura entre tablas contiguas. Las generatrices de una familia son
  paralelas en planta pero se cruzan en el espacio. Separadas en planta el
  ancho b, la distancia 3D entre ejes es ≥ b (proyectar en planta no alarga
  distancias). Por eso nunca se solapan, y la holgura máxima indica si hacen
  falta tablas ahusadas o cuñas.

Las piezas se agrupan en tipos: largo redondeado a la tolerancia y esviajes al
grado. Una tabla girada o volteada es la misma pieza. También se calcula el
corte de piezas de largo comercial (mejor ajuste decreciente).

Secciones, separaciones y largos los fija el usuario. Nada de esto verifica la
resistencia ni la rigidez de la cimbra.
"""

from __future__ import annotations

import math
from bisect import bisect_left, insort
from dataclasses import dataclass

from .geometry import Vec2, Vec3
from .meshing import MeshData
from .model import INTRADOS, ShellModel, miter_direction
from .rulings import FAMILY_NAMES, _max_distance_to_chord, family_lines

BOARD = "BOARD"
JOIST = "JOIST"
KIND_NAMES = {BOARD: "Tabla", JOIST: "Viga"}
PROP_STEP = 0.05                          # puntales cortados de 5 en 5 cm (el resto, con cuñas)
MIN_PIECE = 0.05                          # recortes más cortos (esquinas) no se listan
MAX_PIECES = 20_000                       # por familia y parche: protege la regeneración interactiva
BOARD_FOOT = 0.3048 * 0.3048 * 0.0254     # pie-tablón en m³
SAMPLES = 9

# Partes de la malla de visualización (índice de material en Blender)
PART_BOARD, PART_JOIST, PART_PROP = 0, 1, 2
DRAW_GAP = 0.001  # hueco de dibujo entre piezas en contacto: evita el parpadeo de caras coincidentes


@dataclass
class Piece:
    kind: str
    patch: int
    family: int
    index: int                      # posición en la familia: piezas contiguas tienen índices consecutivos
    start: Vec3
    end: Vec3
    normals: tuple[Vec3, Vec3]      # normal de la superficie en cada extremo
    up: Vec3                        # normal en el centro, perpendicular al eje (orientación de la sección)
    span: tuple[float, float]       # coordenada en planta a lo largo de la familia (común a la familia)
    length: float                   # largo del eje
    cut_length: float               # arista más larga: largo a cortar
    skew: tuple[float, float]       # esviaje de cada extremo (°), 0 = a escuadra
    twist: float                    # giro del plano tangente entre extremos (°)
    deviation: float                # separación máx. entre la pieza recta y la superficie
    type: str = ""
    id: str = ""


@dataclass
class Prop:
    top: Vec3
    length: float                   # altura libre bajo la viga
    cut_length: float               # largo cortado (múltiplo de PROP_STEP; el resto, cuñas)
    joist: int
    type: str = ""
    id: str = ""


@dataclass
class Cutlist:
    boards: list[Piece]
    joists: list[Piece]
    props: list[Prop]
    summary: dict


def compute_cutlist(model: ShellModel, p: dict) -> Cutlist:
    b, e = float(p["formwork_board_width"]), float(p["formwork_board_thickness"])
    sj, bj, hj = (float(p[k]) for k in ("formwork_joist_spacing", "formwork_joist_width", "formwork_joist_depth"))
    sp, stock, tol = (float(p[k]) for k in ("formwork_prop_spacing", "formwork_stock_length", "formwork_tolerance"))
    if min(b, sj, bj, hj, tol) <= 0.0 or min(e, sp, stock) < 0.0:
        raise ValueError("Despiece de cimbra: anchos, separaciones, secciones y tolerancia deben ser positivos.")
    fb = FAMILY_NAMES.index(p["formwork_board_family"])
    th = model.thickness
    s0, mode, exact = th.offset_of(INTRADOS), th.mode, th.is_exact_layer(INTRADOS)

    boards: list[Piece] = []
    joists: list[Piece] = []
    gap_lo, gap_hi = math.inf, -math.inf
    seen = set()
    for ip, patch in enumerate(model.patches):
        dirs = patch.surface.asymptotic_directions()
        if dirs is None:
            continue
        # Tablas: ejes a (k + ½)·b de la recta que pasa por el origen del modelo.
        _, lines = family_lines(patch.domain, dirs[fb], b, (0.0, 0.0), 0.5, limit=MAX_PIECES)
        row = [pc for pc in (_piece(model, ip, BOARD, fb, k, o, dirs[fb], t0, t1, b, s0, mode, exact)
                             for k, o, t0, t1 in lines) if pc is not None]
        boards += row
        for lo, hi in _gaps(row, b):
            gap_lo, gap_hi = min(gap_lo, lo), max(gap_hi, hi)
        # Vigas: la otra familia, ancladas en el origen (una cae sobre cada lima que pasa por él).
        _, lines = family_lines(patch.domain, dirs[1 - fb], sj, (0.0, 0.0), 0.0, limit=MAX_PIECES)
        for k, o, t0, t1 in lines:
            ends = tuple(sorted((round(o[0] + t * dirs[1 - fb][0], 6), round(o[1] + t * dirs[1 - fb][1], 6))
                                for t in (t0, t1)))
            if ends in seen:  # viga sobre una lima compartida: una sola pieza
                continue
            seen.add(ends)
            pc = _piece(model, ip, JOIST, 1 - fb, k, o, dirs[1 - fb], t0, t1, bj, s0, mode, exact, drop=e)
            if pc is not None:
                joists.append(pc)

    props, skipped = _props(joists, sp, hj, float(p["ground_level"]))
    board_types = _assign_types(boards, tol, "T")
    joist_types = _assign_types(joists, tol, "V")
    prop_types = _assign_prop_types(props)

    def family(pieces, types, width, depth):
        cut = [_ceil_to(pc.cut_length, tol) for pc in pieces]
        twist = max((pc.twist for pc in pieces), default=0.0)
        return {
            "family": FAMILY_NAMES[pieces[0].family] if pieces else None,
            "count": len(pieces),
            "length_total": sum(pc.length for pc in pieces),
            "cut_length_total": sum(cut),
            "max_length": max((pc.length for pc in pieces), default=0.0),
            "volume": width * depth * sum(pc.length for pc in pieces),
            "twist_max_deg": twist,
            "twist_rate_max_deg_m": max((pc.twist / pc.length for pc in pieces), default=0.0),
            "skew_max_deg": max((abs(s) for pc in pieces for s in pc.skew), default=0.0),
            "deviation_max": max((pc.deviation for pc in pieces), default=0.0),
            "types": types,
            "stock": stock_plan(cut, stock) if stock > 0.0 else None,
        }

    bo = family(boards, board_types, b, e)
    bo.update(width=b, thickness=e, corner_lift_max=b * math.sin(math.radians(bo["twist_max_deg"])),
              gap_max=gap_hi if boards and gap_hi > -math.inf else 0.0,
              gap_min=gap_lo if boards and gap_lo < math.inf else 0.0)
    jo = family(joists, joist_types, bj, hj)
    jo.update(spacing=sj, width=bj, depth=hj)
    pr = {
        "count": len(props),
        "spacing": sp,
        "step": PROP_STEP,
        "length_total": sum(q.length for q in props),
        "cut_length_total": sum(q.cut_length for q in props),
        "min": min((q.length for q in props), default=0.0),
        "max": max((q.length for q in props), default=0.0),
        "skipped": skipped,
        "types": prop_types,
    }
    wood = bo["volume"] + jo["volume"]
    summary = {"boards": bo, "joists": jo, "props": pr, "wood_volume": wood, "board_feet": wood / BOARD_FOOT,
               "exact": exact, "tolerance": tol, "uses": 1}
    return Cutlist(boards, joists, props, summary)


# --- piezas --------------------------------------------------------------------------


def _piece(model, ip, kind, fam, k, origin, d, t0, t1, width, s0, mode, exact, drop=0.0) -> Piece | None:
    """Pieza recta sobre la generatriz origin + t·d, t ∈ [t0, t1], en el intradós.

    Las vigas bajan `drop` (el espesor de tabla) según la normal; sobre una lima,
    según el inglete de los dos parches.
    """
    patch = model.patches[ip]
    surf, dom = patch.surface, patch.domain
    n_samples = 2 if exact and drop == 0.0 else SAMPLES
    ts = [t0 + (t1 - t0) * i / (n_samples - 1) for i in range(n_samples)]
    plan = [(origin[0] + t * d[0], origin[1] + t * d[1]) for t in ts]
    pts = [patch.point(x, y, s0, mode) for x, y in plan]
    mid = (origin[0] + 0.5 * (t0 + t1) * d[0], origin[1] + 0.5 * (t0 + t1) * d[1])
    share = _incident(model, ip, mid)

    def down(xy):
        return miter_direction([model.patches[q].surface.unit_normal(*xy) for q in share])

    if drop:
        pts = [_sub(q, _scale(down(xy), drop)) for q, xy in zip(pts, plan)]
    start, end = pts[0], pts[-1]
    length = math.dist(start, end)
    if length < MIN_PIECE:
        return None
    axis = _unit(_sub(end, start))
    n0, n1 = surf.unit_normal(*plan[0]), surf.unit_normal(*plan[-1])
    nm = _unit(down(mid))
    up = _unit(_sub(nm, _scale(axis, _dot(nm, axis))))
    tol = 1e-7 * max(1.0, dom.size)
    skew = (_skew(dom, surf, plan[0], d, axis, n0, tol), _skew(dom, surf, plan[-1], d, axis, n1, tol))

    # Largo de corte: la arista más larga. Las aristas son las rectas paralelas a ±ancho/2 en
    # planta, recortadas por el mismo contorno (cota superior de la arista real).
    cut = length
    for side in (-0.5, 0.5):
        o = (origin[0] - side * width * d[1], origin[1] + side * width * d[0])
        seg = dom.clip_line(o, d)
        if seg is not None:
            q0 = patch.point(o[0] + seg[0] * d[0], o[1] + seg[0] * d[1], s0, mode)
            q1 = patch.point(o[0] + seg[1] * d[0], o[1] + seg[1] * d[1], s0, mode)
            cut = max(cut, math.dist(q0, q1))
    twist = math.degrees(math.acos(max(-1.0, min(1.0, _dot(n0, n1)))))
    dev = 0.0 if len(pts) == 2 else _max_distance_to_chord(tuple(pts))
    return Piece(kind, ip, fam, k, start, end, (n0, n1), up, (t0, t1), length, cut, skew, twist, dev)


def _incident(model, ip: int, xy: Vec2) -> list[int]:
    """Parches que contienen el punto (dos si está sobre una lima)."""
    out = {ip}
    for q, patch in enumerate(model.patches):
        if q != ip and patch.domain.contains(xy, 1e-7 * max(1.0, patch.domain.size)):
            out.add(q)
    return sorted(out)


def _skew(dom, surf, xy: Vec2, d: Vec2, axis: Vec3, n: Vec3, tol: float) -> float:
    """Esviaje (°) del corte de un extremo, en el plano tangente: ángulo entre el
    borde donde termina la pieza y la perpendicular a su eje (0° = a escuadra)."""
    best, best_sin = None, 1e-9
    for t in dom.boundary_tangents(xy, tol):
        s = abs(d[0] * t[1] - d[1] * t[0])
        if s > best_sin:  # en una esquina, el borde menos paralelo a la pieza
            best, best_sin = t, s
    if best is None:
        return 0.0
    gx, gy = surf.gradient(*xy)
    c = (best[0], best[1], gx * best[0] + gy * best[1])  # tangente 3D del borde
    w = _cross(n, axis)
    theta = math.degrees(math.atan2(_dot(axis, c), _dot(w, c)))
    if theta > 90.0:
        theta -= 180.0
    elif theta <= -90.0:
        theta += 180.0
    return theta


def _gaps(row: list[Piece], b: float):
    """(holgura mínima, máxima) entre cada tabla y la siguiente, donde coexisten.

    La distancia de un punto de un eje al otro eje es la raíz de una cuadrática
    convexa en el parámetro: el máximo está en un extremo y el mínimo, en el
    vértice o en un extremo. Ambos son exactos."""
    by_k = {pc.index: pc for pc in row}
    for k, pc in by_k.items():
        nb = by_k.get(k + 1)
        if nb is None:
            continue
        lo, hi = max(pc.span[0], nb.span[0]), min(pc.span[1], nb.span[1])
        if hi - lo <= 1e-9:
            continue
        u = _unit(_sub(nb.end, nb.start))
        v = _sub(pc.end, pc.start)
        w0 = _sub(pc.start, nb.start)
        vp = _sub(v, _scale(u, _dot(v, u)))
        wp = _sub(w0, _scale(u, _dot(w0, u)))
        f0, f1 = ((t - pc.span[0]) / (pc.span[1] - pc.span[0]) for t in (lo, hi))
        fs = [f0, f1]
        vv = _dot(vp, vp)
        if vv > 0.0 and f0 < -_dot(wp, vp) / vv < f1:
            fs.append(-_dot(wp, vp) / vv)
        dists = [_norm(_add(wp, _scale(vp, f))) for f in fs]
        yield min(dists) - b, max(dists) - b


def _props(joists: list[Piece], spacing: float, depth: float, ground: float) -> tuple[list[Prop], int]:
    """Puntales repartidos a lo largo de cada viga (separación ≤ spacing en planta)."""
    props, skipped = [], 0
    if spacing <= 0.0:
        return props, skipped
    for j, pc in enumerate(joists):
        n = max(1, math.ceil((pc.span[1] - pc.span[0]) / spacing - 1e-9))
        for i in range(n):
            f = (i + 0.5) / n
            top = _sub(_lerp(pc.start, pc.end, f), _scale(pc.up, depth))
            h = top[2] - ground
            cut = math.floor(h / PROP_STEP + 1e-9) * PROP_STEP
            if cut < PROP_STEP:  # no cabe un puntal entre la viga y el desplante
                skipped += 1
                continue
            props.append(Prop(top, h, cut, j))
    return props, skipped


# --- tipos y corte comercial ---------------------------------------------------------------


def _ceil_to(x: float, step: float) -> float:
    return math.ceil(x / step - 1e-9) * step


def _canonical_skew(skew) -> tuple[int, int]:
    """Esviajes al grado, iguales para una pieza girada 180° (extremos intercambiados)
    o volteada (signos cambiados): es la misma pieza."""
    def norm(r):
        return 90 if r == -90 else r

    r0, r1 = (int(round(s)) for s in skew)
    return min(tuple(norm(r) for r in v) for v in ((r0, r1), (r1, r0), (-r0, -r1), (-r1, -r0)))


def _assign_types(pieces: list[Piece], tol: float, prefix: str) -> list[dict]:
    groups: dict = {}
    for pc in pieces:
        groups.setdefault((round(_ceil_to(pc.length, tol), 9), _canonical_skew(pc.skew)), []).append(pc)
    types = []
    for i, key in enumerate(sorted(groups, key=lambda k: (-k[0], k[1])), 1):
        name = f"{prefix}{i:02d}"
        members = groups[key]
        for j, pc in enumerate(members, 1):
            pc.type, pc.id = name, f"{name}-{j}"
        types.append({"type": name, "count": len(members), "length": key[0],
                      "cut_length": round(max(_ceil_to(pc.cut_length, tol) for pc in members), 9),
                      "skew": list(key[1])})
    return types


def _assign_prop_types(props: list[Prop]) -> list[dict]:
    groups: dict = {}
    for q in props:
        groups.setdefault(round(q.cut_length, 6), []).append(q)
    types = []
    for i, length in enumerate(sorted(groups, reverse=True), 1):
        name = f"P{i:02d}"
        for j, q in enumerate(groups[length], 1):
            q.type, q.id = name, f"{name}-{j}"
        types.append({"type": name, "count": len(groups[length]), "length": length})
    return types


def stock_plan(lengths: list[float], stock: float) -> dict:
    """Piezas comerciales de largo `stock` para cortar `lengths` (mejor ajuste decreciente).

    Una pieza más larga que el largo comercial se arma con piezas enteras más el
    resto: cada unión es un empalme.
    """
    eps = 1e-9
    free: list[float] = []  # sobrantes de las piezas abiertas, ordenados
    count = splices = over = 0
    for length in sorted(lengths, reverse=True):
        if length > stock + eps:
            over += 1
        while length > stock + eps:
            count += 1
            splices += 1
            length -= stock
        i = bisect_left(free, length - eps)
        if i < len(free):
            rest = free.pop(i) - length
        else:
            count += 1
            rest = stock - length
        insort(free, max(0.0, rest))
    total = sum(lengths)
    bought = count * stock
    return {"length": stock, "count": count, "waste": bought - total,
            "waste_pct": 100.0 * (bought - total) / bought if bought else 0.0,
            "splices": splices, "pieces_over": over}


# --- malla de visualización ---------------------------------------------------------------------


def formwork_mesh(cl: Cutlist, p: dict) -> MeshData:
    """Tablas (torcidas como la superficie), vigas rectas y puntales verticales."""
    b, e = float(p["formwork_board_width"]), float(p["formwork_board_thickness"])
    bj, hj = float(p["formwork_joist_width"]), float(p["formwork_joist_depth"])
    md = MeshData()
    for pc in cl.boards:
        md.extend(_sweep([_section(q, n, _cross(n, axis), b, max(e, 1e-3)) for q, n, axis in _board_frames(pc)],
                         PART_BOARD))
    for pc in cl.joists:
        axis = _unit(_sub(pc.end, pc.start))
        md.extend(_sweep([_section(_sub(q, _scale(pc.up, DRAW_GAP)), pc.up, _cross(pc.up, axis), bj, hj - DRAW_GAP)
                          for q in (pc.start, pc.end)], PART_JOIST))
    h = 0.5 * bj
    for q in cl.props:
        x, y, z = q.top
        md.extend(_sweep([[(x - h, y - h, zz), (x + h, y - h, zz), (x + h, y + h, zz), (x - h, y + h, zz)]
                          for zz in (z - q.length, z - DRAW_GAP)], PART_PROP))
    return md


def _board_frames(pc: Piece, max_step_deg: float = 3.0, max_segments: int = 32):
    """Secciones de una tabla a lo largo de su generatriz, con la normal exacta.

    En z = f(x, y) cuadrática, ∇f es lineal a lo largo de una recta, así que la
    normal escalada a n_z = 1, (−f_x, −f_y, 1), se interpola linealmente sin
    error. Basta subdividir para que cada tramo gire ≤ max_step_deg y la cara de
    la tabla siga la torsión."""
    axis = _unit(_sub(pc.end, pc.start))
    m = max(1, min(max_segments, math.ceil(pc.twist / max_step_deg)))
    n0, n1 = (_scale(n, 1.0 / n[2]) for n in pc.normals)
    for i in range(m + 1):
        f = i / m
        yield _lerp(pc.start, pc.end, f), _unit(_lerp(n0, n1, f)), axis


def _section(q: Vec3, up: Vec3, side: Vec3, width: float, depth: float) -> list[Vec3]:
    """Rectángulo de ancho `width` centrado en q y de canto `depth` por debajo (según −up)."""
    w = _scale(_unit(side), 0.5 * width)
    down = _scale(up, -depth)
    return [_sub(q, w), _add(q, w), _add(_add(q, w), down), _add(_sub(q, w), down)]


def _sweep(sections: list[list[Vec3]], part: int) -> MeshData:
    """Sólido cerrado entre secciones consecutivas de 4 vértices, normales hacia afuera.

    Las caras laterales de un tramo torcido no son planas: el primer vértice de
    cada cara (la diagonal al triangular) alterna de un tramo a otro para que
    los errores de volumen se compensen."""
    verts = [v for sec in sections for v in sec]
    m = len(sections) - 1
    faces = [(0, 1, 2, 3), tuple(4 * m + i for i in (3, 2, 1, 0))]
    for s in range(m):
        a, c = 4 * s, 4 * (s + 1)
        for i in range(4):
            j = (i + 1) % 4
            faces.append((a + i, c + i, c + j, a + j) if s % 2 == 0 else (c + i, c + j, a + j, a + i))
    vol = 0.0
    for f in faces:
        o = verts[f[0]]
        for i in range(1, len(f) - 1):
            vol += _dot(o, _cross(verts[f[i]], verts[f[i + 1]]))
    if vol < 0.0:
        faces = [tuple(reversed(f)) for f in faces]
    return MeshData(verts, faces, [part] * len(faces), [(v[0], v[1]) for v in verts], [], (0, 0))


# --- exportación ---------------------------------------------------------------------------------


def cutlist_document(cl: Cutlist) -> dict:
    """Despiece completo (resumen, tipos y cada pieza) para el JSON."""
    def piece(pc: Piece) -> dict:
        return {"id": pc.id, "type": pc.type, "kind": pc.kind, "patch": pc.patch, "family": FAMILY_NAMES[pc.family],
                "start": list(pc.start), "end": list(pc.end), "length": pc.length, "cut_length": pc.cut_length,
                "skew_deg": list(pc.skew), "twist_deg": pc.twist, "deviation": pc.deviation}

    return {**cl.summary,
            "pieces": [piece(pc) for pc in cl.boards + cl.joists],
            "props_list": [{"id": q.id, "type": q.type, "top": list(q.top), "length": q.length,
                            "cut_length": q.cut_length, "joist": cl.joists[q.joist].id} for q in cl.props]}


# --- vectores ------------------------------------------------------------------------------------


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def _lerp(a, b, f):
    return (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]))
