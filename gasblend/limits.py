"""
gasblend.limits
===============
Diving gas limits and helpers (best mix, MOD, ppO2, END, EAD).  Pure
physics, no EOS needed.  Depths in metres of sea water, pressures in bar
absolute.

Ambient absolute pressure:  P_abs = depth/10 + 1   (bar)
"""
from __future__ import annotations
from .gases import Mix, fO2, fHe, fN2

FN2_AIR = 0.79          # narcotic fraction of air if only N2 is narcotic


def p_abs(depth_m: float) -> float:
    return depth_m / 10.0 + 1.0


def ppO2(mix: Mix, depth_m: float) -> float:
    """Oxygen partial pressure (bar) at depth."""
    return fO2(mix) * p_abs(depth_m)


def mod(mix: Mix, ppO2_max: float = 1.4) -> float:
    """Maximum operating depth (m) for a given ppO2 limit (typ. 1.4 bottom,
    1.6 deco)."""
    return (ppO2_max / fO2(mix) - 1.0) * 10.0


def best_mix_O2(depth_m: float, ppO2_max: float = 1.4) -> float:
    """Richest O2 fraction whose ppO2 == limit at `depth_m`."""
    return ppO2_max / p_abs(depth_m)


def best_mix_He(depth_m: float, ppO2_max: float = 1.4,
                end_max: float = 30.0, o2_narcotic: bool = True) -> float:
    """He fraction so that END == `end_max` at `depth_m`, with O2 set by
    best_mix_O2.  Returns 0 if no helium is needed."""
    fo2 = best_mix_O2(depth_m, ppO2_max)
    # narcotic fraction allowed at depth so that END == end_max
    fnarc_allowed = (p_abs(end_max) / p_abs(depth_m))   # vs air baseline 1.0
    if o2_narcotic:
        # O2 + N2 are narcotic; He displaces both
        fhe = 1.0 - fnarc_allowed
    else:
        # only N2 narcotic: keep fN2 <= fnarc_allowed*0.79... solve fHe
        fhe = 1.0 - fo2 - fnarc_allowed * FN2_AIR
    return max(0.0, min(fhe, 1.0 - fo2))


def end(mix: Mix, depth_m: float, o2_narcotic: bool = True) -> float:
    """Equivalent Narcotic Depth (m).  If o2_narcotic, both O2 and N2 are
    treated as narcotic (common technical-diving convention)."""
    fnarc = (fO2(mix) + fN2(mix)) if o2_narcotic else fN2(mix)
    fnarc_air = 1.0 if o2_narcotic else FN2_AIR
    return (fnarc / fnarc_air) * p_abs(depth_m) * 10.0 - 10.0


def ead(mix: Mix, depth_m: float) -> float:
    """Equivalent Air Depth (m) for nitrox (N2-narcosis basis)."""
    return (fN2(mix) / FN2_AIR) * p_abs(depth_m) * 10.0 - 10.0
