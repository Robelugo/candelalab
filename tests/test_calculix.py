"""Pruebas con CalculiX real: exportar, calcular con ccx y leer los resultados.

    python -m unittest discover -s tests -v

Se saltan si no hay CalculiX. Se busca en la variable CANDELALAB_CCX (ruta a
ccx, o a un .cmd/.bat que lo lance) y, si no está, `ccx` en el PATH.

Comparan con soluciones independientes del programa:
* franja plana apoyada en cuatro puntos: el momento total a media luz es el de
  la estática, W·L/8, y la flecha la de una viga, 5·q·L⁴/(384·E·I);
* hypar con vigas de borde bajo peso propio: lejos de los bordes, la membrana
  de Pucher, Nxy = q/(2k) con Nx ≈ Ny ≈ 0, y tracción según la diagonal de las
  esquinas altas;
* las reacciones del .dat, los factores de pandeo del .dat y del .frd, y la
  salida 2D frente a la 3D.
Validan la cadena exportación–cálculo–lectura, no un diseño.
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "candelalab"))

from core.fea import GRAVITY, fe_model_from_params, to_inp  # noqa: E402
from core.frd import load_results, parse_dat, parse_frd  # noqa: E402
from core.pipeline import evaluate  # noqa: E402

CCX = os.environ.get("CANDELALAB_CCX") or shutil.which("ccx")

K, H, A, B, T = 0.1, 3.0, 4.0, 3.0, 0.05  # z = H + K·x·y sobre [−A, A] × [−B, B]
HYPAR = {"shell_type": "HYPAR", "definition": "CORNERS", "span_x": 2 * A, "span_y": 2 * B,
         "z_sw": H + K * A * B, "z_se": H - K * A * B, "z_ne": H + K * A * B, "z_nw": H - K * A * B,
         "res_x": 16, "res_y": 16, "thickness": T, "support_layout": "ALL_CORNERS", "support_type": "PINNED",
         "beam_xmin": True, "beam_xmax": True, "beam_ymin": True, "beam_ymax": True}
L, W_STRIP, T_STRIP, E = 8.0, 1.0, 0.2, 25e9
STRIP = {"shell_type": "HYPAR", "definition": "CORNERS", "span_x": L, "span_y": W_STRIP,
         "z_sw": H, "z_se": H, "z_ne": H, "z_nw": H, "res_x": 32, "res_y": 4, "thickness": T_STRIP,
         "support_layout": "ALL_CORNERS", "support_type": "PINNED", "fea_buckling": False}


def solve(params, folder, name="modelo", with_dat=True):
    """Exporta, calcula con ccx y carga los resultados sobre el cascarón."""
    fe, _rep = fe_model_from_params(params)
    ev = evaluate(params)
    with open(os.path.join(folder, name + ".inp"), "w", encoding="ascii", newline="\n") as f:
        f.write(to_inp(fe, name))
    cmd = [CCX, "-i", name]
    if CCX.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", *cmd]
    run = subprocess.run(cmd, cwd=folder, capture_output=True, text=True)
    frd_path = os.path.join(folder, name + ".frd")
    if not os.path.exists(frd_path):
        raise AssertionError(f"ccx no escribió el .frd (código {run.returncode}):\n{run.stdout[-2000:]}")
    with open(frd_path, encoding="ascii", errors="replace") as f:
        frd = parse_frd(f.read())
    dat = None
    if with_dat:
        with open(os.path.join(folder, name + ".dat"), encoding="ascii", errors="replace") as f:
            dat = parse_dat(f.read())
    mesh = ev.shell_mesh
    return load_results(frd, dat, fe, mesh.plan_points, mesh.vertices, model=ev.model), fe


def check(res, code):
    return next(c for c in res.summary["checks"] if c["code"] == code)


@unittest.skipUnless(CCX, "sin CalculiX (define CANDELALAB_CCX o pon ccx en el PATH)")
class TestCalculix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="clab_ccx_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_strip_matches_statics_and_beam_theory(self):
        weight = 2400 * GRAVITY * T_STRIP * L * W_STRIP          # N
        beam = 5 * (weight / L) * L ** 4 / (384 * E * W_STRIP * T_STRIP ** 3 / 12) * 1000.0
        for order in ("QUADRATIC", "LINEAR"):
            res, fe = solve({**STRIP, "fea_element_order": order}, self.tmp, order.lower())
            self.assertEqual(res.output, "3D")
            self.assertEqual(res.summary["missing"], 0)
            self.assertEqual(len(fe.nsets["NAPOYOS"]), 4)                     # articulaciones de un nodo
            self.assertEqual(check(res, "FRD_REACTIONS")["level"], "INFO", order)
            forces = res._section(0)
            mid = sorted((v for v in forces if abs(res.plan[v][0]) < 1e-9), key=lambda v: res.plan[v][1])
            ys, mx = [res.plan[v][1] for v in mid], [forces[v][7] for v in mid]
            total = sum(0.5 * (mx[j] + mx[j + 1]) * (ys[j + 1] - ys[j]) for j in range(len(ys) - 1))
            # Estática: reacciones W/2 en x = ±L/2 → M = W·L/8; tracciona el intradós (M < 0).
            self.assertAlmostEqual(total / (-weight * L / 8 / 1000.0), 1.0, delta=0.01, msg=order)
            self.assertLess(max(abs(forces[v][2]) for v in mid), 1e-3)          # sin axil
            sag = -min(u for u in res.values(0, "DISP_Z") if u is not None)
            self.assertAlmostEqual(sag / beam, 1.0, delta=0.02, msg=order)

    def test_hypar_membrane_and_outputs(self):
        q = 2400 * GRAVITY * T                                                # N/m² en planta, en el centro
        res, fe = solve({**HYPAR, "fea_output": "3D"}, self.tmp, "hypar3d")
        self.assertEqual(res.summary["missing"], 0)
        self.assertEqual(check(res, "FRD_REACTIONS")["level"], "INFO")
        forces = res._section(0)
        inner = [forces[v] for v in forces if abs(res.plan[v][0]) <= A / 2 and abs(res.plan[v][1]) <= B / 2]
        mean = sum(f[4] for f in inner) / len(inner)
        self.assertAlmostEqual(mean / (q / (2 * K) / 1000.0), 1.0, delta=0.05)  # Pucher: Nxy = q/(2k)
        centre = min(forces, key=lambda v: math.hypot(*res.plan[v]))
        n1, n2, nx, ny, nxy = forces[centre][:5]
        theta = math.degrees(0.5 * math.atan2(2 * nxy, nx - ny))
        self.assertAlmostEqual(theta, 45.0, delta=1.0)        # N₁ (tracción) según la diagonal de las esquinas altas
        self.assertGreater(n1, 0.0)
        self.assertLess(n2, 0.0)

        # Pandeo: tantos modos como se pidieron (el estado base del paso no es un modo), y los factores del
        # .dat y los del .frd coinciden.
        modes = [it for it in res.items if it.kind == "BUCKLE"]
        self.assertEqual(len(modes), fe.buckling_modes * len(fe.buckling))
        self.assertTrue(all(it.factor_source == ".dat" and it.factor > 0 for it in modes))
        self.assertNotIn("FRD_BUCKLE_FACTORS", {c["code"] for c in res.summary["checks"]})
        no_dat, _fe = solve({**HYPAR, "fea_output": "3D"}, self.tmp, "hypar3d", with_dat=False)
        from_frd = {(it.step, it.mode): it.factor for it in no_dat.items if it.kind == "BUCKLE"}
        for it in modes:
            self.assertAlmostEqual(from_frd[(it.step, it.mode)] / it.factor, 1.0, delta=1e-4)

        # Salida 2D: CalculiX promedia en el espesor. Lejos de las vigas da la misma N que las dos caras de
        # la 3D; en la unión con el alma mezcla la lámina con la viga.
        two, _fe = solve({**HYPAR, "fea_output": "2D", "fea_buckling": False}, self.tmp, "hypar2d")
        self.assertEqual(two.output, "2D")
        flat = two._section(0)
        webs = {v for _t, s, conns in fe.blocks if s.startswith("EVIGA_") for c in conns for v in c}
        diff = max(abs(forces[v][k] - flat[v][k]) for v in forces if v not in webs for k in (2, 3, 4))
        self.assertLess(diff, 1e-3)
