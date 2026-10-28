"""Pruebas del despiece de cimbra (sin Blender).

    python -m unittest discover -s tests -v

La hypar de prueba es z = 3 + k·x·y (k = 0.1) sobre 8 × 6 m, definida por
alturas de esquina. Sus generatrices son las rectas x = cte e y = cte, y las
fórmulas cerradas de largo, giro, esviaje y holgura se comparan con el despiece.
"""

import csv
import io
import json
import math
import os
import sys
import unittest

ADDON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "candelalab")
sys.path.insert(0, ADDON_DIR)

from core.export import build_document, cutlist_csv  # noqa: E402
from core.formwork import _canonical_skew, stock_plan  # noqa: E402
from core.meshing import mesh_volume, topology_report  # noqa: E402
from core.pipeline import evaluate  # noqa: E402
from core.radial import PRESETS as RADIAL  # noqa: E402
from core.umbrella import PRESETS as UMBRELLA  # noqa: E402

K, H, A, B = 0.1, 3.0, 4.0, 3.0  # z = H + K·x·y sobre [−A, A] × [−B, B]
HYPAR = {"shell_type": "HYPAR", "definition": "CORNERS", "span_x": 2 * A, "span_y": 2 * B,
         "z_sw": H + K * A * B, "z_se": H - K * A * B, "z_ne": H + K * A * B, "z_nw": H - K * A * B,
         "res_x": 8, "res_y": 8, "formwork_enabled": True}
BW = 0.10  # ancho de tabla por defecto


def cut(**kw):
    return evaluate({**HYPAR, **kw})


def normal(x, y):
    n = (-K * y, -K * x, 1.0)
    ln = math.sqrt(sum(c * c for c in n))
    return tuple(c / ln for c in n)


class TestBoardsOnExactHypar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev = cut()
        cls.boards = cls.ev.cutlist.boards
        cls.rep = cls.ev.report["cutlist"]

    def test_boards_follow_family_a_at_half_offsets(self):
        ys = sorted(round(pc.start[1], 9) for pc in self.boards)
        self.assertEqual(len(ys), 60)                                   # (k + ½)·0.1 en [−3, 3]
        self.assertAlmostEqual(ys[0], -2.95, places=9)
        for pc in self.boards:
            self.assertAlmostEqual(pc.start[1], pc.end[1], places=12)   # recta y = cte
            self.assertAlmostEqual(abs(pc.start[0]), A, places=9)

    def test_length_and_cut_length(self):
        for pc in self.boards:
            y0 = pc.start[1]
            self.assertAlmostEqual(pc.length, 2 * A * math.sqrt(1 + (K * y0) ** 2), places=9)
            # arista exterior: la recta y0 ± b/2 más alejada del eje x
            self.assertAlmostEqual(pc.cut_length, 2 * A * math.sqrt(1 + (K * (abs(y0) + BW / 2)) ** 2), places=9)

    def test_twist_is_rotation_of_normal_along_ruling(self):
        for pc in self.boards:
            y0 = pc.start[1]
            n0, n1 = normal(-A, y0), normal(A, y0)
            expected = math.degrees(math.acos(sum(a * b for a, b in zip(n0, n1))))
            self.assertAlmostEqual(pc.twist, expected, places=9)
        self.assertAlmostEqual(self.rep["boards"]["corner_lift_max"],
                               BW * math.sin(math.radians(self.rep["boards"]["twist_max_deg"])), places=12)

    def test_skew_is_angle_between_rulings_in_space(self):
        # En planta las tablas llegan a escuadra al borde x = ±A; en el espacio, las dos
        # generatrices forman un ángulo ψ con cos ψ = k²·|x·y| / √((1 + k²y²)(1 + k²x²)).
        for pc in self.boards:
            y0 = pc.start[1]
            cos_psi = K * K * A * abs(y0) / math.sqrt((1 + (K * y0) ** 2) * (1 + (K * A) ** 2))
            expected = 90.0 - math.degrees(math.acos(cos_psi))
            self.assertAlmostEqual(abs(pc.skew[0]), expected, places=9)
            self.assertAlmostEqual(abs(pc.skew[1]), expected, places=9)

    def test_gaps_closed_form(self):
        # Distancia de (x, y0, H + k·x·y0) a la generatriz y = y1: máxima en x = ±A.
        def dist(x, y0, y1):
            w = (x, y0 - y1, K * x * y0)
            u = (1.0, 0.0, K * y1)
            uu = 1 + (K * y1) ** 2
            return math.sqrt(sum(c * c for c in w) - (w[0] * u[0] + w[2] * u[2]) ** 2 / uu)

        ys = sorted(pc.start[1] for pc in self.boards)
        expected = max(dist(x, y0, y1) for y0, y1 in zip(ys, ys[1:]) for x in (-A, A)) - BW
        self.assertAlmostEqual(self.rep["boards"]["gap_max"], expected, places=12)
        self.assertGreaterEqual(self.rep["boards"]["gap_min"], -1e-12)   # nunca se solapan
        self.assertGreater(expected, 0.0)

    def test_symmetric_pieces_share_types(self):
        for t in self.rep["boards"]["types"]:
            self.assertEqual(t["count"] % 2, 0, t)                         # ±y0
        self.assertEqual(sum(t["count"] for t in self.rep["boards"]["types"]), 60)
        self.assertEqual(len({pc.id for pc in self.boards}), 60)


class TestJoistsAndProps(unittest.TestCase):
    def test_joists_follow_other_family_from_origin(self):
        ev = cut()
        # los extremos bajan el espesor de tabla según la normal; el centro queda sobre la recta x = cte
        xs = sorted(round(0.5 * (pc.start[0] + pc.end[0]), 6) for pc in ev.cutlist.joists)
        self.assertEqual(xs, [round(0.6 * k, 6) for k in range(-6, 7)])
        jo = ev.report["cutlist"]["joists"]
        self.assertEqual(jo["family"], "B")
        self.assertAlmostEqual(jo["deviation_max"], max(pc.deviation for pc in ev.cutlist.joists))

    def test_board_family_switch(self):
        ev = cut(formwork_board_family="B")
        self.assertTrue(all(abs(pc.start[0] - pc.end[0]) < 1e-12 for pc in ev.cutlist.boards))
        self.assertEqual(ev.report["cutlist"]["boards"]["family"], "B")

    def test_props_on_nearly_flat_surface(self):
        # Con alabeo casi nulo, la altura libre es H − tabla − viga.
        eps = 1e-4
        ev = evaluate({**HYPAR, "z_sw": H + eps, "z_se": H - eps, "z_ne": H + eps, "z_nw": H - eps})
        pr = ev.report["cutlist"]["props"]
        self.assertEqual(pr["count"], 13 * math.ceil(6 / 1.2))            # 13 vigas, 5 puntales cada una
        for q in ev.cutlist.props:
            self.assertAlmostEqual(q.length, H - 0.025 - 0.15, places=3)
            self.assertAlmostEqual(q.cut_length, 2.80, places=9)          # de 5 en 5 cm, el resto con cuñas
        self.assertEqual(pr["types"], [{"type": "P01", "count": pr["count"], "length": 2.8}])

    def test_props_that_do_not_fit_are_reported(self):
        ev = cut(ground_level=2.0)
        pr = ev.report["cutlist"]["props"]
        self.assertGreater(pr["skipped"], 0)
        self.assertTrue(all(q.top[2] - q.length == 2.0 for q in ev.cutlist.props))
        self.assertIn("CUTLIST_PROPS", {c["code"] for c in ev.report["checks"]})


class TestStockAndTypes(unittest.TestCase):
    def test_best_fit_decreasing(self):
        st = stock_plan([3.0, 3.0, 1.0, 1.0, 2.0, 2.0], 4.0)
        self.assertEqual((st["count"], st["splices"], st["pieces_over"]), (3, 0, 0))
        self.assertAlmostEqual(st["waste"], 0.0)

    def test_long_pieces_are_spliced(self):
        st = stock_plan([9.0, 0.5], 4.0)
        self.assertEqual((st["count"], st["splices"], st["pieces_over"]), (3, 2, 1))   # 4 + 4 + (1 + 0.5)
        self.assertAlmostEqual(st["waste"], 12.0 - 9.5)

    def test_packing_bounds(self):
        lengths = [0.37 * (i % 11) + 0.4 for i in range(200)]
        st = stock_plan(lengths, 4.0)
        self.assertGreaterEqual(st["count"], math.ceil(sum(lengths) / 4.0))
        self.assertLessEqual(st["count"], math.ceil(sum(lengths) / 4.0) * 1.25)

    def test_turned_or_flipped_board_is_the_same_type(self):
        self.assertEqual(_canonical_skew((12.2, -3.0)), _canonical_skew((-3.0, 12.2)))
        self.assertEqual(_canonical_skew((12.2, -3.0)), _canonical_skew((-12.2, 3.0)))
        self.assertNotEqual(_canonical_skew((10.0, 10.0)), _canonical_skew((10.0, -10.0)))  # paralelogramo ≠ trapecio
        self.assertEqual(_canonical_skew((-90.0, 0.0)), _canonical_skew((90.0, 0.0)))


class TestOtherShells(unittest.TestCase):
    def test_umbrella_symmetry_and_crease_joist(self):
        ev = evaluate({"shell_type": "UMBRELLA", **UMBRELLA["INVERTED"][2], "res_x": 4, "res_y": 4,
                       "formwork_enabled": True})
        rep = ev.report["cutlist"]
        self.assertTrue(all(t["count"] % 4 == 0 for t in rep["boards"]["types"]))
        on_crease = [pc for pc in ev.cutlist.joists if abs(pc.start[0]) < 1e-9 and abs(pc.end[0]) < 1e-9]
        self.assertEqual(len(on_crease), 2)                               # una por lado, no duplicada
        self.assertLess(rep["boards"]["gap_max"], rep["tolerance"])

    def test_radial_vaults_with_curved_and_cut_edges(self):
        for key, (_l, _d, params) in RADIAL.items():
            ev = evaluate({"shell_type": "RADIAL", **params, "res_x": 4, "res_y": 4, "formwork_enabled": True})
            cl = ev.cutlist
            self.assertGreater(len(cl.boards), 0, key)
            for pc in cl.boards + cl.joists:
                self.assertTrue(all(-90.0 < s <= 90.0 for s in pc.skew), (key, pc.skew))
                self.assertGreaterEqual(pc.cut_length, pc.length - 1e-12)
            self.assertGreaterEqual(ev.report["cutlist"]["boards"]["gap_min"], -1e-9, key)
            self.assertNotIn("CUTLIST_INVALID", {c["code"] for c in ev.report["checks"]})

    def test_exactness_follows_the_intrados(self):
        exact = cut(thickness_reference="MID", thickness_mode="VERTICAL").report["cutlist"]
        self.assertTrue(exact["exact"])
        self.assertEqual(exact["boards"]["deviation_max"], 0.0)
        ev = cut(thickness_reference="MID", thickness=0.08)
        approx = ev.report["cutlist"]
        self.assertFalse(approx["exact"])
        self.assertGreater(approx["boards"]["deviation_max"], 0.0)
        self.assertLess(approx["boards"]["deviation_max"], 0.04)
        self.assertIn("CUTLIST_APPROX", {c["code"] for c in ev.report["checks"]})


class TestIntegration(unittest.TestCase):
    def test_disabled_by_default(self):
        ev = evaluate({**HYPAR, "formwork_enabled": False})
        self.assertIsNone(ev.cutlist)
        self.assertIsNone(ev.report["cutlist"])
        self.assertIsNone(ev.formwork_mesh)

    def test_display_mesh_is_closed_and_measures_the_wood(self):
        ev = cut()
        md = ev.formwork_mesh
        cl = ev.cutlist
        self.assertEqual(len(md.vertices), 4 * sum(min(32, math.ceil(pc.twist / 3.0)) + 1 for pc in cl.boards)
                         + 8 * (len(cl.joists) + len(cl.props)))
        topo = topology_report(md)
        self.assertTrue(topo["closed"] and topo["consistent"])
        expected = ev.report["cutlist"]["wood_volume"] + sum(q.length for q in cl.props) * 0.075 ** 2
        self.assertAlmostEqual(mesh_volume(md) / expected, 1.0, delta=0.01)
        self.assertIsNone(cut(formwork_show=False).formwork_mesh)

    def test_invalid_parameters_become_a_check(self):
        rep = cut(formwork_board_width=0.0).report
        self.assertIsNone(rep["cutlist"])
        self.assertIn("CUTLIST_INVALID", {c["code"] for c in rep["checks"] if c["level"] == "ERROR"})

    def test_grid_uses_one_set(self):
        params = {"shell_type": "UMBRELLA", "res_x": 4, "res_y": 4, "formwork_enabled": True,
                  "grid_count_x": 3, "grid_count_y": 2, "grid_step_y": 0.5}
        ev = evaluate(params)
        mod = ev.module.report["cutlist"]
        rep = ev.report["cutlist"]
        self.assertEqual(rep["uses"], 6)
        self.assertEqual(rep["level_range"], [0.0, 0.5])
        self.assertEqual(rep["boards"]["count"], mod["boards"]["count"])
        self.assertAlmostEqual(rep["wood_volume"], mod["wood_volume"])
        self.assertEqual(len(ev.formwork_mesh.vertices), len(ev.module.formwork_mesh.vertices))
        first = ev.placements[0]["offset"]
        xs = [v[0] for v in ev.formwork_mesh.vertices]
        self.assertAlmostEqual(0.5 * (min(xs) + max(xs)), first[0], delta=0.5)  # en el primer módulo
        self.assertIn("CUTLIST_LEVELS", {c["code"] for c in ev.report["checks"]})

    def test_exports(self):
        ev = cut()
        doc = json.loads(json.dumps(build_document(ev, include_mesh=False)))
        cl = ev.cutlist
        self.assertEqual(len(doc["cutlist"]["pieces"]), len(cl.boards) + len(cl.joists))
        self.assertEqual(len(doc["cutlist"]["props_list"]), len(cl.props))
        rows = list(csv.reader(io.StringIO(cutlist_csv(ev)), delimiter=";"))
        ids = {r[0] for r in rows if r and "-" in r[0] and r[0][0] in "TVP" and r[0][1:3].isdigit()}
        self.assertEqual(len(ids), len(cl.boards) + len(cl.joists) + len(cl.props))
        first = next(r for r in rows if r and r[0] == cl.boards[0].id)
        self.assertEqual(float(first[5].replace(",", ".")), round(cl.boards[0].length, 3))
        dot = list(csv.reader(io.StringIO(cutlist_csv(ev, decimal=".")), delimiter=","))
        self.assertTrue(any(r and r[0] == cl.boards[0].id for r in dot))


if __name__ == "__main__":
    unittest.main()
