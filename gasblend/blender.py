"""
gasblend.blender
================
Real-gas partial-pressure blending engine.

One solver parameterised by:

    * start state         (empty / existing mix = top-off / bank decant)
    * target O2, He, P
    * available pure gases (O2, He) and a top gas (air, nitrox or bank mix)
    * the EOS              (GERG-2008)

Fill order: **He -> O2 -> top gas**.  Each pure-gas fill pressure is found
by Newton iteration on the real-gas amount of that gas.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from .gases import Mix, normalise, AIR, GASES, R, BAR_KPA, T_STD, label, fO2, fHe
from .eos import EOS, get_eos
from .cylinder import CylinderState


@dataclass
class FillStep:
    gas: str                 # 'He', 'O2', or a top-gas label
    to_pressure: float       # gauge pressure (bar) to fill up to
    mix_added: Mix           # composition of what is being added


@dataclass
class BlendResult:
    steps: List[FillStep]
    final: CylinderState
    warnings: List[str] = field(default_factory=list)
    feasible: bool = True

    def pretty(self) -> str:
        lines = [f"Target {label(self.final.mix)} @ {self.final.P:.0f} bar"]
        prev = self.final.P * 0
        for s in self.steps:
            lines.append(f"  add {s.gas:14s} -> {s.to_pressure:6.1f} bar")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)


class Blender:
    """EOS-agnostic gas blender.  Construct with an EOS (default GERG)."""

    def __init__(self, eos: Optional[EOS] = None):
        self.eos = eos or get_eos("gerg")

    # ---- public API --------------------------------------------------
    def blend(self,
              start: CylinderState,
              target_O2: float,
              target_He: float,
              target_P: float,
              top_gas: Mix = None,
              allow_drain: bool = False) -> BlendResult:
        """Compute fill instructions to reach the target.

        `target_O2`/`target_He` are fractions (0..1); N2 is the remainder.
        `top_gas` is the gas used for the final top-off (default air).
        Set `allow_drain=True` to permit a drain-down step when the start
        mix already contains too much of a species.
        """
        if top_gas is None:
            top_gas = AIR
        top = normalise(top_gas)
        T = start.T
        target_N2 = max(0.0, 1.0 - target_O2 - target_He)
        target_mix = {"O2": target_O2, "He": target_He, "N2": target_N2}
        warnings: List[str] = []

        n_final = self.eos.moles(target_mix, start.V, target_P, T)
        need = {g: n_final * target_mix[g] for g in GASES}
        have = start.species_moles(self.eos)

        add = {g: need[g] - have.get(g, 0.0) for g in GASES}

        # --- drain handling ------------------------------------------
        drain_step = None
        if any(add[g] < -1e-6 for g in GASES):
            if not allow_drain:
                worst = min(GASES, key=lambda g: add[g])
                return BlendResult(
                    [], start,
                    [f"Cannot reach target without draining: start already "
                     f"has more {worst} than target. Empty/partly drain the "
                     f"cylinder, or enable allow_drain."],
                    feasible=False)
            # drain proportionally: find pressure where every species <= need
            frac_keep = min(min(need[g] / have[g] for g in GASES if have[g] > 1e-9), 1.0)
            P_drain = self._drain_to(start, frac_keep)
            drain_step = FillStep("DRAIN", round(P_drain, 1), dict(start.mix))
            start = CylinderState(start.V, P_drain, dict(start.mix), T)
            have = start.species_moles(self.eos)
            add = {g: need[g] - have.get(g, 0.0) for g in GASES}

        # --- size the top gas, back out pure O2/He -------------------
        n_top = add["N2"] / top.get("N2", 1e-12) if top.get("N2") else 0.0
        add_O2 = add["O2"] - n_top * top.get("O2", 0.0)
        add_He = add["He"] - n_top * top.get("He", 0.0)
        if add_O2 < -1e-6:
            warnings.append("Top gas supplies too much O2 for this target; "
                            "use a leaner top gas (air or pure N2).")
            add_O2 = max(add_O2, 0.0)
        if add_He < -1e-6:
            add_He = max(add_He, 0.0)

        steps: List[FillStep] = []
        if drain_step:
            steps.append(drain_step)
        running = dict(have)

        if add_He > 1e-9:
            P = self._fill_to(start.V, T, running, "He", running.get("He", 0) + add_He)
            steps.append(FillStep("He", round(P, 1), {"He": 1.0}))
            running["He"] = running.get("He", 0) + add_He

        if add_O2 > 1e-9:
            P = self._fill_to(start.V, T, running, "O2", running.get("O2", 0) + add_O2)
            steps.append(FillStep("O2", round(P, 1), {"O2": 1.0}))
            running["O2"] = running.get("O2", 0) + add_O2

        # top off to final pressure (skip if no top gas is needed, e.g.
        # heliox, where the last pure fill already reaches target_P)
        if n_top > 1e-9:
            for g in GASES:
                running[g] = running.get(g, 0) + n_top * top.get(g, 0.0)
            steps.append(FillStep(label(top), round(target_P, 1), dict(top)))
        elif steps:
            steps[-1].to_pressure = round(target_P, 1)

        final = CylinderState(start.V, target_P, dict(running), T)
        # sanity: confirm round-trip composition
        fx = normalise(final.mix)
        if abs(fx.get("O2", 0) - target_O2) > 5e-3 or abs(fx.get("He", 0) - target_He) > 5e-3:
            warnings.append("Round-trip composition drifted >0.5%; check inputs.")
        return BlendResult(steps, final, warnings, feasible=True)

    # ---- internal solvers -------------------------------------------
    def _fill_to(self, V, T, base_moles: Dict[str, float],
                 add_gas: str, target_addgas_moles: float) -> float:
        """Gauge pressure to fill PURE `add_gas` into a cylinder already
        holding `base_moles` until its `add_gas` content == target."""
        fixed = {g: m for g, m in base_moles.items() if g != add_gas and m > 0}

        def addgas_at(P):
            if P <= 0:
                return 0.0
            running = dict(fixed)
            running[add_gas] = max(target_addgas_moles, 1e-12)
            n_total = self.eos.moles(running, V, P, T)
            return n_total * normalise(running)[add_gas]

        n_fixed = sum(fixed.values())
        P = (n_fixed + target_addgas_moles) * R * T / (BAR_KPA * V)
        for _ in range(100):
            f = addgas_at(P) - target_addgas_moles
            dP = max(P * 1e-7, 1e-6)
            df = (addgas_at(P + dP) - addgas_at(P - dP)) / (2 * dP)
            if df == 0:
                break
            step = f / df
            P -= step
            if abs(step) < 1e-9:
                break
        return max(P, 0.0)

    def _drain_to(self, start: CylinderState, frac_keep: float) -> float:
        """Pressure to drain the cylinder down to `frac_keep` of its moles."""
        target_moles = start.total_moles(self.eos) * frac_keep
        P = start.P * frac_keep
        for _ in range(100):
            f = self.eos.moles(start.mix, start.V, P, start.T) - target_moles
            dP = max(P * 1e-7, 1e-6)
            df = (self.eos.moles(start.mix, start.V, P + dP, start.T)
                  - self.eos.moles(start.mix, start.V, P - dP, start.T)) / (2 * dP)
            if df == 0:
                break
            P -= f / df
            if abs(f) < 1e-9:
                break
        return max(P, 0.0)
