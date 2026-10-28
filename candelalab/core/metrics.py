"""Métricas geométricas calculadas a partir de las ecuaciones (no de la malla).

Áreas
-----
    A_ref = ∬_D W dx dy,   W = √(1 + f_x² + f_y²)

Capa desfasada una distancia s según la normal (fórmula de Steiner):
    A(s) = ∬_D (1 − 2·H·s + K·s²) · W dx dy
Con desfase vertical, A(s) = A_ref (es una traslación).

Volumen del sólido entre s0 y s1
--------------------------------
    VERTICAL: V = (s1 − s0) · Área_planta                          (exacto)
    NORMAL:   V = ∬_D [Δs − H·Δ(s²) + K·Δ(s³)/3] · W dx dy          (Steiner)
Con referencia en la superficie media el término en H se anula:
    V = ∬ (t + K·t³/12) dA.  Como K < 0 en una hypar, V < t·A_ref.

Las integrales se evalúan con Gauss-Legendre compuesta sobre la aplicación
bilineal del dominio; el integrando es analítico y el error es despreciable
(< 1e-10 relativo con la regla por defecto).
"""

from __future__ import annotations

import math

from .geometry import Vec2
from .model import NORMAL, VERTICAL, ShellPatch, ThicknessSpec
from .quadrature import unit_square_rule


def surface_integrals(patch: ShellPatch, thickness: ThicknessSpec, slope_limit: float,
                      panels: int = 8, order: int = 8) -> dict:
    surf, dom = patch.surface, patch.domain
    s0, s1 = thickness.offsets
    sm = 0.5 * (s0 + s1)
    ds = s1 - s0
    ds2 = s1 ** 2 - s0 ** 2
    ds3 = s1 ** 3 - s0 ** 3
    ds4 = s1 ** 4 - s0 ** 4
    tan_lim = math.tan(slope_limit)

    plan_area = area_ref = area_in = area_out = area_mid = vol = steep = 0.0
    mx = my = mz = 0.0  # momentos de volumen
    for s, t, w in unit_square_rule(panels, order):
        x, y = dom.map(s, t)
        da = w * abs(dom.jacobian(s, t))
        fx, fy = surf.gradient(x, y)
        wf = math.sqrt(1.0 + fx * fx + fy * fy)
        k_g, h_m = surf.curvatures(x, y)
        z = surf.z(x, y)
        dA = wf * da
        plan_area += da
        area_ref += dA
        if math.hypot(fx, fy) > tan_lim:
            steep += dA
        if thickness.mode == VERTICAL:
            area_in += dA
            area_out += dA
            area_mid += dA
            dv = ds * da
            vol += dv
            mx += x * dv
            my += y * dv
            mz += (z + sm) * dv
        else:
            area_in += (1.0 - 2.0 * h_m * s0 + k_g * s0 * s0) * dA
            area_out += (1.0 - 2.0 * h_m * s1 + k_g * s1 * s1) * dA
            area_mid += (1.0 - 2.0 * h_m * sm + k_g * sm * sm) * dA
            i0 = ds - h_m * ds2 + k_g * ds3 / 3.0
            i1 = 0.5 * ds2 - 2.0 * h_m * ds3 / 3.0 + 0.25 * k_g * ds4
            nx, ny, nz = -fx / wf, -fy / wf, 1.0 / wf
            vol += i0 * dA
            mx += (x * i0 + nx * i1) * dA
            my += (y * i0 + ny * i1) * dA
            mz += (z * i0 + nz * i1) * dA
    if thickness.mode == VERTICAL:
        vol = ds * dom.area  # exacto; la cuadratura sólo se usa para el centroide
    return {
        "plan_area": dom.area,
        "plan_area_quadrature": plan_area,
        "area_reference": area_ref,
        "area_intrados": area_in,
        "area_extrados": area_out,
        "area_mid": area_mid,
        "volume": vol,
        "moment": (mx, my, mz),
        "centroid": (mx / vol, my / vol, mz / vol) if vol > 0 else (0.0, 0.0, 0.0),
        "steep_area": steep,
        "steep_area_fraction": steep / area_ref if area_ref > 0 else 0.0,
    }


def boundary_extrema(domain, g) -> tuple[float, float]:
    """(min, max) de g(x, y) sobre el contorno del dominio.

    Sobre cada segmento se supone g cuadrática en el parámetro (es el caso de
    f y de |∇f|² para una superficie cuadrática): tres evaluaciones fijan la
    parábola y el extremo interior se obtiene en forma cerrada (exacto).
    Sobre un borde curvo (arco, corte por plano) se muestrea y se refina por
    sección áurea.
    """
    lo, hi = math.inf, -math.inf
    for piece in domain.boundary_pieces():
        if piece[0] == "segment":
            (ax, ay), (bx, by) = piece[1], piece[2]

            def at(tau, ax=ax, ay=ay, bx=bx, by=by):
                return g(ax + tau * (bx - ax), ay + tau * (by - ay))

            g0, gm, g1 = at(0.0), at(0.5), at(1.0)
            alpha = 2.0 * (g0 - 2.0 * gm + g1)
            beta = g1 - g0 - alpha
            vals = [g0, g1]
            if abs(alpha) > 1e-300:
                tau = -beta / (2.0 * alpha)
                if 0.0 < tau < 1.0:
                    vals.append(at(tau))
        else:
            curve = piece[1]

            def at(tau, curve=curve):
                return g(*curve(tau))

            vals = [_golden(at), -_golden(lambda u: -at(u))]
        lo, hi = min(lo, *vals), max(hi, *vals)
    return lo, hi


def _golden(h, samples: int = 64, iters: int = 60) -> float:
    """Mínimo de h en [0, 1]: muestreo y refinamiento por sección áurea."""
    taus = [i / samples for i in range(samples + 1)]
    vals = [h(t) for t in taus]
    i = min(range(len(vals)), key=vals.__getitem__)
    a, b = taus[max(i - 1, 0)], taus[min(i + 1, samples)]
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c, d = b - gr * (b - a), a + gr * (b - a)
    hc, hd = h(c), h(d)
    for _ in range(iters):
        if hc < hd:
            b, d, hd = d, c, hc
            c = b - gr * (b - a)
            hc = h(c)
        else:
            a, c, hc = c, d, hd
            d = a + gr * (b - a)
            hd = h(d)
    return min(vals[i], hc, hd)


def sample_extrema(patch: ShellPatch, thickness: ThicknessSpec, n: int = 33) -> dict:
    """Extremos de pendiente y curvatura, y validez del desfase normal.

    * La pendiente máxima es exacta: |∇f|² es una función convexa (∇f es afín),
      así que su máximo sobre un dominio convexo está en el contorno (en una
      esquina si el dominio es poligonal).
    * El rango de z también está en el contorno: una hypar no tiene extremos
      interiores (su único punto crítico es de silla).
    * Curvaturas: muestreo en rejilla n×n más el punto crítico de f si cae
      dentro del dominio (ahí W = 1 y |K| es máximo).
    * Desfase normal regular <=> (1 − s·κ1)(1 − s·κ2) > 0 para s en [s0, s1];
      se informa el mínimo de los factores 1 − s·κi.
    """
    surf, dom = patch.surface, patch.domain
    pts: list[Vec2] = [dom.map(i / (n - 1), j / (n - 1)) for j in range(n) for i in range(n)]
    crit = surf.critical_point()
    if crit is not None and dom.contains(crit):
        pts.append(crit)

    def grad2(x, y):
        fx, fy = surf.gradient(x, y)
        return fx * fx + fy * fy

    slope_max = math.atan(math.sqrt(boundary_extrema(dom, grad2)[1]))
    slope_min = min(surf.slope(x, y) for x, y in pts)
    k_min, k_max = math.inf, -math.inf
    kappa_max = 0.0
    fold_factor = math.inf
    s_vals = thickness.offsets if thickness.mode == NORMAL else ()
    for x, y in pts:
        k_g, _ = surf.curvatures(x, y)
        k1, k2 = surf.principal_curvatures(x, y)
        k_min, k_max = min(k_min, k_g), max(k_max, k_g)
        kappa_max = max(kappa_max, abs(k1), abs(k2))
        for s in s_vals:
            fold_factor = min(fold_factor, 1.0 - s * k1, 1.0 - s * k2)
    zmin, zmax = boundary_extrema(dom, surf.z)
    return {
        "slope_max": slope_max,
        "slope_min": slope_min,
        "gauss_min": k_min,
        "gauss_max": k_max,
        "kappa_max": kappa_max,
        "radius_min": 1.0 / kappa_max if kappa_max > 0 else math.inf,
        "offset_fold_factor": fold_factor if s_vals else None,
        "z_ref_min": zmin,
        "z_ref_max": zmax,
    }


def chord_error(patch: ShellPatch, nu: int, nv: int) -> float:
    """Máxima diferencia vertical entre la superficie exacta y la interpolación
    bilineal de las 4 esquinas de cada celda, evaluada en el centro y en los
    puntos medios de las aristas.

    Para f cuadrática sobre una celda rectangular el error es
    e(σ,τ) = A·σ(1−σ) + B·τ(1−τ), cuyo máximo está en esos 5 puntos, así que la
    medida es exacta en rectángulos.

    Es 0 cuando la rejilla sigue las generatrices (bordes rectos, θ = 0): cada
    celda es entonces un trozo de la misma hypar (doble reglado exacto).
    """
    surf, dom = patch.surface, patch.domain
    probes = ((0.5, 0.5), (0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5))
    worst = 0.0
    for j in range(nv):
        for i in range(nu):
            s_a, t_a, ds, dt = i / nu, j / nv, 1.0 / nu, 1.0 / nv
            z00 = surf.z(*dom.map(s_a, t_a))
            z10 = surf.z(*dom.map(s_a + ds, t_a))
            z11 = surf.z(*dom.map(s_a + ds, t_a + dt))
            z01 = surf.z(*dom.map(s_a, t_a + dt))
            for a, b in probes:
                zb = (1 - a) * (1 - b) * z00 + a * (1 - b) * z10 + a * b * z11 + (1 - a) * b * z01
                ze = surf.z(*dom.map(s_a + a * ds, t_a + b * dt))
                worst = max(worst, abs(ze - zb))
    return worst


def thickness_report(thickness: ThicknessSpec, extrema: dict) -> dict:
    """Relación entre espesor vertical y espesor normal (primer orden: t_n ≈ t_v·cos α)."""
    t = thickness.thickness
    cos_min = math.cos(extrema["slope_max"])
    if thickness.mode == VERTICAL:
        return {"normal_min": t * cos_min, "normal_max": t, "vertical_min": t, "vertical_max": t}
    return {"normal_min": t, "normal_max": t, "vertical_min": t, "vertical_max": t / cos_min}


# --- nivel modelo (varios parches) ----------------------------------------------------


def crease_corrections(model, order: int = 24) -> dict:
    """Corrección por inglete en los pliegues (limas/cumbreras), espesor NORMAL.

    La fórmula de Steiner de cada parche cuenta la región {p + s·n(p)}, cuyo
    canto en el pliegue sigue la normal de ESE parche. El sólido real lleva
    inglete: en el lado donde las normales divergen queda una cuña adicional y
    en el lado donde convergen las dos regiones se solapan. En la sección
    perpendicular al pliegue, con δ el ángulo entre las normales, la cuña entre
    los desfases 0 y s es un deltoide de área s²·tan(δ/2). Por unidad de
    longitud de pliegue:

        ΔV    = σ · tan(δ/2) · (s1² − s0²)
        ΔA(s) = σ · 2·s · tan(δ/2)          (= dΔV/ds)

    σ = +1 en una cumbrera (normales divergentes hacia +n), −1 en un valle.
    Con la superficie media como referencia (s0 = −s1) ΔV = 0. Es exacta a
    primer orden; el error es O(t³) (incluye el vértice donde concurren
    varios pliegues, que no se corrige).
    """
    from .quadrature import gauss_legendre

    th = model.thickness
    s0, s1 = th.offsets
    sm = 0.5 * (s0 + s1)
    out = {"volume": 0.0, "area_intrados": 0.0, "area_extrados": 0.0, "area_mid": 0.0,
           "delta_min": math.inf, "delta_max": 0.0, "valleys": 0, "ridges": 0, "mixed": 0,
           "length": 0.0, "valley_length": 0.0, "ridge_length": 0.0}
    if not model.creases:
        return out
    xs, ws = gauss_legendre(order)
    for cr in model.creases:
        length = math.dist(cr.start, cr.end)
        valley = 0.0
        for xg, wg in zip(xs, ws):
            tau, weight = 0.5 * (xg + 1.0), 0.5 * wg
            sigma, tan_half, cosd, density = crease_wedge(model, cr, tau)
            dl = length * density * weight
            out["volume"] += sigma * tan_half * (s1 * s1 - s0 * s0) * dl
            out["area_intrados"] += sigma * 2.0 * s0 * tan_half * dl
            out["area_extrados"] += sigma * 2.0 * s1 * tan_half * dl
            out["area_mid"] += sigma * 2.0 * sm * tan_half * dl
            out["length"] += dl
            delta = math.degrees(math.acos(cosd))
            out["delta_min"] = min(out["delta_min"], delta)
            out["delta_max"] = max(out["delta_max"], delta)
            out["valley_length" if sigma < 0 else "ridge_length"] += dl
            if sigma < 0:
                valley += weight
        # Una lima puede cambiar de valle a cumbrera donde las normales coinciden.
        out["valleys" if valley >= 0.9 else "ridges" if valley <= 0.1 else "mixed"] += 1
    if th.mode == VERTICAL:  # el desfase vertical es continuo: no hay cuñas
        for key in ("volume", "area_intrados", "area_extrados", "area_mid"):
            out[key] = 0.0
    return out


def crease_wedge(model, cr, tau: float) -> tuple[float, float, float, float]:
    """En el punto τ ∈ [0, 1] de una lima: (σ, tan(δ/2), cos δ, dℓ/ds), con σ = +1 en una
    cumbrera y −1 en un valle, δ el ángulo entre las normales y dℓ/ds la longitud 3D por
    unidad de longitud en planta."""
    pa, pb = model.patches[cr.patch_a], model.patches[cr.patch_b]
    (x0, y0), (x1, y1) = cr.start, cr.end
    length = math.dist(cr.start, cr.end)
    ex, ey = (x1 - x0) / length, (y1 - y0) / length
    wx, wy = -ey, ex  # perpendicular en planta, hacia el interior del parche a
    cx, cy = pa.domain.centroid
    if (cx - x0) * wx + (cy - y0) * wy < 0.0:
        wx, wy = -wx, -wy
    x, y = x0 + tau * (x1 - x0), y0 + tau * (y1 - y0)
    gx, gy = pa.surface.gradient(x, y)
    na = pa.surface.unit_normal(x, y)
    nb = pb.surface.unit_normal(x, y)
    cosd = max(-1.0, min(1.0, na[0] * nb[0] + na[1] * nb[1] + na[2] * nb[2]))
    tan_half = math.sqrt(max(0.0, (1.0 - cosd) / (1.0 + cosd)))
    u = (wx, wy, gx * wx + gy * wy)
    sigma = 1.0 if nb[0] * u[0] + nb[1] * u[1] + nb[2] * u[2] < 0.0 else -1.0
    return sigma, tan_half, cosd, math.sqrt(1.0 + (gx * ex + gy * ey) ** 2)


def model_integrals(model, slope_limit: float) -> dict:
    """Suma de las integrales de todos los parches + corrección de pliegues."""
    keys = ("plan_area", "area_reference", "area_intrados", "area_extrados", "area_mid", "volume", "steep_area")
    tot = dict.fromkeys(keys, 0.0)
    mom = [0.0, 0.0, 0.0]
    # Con varios parches basta una regla más corta (error < 1e-12 en estos integrandos).
    panels = 8 if len(model.patches) == 1 else 3
    for patch in model.patches:
        r = surface_integrals(patch, model.thickness, slope_limit, panels=panels)
        for k in keys:
            tot[k] += r[k]
        for i in range(3):
            mom[i] += r["moment"][i]
    corr = crease_corrections(model)
    for k in ("volume", "area_intrados", "area_extrados", "area_mid"):
        tot[k] += corr[k]
    vol = tot["volume"]
    # El centroide no incluye las cuñas de inglete (efecto de segundo orden).
    tot["centroid"] = tuple(m / vol for m in mom) if vol > 0 else (0.0, 0.0, 0.0)
    tot["steep_area_fraction"] = tot["steep_area"] / tot["area_reference"] if tot["area_reference"] else 0.0
    tot["creases"] = corr
    return tot


def model_extrema(model) -> dict:
    n = 33 if len(model.patches) == 1 else 21
    parts = [sample_extrema(p, model.thickness, n) for p in model.patches]
    folds = [p["offset_fold_factor"] for p in parts if p["offset_fold_factor"] is not None]
    kappa = max(p["kappa_max"] for p in parts)
    return {
        "slope_max": max(p["slope_max"] for p in parts),
        "slope_min": min(p["slope_min"] for p in parts),
        "gauss_min": min(p["gauss_min"] for p in parts),
        "gauss_max": max(p["gauss_max"] for p in parts),
        "kappa_max": kappa,
        "radius_min": 1.0 / kappa if kappa > 0 else math.inf,
        "offset_fold_factor": min(folds) if folds else None,
        "z_ref_min": min(p["z_ref_min"] for p in parts),
        "z_ref_max": max(p["z_ref_max"] for p in parts),
    }
