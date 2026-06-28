"""
gasblend.cylinder
=================
Cylinder geometry and gas state.  A cylinder is defined by its water
volume; a state adds pressure, temperature and the mix it holds.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict
from .gases import Mix, normalise, T_STD, GASES
from .eos import EOS


@dataclass
class CylinderState:
    """A cylinder of `V` litres water volume holding `mix` at `P` bar / `T` K.

    Twin/manifolded sets: pass the *total* water volume (e.g. twin 12 L
    -> V=24).  An empty cylinder is P=0 (mix is then irrelevant)."""
    V: float
    P: float = 0.0
    mix: Mix = field(default_factory=lambda: {"O2": 0.209, "N2": 0.791})
    T: float = T_STD

    def species_moles(self, eos: EOS) -> Dict[str, float]:
        """Moles of each species (He/O2/N2) currently in the cylinder."""
        if self.P <= 0:
            return {g: 0.0 for g in GASES}
        n = eos.moles(self.mix, self.V, self.P, self.T)
        x = normalise(self.mix)
        return {g: n * x.get(g, 0.0) for g in GASES}

    def total_moles(self, eos: EOS) -> float:
        return eos.moles(self.mix, self.V, self.P, self.T) if self.P > 0 else 0.0
