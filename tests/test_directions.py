"""Pruebas de las direcciones principales de N dibujadas sobre el cascarón (sin Blender ni CalculiX).

    python -m unittest discover -s tests -v

El campo de esfuerzos es sintético (tests/frd_fixture.py): una membrana
conocida en los ejes locales de cada nodo, igual en ambas caras.
"""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "candelalab"))
sys.path.insert(0, HERE)

from core.directions import (COMPRESSION, FILL, TENSION, arm_range, auto_spacing, direction_glyphs,  # noqa: E402
                             principal_axes, principal_crosses, sample_nodes)
from core.elements import intrados_height  # noqa: E402
from core.fea import fe_model_from_params  # noqa: E402
from core.frd import load_results, local_frame, parse_frd, principal2, section_csv  # noqa: E402
from core.pipeline import evaluate  # noqa: E402
from frd_fixture import frd_text, synthetic  # noqa: E402

K, H, A, B, T = 0.1, 3.0, 4.0, 3.0, 0.05
HYPAR = {"shell_type": "HYPAR", "definition": "CORNERS", "span_x": 2 * A, "span_y": 2 * B,
         "z_sw": H + K * A * B, "z_se": H - K * A * B, "z_ne": H + K * A * B, "z_nw": H - K * A * B,
         "res_x": 8, "res_y": 8, "thickness": T, "support_layout": "ALL_CORNERS", "support_type": "PINNED",
         "fea_live_load": 1.0}
SM = (2e6, -1e6, 0.5e6)   # σ11, σ22, σ12 en los ejes locales (Pa): sólo membrana


def tensor(local, n):
    """(SXX, SYY, SZZ, SXY, SYZ, SZX) de un estado plano dado en los ejes locales de n."""
    e1, e2 = local_frame(n)
    s11, s22, s12 = local
    m = [[s11 * e1[i] * e1[j] + s22 * e2[i] * e2[j] + s12 * (e1[i] * e2[j] + e2[i] * e1[j]) for j in range(3)]
         for i in range(3)]
    return [m[0][0], m[1][1], m[2][2], m[0][1], m[1][2], m[2][0]]


def results(local=SM, **kw):
    """Resultados de un .frd sintético con salida 3D (caras ±t/2 según la normal de cada nodo)."""
    params = {**HYPAR, **kw}
    fe, _rep = fe_model_from_params(params)
    ev = evaluate(params)
    nodes, stress, nid = {}, {}, 700000
    for v in fe.plan:
        mid, n, h = fe.nodes[v], fe.normals[v], 0.5 * fe.nodal_thickness.get(v, params["thickness"])
        for face in (1, -1, 0):
            nid += 3
            nodes[nid] = tuple(mid[i] + face * h * n[i] for i in range(3))
            stress[nid] = tensor(local, n)
    steps = synthetic(fe, nodes, lambda n_, p, k: [0.0, 0.0, -1e-3], lambda n_, p, k: stress[n_])
    mesh = ev.shell_mesh
    res = load_results(parse_frd(frd_text(nodes, steps)), None, fe, mesh.plan_points, mesh.vertices, model=ev.model)
    return res, ev


class TestAxes(unittest.TestCase):
    def test_principal_axes_diagonalize_the_tensor(self):
        n = tuple(c / math.sqrt(1.34) for c in (0.3, -0.5, 1.0))
        e1, e2 = local_frame(n)
        for nx, ny, nxy in ((2.0, -1.0, 0.5), (-3.0, -3.0, 0.0), (0.0, 0.0, 2.0), (1.0, 4.0, -2.0)):
            n1, n2, d1, d2, angle = principal_axes(nx, ny, nxy, n)

            def q(a, b):
                ca, cb = [sum(x * y for x, y in zip(a, e)) for e in (e1, e2)], \
                         [sum(x * y for x, y in zip(b, e)) for e in (e1, e2)]
                return ca[0] * cb[0] * nx + ca[1] * cb[1] * ny + (ca[0] * cb[1] + ca[1] * cb[0]) * nxy

            self.assertAlmostEqual(q(d1, d1), n1, places=12)
            self.assertAlmostEqual(q(d2, d2), n2, places=12)
            self.assertAlmostEqual(q(d1, d2), 0.0, places=12)
            self.assertGreaterEqual(n1, n2)
            for d in (d1, d2):
                self.assertAlmostEqual(sum(c * c for c in d), 1.0, places=12)
                self.assertAlmostEqual(sum(a * b for a, b in zip(d, n)), 0.0, places=12)   # en el plano tangente
            cross = (d1[1] * d2[2] - d1[2] * d2[1], d1[2] * d2[0] - d1[0] * d2[2], d1[0] * d2[1] - d1[1] * d2[0])
            for a, b in zip(cross, n):                                                     # d₂ = n × d₁
                self.assertAlmostEqual(a, b, places=12)
        self.assertAlmostEqual(principal_axes(2.0, -1.0, 0.5, n)[4], math.degrees(0.5 * math.atan2(1.0, 3.0)),
                               places=12)
        self.assertEqual(principal_axes(5.0, 5.0, 0.0, n)[4], 0.0)                       # isótropo: ejes locales


class TestSampling(unittest.TestCase):
    def test_one_node_per_cell_nearest_to_its_center(self):
        plan = {j * 33 + i: (i * 0.25, j * 0.2) for j in range(31) for i in range(33)}    # 8 × 6 m
        self.assertEqual(auto_spacing(plan), 0.5)
        chosen = sample_nodes(plan, 1.0)
        self.assertEqual(len(chosen), 8 * 6)
        cells = {(min(7, math.floor(plan[v][0])), min(5, math.floor(plan[v][1]))) for v in chosen}
        self.assertEqual(len(cells), 48)
        for v in chosen:
            x, y = plan[v]
            i, j = min(7, math.floor(x)), min(5, math.floor(y))
            dist = math.hypot(x - i - 0.5, y - j - 0.5)
            others = [math.hypot(p[0] - i - 0.5, p[1] - j - 0.5) for p in plan.values()
                      if (min(7, math.floor(p[0])), min(5, math.floor(p[1]))) == (i, j)]
            self.assertAlmostEqual(dist, min(others), places=12)
        # Rejilla centrada: con una separación que no divide la planta sobra lo mismo a cada lado
        xs = sorted({plan[v][0] for v in sample_nodes(plan, 3.0)})
        self.assertAlmostEqual(xs[0] - 0.0, 8.0 - xs[-1], places=12)


class TestOnTheShell(unittest.TestCase):
    def test_crosses_follow_the_membrane_field(self):
        res, _ev = results()
        crosses, spacing = principal_crosses(res, 0)
        self.assertEqual(spacing, 8.0 / 16)
        n1, n2 = (T * x / 1000.0 for x in principal2(*SM))
        theta = math.degrees(0.5 * math.atan2(2 * SM[2], SM[0] - SM[1]))
        self.assertGreater(len(crosses), 100)
        for c in crosses:
            self.assertAlmostEqual(c.values[0], n1, delta=1e-4 * abs(n1))
            self.assertAlmostEqual(c.values[1], n2, delta=1e-4 * abs(n1))
            self.assertAlmostEqual(c.angle, theta, delta=1e-3)
            n = res.normals[c.node]
            self.assertAlmostEqual(sum(a * b for a, b in zip(c.directions[0], n)), 0.0, places=9)

    def test_glyph_mesh_and_scale(self):
        res, _ev = results()
        md, info = direction_glyphs(res, 0)
        crosses, spacing = principal_crosses(res, 0)
        self.assertEqual(info["count"], len(crosses))
        self.assertEqual(set(md.face_parts), {TENSION, COMPRESSION})                    # N₁ > 0, N₂ < 0
        self.assertAlmostEqual(info["scale"], info["max"] / (FILL * spacing), places=12)
        self.assertEqual(info["omitted"], 0)
        self.assertEqual(len(md.faces), len(md.face_parts))
        self.assertTrue(all(max(f) < len(md.vertices) for f in md.faces))
        # Escala fija: kN/m por metro de brazo; el campo no cambia, el dibujo sí
        md2, info2 = direction_glyphs(res, 0, scale=2 * info["scale"])
        self.assertEqual(info2["scale"], 2 * info["scale"])
        span = lambda m: max(v[0] for v in m.vertices) - min(v[0] for v in m.vertices)  # noqa: E731
        self.assertLessEqual(span(md2), span(md))

    def test_arms_lie_on_both_faces(self):
        # Espesor vertical: cada cara es la referencia desplazada en z, así que el desfase es exacto
        res, ev = results(thickness_mode="VERTICAL")
        lift = 0.01
        md, _info = direction_glyphs(res, 0, lift=lift)
        surf = ev.model.patches[0].surface
        s0, s1 = 0.0, T                                                                    # referencia = intradós
        up = down = 0
        for x, y, z in md.vertices:
            top, bottom = z - (surf.z(x, y) + s1 + lift), z - (surf.z(x, y) + s0 - lift)
            self.assertLess(min(abs(top), abs(bottom)), 1e-12)
            up, down = up + (abs(top) < 1e-12), down + (abs(bottom) < 1e-12)
        self.assertEqual(up, down)
        surface, _info = direction_glyphs(res, 0, solid=False, lift=lift)              # salida «Superficie»
        self.assertTrue(all(min(abs(z - surf.z(x, y) - lift), abs(z - surf.z(x, y) + lift)) < 1e-12
                            for x, y, z in surface.vertices))

    def test_under_the_capital(self):
        params = {"shell_type": "UMBRELLA", "res_x": 16, "res_y": 16, "thickness": T, "thickness_mode": "VERTICAL",
                  "support_type": "COLUMN", "capital_enabled": True, "capital_radius": 1.8, "capital_extra": 0.25}
        res, ev = results(**params)
        lift = 0.01
        md, _info = direction_glyphs(res, 0, lift=lift)
        model = ev.model
        below = 0
        for x, y, z in md.vertices:
            zi = intrados_height(model, x, y)
            if zi is not None and z < zi:                                                  # cara inferior
                self.assertAlmostEqual(zi - z, lift, delta=1e-9)
                below += 1
        self.assertGreater(below, len(md.vertices) // 3)
        self.assertGreater(max(c.drop(x, y) for c in model.capitals for x, y, _z in md.vertices), 0.1)

    def test_arms_are_clipped_at_the_edge(self):
        res, ev = results()
        model = ev.model
        self.assertEqual(arm_range(model, (0.0, 0.0), (1.0, 0.0, 0.0), 1.0), (-1.0, 1.0))
        lo, hi = arm_range(model, (A - 0.3, 0.0), (1.0, 0.0, 0.0), 1.0)                  # borde x = A
        self.assertEqual(lo, -1.0)
        self.assertAlmostEqual(hi, 0.3, places=9)
        lo, hi = arm_range(model, (A, B), (0.6, 0.8, 0.0), 1.0)                         # esquina: hacia fuera, nada
        self.assertAlmostEqual(hi, 0.0, places=9)
        md, _info = direction_glyphs(res, 0)
        reach = 0.06 * 0.5 + 1.25 * T              # borde de la cinta + desfase en planta del extradós (normal)
        self.assertTrue(all(abs(x) <= A + reach and abs(y) <= B + reach for x, y, _z in md.vertices))

    def test_nothing_to_draw(self):
        res, _ev = results()
        buckle = next(i for i, it in enumerate(res.items) if it.kind == "BUCKLE")
        md, info = direction_glyphs(res, buckle)
        self.assertIsNone(md)
        self.assertIn("pandeo", info["reason"])
        zero, _ev = results(local=(0.0, 0.0, 0.0))
        md, info = direction_glyphs(zero, 0)
        self.assertIsNone(md)
        self.assertIn("nula", info["reason"])
        # Uniaxial: N₂ = 0 no se dibuja
        uni, _ev = results(local=(3e6, 0.0, 0.0))
        md, info = direction_glyphs(uni, 0)
        self.assertEqual(set(md.face_parts), {TENSION})
        self.assertEqual(info["omitted"], info["count"])

    def test_csv_angle(self):
        import csv
        import io
        res, _ev = results()
        rows = list(csv.reader(io.StringIO(section_csv(res, 0)), delimiter=";"))
        head = next(i for i, r in enumerate(rows) if r and r[0] == "Nodo")
        col = rows[head].index("θN1")
        theta = math.degrees(0.5 * math.atan2(2 * SM[2], SM[0] - SM[1]))
        for r in rows[head + 1:head + 30]:
            self.assertAlmostEqual(float(r[col].replace(",", ".")), theta, delta=0.01)


if __name__ == "__main__":
    unittest.main()
