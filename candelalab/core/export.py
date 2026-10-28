"""Exportación para análisis externo (FEA, scripts, otras herramientas).

El documento JSON contiene:
* parameters: el diccionario completo que reproduce el modelo (re-importable).
* definition: la ecuación explícita y sus coeficientes.
* patches: ecuación y dominio de cada parche.
* report: métricas y comprobaciones geométricas.
* midsurface: malla de la superficie media (nodos + elementos de 3 o 4 nodos),
  conjuntos de nodos con nombre (bordes, limas, clave) y nodos de apoyo; base
  directa para elementos lámina tipo S3/S4 / MITC.
* rulings: generatrices exactas de la superficie de referencia.
* cutlist: despiece de cimbra (si está activado): tipos y cada pieza.
* stiffeners: nervios en las limas y viga del borde libre (secciones y volúmenes).
* fea (opcional): modelo de elementos finitos con apoyos y cargas (ver fea.py),
  el mismo que se escribe en formato CalculiX/Abaqus.

El despiece también se exporta como CSV (cutlist_csv) para hoja de cálculo.
"""

from __future__ import annotations

import csv
import io
import math

from . import SCHEMA_VERSION
from .formwork import KIND_NAMES, cutlist_document
from .geometry import SectorDomain
from .model import MID, mesher_for
from .pipeline import Evaluation
from .rulings import model_rulings

DISCLAIMER = ("Datos exclusivamente geométricos generados por CandelaLab. No se ha realizado ningún "
              "análisis ni verificación estructural; ningún valor de este archivo indica seguridad, "
              "resistencia o estabilidad.")


def build_document(ev: Evaluation, world_matrix: list[list[float]] | None = None,
                   include_mesh: bool = True, include_fea: bool = False) -> dict:
    p = ev.params
    model = ev.model
    th = model.thickness
    doc = {
        "schema": f"candelalab.shell/{SCHEMA_VERSION}",
        "units": {"length": "m", "angle": "rad", "density": "kg/m3", "force": "kN"},
        "coordinate_system": "Local del objeto; Z vertical; normal de la superficie hacia +Z.",
        "world_matrix": world_matrix,
        "disclaimer": DISCLAIMER,
        "parameters": _jsonable(p),
        "definition": model.definition,
        "patches": [_patch_doc(patch) for patch in model.patches],
        "creases": [{"patches": [c.patch_a, c.patch_b], "start": list(c.start), "end": list(c.end)}
                    for c in model.creases],
        "thickness": {"value": th.thickness, "mode": th.mode, "reference": th.reference,
                      "offsets_intrados_extrados": list(th.offsets)},
        "report": ev.report,
    }
    if include_mesh:
        nu, nv = int(p["res_x"]), int(p["res_y"])
        mid = mesher_for(model.kind)[1](model, nu, nv, th.offset_of(MID))
        nodes = [list(v) for v in mid.vertices]
        doc["midsurface"] = {
            "description": "Superficie media; nodos (x,y,z) y elementos (índices base 0, 3 o 4 nodos), normal +Z.",
            "grid": [nu, nv],
            "nodes": nodes,
            "elements": [list(f) for f in mid.faces],
            "element_patch": [_patch_of(model, mid, f) for f in mid.faces],
            "node_sets": mid.node_sets,
            "support_nodes": {s.name: _nearest(nodes, s.position) for s in model.supports},
        }
    rulings, _ = model_rulings(model, float(p["ruling_spacing"]), 0.0, th.mode)
    doc["rulings"] = [{"family": "AB"[r.family], "start": list(r.points[0]), "end": list(r.points[-1])}
                      for r in rulings]
    if ev.cutlist is not None:
        doc["cutlist"] = cutlist_document(ev.cutlist)
    conc = ev.report["concrete"]
    stiff = {}
    if conc.get("volume_ribs"):
        stiff["crease_ribs"] = {
            "description": "Prisma vertical centrado en cada lima: cara superior en el intradós, fondo "
                           "a «depth» por debajo; unidos en la clave por las bisectrices.",
            "width": p["rib_width"], "depth": p["rib_depth"], "volume": conc["volume_ribs"],
            "creases": [{"patches": [c.patch_a, c.patch_b], "start": list(c.start), "end": list(c.end)}
                        for c in model.creases]}
    if p.get("beam_free_edge") and model.outline is None and conc.get("volume_beams"):
        stiff["free_edge_beam"] = {"width": p["beam_width"], "depth": p["beam_depth"], "volume": conc["volume_beams"],
                                   "description": "Franja hacia el interior desde el borde libre, bajo el intradós."}
    if ev.report.get("capitals"):
        stiff["capitals"] = {
            "profile": "Δt(r) = Δ·(1 − (r/R)²)² bajo cada apoyo; baja el intradós, el extradós no cambia",
            "items": ev.report["capitals"]}
    if stiff:
        doc["stiffeners"] = stiff
    if include_fea:
        from .fea import fe_document, fe_model_from_params
        doc["fea"] = fe_document(fe_model_from_params(ev.params)[0])
    if ev.placements:
        # Retícula: la geometría (superficie media, generatrices, parches) es la de UN módulo en sus
        # coordenadas locales; cada colocación la refleja (mirror) y la traslada (offset).
        doc["grid"] = {
            **ev.report["grid"],
            "transform": "p_mundo = (±x, ±y, z) + offset; el signo − en los ejes con mirror = true",
            "placements": [{"index": list(pl["index"]), "offset": list(pl["offset"]), "mirror": list(pl["mirror"])}
                           for pl in ev.placements],
        }
        doc["module_report"] = ev.module.report
        if "midsurface" in doc:
            doc["midsurface"]["description"] += " Corresponde a un módulo (ver grid.placements)."
    return doc


def cutlist_csv(ev: Evaluation, decimal: str = ",") -> str:
    """Despiece en CSV: resumen, tipos y piezas (coordenadas locales, metros).

    Con coma decimal el separador de campos es «;» (hojas de cálculo en español).
    """
    cl = ev.cutlist
    if cl is None:
        raise ValueError("El despiece de cimbra no está calculado.")
    s = cl.summary
    out = io.StringIO()
    w = csv.writer(out, delimiter=";" if decimal == "," else ",", lineterminator="\n")

    def num(v, digits=3):
        return "" if v is None else f"{v:.{digits}f}".replace(".", decimal)

    w.writerow(["CandelaLab · despiece de cimbra", ev.params["shell_type"]])
    w.writerow(["Aviso", DISCLAIMER + " Secciones, separaciones y largos comerciales son datos del usuario; "
                "la cimbra no está dimensionada."])
    w.writerow(["Coordenadas", "locales del objeto, en metros" + ("; un módulo de la retícula" if ev.placements else "")])
    if s.get("uses", 1) > 1:
        w.writerow(["Usos del juego", s["uses"]])
    w.writerow([])
    w.writerow(["Resumen", "Piezas", "Tipos", "Largo total (m)", "Largo de corte (m)", "Volumen (m3)",
                "Piezas comerciales", "Largo comercial (m)", "Desperdicio (%)", "Empalmes"])
    for label, fam in (("Tablas", s["boards"]), ("Vigas", s["joists"])):
        st = fam["stock"] or {}
        w.writerow([label, fam["count"], len(fam["types"]), num(fam["length_total"]), num(fam["cut_length_total"]),
                    num(fam["volume"], 4), st.get("count", ""), num(st.get("length")),
                    num(st.get("waste_pct"), 1), st.get("splices", "")])
    pr = s["props"]
    w.writerow(["Puntales", pr["count"], len(pr["types"]), num(pr["length_total"]), num(pr["cut_length_total"]),
                "", "", "", "", ""])
    w.writerow(["Madera (tablas y vigas)", "", "", "", "", num(s["wood_volume"], 4), "", "", "", ""])
    w.writerow(["Pies-tablón", "", "", "", "", num(s["board_feet"], 0), "", "", "", ""])
    w.writerow([])
    w.writerow(["Tipo", "Elemento", "Cantidad", "Largo del eje (m)", "Largo de corte (m)",
                "Esviaje inicio (°)", "Esviaje fin (°)"])
    for label, fam in (("Tabla", s["boards"]), ("Viga", s["joists"])):
        for t in fam["types"]:
            w.writerow([t["type"], label, t["count"], num(t["length"]), num(t["cut_length"]),
                        t["skew"][0], t["skew"][1]])
    for t in pr["types"]:
        w.writerow([t["type"], "Puntal", t["count"], "", num(t["length"]), "", ""])
    w.writerow([])
    w.writerow(["Pieza", "Tipo", "Elemento", "Parche", "Familia", "Largo del eje (m)", "Largo de corte (m)",
                "Esviaje inicio (°)", "Esviaje fin (°)", "Giro (°)", "x0", "y0", "z0", "x1", "y1", "z1"])
    for pc in cl.boards + cl.joists:
        w.writerow([pc.id, pc.type, KIND_NAMES[pc.kind], pc.patch, "AB"[pc.family], num(pc.length),
                    num(pc.cut_length), num(pc.skew[0], 1), num(pc.skew[1], 1), num(pc.twist, 1),
                    *(num(c) for c in pc.start), *(num(c) for c in pc.end)])
    for q in cl.props:
        x, y, z = q.top
        w.writerow([q.id, q.type, "Puntal", "", "", num(q.length), num(q.cut_length), "", "", "",
                    num(x), num(y), num(z - q.length), num(x), num(y), num(z)])
    return out.getvalue()


def _patch_doc(patch) -> dict:
    s = patch.surface
    dom = patch.domain
    if isinstance(dom, SectorDomain):
        domain = {"type": "sector", "center": list(dom.center), "a": list(dom.a), "b": list(dom.b),
                  "outer": dom.outer_kind}
        if dom.cut is not None:
            c = dom.cut
            domain["cut_function"] = {"coefficients": [c.c0, c.c1, c.c2, c.c3, c.c4, c.c5],
                                      "meaning": "g(x,y) = f − plano; el borde es g = 0 y g > 0 dentro"}
    else:
        domain = {"type": "quad", "corners": [list(c) for c in dom.corners]}
    return {"name": patch.name, "coefficients": [s.c0, s.c1, s.c2, s.c3, s.c4, s.c5], "domain": domain}


def _patch_of(model, md, face) -> int:
    """Parche al que pertenece un elemento: el que contiene su centroide en planta."""
    cx = sum(md.plan_points[i][0] for i in face) / len(face)
    cy = sum(md.plan_points[i][1] for i in face) / len(face)
    for k, patch in enumerate(model.patches):
        if patch.domain.contains((cx, cy), 1e-9):
            return k
    return md.vertex_patch[face[0]]


def _nearest(nodes: list[list[float]], p) -> int:
    return min(range(len(nodes)), key=lambda i: math.dist(nodes[i], p))


def _jsonable(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, (bool, int, float, str)) or v is None:
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = list(v)
        else:
            out[k] = str(v)
    return out
