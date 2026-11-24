#!/usr/bin/env python3
"""Standardized engineering calculations for OBER chemical absorption/desorption and drying experiments."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from typing import Any, Iterable


def _interp_table(temp_c: float, table: dict[float, float]) -> float:
    """Linearly interpolate a temperature-property lookup table (clamped at ends)."""
    pts = sorted(table.items(), key=lambda kv: kv[0])
    if temp_c <= pts[0][0]:
        return float(pts[0][1])
    if temp_c >= pts[-1][0]:
        return float(pts[-1][1])
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= temp_c <= t1:
            if abs(t1 - t0) < 1e-12:
                return float(v0)
            frac = (temp_c - t0) / (t1 - t0)
            return float(v0 + frac * (v1 - v0))
    return float(pts[-1][1])


def _parse_mmss_to_minutes(raw: str) -> float:
    """Parse MM:SS(.cc) or HH:MM:SS(.cc) timer string into decimal minutes."""
    text = raw.replace("：", ":").strip()
    m_hms = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", text)
    if m_hms:
        return int(m_hms.group(1)) * 60.0 + int(m_hms.group(2)) + float(m_hms.group(3)) / 60.0
    m_ms = re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)", text)
    if m_ms:
        return int(m_ms.group(1)) + float(m_ms.group(2)) / 60.0
    return float(text)


def calc_fluidized_bed(time_mmss: str, w1: float, w2: float, w0: float = 32.0) -> dict[str, float]:
    """Compute fluidized-bed drying sample time (min), moisture mass, dry solid mass, and moisture %."""
    t_min = _parse_mmss_to_minutes(time_mmss)
    delta_w = float(w1) - float(w2)
    dry_w = float(w2) - float(w0)
    if dry_w <= 0:
        raise ValueError(f"干物重必须为正数: w2({w2}) - w0({w0}) = {dry_w}")
    moisture_pct = (delta_w / dry_w) * 100.0
    return {
        "time_min": round(t_min, 2),
        "delta_w_g": round(delta_w, 4),
        "dry_w_g": round(dry_w, 4),
        "moisture_pct_1e2": round(moisture_pct, 2),
    }


def calc_packed_hydro(
    vn: float,
    t1: float,
    dh_mm: float = 0.0,
    d: float = 0.10,
    z: float = 0.54,
    rho_u: float = 1000.0,
) -> dict[str, float]:
    """Compute packed-column hydrodynamics: corrected gas flow, superficial velocity u, and ΔP/Z."""
    area = math.pi * (float(d) ** 2) / 4.0
    dp_pa = float(rho_u) * 9.81 * (float(dh_mm) / 1000.0)
    p_atm = 101325.0
    v_corr_nodp = float(vn) * math.sqrt((273.0 + float(t1)) / 293.0)
    v_corr_dp = float(vn) * math.sqrt((p_atm * (273.0 + float(t1))) / ((p_atm + dp_pa) * 293.0))
    u_nodp = v_corr_nodp / (3600.0 * area)
    u_dp = v_corr_dp / (3600.0 * area)
    return {
        "area_m2": round(area, 6),
        "v_corr_m3h": round(v_corr_dp, 4),
        "v_corr_nodp_m3h": round(v_corr_nodp, 4),
        "u_ms": round(u_dp, 4),
        "u_nodp_ms": round(u_nodp, 4),
        "dp_pa": round(dp_pa, 2),
        "dp_per_z_pa_m": round(dp_pa / float(z), 2),
    }


_CO2_M_TABLE: dict[float, float] = {10.0: 1054.0, 15.0: 1236.0, 20.0: 1421.0, 25.0: 1620.0, 30.0: 1836.0}
_NH3_M_TABLE: dict[float, float] = {
    10.0: 0.48,
    15.0: 0.60,
    20.0: 0.77,
    23.0: 0.89,
    25.0: 0.98,
    28.0: 1.11,
    30.0: 1.21,
}
_ACETONE_M_TABLE: dict[float, float] = {10.0: 0.881, 20.0: 1.49, 25.0: 1.98, 30.0: 2.43, 40.0: 3.83}


def calc_co2_absorption(
    vn_air: float,
    vn_co2: float,
    l_lh: float,
    t1: float,
    t2: float,
    t3: float,
    t4: float,
    dh_mm: float = 0.0,
    y1: float = 0.0,
    y2: float = 0.0,
    d: float = 0.10,
    z: float = 0.54,
    rho_u: float = 1000.0,
    rho_air20: float = 1.204,
    rho_co2_20: float = 1.839,
    m_override: float | None = None,
) -> dict[str, float]:
    """Compute CO2 absorption mass-transfer quantities."""
    p_atm = 101325.0
    dp_pa = float(rho_u) * 9.81 * (float(dh_mm) / 1000.0)
    v_air = float(vn_air) * math.sqrt((p_atm * (273.0 + float(t1))) / ((p_atm + dp_pa) * 293.0))
    v_co2 = float(vn_co2) * math.sqrt((float(rho_air20) * (273.0 + float(t2))) / (float(rho_co2_20) * 293.0))
    area = math.pi * (float(d) ** 2) / 4.0
    vp = area * float(z)
    v_kmol_h = (v_air / 22.4) * (273.0 / (273.0 + float(t1)))
    l_kmol_h = (float(l_lh) / 1000.0) * (1000.0 / 18.0)

    eff_y1 = float(y1) if y1 > 0 else v_co2 / (v_air + v_co2)
    eff_y2 = float(y2)
    m_val = float(m_override) if m_override is not None else _interp_table((float(t3) + float(t4)) / 2.0, _CO2_M_TABLE)
    out: dict[str, float] = {
        "Vp_m3": round(vp, 6),
        "V_air_corr_m3h": round(v_air, 4),
        "V_co2_corr_m3h": round(v_co2, 5),
        "V_kmol_h": round(v_kmol_h, 5),
        "L_kmol_h": round(l_kmol_h, 5),
        "m": round(m_val, 3),
        "Y1_ratio": round(eff_y1 / max(1e-12, 1.0 - eff_y1), 6),
    }
    if eff_y2 > 0:
        big_y1 = eff_y1 / max(1e-12, 1.0 - eff_y1)
        big_y2 = eff_y2 / max(1e-12, 1.0 - eff_y2)
        x1 = (v_kmol_h * (big_y1 - big_y2)) / max(1e-12, l_kmol_h)
        y1_star = m_val * x1
        denom_ratio = (big_y1 - y1_star) / max(1e-12, big_y2)
        dym = ((big_y1 - y1_star) - big_y2) / math.log(denom_ratio) if denom_ratio > 0 and abs(denom_ratio - 1.0) > 1e-9 else big_y2
        nog = (big_y1 - big_y2) / dym
        hog = float(z) / nog
        kya = v_kmol_h / (hog * area)
        out.update(
            {
                "Y2_ratio": round(big_y2, 6),
                "X1_ratio": round(x1, 8),
                "Y1_star": round(y1_star, 6),
                "dYm": round(dym, 6),
                "NOG": round(nog, 4),
                "HOG_m": round(hog, 4),
                "KYa_kmol_m3_h": round(kya, 4),
                "phi_pct": round((big_y1 - big_y2) / big_y1 * 100.0, 2),
            }
        )
    return out


def calc_ammonia_absorption(
    vn_air: float,
    vn_nh3: float,
    l_lh: float,
    dh_mm: float,
    t1: float,
    t2: float,
    t3: float,
    t4: float,
    v_eud_ml: float,
    v_sample_ml: float,
    v_h2so4_abs_ml: float,
    c_exh: float = 0.006313,
    v_exh_ml: float = 5.00,
    c_abs: float = 0.05148,
    d: float = 0.10,
    z: float = 0.54,
    rho_u: float = 1000.0,
    rho_air20: float = 1.204,
    rho_nh3_20: float = 0.707,
) -> dict[str, float]:
    """Compute all 23 ammonia-water absorption P03 quantities per official manual."""
    p_atm = 101325.0
    dp_pa = float(rho_u) * 9.81 * (float(dh_mm) / 1000.0)
    v_air = float(vn_air) * math.sqrt((p_atm * (273.0 + float(t1))) / ((p_atm + dp_pa) * 293.0))
    v_nh3 = float(vn_nh3) * math.sqrt((float(rho_air20) * (273.0 + float(t2))) / (float(rho_nh3_20) * 293.0))
    m_val = _interp_table(float(t4), _NH3_M_TABLE)

    big_y1 = v_nh3 / v_air
    big_y2 = (2.0 * float(c_exh) * float(v_exh_ml) * 22.4) / (float(v_eud_ml) * (273.0 / (273.0 + float(t3))))
    c1 = 2.0 * float(c_abs) * float(v_h2so4_abs_ml) / float(v_sample_ml)
    x1 = c1 / (1000.0 / 18.0)
    y1_star = m_val * x1
    dym = ((big_y1 - y1_star) - big_y2) / math.log((big_y1 - y1_star) / big_y2)
    nog = (big_y1 - big_y2) / dym
    hog = float(z) / nog
    v_kmol_h = (v_air / 22.4) * (273.0 / (273.0 + float(t1)))
    area = math.pi * (float(d) ** 2) / 4.0
    kya = v_kmol_h / (hog * area)
    phi_pct = (big_y1 - big_y2) / big_y1 * 100.0

    return {
        "V_air_m3h": round(v_air, 4),
        "V_nh3_m3h": round(v_nh3, 4),
        "L_lh": round(float(l_lh), 2),
        "m": round(m_val, 4),
        "Y1": round(big_y1, 5),
        "Y2": round(big_y2, 6),
        "C1_mol_L": round(c1, 6),
        "X1": round(x1, 6),
        "Y1_star": round(y1_star, 6),
        "dYm": round(dym, 5),
        "NOG": round(nog, 3),
        "HOG_m": round(hog, 4),
        "V_kmol_h": round(v_kmol_h, 4),
        "KYa_kmol_m3_h": round(kya, 2),
        "phi_pct": round(phi_pct, 2),
    }


def calc_acetone_absorption(
    vn2: float,
    l2_lh: float,
    t1: float,
    t2: float,
    t3: float,
    t4: float,
    y1: float,
    y2: float,
    d: float = 0.050,
    z: float = 0.700,
) -> dict[str, float]:
    """Compute all 21 acetone-air-water absorption P03 quantities per official manual."""
    vp = math.pi / 4.0 * (float(d) ** 2) * float(z)
    v2 = float(vn2) * math.sqrt((273.0 + float(t1)) / 293.0)
    v_mol_s = v2 * 293.0 * 1000.0 / (22.4 * (273.0 + float(t1)) * 3600.0)
    l_mol_s = float(l2_lh) / 64.8
    big_y1 = float(y1) / (1.0 - float(y1))
    big_y2 = float(y2) / (1.0 - float(y2))
    x1 = v_mol_s * (big_y1 - big_y2) / l_mol_s
    t_avg = (float(t3) + float(t4)) / 2.0
    m_val = _interp_table(t_avg, _ACETONE_M_TABLE)
    y1_star = m_val * x1
    dym = ((big_y1 - y1_star) - big_y2) / math.log((big_y1 - y1_star) / big_y2)
    na = v_mol_s * (big_y1 - big_y2)
    kya = na / (vp * dym)
    phi_pct = (big_y1 - big_y2) / big_y1 * 100.0

    return {
        "T1_C": round(float(t1), 2),
        "F2_Nm3h": round(float(vn2), 4),
        "V2_m3h": round(v2, 4),
        "L2_Lh": round(float(l2_lh), 2),
        "T3_C": round(float(t3), 2),
        "T2_C": round(float(t2), 2),
        "T4_C": round(float(t4), 2),
        "y1": round(float(y1), 6),
        "Y1": round(big_y1, 5),
        "y2": round(float(y2), 6),
        "Y2": round(big_y2, 6),
        "V_mol_s": round(v_mol_s, 5),
        "L_mol_s": round(l_mol_s, 4),
        "X1": round(x1, 6),
        "m": round(m_val, 3),
        "Y1_star": round(y1_star, 6),
        "dYm": round(dym, 5),
        "NA_mol_s": round(na, 6),
        "Vp_m3": round(vp, 6),
        "KYa_mol_m3_s": round(kya, 2),
        "phi_pct": round(phi_pct, 2),
    }


_DIST1522_SYSTEMS: dict[str, dict[str, Any]] = {
    "ethanol-propanol": {
        "label": "乙醇-正丙醇",
        "M1": 46.0,
        "M2": 60.0,
        "tb_coeffs": (9.1389, -27.861, 97.359),
        "cp1": {20.0: 2.38, 30.0: 2.46, 40.0: 2.55, 50.0: 2.65, 60.0: 2.76, 70.0: 2.88, 80.0: 3.01, 90.0: 3.14, 100.0: 3.29},
        "cp2": {20.0: 2.35, 30.0: 2.43, 40.0: 2.49, 50.0: 2.59, 60.0: 2.69, 70.0: 2.79, 80.0: 2.89, 90.0: 2.92, 100.0: 2.96},
        "r1": {20.0: 953.21, 30.0: 936.03, 40.0: 918.12, 50.0: 899.31, 60.0: 879.77, 70.0: 859.32, 80.0: 838.05, 90.0: 815.79, 100.0: 792.52},
        "r2": {20.0: 814.8, 30.0: 801.42, 40.0: 787.42, 50.0: 772.86, 60.0: 757.6, 70.0: 741.78, 80.0: 725.34, 90.0: 708.2, 100.0: 690.3},
        "vle_xs": [0.0, 0.126, 0.188, 0.210, 0.358, 0.461, 0.546, 0.600, 0.663, 0.884, 1.0],
        "vle_ys": [0.0, 0.240, 0.318, 0.349, 0.550, 0.650, 0.711, 0.760, 0.799, 0.914, 1.0],
    },
    "ethanol-water": {
        "label": "乙醇-水",
        "M1": 46.0,
        "M2": 18.0,
        "tb_coeffs": (9.4673, -28.012, 97.365),
        "cp1": {20.0: 2.38, 30.0: 2.46, 40.0: 2.55, 50.0: 2.65, 60.0: 2.76, 70.0: 2.88, 80.0: 3.01, 90.0: 3.14, 100.0: 3.29},
        "cp2": {20.0: 4.1889, 30.0: 4.1807, 40.0: 4.1766, 50.0: 4.1767, 60.0: 4.1803, 70.0: 4.1868, 80.0: 4.1957, 90.0: 4.2065, 100.0: 4.2192},
        "r1": {20.0: 953.21, 30.0: 936.03, 40.0: 918.12, 50.0: 899.31, 60.0: 879.77, 70.0: 859.32, 80.0: 838.05, 90.0: 815.79, 100.0: 792.52},
        "r2": {20.0: 2453.6, 30.0: 2430.1, 40.0: 2406.3, 50.0: 2382.4, 60.0: 2358.1, 70.0: 2333.6, 80.0: 2308.3, 90.0: 2282.8, 100.0: 2256.8},
    },
    "benzene-toluene": {
        "label": "苯-甲苯",
        "M1": 78.11,
        "M2": 92.14,
        "tb_coeffs": (11.6317, -42.050, 109.467),
        "cp1": {20.0: 1.6756, 30.0: 1.7126, 40.0: 1.7496, 50.0: 1.7866, 60.0: 1.8236, 70.0: 1.8606, 80.0: 1.8976, 90.0: 1.9346, 100.0: 1.9716, 110.0: 2.0086, 120.0: 2.0456},
        "cp2": {20.0: 1.688, 30.0: 1.726, 40.0: 1.764, 50.0: 1.802, 60.0: 1.84, 70.0: 1.878, 80.0: 1.916, 90.0: 1.954, 100.0: 1.992, 110.0: 2.03, 120.0: 2.068},
        "r1": {20.0: 434.288, 30.0: 427.167, 40.0: 420.046, 50.0: 412.925, 60.0: 405.804, 70.0: 398.683, 80.0: 391.562, 90.0: 384.441, 100.0: 377.32, 110.0: 370.199, 120.0: 363.078},
        "r2": {20.0: 413.096, 30.0: 407.219, 40.0: 401.342, 50.0: 395.465, 60.0: 389.588, 70.0: 383.711, 80.0: 377.834, 90.0: 371.957, 100.0: 366.08, 110.0: 360.203, 120.0: 354.326},
    },
}


def _wt_to_mol_pct(w_pct: float, m1: float, m2: float) -> float:
    """Convert light-component mass % into mole %."""
    w = float(w_pct) / 100.0
    n1 = w / float(m1)
    n2 = (1.0 - w) / float(m2)
    return (n1 / max(1e-12, n1 + n2)) * 100.0


def _mccabe_thiele_stages(
    xs_vle: list[float],
    ys_vle: list[float],
    xd: float,
    xw: float,
    r_ratio: float | None = None,
    xf: float | None = None,
    q: float | None = None,
) -> int:
    """Count theoretical tray stages (reboiler subtracted: steps - 1) using linear VLE interpolation."""
    def _x_eq(y_val: float) -> float:
        for i in range(len(ys_vle) - 1):
            if ys_vle[i] <= y_val <= ys_vle[i + 1]:
                dy = max(1e-12, ys_vle[i + 1] - ys_vle[i])
                return xs_vle[i] + (y_val - ys_vle[i]) / dy * (xs_vle[i + 1] - xs_vle[i])
        return y_val

    full_reflux = r_ratio is None or xf is None or q is None
    if not full_reflux:
        m_r = float(r_ratio) / (float(r_ratio) + 1.0)
        b_r = xd / (float(r_ratio) + 1.0)
        if abs(float(q) - 1.0) > 1e-6:
            m_q = float(q) / (float(q) - 1.0)
            b_q = -float(xf) / (float(q) - 1.0)
            x_int = (b_q - b_r) / (m_r - m_q)
        else:
            x_int = float(xf)
        y_int = m_r * x_int + b_r
        m_s = (y_int - xw) / max(1e-9, x_int - xw)
        b_s = xw - m_s * xw

    x_cur = xd
    y_cur = xd
    steps = 0
    while x_cur > xw and steps < 50:
        x_eq = _x_eq(y_cur)
        steps += 1
        if full_reflux:
            y_next = x_eq
        elif x_eq >= x_int:
            y_next = m_r * x_eq + b_r
        else:
            y_next = m_s * x_eq + b_s
        x_cur = x_eq
        y_cur = y_next
    return max(0, steps - 1)


def calc_distillation_1522(
    system: str,
    wd_full: float | None = None,
    ww_full: float | None = None,
    wd_part: float | None = None,
    ww_part: float | None = None,
    wf_part: float | None = None,
    l_lh: float | None = None,
    d_lh: float | None = None,
    t_feed: float | None = None,
    np_plates: float = 10.0,
    m1: float | None = None,
    m2: float | None = None,
) -> dict[str, Any]:
    """Compute full-reflux and partial-reflux distillation data-table quantities for OBER 1522."""
    key = system.lower().strip()
    if key not in _DIST1522_SYSTEMS:
        raise ValueError(f"Unsupported system '{system}'. Choose from: {sorted(_DIST1522_SYSTEMS)}")
    cfg = _DIST1522_SYSTEMS[key]
    eff_m1 = float(m1) if m1 is not None else float(cfg["M1"])
    eff_m2 = float(m2) if m2 is not None else float(cfg["M2"])
    out: dict[str, Any] = {
        "system": key,
        "system_label": cfg["label"],
        "M1": eff_m1,
        "M2": eff_m2,
        "NP": float(np_plates),
    }

    if wd_full is not None and ww_full is not None:
        xd_f = _wt_to_mol_pct(wd_full, eff_m1, eff_m2)
        xw_f = _wt_to_mol_pct(ww_full, eff_m1, eff_m2)
        out["xD_full_pct"] = round(xd_f, 2)
        out["xW_full_pct"] = round(xw_f, 2)
        if "vle_xs" in cfg:
            nt_full = _mccabe_thiele_stages(cfg["vle_xs"], cfg["vle_ys"], xd_f / 100.0, xw_f / 100.0)
            out["NT_full"] = nt_full
            out["ET_full_pct"] = round(nt_full / float(np_plates) * 100.0, 2)

    if None not in (wd_part, ww_part, wf_part, l_lh, d_lh, t_feed):
        xd_p = _wt_to_mol_pct(float(wd_part), eff_m1, eff_m2)
        xw_p = _wt_to_mol_pct(float(ww_part), eff_m1, eff_m2)
        xf_p = _wt_to_mol_pct(float(wf_part), eff_m1, eff_m2)
        r_ratio = float(l_lh) / max(1e-9, float(d_lh))
        x_f = xf_p / 100.0
        w_f = float(wf_part) / 100.0
        a, b, c = cfg["tb_coeffs"]
        t_bp = a * (x_f ** 2) + b * x_f + c
        t_avg = 0.5 * (t_bp + float(t_feed))
        m_mix = x_f * eff_m1 + (1.0 - x_f) * eff_m2
        cp1_v = _interp_table(t_avg, cfg["cp1"])
        cp2_v = _interp_table(t_avg, cfg["cp2"])
        r1_v = _interp_table(t_bp, cfg["r1"])
        r2_v = _interp_table(t_bp, cfg["r2"])
        cp_m = m_mix * (w_f * cp1_v + (1.0 - w_f) * cp2_v)
        r_m = m_mix * (w_f * r1_v + (1.0 - w_f) * r2_v)
        q_val = (cp_m * (t_bp - float(t_feed)) + r_m) / max(1e-9, r_m)
        out.update(
            {
                "xD_part_pct": round(xd_p, 2),
                "xW_part_pct": round(xw_p, 2),
                "xF_part_pct": round(xf_p, 2),
                "R": round(r_ratio, 2),
                "t_bp_C": round(t_bp, 2),
                "t_avg_C": round(t_avg, 2),
                "cp1_kJ_kgC": round(cp1_v, 4),
                "cp2_kJ_kgC": round(cp2_v, 4),
                "Cp_m_kJ_kmolC": round(cp_m, 2),
                "r1_kJ_kg": round(r1_v, 2),
                "r2_kJ_kg": round(r2_v, 2),
                "r_m_kJ_kmol": round(r_m, 2),
                "q": round(q_val, 2),
            }
        )
        if "vle_xs" in cfg:
            nt_part = _mccabe_thiele_stages(
                cfg["vle_xs"],
                cfg["vle_ys"],
                xd_p / 100.0,
                xw_p / 100.0,
                r_ratio=r_ratio,
                xf=x_f,
                q=q_val,
            )
            out["NT_part"] = nt_part
            out["ET_part_pct"] = round(nt_part / float(np_plates) * 100.0, 2)

    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OBER chemical simulation calculator CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fb = sub.add_parser("fluidized-bed", help="Fluidized-bed drying moisture calculation")
    p_fb.add_argument("--time-mmss", required=True, help="Timer MM:SS.cc or HH:MM:SS")
    p_fb.add_argument("--w1", type=float, required=True, help="Bottle + wet sample mass (g)")
    p_fb.add_argument("--w2", type=float, required=True, help="Bottle + dry sample mass (g)")
    p_fb.add_argument("--w0", type=float, default=32.0, help="Empty bottle mass (g)")

    p_ph = sub.add_parser("packed-hydro", help="Packed-column hydrodynamics calculation")
    p_ph.add_argument("--vn", type=float, required=True, help="Rotameter reading (Nm3/h or m3/h)")
    p_ph.add_argument("--t1", type=float, required=True, help="Inlet gas temperature (°C)")
    p_ph.add_argument("--dh-mm", type=float, default=0.0, help="U-tube manometer diff (mmH2O)")
    p_ph.add_argument("--d", type=float, default=0.10, help="Column ID (m)")
    p_ph.add_argument("--z", type=float, default=0.54, help="Packing height (m)")
    p_ph.add_argument("--rho-u", type=float, default=1000.0, help="Manometer liquid density (kg/m3)")

    p_co2 = sub.add_parser("co2-absorption", help="CO2 absorption/desorption mass transfer")
    p_co2.add_argument("--vn-air", type=float, required=True)
    p_co2.add_argument("--vn-co2", type=float, required=True)
    p_co2.add_argument("--l-lh", type=float, required=True)
    p_co2.add_argument("--t1", type=float, required=True)
    p_co2.add_argument("--t2", type=float, required=True)
    p_co2.add_argument("--t3", type=float, required=True)
    p_co2.add_argument("--t4", type=float, required=True)
    p_co2.add_argument("--dh-mm", type=float, default=0.0)
    p_co2.add_argument("--y1", type=float, default=0.0)
    p_co2.add_argument("--y2", type=float, default=0.0)
    p_co2.add_argument("--d", type=float, default=0.10)
    p_co2.add_argument("--z", type=float, default=0.54)
    p_co2.add_argument("--rho-u", type=float, default=1000.0)
    p_co2.add_argument("--rho-air20", type=float, default=1.204)
    p_co2.add_argument("--rho-co2-20", type=float, default=1.839)
    p_co2.add_argument("--m", type=float, default=None)

    p_nh3 = sub.add_parser("ammonia-absorption", help="Ammonia-air-water absorption (23 fields)")
    p_nh3.add_argument("--vn-air", type=float, required=True)
    p_nh3.add_argument("--vn-nh3", type=float, required=True)
    p_nh3.add_argument("--l-lh", type=float, default=30.0)
    p_nh3.add_argument("--dh-mm", type=float, required=True)
    p_nh3.add_argument("--t1", type=float, required=True)
    p_nh3.add_argument("--t2", type=float, required=True)
    p_nh3.add_argument("--t3", type=float, required=True)
    p_nh3.add_argument("--t4", type=float, required=True)
    p_nh3.add_argument("--v-eud-ml", type=float, required=True)
    p_nh3.add_argument("--v-sample-ml", type=float, required=True)
    p_nh3.add_argument("--v-h2so4-abs-ml", type=float, required=True)
    p_nh3.add_argument("--c-exh", type=float, default=0.006313)
    p_nh3.add_argument("--v-exh-ml", type=float, default=5.00)
    p_nh3.add_argument("--c-abs", type=float, default=0.05148)
    p_nh3.add_argument("--d", type=float, default=0.10)
    p_nh3.add_argument("--z", type=float, default=0.54)
    p_nh3.add_argument("--rho-u", type=float, default=1000.0)
    p_nh3.add_argument("--rho-air20", type=float, default=1.204)
    p_nh3.add_argument("--rho-nh3-20", type=float, default=0.707)

    p_ac = sub.add_parser("acetone-absorption", help="Acetone-air-water absorption (21 fields)")
    p_ac.add_argument("--vn2", type=float, required=True)
    p_ac.add_argument("--l2-lh", type=float, required=True)
    p_ac.add_argument("--t1", type=float, required=True)
    p_ac.add_argument("--t2", type=float, required=True)
    p_ac.add_argument("--t3", type=float, required=True)
    p_ac.add_argument("--t4", type=float, required=True)
    p_ac.add_argument("--y1", type=float, required=True)
    p_ac.add_argument("--y2", type=float, required=True)
    p_ac.add_argument("--d", type=float, default=0.050)
    p_ac.add_argument("--z", type=float, default=0.700)

    p_d15 = sub.add_parser("distillation-1522", help="OBER 1522 binary distillation data processing")
    p_d15.add_argument("--system", required=True, choices=sorted(_DIST1522_SYSTEMS))
    p_d15.add_argument("--wd-full", type=float, default=None)
    p_d15.add_argument("--ww-full", type=float, default=None)
    p_d15.add_argument("--wd-part", type=float, default=None)
    p_d15.add_argument("--ww-part", type=float, default=None)
    p_d15.add_argument("--wf-part", type=float, default=None)
    p_d15.add_argument("--l-lh", type=float, default=None)
    p_d15.add_argument("--d-lh", type=float, default=None)
    p_d15.add_argument("--t-feed", type=float, default=None)
    p_d15.add_argument("--np", dest="np_plates", type=float, default=10.0)
    p_d15.add_argument("--m1", type=float, default=None)
    p_d15.add_argument("--m2", type=float, default=None)

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    if args.cmd == "fluidized-bed":
        res = calc_fluidized_bed(args.time_mmss, args.w1, args.w2, args.w0)
    elif args.cmd == "packed-hydro":
        res = calc_packed_hydro(args.vn, args.t1, args.dh_mm, args.d, args.z, args.rho_u)
    elif args.cmd == "co2-absorption":
        res = calc_co2_absorption(
            args.vn_air,
            args.vn_co2,
            args.l_lh,
            args.t1,
            args.t2,
            args.t3,
            args.t4,
            args.dh_mm,
            args.y1,
            args.y2,
            args.d,
            args.z,
            args.rho_u,
            args.rho_air20,
            args.rho_co2_20,
            args.m,
        )
    elif args.cmd == "ammonia-absorption":
        res = calc_ammonia_absorption(
            args.vn_air,
            args.vn_nh3,
            args.l_lh,
            args.dh_mm,
            args.t1,
            args.t2,
            args.t3,
            args.t4,
            args.v_eud_ml,
            args.v_sample_ml,
            args.v_h2so4_abs_ml,
            args.c_exh,
            args.v_exh_ml,
            args.c_abs,
            args.d,
            args.z,
            args.rho_u,
            args.rho_air20,
            args.rho_nh3_20,
        )
    elif args.cmd == "acetone-absorption":
        res = calc_acetone_absorption(
            args.vn2,
            args.l2_lh,
            args.t1,
            args.t2,
            args.t3,
            args.t4,
            args.y1,
            args.y2,
            args.d,
            args.z,
        )
    elif args.cmd == "distillation-1522":
        res = calc_distillation_1522(
            args.system,
            args.wd_full,
            args.ww_full,
            args.wd_part,
            args.ww_part,
            args.wf_part,
            args.l_lh,
            args.d_lh,
            args.t_feed,
            args.np_plates,
            args.m1,
            args.m2,
        )
    else:
        raise SystemExit(f"Unsupported command: {args.cmd}")

    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
