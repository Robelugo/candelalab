"""Pruebas de los capiteles (engrosamientos en los apoyos), sin Blender.

    python -m unittest discover -s tests -v
"""

import json
import math
import os
import sys
import unittest

ADDON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "candelalab")
sys.path.insert(0, ADDON_DIR)

from core.capitals import capital_drop  # noqa: E402
from core.elements import intrados_height  # noqa: E402
from core.export import build_document  # noqa: E402
from core.fea import GRAVITY, fe_model_from_params  # noqa: E402
from core.meshing import PART_EXTRADOS, PART_INTRADOS, mesh_volume, topology_report  # noqa: E402
from core.pipeline import evaluate  # noqa: E402

R, D = 1.0, 0.10
CAP = {"capital_enabled": True, "capital_radius": R, "capital_extra": D}
UMB = {"shell_type": "UMBRELLA", "support_type": "COLUMN", "res_x": 12, "res_y": 12}


def face_vertices(md, part):
    return {v for f, q in zip(md.faces, md.face_parts) if q == part for v in f}


class TestVolume(unittest.TestCase):
    def test_closed_forms_with_vertical_thickness(self):
        disk = math.pi * D * R * R / 3          # ∫ Δ(1 − r²/R²)² dA sobre el disco
        cases = ((UMB, disk),                                                     # columna interior
                 ({"shell_type": "HYPAR", "res_x": 8, "res_y": 8}, 2 * disk / 4),  # 2 esquinas: cuartos
                 ({"shell_type": "HYPAR", "res_x": 8, "res_y": 8, "support_layout": "LOW_EDGE_MIDPOINTS"},
                  2 * disk / 2))                                                  # 2 centros de borde: mitades
        for params, expected in cases:
            rep = evaluate({**params, **CAP, "thickness_mode": "VERTICAL"}).report
            self.assertAlmostEqual(rep["concrete"]["volume_capitals"], expected, places=9, msg=params)
            base = evaluate({**params, "thickness_mode": "VERTICAL"}).report["concrete"]["volume_total"]
            self.assertAlmostEqual(rep["concrete"]["volume_total"], base + expected, places=9)

    def test_mesh_converges_to_analytic_with_normal_thickness(self):
        # Incluye la cuña de inglete de las limas bajo el capitel (sin ella la malla se aleja).
        on = evaluate({**UMB, **CAP, "res_x": 64, "res_y": 64})
        off = evaluate({**UMB, "res_x": 64, "res_y": 64})
        extra = mesh_volume(on.shell_mesh) - mesh_volume(off.shell_mesh)
        self.assertAlmostEqual(extra / on.report["concrete"]["volume_capitals"], 1.0, delta=2e-3)
        self.assertLess(abs(on.report["mesh"]["volume_rel_error"]), 1e-3)


class TestGeometry(unittest.TestCase):
    def test_intrados_drops_and_extrados_stays(self):
        p = {**UMB, "thickness_mode": "VERTICAL"}
        on, off = evaluate({**p, **CAP}).shell_mesh, evaluate(p).shell_mesh
        self.assertTrue(topology_report(on)["closed"] and topology_report(on)["consistent"])
        for v in face_vertices(on, PART_EXTRADOS):
            self.assertEqual(on.vertices[v], off.vertices[v])
        for v in face_vertices(on, PART_INTRADOS):
            x, y = on.plan_points[v]
            drop = D * max(0.0, 1 - (x * x + y * y) / (R * R)) ** 2
            self.assertAlmostEqual(off.vertices[v][2] - on.vertices[v][2], drop, places=12)

    def test_columns_ribs_and_beams_hang_from_the_capital(self):
        ev = evaluate({**UMB, **CAP, "rib_creases": True, "beam_xmin": True, "thickness_mode": "VERTICAL"})
        top = max(v[2] for v in ev.supports_mesh.vertices)
        self.assertAlmostEqual(top, intrados_height(ev.model, 0.0, 0.0), places=9)      # bajo el capitel
        self.assertAlmostEqual(intrados_height(ev.model, 0.0, 0.0),
                               intrados_height(evaluate(UMB).model, 0.0, 0.0) - D, places=12)
        for v in face_vertices(ev.ribs_mesh, PART_EXTRADOS):
            x, y, z = ev.ribs_mesh.vertices[v]
            self.assertAlmostEqual(z, intrados_height(ev.model, x, y), places=12)
        self.assertGreater(capital_drop(ev.model, 0.2, 0.1), 0.0)
        self.assertEqual(capital_drop(ev.model, 2.0, 0.0), 0.0)

    def test_checks_and_limits(self):
        coarse = evaluate({**UMB, **CAP, "res_x": 4, "res_y": 4}).report
        self.assertIn("CAPITAL_COARSE", {c["code"] for c in coarse["checks"]})
        self.assertIn("CAPITALS", {c["code"] for c in coarse["checks"]})
        overlap = evaluate({"shell_type": "HYPAR", "res_x": 8, "res_y": 8, "support_layout": "ALL_CORNERS",
                            **CAP, "capital_radius": 6.0}).report
        self.assertIn("CAPITAL_INVALID", {c["code"] for c in overlap["checks"] if c["level"] == "ERROR"})
        self.assertEqual(overlap["concrete"]["volume_capitals"], 0.0)
        free = evaluate({"shell_type": "HYPAR", "res_x": 8, "res_y": 8, "support_layout": "NONE", **CAP}).report
        self.assertIn("CAPITAL_NO_SUPPORTS", {c["code"] for c in free["checks"]})
        cim = evaluate({**UMB, **CAP, "formwork_enabled": True, "formwork_show": False}).report
        self.assertIn("CAPITAL_FORMWORK", {c["code"] for c in cim["checks"]})

    def test_grid_and_export(self):
        single = evaluate({**UMB, **CAP}).report["concrete"]["volume_capitals"]
        grid = evaluate({**UMB, **CAP, "grid_count_x": 3, "grid_count_y": 2}).report
        self.assertAlmostEqual(grid["concrete"]["volume_capitals"], 6 * single, places=9)
        doc = json.loads(json.dumps(build_document(evaluate({**UMB, **CAP}), include_mesh=False)))
        self.assertEqual(len(doc["stiffeners"]["capitals"]["items"]), 1)


class TestFea(unittest.TestCase):
    def test_nodes_move_to_the_new_mid_surface(self):
        p = {**UMB, **CAP, "thickness_mode": "VERTICAL", "thickness": 0.05}
        fe, rep = fe_model_from_params(p)
        plain, _ = fe_model_from_params({**UMB, "thickness_mode": "VERTICAL", "thickness": 0.05})
        center = next(v for v, xy in fe.plan.items() if abs(xy[0]) < 1e-12 and abs(xy[1]) < 1e-12)
        self.assertAlmostEqual(plain.nodes[center][2] - fe.nodes[center][2], D / 2, places=12)
        # Espesor vertical: el real por nodo es (t + Δt)·cos α (el centro del paraguas no es horizontal)
        self.assertAlmostEqual(fe.nodal_thickness[center], plain.nodal_thickness[center] * (0.05 + D) / 0.05,
                               places=12)
        self.assertAlmostEqual(fe.summary["weight_kN"]["shell"] / (
            (rep["concrete"]["volume_shell"] + rep["concrete"]["volume_capitals"]) * 2400 * GRAVITY / 1000), 1.0,
            delta=5e-3)

    def test_normal_thickness_gets_nodal_thickness(self):
        fe, _rep = fe_model_from_params({**UMB, **CAP})
        self.assertTrue(fe.nodal_thickness)
        self.assertAlmostEqual(max(fe.nodal_thickness.values()), 0.04 + D, places=9)
        self.assertAlmostEqual(min(fe.nodal_thickness.values()), 0.04, places=12)


if __name__ == "__main__":
    unittest.main()
