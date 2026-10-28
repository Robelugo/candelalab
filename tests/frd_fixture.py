"""Archivos .frd y .dat sintéticos para las pruebas (no hay CalculiX en el equipo de desarrollo).

Siguen el formato documentado en el manual de CalculiX GraphiX («Result Format»):
    nodos      (1X,'   2','C',18X,I12,37X,I1) y (1X,'-1',I10,3E12.5)  (largo; corto: I5)
    resultados (1X,' 100','C',6A1,E12.5,I12,20A1,I2,I5,10A1,I2), (1X,I2,2X,8A1,2I5),
               (1X,I2,2X,8A1,5I5,8A1) y (1X,'-1',I10,6E12.5) con continuaciones '-2'
y la disposición de 1PSTEP / 1PMODE que leen otros lectores (FreeCAD).
"""


def _fmt_node(nid, fmt):
    return f"{nid:10d}" if fmt == 1 else f"{nid:5d}"


def frd_text(nodes, steps, fmt=1, elements=None):
    """nodes: {id: (x, y, z)}; steps: lista de pasos, cada uno una lista de conjuntos
    {"name", "components", "data": {id: [valores]}, "value", "mode" (opcional)}."""
    out = ["    1C                                                                   ",
           "    1UPGM               CalculiX (prueba sintetica)"]
    out.append(f"    2C{'':18s}{len(nodes):12d}{'':37s}{fmt:1d}")
    for nid, (x, y, z) in nodes.items():
        out.append(f" -1{_fmt_node(nid, fmt)}{x:12.5E}{y:12.5E}{z:12.5E}")
    out.append(" -3")
    if elements:
        out.append(f"    3C{'':18s}{len(elements):12d}{'':37s}{fmt:1d}")
        for eid, (etype, conn) in elements.items():
            out.append(f" -1{_fmt_node(eid, fmt)}{etype:5d}{0:5d}{1:5d}")
            for k in range(0, len(conn), 10):
                out.append(" -2" + "".join(f"{v:10d}" for v in conn[k:k + 10]))
        out.append(" -3")
    kode = 0
    for kstep, datasets in enumerate(steps, 1):
        for ds in datasets:
            kode += 1
            out.append(f"    1PSTEP{kode:25d}{1:12d}{kstep:12d}")
            if ds.get("mode"):
                out.append(f"    1PMODE{ds['mode']:26d}")
            out.append(f"  100CL{100 + kode:5d}{ds.get('value', 1.0):12.5E}{len(ds['data']):12d}{'':20s}"
                       f"{2 if ds.get('mode') else 0:2d}{kstep:5d}{'':10s}{fmt:2d}")
            comps = ds["components"]
            out.append(f" -4  {ds['name']:8s}{len(comps) + (1 if ds['name'] == 'DISP' else 0):5d}{1:5d}")
            for k, c in enumerate(comps, 1):
                out.append(f" -5  {c:8s}{1:5d}{2 if len(comps) == 3 else 4:5d}{k:5d}{0:5d}")
            if ds["name"] == "DISP":
                out.append(f" -5  {'ALL':8s}{1:5d}{2:5d}{0:5d}{0:5d}{1:5d}ALL")
            for nid, vals in ds["data"].items():
                out.append(f" -1{_fmt_node(nid, fmt)}" + "".join(f"{v:12.5E}" for v in vals[:6]))
                if len(vals) > 6:
                    out.append(" -2" + " " * (10 if fmt == 1 else 5) + "".join(f"{v:12.5E}" for v in vals[6:]))
            out.append(" -3")
    out.append(" 9999")
    return "\n".join(out) + "\n"


def dat_text(buckling=(), totals=()):
    out = []
    for k, tot in enumerate(totals, 1):
        out += ["", f" total force (fx,fy,fz) for set NAPOYOS and time  {1.0:.7E}", "",
                f"       {tot[0]:.6E}  {tot[1]:.6E}  {tot[2]:.6E}"]
    for factors in buckling:
        out += ["", "     B U C K L I N G   F A C T O R   O U T P U T", "", " MODE NO       BUCKLING",
                "                FACTOR", ""]
        out += [f"      {k:d}   {f:.7E}" for k, f in enumerate(factors, 1)]
    return "\n".join(out) + "\n"


def synthetic(fe, nodes, disp, stress, buckling_factors=(2.5, 4.0)):
    """Pasos como los del modelo FE: estáticos con DISP/FORC/STRESS y modos de pandeo."""
    try:
        from core.fea import analysis_steps
    except ImportError:  # dentro de Blender el paquete es candelalab
        from candelalab.core.fea import analysis_steps
    steps = []
    for k, s in enumerate(analysis_steps(fe), 1):
        if s["kind"] == "STATIC":
            steps.append([
                {"name": "DISP", "components": ["D1", "D2", "D3"], "data": {n: disp(n, p, k) for n, p in nodes.items()}},
                {"name": "FORC", "components": ["F1", "F2", "F3"], "data": {n: [0.0, 0.0, 0.0] for n in nodes}},
                {"name": "STRESS", "components": ["SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"],
                 "data": {n: stress(n, p, k) for n, p in nodes.items()}},
            ])
        else:
            steps.append([{"name": "DISP", "components": ["D1", "D2", "D3"], "mode": m, "value": f,
                           "data": {n: disp(n, p, k * 10 + m) for n, p in nodes.items()}}
                          for m, f in enumerate(buckling_factors, 1)])
    return steps
