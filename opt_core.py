"""Pure-compute core for the Optimal-bailout optimiser.

Deliberately free of PyQt / matplotlib imports so it can be imported cheaply by
multiprocessing worker processes (spawn start method on Windows re-imports the
module in every child).  All heavy lifting comes from buhlmann only.

dive_planner_tab keeps thin wrappers (`_OptWorker.simulate_combo`,
`_OptWorker.build_result`, `_SimWorker._ccr_bottom_rt`) that delegate here, so
existing call sites are unchanged.
"""
from buhlmann import (
    OCGas, simulate_bailout_from_bottom, mod_switch_depth,
    _gas_litres_per_gas, _gas_litres_per_idx,
)


def ccr_bottom_rt(p: dict) -> float:
    """Runtime at the end of the deepest segment (needed for bailout RT offset)."""
    segments = p["segments"]
    desc_r   = p["desc_r"]
    asc_r    = p["asc_r"]
    _max_d   = max((d for d, _ in segments), default=0.0)
    cur_d, cur_rt, ccr_rt = 0.0, 0.0, 0.0
    for seg_depth, total_seg_time in segments:
        if seg_depth != cur_d:
            rate     = desc_r if seg_depth > cur_d else asc_r
            t_travel = abs(seg_depth - cur_d) / rate
            cur_rt  += t_travel
            cur_d    = seg_depth
        else:
            t_travel = 0.0
        t_at    = max(0.0, total_seg_time - t_travel)
        cur_rt += t_at
        if seg_depth >= _max_d:
            ccr_rt = cur_rt
    return ccr_rt


def simulate_combo(combo, oc_gases, p, po2_max=None, bottom_d=None):
    """Run simulate_bailout_from_bottom for a single gas combination.

    Returns (DiveResult, new_gases).  When po2_max (per-position max PO2) is
    given, each gas's switch depth is the MOD at its O2 and that max PO2
    (bailout = bottom).  Otherwise the gases' existing switch depths are kept.
    """
    _be  = p.get("bail_extra", 0.0)
    segs = list(p["segments"])
    if _be > 0.0 and segs:
        _max_d = max((d for d, _ in segs), default=0.0)
        for bi in range(len(segs) - 1, -1, -1):
            if segs[bi][0] >= _max_d:
                d_, t_ = segs[bi]
                segs[bi] = (d_, max(0.0, t_ - _be))
                break

    if bottom_d is None:
        bottom_d = max((d for d, _ in p["segments"]), default=0.0)

    new_gases = []
    for i, gas in enumerate(oc_gases):
        o2f, hef = combo[i] if i in combo else (gas.o2, gas.he)
        if po2_max is not None and i < len(po2_max):
            sw = (bottom_d if i == 0
                  else mod_switch_depth(o2f, po2_max[i], bottom_d))
        else:
            sw = gas.switch_depth
        new_gases.append(OCGas(o2=o2f, he=hef, switch_depth=sw))

    result = simulate_bailout_from_bottom(
        segments      = segs,
        ccr           = p.get("ccr"),
        oc_gases      = new_gases,
        gf_low        = p.get("bo_gf_lo", 0.55),
        gf_high       = p.get("bo_gf_hi", 0.70),
        desc_rate     = p.get("desc_r",   20.0),
        asc_rate      = p.get("asc_r",     9.0),
        deco_rate     = p.get("deco_r",    3.0),
        snap_interval = 9999.0,   # no tissue snapshots needed
        stop_interval = p.get("stop_iv", 3.0),
        bail_extra    = _be,
        last_stop     = p.get("bo_last_stop", 3.0),
    )
    return result, new_gases


def build_result(combo, oc_gases, p, sac, po2_max=None, bottom_d=None):
    """Simulate one combination and assemble its summary result dict."""
    _be = p.get("bail_extra", 0.0)
    if bottom_d is None:
        bottom_d = max((d for d, _ in p["segments"]), default=0.0)

    result, new_gases = simulate_combo(combo, oc_gases, p, po2_max, bottom_d)
    litres = _gas_litres_per_gas(
        result    = result,    oc_gases  = new_gases, sac      = sac,
        bail_extra = _be,      bottom_d  = bottom_d,
        asc_rate  = p.get("asc_r", 9.0), deco_rate = p.get("deco_r", 3.0),
    )
    bail_label = new_gases[0].label()
    bailout_L  = litres.get(bail_label, 0.0)
    deco_L     = sum(L for lbl, L in litres.items() if lbl != bail_label)
    litres_per_idx = _gas_litres_per_idx(
        result    = result,    oc_gases  = new_gases, sac      = sac,
        bail_extra = _be,      bottom_d  = bottom_d,
        asc_rate  = p.get("asc_r", 9.0), deco_rate = p.get("deco_r", 3.0),
    )
    rt_offset = ccr_bottom_rt(p) - sum(t for _, t in p["segments"])
    return {
        "combination":    combo,
        "tts":            result.tts,
        "deco_time":      sum(s.time for s in result.stops),
        "surface":        result.runtime + rt_offset,
        "gas_litres":     litres,
        "litres_per_idx": litres_per_idx,
        "bailout_L":      bailout_L,
        "deco_L":         deco_L,
        "total_L":        bailout_L + deco_L,
    }


def exceeds_volume(result_dict, available_L) -> bool:
    """True if any cylinder's used gas exceeds its available volume."""
    if not available_L:
        return False
    lpi = result_dict.get("litres_per_idx", [])
    for i, a in enumerate(available_L):
        if a is not None and i < len(lpi) and lpi[i] > a:
            return True
    return False


# ── multiprocessing pool helpers ─────────────────────────────────────────────
# The constant arguments (gas list, sim params, …) are shipped to each worker
# process ONCE via the pool initializer, then each task only ships one combo.
_CTX: dict = {}


def pool_init(oc_gases, p, sac, po2_max, bottom_d):
    _CTX["args"] = (oc_gases, p, sac, po2_max, bottom_d)


def pool_eval(combo):
    """Evaluate one combo in a worker process.  Returns the result dict, or
    None on any simulation error (mirrors the serial loop's try/except)."""
    oc_gases, p, sac, po2_max, bottom_d = _CTX["args"]
    try:
        return build_result(combo, oc_gases, p, sac, po2_max, bottom_d)
    except Exception:
        return None
