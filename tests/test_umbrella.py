"""Pruebas del paraguas de cuatro hypars (sin Blender).

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
from core.meshing import mesh_volume, topology_report  # noqa: E402
from core.metrics import model_integrals  # noqa: E402
from core.model import build_model  # noqa: E402
from core.pipeline import evaluate, resolve_params  # noqa: E402
from core.umbrella import PRESETS, umbrella_solid  # noqa: E402


def umbrella(**kw):
    return build_model("UMBRELLA", resolve_params({"shell_type": "UMBRELLA", **kw}))


def codes(report):
    return {c["code"] for c in report["checks"]}


class TestUmbrellaGeometry(unittest.TestCase):
    P = dict(span_x=12.0, span_y=8.0, umbrella_center_height=4.0, umbrella_edge_height=5.2)

    def test_corner_and_edge_heights(self):
        m = umbrella(**self.P)
        a, b = 6.0, 4.0
        for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
            s = m.patch_at(sx * 0.5 * a, sy * 0.5 * b).surface
            self.assertTrue(s.is_hyperbolic())
            self.assertAlmostEqual(s.z(0, 0), 4.0, places=12)                 # cabeza de columna
            self.assertAlmostEqual(s.z(sx * a, 0), 5.2, places=12)            # punto medio de borde
            self.assertAlmostEqual(s.z(0, sy * b), 5.2, places=12)
            self.assertAlmostEqual(s.z(sx * a, sy * b), 5.2, places=12)       # esquina
            for t in (0.2, 0.5, 0.9):                                         # borde exterior horizontal
                self.assertAlmostEqual(s.z(sx * a, sy * t * b), 5.2, places=12)
                self.assertAlmostEqual(s.z(sx * t * a, sy * b), 5.2, places=12)

    def test_valleys_are_shared_straight_rulings(self):
        m = umbrella(**self.P, tilt_x=math.radians(7), tilt_y=math.radians(-4))
        for cr in m.creases:
            pa, pb = m.patches[cr.patch_a].surface, m.patches[cr.patch_b].surface
            (x0, y0), (x1, y1) = cr.start, cr.end
            d = ((x1 - x0) / math.dist(cr.start, cr.end), (y1 - y0) / math.dist(cr.start, cr.end))
            self.assertAlmostEqual(pa.quadratic_form(d), 0.0, places=14)
            self.assertAlmostEqual(pb.quadratic_form(d), 0.0, places=14)
            for t in (0.0, 0.3, 0.71, 1.0):
                x, y = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
                self.assertAlmostEqual(pa.z(x, y), pb.z(x, y), places=12)   # continuidad C0
                self.assertAlmostEqual(pa.z(x, y), pa.z(x0, y0) + t * (pa.z(x1, y1) - pa.z(x0, y0)), places=12)

    def test_tilt_keeps_rulings_parallel_to_axes(self):
        m = umbrella(**self.P, tilt_x=math.radians(9))
        for p in m.patches:
            dirs = sorted(round(math.degrees(math.atan2(d[1], d[0])), 9) for d in p.surface.asymptotic_directions())
            self.assertEqual(dirs, [0.0, 90.0])

    def test_inverted_has_valleys_upright_has_ridges(self):
        inv = evaluate({"shell_type": "UMBRELLA", **PRESETS["INVERTED"][2], "res_x": 4, "res_y": 4}).report
        upr = evaluate({"shell_type": "UMBRELLA", **PRESETS["UPRIGHT"][2], "res_x": 4, "res_y": 4}).report
        self.assertEqual((inv["geometry"]["creases"]["valleys"], inv["geometry"]["creases"]["ridges"]), (4, 0))
        self.assertEqual((upr["geometry"]["creases"]["valleys"], upr["geometry"]["creases"]["ridges"]), (0, 4))

    def test_flat_umbrella_reported(self):
        rep = evaluate({"shell_type": "UMBRELLA", "umbrella_center_height": 5.0, "umbrella_edge_height": 5.0,
                        "res_x": 3, "res_y": 3}).report
        self.assertIn("PLANAR", codes(rep))


class TestUmbrellaMesh(unittest.TestCase):
    def test_welded_closed_and_exact(self):
        for mode in ("NORMAL", "VERTICAL"):
            m = umbrella(span_x=12.0, span_y=8.0, thickness=0.1, thickness_mode=mode, tilt_y=math.radians(5))
            md = umbrella_solid(m, 5, 4)
            topo = topology_report(md)
            self.assertTrue(topo["closed"] and topo["consistent"], mode)
            self.assertEqual(len(md.vertices), 2 * 11 * 9)  # una sola rejilla soldada
        m = umbrella(span_x=12.0, span_y=8.0, thickness=0.1, thickness_mode="VERTICAL")
        self.assertAlmostEqual(mesh_volume(umbrella_solid(m, 5, 4)), 0.1 * 96, places=9)
        self.assertAlmostEqual(model_integrals(m, 0.8)["volume"], 0.1 * 96, places=9)

    def test_chord_error_is_zero(self):
        rep = evaluate({"shell_type": "UMBRELLA", "res_x": 3, "res_y": 5, "tilt_x": 0.1}).report
        self.assertLess(rep["mesh"]["chord_error_max"], 1e-12)

    def test_miter_correction_improves_volume(self):
        for ref in ("INTRADOS", "EXTRADOS"):
            m = umbrella(thickness=0.3, thickness_reference=ref, umbrella_center_height=3.0,
                         umbrella_edge_height=5.5)
            integ = model_integrals(m, 0.8)
            corr = integ["creases"]["volume"]
            v_mesh = mesh_volume(umbrella_solid(m, 40, 40))
            err_with = abs(v_mesh - integ["volume"]) / integ["volume"]
            err_without = abs(v_mesh - (integ["volume"] - corr)) / integ["volume"]
            self.assertGreater(abs(corr) / integ["volume"], 0.005)
            self.assertLess(err_with, 0.2 * err_without, ref)


class TestUmbrellaPipeline(unittest.TestCase):
    def test_column_beams_rulings_export(self):
        ev = evaluate({"shell_type": "UMBRELLA", "res_x": 6, "res_y": 6, "ruling_spacing": 1.0,
                       "beam_xmin": True, "beam_xmax": True, "beam_ymin": True, "beam_ymax": True,
                       "beam_width": 0.2, "beam_depth": 0.3})
        rep = ev.report
        self.assertEqual([s["name"] for s in rep["supports"]], ["C"])
        self.assertAlmostEqual(rep["supports"][0]["position"][2], 4.0, places=9)  # intradós en la columna
        band = 100.0 - 9.6 * 9.6
        self.assertAlmostEqual(rep["concrete"]["volume_beams"], 0.3 * band, places=9)
        self.assertAlmostEqual(mesh_volume(ev.beams_mesh), 0.3 * band, places=6)
        topo = topology_report(ev.beams_mesh)
        self.assertTrue(topo["closed"] and topo["consistent"])
        # generatrices alineadas a través de las limas y sin duplicados sobre ellas
        ends = [tuple(sorted((tuple(round(c, 6) for c in r.points[0]), tuple(round(c, 6) for c in r.points[-1]))))
                for r in ev.display_rulings]
        self.assertEqual(len(ends), len(set(ends)))
        ys = {round(r.points[0][1], 9) for r in ev.display_rulings if r.family == 0}
        self.assertIn(0.0, ys)                                   # la lima del eje x es una generatriz
        doc = json.loads(json.dumps(build_document(ev)))
        self.assertEqual(len(doc["patches"]), 4)
        self.assertIn("axis_x", doc["midsurface"]["node_sets"])
        self.assertEqual(doc["midsurface"]["support_nodes"]["C"], doc["midsurface"]["node_sets"]["center"][0])
        again = evaluate(doc["parameters"])
        self.assertAlmostEqual(again.report["concrete"]["volume_shell"], rep["concrete"]["volume_shell"])

    def test_column_never_crosses_the_shell(self):
        """Regresión: la cabeza plana de la columna asomaba por encima de una cumbrera."""
        from core.elements import intrados_height
        cases = [{"shell_type": "UMBRELLA", **PRESETS[k][2]} for k in PRESETS]
        cases.append({"shell_type": "RADIAL", "radial_supports": "CENTER", "support_type": "COLUMN",
                      "support_size": 0.6})
        for params in cases:
            ev = evaluate({**params, "res_x": 6, "res_y": 6})
            self.assertIsNotNone(ev.supports_mesh)
            for x, y, z in ev.supports_mesh.vertices:
                h = intrados_height(ev.model, x, y)
                if h is not None:
                    self.assertLessEqual(z, h + 1e-9, (params.get("shell_type"), x, y))

    def test_presets_without_warnings(self):
        for key, (_label, _desc, params) in PRESETS.items():
            rep = evaluate({"shell_type": "UMBRELLA", **params}).report
            self.assertTrue(rep["mesh"]["closed"] and rep["mesh"]["consistent"], key)
            self.assertFalse([c for c in rep["checks"] if c["level"] != "INFO"], (key, rep["checks"]))


if __name__ == "__main__":
    unittest.main()
