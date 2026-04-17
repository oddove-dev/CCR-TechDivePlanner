"""
gas_calc.py — Gas Calculator engine for JJ TechDivePlanner
===========================================================

Pure calculation functions, no Tkinter.

Key references:
  - Mitchell SJ, Doolette DJ (2009): "Selective vulnerability of the inner ear
    to decompression sickness in divers with right-to-left shunt: the role of
    bubble absorption from the cerebrospinal fluid."  J Appl Physiol.
    → O₂ is NOT narcotic; EAD uses N₂ only (fN₂ = 1 - fO₂ - fHe).

  - NOAA Diving Manual, 6th ed. (2017): CNS O₂ toxicity limits table.

  - Hamilton RW (1989): "Tolerating exposure to high oxygen: pulmonary toxicity."
    Undersea Biomed Res. → OTU calculation (UPTD method).

  - Bühlmann AA (1984): Dekompression – Dekompressionskrankheit. Springer.

  - Sofnolime 797 scrubber capacity: empirical values from Ambient Pressure
    Diving technical bulletins and independent test data.

Units:
  Depth  : metres (fresh-water 1 bar / 10 m approximation)
  Pressure: bar
  Fractions: 0–1 (never percent internally)
"""

from __future__ import annotations
import math
from typing import List, Tuple, Optional, Dict, Any


# ── Physical constants ────────────────────────────────────────────────────────
_P_SURF = 1.0          # surface pressure [bar]
_P_AMB  = lambda d: _P_SURF + d / 10.0   # ambient pressure at depth d [bar]


# ─────────────────────────────────────────────────────────────────────────────
# Limits & Warnings
# ─────────────────────────────────────────────────────────────────────────────

def calc_mod(fO2: float, ppo2_limit: float) -> float:
    """
    Maximum Operating Depth [m] for a given O₂ fraction and PO₂ limit.

    MOD = (ppo2_limit / fO2 - 1) * 10

    Parameters
    ----------
    fO2        : O₂ fraction (0–1)
    ppo2_limit : Maximum acceptable PO₂ [bar]

    Returns
    -------
    Depth in metres.  Returns +inf if fO2 <= 0.
    """
    if fO2 <= 0:
        return float("inf")
    return (ppo2_limit / fO2 - 1.0) * 10.0


def calc_ead(fO2: float, fHe: float, depth: float) -> float:
    """
    Equivalent Air Depth [m] — Mitchell/Doolette convention.

    O₂ is NOT narcotic; narcotic load is carried by N₂ only.
    fN₂ = 1 - fO₂ - fHe

    EAD = (fN₂ / 0.79 × (depth/10 + 1) − 1) × 10

    Parameters
    ----------
    fO2   : O₂ fraction (0–1)
    fHe   : He fraction (0–1)
    depth : actual depth [m]

    Returns
    -------
    EAD in metres.  Returns 0.0 if fN₂ <= 0 (no nitrogen narcosis).
    """
    fN2 = 1.0 - fO2 - fHe
    if fN2 <= 0:
        return 0.0
    p_amb = depth / 10.0 + 1.0
    return (fN2 / 0.79 * p_amb - 1.0) * 10.0


def calc_hypoxic_floor(fO2: float, pO2_min: float = 0.18) -> float:
    """
    Hypoxic floor [m]: shallowest depth at which mix is breathable.

    floor = (pO2_min / fO2 − 1) × 10

    A negative value means the mix is surface-safe (pO₂ at 1 bar >= pO2_min).

    Parameters
    ----------
    fO2     : O₂ fraction (0–1)
    pO2_min : Minimum acceptable PO₂ [bar], default 0.18

    Returns
    -------
    Floor depth in metres.  Negative = breathable at surface.
    """
    if fO2 <= 0:
        return float("inf")
    return (pO2_min / fO2 - 1.0) * 10.0


def calc_cns_rate(fO2: float, depth_m: float) -> float:
    """
    CNS O₂ toxicity rate [% per minute] at given depth.

    Uses NOAA limits table (piecewise constant approximation):
      PO₂ ≥ 1.6 bar → 45-min limit  → 100/45  %/min
      PO₂ ≥ 1.5 bar → 120-min limit
      PO₂ ≥ 1.4 bar → 150-min limit
      PO₂ ≥ 1.3 bar → 180-min limit
      PO₂ ≥ 1.2 bar → 210-min limit
      PO₂ ≥ 1.1 bar → 240-min limit
      PO₂ ≥ 1.0 bar → 300-min limit
      PO₂ <  1.0 bar → negligible (9999-min)

    Parameters
    ----------
    fO2     : O₂ fraction (0–1)
    depth_m : Depth [m]

    Returns
    -------
    CNS accumulation rate [%/min].
    """
    po2 = fO2 * _P_AMB(depth_m)
    return _cns_rate_from_po2(po2)


def _cns_rate_from_po2(po2: float) -> float:
    """CNS rate [%/min] from PO₂ [bar] — NOAA table."""
    limits = [
        (1.6, 45),
        (1.5, 120),
        (1.4, 150),
        (1.3, 180),
        (1.2, 210),
        (1.1, 240),
        (1.0, 300),
        (0.0, 9999),
    ]
    for thresh, limit_min in limits:
        if po2 >= thresh:
            return 100.0 / limit_min
    return 0.0


def calc_otu_rate(fO2: float, depth_m: float) -> float:
    """
    Pulmonary O₂ toxicity rate [OTU per minute] at given depth.

    OTU/min = ((PO₂ − 0.5) / 0.5)^0.833   for PO₂ > 0.5 bar
            = 0                              otherwise

    Reference: Hamilton (1989), UPTD method adapted to OTU.

    Parameters
    ----------
    fO2     : O₂ fraction (0–1)
    depth_m : Depth [m]

    Returns
    -------
    OTU accumulation rate [OTU/min].
    """
    po2 = fO2 * _P_AMB(depth_m)
    if po2 <= 0.5:
        return 0.0
    return ((po2 - 0.5) / 0.5) ** 0.833


def calc_cns_otu_segment(depth_m: float, time_min: float, fO2: float) -> Dict[str, float]:
    """
    CNS% and OTU accumulated over one constant-depth segment.

    Parameters
    ----------
    depth_m  : Depth [m]
    time_min : Segment duration [min]
    fO2      : O₂ fraction (0–1)

    Returns
    -------
    dict with keys: cns_pct, otu, cns_rate_per_min, otu_rate_per_min, po2
    """
    cns_rate = calc_cns_rate(fO2, depth_m)
    otu_rate = calc_otu_rate(fO2, depth_m)
    po2 = fO2 * _P_AMB(depth_m)
    return {
        "cns_pct":          cns_rate * time_min,
        "otu":              otu_rate * time_min,
        "cns_rate_per_min": cns_rate,
        "otu_rate_per_min": otu_rate,
        "po2":              po2,
    }


def calc_cns_otu_profile(segments: List[Tuple[float, float, float]]) -> Dict[str, float]:
    """
    Total CNS% and OTU for a multi-segment profile.

    Parameters
    ----------
    segments : list of (depth_m, time_min, fO2)

    Returns
    -------
    dict with keys: total_cns, total_otu, segments (list of per-segment dicts)
    """
    total_cns = 0.0
    total_otu = 0.0
    seg_results = []
    for depth_m, time_min, fO2 in segments:
        r = calc_cns_otu_segment(depth_m, time_min, fO2)
        total_cns += r["cns_pct"]
        total_otu += r["otu"]
        seg_results.append(r)
    return {
        "total_cns": total_cns,
        "total_otu": total_otu,
        "segments":  seg_results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fill Sequence
# ─────────────────────────────────────────────────────────────────────────────

def pp_fill_sequence(
    vol_L: float,
    p_start: float,
    p_target: float,
    target_o2_frac: float,
    target_he_frac: float,
    avail_top_frac_o2: float = 0.21,
    avail_top_frac_he: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Partial-pressure fill sequence using two-stage booster: He first, then O₂, then top-gas.

    Supply pressures are irrelevant — the booster delivers at any source pressure.

    Steps:
      1. He fill to required partial pressure
      2. O₂ fill to required partial pressure
      3. Top-gas (air or nitrox) to target

    Parameters
    ----------
    vol_L           : Cylinder water volume [L] (for reference)
    p_start         : Starting pressure in cylinder [bar]
    p_target        : Target fill pressure [bar]
    target_o2_frac  : Desired O₂ fraction (0–1)
    target_he_frac  : Desired He fraction (0–1)
    avail_top_frac_o2: O₂ fraction of top gas (default 0.21 = air)
    avail_top_frac_he: He fraction of top gas (default 0.0)

    Returns
    -------
    List of step dicts with keys:
      gas, p_added, p_running, fO2_running, fHe_running, fN2_running
    On error returns a list with one dict containing key 'error'.
    """
    steps: List[Dict[str, Any]] = []

    if p_target <= p_start:
        return [{"error": f"Target pressure ({p_target} bar) must exceed start pressure ({p_start} bar)."}]
    if target_o2_frac < 0 or target_he_frac < 0 or target_o2_frac + target_he_frac > 1.0:
        return [{"error": "Invalid target gas fractions (O₂ + He > 100%)."}]
    top_n2_frac = 1.0 - avail_top_frac_o2 - avail_top_frac_he
    if top_n2_frac < -0.001:
        return [{"error": "Invalid top-gas fractions (O₂ + He > 100%)."}]
    top_n2_frac = max(0.0, top_n2_frac)

    target_n2_frac = 1.0 - target_o2_frac - target_he_frac

    # ── Correct simultaneous-solve algorithm ──────────────────────────────────
    # Assumption: existing gas at p_start has the TARGET composition
    # (standard practice for topping off same mix; use p_start=0 for fresh fill).
    #
    # Gas to add (incremental, above p_start):
    #   p_he + p_o2 + p_top = delta_p                     [pressure balance]
    #   p_he + top_fhe × p_top = fhe × delta_p             [He balance]
    #   p_o2 + top_fo2 × p_top = fo2 × delta_p             [O₂ balance]
    #   top_fn2 × p_top        = fn2 × delta_p             [N₂ balance]
    #
    # Solve from N₂ balance first, then He, then O₂.

    delta_p = p_target - p_start

    if top_n2_frac < 1e-9:
        # Top gas has no N₂ — can only work if target N₂ fraction is also zero
        if target_n2_frac > 1e-6:
            return [{"error":
                "Top gas contains no N₂ but target mix requires N₂.  "
                "Use air or a nitrox mix as top gas."}]
        p_top = 0.0
    else:
        p_top = target_n2_frac * delta_p / top_n2_frac

    p_he = target_he_frac * delta_p - avail_top_frac_he * p_top
    p_o2 = target_o2_frac * delta_p - avail_top_frac_o2 * p_top

    if p_he < -0.1:
        return [{"error":
            f"Top gas contains too much He ({avail_top_frac_he*100:.0f}%) for target He "
            f"({target_he_frac*100:.0f}%).  Use a top gas with less He."}]
    if p_o2 < -0.1:
        return [{"error":
            f"Top gas contains too much O₂ ({avail_top_frac_o2*100:.0f}%) for target O₂ "
            f"({target_o2_frac*100:.0f}%).  Use a top gas with less O₂."}]

    p_he  = max(0.0, p_he)
    p_o2  = max(0.0, p_o2)
    p_top = max(0.0, p_top)

    def _fracs(he_bar: float, o2_bar: float, p_total: float):
        if p_total <= 0:
            return 0.0, 0.0, 1.0
        fhe = he_bar / p_total
        fo2 = o2_bar / p_total
        return fo2, fhe, max(0.0, 1.0 - fo2 - fhe)

    # Track cumulative gas in cylinder
    p_run   = float(p_start)
    he_in   = target_he_frac  * p_start   # existing He
    o2_in   = target_o2_frac  * p_start   # existing O₂

    # ── Step 1: He ──
    if p_he > 1e-6:
        he_in += p_he
        p_run += p_he
        fo2, fhe, fn2 = _fracs(he_in, o2_in, p_run)
        steps.append({
            "gas":         "Helium",
            "p_added":     round(p_he, 2),
            "p_running":   round(p_run, 2),
            "fO2_running": round(fo2, 4),
            "fHe_running": round(fhe, 4),
            "fN2_running": round(fn2, 4),
        })

    # ── Step 2: O₂ ──
    if p_o2 > 1e-6:
        o2_in += p_o2
        p_run += p_o2
        fo2, fhe, fn2 = _fracs(he_in, o2_in, p_run)
        steps.append({
            "gas":         "Oxygen",
            "p_added":     round(p_o2, 2),
            "p_running":   round(p_run, 2),
            "fO2_running": round(fo2, 4),
            "fHe_running": round(fhe, 4),
            "fN2_running": round(fn2, 4),
        })

    # ── Step 3: Top gas ──
    if p_top > 1e-6:
        he_in += avail_top_frac_he * p_top
        o2_in += avail_top_frac_o2 * p_top
        p_run += p_top
        fo2, fhe, fn2 = _fracs(he_in, o2_in, p_run)
        if abs(avail_top_frac_o2 - 0.21) < 0.005 and avail_top_frac_he < 0.005:
            top_label = "Air"
        elif avail_top_frac_he < 0.005:
            top_label = f"Nitrox {int(round(avail_top_frac_o2 * 100))}"
        else:
            top_label = f"Top gas ({int(round(avail_top_frac_o2*100))}/{int(round(avail_top_frac_he*100))})"
        steps.append({
            "gas":         top_label,
            "p_added":     round(p_top, 2),
            "p_running":   round(p_run, 2),
            "fO2_running": round(fo2, 4),
            "fHe_running": round(fhe, 4),
            "fN2_running": round(fn2, 4),
        })

    if not steps:
        return [{"error": "Nothing to fill — start pressure equals target pressure."}]

    return steps


def blend_optimizer(
    target_depth_m: float,
    ppo2_bottom: float,
    ppo2_limit_deco: float,
    ead_limit_m: float,
    he_step: int = 5,
    o2_step: int = 1,
) -> List[Dict[str, Any]]:
    """
    Find all gas mixes suitable for a target depth.

    Filters applied:
      - MOD ≥ target_depth (breathable at depth with ppo2_bottom limit)
      - hypoxic_floor ≤ 0 m (surface-safe, pO₂ ≥ 0.18 at surface)
      - EAD ≤ ead_limit_m (narcotic load acceptable)
      - fO₂ + fHe ≤ 0.95 (at least 5% N₂ for lung protection)
      - fO₂ ≥ 0.01, fHe ≥ 0

    Results sorted by He fraction ascending (least exotic first).

    Parameters
    ----------
    target_depth_m   : Target bottom depth [m]
    ppo2_bottom      : PO₂ at bottom [bar] — used to derive min fO₂
    ppo2_limit_deco  : PO₂ limit for deco gas (for MOD check)
    ead_limit_m      : Maximum EAD [m]
    he_step          : He% step size (default 5)
    o2_step          : O₂% step size (default 1)

    Returns
    -------
    List of dicts: {fO2, fHe, fN2, mod, ead, hypoxic_floor, label}
    """
    results = []
    for he_pct in range(0, 76, he_step):
        for o2_pct in range(1, 100, o2_step):
            fO2 = o2_pct / 100.0
            fHe = he_pct / 100.0
            fN2 = 1.0 - fO2 - fHe
            if fN2 < 0.05 or fO2 + fHe > 0.95:
                continue
            mod  = calc_mod(fO2, ppo2_limit_deco)
            if mod < target_depth_m - 0.01:
                continue
            floor = calc_hypoxic_floor(fO2)
            if floor > 0.5:   # must be breathable at surface (floor ≤ 0 with 0.5m tolerance)
                continue
            ead = calc_ead(fO2, fHe, target_depth_m)
            if ead > ead_limit_m + 0.01:
                continue
            # Check PO₂ at target depth
            po2_at_depth = fO2 * _P_AMB(target_depth_m)
            if po2_at_depth > ppo2_bottom + 0.005:
                continue

            he_lbl = f"/{int(fHe*100)}" if fHe > 0.005 else ""
            label = f"Tx {int(fO2*100)}{he_lbl}" if fHe > 0.005 else \
                    ("Air" if abs(fO2 - 0.21) < 0.005 else f"Nx {int(fO2*100)}")
            results.append({
                "fO2":           round(fO2, 4),
                "fHe":           round(fHe, 4),
                "fN2":           round(fN2, 4),
                "mod":           round(mod, 1),
                "ead":           round(ead, 1),
                "hypoxic_floor": round(floor, 1),
                "label":         label,
                "po2_at_depth":  round(po2_at_depth, 3),
            })

    results.sort(key=lambda r: (r["fHe"], r["fO2"]))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# CCR Rebreather
# ─────────────────────────────────────────────────────────────────────────────

def o2_consumption(rmv_L_min: float, fO2: float, time_min: float) -> Dict[str, float]:
    """
    O₂ consumption from RMV and gas O₂ fraction.

    O₂ consumed [L] = RMV × fO₂ × time
    O₂ consumed [g] = L × 1.429  (density at STP, 0°C / 1 atm)

    Parameters
    ----------
    rmv_L_min : Respiratory Minute Volume [L/min] at surface
    fO2       : O₂ fraction of gas breathed (0–1)
    time_min  : Time [min]

    Returns
    -------
    dict: {liters, grams}
    """
    liters = rmv_L_min * fO2 * time_min
    grams  = liters * 1.429   # O₂ density at STP
    return {"liters": liters, "grams": grams}


def diluent_consumption(
    flush_vol_L: float,
    n_flushes: int,
    depth_m: float,
    vol_loop_L: float,
    desc_rate_m_min: float = 20.0,
) -> float:
    """
    Diluent gas consumption on CCR [L at surface pressure].

    Two consumption modes:
      1. Manual/auto flushes: each flush replaces loop volume with diluent.
         Surface-equivalent = flush_vol_L * n_flushes * (depth/10 + 1)
      2. Descent loop compression: as depth increases, loop shrinks and
         diluent is injected to maintain volume.
         Volume injected = vol_loop_L * depth/10

    Parameters
    ----------
    flush_vol_L     : Volume per flush [L] (typically = loop volume, ~4–5 L)
    n_flushes       : Number of flushes
    depth_m         : Depth [m]
    vol_loop_L      : Loop volume [L]
    desc_rate_m_min : Descent rate [m/min] (unused in this formula, kept for API)

    Returns
    -------
    Total diluent consumed [L at surface pressure].
    """
    flush_consumption = flush_vol_L * n_flushes * _P_AMB(depth_m)
    descent_consumption = vol_loop_L * (depth_m / 10.0)
    return flush_consumption + descent_consumption


def crossover_depth(fO2_diluent: float, setpoint: float) -> float:
    """
    Crossover depth [m]: depth at which diluent alone delivers the setpoint PO₂.

    At shallower depths the loop O₂ fraction would exceed the setpoint,
    so the unit must vent O₂.  At deeper depths the unit injects O₂.

    crossover = (setpoint / fO2_diluent − 1) × 10

    A negative result means the diluent is always hypoxic relative to the
    setpoint at the surface (normal for hypoxic trimix diluents).

    Parameters
    ----------
    fO2_diluent : O₂ fraction of diluent (0–1)
    setpoint    : CCR PO₂ setpoint [bar]

    Returns
    -------
    Crossover depth [m].  Negative = setpoint never reached from diluent alone.
    """
    if fO2_diluent <= 0:
        return float("inf")
    return (setpoint / fO2_diluent - 1.0) * 10.0


def loop_gas_fractions(
    segments: List[Tuple[float, float]],
    fO2_dil: float,
    fHe_dil: float,
    setpoint: float,
) -> List[Dict[str, Any]]:
    """
    Loop gas fractions and narcotic load (EAD) at each segment depth on CCR.

    On CCR, PO₂ is controlled to `setpoint`.  The inert gas comes from the
    diluent and fills the remaining partial pressure:
      p_amb = depth/10 + 1
      pO₂_loop = min(setpoint, p_amb)   [clamped at shallow depths]
      p_inert   = p_amb − pO₂_loop
      pHe = fHe_dil / (fHe_dil + fN2_dil) × p_inert
      pN₂ = fN2_dil / (fHe_dil + fN2_dil) × p_inert

    EAD is calculated from fN₂_loop using Mitchell/Doolette convention.

    Parameters
    ----------
    segments : list of (depth_m, time_min)
    fO2_dil  : O₂ fraction of diluent (0–1)
    fHe_dil  : He fraction of diluent (0–1)
    setpoint : CCR PO₂ setpoint [bar]

    Returns
    -------
    List of dicts per segment:
      {depth, time, p_amb, pO2, pN2, pHe, fO2_loop, fN2_loop, fHe_loop, ead}
    """
    fN2_dil = 1.0 - fO2_dil - fHe_dil
    f_inert = fHe_dil + fN2_dil
    results = []
    for depth_m, time_min in segments:
        p_amb = _P_AMB(depth_m)
        pO2 = min(setpoint, p_amb)
        p_inert = max(0.0, p_amb - pO2)
        if f_inert > 1e-9:
            pHe = (fHe_dil / f_inert) * p_inert
            pN2 = (fN2_dil / f_inert) * p_inert
        else:
            pHe = pN2 = 0.0
        fO2_loop = pO2 / p_amb if p_amb > 0 else 0.0
        fHe_loop = pHe / p_amb if p_amb > 0 else 0.0
        fN2_loop = pN2 / p_amb if p_amb > 0 else 0.0
        # EAD from loop N₂ fraction
        ead = calc_ead(fO2_loop, fHe_loop, depth_m)
        results.append({
            "depth":    depth_m,
            "time":     time_min,
            "p_amb":    round(p_amb, 3),
            "pO2":      round(pO2, 3),
            "pN2":      round(pN2, 3),
            "pHe":      round(pHe, 3),
            "fO2_loop": round(fO2_loop, 4),
            "fN2_loop": round(fN2_loop, 4),
            "fHe_loop": round(fHe_loop, 4),
            "ead":      round(ead, 1),
        })
    return results


def scrubber_remaining(
    scrubber_type: str,
    mass_kg: float,
    temp_C: float,
    depth_m: float,
    elapsed_min: float,
) -> Dict[str, Any]:
    """
    Estimate scrubber CO₂ absorption capacity remaining.

    WARNING: These values are EMPIRICAL APPROXIMATIONS based on published
    test data for Sofnolime 797.  Actual capacity varies with:
      - Breathing pattern and tidal volume
      - Moisture content of the absorbent
      - Channelling and packing uniformity
      - Specific rebreather geometry

    Do NOT rely on these calculations for dive planning without cross-
    referencing manufacturer specifications and independent test data.
    Always apply a conservative safety margin (≥ 20% buffer).

    Base capacity (Sofnolime 797, 20°C, 1 bar): ≈ 100 min·L/kg
    Temperature correction: × (1 + 0.02 × (T − 20))
    Depth/pressure factor:  × (1 + 0.1 × depth/10)

    Parameters
    ----------
    scrubber_type : String label (currently only "Sofnolime 797" supported)
    mass_kg       : Mass of absorbent [kg]
    temp_C        : Water temperature [°C]
    depth_m       : Operating depth [m] (representative)
    elapsed_min   : Elapsed dive time [min]

    Returns
    -------
    dict: {capacity_min, elapsed_min, remaining_min, pct_used, warning_level}
    warning_level: "ok" (< 70%), "caution" (70–85%), "warning" (> 85%)
    """
    # Base capacity [min] per kg
    base_capacity_per_kg = 100.0   # conservative; data range 80-130

    temp_factor  = 1.0 + 0.02 * (temp_C - 20.0)
    temp_factor  = max(0.4, min(2.0, temp_factor))   # clamp
    depth_factor = 1.0 + 0.1 * (depth_m / 10.0)

    capacity_total_min = mass_kg * base_capacity_per_kg * temp_factor * depth_factor
    remaining_min = max(0.0, capacity_total_min - elapsed_min)
    pct_used = min(100.0, (elapsed_min / capacity_total_min * 100.0) if capacity_total_min > 0 else 100.0)

    if pct_used < 70.0:
        warning_level = "ok"
    elif pct_used < 85.0:
        warning_level = "caution"
    else:
        warning_level = "warning"

    return {
        "scrubber_type":    scrubber_type,
        "capacity_min":     round(capacity_total_min, 1),
        "elapsed_min":      elapsed_min,
        "remaining_min":    round(remaining_min, 1),
        "pct_used":         round(pct_used, 1),
        "warning_level":    warning_level,
        "temp_factor":      round(temp_factor, 3),
        "depth_factor":     round(depth_factor, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ICD Analysis
# ─────────────────────────────────────────────────────────────────────────────

def delta_pn2(
    fO2_from: float, fHe_from: float,
    fO2_to: float,   fHe_to: float,
    depth_m: float,
) -> float:
    """
    Isobaric Counter-Diffusion (ICD): change in N₂ partial pressure when
    switching from one gas to another at constant depth.

    ΔPN₂ = (fN₂_to − fN₂_from) × (depth/10 + 1)

    A positive ΔPN₂ indicates N₂ loading on switch (ICD risk).
    A negative value indicates N₂ washout (safe).

    Parameters
    ----------
    fO2_from, fHe_from : From-gas fractions (0–1)
    fO2_to,   fHe_to   : To-gas fractions (0–1)
    depth_m            : Depth [m]

    Returns
    -------
    ΔPN₂ [bar]
    """
    fN2_from = 1.0 - fO2_from - fHe_from
    fN2_to   = 1.0 - fO2_to   - fHe_to
    return (fN2_to - fN2_from) * _P_AMB(depth_m)


def safe_switch_depth(
    fO2_from: float, fHe_from: float,
    fO2_to:   float, fHe_to:   float,
    limit: float = 0.5,
) -> Optional[float]:
    """
    Depth at which ΔPN₂ equals the ICD limit.

    safe_depth = (limit / (fN₂_to − fN₂_from) − 1) × 10

    If fN₂_to ≤ fN₂_from, the switch always reduces N₂ loading (safe at
    any depth) and None is returned.

    Parameters
    ----------
    fO2_from, fHe_from : From-gas fractions (0–1)
    fO2_to,   fHe_to   : To-gas fractions (0–1)
    limit              : ΔPN₂ limit [bar] (IANTD/GUE convention: 0.5 bar)

    Returns
    -------
    Safe switch depth [m], or None if switch is always safe.
    """
    fN2_from = 1.0 - fO2_from - fHe_from
    fN2_to   = 1.0 - fO2_to   - fHe_to
    d_fN2 = fN2_to - fN2_from
    if d_fN2 <= 1e-9:
        return None   # switch is safe at any depth
    return (limit / d_fN2 - 1.0) * 10.0


def icd_curve(
    fO2_from: float, fHe_from: float,
    fO2_to:   float, fHe_to:   float,
    max_depth: float = 100.0,
    step: float = 1.0,
) -> List[Tuple[float, float]]:
    """
    ΔPN₂ vs depth curve for ICD analysis.

    Parameters
    ----------
    fO2_from, fHe_from : From-gas fractions (0–1)
    fO2_to,   fHe_to   : To-gas fractions (0–1)
    max_depth          : Maximum depth to evaluate [m]
    step               : Depth step [m]

    Returns
    -------
    List of (depth_m, delta_pn2) tuples.
    """
    fN2_from = 1.0 - fO2_from - fHe_from
    fN2_to   = 1.0 - fO2_to   - fHe_to
    d_fN2    = fN2_to - fN2_from
    curve = []
    d = 0.0
    while d <= max_depth + 1e-9:
        dpn2 = d_fN2 * _P_AMB(d)
        curve.append((round(d, 1), round(dpn2, 4)))
        d += step
    return curve


# ─────────────────────────────────────────────────────────────────────────────
# Comparison & Tables
# ─────────────────────────────────────────────────────────────────────────────

def compare_mixes(
    mixes: List[Tuple[float, float, str]],
    eval_depth_m: float,
    ppo2_limit: float = 1.4,
) -> List[Dict[str, Any]]:
    """
    Compare multiple gas mixes at an evaluation depth.

    Parameters
    ----------
    mixes        : list of (fO2, fHe, label)
    eval_depth_m : Depth at which to evaluate PO₂, EAD, CNS/OTU rates
    ppo2_limit   : PO₂ limit for MOD calculation [bar]

    Returns
    -------
    List of dicts per mix:
      {label, fO2, fHe, fN2, mod, ead, hypoxic_floor,
       po2_at_depth, cns_rate_at_depth, otu_rate_at_depth}
    """
    results = []
    for fO2, fHe, label in mixes:
        fN2 = 1.0 - fO2 - fHe
        mod   = calc_mod(fO2, ppo2_limit)
        ead   = calc_ead(fO2, fHe, eval_depth_m)
        floor = calc_hypoxic_floor(fO2)
        po2   = fO2 * _P_AMB(eval_depth_m)
        cns_r = _cns_rate_from_po2(po2)
        otu_r = calc_otu_rate(fO2, eval_depth_m)
        results.append({
            "label":              label,
            "fO2":                round(fO2, 4),
            "fHe":                round(fHe, 4),
            "fN2":                round(fN2, 4),
            "mod":                round(mod, 1),
            "ead":                round(ead, 1),
            "hypoxic_floor":      round(floor, 1),
            "po2_at_depth":       round(po2, 3),
            "cns_rate_at_depth":  round(cns_r, 4),
            "otu_rate_at_depth":  round(otu_r, 4),
        })
    return results


def generate_trimix_table(
    target_depth_m: float,
    ppo2_bottom: float = 1.3,
    ppo2_limit:  float = 1.4,
    ead_limit_m: float = 30.0,
    he_step: int = 5,
    o2_step: int = 1,
) -> List[Dict[str, Any]]:
    """
    Generate a table of all valid trimix/nitrox blends for a target depth.

    Filters applied:
      - fO₂ + fHe ≤ 0.95
      - MOD ≥ target_depth
      - hypoxic_floor ≤ 6 m (suitable for CCR — surface O₂ may be low)
      - EAD ≤ ead_limit_m
      - PO₂ at depth ≤ ppo2_bottom

    Parameters
    ----------
    target_depth_m : Target dive depth [m]
    ppo2_bottom    : Acceptable PO₂ at target depth [bar]
    ppo2_limit     : Absolute PO₂ limit for MOD [bar]
    ead_limit_m    : Maximum EAD [m]
    he_step        : He% iteration step
    o2_step        : O₂% iteration step

    Returns
    -------
    List of dicts, sorted by He% ascending then O₂% ascending.
    """
    results = []
    for he_pct in range(0, 76, he_step):
        for o2_pct in range(1, 100, o2_step):
            fO2 = o2_pct / 100.0
            fHe = he_pct / 100.0
            fN2 = 1.0 - fO2 - fHe
            if fN2 < 0.05 or fO2 + fHe > 0.95:
                continue
            mod   = calc_mod(fO2, ppo2_limit)
            if mod < target_depth_m - 0.01:
                continue
            floor = calc_hypoxic_floor(fO2)
            if floor > 6.0:
                continue
            ead = calc_ead(fO2, fHe, target_depth_m)
            if ead > ead_limit_m + 0.01:
                continue
            po2_at_depth = fO2 * _P_AMB(target_depth_m)
            if po2_at_depth > ppo2_bottom + 0.005:
                continue
            cns_r = _cns_rate_from_po2(po2_at_depth)
            otu_r = (((po2_at_depth - 0.5) / 0.5) ** 0.833) if po2_at_depth > 0.5 else 0.0

            he_lbl = f"/{he_pct}" if he_pct > 0 else ""
            if he_pct > 0:
                label = f"Tx {o2_pct}{he_lbl}"
            elif o2_pct == 21:
                label = "Air"
            else:
                label = f"Nx {o2_pct}"

            results.append({
                "label":         label,
                "o2_pct":        o2_pct,
                "he_pct":        he_pct,
                "n2_pct":        round(fN2 * 100, 1),
                "fO2":           round(fO2, 4),
                "fHe":           round(fHe, 4),
                "fN2":           round(fN2, 4),
                "mod":           round(mod, 1),
                "ead":           round(ead, 1),
                "hypoxic_floor": round(floor, 1),
                "po2_at_depth":  round(po2_at_depth, 3),
                "cns_rate":      round(cns_r, 4),
                "otu_rate":      round(otu_r, 4),
            })

    results.sort(key=lambda r: (r["he_pct"], r["o2_pct"]))
    return results
