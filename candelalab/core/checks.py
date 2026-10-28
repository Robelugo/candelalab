"""Detección de problemas geométricos y de constructibilidad evidentes.

Son comprobaciones GEOMÉTRICAS. Ninguna afirma ni niega seguridad estructural:
una hypar geométricamente impecable puede fallar por pandeo, por empujes en los
bordes o por falta de armado. Eso requiere análisis (v0.4).
"""

from __future__ import annotations

import math

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"


def _c(level: str, code: str, message: str) -> dict:
    return {"level": level, "code": code, "message": message}


def run_checks(report: dict, params: dict, component_errors: list[tuple[str, str]]) -> list[dict]:
    out: list[dict] = []
    geo, th, mesh, conc, form = (report[k] for k in ("geometry", "thickness", "mesh", "concrete", "formwork"))
    t = th["value"]

    for code, msg in component_errors:
        out.append(_c(ERROR, code, msg))

    if not report["definition"].get("is_hyperbolic", False):
        out.append(_c(ERROR, "PLANAR", "Alabeo nulo: la superficie es plana, no un paraboloide hiperbólico "
                                       "(sin doble curvatura ni generatrices cruzadas)."))

    cr = geo.get("creases") or {}
    if cr.get("count"):
        kinds = ", ".join(f"{cr[k]} {name}" for k, name in (("valleys", "valles"), ("ridges", "cumbreras"),
                                                            ("mixed", "mixtas")) if cr[k])
        out.append(_c(INFO, "CREASES",
                      f"{cr['count']} limas rectas ({kinds}), {cr['length']:.1f} m en total; ángulo entre normales "
                      f"{cr['normal_angle_min']:.1f}°–{cr['normal_angle_max']:.1f}°. Espesor con inglete en las limas."))

    rect = any(params.get(f"beam_{k}") for k in ("xmin", "xmax", "ymin", "ymax"))
    if rect and report["shell_type"] == "RADIAL":
        out.append(_c(INFO, "BEAMS_UNSUPPORTED", "Las vigas por lado son de contornos rectangulares; en la bóveda "
                                                 "radial usa la viga del borde libre."))
    if params.get("beam_free_edge") and report["shell_type"] != "RADIAL":
        out.append(_c(INFO, "BEAMS_UNSUPPORTED", "La viga del borde libre es de las bóvedas radiales; en este "
                                                 "cascarón usa las vigas por lado."))

    if params.get("rib_creases"):
        if not cr.get("count"):
            out.append(_c(INFO, "RIBS_NO_CREASES", "Este cascarón no tiene limas, así que no lleva nervios: una "
                                                   "hypar de un solo paño se rigidiza con vigas de borde."))
        elif conc.get("volume_ribs"):
            out.append(_c(INFO, "RIBS",
                          f"Nervios bajo {cr['count']} limas ({cr['length']:.1f} m), "
                          f"{params['rib_width'] * 100:.0f} × {params['rib_depth'] * 100:.0f} cm: "
                          f"{conc['volume_ribs']:.3f} m³ de concreto. La sección la fija el usuario; "
                          "no está dimensionada."))

    fold = th.get("offset_fold_factor")
    if fold is not None and fold <= 0.0:
        out.append(_c(ERROR, "OFFSET_FOLD",
                      f"El desfase normal se pliega: el espesor ({t * 1000:.0f} mm) alcanza el radio de curvatura "
                      f"mínimo ({geo['radius_min']:.3f} m). Reduce espesor o alabeo, o usa espesor vertical."))
    elif fold is not None and fold < 0.5:
        out.append(_c(WARNING, "OFFSET_DISTORTION",
                      f"El desfase normal distorsiona mucho la capa opuesta (factor mínimo {fold:.2f}); "
                      "la cara desfasada se aparta notablemente de una hypar."))

    if th["mode"] == "VERTICAL" and th["normal_min"] < 0.85 * t:
        out.append(_c(WARNING, "THICKNESS_VARIATION",
                      f"Con espesor vertical el espesor real (normal) baja hasta ≈ {th['normal_min'] * 1000:.1f} mm "
                      f"({100 * th['normal_min'] / t:.0f} %) en las zonas de mayor pendiente."))

    limit = math.degrees(params["pour_slope_limit"])
    if geo["slope_max_deg"] > limit:
        out.append(_c(WARNING, "STEEP_SLOPES",
                      f"Pendiente máxima {geo['slope_max_deg']:.1f}° > {limit:.0f}° en ≈ {100 * geo['steep_area_fraction']:.0f} % "
                      "de la superficie: el colado probablemente requiera contracimbra o concreto lanzado "
                      "(criterio indicativo y ajustable)."))

    ground = params["ground_level"]
    if geo["z_concrete_min"] < ground - 1e-6:
        out.append(_c(WARNING, "BELOW_GROUND",
                      f"Parte del concreto queda {ground - geo['z_concrete_min']:.3f} m bajo el nivel de desplante "
                      f"(z = {ground:.2f} m). Usa «Apoyar en desplante» o ajusta la altura."))

    if mesh["output"] == "SOLID" and not (mesh["closed"] and mesh["consistent"]):
        out.append(_c(ERROR, "MESH_TOPOLOGY", "La malla del sólido no es cerrada o tiene normales incoherentes."))

    if mesh["chord_error_max"] > 0.1 * t:
        out.append(_c(WARNING, "MESH_COARSE",
                      f"La malla se aparta de la superficie exacta hasta {mesh['chord_error_max'] * 1000:.1f} mm "
                      "(> 10 % del espesor). Aumenta la resolución para visualizar o exportar."))
    if mesh["volume_rel_error"] is not None and abs(mesh["volume_rel_error"]) > 0.01:
        out.append(_c(WARNING, "VOLUME_MISMATCH",
                      f"El volumen de la malla difiere {100 * mesh['volume_rel_error']:.2f} % del analítico."))

    if not report["supports"]:
        out.append(_c(INFO, "NO_SUPPORTS", "No hay apoyos definidos en el concepto estructural."))

    for fam in form["rulings"]:
        if fam["count"]:
            out.append(_c(INFO, f"RULING_{fam['family']}",
                          f"Cimbra, familia {fam['family']}: {fam['count']} piezas rectas a {fam['spacing']:.2f} m, "
                          f"pieza más larga {fam['max_length']:.2f} m, total {fam['total_length']:.1f} m."))
    if not form["rulings_exact"]:
        dev = max((f["straightness_dev_max"] for f in form["rulings"]), default=0.0)
        out.append(_c(INFO, "INTRADOS_NOT_RULED",
                      f"El intradós no es la superficie de referencia y el espesor es normal: sus generatrices "
                      f"no son rectas exactas (desviación máx. {dev * 1000:.2f} mm)."))

    out += _cutlist_checks(report.get("cutlist"), params)
    out += _capital_checks(report, params)

    out.append(_c(INFO, "SLENDERNESS",
                  f"Relación luz/espesor = {geo['span_thickness_ratio']:.0f}; peso propio "
                  f"{conc['weight_per_plan_area']:.2f} kN/m² en planta (sólo cuantificación)."))
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    out.sort(key=lambda c: order[c["level"]])
    return out


def _capital_checks(report: dict, params: dict) -> list[dict]:
    if not params.get("capital_enabled"):
        return []
    caps = report.get("capitals") or []
    if not caps:
        if not report["supports"]:
            return [_c(INFO, "CAPITAL_NO_SUPPORTS", "No hay apoyos: no hay dónde poner capiteles.")]
        return []
    c0 = caps[0]
    vol = report["concrete"].get("volume_capitals", 0.0)
    out = [_c(INFO, "CAPITALS",
              f"Capiteles en {len(caps)} apoyos: radio {c0['radius']:.2f} m, espesor hasta "
              f"{c0['thickness_max'] * 1000:.0f} mm en el centro; {vol:.3f} m³ de concreto. Las dimensiones las "
              "fija el usuario; no están dimensionadas.")]
    if c0["radius"] < 3.0 * c0["mesh_size"]:
        out.append(_c(WARNING, "CAPITAL_COARSE",
                      f"La malla (≈ {c0['mesh_size']:.2f} m) es gruesa para un capitel de radio {c0['radius']:.2f} m: "
                      "aumenta las divisiones para verlo y exportarlo bien (el volumen analítico es exacto)."))
    folds = [c["fold_factor"] for c in caps if c.get("fold_factor") is not None]
    if folds and min(folds) < 0.5:
        out.append(_c(WARNING, "CAPITAL_FOLD",
                      f"Con el capitel, el desfase normal del intradós se acerca al radio de curvatura (factor "
                      f"{min(folds):.2f}): reduce el espesor añadido o usa espesor vertical."))
    if params.get("formwork_enabled"):
        out.append(_c(INFO, "CAPITAL_FORMWORK", "El despiece de cimbra sigue el intradós sin capiteles: bajo cada "
                                                "capitel hace falta un cajón aparte."))
    return out


def _cutlist_checks(cl: dict | None, params: dict) -> list[dict]:
    if not cl:
        return []
    bo, jo, pr = cl["boards"], cl["joists"], cl["props"]
    if not bo["count"] and not jo["count"]:
        return [_c(WARNING, "CUTLIST_EMPTY", "Sin generatrices rectas no hay despiece de cimbra.")]
    out = []
    uses = cl.get("uses", 1)
    props = f", {pr['count']} puntales en {len(pr['types'])} largos" if pr["count"] else ""
    out.append(_c(INFO, "CUTLIST",
                  f"Despiece de cimbra{f' (un juego, {uses} usos)' if uses > 1 else ''}: {bo['count']} tablas en "
                  f"{len(bo['types'])} tipos y {jo['count']} vigas en {len(jo['types'])} tipos{props}; madera "
                  f"{cl['wood_volume']:.2f} m³ ({cl['board_feet']:,.0f} pies-tablón). Secciones y separaciones "
                  "son datos del usuario, no un dimensionamiento de la cimbra."))
    out.append(_c(INFO, "CUTLIST_TWIST",
                  f"Las tablas se tuercen hasta {bo['twist_max_deg']:.1f}° de un extremo a otro (hasta "
                  f"{bo['twist_rate_max_deg_m']:.1f}°/m; una esquina queda "
                  f"{bo['corner_lift_max'] * 1000:.1f} mm fuera del plano con {bo['width'] * 100:.0f} cm de ancho): "
                  "la hypar no es desarrollable. Con tablas más angostas ese desajuste baja en proporción."))
    tol = cl["tolerance"]
    if bo["gap_max"] > tol:
        out.append(_c(WARNING, "CUTLIST_GAPS",
                      f"Holgura entre tablas contiguas hasta {bo['gap_max'] * 1000:.1f} mm (> {tol * 1000:.0f} mm): "
                      "las generatrices de una familia se cruzan en el espacio y se separan hacia los bordes. "
                      "Hacen falta tablas ahusadas, cuñas o tablas más angostas."))
    elif bo["count"]:
        out.append(_c(INFO, "CUTLIST_GAPS",
                      f"Holgura máxima entre tablas contiguas: {bo['gap_max'] * 1000:.1f} mm."))
    for name, fam in (("tablas", bo), ("vigas", jo)):
        st = fam["stock"]
        if st and st["splices"]:
            out.append(_c(INFO, "CUTLIST_SPLICES",
                          f"{st['pieces_over']} {name} superan el largo comercial de {st['length']:.2f} m: "
                          f"{st['splices']} empalmes, que deben caer sobre un apoyo."))
    if pr["skipped"]:
        out.append(_c(WARNING, "CUTLIST_PROPS",
                      f"{pr['skipped']} puntales no caben entre la viga y el desplante "
                      f"(z = {params['ground_level']:.2f} m)."))
    if cl.get("level_range") and cl["level_range"][1] - cl["level_range"][0] > 1e-9:
        lo, hi = cl["level_range"]
        out.append(_c(INFO, "CUTLIST_LEVELS",
                      f"Retícula escalonada: los puntales de cada módulo cambian entre {lo:+.2f} y {hi:+.2f} m "
                      "respecto del despiece (que es el del primer módulo)."))
    if not cl["exact"]:
        dev = max(bo["deviation_max"], jo["deviation_max"])
        out.append(_c(INFO, "CUTLIST_APPROX",
                      f"El intradós no es reglado (espesor normal, otra superficie exacta): las piezas son cuerdas "
                      f"y se separan de él hasta {dev * 1000:.2f} mm."))
    return out
