"""
Bühlmann ZHL-16C decompression algorithm.

Supports:
  - CCR (Closed Circuit Rebreather) with fixed PO2 setpoint + diluent
  - OC (Open Circuit) bailout gases with user-defined switch depths
  - Gradient Factors (GF Low / GF High)
  - Configurable deco stop interval (default 3 m)

Tissue model : Bühlmann ZHL-16C, 16 compartments (N2 + He)
Pressure     : seawater density 1.025 kg/L → 1 bar per 9.975 m
               (fresh water would be 1.000 kg/L → 1 bar per 10.0 m;
                difference is ~2.5% — at 40 m: 5.025 bar seawater vs 5.0 bar fresh)
Ascent rates : configurable (default 9 m/min open, 3 m/min in deco)
Descent rate : configurable (default 20 m/min)
"""

import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

# ── ZHL-16C tissue parameters ─────────────────────────────────────────────────
# (N2_ht_min, N2_a_bar, N2_b, He_ht_min, He_a_bar, He_b)
TISSUES: List[Tuple] = [
    ( 4.0,  1.1696, 0.5050,   1.51, 1.7424, 0.4245),
    ( 8.0,  1.0000, 0.6514,   3.02, 1.3830, 0.5747),
    (12.5,  0.8618, 0.7222,   4.72, 1.1919, 0.6527),
    (18.5,  0.7562, 0.7825,   6.99, 1.0458, 0.7223),
    (27.0,  0.6200, 0.8126,  10.21, 0.9220, 0.7582),
    (38.3,  0.5933, 0.8434,  14.48, 0.8205, 0.7957),
    (54.3,  0.5282, 0.8693,  20.53, 0.7305, 0.8279),
    (77.0,  0.4701, 0.8910,  29.11, 0.6502, 0.8553),
    (109.0, 0.4187, 0.9092,  41.20, 0.5950, 0.8757),
    (146.0, 0.3798, 0.9222,  55.19, 0.5545, 0.8903),
    (187.0, 0.3497, 0.9319,  70.69, 0.5333, 0.8997),
    (239.0, 0.3223, 0.9403,  90.34, 0.5189, 0.9073),
    (305.0, 0.2971, 0.9477, 115.29, 0.5181, 0.9122),
    (390.0, 0.2737, 0.9544, 147.42, 0.5176, 0.9171),
    (498.0, 0.2523, 0.9602, 188.24, 0.5172, 0.9217),
    (635.0, 0.2327, 0.9653, 240.03, 0.5119, 0.9267),
]

PH2O          = 0.0627   # alveolar water vapour [bar] at 37 °C
P_SURF        = 1.0      # surface pressure [bar]
WATER_DENSITY = 1.025    # seawater [kg/L] — use 1.000 for fresh water
STOP_INT = 3.0      # deco stop interval [m]

# Surface N2 loading (air, 79 % N2)
_PN2_SURF = (P_SURF - PH2O) * 0.7902


# ── Gas data classes ───────────────────────────────────────────────────────────
@dataclass
class CCRConfig:
    """Rebreather configuration."""
    setpoint:    float   # PO2 setpoint at bottom [bar]
    diluent_o2:  float   # O2 fraction in diluent (0-1)
    diluent_he:  float   # He fraction in diluent (0-1)
    sp_descend:  float = 0.0   # PO2 during descent (0 = same as setpoint)
    sp_deco:     float = 0.0   # PO2 during deco stops (0 = same as setpoint)

    def __post_init__(self):
        if self.sp_descend <= 0:
            self.sp_descend = self.setpoint
        if self.sp_deco <= 0:
            self.sp_deco = self.setpoint

    @property
    def diluent_n2(self) -> float:
        return 1.0 - self.diluent_o2 - self.diluent_he

    def label(self, sp: float = 0.0) -> str:
        sp_used = sp if sp > 0 else self.setpoint
        return (f"SP {sp_used:.2f} "
                f"dil {int(self.diluent_o2*100)}/{int(self.diluent_he*100)}")

    def dil_label(self) -> str:
        return f"{int(self.diluent_o2*100)}/{int(self.diluent_he*100)}"

    def sp_label(self, sp: float = 0.0) -> str:
        sp_used = sp if sp > 0 else self.setpoint
        return f"SP {sp_used:.2f}"


@dataclass
class OCGas:
    """Open-circuit bailout gas with a switch depth."""
    o2:           float   # O2 fraction (0-1)
    he:           float   # He fraction (0-1)
    switch_depth: float   # depth [m] at which to switch to this gas on ascent

    @property
    def n2(self) -> float:
        return 1.0 - self.o2 - self.he

    def label(self) -> str:
        o2p = int(round(self.o2 * 100))
        hep = int(round(self.he * 100))
        if hep == 0:
            return "Air" if o2p == 21 else f"Nitrox {o2p}"
        return f"Trimix {o2p}/{hep}"


@dataclass
class Stop:
    """One decompression stop."""
    depth:    float   # [m]
    time:     float   # [min]
    gas:      str     # label
    runtime:  float   # elapsed runtime at END of stop [min]
    tissue_snapshot: list = field(default_factory=list)  # [(p_total, m_val)] per tissue


@dataclass
class DiveResult:
    stops:        List[Stop]
    bottom_time:  float   # time from start to first ascent [min]
    tts:          float   # time from start of ascent to surface [min]
    runtime:      float   # total runtime [min]
    otu:          float   # oxygen toxicity units
    cns:          float   # CNS % (approximate)
    tissue_at_depth:   list  = field(default_factory=list)  # snapshot at end of bottom
    tissue_at_surface: list  = field(default_factory=list)  # snapshot after deco
    tissue_timeline:   list  = field(default_factory=list)  # [(runtime, depth, snapshot)] every N min
    tissue_phase_list: list  = field(default_factory=list)  # [(runtime, depth, label, snapshot)] per dive-plan phase
    first_stop_depth:  float = 0.0   # simulation's actual first deco stop depth [m] (rounded ceiling at GF_low)


# ── Schreiner equation ─────────────────────────────────────────────────────────
def schreiner(p0: float, pi_start: float, pi_end: float,
              t: float, ht: float) -> float:
    """
    Tissue loading with linear change in inspired PP.

    p0       : initial tissue partial pressure [bar]
    pi_start : inspired PP at start of segment [bar]
    pi_end   : inspired PP at end of segment [bar]
    t        : segment duration [min]
    ht       : tissue half-time [min]
    """
    if t <= 0:
        return p0
    k = math.log(2) / ht
    R = (pi_end - pi_start) / t          # rate [bar/min]
    return pi_start + R * (t - 1.0 / k) - (pi_start - R / k - p0) * math.exp(-k * t)


# ── Inspired gas partial pressures ────────────────────────────────────────────
def _p_amb(depth: float) -> float:
    return P_SURF + depth * WATER_DENSITY / 10.0


def ccr_inspired(depth: float, cfg: CCRConfig, sp: float = 0.0) -> Tuple[float, float]:
    """
    Alveolar PN2 and PHe for CCR at given depth.
    PO2 is clamped to what is achievable at shallow depths.
    sp: explicit setpoint override (0 = use cfg.setpoint)
    """
    p_amb = _p_amb(depth)
    f_inert = cfg.diluent_n2 + cfg.diluent_he        # inert fraction of diluent
    sp_use  = sp if sp > 0 else cfg.setpoint
    po2_eff = min(sp_use, p_amb - PH2O)
    po2_eff = max(0.0, po2_eff)
    p_inert = max(0.0, p_amb - po2_eff - PH2O)
    if f_inert > 0:
        pn2 = p_inert * (cfg.diluent_n2 / f_inert)
        phe = p_inert * (cfg.diluent_he / f_inert)
    else:
        pn2 = phe = 0.0
    return pn2, phe


def oc_inspired(depth: float, gas: OCGas) -> Tuple[float, float]:
    """Alveolar PN2 and PHe for an open-circuit gas at given depth."""
    p_alv = max(0.0, _p_amb(depth) - PH2O)
    return p_alv * gas.n2, p_alv * gas.he


# ── Tissue state ──────────────────────────────────────────────────────────────
class TissueState:
    """Bühlmann ZHL-16C tissue loading."""

    def __init__(self):
        self.p_n2 = [_PN2_SURF] * 16
        self.p_he = [0.0]       * 16

    def copy(self) -> "TissueState":
        ts = TissueState.__new__(TissueState)
        ts.p_n2 = list(self.p_n2)
        ts.p_he = list(self.p_he)
        return ts

    def load(self, d_start: float, d_end: float, t_min: float,
             pn2_s: float, pn2_e: float, phe_s: float, phe_e: float):
        """Load all 16 tissues over a segment."""
        for i, (N2_ht, _, _, He_ht, _, _) in enumerate(TISSUES):
            self.p_n2[i] = schreiner(self.p_n2[i], pn2_s, pn2_e, t_min, N2_ht)
            self.p_he[i] = schreiner(self.p_he[i], phe_s, phe_e, t_min, He_ht)

    def ceiling_pressure(self, gf: float) -> float:
        """Minimum ambient pressure the tissues can tolerate [bar]."""
        p_ceil = 0.0
        for i, (_, N2_a, N2_b, _, He_a, He_b) in enumerate(TISSUES):
            pt = self.p_n2[i] + self.p_he[i]
            if pt <= 0:
                continue
            a = (N2_a * self.p_n2[i] + He_a * self.p_he[i]) / pt
            b = (N2_b * self.p_n2[i] + He_b * self.p_he[i]) / pt
            denom = gf / b + 1.0 - gf
            if denom == 0:
                continue
            p_c = (pt - a * gf) / denom
            p_ceil = max(p_ceil, p_c)
        return max(P_SURF, p_ceil)

    def ceiling_depth(self, gf: float) -> float:
        """Ceiling depth [m] (0 = surface is safe)."""
        return max(0.0, (self.ceiling_pressure(gf) - P_SURF) * 10.0 / WATER_DENSITY)

    def snapshot(self) -> list:
        """Return [(p_n2, p_he)] for each compartment (raw tissue loadings).
        Use snap_mv(snap, depth) in the GUI to compute (p_total, m_value) at any depth."""
        return list(zip(self.p_n2, self.p_he))


# ── Gas selection ─────────────────────────────────────────────────────────────
def select_oc_gas(depth: float, oc_gases: List[OCGas]) -> OCGas:
    """
    Choose the OC gas to breathe at `depth` on ascent.
    Uses the gas with the smallest switch_depth that is >= depth.
    Falls back to the deepest gas if none qualifies.
    """
    qualifying = [g for g in oc_gases if g.switch_depth >= depth]
    if qualifying:
        return min(qualifying, key=lambda g: g.switch_depth)
    # Fallback: deepest gas
    return max(oc_gases, key=lambda g: g.switch_depth)


def _oc_ascent_waypoints(d_from: float, d_to: float,
                          oc_gases: List[OCGas]) -> List[float]:
    """Return sorted list of depths at which the OC gas changes during ascent
    from d_from down to d_to (d_from > d_to).  Always includes d_to."""
    waypoints = {d_to}
    for g in oc_gases:
        sw = g.switch_depth
        if d_to <= sw < d_from:
            waypoints.add(sw)
    return sorted(waypoints, reverse=True)   # descending (shallowest last)


def _inspired(depth: float, mode: str, ccr: Optional[CCRConfig],
              oc_gases: List[OCGas], sp: float = 0.0) -> Tuple[float, float, str]:
    """Return (pn2, phe, gas_label) for current depth and mode.
    sp: explicit CCR setpoint override (0 = use cfg.setpoint)"""
    if mode == "ccr":
        pn2, phe = ccr_inspired(depth, ccr, sp=sp)
        return pn2, phe, ccr.label(sp=sp)
    gas = select_oc_gas(depth, oc_gases)
    pn2, phe = oc_inspired(depth, gas)
    return pn2, phe, gas.label()


# ── OTU / CNS helpers ─────────────────────────────────────────────────────────
def _otu_rate(po2: float) -> float:
    """OTU per minute at given PO2 [bar]."""
    if po2 <= 0.5:
        return 0.0
    return ((po2 - 0.5) / 0.5) ** 0.833


def _cns_rate(po2: float) -> float:
    """Approximate CNS % per minute."""
    # NOAA limits table approximation
    limits = [(1.6, 45), (1.5, 120), (1.4, 150), (1.3, 180),
              (1.2, 210), (1.1, 240), (1.0, 300), (0.0, 9999)]
    for thresh, limit_min in limits:
        if po2 >= thresh:
            return 100.0 / limit_min
    return 0.0


# ── Main simulation ───────────────────────────────────────────────────────────
def simulate_dive(
    segments:      List[Tuple[float, float]],  # [(depth_m, time_min)]
    mode:          str,                         # "ccr" or "oc"
    ccr:           Optional[CCRConfig],
    oc_gases:      List[OCGas],
    gf_low:        float,                       # 0-1
    gf_high:       float,                       # 0-1
    desc_rate:     float = 20.0,               # m/min
    asc_rate:      float = 9.0,                # m/min (transit to first stop)
    deco_rate:     float = 3.0,                # m/min (between stops)
    snap_interval: float = 1.0,               # min between timeline snapshots
    stop_interval: float = 3.0,               # deco stop interval [m]
    last_stop:     float = 0.0,               # shallowest mandatory CCR deco stop [m]
) -> DiveResult:
    """
    Simulate a dive and return the decompression plan.

    segments : planned waypoints [(depth, time_at_depth)].
               The simulator adds descent/ascent between waypoints automatically.
    mode     : "ccr" uses CCR config; "oc" uses OC gases throughout.
    """
    if not oc_gases:
        oc_gases = [OCGas(o2=0.21, he=0.0, switch_depth=999)]

    state   = TissueState()
    stops:  List[Stop] = []
    runtime = 0.0
    otu     = 0.0
    cns     = 0.0
    timeline: list = []         # (runtime, depth, snapshot)
    _next_snap = [snap_interval]  # mutable; next runtime to snapshot

    # Phase setpoints for CCR
    sp_desc   = ccr.sp_descend if ccr else 0.0
    sp_bottom = ccr.setpoint   if ccr else 0.0
    sp_deco   = ccr.sp_deco    if ccr else 0.0
    _phase_sp = [sp_desc]   # mutable list so closure can update it

    def _load_seg(d_from, d_to, t_min):
        nonlocal runtime, otu, cns
        if t_min <= 0:
            return
        t_left = t_min
        t_done = 0.0
        while t_left > 1e-9:
            # chunk up to next snapshot boundary
            to_snap = _next_snap[0] - runtime
            chunk   = min(t_left, to_snap) if to_snap > 1e-9 else min(t_left, snap_interval)
            chunk   = max(chunk, 1e-9)
            frac_s  = t_done / t_min
            frac_e  = (t_done + chunk) / t_min
            d_s = d_from + (d_to - d_from) * frac_s
            d_e = d_from + (d_to - d_from) * frac_e
            sp  = _phase_sp[0]
            pn2_s, phe_s, _ = _inspired(d_s, mode, ccr, oc_gases, sp=sp)
            pn2_e, phe_e, _ = _inspired(d_e, mode, ccr, oc_gases, sp=sp)
            state.load(d_s, d_e, chunk, pn2_s, pn2_e, phe_s, phe_e)
            if mode == "ccr" and ccr:
                po2 = sp if sp > 0 else ccr.setpoint
            else:
                gas = select_oc_gas((d_s + d_e) / 2, oc_gases)
                po2 = _p_amb((d_s + d_e) / 2) * gas.o2
            otu += _otu_rate(po2) * chunk
            cns += _cns_rate(po2) * chunk
            runtime += chunk
            t_left  -= chunk
            t_done  += chunk
            if runtime >= _next_snap[0] - 1e-9:
                timeline.append((runtime, d_e, state.snapshot(), (pn2_e, phe_e)))
                _next_snap[0] += snap_interval

    # ── Execute planned segments ───────────────────────────────────────────────
    current_depth = 0.0
    bottom_time   = 0.0
    phase_list    = [(0.0, 0.0, "surface", TissueState().snapshot())]  # initial state @ surface
    for seg_depth, total_seg_time in segments:
        # Transit time to reach depth
        if seg_depth != current_depth:
            rate     = desc_rate if seg_depth > current_depth else asc_rate
            t_travel = abs(seg_depth - current_depth) / rate
            # Use descend SP during descent, bottom SP during ascent between segments
            _phase_sp[0] = sp_desc if seg_depth > current_depth else sp_bottom
            _load_seg(current_depth, seg_depth, t_travel)
            current_depth = seg_depth
            phase_list.append((runtime, seg_depth, "transit", state.snapshot()))
        else:
            t_travel = 0.0
        # Remaining time at depth — bottom phase
        t_at_depth = max(0.0, total_seg_time - t_travel)
        if t_at_depth > 0:
            _phase_sp[0] = sp_bottom
            _load_seg(seg_depth, seg_depth, t_at_depth)
            bottom_time += t_at_depth
            phase_list.append((runtime, seg_depth, "bottom", state.snapshot()))

    # ── Tissue snapshot at end of bottom phase ───────────────────────────────
    snap_bottom = state.snapshot()

    # ── Find first stop ────────────────────────────────────────────────────────
    raw_ceil  = state.ceiling_depth(gf_low)
    first_stop = math.ceil(raw_ceil / stop_interval) * stop_interval if raw_ceil > 0 else 0.0

    if first_stop == 0.0:
        # No deco — ascend directly using bottom SP
        _phase_sp[0] = sp_bottom
        t_asc = current_depth / asc_rate
        _load_seg(current_depth, 0.0, t_asc)
        phase_list.append((runtime, 0.0, "transit", state.snapshot()))
        return DiveResult(stops=[], bottom_time=bottom_time,
                          tts=runtime - bottom_time,
                          runtime=runtime, otu=otu, cns=cns,
                          tissue_at_depth=snap_bottom,
                          tissue_at_surface=state.snapshot(),
                          tissue_timeline=timeline,
                          tissue_phase_list=phase_list)

    # ── Ascend to first stop using bottom SP ──────────────────────────────────
    _phase_sp[0] = sp_bottom
    t_to_first = abs(current_depth - first_stop) / asc_rate
    _load_seg(current_depth, first_stop, t_to_first)
    current_depth = first_stop
    phase_list.append((runtime, first_stop, "transit", state.snapshot()))

    # ── Work through deco stops using deco SP ────────────────────────────────
    _phase_sp[0] = sp_deco
    deco_start = runtime
    depth      = first_stop

    while depth > 0.0:
        next_stop  = max(0.0, depth - stop_interval)
        # Enforce last stop: don't ascend past last_stop until ceiling clears to surface
        if last_stop > 0.0 and next_stop < last_stop:
            next_stop = 0.0
        stop_t     = 0.0

        # GF at this stop (linear interpolation)
        gf = gf_high + (gf_low - gf_high) * (depth / first_stop)
        gf = max(gf_low, min(gf_high, gf))

        # Hold until ceiling allows moving to next stop
        while True:
            if state.ceiling_depth(gf) <= next_stop:
                break
            _load_seg(depth, depth, 1.0)
            stop_t += 1.0

        # Record stop (only if we actually stayed)
        if stop_t > 0:
            _, _, glabel = _inspired(depth, mode, ccr, oc_gases, sp=sp_deco)
            stops.append(Stop(depth=depth, time=stop_t,
                              gas=glabel, runtime=runtime,
                              tissue_snapshot=state.snapshot()))

        if next_stop == 0.0:
            # Final ascent from last stop to surface at deco rate
            t_final = depth / deco_rate
            _load_seg(depth, 0.0, t_final)
            break

        # Ascend to next stop
        t_step = stop_interval / deco_rate
        _load_seg(depth, next_stop, t_step)
        depth = next_stop

    tts = runtime - deco_start

    return DiveResult(stops=stops, bottom_time=bottom_time,
                      tts=tts, runtime=runtime, otu=otu, cns=cns,
                      tissue_at_depth=snap_bottom,
                      tissue_at_surface=state.snapshot(),
                      tissue_timeline=timeline,
                      tissue_phase_list=phase_list,
                      first_stop_depth=first_stop)


def simulate_bailout_from_bottom(
    segments:      List[Tuple[float, float]],
    ccr:           Optional[CCRConfig],
    oc_gases:      List[OCGas],
    gf_low:        float,
    gf_high:       float,
    desc_rate:     float = 20.0,
    asc_rate:      float = 9.0,
    deco_rate:     float = 3.0,
    snap_interval: float = 1.0,
    stop_interval: float = 3.0,
) -> DiveResult:
    """
    Worst-case bailout: simulate CCR bottom phase (descent + segments),
    then ascend OC from that tissue state using oc_gases.
    """
    if not oc_gases:
        oc_gases = [OCGas(o2=0.21, he=0.0, switch_depth=999)]

    state         = TissueState()
    runtime       = 0.0
    otu           = 0.0
    cns           = 0.0
    current_depth = 0.0
    bottom_time   = 0.0
    timeline: list = []
    _next_snap = [snap_interval]

    def _load_ccr(d_from, d_to, t_min):
        nonlocal runtime, otu, cns
        if t_min <= 0:
            return
        t_left = t_min; t_done = 0.0
        while t_left > 1e-9:
            to_snap = _next_snap[0] - runtime
            chunk   = min(t_left, to_snap) if to_snap > 1e-9 else min(t_left, snap_interval)
            chunk   = max(chunk, 1e-9)
            frac_s  = t_done / t_min
            frac_e  = (t_done + chunk) / t_min
            d_s = d_from + (d_to - d_from) * frac_s
            d_e = d_from + (d_to - d_from) * frac_e
            pn2_s, phe_s, _ = _inspired(d_s, "ccr", ccr, oc_gases)
            pn2_e, phe_e, _ = _inspired(d_e, "ccr", ccr, oc_gases)
            state.load(d_s, d_e, chunk, pn2_s, pn2_e, phe_s, phe_e)
            po2 = ccr.setpoint if ccr else 0.0
            otu += _otu_rate(po2) * chunk
            cns += _cns_rate(po2) * chunk
            runtime += chunk; t_left -= chunk; t_done += chunk
            if runtime >= _next_snap[0] - 1e-9:
                timeline.append((runtime, d_e, state.snapshot(), (pn2_e, phe_e)))
                _next_snap[0] += snap_interval

    def _load_oc(d_from, d_to, t_min):
        nonlocal runtime, otu, cns
        if t_min <= 0:
            return
        t_left = t_min; t_done = 0.0
        while t_left > 1e-9:
            to_snap = _next_snap[0] - runtime
            chunk   = min(t_left, to_snap) if to_snap > 1e-9 else min(t_left, snap_interval)
            chunk   = max(chunk, 1e-9)
            frac_s  = t_done / t_min
            frac_e  = (t_done + chunk) / t_min
            d_s = d_from + (d_to - d_from) * frac_s
            d_e = d_from + (d_to - d_from) * frac_e
            pn2_s, phe_s, _ = _inspired(d_s, "oc", ccr, oc_gases)
            pn2_e, phe_e, _ = _inspired(d_e, "oc", ccr, oc_gases)
            state.load(d_s, d_e, chunk, pn2_s, pn2_e, phe_s, phe_e)
            gas = select_oc_gas((d_s + d_e) / 2, oc_gases)
            po2 = _p_amb((d_s + d_e) / 2) * gas.o2
            otu += _otu_rate(po2) * chunk
            cns += _cns_rate(po2) * chunk
            runtime += chunk; t_left -= chunk; t_done += chunk
            if runtime >= _next_snap[0] - 1e-9:
                timeline.append((runtime, d_e, state.snapshot(), (pn2_e, phe_e)))
                _next_snap[0] += snap_interval

    # ── CCR bottom phase ───────────────────────────────────────────────────────
    phase_list = [(0.0, 0.0, "surface", TissueState().snapshot())]
    for depth, total_seg_time in segments:
        if depth != current_depth:
            rate     = desc_rate if depth > current_depth else asc_rate
            t_travel = abs(depth - current_depth) / rate
            _load_ccr(current_depth, depth, t_travel)
            current_depth = depth
            phase_list.append((runtime, depth, "transit", state.snapshot()))
        else:
            t_travel = 0.0
        t_at_depth = max(0.0, total_seg_time - t_travel)
        if t_at_depth > 0:
            _load_ccr(depth, depth, t_at_depth)
            bottom_time += t_at_depth
            phase_list.append((runtime, depth, "bottom", state.snapshot()))

    # ── Bailout switch point ───────────────────────────────────────────────────
    phase_list.append((runtime, current_depth, "bailout", state.snapshot()))

    # ── OC ascent from bottom tissue state ─────────────────────────────────────
    snap_bottom = state.snapshot()
    stops: List[Stop] = []
    raw_ceil   = state.ceiling_depth(gf_low)
    first_stop = math.ceil(raw_ceil / stop_interval) * stop_interval if raw_ceil > 0 else 0.0

    def _oc_ascend_to(target: float):
        """Ascend OC from current_depth to target, switching gas at waypoints."""
        nonlocal current_depth
        wps = _oc_ascent_waypoints(current_depth, target, oc_gases)
        prev = current_depth
        for wp in wps:
            if wp >= prev:
                continue
            t_seg = abs(prev - wp) / asc_rate
            _load_oc(prev, wp, t_seg)
            prev = wp
        current_depth = target

    if first_stop == 0.0:
        _oc_ascend_to(0.0)
        phase_list.append((runtime, 0.0, "transit", state.snapshot()))
        return DiveResult(stops=[], bottom_time=bottom_time,
                          tts=runtime - bottom_time,
                          runtime=runtime, otu=otu, cns=cns,
                          tissue_at_depth=snap_bottom,
                          tissue_at_surface=state.snapshot(),
                          tissue_timeline=timeline,
                          tissue_phase_list=phase_list)

    _oc_ascend_to(first_stop)
    phase_list.append((runtime, first_stop, "transit", state.snapshot()))

    deco_start = runtime
    depth      = first_stop

    while depth > 0.0:
        next_stop = max(0.0, depth - stop_interval)
        stop_t    = 0.0
        gf = gf_high + (gf_low - gf_high) * (depth / first_stop)
        gf = max(gf_low, min(gf_high, gf))

        while True:
            if state.ceiling_depth(gf) <= next_stop:
                break
            _load_oc(depth, depth, 1.0)
            stop_t += 1.0

        if stop_t > 0:
            _, _, glabel = _inspired(depth, "oc", ccr, oc_gases)
            stops.append(Stop(depth=depth, time=stop_t,
                              gas=glabel, runtime=runtime,
                              tissue_snapshot=state.snapshot()))

        if next_stop == 0.0:
            # Final ascent from last stop to surface at deco rate
            _load_oc(depth, 0.0, depth / deco_rate)
            break
        _load_oc(depth, next_stop, stop_interval / deco_rate)
        depth = next_stop

    phase_list.append((runtime, 0.0, "transit", state.snapshot()))

    return DiveResult(stops=stops, bottom_time=bottom_time,
                      tts=runtime - deco_start,
                      runtime=runtime, otu=otu, cns=cns,
                      tissue_at_depth=snap_bottom,
                      tissue_at_surface=state.snapshot(),
                      tissue_timeline=timeline,
                      tissue_phase_list=phase_list,
                      first_stop_depth=first_stop)


# ── Self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if sys.stdout is not None:
        try: sys.stdout.reconfigure(encoding="utf-8")
        except Exception: pass
    W = 52
    print("\n" + "=" * W)
    print("  buhlmann.py -- ZHL-16C self-test")
    print("=" * W)
    print(f"  Tissues     : {len(TISSUES)}")
    print(f"  Stop interval: {STOP_INT} m")
    print(f"  PH2O        : {PH2O} bar")
    print("-" * W)

    # CCR dive to 60m/20min segment, GF 30/80
    DEPTH, SEGMENT, DESC_RATE, ASC_RATE, DECO_RATE = 60.0, 20.0, 20.0, 9.0, 3.0
    desc_time = DEPTH / DESC_RATE
    ccr = CCRConfig(setpoint=1.3, diluent_o2=0.21, diluent_he=0.35)

    def _print_result(r, label):
        print(f"  Dive: {label}")
        print(f"  Descent rate : {DESC_RATE:.0f} m/min")
        print(f"  Descent time : {desc_time:.1f} min")
        print(f"  Ascent rate  : {ASC_RATE:.0f} m/min")
        print(f"  Deco rate    : {DECO_RATE:.0f} m/min")
        print(f"  Bottom time  : {r.bottom_time:.1f} min")
        print(f"  TTS          : {r.tts:.1f} min")
        print(f"  Runtime      : {r.runtime:.1f} min")
        print(f"  OTU          : {r.otu:.0f}")
        print(f"  CNS          : {r.cns:.1f} %")
        print("-" * W)
        print(f"  {'Depth':>8}  {'Time':>6}  {'Runtime':>8}  Gas")
        print("-" * W)
        for s in r.stops:
            print(f"  {s.depth:>6.0f} m  {s.time:>5.0f}'  {s.runtime:>7.1f}'  {s.gas}")

    result = simulate_dive(
        segments=[(DEPTH, SEGMENT)], mode="ccr", ccr=ccr,
        oc_gases=[OCGas(0.21, 0.35, 999), OCGas(0.50, 0.0, 21), OCGas(1.0, 0.0, 6)],
        gf_low=0.55, gf_high=0.70, desc_rate=DESC_RATE, asc_rate=ASC_RATE, deco_rate=DECO_RATE,
    )
    _print_result(result, "CCR SP1.3 / Trimix 21/35 / 60m 20min seg / GF55/70")
    print("═" * W + "\n")
