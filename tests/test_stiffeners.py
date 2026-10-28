"""Pruebas de los rigidizadores: nervios en las limas y viga del borde libre (sin Blender).

    python -m unittest discover -s tests -v
"""

import json
import math
import os
import sys
import unittest

ADDON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "candelalab")
sys.path.insert(0, ADDON_DIR)

from core.elements import intrados_height  # noqa: E402
from core.export import build_document  # noqa: E402
from core.fea import GRAVITY, fe_model_from_params  # noqa: E402
from core.meshing import PART_EXTRADOS, PART_INTRADOS, mesh_volume, topology_report  # noqa: E402
from core.pipeline import evaluate  # noqa: E402
from core.radial import PRESETS as RADIAL  # noqa: E402

W, D = 0.2, 0.3
UMB = {"shell_type": "UMBRELLA", "span_x": 10.0, "span_y": 8.0, "res_x": 6, "res_y": 6,
       "rib_creases": True, "rib_width": W, "rib_depth": D}
DOME = {"shell_type": "RADIAL", **RADIAL["DOME"][2], "res_x": 6, "res_y": 6}


def solid_ok(md):
    topo = topology_report(md)
    return topo["closed"] and topo["consistent"]


class TestCreaseRibs(unittest.TestCase):
    def test_umbrella_ribs_volume_and_mesh(self):
        ev = evaluate(UMB)
        a, b = 5.0, 4.0
        # Cruz de dos franjas de ancho W que se cortan en un cuadrado W × W bajo la columna
        expected = D * (W * (2 * a + 2 * b) - W * W)
        self.assertAlmostEqual(ev.report["concrete"]["volume_ribs"], expected, places=12)
        self.assertAlmostEqual(ev.report["formwork"]["rib_plan_area"], expected / D, places=12)
        self.assertTrue(solid_ok(ev.ribs_mesh))
        self.assertAlmostEqual(mesh_volume(ev.ribs_mesh), expected, places=9)
        base = evaluate({**UMB, "rib_creases": False}).report["concrete"]
        self.assertAlmostEqual(ev.report["concrete"]["volume_total"], base["volume_total"] + expected, places=12)

    def test_top_follows_intrados_and_bottom_is_depth_below(self):
        ev = evaluate(UMB)
        md = ev.ribs_mesh
        tops = {v for f, part in zip(md.faces, md.face_parts) if part == PART_EXTRADOS for v in f}
        bottoms = {v for f, part in zip(md.faces, md.face_parts) if part == PART_INTRADOS for v in f}
        for v in tops:
            x, y, z = md.vertices[v]
            self.assertAlmostEqual(z, intrados_height(ev.model, x, y), places=12)
        self.assertEqual(len(tops), len(bottoms))
        zb = sorted(md.vertices[v][2] for v in bottoms)
        zt = sorted(md.vertices[v][2] for v in tops)
        self.assertAlmostEqual(zb[0], zt[0] - D, places=12)

    def test_radial_hub_trimmed_by_bisectors(self):
        p = {**DOME, "rib_creases": True, "rib_width": W, "rib_depth": D}
        ev = evaluate(p)
        n, r = p["radial_count"], p["radial_radius"]
        h = W / 2
        t0 = h / math.tan(math.pi / n)                    # empieza el ancho completo
        lout = math.sqrt(r * r - h * h)                   # la arista exterior termina en el arco
        half = 0.5 * h * (r + lout - t0)                  # trapecio (0,0) (r,0) (lout,h) (t0,h)
        self.assertAlmostEqual(ev.report["formwork"]["rib_plan_area"], 2 * n * half, places=9)
        self.assertTrue(solid_ok(ev.ribs_mesh))
        self.assertAlmostEqual(mesh_volume(ev.ribs_mesh), ev.report["concrete"]["volume_ribs"], places=9)

    def test_other_vaults_and_limits(self):
        for key, (_l, _d, params) in RADIAL.items():
            ev = evaluate({"shell_type": "RADIAL", **params, "res_x": 4, "res_y": 4, "rib_creases": True})
            self.assertTrue(solid_ok(ev.ribs_mesh), key)
            self.assertGreater(ev.report["concrete"]["volume_ribs"], 0.0)
        hyp = evaluate({"shell_type": "HYPAR", "res_x": 4, "res_y": 4, "rib_creases": True}).report
        self.assertEqual(hyp["concrete"]["volume_ribs"], 0.0)
        self.assertIn("RIBS_NO_CREASES", {c["code"] for c in hyp["checks"]})
        bad = evaluate({**UMB, "rib_width": 3.0}).report
        self.assertIn("RIBS_INVALID", {c["code"] for c in bad["checks"] if c["level"] == "ERROR"})
        self.assertIn("RIBS", {c["code"] for c in evaluate(UMB).report["checks"]})


class TestFreeEdgeBeam(unittest.TestCase):
    def test_circular_edge_beam_is_an_annulus(self):
        ev = evaluate({**DOME, "beam_free_edge": True, "beam_width": W, "beam_depth": D})
        r = DOME["radial_radius"]
        expected = D * math.pi * (r * r - (r - W) ** 2)
        self.assertAlmostEqual(ev.report["concrete"]["volume_beams"] / expected, 1.0, delta=1e-5)
        self.assertTrue(solid_ok(ev.beams_mesh))
        self.assertAlmostEqual(mesh_volume(ev.beams_mesh) / expected, 1.0, delta=1e-3)   # malla más gruesa
        self.assertNotIn("BEAMS_UNSUPPORTED", {c["code"] for c in ev.report["checks"]})

    def test_polygonal_and_inclined_edges_are_cut_at_the_groins(self):
        for key in ("GROINED", "LOBES"):
            p = {"shell_type": "RADIAL", **RADIAL[key][2], "res_x": 4, "res_y": 4, "beam_free_edge": True}
            ev = evaluate(p)
            self.assertTrue(solid_ok(ev.beams_mesh), key)
            self.assertGreater(ev.report["concrete"]["volume_beams"], 0.0)
            # Los extremos interiores de cada tramo están sobre las limas (radios del sector)
            from core.elements import _edge_band
            for patch in ev.model.patches:
                dom = patch.domain
                _outer, inner = _edge_band(dom, p.get("beam_width", 0.2), 8)
                for end, ray in ((inner[0], dom.a), (inner[-1], dom.b)):
                    cross = (ray[0] - dom.center[0]) * (end[1] - dom.center[1]) - \
                        (ray[1] - dom.center[1]) * (end[0] - dom.center[0])
                    self.assertAlmostEqual(cross, 0.0, places=9)

    def test_square_plan_gives_a_mitred_frame(self):
        # Bóveda por arista de 4 lóbulos sobre planta cuadrada de 30 m: la viga es un marco de ancho W
        p = {"shell_type": "RADIAL", **RADIAL["GROINED"][2], "res_x": 4, "res_y": 4, "beam_free_edge": True,
             "beam_width": W, "beam_depth": D}
        side = 2 * p["radial_radius"] / math.sqrt(2)
        ev = evaluate(p)
        self.assertAlmostEqual(ev.report["formwork"]["beam_plan_area"], side ** 2 - (side - 2 * W) ** 2, places=9)
        self.assertAlmostEqual(mesh_volume(ev.beams_mesh), ev.report["concrete"]["volume_beams"], places=9)

    def test_rectangular_toggles_do_not_apply_to_vaults(self):
        rep = evaluate({**DOME, "beam_xmin": True}).report
        self.assertEqual(rep["concrete"]["volume_beams"], 0.0)
        self.assertIn("BEAMS_UNSUPPORTED", {c["code"] for c in rep["checks"]})
        rep = evaluate({**UMB, "beam_free_edge": True}).report
        self.assertIn("BEAMS_UNSUPPORTED", {c["code"] for c in rep["checks"]})


class TestIntegration(unittest.TestCase):
    def test_grid_scales_and_instances(self):
        single = evaluate(UMB).report["concrete"]["volume_ribs"]
        real = evaluate({**UMB, "grid_count_x": 3, "grid_count_y": 2, "grid_instancing": "REAL"})
        inst = evaluate({**UMB, "grid_count_x": 3, "grid_count_y": 2, "grid_instancing": "INSTANCES"})
        for ev in (real, inst):
            self.assertAlmostEqual(ev.report["concrete"]["volume_ribs"], 6 * single, places=9)
        self.assertEqual(len(real.ribs_mesh.vertices), 6 * len(inst.ribs_mesh.vertices))

    def test_export_document(self):
        ev = evaluate({**DOME, "rib_creases": True, "beam_free_edge": True})
        doc = json.loads(json.dumps(build_document(ev, include_mesh=False)))
        self.assertEqual(len(doc["stiffeners"]["crease_ribs"]["creases"]), DOME["radial_count"])
        self.assertIn("free_edge_beam", doc["stiffeners"])

    def test_fea_webs_under_creases(self):
        p = {**UMB, "thickness_mode": "VERTICAL", "thickness": 0.05, "support_type": "COLUMN"}
        fe, rep = fe_model_from_params(p)
        sets = [b[1] for b in fe.blocks if b[1].startswith("ENERVIO_")]
        self.assertEqual(sorted(sets), ["ENERVIO_AXIS_X", "ENERVIO_AXIS_Y"])
        self.assertEqual(fe.summary["components"], 1)
        self.assertEqual(fe.summary["rigid_body_modes"], 0)
        # Almas verticales bajo los ejes, de altura t/2 + D: peso = ρ·g·W·(t/2 + D)·(2a + 2b)
        expected = 2400 * GRAVITY * W * (0.025 + D) * (10.0 + 8.0) / 1000.0
        self.assertAlmostEqual(fe.summary["weight_kN"]["ribs"], expected, places=6)
        for etype, name, conns in fe.blocks:
            if name == "ENERVIO_AXIS_X":
                self.assertTrue(all(abs(fe.nodes[v][1]) < 1e-12 for c in conns for v in c))   # plano y = 0
        self.assertIn("nervios", next(c["message"] for c in fe.checks if c["code"] == "FEA_WEIGHT"))

    def test_fea_free_edge_web_closes_the_ring(self):
        fe, _rep = fe_model_from_params({**DOME, "beam_free_edge": True, "rib_creases": True})
        names = {b[1] for b in fe.blocks}
        self.assertIn("EVIGA_BORDE", names)
        self.assertEqual(len([n for n in names if n.startswith("ENERVIO_GROIN")]), DOME["radial_count"])
        ring = next(c for t, n, c in fe.blocks if n == "EVIGA_BORDE")
        self.assertEqual(len(ring), DOME["radial_count"] * DOME["res_y"])          # anillo completo
        self.assertEqual(fe.summary["components"], 1)


if __name__ == "__main__":
    unittest.main()
