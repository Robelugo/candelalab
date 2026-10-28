"""Pruebas de la retícula modular (sin Blender).

    python -m unittest discover -s tests -v
"""

import json
import math
import os
import sys
import unittest

ADDON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "candelalab")
sys.path.insert(0, ADDON_DIR)

from core.export import build_document  # noqa: E402
from core.grid import PRESETS  # noqa: E402
from core.meshing import mesh_volume, topology_report  # noqa: E402
from core.pipeline import evaluate  # noqa: E402

UMB = {"shell_type": "UMBRELLA", "res_x": 4, "res_y": 4, "support_type": "COLUMN"}


def grid(**kw):
    return evaluate({**UMB, **kw})


class TestPlacements(unittest.TestCase):
    def test_single_module_is_not_a_grid(self):
        ev = grid()
        self.assertNotIn("grid", ev.report)
        self.assertEqual(ev.placements, [])

    def test_offsets_are_centered_with_pitch(self):
        ev = grid(span_x=10, span_y=8, grid_count_x=3, grid_count_y=2, grid_gap_x=0.5, grid_gap_y=0.2)
        offs = sorted((round(pl["offset"][0], 9), round(pl["offset"][1], 9)) for pl in ev.placements)
        self.assertEqual(offs, sorted((x, y) for x in (-10.5, 0.0, 10.5) for y in (-4.1, 4.1)))
        self.assertEqual(ev.report["grid"]["pitch"], [10.5, 8.2])
        self.assertAlmostEqual(ev.report["grid"]["footprint"][0], 2 * 10.5 + 10, places=9)

    def test_patterns(self):
        for pattern, expected in (("UNIFORM", {(False, False)}), ("MIRROR_X", {(False, False), (True, False)}),
                                  ("MIRROR_XY", {(False, False), (True, False), (False, True), (True, True)})):
            ev = grid(grid_count_x=2, grid_count_y=2, grid_pattern=pattern)
            self.assertEqual({pl["mirror"] for pl in ev.placements}, expected)


class TestTotalsAndMesh(unittest.TestCase):
    def test_totals_are_exact_multiples(self):
        ev = grid(grid_count_x=3, grid_count_y=2, tilt_x=0.12, grid_pattern="MIRROR_XY")
        base, rep = ev.module.report, ev.report
        for sec, key in (("concrete", "volume_shell"), ("concrete", "weight_kN"), ("geometry", "area_reference"),
                         ("formwork", "area_intrados"), ("geometry", "plan_area")):
            self.assertAlmostEqual(rep[sec][key], 6 * base[sec][key], places=9)
        self.assertEqual(rep["formwork"]["rulings"][0]["count"], 6 * base["formwork"]["rulings"][0]["count"])
        self.assertEqual(len(rep["supports"]), 6)

    def test_mirrored_copies_keep_outward_normals(self):
        ev = grid(grid_count_x=2, grid_count_y=2, tilt_x=0.12, tilt_y=-0.05, grid_pattern="MIRROR_XY")
        topo = topology_report(ev.shell_mesh)
        self.assertTrue(topo["closed"] and topo["consistent"])
        self.assertAlmostEqual(mesh_volume(ev.shell_mesh), 4 * mesh_volume(ev.module.shell_mesh), places=9)

    def test_mirror_reflects_geometry(self):
        ev = grid(grid_count_x=2, tilt_x=0.15, grid_pattern="MIRROR_X")
        left = [v for v in ev.shell_mesh.vertices if v[0] < 0]
        right = {(round(-x, 9), round(y, 9), round(z, 9)) for x, y, z in ev.shell_mesh.vertices if x > 0}
        self.assertTrue(all((round(x, 9), round(y, 9), round(z, 9)) in right for x, y, z in left))


class TestJointSteps(unittest.TestCase):
    def test_sawtooth_step_equals_2_a_tan_tilt(self):
        p = PRESETS["SAWTOOTH"][2]
        rep = grid(**p).report
        self.assertAlmostEqual(rep["grid"]["joint_step_x"], 2 * 6.0 * math.tan(math.radians(9.0)), places=12)
        self.assertAlmostEqual(rep["grid"]["joint_step_y"], 0.0, places=12)
        self.assertIn("banda de luz", next(c["message"] for c in rep["checks"] if c["code"] == "GRID"))

    def test_alternate_pattern_closes_steps(self):
        rep = grid(**PRESETS["ALTERNATE"][2]).report
        self.assertAlmostEqual(rep["grid"]["joint_step_x"], 0.0, places=12)

    def test_height_steps_and_columns_to_ground(self):
        ev = grid(grid_count_x=3, grid_step_x=0.7, ground_level=0.0)
        self.assertAlmostEqual(ev.report["grid"]["joint_step_x"], 0.7, places=12)
        zs = [v[2] for v in ev.supports_mesh.vertices]
        self.assertAlmostEqual(min(zs), 0.0, places=12)            # todas las columnas llegan al suelo
        tops = sorted({round(s["position"][2], 9) for s in ev.report["supports"]})
        self.assertEqual(tops, [4.0, 4.7, 5.4])


class TestGridPipeline(unittest.TestCase):
    def test_height_map_is_global(self):
        ev = grid(grid_count_x=2, grid_step_x=1.0, analysis="HEIGHT")
        zs = [v[2] for v in ev.shell_mesh.vertices]
        self.assertAlmostEqual(ev.report["analysis"]["min"], min(zs))
        self.assertAlmostEqual(ev.report["analysis"]["max"], max(zs))
        self.assertEqual(len(ev.analysis_colors), len(zs))

    def test_export_module_and_placements(self):
        ev = grid(grid_count_x=2, grid_count_y=3)
        doc = json.loads(json.dumps(build_document(ev)))
        self.assertEqual(len(doc["grid"]["placements"]), 6)
        self.assertEqual(len(doc["midsurface"]["nodes"]), 9 * 9)       # un módulo
        self.assertAlmostEqual(doc["report"]["concrete"]["volume_shell"],
                               6 * doc["module_report"]["concrete"]["volume_shell"])
        again = evaluate(doc["parameters"])
        self.assertAlmostEqual(again.report["concrete"]["volume_total"], ev.report["concrete"]["volume_total"])

    def test_works_for_other_shell_types(self):
        ev = evaluate({"shell_type": "RADIAL", "radial_count": 4, "radial_plan": "POLYGON",
                       "radial_radius": 15 * math.sqrt(2), "crown_height": 8, "edge_height": 6,
                       "res_x": 4, "res_y": 4, "grid_count_x": 3, "grid_gap_x": 0.0})
        self.assertAlmostEqual(ev.report["grid"]["pitch"][0], 30.0, places=9)  # bóvedas por arista en hilera
        topo = topology_report(ev.shell_mesh)
        self.assertTrue(topo["closed"] and topo["consistent"])
        hyp = evaluate({"shell_type": "HYPAR", "res_x": 4, "res_y": 4, "grid_count_y": 2,
                        "beam_xmin": True, "grid_pattern": "MIRROR_Y"})
        self.assertAlmostEqual(hyp.report["concrete"]["volume_beams"],
                               2 * hyp.module.report["concrete"]["volume_beams"], places=9)

    def test_presets_without_warnings(self):
        for key, (_l, _d, params) in PRESETS.items():
            rep = grid(**params).report
            self.assertTrue(rep["mesh"]["closed"] and rep["mesh"]["consistent"], key)
            self.assertFalse([c for c in rep["checks"] if c["level"] != "INFO"], (key, rep["checks"]))


class TestInstancing(unittest.TestCase):
    P = dict(grid_count_x=3, grid_count_y=2, tilt_x=0.12, grid_pattern="MIRROR_XY", grid_step_y=0.4,
             beam_xmin=True, beam_ymax=True)

    def test_instances_and_real_geometry_report_the_same(self):
        real = grid(**self.P, grid_instancing="REAL").report
        inst = grid(**self.P, grid_instancing="INSTANCES").report
        for sec, key in (("concrete", "volume_total"), ("concrete", "weight_kN"), ("geometry", "area_reference"),
                         ("formwork", "area_intrados"), ("geometry", "z_concrete_min"),
                         ("geometry", "z_concrete_max"), ("mesh", "display_vertices"), ("mesh", "display_faces")):
            self.assertAlmostEqual(real[sec][key], inst[sec][key], places=9, msg=(sec, key))
        self.assertEqual(real["supports"], inst["supports"])
        self.assertAlmostEqual(real["grid"]["joint_step_y"], inst["grid"]["joint_step_y"], places=12)
        self.assertEqual((real["grid"]["instanced"], inst["grid"]["instanced"]), (False, True))
        self.assertIn("GRID_INSTANCED", {c["code"] for c in inst["checks"]})

    def test_instanced_evaluation_carries_one_module(self):
        ev = grid(**self.P, grid_instancing="INSTANCES")
        self.assertEqual(len(ev.shell_mesh.vertices), len(ev.module.shell_mesh.vertices))
        self.assertEqual(len(ev.beams_mesh.vertices), len(ev.module.beams_mesh.vertices))
        self.assertEqual(len(ev.placements), 6)
        self.assertEqual(len(ev.supports_mesh.vertices), 6 * 8)   # columnas: geometría real, una por módulo

    def test_auto_threshold(self):
        small = grid(grid_count_x=2, grid_count_y=2).report["grid"]["instanced"]
        big = evaluate({"shell_type": "UMBRELLA", "res_x": 12, "res_y": 12, "grid_count_x": 10,
                        "grid_count_y": 10}).report
        self.assertFalse(small)
        self.assertTrue(big["grid"]["instanced"])                 # 100 × 1250 vértices > 50 000
        self.assertEqual(big["mesh"]["display_vertices"], 100 * 1250)

    def test_height_map_is_per_module_when_instanced(self):
        rep = grid(grid_count_x=2, grid_step_x=1.0, analysis="HEIGHT", grid_instancing="INSTANCES").report
        self.assertTrue(rep["analysis"]["per_module"])


if __name__ == "__main__":
    unittest.main()
