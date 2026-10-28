"""Pruebas del modelo de elementos finitos exportado (sin Blender ni solver).

    python -m unittest discover -s tests -v

El .inp se relee con un lector mínimo propio (independiente del escritor) para
comprobar referencias, conjuntos, pasos y sumas de cargas.
"""

import json
import math
import os
import sys
import unittest

ADDON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "candelalab")
sys.path.insert(0, ADDON_DIR)

from core.export import build_document  # noqa: E402
from core.fea import GRAVITY, fe_document, fe_model_from_params, to_inp  # noqa: E402
from core.pipeline import evaluate  # noqa: E402
from core.radial import PRESETS as RADIAL  # noqa: E402
from core.umbrella import PRESETS as UMBRELLA  # noqa: E402

K, H, A, B, T = 0.1, 3.0, 4.0, 3.0, 0.05  # z = H + K·x·y sobre [−A, A] × [−B, B]
HYPAR = {"shell_type": "HYPAR", "definition": "CORNERS", "span_x": 2 * A, "span_y": 2 * B,
         "z_sw": H + K * A * B, "z_se": H - K * A * B, "z_ne": H + K * A * B, "z_nw": H - K * A * B,
         "res_x": 8, "res_y": 6, "thickness": T, "thickness_mode": "VERTICAL",
         "support_layout": "ALL_CORNERS", "support_type": "PINNED"}
UMB = {"shell_type": "UMBRELLA", **UMBRELLA["INVERTED"][2], "res_x": 4, "res_y": 4, "support_type": "COLUMN"}


def fe(**kw):
    return fe_model_from_params({**HYPAR, **kw})[0]


def parse_inp(text):
    """Lector mínimo de .inp: nodos, elementos, conjuntos, secciones, apoyos y pasos."""
    m = {"nodes": {}, "elements": {}, "nsets": {}, "elsets": {}, "boundary": [], "steps": [],
         "sections": [], "nodal_thickness": {}, "keywords": []}
    kw, opts, step, ctx = None, {}, None, {}
    for line in text.splitlines():
        if line.startswith("**") or not line.strip():
            continue
        if line.startswith("*"):
            parts = [s.strip() for s in line[1:].split(",")]
            kw = parts[0].upper()
            opts = {}
            for q in parts[1:]:
                k, _, v = q.partition("=")
                opts[k.strip().upper()] = v.strip()
            m["keywords"].append(kw)
            if kw == "STEP":
                step = {"type": None, "grav": 0.0, "cload": {}, "modes": None}
                m["steps"].append(step)
            elif kw in ("STATIC", "BUCKLE"):
                step["type"] = kw
            elif kw == "ELEMENT":
                ctx = {"type": opts["TYPE"], "set": opts["ELSET"]}
                m["elsets"].setdefault(opts["ELSET"], [])
            elif kw == "NSET":
                ctx = {"set": opts["NSET"]}
                m["nsets"][opts["NSET"]] = []
            elif kw == "ELSET":
                ctx = {"set": opts["ELSET"]}
                m["elsets"].setdefault(opts["ELSET"], [])
            elif kw in ("SHELL SECTION", "BEAM SECTION"):
                m["sections"].append((kw, opts["ELSET"], opts))
            continue
        vals = [s.strip() for s in line.split(",") if s.strip()]
        if kw == "NODE":
            m["nodes"][int(vals[0])] = tuple(float(v) for v in vals[1:4])
        elif kw == "ELEMENT":
            m["elements"][int(vals[0])] = (ctx["type"], [int(v) for v in vals[1:]])
            m["elsets"][ctx["set"]].append(int(vals[0]))
        elif kw == "NSET":
            m["nsets"][ctx["set"]] += [int(v) for v in vals]
        elif kw == "ELSET":
            m["elsets"][ctx["set"]] += vals
        elif kw == "BOUNDARY":
            m["boundary"].append((vals[0], int(vals[1]), int(vals[2])))
        elif kw == "NODAL THICKNESS":
            m["nodal_thickness"][int(vals[0])] = float(vals[1])
        elif kw == "CLOAD":
            key = (int(vals[0]), int(vals[1]))
            step["cload"][key] = step["cload"].get(key, 0.0) + float(vals[2])
        elif kw == "DLOAD" and vals[1] == "GRAV":
            step["grav"] = float(vals[2])
        elif kw == "BUCKLE":
            step["modes"] = int(vals[0])
    return m


def shell_elements(m):
    return [(t, c) for t, c in m["elements"].values() if t.startswith("S")]


class TestQuadraticShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = fe()
        cls.m = parse_inp(to_inp(cls.fe, "prueba"))

    def test_nodes_lie_on_exact_mid_surface(self):
        # Espesor vertical con el intradós exacto: la superficie media es z = f + t/2.
        for x, y, z in self.m["nodes"].values():
            self.assertAlmostEqual(z, H + K * x * y + T / 2, places=12)

    def test_counts_and_no_orphan_nodes(self):
        nu, nv = 8, 6
        self.assertEqual(len(shell_elements(self.m)), nu * nv)
        self.assertEqual(len(self.m["nodes"]), (2 * nu + 1) * (2 * nv + 1) - nu * nv)   # sin centros
        used = {v for _t, c in self.m["elements"].values() for v in c}
        self.assertEqual(used, set(self.m["nodes"]))

    def test_s8r_geometry_and_orientation(self):
        nd = self.m["nodes"]
        for etype, c in shell_elements(self.m):
            self.assertEqual(etype, "S8R")
            corners, mids = c[:4], c[4:]
            area = sum(nd[a][0] * nd[b][1] - nd[b][0] * nd[a][1] for a, b in zip(corners, corners[1:] + corners[:1]))
            self.assertGreater(area, 0.0)                                    # normal hacia +Z
            for k, mid in enumerate(mids):                                   # nodo intermedio de cada lado
                a, b = nd[corners[k]], nd[corners[(k + 1) % 4]]
                self.assertAlmostEqual(nd[mid][0], 0.5 * (a[0] + b[0]), places=12)
                self.assertAlmostEqual(nd[mid][1], 0.5 * (a[1] + b[1]), places=12)

    def test_inp_references_are_consistent(self):
        m = self.m
        for _t, c in m["elements"].values():
            self.assertTrue(all(v in m["nodes"] for v in c))
        for name, _a, _b in m["boundary"]:
            self.assertIn(name, m["nsets"])
        for _kw, elset, _o in m["sections"]:
            self.assertIn(elset, m["elsets"])
        for members in m["elsets"].values():
            for item in members:
                self.assertTrue(isinstance(item, int) or item in m["elsets"], item)
        self.assertEqual(m["keywords"].count("STEP"), m["keywords"].count("END STEP"))
        to_inp(self.fe, "título con acentos").encode("ascii")              # sólo ASCII

    def test_linear_elements(self):
        m = parse_inp(to_inp(fe(fea_element_order="LINEAR"), "lineal"))
        self.assertEqual({t for t, _c in shell_elements(m)}, {"S4"})
        self.assertEqual(len(m["nodes"]), 9 * 7)
        refined = fe(fea_element_order="LINEAR", fea_refine=2).summary
        self.assertEqual(refined["elements"]["S4"], 4 * 8 * 6)


class TestLoads(unittest.TestCase):
    Q = {"fea_dead_load": 1.5, "fea_live_load": 2.0, "fea_pressure": 0.4,
         "fea_factor_dead": 1.2, "fea_factor_live": 1.6}

    @classmethod
    def setUpClass(cls):
        cls.fe = fe(**cls.Q)
        cls.m = parse_inp(to_inp(cls.fe, "cargas"))
        cls.rep = evaluate({**HYPAR, **cls.Q}).report

    def step_force(self, i):
        f = [0.0, 0.0, 0.0]
        for (_n, d), v in self.m["steps"][i]["cload"].items():
            f[d - 1] += v
        return f

    def test_case_totals(self):
        s = self.fe.summary
        area = self.rep["geometry"]["area_mid"]
        self.assertAlmostEqual(s["shell_area"] / area, 1.0, delta=1e-5)       # S8R sobre la superficie exacta
        self.assertAlmostEqual(s["plan_area"], 2 * A * 2 * B, places=9)
        names = [c.name for c in self.fe.cases]
        self.assertEqual(names, ["PP", "CM", "CV", "CV_MEDIA", "PRESION"])
        tot = s["totals_kN"]
        self.assertAlmostEqual(tot["CM"][2], -1.5 * s["shell_area"], places=9)
        self.assertAlmostEqual(tot["CV"][2], -2.0 * 48.0, places=9)
        self.assertAlmostEqual(tot["CV_MEDIA"][2], -2.0 * 24.0, places=9)
        # Resultante de una presión uniforme = p × área proyectada (aquí, simétrica en planta)
        self.assertAlmostEqual(tot["PRESION"][2], -0.4 * 48.0, places=9)
        self.assertAlmostEqual(tot["PRESION"][0], 0.0, places=9)
        self.assertAlmostEqual(s["weight_kN"]["shell"],
                               self.rep["concrete"]["weight_kN"], delta=1e-4 * self.rep["concrete"]["weight_kN"])

    def test_steps_carry_the_same_totals(self):
        steps = self.m["steps"]
        names = [c.name for c in self.fe.cases] + [c[0] for c in self.fe.combos]
        self.assertEqual(len(steps), len(names) + len(self.fe.buckling))
        tot = self.fe.summary["totals_kN"]
        for i, name in enumerate(names):
            f = self.step_force(i)
            grav = steps[i]["grav"]
            weight = sum(self.fe.summary["weight_kN"].values())
            for d in range(3):
                expected = tot[name][d] + (grav / GRAVITY * weight if d == 2 else 0.0)  # sin la gravedad
                self.assertAlmostEqual(f[d] / 1000.0, expected, places=6, msg=(name, d))
        comb = names.index("COMB")
        self.assertAlmostEqual(steps[comb]["grav"], 1.2 * GRAVITY, places=9)
        self.assertAlmostEqual(tot["COMB"][2], 1.2 * (tot["PP"][2] + tot["CM"][2]) + 1.6 * tot["CV"][2], places=9)
        self.assertEqual([s["type"] for s in steps[-2:]], ["BUCKLE", "BUCKLE"])
        self.assertEqual(steps[-1]["modes"], 5)

    def test_consistent_loads_include_negative_corners(self):
        # Carga uniforme en S8R: las esquinas reciben fuerzas de signo contrario (correcto).
        cm = self.fe.cases[1].forces
        corners = {c[0] for _t, _s, conns in self.fe.blocks for c in conns if _t == "S8R"}
        self.assertTrue(any(cm[v][2] > 0.0 for v in corners))

    def test_only_self_weight_by_default(self):
        f = fe()
        self.assertEqual([c.name for c in f.cases], ["PP"])
        self.assertIn("FEA_ONLY_SELF_WEIGHT", {c["code"] for c in f.checks})


class TestSupports(unittest.TestCase):
    def test_mechanism_detection(self):
        two = fe(support_layout="LOW_CORNERS")
        self.assertEqual(two.summary["rigid_body_modes"], 1)                    # giro alrededor de la diagonal
        self.assertIn("FEA_MECHANISM", {c["code"] for c in two.checks if c["level"] == "WARNING"})
        self.assertEqual(fe().summary["rigid_body_modes"], 0)                     # 4 esquinas articuladas
        self.assertEqual(fe(support_layout="LOW_CORNERS", support_type="FIXED").summary["rigid_body_modes"], 0)
        wide = fe(support_layout="LOW_CORNERS", support_size=1.5)                 # huellas con varios nodos
        self.assertGreater(min(s["nodes"] for s in wide.summary["supports"]), 1)
        self.assertEqual(wide.summary["rigid_body_modes"], 0)
        none = fe(support_layout="NONE")
        self.assertEqual(none.summary["rigid_body_modes"], 6)
        self.assertIn("FEA_NO_SUPPORTS", {c["code"] for c in none.checks if c["level"] == "ERROR"})

    def test_umbrella_column(self):
        f = fe_model_from_params(UMB)[0]
        m = parse_inp(to_inp(f, "paraguas"))
        cols = [c for t, c in m["elements"].values() if t == "B32"]
        self.assertEqual(len(cols), 4)
        base = m["nsets"]["NB_C"]
        self.assertEqual(len(base), 1)
        self.assertAlmostEqual(m["nodes"][base[0]][2], 0.0, places=12)
        top = cols[-1][-1]
        self.assertAlmostEqual(m["nodes"][top][0], 0.0, places=12)
        self.assertTrue(any(top in c for t, c in m["elements"].values() if t == "S8R"))   # unida al cascarón
        self.assertEqual(m["boundary"], [("NB_C", 1, 6)])
        self.assertEqual(f.summary["rigid_body_modes"], 0)
        self.assertEqual(f.summary["components"], 1)
        self.assertEqual(fe_model_from_params({**UMB, "fea_columns": "PINNED_BASE"})[0].summary["rigid_body_modes"], 3)
        head = fe_model_from_params({**UMB, "fea_columns": "HEAD"})[0]
        self.assertNotIn("B32", head.summary["elements"])
        self.assertEqual(head.boundary, [("NS_C", 1, 6)])

    def test_edge_beams_as_webs(self):
        f = fe(beam_xmin=True, beam_ymax=True, beam_width=0.2, beam_depth=0.3)
        m = parse_inp(to_inp(f, "vigas"))
        webs = {k: v for k, v in m["elsets"].items() if k.startswith("EVIGA_")}
        self.assertEqual(set(webs), {"EVIGA_XMIN", "EVIGA_YMAX"})
        self.assertEqual(len(webs["EVIGA_XMIN"]), 6)                            # una por elemento del borde
        self.assertEqual(f.summary["components"], 1)                            # comparten nodos con la lámina
        nd = m["nodes"]
        for eid in webs["EVIGA_XMIN"]:
            c = m["elements"][eid][1]
            self.assertTrue(all(abs(nd[v][0] + A) < 1e-12 for v in c))          # plano vertical x = −A
            p0, p1, p3 = nd[c[0]], nd[c[1]], nd[c[3]]
            n = ((p1[1] - p0[1]) * (p3[2] - p0[2]) - (p1[2] - p0[2]) * (p3[1] - p0[1]))
            self.assertLess(n, 0.0)                                             # normal hacia −X (afuera)
            bottom = min(nd[v][2] for v in c[:4])
            top = max(nd[v][2] for v in c[:4])
            self.assertLess(bottom, top)
        # Alma vertical de altura t/2 + d sobre el borde: peso = ρ·g·b·(t/2 + d)·longitud en planta
        expected = 2400 * GRAVITY * 0.2 * (T / 2 + 0.3) * (2 * B + 2 * A) / 1000.0
        self.assertAlmostEqual(f.summary["weight_kN"]["edge_beams"], expected, places=6)

    def test_nodal_thickness_with_vertical_thickness(self):
        m = parse_inp(to_inp(fe(), "espesor"))
        self.assertTrue(any("NODAL THICKNESS" in o for kw, s, o in m["sections"] if s == "ECASCARON"))
        for v, t in list(m["nodal_thickness"].items())[:50]:
            x, y, _z = m["nodes"][v]
            self.assertAlmostEqual(t, T / math.sqrt(1 + (K * y) ** 2 + (K * x) ** 2), places=9)
        normal = parse_inp(to_inp(fe(thickness_mode="NORMAL"), "espesor"))
        self.assertEqual(normal["nodal_thickness"], {})


class TestOtherShells(unittest.TestCase):
    def test_radial_vault_with_fan_triangles(self):
        for key, (_l, _d, params) in RADIAL.items():
            p = {"shell_type": "RADIAL", **params, "res_x": 4, "res_y": 3, "thickness_mode": "VERTICAL"}
            f, rep = fe_model_from_params(p)
            m = parse_inp(to_inp(f, key))
            n = params["radial_count"]
            self.assertEqual(f.summary["elements"], {"S6": n * 3, "S8R": n * 3 * 3}, key)
            self.assertEqual(f.summary["components"], 1, key)
            self.assertAlmostEqual(f.summary["shell_area"] / rep["geometry"]["area_mid"], 1.0, delta=2e-3)
            ev = evaluate(p)
            for x, y, z in m["nodes"].values():  # nodos sobre la superficie media exacta (limas incluidas)
                self.assertAlmostEqual(z, ev.model.patch_at(x, y).surface.z(x, y) + p.get("thickness", 0.04) / 2,
                                       places=9, msg=key)

    def test_grid_exports_one_module(self):
        f = fe_model_from_params({**UMB, "grid_count_x": 3, "grid_count_y": 2})[0]
        single = fe_model_from_params(UMB)[0]
        self.assertEqual(len(f.nodes), len(single.nodes))
        self.assertIn("FEA_GRID", {c["code"] for c in f.checks})

    def test_json_documents(self):
        f = fe(fea_dead_load=1.0)
        doc = json.loads(json.dumps(fe_document(f)))
        self.assertEqual(len(doc["nodes"]), len(f.nodes))
        cm = next(c for c in doc["load_cases"] if c["name"] == "CM")
        self.assertAlmostEqual(sum(r[3] for r in cm["nodal_forces"]) / 1000.0, f.summary["totals_kN"]["CM"][2],
                               places=9)
        full = json.loads(json.dumps(build_document(evaluate({**HYPAR, "fea_dead_load": 1.0}), include_mesh=False,
                                                    include_fea=True)))
        self.assertEqual(len(full["fea"]["elements"]), sum(f.summary["elements"].values()))


if __name__ == "__main__":
    unittest.main()
