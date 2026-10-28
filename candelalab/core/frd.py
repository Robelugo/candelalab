"""Lectura de resultados de CalculiX (.frd y .dat) sobre el cascarón (sin bpy).

El .frd ASCII es texto de ancho fijo (manual de CalculiX GraphiX, «Result
Format»):
* bloque «2C»: nodos, «-1» + número (I5 corto / I10 largo) + 3 × E12.5;
* bloque «3C»: elementos (se ignoran: la conectividad es la del modelo exportado);
* «1PSTEP» abre la salida de un paso (su último campo es el número de paso),
  «1PMODE» la de un modo propio;
* «100C» abre un conjunto de resultados (su valor: tiempo o factor del modo),
  «-4» lo nombra (DISP, STRESS, FORC...), «-5» define cada componente y las
  líneas «-1»/«-2» llevan nodo y valores de 12 caracteres, que pueden ir
  pegados («1-1.23450E-03»): se cortan por columnas, nunca por espacios.

Láminas (parámetro OUTPUT del primer *NODE FILE / *EL FILE):
* 3D, el valor por defecto de CalculiX: escribe los nodos de la lámina
  expandida, con números nuevos, en las caras superior e inferior. Cada
  vértice del cascarón (intradós y extradós) toma el nodo expandido más
  cercano, así que se ve la flexión en cada cara;
* 2D: resultados en los nodos originales, promediados en el espesor (sin
  flexión). Se asocian por número y se comprueban las coordenadas.

Esfuerzos por unidad de longitud (N, M)
---------------------------------------
Con salida 3D se conocen las tensiones de las dos caras en cada punto de la
lámina. En ejes locales del plano tangente (x = proyección del eje X global,
y = n × x) y con una distribución lineal en el espesor t (también vale para
una cuadrática en M):
    N = t·(σ_ext + σ_int)/2          fuerza de membrana por metro (tracción +)
    M = t²/12·(σ_ext − σ_int)        momento por metro; M > 0 tracciona el extradós
N₁, N₂ y M₁, M₂ son sus valores principales (independientes de los ejes). Con
salida 2D CalculiX promedia en el espesor: hay N, pero no M.

Factores de pandeo: del .dat, donde CalculiX siempre los escribe, o, sin
.dat, del valor de cada modo en el .frd. Las reacciones totales del .dat
(*NODE PRINT, TOTALS=ONLY) se cotejan con los totales de carga del modelo.

Leer resultados no valida el modelo ni el diseño.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, field

from .colormaps import diverging, sequential
from .geometry import Vec3

FLOAT = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[EeDd][-+]?\d+)?"

# Parámetros que no cambian el modelo FE (sólo visualización, cimbra o colocación en la retícula).
INDEPENDENT_PREFIXES = ("show_", "ruling_", "normal_", "formwork_", "grid_")
INDEPENDENT_KEYS = {"analysis", "pour_slope_limit", "output"}

QUANTITIES = {
    "DISP": ("Desplazamiento |U|", "mm", False),
    "DISP_Z": ("Desplazamiento vertical Uz", "mm", True),
    "VON_MISES": ("Tensión de von Mises", "MPa", False),
    "S_MAX": ("Tensión principal máxima (tracción +)", "MPa", True),
    "S_MIN": ("Tensión principal mínima (compresión −)", "MPa", True),
    "N_MAX": ("Membrana N₁ (principal máxima)", "kN/m", True),
    "N_MIN": ("Membrana N₂ (principal mínima)", "kN/m", True),
    "N_X": ("Membrana Nx", "kN/m", True),
    "N_Y": ("Membrana Ny", "kN/m", True),
    "N_XY": ("Membrana Nxy (cortante)", "kN/m", True),
    "M_MAX": ("Momento M₁ (principal máximo)", "kN·m/m", True),
    "M_MIN": ("Momento M₂ (principal mínimo)", "kN·m/m", True),
    "M_X": ("Momento Mx", "kN·m/m", True),
    "M_Y": ("Momento My", "kN·m/m", True),
    "M_XY": ("Momento torsor Mxy", "kN·m/m", True),
}
SECTION = {"N_MAX": 0, "N_MIN": 1, "N_X": 2, "N_Y": 3, "N_XY": 4,
           "M_MAX": 5, "M_MIN": 6, "M_X": 7, "M_Y": 8, "M_XY": 9}


def results_key(params: dict) -> str:
    """Huella de los parámetros de los que dependen los resultados."""
    kept = {k: v for k, v in params.items()
            if k not in INDEPENDENT_KEYS and not k.startswith(INDEPENDENT_PREFIXES)}
    return hashlib.sha1(json.dumps(kept, sort_keys=True, default=str).encode()).hexdigest()


# --- lectura ------------------------------------------------------------------------------------


@dataclass
class Dataset:
    step: int
    mode: int | None
    value: float
    name: str
    components: list[str]
    data: dict[int, list[float]] = field(default_factory=dict)


@dataclass
class Frd:
    nodes: dict[int, Vec3]
    datasets: list[Dataset]
    has_steps: bool


def _format(header: str) -> int:
    last = header.split()[-1]
    fmt = int(last) if last.isdigit() else 1
    if fmt == 2:
        raise ValueError("El .frd está en formato binario: este lector sólo lee el formato ASCII "
                         "(el predeterminado de CalculiX).")
    return fmt


def _record(line: str, fmt: int) -> tuple[int | None, list[float]]:
    """Número de nodo (None en continuaciones «-2») y valores de 12 caracteres."""
    width = 10 if fmt == 1 else 5
    head, rest = line[3:3 + width], line[3 + width:].rstrip()
    node = int(head) if head.strip() else None
    try:
        vals = [float(rest[k:k + 12]) for k in range(0, len(rest), 12)]
    except ValueError:  # columnas inesperadas: números separados por espacios
        vals = [float(x.replace("D", "E")) for x in re.findall(FLOAT, rest)]
    return node, vals


def parse_frd(text: str) -> Frd:
    lines = text.splitlines()
    nodes: dict[int, Vec3] = {}
    datasets: list[Dataset] = []
    step, mode, has_steps = 0, None, False
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        i += 1
        if not s:
            continue
        if s.startswith("9999"):
            break
        if s.startswith("1PSTEP"):
            step, mode, has_steps = int(s.split()[-1]), None, True
        elif s.startswith("1PMODE"):
            mode = int(s.split()[-1])
        elif s.startswith("2C"):
            fmt = _format(s)
            while i < n and not lines[i].strip().startswith("-3"):
                if lines[i].strip().startswith("-1"):
                    node, vals = _record(lines[i], fmt)
                    nodes[node] = (vals[0], vals[1], vals[2])
                i += 1
            i += 1
        elif s.startswith("3C"):
            while i < n and not lines[i].strip().startswith("-3"):
                i += 1
            i += 1
        elif s.startswith("100C"):
            fmt = _format(s)
            try:
                value = float(lines[i - 1][12:24])  # (1X,' 100','C',6A1,E12.5,...)
            except ValueError:
                value = float(s.split()[2])
            ds = Dataset(step, mode, value, "", [])
            last = None
            while i < n and not lines[i].strip().startswith("-3"):
                line = lines[i]
                tag = line.strip()[:2]
                if tag == "-4":
                    ds.name = line.split()[1]
                elif tag == "-5":
                    parts = line.split()
                    if not (len(parts) > 6 and parts[6].startswith("1")):  # IEXIST = 1: calculada, sin datos
                        ds.components.append(parts[1])
                elif tag == "-1":
                    last, vals = _record(line, fmt)
                    ds.data[last] = vals
                elif tag == "-2" and last is not None:
                    ds.data[last] += _record(line, fmt)[1]
                i += 1
            i += 1
            datasets.append(ds)
    if not nodes:
        raise ValueError("El archivo no tiene bloque de nodos (2C): no parece un .frd de CalculiX.")
    return Frd(nodes, datasets, has_steps)


def parse_dat(text: str) -> dict:
    """Factores de pandeo (una lista por paso *BUCKLE) y reacciones totales de NAPOYOS (una por paso)."""
    lines = text.splitlines()
    buckling, totals = [], []
    i = 0
    while i < len(lines):
        compact = lines[i].replace(" ", "").upper()
        if "BUCKLINGFACTOROUTPUT" in compact:
            factors, i = [], i + 1
            while i < len(lines):
                m = re.match(rf"^\s*(\d+)\s+({FLOAT})\s*$", lines[i])
                if m:
                    factors.append(float(m.group(2).replace("D", "E")))
                elif factors and lines[i].strip():
                    break
                i += 1
            buckling.append(factors)
            continue
        low = lines[i].lower()
        if "total force" in low and "napoyos" in low:
            for j in range(i + 1, min(i + 6, len(lines))):
                nums = re.findall(FLOAT, lines[j])
                if len(nums) >= 3:
                    totals.append([float(x.replace("D", "E")) for x in nums[:3]])
                    break
        i += 1
    return {"buckling": buckling, "totals": totals}


# --- resultados sobre el cascarón -------------------------------------------------------------


@dataclass
class ResultItem:
    step: str
    title: str
    kind: str                         # STATIC / BUCKLE
    mode: int | None
    factor: float | None
    factor_source: str | None
    disp: dict[int, list[float]]
    stress: dict[int, list[float]] | None


@dataclass
class FeaResults:
    output: str                       # "2D" o "3D" (detectada)
    vertex_nodes: list[int | None]    # nodo del .frd de cada vértice del cascarón (módulo)
    items: list[ResultItem]
    summary: dict
    vertex_fe: list[int | None] = field(default_factory=list)            # nodo de lámina del modelo FE
    faces: dict[int, tuple[int, int]] = field(default_factory=dict)      # nodo FE -> nodos .frd (extradós, intradós)
    normals: dict[int, Vec3] = field(default_factory=dict)
    thickness: dict[int, float] = field(default_factory=dict)
    points: dict[int, Vec3] = field(default_factory=dict)               # nodo FE -> posición (superficie media)

    def labels(self) -> list[str]:
        return [_label(it) for it in self.items]

    def displacement(self, item: int) -> list[Vec3 | None]:
        """Desplazamiento de cada vértice (m; en un modo de pandeo, normalizado a máx. 1)."""
        it = self.items[item]
        raw = [it.disp.get(v) if v is not None else None for v in self.vertex_nodes]
        out = [tuple(d[:3]) if d is not None else None for d in raw]
        if it.kind == "BUCKLE":
            peak = max((math.sqrt(sum(c * c for c in d)) for d in out if d), default=0.0) or 1.0
            out = [tuple(c / peak for c in d) if d else None for d in out]
        return out

    def values(self, item: int, quantity: str) -> list[float | None]:
        """Valor de cada vértice en unidades de presentación (mm, MPa; sin unidad en pandeo)."""
        it = self.items[item]
        if quantity in ("DISP", "DISP_Z"):
            scale = 1.0 if it.kind == "BUCKLE" else 1000.0
            disp = self.displacement(item)
            if quantity == "DISP":
                return [scale * math.sqrt(sum(c * c for c in d)) if d else None for d in disp]
            return [scale * d[2] if d else None for d in disp]
        if it.stress is None or (quantity.startswith("M_") and self.output == "2D"):
            return [None] * len(self.vertex_nodes)
        if quantity in SECTION:
            k = SECTION[quantity]
            forces = self._section(item)
            return [forces[v][k] if v is not None and forces.get(v) is not None else None for v in self.vertex_fe]
        fn = {"VON_MISES": von_mises, "S_MAX": lambda s: principal(s)[0], "S_MIN": lambda s: principal(s)[1]}[quantity]
        out = []
        for v in self.vertex_nodes:
            s = it.stress.get(v) if v is not None else None
            out.append(fn(s) / 1e6 if s is not None and len(s) >= 6 else None)
        return out


    def missing_reason(self, item: int, quantity: str) -> str:
        it = self.items[item]
        if it.kind == "BUCKLE" and quantity not in ("DISP", "DISP_Z"):
            return "Los modos de pandeo sólo traen desplazamientos."
        if quantity.startswith("M_") and self.output == "2D":
            return ("Con salida 2D CalculiX promedia en el espesor y no hay momentos: exporta con salida 3D "
                    "(caras) y vuelve a calcular.")
        return "Este paso no trae tensiones."

    def _section(self, item: int) -> dict:
        """N y M en cada nodo de lámina (kN/m, kN·m/m), en el orden de SECTION."""
        cache = self.__dict__.setdefault("_section_cache", {})
        if item not in cache:
            stress = self.items[item].stress or {}
            out = {}
            for v, (top, bottom) in self.faces.items():
                st, sb = stress.get(top), stress.get(bottom)
                if st is None or sb is None or len(st) < 6 or len(sb) < 6:
                    out[v] = None
                    continue
                n, m = section_forces(st, sb, self.normals[v], self.thickness[v])
                out[v] = tuple(x / 1000.0 for x in (*principal2(*n), *n, *principal2(*m), *m))
            cache[item] = out
        return cache[item]


def section_csv(res: FeaResults, item: int, decimal: str = ",") -> str:
    """N y M de cada nodo de la lámina para el resultado `item` (coma decimal y «;» por defecto)."""
    w = io.StringIO()
    out = csv.writer(w, delimiter=";" if decimal == "," else ",", lineterminator="\n")

    def num(v, digits=4):
        return "" if v is None else f"{v:.{digits}f}".replace(".", decimal)

    out.writerow(["CandelaLab · esfuerzos por unidad de longitud", res.labels()[item]])
    out.writerow(["Convención", "N en kN/m (tracción +); M en kN·m/m (M > 0 tracciona el extradós); ejes "
                  "locales x = proyección del X global sobre la lámina, y = n × x; de las tensiones de ambas caras "
                  "con distribución lineal en el espesor."])
    out.writerow(["Aviso", "Resultados de un análisis lineal con datos del usuario. Interpretarlos y verificar el "
                  "diseño corresponde a un ingeniero."])
    if res.output == "2D":
        out.writerow(["Salida 2D", "CalculiX promedió en el espesor: no hay momentos."])
    out.writerow([])
    out.writerow(["Nodo", "x", "y", "z", "t", "N1", "N2", "Nx", "Ny", "Nxy", "M1", "M2", "Mx", "My", "Mxy"])
    forces = res._section(item)
    for v in sorted(forces):
        f = forces[v]
        if f is None:
            continue
        p = res.points.get(v, (None, None, None))
        m = f[5:] if res.output == "3D" else (None,) * 5
        out.writerow([v + 1, *(num(c) for c in p), num(res.thickness[v]), *(num(x) for x in f[:5]),
                      *(num(x, 5) for x in m)])
    return w.getvalue()


def local_frame(n: Vec3) -> tuple[Vec3, Vec3]:
    """Ejes del plano tangente: x = proyección del X global (o del Y si la normal es casi X), y = n × x."""
    for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        d = sum(a * b for a, b in zip(axis, n))
        e = tuple(a - d * b for a, b in zip(axis, n))
        ln = math.sqrt(sum(c * c for c in e))
        if ln > 1e-6:
            e1 = tuple(c / ln for c in e)
            e2 = (n[1] * e1[2] - n[2] * e1[1], n[2] * e1[0] - n[0] * e1[2], n[0] * e1[1] - n[1] * e1[0])
            return e1, e2
    raise ValueError("Normal nula.")


def in_plane(s, e1: Vec3, e2: Vec3) -> tuple[float, float, float]:
    """(σ11, σ22, σ12) del tensor (SXX, SYY, SZZ, SXY, SYZ, SZX) en los ejes e1, e2."""
    xx, yy, zz, xy, yz, zx = s[:6]
    m = ((xx, xy, zx), (xy, yy, yz), (zx, yz, zz))

    def q(a, b):
        return sum(a[i] * m[i][j] * b[j] for i in range(3) for j in range(3))

    return q(e1, e1), q(e2, e2), q(e1, e2)


def section_forces(top, bottom, n: Vec3, t: float):
    """(N11, N22, N12) y (M11, M22, M12) por unidad de longitud (N/m, N·m/m) a partir de las
    tensiones del extradós (lado +n) y del intradós, con distribución lineal en el espesor."""
    e1, e2 = local_frame(n)
    a, b = in_plane(top, e1, e2), in_plane(bottom, e1, e2)
    return (tuple(0.5 * t * (x + y) for x, y in zip(a, b)),
            tuple(t * t / 12.0 * (x - y) for x, y in zip(a, b)))


def principal2(a: float, b: float, c: float) -> tuple[float, float]:
    """Valores principales (máximo, mínimo) del tensor plano [[a, c], [c, b]]."""
    mid, rad = 0.5 * (a + b), math.hypot(0.5 * (a - b), c)
    return mid + rad, mid - rad


def _label(it: ResultItem) -> str:
    if it.kind == "BUCKLE":
        f = f" · λ = {it.factor:.4g}" if it.factor is not None else ""
        return f"{it.step} · modo {it.mode}{f}"
    return f"{it.step} · {it.title}"


def load_results(frd: Frd, dat: dict | None, fe, display_plan, display_points, tolerance: float | None = None
                 ) -> FeaResults:
    """Asocia el .frd al modelo FE (pasos) y a los vértices del cascarón mostrado (módulo).

    `display_plan` y `display_points`: punto en planta de referencia y posición
    3D de cada vértice de la malla del cascarón en Blender.
    """
    from .fea import analysis_steps
    steps = analysis_steps(fe)
    checks = []
    t = fe.summary.get("thickness", 0.05)

    # 2D: los nodos originales conservan su número y sus coordenadas.
    size = max(1.0, max(abs(c) for p in fe.nodes for c in p))
    tol2d = 2e-5 * size
    matched = sum(1 for k, p in enumerate(fe.nodes, 1)
                  if k in frd.nodes and math.dist(frd.nodes[k], p) <= tol2d)
    if matched == len(fe.nodes):
        output = "2D"
        index = {_key(xy): v + 1 for v, xy in fe.plan.items()}
        vertex_nodes = [index.get(_key(xy)) for xy in display_plan]
        dist_max = 0.0
    else:
        output = "3D"
        tol = tolerance if tolerance is not None else 0.6 * t + 1e-6 * size
        vertex_nodes, dist_max = _nearest_nodes(frd.nodes, display_points, tol)
    missing = sum(1 for v in vertex_nodes if v is None)
    if missing > 0.02 * len(vertex_nodes):
        raise ValueError(
            f"{missing} de {len(vertex_nodes)} vértices del cascarón no tienen nodo en el .frd. Los resultados "
            "no corresponden a esta geometría: ¿cambiaron los parámetros después de exportar, o es de otro modelo?")
    if missing:
        checks.append(_c("WARNING", "FRD_UNMATCHED", f"{missing} vértices sin resultado (se dejan en gris)."))

    items = _organize(frd, dat, steps, checks)

    # Esfuerzos N, M: cada nodo de lámina del modelo con sus nodos del .frd en ambas caras.
    plan_index = {_key(xy): v for v, xy in fe.plan.items()}
    vertex_fe = [plan_index.get(_key(xy)) for xy in display_plan]
    thick = {v: fe.nodal_thickness.get(v, t) for v in fe.plan}
    if output == "2D":
        faces = {v: (v + 1, v + 1) for v in fe.plan}
    else:
        order = sorted(fe.plan)
        targets = []
        for v in order:
            mid, n, h = fe.nodes[v], fe.normals[v], 0.5 * thick[v]
            targets += [tuple(mid[i] + h * n[i] for i in range(3)), tuple(mid[i] - h * n[i] for i in range(3))]
        found, _d = _nearest_nodes(frd.nodes, targets, 0.3 * t + 1e-6 * size)
        faces = {v: (found[2 * k], found[2 * k + 1]) for k, v in enumerate(order)
                 if found[2 * k] is not None and found[2 * k + 1] is not None}
        if len(faces) < len(order):
            checks.append(_c("WARNING", "FRD_FACES", f"{len(order) - len(faces)} nodos de la lámina sin sus dos "
                                                     "caras en el .frd: sin N ni M en ellos."))
    expected = fe.summary.get("output")
    if expected and expected != output:
        checks.append(_c("WARNING", "FRD_OUTPUT", f"El modelo se exportó con salida {expected} y el .frd parece {output}."))
    if output == "2D":
        checks.append(_c("INFO", "FRD_2D", "Salida 2D: valores en los nodos de la lámina promediados en el espesor; "
                                           "las tensiones son de membrana, sin flexión. Ambas caras muestran lo mismo."))
    else:
        checks.append(_c("INFO", "FRD_3D", f"Salida 3D: cada cara del cascarón toma el nodo expandido más cercano "
                                           f"(a {dist_max * 1000:.2f} mm como máximo); se ve la flexión."))

    reactions = []
    totals = (dat or {}).get("totals", [])
    static = [s for s in steps if s["kind"] == "STATIC"]
    for s, r in zip(static, totals):
        load = fe.summary["totals_kN"].get(s["name"])
        if load is None:
            continue
        rk = [x / 1000.0 for x in r]
        scale = max(1e-9, math.sqrt(sum(x * x for x in load)))
        err = math.sqrt(sum((a + b) ** 2 for a, b in zip(rk, load))) / scale
        reactions.append({"step": s["name"], "reaction_kN": rk, "load_kN": load, "rel_error": err})
    if totals and reactions:
        worst = max(r["rel_error"] for r in reactions)
        level = "INFO" if worst < 1e-3 else "WARNING"
        checks.append(_c(level, "FRD_REACTIONS",
                         f"Reacciones totales del .dat frente a las cargas del modelo: diferencia máxima "
                         f"{100 * worst:.3f} % en {len(reactions)} pasos."))
    elif dat is None:
        checks.append(_c("INFO", "FRD_NO_DAT", "No se encontró el .dat junto al .frd: sin cotejo de reacciones y "
                                               "con los factores de pandeo tomados del .frd."))

    summary = {"output": output, "nodes": len(frd.nodes), "vertices": len(vertex_nodes), "missing": missing,
               "section_forces": "N y M" if output == "3D" else "sólo N",
               "items": [{"label": _label(it), "step": it.step, "kind": it.kind, "mode": it.mode,
                          "factor": it.factor, "factor_source": it.factor_source,
                          "has_stress": it.stress is not None} for it in items],
               "reactions": reactions, "checks": checks}
    return FeaResults(output, vertex_nodes, items, summary, vertex_fe, faces, dict(fe.normals), thick,
                      {v: fe.nodes[v] for v in fe.plan})


def _organize(frd: Frd, dat, steps, checks) -> list[ResultItem]:
    groups: dict[int, list[Dataset]] = {}
    for ds in frd.datasets:
        groups.setdefault(ds.step, []).append(ds)
    ordered = [groups[k] for k in sorted(groups)]
    if not frd.has_steps or len(ordered) != len(steps):
        checks.append(_c("WARNING", "FRD_STEPS", f"El .frd tiene {len(ordered)} pasos y el modelo {len(steps)}: "
                                                 "se asocian en orden (¿terminó el cálculo?)."))
    dat_buckling = list((dat or {}).get("buckling", []))
    items = []
    for step, group in zip(steps, ordered):
        disps = [d for d in group if d.name.upper().startswith("DISP")]
        if not disps:
            continue
        if step["kind"] == "STATIC":
            stress = [d for d in group if d.name.upper().startswith("STRESS")]
            items.append(ResultItem(step["name"], step["title"], "STATIC", None, None, None, disps[-1].data,
                                    stress[-1].data if stress else None))
        else:
            factors = dat_buckling.pop(0) if dat_buckling else []
            for k, d in enumerate(disps):
                from_dat = k < len(factors)
                items.append(ResultItem(step["name"], step["title"], "BUCKLE", d.mode or k + 1,
                                        factors[k] if from_dat else d.value, ".dat" if from_dat else ".frd",
                                        d.data, None))
    if not items:
        raise ValueError("El .frd no tiene desplazamientos (DISP): exporta con *NODE FILE U (lo hace CandelaLab).")
    return items


def _key(xy) -> tuple[float, float]:
    return (round(xy[0], 6), round(xy[1], 6))


def _nearest_nodes(nodes: dict[int, Vec3], points, tol: float):
    """Nodo más cercano a cada punto (a menos de tol), con una rejilla de celdas de lado tol."""
    cells: dict[tuple[int, int, int], list[int]] = {}
    for nid, p in nodes.items():
        cells.setdefault((math.floor(p[0] / tol), math.floor(p[1] / tol), math.floor(p[2] / tol)), []).append(nid)
    out, worst = [], 0.0
    for p in points:
        c = (math.floor(p[0] / tol), math.floor(p[1] / tol), math.floor(p[2] / tol))
        best, best_d = None, tol
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for nid in cells.get((c[0] + dx, c[1] + dy, c[2] + dz), ()):
                        d = math.dist(nodes[nid], p)
                        if d <= best_d:
                            best, best_d = nid, d
        out.append(best)
        if best is not None:
            worst = max(worst, best_d)
    return out, worst


def von_mises(s) -> float:
    xx, yy, zz, xy, yz, zx = s[:6]
    return math.sqrt(0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) + 3.0 * (xy * xy + yz * yz + zx * zx))


def principal(s) -> tuple[float, float]:
    """(máxima, mínima) de las tensiones principales: autovalores del tensor simétrico
    (SXX, SYY, SZZ, SXY, SYZ, SZX), método trigonométrico."""
    a, b, c, d, e, f = s[:6]
    p1 = d * d + e * e + f * f
    q = (a + b + c) / 3.0
    if p1 <= 1e-30 * max(1.0, a * a + b * b + c * c):
        return max(a, b, c), min(a, b, c)
    p = math.sqrt(((a - q) ** 2 + (b - q) ** 2 + (c - q) ** 2 + 2.0 * p1) / 6.0)
    m = [[(a - q) / p, d / p, f / p], [d / p, (b - q) / p, e / p], [f / p, e / p, (c - q) / p]]
    det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    phi = math.acos(max(-1.0, min(1.0, det / 2.0))) / 3.0
    return q + 2.0 * p * math.cos(phi), q + 2.0 * p * math.cos(phi + 2.0 * math.pi / 3.0)


def colors(values: list[float | None], signed: bool) -> tuple[list, dict]:
    """Colores por vértice y leyenda. Con signo: escala simétrica centrada en 0."""
    vals = [v for v in values if v is not None]
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 0.0)
    grey = (0.35, 0.35, 0.35, 1.0)
    if signed:
        m = max(abs(lo), abs(hi)) or 1.0
        cols = [diverging(0.5 + 0.5 * v / m) if v is not None else grey for v in values]
    else:
        span = (hi - lo) or 1.0
        cols = [sequential((v - lo) / span) if v is not None else grey for v in values]
    return cols, {"min": lo, "max": hi, "signed": signed}


def _c(level, code, message):
    return {"level": level, "code": code, "message": message}
