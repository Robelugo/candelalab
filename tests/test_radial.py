"""Pruebas de la bóveda radial de hypars (sin Blender).

    python -m unittest discover -s tests -v
"""

import json
import math
import os
import random
import sys
import unittest

ADDON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "candelalab")
sys.path.insert(0, ADDON_DIR)

from core.export import build_document  # noqa: E402
from core.geometry import SectorDomain  # noqa: E402
from core.meshing import mesh_volume, topology_report  # noqa: E402
from core.metrics import model_integrals  # noqa: E402
from core.model import build_model, miter_direction  # noqa: E402
from core.pipeline import evaluate, resolve_params  # noqa: E402
from core.quadrature import unit_square_rule  # noqa: E402
from core.radial import radial_solid  # noqa: E402


def radial_model(**kw):
    return build_model("RADIAL", resolve_params({"shell_type": "RADIAL", **kw}))


def codes(report):
    return {c["code"] for c in report["checks"]}


def polar(r, ang):
    return (r * math.cos(ang), r * math.sin(ang))


class TestSectorDomain(unittest.TestCase):
    def test_area_centroid_and_clip(self):
        cases = ((SectorDomain((0, 0), (3, -1), (3, 1)), 3.0),
                 (SectorDomain((0, 0), polar(2, -0.4), polar(2, 0.4), arc=True), 2.0))
        for dom, reach in cases:
            dom.validate()
            a = cx = 0.0
            for s, t, w in unit_square_rule(8, 8):
                j = dom.jacobian(s, t) * w
                a += j
                cx += dom.map(s, t)[0] * j
            self.assertAlmostEqual(a, dom.area, places=12)
            self.assertAlmostEqual(cx / a, dom.centroid[0], places=12)
            t0, t1 = dom.clip_line((0.0, 0.0), (1.0, 0.0))
            self.assertAlmostEqual(t0, 0.0, places=12)
            self.assertAlmostEqual(t1, reach, places=12)

    def test_projection_range_includes_arc_extreme(self):
        dom = SectorDomain((0, 0), polar(2, -0.4), polar(2, 0.4), arc=True)
        self.assertAlmostEqual(dom.projection_range((1.0, 0.0))[1], 2.0, places=12)

    def test_miter_direction_is_equidistant(self):
        rng = random.Random(3)
        for k in (2, 3, 5):
            ns = []
            for i in range(k):
                if k == 2:
                    v = (rng.uniform(-.5, .5), rng.uniform(-.5, .5), 1.0)
                else:
                    ang = 2 * math.pi * i / k
                    v = (0.4 * math.cos(ang), 0.4 * math.sin(ang), 1.0)
                ln = math.sqrt(sum(c * c for c in v))
                ns.append(tuple(c / ln for c in v))
            m = miter_direction(ns)
            for n in ns:  # a distancia 1 de todos los planos tangentes
                self.assertAlmostEqual(sum(a * b for a, b in zip(m, n)), 1.0, places=12)


class TestRadialVault(unittest.TestCase):
    def test_heights_and_hyperbolicity(self):
        for plan in ("CIRCLE", "POLYGON"):
            m = radial_model(radial_plan=plan, radial_count=7, crown_height=6.0, edge_height=2.0, support_height=0.5)
            beta = math.pi / 7
            x_tip = 10.0 if plan == "CIRCLE" else 10.0 * math.cos(beta)
            for k, patch in enumerate(m.patches):
                s = patch.surface
                self.assertTrue(s.is_hyperbolic())
                self.assertAlmostEqual(s.z(0, 0), 6.0, places=12)          # clave
                self.assertAlmostEqual(s.z(*patch.domain.a), 0.5, places=12)  # apoyo
                self.assertAlmostEqual(s.z(*polar(x_tip, 2 * math.pi * k / 7)), 2.0, places=12)  # borde

    def test_groins_are_shared_straight_rulings(self):
        m = radial_model(radial_count=8, edge_height=3.0)
        n = len(m.patches)
        for k in range(n):
            a, b = m.patches[(k - 1) % n].surface, m.patches[k].surface
            ex, ey = m.patches[k].domain.a
            d = (ex / math.hypot(ex, ey), ey / math.hypot(ex, ey))
            self.assertAlmostEqual(a.quadratic_form(d), 0.0, places=12)  # generatriz de ambos lóbulos
            self.assertAlmostEqual(b.quadratic_form(d), 0.0, places=12)
            for t in (0.0, 0.3, 0.77, 1.0):
                x, y = t * ex, t * ey
                self.assertAlmostEqual(a.z(x, y), b.z(x, y), places=12)  # continuidad C0
                self.assertAlmostEqual(b.z(x, y), b.z(0, 0) + t * (b.z(ex, ey) - b.z(0, 0)), places=12)  # recta

    def test_plan_area(self):
        for n in (3, 4, 8):
            mp = radial_model(radial_count=n, radial_plan="POLYGON", radial_radius=5.0)
            mc = radial_model(radial_count=n, radial_plan="CIRCLE", radial_radius=5.0)
            self.assertAlmostEqual(sum(p.domain.area for p in mp.patches),
                                   0.5 * n * 25 * math.sin(2 * math.pi / n), places=10)
            self.assertAlmostEqual(sum(p.domain.area for p in mc.patches), math.pi * 25, places=10)

    def test_welded_closed_solid(self):
        for n, plan, mode in ((3, "POLYGON", "NORMAL"), (4, "CIRCLE", "VERTICAL"), (8, "CIRCLE", "NORMAL")):
            m = radial_model(radial_count=n, radial_plan=plan, thickness_mode=mode, edge_height=3.0)
            md = radial_solid(m, 6, 5)
            topo = topology_report(md)
            self.assertTrue(topo["closed"] and topo["consistent"], (n, plan, mode))
            self.assertEqual(len(md.vertices), 2 * (1 + n * 6 * 5))  # limas y clave soldadas
            self.assertGreater(mesh_volume(md), 0.0)

    def test_vertical_volume_exact_on_polygon(self):
        m = radial_model(radial_count=6, radial_plan="POLYGON", thickness=0.2, thickness_mode="VERTICAL",
                         edge_height=3.0)
        v = model_integrals(m, 0.8)["volume"]
        self.assertAlmostEqual(v, 0.2 * 0.5 * 6 * 100 * math.sin(math.pi / 3), places=9)
        self.assertAlmostEqual(mesh_volume(radial_solid(m, 7, 5)), v, places=9)

    def test_miter_correction_improves_volume(self):
        """La corrección de inglete acerca el volumen analítico al de una malla fina."""
        for ref in ("INTRADOS", "EXTRADOS"):
            m = radial_model(radial_count=6, radial_plan="POLYGON", thickness=0.3, thickness_reference=ref,
                             edge_height=3.0)
            integ = model_integrals(m, 0.8)
            corr = integ["creases"]["volume"]
            v_mesh = mesh_volume(radial_solid(m, 48, 48))
            err_with = abs(v_mesh - integ["volume"]) / integ["volume"]
            err_without = abs(v_mesh - (integ["volume"] - corr)) / integ["volume"]
            self.assertGreater(abs(corr) / integ["volume"], 0.01)
            self.assertLess(err_with, 0.1 * err_without, ref)
        m = radial_model(radial_count=6, thickness=0.3, thickness_reference="MID")
        self.assertAlmostEqual(model_integrals(m, 0.8)["creases"]["volume"], 0.0, places=12)

    def test_rulings_lie_on_lobes(self):
        ev = evaluate({"shell_type": "RADIAL", "ruling_spacing": 1.0, "ruling_layer": "MID",
                       "thickness_reference": "MID", "res_x": 6, "res_y": 6})
        self.assertGreater(len(ev.display_rulings), 0)
        for r in ev.display_rulings:
            mid = tuple(0.5 * (a + b) for a, b in zip(r.points[0], r.points[-1]))
            self.assertTrue(any(p.domain.contains(mid[:2], 1e-9) and abs(p.surface.z(*mid[:2]) - mid[2]) < 1e-9
                                for p in ev.model.patches))

    def test_pipeline_and_export(self):
        ev = evaluate({"shell_type": "RADIAL", "res_x": 8, "res_y": 6, "radial_supports": "GROINS"})
        rep = ev.report
        self.assertTrue(rep["mesh"]["closed"] and rep["mesh"]["consistent"])
        self.assertEqual(len(rep["supports"]), 8)
        self.assertEqual(rep["geometry"]["creases"]["count"], 8)
        self.assertIn("CREASES", codes(rep))
        doc = json.loads(json.dumps(build_document(ev)))
        self.assertEqual(len(doc["midsurface"]["nodes"]), 1 + 8 * 8 * 6)
        self.assertEqual(len(doc["patches"]), 8)
        self.assertIn("groin_3", doc["midsurface"]["node_sets"])
        self.assertEqual(set(doc["midsurface"]["element_patch"]), set(range(8)))
        again = evaluate(doc["parameters"])
        self.assertAlmostEqual(again.report["concrete"]["volume_shell"], rep["concrete"]["volume_shell"])

    def test_flat_lobes_reported(self):
        # planta poligonal con el borde a la altura de los apoyos -> c3 = 0 -> pirámide
        rep = evaluate({"shell_type": "RADIAL", "radial_plan": "POLYGON", "edge_height": 0.0,
                        "support_height": 0.0, "res_x": 4, "res_y": 4}).report
        self.assertIn("PLANAR", codes(rep))

    def test_default_dome_has_no_warnings(self):
        rep = evaluate({"shell_type": "RADIAL"}).report
        self.assertFalse([c for c in rep["checks"] if c["level"] != "INFO"], rep["checks"])


class TestInclinedCut(unittest.TestCase):
    """Bordes cortados por planos inclinados (tipo Los Manantiales)."""

    P = dict(radial_plan="INCLINED", radial_count=8, radial_radius=18.0, crown_height=5.0,
             edge_height=10.0, edge_overhang=6.0, support_height=0.0)

    def _plane(self, k, x, y, n=8, radius=18.0):
        """Altura del plano de corte del lóbulo k en (x, y)."""
        beta, theta = math.pi / n, 2 * math.pi * k / n
        xl = x * math.cos(theta) + y * math.sin(theta)
        x_g = radius * math.cos(beta)
        slope = (10.0 - 0.0) / 6.0
        return 0.0 + slope * (xl - x_g)

    def test_edge_is_plane_curve_on_surface(self):
        m = radial_model(**self.P)
        for k, patch in enumerate(m.patches):
            for i in range(1, 40):
                x, y = patch.domain.outer(i / 40)
                self.assertAlmostEqual(patch.surface.z(x, y), self._plane(k, x, y), places=9)
                self.assertAlmostEqual(patch.domain.cut.z(x, y), 0.0, places=9)

    def test_edge_starts_at_supports_and_reaches_apex(self):
        m = radial_model(**self.P)
        x_tip = 18.0 * math.cos(math.pi / 8) + 6.0
        for k, patch in enumerate(m.patches):
            dom, s = patch.domain, patch.surface
            self.assertAlmostEqual(s.z(*dom.a), 0.0, places=12)
            self.assertAlmostEqual(math.dist(dom.center, dom.outer(0.5)), x_tip, places=9)
            self.assertAlmostEqual(s.z(*dom.outer(0.5)), 10.0, places=9)
        self.assertAlmostEqual(m.characteristic_span, 2 * x_tip, places=9)

    def test_boundary_derivative_matches_finite_difference(self):
        dom = radial_model(**self.P).patches[3].domain
        for t in (0.1, 0.37, 0.5, 0.9):
            h = 1e-6
            a, b = dom.outer(t - h), dom.outer(t + h)
            fd = ((b[0] - a[0]) / (2 * h), (b[1] - a[1]) / (2 * h))
            an = dom._outer_derivative(t)
            self.assertAlmostEqual(fd[0], an[0], places=5)
            self.assertAlmostEqual(fd[1], an[1], places=5)

    def test_area_matches_dense_polygon(self):
        dom = radial_model(**self.P).patches[0].domain
        pts = [dom.center] + [dom.outer(i / 4000) for i in range(4001)]
        shoelace = 0.5 * sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                             for i in range(len(pts)))
        self.assertLess(abs(shoelace - dom.area) / dom.area, 1e-6)

    def test_overhang_increases_plan_beyond_polygon(self):
        base = dict(self.P, edge_overhang=1e-4)
        tiny = sum(p.domain.area for p in radial_model(**base).patches)
        polygon = 0.5 * 8 * 18.0 ** 2 * math.sin(2 * math.pi / 8)
        self.assertLess(abs(tiny - polygon) / polygon, 1e-3)
        self.assertGreater(sum(p.domain.area for p in radial_model(**self.P).patches), polygon)

    def test_rulings_end_on_edge_or_groins(self):
        ev = evaluate({"shell_type": "RADIAL", **self.P, "ruling_spacing": 1.5, "ruling_layer": "INTRADOS",
                       "res_x": 6, "res_y": 6})
        self.assertGreater(len(ev.display_rulings), 50)
        for r in ev.display_rulings:
            for x, y, z in (r.points[0], r.points[-1]):
                on_edge = any(abs(p.domain.cut.z(x, y)) < 1e-8 for p in ev.model.patches)
                on_groin = any(abs(p.domain.a[0] * y - p.domain.a[1] * x) < 1e-8 * 18 for p in ev.model.patches)
                self.assertTrue(on_edge or on_groin, (x, y))

    def test_closed_mesh_and_volume(self):
        for mode in ("NORMAL", "VERTICAL"):
            m = radial_model(**self.P, thickness=0.1, thickness_mode=mode)
            md = radial_solid(m, 24, 32)
            topo = topology_report(md)
            self.assertTrue(topo["closed"] and topo["consistent"], mode)
            v = model_integrals(m, 0.8)["volume"]
            self.assertLess(abs(mesh_volume(md) - v) / v, 2e-3, mode)
        m = radial_model(**self.P, thickness=0.1, thickness_mode="VERTICAL")
        self.assertAlmostEqual(model_integrals(m, 0.8)["volume"], 0.1 * sum(p.domain.area for p in m.patches),
                               places=9)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            radial_model(**dict(self.P, edge_overhang=0.0))
        with self.assertRaises(ValueError):  # borde muy bajo: el plano corta antes del vértice o no cierra
            radial_model(**dict(self.P, edge_height=-30.0))

    def test_preset_has_no_errors(self):
        from core.radial import PRESETS
        rep = evaluate({"shell_type": "RADIAL", **PRESETS["LOBES"][2]}).report
        self.assertTrue(rep["mesh"]["closed"] and rep["mesh"]["consistent"])
        self.assertFalse([c for c in rep["checks"] if c["level"] == "ERROR"], rep["checks"])
        self.assertEqual(rep["definition"]["edge_cut"], "plano inclinado (hipérbola plana)")
        doc = json.loads(json.dumps(build_document(evaluate({"shell_type": "RADIAL", **self.P,
                                                             "res_x": 4, "res_y": 4}))))
        self.assertEqual(doc["patches"][0]["domain"]["outer"], "cut")


if __name__ == "__main__":
    unittest.main()
