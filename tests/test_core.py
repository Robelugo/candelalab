"""Pruebas del núcleo geométrico (sin Blender).

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

from core.elements import beam_strips  # noqa: E402
from core.export import build_document  # noqa: E402
from core.geometry import QuadDomain, QuadraticSurface  # noqa: E402
from core.hypar import HyparParams, hypar_surface  # noqa: E402
from core.meshing import (PART_EXTRADOS, build_solid, build_surface, mesh_area, mesh_volume,  # noqa: E402
                          topology_report)
from core.metrics import surface_integrals  # noqa: E402
from core.model import ShellPatch, ThicknessSpec  # noqa: E402
from core.pipeline import evaluate  # noqa: E402
from core.quadrature import gauss_legendre  # noqa: E402
from core.rulings import compute_rulings  # noqa: E402


def hypar_patch(**kw):
    surf, dom, k = hypar_surface(HyparParams(**kw))
    return ShellPatch("t", surf, dom), k


def codes(report):
    return {c["code"] for c in report["checks"]}


def reference_area_kxy(k, a, b, n=20000):
    """Área de z = k·x·y sobre [-a,a]×[-b,b]: integral interior en forma cerrada,
    exterior por Simpson fino (comprobación independiente de la cuadratura)."""
    def inner(x):
        c2 = 1.0 + k * k * x * x
        c = math.sqrt(c2)
        f = lambda y: 0.5 * y * math.sqrt(c2 + k * k * y * y) + c2 / (2 * k) * math.asinh(k * y / c)  # noqa: E731
        return f(b) - f(-b)
    h = 2 * a / n
    s = inner(-a) + inner(a)
    for i in range(1, n):
        s += (4 if i % 2 else 2) * inner(-a + i * h)
    return s * h / 3


class TestQuadrature(unittest.TestCase):
    def test_exact_for_polynomials(self):
        for n in (1, 2, 5, 8, 12):
            xs, ws = gauss_legendre(n)
            for deg in range(0, 2 * n):
                exact = 0.0 if deg % 2 else 2.0 / (deg + 1)
                approx = sum(w * x ** deg for x, w in zip(xs, ws))
                self.assertAlmostEqual(approx, exact, places=12, msg=f"n={n} deg={deg}")


class TestSurface(unittest.TestCase):
    def test_asymptotic_directions_are_straight_lines(self):
        rng = random.Random(1)
        for _ in range(200):
            c = [rng.uniform(-1, 1) for _ in range(6)]
            surf = QuadraticSurface(*c)
            dirs = surf.asymptotic_directions()
            if not surf.is_hyperbolic():
                self.assertIsNone(dirs)
                continue
            for d in dirs:
                self.assertAlmostEqual(math.hypot(*d), 1.0, places=12)
                self.assertAlmostEqual(surf.quadratic_form(d), 0.0, places=12)
                # la curva levantada es recta: el punto medio está sobre la cuerda
                x0, y0 = rng.uniform(-5, 5), rng.uniform(-5, 5)
                L = rng.uniform(1, 10)
                za, zb = surf.z(x0, y0), surf.z(x0 + L * d[0], y0 + L * d[1])
                zm = surf.z(x0 + 0.5 * L * d[0], y0 + 0.5 * L * d[1])
                self.assertAlmostEqual(zm, 0.5 * (za + zb), places=9)

    def test_gaussian_curvature_kxy(self):
        k, p, q = 0.13, 0.3, -0.2
        surf = QuadraticSurface(1.0, p, q, 0.0, k, 0.0)
        K, H = surf.curvatures(0.0, 0.0)
        self.assertAlmostEqual(K, -k * k / (1 + p * p + q * q) ** 2, places=14)
        k1, k2 = surf.principal_curvatures(0.0, 0.0)
        self.assertAlmostEqual(k1 * k2, K, places=14)
        self.assertGreater(k1, 0.0)
        self.assertLess(k2, 0.0)

    def test_clip_line_rectangle(self):
        dom = QuadDomain.rectangle(10, 6)
        self.assertEqual(dom.clip_line((0, 0), (1, 0)), (-5.0, 5.0))
        self.assertIsNotNone(dom.clip_line((0, 3), (1, 0)))  # sobre el borde: se conserva
        self.assertIsNone(dom.clip_line((0, 3.01), (1, 0)))


class TestHyparParametrization(unittest.TestCase):
    def test_ruled_edges_corner_heights(self):
        patch, k = hypar_patch(span_x=10, span_y=8, rise=3, warp=2, edge_angle=0)
        self.assertAlmostEqual(k, 2 / (5 * 4))
        s = patch.surface
        self.assertAlmostEqual(s.z(5, 4), 5.0)
        self.assertAlmostEqual(s.z(-5, -4), 5.0)
        self.assertAlmostEqual(s.z(5, -4), 1.0)
        self.assertAlmostEqual(s.z(-5, 4), 1.0)
        # cada borde es una generatriz: z lineal a lo largo del borde
        for x in (-5, 5):
            self.assertAlmostEqual(s.z(x, 0), 0.5 * (s.z(x, 4) + s.z(x, -4)))

    def test_principal_edges_warp_definition(self):
        patch, k = hypar_patch(span_x=10, span_y=10, rise=0, warp=2, edge_angle=math.pi / 4)
        s = patch.surface
        self.assertAlmostEqual(abs(s.z(5, 0)), 2.0)   # punto medio de borde
        self.assertAlmostEqual(abs(s.z(0, 5)), 2.0)
        self.assertAlmostEqual(s.z(5, 5), 0.0)       # esquina (planta cuadrada)
        dirs = s.asymptotic_directions()
        angles = sorted(round(math.degrees(math.atan2(d[1], d[0])), 9) for d in dirs)
        self.assertEqual(angles, [45.0, 135.0])

    def test_corner_definition_reproduces_corners(self):
        d = {"definition": "CORNERS", "span_x": 12, "span_y": 7,
             "z_sw": 1.0, "z_se": 4.5, "z_ne": 2.0, "z_nw": 6.0}
        hp = HyparParams.from_dict(d)
        surf, dom, _ = hypar_surface(hp)
        for name, (x, y) in zip(("z_sw", "z_se", "z_ne", "z_nw"), dom.corners):
            self.assertAlmostEqual(surf.z(x, y), d[name], places=12)


class TestMetrics(unittest.TestCase):
    def test_area_against_independent_reference(self):
        patch, k = hypar_patch(span_x=10, span_y=6, rise=0, warp=2.5)
        integ = surface_integrals(patch, ThicknessSpec(0.05, "VERTICAL", "MID"), math.radians(45))
        ref = reference_area_kxy(k, 5, 3)
        self.assertLess(abs(integ["area_reference"] - ref) / ref, 1e-10)

    def test_tilted_plane_area_exact(self):
        patch, _ = hypar_patch(span_x=4, span_y=3, rise=1, warp=0, slope_x=0.5, slope_y=-0.25)
        integ = surface_integrals(patch, ThicknessSpec(0.1, "VERTICAL", "MID"), math.radians(45))
        self.assertAlmostEqual(integ["area_reference"], 12 * math.sqrt(1 + 0.25 + 0.0625), places=10)

    def test_mesh_area_converges_quadratically(self):
        patch, _ = hypar_patch(span_x=10, span_y=10, rise=0, warp=3, edge_angle=math.radians(30))
        exact = surface_integrals(patch, ThicknessSpec(0.04), math.radians(45))["area_reference"]
        e1 = abs(mesh_area(build_surface(patch, 8, 8)) - exact)
        e2 = abs(mesh_area(build_surface(patch, 16, 16)) - exact)
        self.assertGreater(e1 / e2, 3.5)  # ~4 para O(h²)

    def test_vertical_volume_is_exact_on_mesh(self):
        patch, _ = hypar_patch(span_x=9, span_y=7, rise=2, warp=3, edge_angle=0.4)
        th = ThicknessSpec(0.08, "VERTICAL", "INTRADOS")
        md = build_solid(patch, th, 10, 7)
        self.assertAlmostEqual(mesh_volume(md), 0.08 * 63, places=9)

    def test_normal_volume_steiner_matches_fine_mesh(self):
        patch, _ = hypar_patch(span_x=10, span_y=10, rise=0, warp=4)
        for ref in ("INTRADOS", "MID", "EXTRADOS"):
            th = ThicknessSpec(0.3, "NORMAL", ref)
            v_exact = surface_integrals(patch, th, math.radians(45))["volume"]
            v_mesh = mesh_volume(build_solid(patch, th, 160, 160))
            self.assertLess(abs(v_mesh - v_exact) / v_exact, 2e-4, msg=ref)
            # K < 0  ->  volumen normal menor que t·A_media
        th = ThicknessSpec(0.3, "NORMAL", "MID")
        integ = surface_integrals(patch, th, math.radians(45))
        self.assertLess(integ["volume"], 0.3 * integ["area_reference"])


class TestMeshTopology(unittest.TestCase):
    def test_solid_closed_consistent_outward(self):
        for mode in ("VERTICAL", "NORMAL"):
            patch, _ = hypar_patch(span_x=8, span_y=5, rise=1, warp=2, edge_angle=0.3)
            md = build_solid(patch, ThicknessSpec(0.1, mode, "MID"), 9, 6)
            topo = topology_report(md)
            self.assertTrue(topo["closed"])
            self.assertTrue(topo["consistent"])
            self.assertGreater(mesh_volume(md), 0.0)
            # caras del extradós con normal +Z
            for f, part in zip(md.faces, md.face_parts):
                if part == PART_EXTRADOS:
                    a, b, c = (md.vertices[i] for i in f[:3])
                    nz = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                    self.assertGreater(nz, 0.0)


class TestRulings(unittest.TestCase):
    def test_counts_and_exact_lengths(self):
        patch, k = hypar_patch(span_x=10, span_y=10, rise=0, warp=2.5)
        rulings, steps = compute_rulings(patch, 0.5)
        self.assertEqual(sum(r.family == 0 for r in rulings), 21)
        self.assertEqual(sum(r.family == 1 for r in rulings), 21)
        for r in rulings:
            if r.family == 0:  # paralela a X en y = c
                c = r.points[0][1]
                self.assertAlmostEqual(r.length, math.hypot(10, 2 * k * 5 * c), places=9)
            a, b = r.points[0], r.points[-1]
            for t in (0.25, 0.5, 0.75):  # puntos intermedios sobre la superficie
                x, y, z = (a[i] + t * (b[i] - a[i]) for i in range(3))
                self.assertAlmostEqual(z, patch.surface.z(x, y), places=10)

    def test_offset_rulings_straight_only_when_exact(self):
        patch, _ = hypar_patch(span_x=10, span_y=10, rise=0, warp=3)
        r_n, _ = compute_rulings(patch, 1.0, 0.1, "NORMAL", samples=11)
        r_v, _ = compute_rulings(patch, 1.0, 0.1, "VERTICAL")
        self.assertGreater(max(r.straightness_dev for r in r_n), 1e-6)
        self.assertEqual(max(r.straightness_dev for r in r_v), 0.0)


class TestPipeline(unittest.TestCase):
    def test_default_evaluation(self):
        ev = evaluate({})
        rep = ev.report
        self.assertTrue(rep["mesh"]["closed"] and rep["mesh"]["consistent"])
        self.assertLess(abs(rep["mesh"]["volume_rel_error"]), 1e-3)
        self.assertAlmostEqual(rep["mesh"]["chord_error_max"], 0.0, places=12)  # bordes rectos
        self.assertEqual({s["name"] for s in rep["supports"]}, {"SE", "NO"})
        self.assertNotIn("PLANAR", codes(rep))
        json.dumps(rep)  # serializable

    def test_planar_and_fold_errors(self):
        self.assertIn("PLANAR", codes(evaluate({"warp": 0.0}).report))
        # k = 5/(2·2) = 1.25 1/m -> radio mínimo 0.8 m; con referencia media s = ±1.0 m se pliega
        rep = evaluate({"warp": 5.0, "span_x": 4, "span_y": 4, "thickness": 2.0, "thickness_reference": "MID"}).report
        self.assertIn("OFFSET_FOLD", codes(rep))
        rep = evaluate({"warp": 5.0, "span_x": 4, "span_y": 4, "thickness": 1.0, "thickness_reference": "MID"}).report
        self.assertNotIn("OFFSET_FOLD", codes(rep))

    def test_principal_edges_chord_error_and_planar_quads(self):
        ev = evaluate({"edge_mode": "PRINCIPAL", "res_x": 8, "res_y": 8, "warp": 2.0, "span_x": 10, "span_y": 10})
        rep = ev.report
        # z = H + (k/2)(y² − x²), k = 2f/a²: error en el punto medio de arista = (k/2)·h²/4
        k = 2 * 2.0 / 25
        self.assertAlmostEqual(rep["mesh"]["chord_error_max"], 0.5 * k * (10 / 8) ** 2 / 4, places=12)
        # superficie de traslación z = g(x) + h(y): los cuadriláteros alineados son planos
        self.assertLess(rep["mesh"]["quad_warp_max"], 1e-12)

    def test_edge_beams_volume(self):
        ev = evaluate({"beam_xmin": True, "beam_xmax": True, "beam_ymin": True, "beam_ymax": True,
                       "beam_width": 0.25, "beam_depth": 0.4, "span_x": 10, "span_y": 8})
        band = 10 * 8 - (10 - 0.5) * (8 - 0.5)
        self.assertAlmostEqual(ev.report["concrete"]["volume_beams"], 0.4 * band, places=9)
        self.assertAlmostEqual(mesh_volume(ev.beams_mesh), 0.4 * band, places=6)
        topo = topology_report(ev.beams_mesh)
        self.assertTrue(topo["closed"] and topo["consistent"])

    def test_beam_strip_miter_single_edge(self):
        patch, _ = hypar_patch()
        strips = beam_strips(patch.domain, {"ymin": True}, 0.3)
        self.assertEqual(len(strips), 1)
        _, (a, b, b_in, a_in) = strips[0]
        self.assertAlmostEqual(a_in[0], a[0])  # sin vecinos: corte recto

    def test_export_roundtrip(self):
        ev = evaluate({"warp": 1.7, "edge_mode": "CUSTOM", "edge_angle": 0.3, "res_x": 6, "res_y": 5})
        doc = json.loads(json.dumps(build_document(ev)))
        self.assertEqual(len(doc["midsurface"]["nodes"]), 7 * 6)
        self.assertEqual(len(doc["midsurface"]["elements"]), 30)
        again = evaluate(doc["parameters"])
        self.assertAlmostEqual(again.report["concrete"]["volume_shell"], ev.report["concrete"]["volume_shell"])

    def test_z_range_exact(self):
        ev = evaluate({"edge_mode": "CUSTOM", "edge_angle": 0.5, "tilt_x": 0.2, "res_x": 200, "res_y": 200,
                       "output": "SURFACE"})
        zs = [v[2] for v in ev.shell_mesh.vertices]
        g = ev.report["geometry"]
        self.assertLessEqual(g["z_ref_min"], min(zs) + 1e-12)
        self.assertGreaterEqual(g["z_ref_max"], max(zs) - 1e-12)
        self.assertLess(min(zs) - g["z_ref_min"], 1e-3)


if __name__ == "__main__":
    unittest.main()
