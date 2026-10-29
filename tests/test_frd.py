"""Pruebas de la lectura de resultados .frd/.dat (sin Blender ni CalculiX).

    python -m unittest discover -s tests -v

Los archivos son sintéticos (tests/frd_fixture.py), escritos con los formatos
documentados: comprueban la lectura por columnas, la asociación a la malla del
cascarón en salida 2D y 3D, los pasos, los modos de pandeo y las reacciones.
"""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "candelalab"))
sys.path.insert(0, HERE)

from core.fea import analysis_steps, fe_model_from_params, to_inp  # noqa: E402
from core.frd import colors, load_results, parse_dat, parse_frd, principal, results_key, von_mises  # noqa: E402
from core.pipeline import evaluate  # noqa: E402
from frd_fixture import dat_text, frd_text, synthetic  # noqa: E402

K, H, A, B, T = 0.1, 3.0, 4.0, 3.0, 0.05
HYPAR = {"shell_type": "HYPAR", "definition": "CORNERS", "span_x": 2 * A, "span_y": 2 * B,
         "z_sw": H + K * A * B, "z_se": H - K * A * B, "z_ne": H + K * A * B, "z_nw": H - K * A * B,
         "res_x": 4, "res_y": 4, "thickness": T, "support_layout": "ALL_CORNERS", "support_type": "PINNED",
         "fea_live_load": 1.0}


def setup(**kw):
    params = {**HYPAR, **kw}
    fe, _rep = fe_model_from_params(params)
    ev = evaluate(params)
    return fe, ev.shell_mesh


def uz(x, y, k=1):
    return -1e-3 * k * (1.0 + 0.01 * x * y)


class TestParser(unittest.TestCase):
    def test_fixed_columns_with_glued_negative_numbers(self):
        text = frd_text({1: (-1.5, 2.0, -0.001), 22: (3.0, -4.0, 5.0)},
                        [[{"name": "DISP", "components": ["D1", "D2", "D3"],
                           "data": {1: [-1.2345e-3, -2e-5, -3.5], 22: [1, 2, 3]}}]])
        self.assertIn(" -1         1-1.50000E+00", text)                   # número y valor pegados
        frd = parse_frd(text)
        self.assertEqual(frd.nodes[1], (-1.5, 2.0, -0.001))
        ds = frd.datasets[0]
        self.assertEqual((ds.name, ds.components, ds.step), ("DISP", ["D1", "D2", "D3"], 1))
        self.assertEqual(ds.data[1], [-1.2345e-3, -2e-5, -3.5])

    def test_short_format_continuations_and_elements(self):
        vals = [float(i) - 4.5 for i in range(9)]
        text = frd_text({7: (0.0, 0.0, 0.0)},
                        [[{"name": "EXTRA", "components": [f"C{i}" for i in range(9)], "data": {7: vals}}]],
                        fmt=0, elements={1: (10, list(range(1, 9)))})
        frd = parse_frd(text)
        self.assertEqual(frd.datasets[0].data[7], vals)

    def test_steps_modes_and_values(self):
        steps = [[{"name": "DISP", "components": ["D1", "D2", "D3"], "data": {1: [0, 0, 0]}}],
                 [{"name": "DISP", "components": ["D1", "D2", "D3"], "mode": m, "value": v, "data": {1: [0, 0, 1]}}
                  for m, v in ((1, -3.25), (2, 7.5))]]
        frd = parse_frd(frd_text({1: (0, 0, 0)}, steps))
        self.assertEqual([(d.step, d.mode, d.value) for d in frd.datasets], [(1, None, 1.0), (2, 1, -3.25), (2, 2, 7.5)])

    def test_binary_and_garbage_are_rejected(self):
        text = frd_text({1: (0, 0, 0)}, []).replace("                                     1\n", "                                     2\n", 1)
        with self.assertRaises(ValueError):
            parse_frd(text)
        with self.assertRaises(ValueError):
            parse_frd("esto no es un frd\n")

    def test_dat(self):
        dat = parse_dat(dat_text(buckling=[[12.5, 20.0], [3.0]], totals=[[1.0, -2.0, 3.5e5]]))
        self.assertEqual(dat["buckling"], [[12.5, 20.0], [3.0]])
        self.assertEqual(dat["totals"], [[1.0, -2.0, 3.5e5]])


class TestTensors(unittest.TestCase):
    def test_principal_and_von_mises(self):
        self.assertEqual(principal([5, 0, 0, 0, 0, 0]), (5, 0))
        hi, lo = principal([0, 0, 0, 2.0, 0, 0])                                 # cortante puro
        self.assertAlmostEqual(hi, 2.0, places=12)
        self.assertAlmostEqual(lo, -2.0, places=12)
        self.assertAlmostEqual(von_mises([0, 0, 0, 2.0, 0, 0]), 2.0 * math.sqrt(3), places=12)
        self.assertAlmostEqual(von_mises([7, 7, 7, 0, 0, 0]), 0.0, places=12)    # hidrostático
        # Tensor diag(3, 1, −2) girado 30° alrededor de z: mismos autovalores
        c, s = math.cos(math.pi / 6), math.sin(math.pi / 6)
        sxx, syy, sxy = 3 * c * c + 1 * s * s, 3 * s * s + 1 * c * c, (3 - 1) * c * s
        hi, lo = principal([sxx, syy, -2, sxy, 0, 0])
        self.assertAlmostEqual(hi, 3.0, places=12)
        self.assertAlmostEqual(lo, -2.0, places=12)

    def test_signed_colors_are_centered(self):
        cols, legend = colors([-1.0, 0.0, 4.0, None], signed=True)
        self.assertEqual((legend["min"], legend["max"]), (-1.0, 4.0))
        self.assertEqual(cols[3], (0.35, 0.35, 0.35, 1.0))                        # sin dato: gris
        self.assertNotEqual(cols[0], cols[2])


class TestOnTheShell(unittest.TestCase):
    def test_2d_output_by_node_number(self):
        fe, mesh = setup(thickness_mode="VERTICAL", fea_output="2D")
        nodes = {k: p for k, p in enumerate(fe.nodes, 1)}
        steps = synthetic(fe, nodes, lambda n, p, k: [0.0, 0.0, uz(p[0], p[1], k)],
                          lambda n, p, k: [1e6 * p[0], 0, 0, 0, 0, 0])
        res = load_results(parse_frd(frd_text(nodes, steps)), None, fe, mesh.plan_points, mesh.vertices)
        self.assertEqual(res.output, "2D")
        self.assertEqual(res.summary["missing"], 0)
        names = [s["name"] for s in analysis_steps(fe)]
        self.assertEqual([it.step for it in res.items], names[:-2] + ["PANDEO_COMB"] * 2 + ["PANDEO_COMB_MEDIA"] * 2)
        vz = res.values(0, "DISP_Z")
        for (x, y), v in zip(mesh.plan_points, vz):   # espesor vertical: el nodo medio está sobre el punto en planta
            self.assertAlmostEqual(v, 1000 * uz(x, y, 1), delta=1e-5)
        smax = res.values(0, "S_MAX")
        for (x, _y), v in zip(mesh.plan_points, smax):
            self.assertAlmostEqual(v, max(x, 0.0), delta=1e-4)
        # Ambas caras (intradós y extradós) toman el mismo nodo: membrana
        n = len(mesh.vertices) // 2
        self.assertEqual(res.vertex_nodes[:n], res.vertex_nodes[n:])
        self.assertIn("FRD_2D", {c["code"] for c in res.summary["checks"]})

    def test_3d_output_shows_both_faces(self):
        fe, mesh = setup()                                     # espesor normal, intradós exacto
        ev = evaluate({**HYPAR})
        nodes, sign, nid = {}, {}, 900000
        for v, (x, y) in fe.plan.items():                      # expansión: caras ±t/2 según la normal
            mid = fe.nodes[v]
            n = ev.model.patch_at(x, y).surface.unit_normal(x, y)
            for face in (-1, 0, 1):
                nid += 7
                nodes[nid] = tuple(mid[i] + face * 0.5 * T * n[i] for i in range(3))
                sign[nid] = face
        steps = synthetic(fe, nodes, lambda n, p, k: [0.0, 0.0, -1e-3],
                          lambda n, p, k: [2e6 + 1e6 * sign[n], 0, 0, 0, 0, 0])
        res = load_results(parse_frd(frd_text(nodes, steps)), None, fe, mesh.plan_points, mesh.vertices)
        self.assertEqual(res.output, "3D")
        self.assertEqual(res.summary["missing"], 0)
        smax = res.values(0, "S_MAX")
        n = len(mesh.vertices) // 2                            # 0..n−1 intradós, n.. extradós
        self.assertTrue(all(abs(v - 1.0) < 1e-4 for v in smax[:n]))
        self.assertTrue(all(abs(v - 3.0) < 1e-4 for v in smax[n:]))     # flexión: cada cara su valor
        self.assertIn("FRD_3D", {c["code"] for c in res.summary["checks"]})

    def test_results_from_other_geometry_are_rejected(self):
        fe, mesh = setup(thickness_mode="VERTICAL")
        nodes = {k: p for k, p in enumerate(fe.nodes, 1)}
        steps = synthetic(fe, nodes, lambda n, p, k: [0, 0, 0], lambda n, p, k: [0] * 6)
        text = frd_text(nodes, steps)
        other_fe, other_mesh = setup(thickness_mode="VERTICAL", z_sw=H + 2 * K * A * B, z_ne=H + 2 * K * A * B)
        with self.assertRaises(ValueError):
            load_results(parse_frd(text), None, other_fe, other_mesh.plan_points, other_mesh.vertices)

    def test_buckling_factors_and_reactions(self):
        fe, mesh = setup(thickness_mode="VERTICAL", fea_output="2D")
        nodes = {k: p for k, p in enumerate(fe.nodes, 1)}
        steps = synthetic(fe, nodes, lambda n, p, k: [0.0, 0.0, uz(p[0], p[1], k)], lambda n, p, k: [0] * 6)
        text = frd_text(nodes, steps)
        static = [s["name"] for s in analysis_steps(fe) if s["kind"] == "STATIC"]
        # CalculiX: RF = −carga + la carga aplicada en los propios nodos de apoyo.
        reactions = [[1000 * (sp - c) for sp, c in zip(fe.summary["support_loads_kN"][s], fe.summary["totals_kN"][s])]
                     for s in static]
        self.assertTrue(any(abs(fe.summary["support_loads_kN"][s][2]) > 1e-6 for s in static))
        dat = parse_dat(dat_text(buckling=[[11.0, 12.0], [21.0, 22.0]], totals=reactions))
        res = load_results(parse_frd(text), dat, fe, mesh.plan_points, mesh.vertices)
        modes = [it for it in res.items if it.kind == "BUCKLE"]
        self.assertEqual([(m.mode, m.factor, m.factor_source) for m in modes],
                         [(1, 11.0, ".dat"), (2, 12.0, ".dat"), (1, 21.0, ".dat"), (2, 22.0, ".dat")])
        kb = next(k for k, s in enumerate(analysis_steps(fe), 1) if s["kind"] == "BUCKLE")
        n0, p0 = next(iter(nodes.items()))
        self.assertAlmostEqual(modes[0].disp[n0][2], uz(p0[0], p0[1], 10 * kb + 1), places=6)  # no el estado base
        k = res.items.index(modes[0])
        mags = [v for v in res.values(k, "DISP") if v is not None]
        self.assertAlmostEqual(max(mags), 1.0, places=12)                # modo normalizado
        self.assertTrue(all(v is None for v in res.values(k, "VON_MISES")))
        self.assertEqual(len(res.summary["reactions"]), len(static))
        self.assertLess(max(r["rel_error"] for r in res.summary["reactions"]), 1e-6)
        self.assertEqual(next(c for c in res.summary["checks"] if c["code"] == "FRD_REACTIONS")["level"], "INFO")
        no_dat = load_results(parse_frd(text), None, fe, mesh.plan_points, mesh.vertices)
        self.assertEqual([(m.factor, m.factor_source) for m in no_dat.items if m.kind == "BUCKLE"][:2],
                         [(2.5, ".frd"), (4.0, ".frd")])
        bad = parse_dat(dat_text(totals=[[0.0, 0.0, 1.0]] * len(static)))
        warn = load_results(parse_frd(text), bad, fe, mesh.plan_points, mesh.vertices)
        self.assertEqual(next(c for c in warn.summary["checks"] if c["code"] == "FRD_REACTIONS")["level"], "WARNING")

    def test_buckling_layouts_and_factor_mismatch(self):
        # CalculiX 2.23 escribe el estado base del paso antes de los modos; otros programas, 1PMODE.
        fe, mesh = setup(fea_output="2D")
        nodes = {k: p for k, p in enumerate(fe.nodes, 1)}
        texts = {}
        for layout in (False, True):
            steps = synthetic(fe, nodes, lambda n, p, k: [0.0, 0.0, uz(p[0], p[1], k)], lambda n, p, k: [0] * 6,
                              modes_layout=layout)
            texts[layout] = frd_text(nodes, steps)
            res = load_results(parse_frd(texts[layout]), None, fe, mesh.plan_points, mesh.vertices)
            self.assertEqual([(m.mode, m.factor) for m in res.items if m.kind == "BUCKLE"],
                             [(1, 2.5), (2, 4.0)] * len(fe.buckling), layout)
        self.assertNotIn("1PMODE", texts[False])
        dat = parse_dat(dat_text(buckling=[[2.5, 4.4], [2.5, 4.0]]))
        res = load_results(parse_frd(texts[False]), dat, fe, mesh.plan_points, mesh.vertices)
        warn = [c for c in res.summary["checks"] if c["code"] == "FRD_BUCKLE_FACTORS"]
        self.assertEqual(len(warn), 1)
        self.assertIn("modo 2", warn[0]["message"])

    def test_incomplete_run(self):
        fe, mesh = setup(thickness_mode="VERTICAL")
        nodes = {k: p for k, p in enumerate(fe.nodes, 1)}
        steps = synthetic(fe, nodes, lambda n, p, k: [0, 0, 0], lambda n, p, k: [0] * 6)[:2]
        res = load_results(parse_frd(frd_text(nodes, steps)), None, fe, mesh.plan_points, mesh.vertices)
        self.assertEqual(len(res.items), 2)
        self.assertIn("FRD_STEPS", {c["code"] for c in res.summary["checks"]})


class TestSectionForces(unittest.TestCase):
    SM = (2e6, -1e6, 0.5e6)       # membrana en ejes locales: σ11, σ22, σ12 (Pa)
    SB = (0.3e6, 0.0, -0.1e6)     # flexión: el extradós suma, el intradós resta

    def test_frame_and_formulas(self):
        from core.frd import in_plane, local_frame, principal2, section_forces
        self.assertEqual(local_frame((0.0, 0.0, 1.0)), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))
        self.assertEqual(in_plane([1, 2, 3, 4, 5, 6], (1, 0, 0), (0, 1, 0)), (1, 2, 4))
        n = (0.0, -math.sin(0.4), math.cos(0.4))
        e1, e2 = local_frame(n)
        self.assertAlmostEqual(sum(a * b for a, b in zip(e1, n)), 0.0, places=12)
        self.assertAlmostEqual(sum(a * b for a, b in zip(e2, n)), 0.0, places=12)
        nn, mm = section_forces([10, 20, 0, 5, 0, 0], [2, 4, 0, -1, 0, 0], (0.0, 0.0, 1.0), 0.1)
        self.assertEqual(tuple(round(x, 12) for x in nn), (0.6, 1.2, 0.2))       # t·(σt + σb)/2
        self.assertEqual(tuple(round(x, 12) for x in mm), (round(0.01 / 12 * 8, 12), round(0.01 / 12 * 16, 12),
                                                          round(0.01 / 12 * 6, 12)))   # t²/12·(σt − σb)
        hi, lo = principal2(3.0, -1.0, 2.0)
        self.assertAlmostEqual(hi + lo, 2.0, places=12)
        self.assertAlmostEqual(hi * lo, -3.0 - 4.0, places=12)

    def frd_with_bending(self, fe, mesh):
        from core.frd import local_frame
        nodes, stress, nid = {}, {}, 700000
        for v in fe.plan:
            mid, n = fe.nodes[v], fe.normals[v]
            e1, e2 = local_frame(n)
            for face in (1, -1, 0):
                s11, s22, s12 = (m + face * b for m, b in zip(self.SM, self.SB))
                tensor = [[s11 * e1[i] * e1[j] + s22 * e2[i] * e2[j] + s12 * (e1[i] * e2[j] + e2[i] * e1[j])
                           for j in range(3)] for i in range(3)]
                nid += 3
                nodes[nid] = tuple(mid[i] + face * 0.5 * T * n[i] for i in range(3))
                stress[nid] = [tensor[0][0], tensor[1][1], tensor[2][2], tensor[0][1], tensor[1][2], tensor[2][0]]
        steps = synthetic(fe, nodes, lambda n_, p, k: [0.0, 0.0, -1e-3], lambda n_, p, k: stress[n_])
        return load_results(parse_frd(frd_text(nodes, steps)), None, fe, mesh.plan_points, mesh.vertices)

    def test_membrane_and_bending_from_both_faces(self):
        from core.frd import principal2
        fe, mesh = setup()
        res = self.frd_with_bending(fe, mesh)
        self.assertEqual(res.output, "3D")
        self.assertEqual(len(res.faces), len(fe.plan))
        expected = {"N_X": T * self.SM[0], "N_Y": T * self.SM[1], "N_XY": T * self.SM[2],
                    "M_X": T * T / 12 * 2 * self.SB[0], "M_Y": 0.0, "M_XY": T * T / 12 * 2 * self.SB[2]}
        expected["N_MAX"], expected["N_MIN"] = principal2(*(T * s for s in self.SM))
        expected["M_MAX"], expected["M_MIN"] = principal2(*(T * T / 6 * s for s in self.SB))
        for q, value in expected.items():
            vals = res.values(0, q)
            self.assertTrue(all(v is not None for v in vals), q)
            for v in vals:  # ambas caras de cada punto dan el mismo esfuerzo
                self.assertAlmostEqual(v, value / 1000.0, delta=2e-5 * max(1.0, abs(value) / 1000.0), msg=q)

    def test_section_csv(self):
        import csv
        import io
        from core.frd import section_csv
        fe, mesh = setup()
        res = self.frd_with_bending(fe, mesh)
        rows = list(csv.reader(io.StringIO(section_csv(res, 0)), delimiter=";"))
        head = next(i for i, r in enumerate(rows) if r and r[0] == "Nodo")
        data = rows[head + 1:]
        self.assertEqual(len(data), len(fe.plan))
        col = {name: k for k, name in enumerate(rows[head])}
        for r in data[:20]:
            self.assertAlmostEqual(float(r[col["Nx"]].replace(",", ".")), T * self.SM[0] / 1000, places=3)
            self.assertAlmostEqual(float(r[col["Mx"]].replace(",", ".")), T * T / 6 * self.SB[0] / 1000, places=4)

    def test_2d_output_has_n_but_no_m(self):
        fe, mesh = setup(thickness_mode="VERTICAL", fea_output="2D")
        nodes = {k: p for k, p in enumerate(fe.nodes, 1)}
        steps = synthetic(fe, nodes, lambda n, p, k: [0.0, 0.0, 0.0], lambda n, p, k: [3e6, 0, 0, 0, 0, 0])
        res = load_results(parse_frd(frd_text(nodes, steps)), None, fe, mesh.plan_points, mesh.vertices)
        self.assertEqual(res.summary["section_forces"], "sólo N")
        nx = [v for v in res.values(0, "N_X") if v is not None]
        self.assertEqual(len(nx), len(mesh.vertices))
        # Espesor vertical: el espesor real por nodo es t·cos α; σxx global → N en ejes locales
        self.assertTrue(all(0.0 < v <= T * 3e6 / 1000.0 + 1e-6 for v in nx))
        self.assertTrue(all(v is None for v in res.values(0, "M_X")))
        self.assertIn("salida 2D", res.missing_reason(0, "M_X"))
        buckle = next(i for i, it in enumerate(res.items) if it.kind == "BUCKLE")
        self.assertTrue(all(v is None for v in res.values(buckle, "N_MAX")))
        self.assertIn("pandeo", res.missing_reason(buckle, "N_MAX"))


class TestExportAndKey(unittest.TestCase):
    def test_output_parameter_in_first_step_only(self):
        for mode in ("3D", "2D"):
            text = to_inp(setup(fea_output=mode)[0], "salida")
            self.assertEqual(text.count(f"*NODE FILE, OUTPUT={mode}"), 1)
            self.assertEqual(text.count(f"*EL FILE, OUTPUT={mode}"), 1)
            self.assertLess(text.index("OUTPUT="), text.index("*END STEP"))

    def test_results_key(self):
        base = dict(HYPAR)
        self.assertEqual(results_key(base), results_key({**base, "show_rulings": False, "grid_count_x": 3,
                                                         "formwork_enabled": True, "analysis": "SLOPE"}))
        self.assertNotEqual(results_key(base), results_key({**base, "thickness": 0.06}))
        self.assertNotEqual(results_key(base), results_key({**base, "fea_live_load": 2.0}))


if __name__ == "__main__":
    unittest.main()
