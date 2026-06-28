"""
gasblend.eos
============
Equation-of-state layer.  The blending engine touches the EOS through one
interface (``molar_volume`` / ``Z`` / ``moles``).

The engine runs on **GERG-2008** via CoolProp's HEOS multi-fluid backend
(reference equations + binary departure functions) -- the high-accuracy
reference for He/O2/N2 mixtures.  The abstract ``EOS`` base is kept so a
different provider (REFPROP, a tabulated model, ...) can be plugged in
later without touching the blender; today there is exactly one backend.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from .gases import R, BAR_KPA, T_STD, normalise, Mix


class EOS(ABC):
    """Equation-of-state interface used by the blender."""
    name: str = "eos"

    @abstractmethod
    def molar_volume(self, mix: Mix, P_bar: float, T: float = T_STD) -> float:
        """Molar volume [L/mol] of `mix` at P [bar], T [K]."""

    def Z(self, mix: Mix, P_bar: float, T: float = T_STD) -> float:
        """Compressibility factor Z = P v / (R T)."""
        v = self.molar_volume(mix, P_bar, T)
        return (P_bar * BAR_KPA) * v / (R * T)

    def moles(self, mix: Mix, V_litre: float, P_bar: float,
              T: float = T_STD) -> float:
        """Total moles held in a cylinder of water volume `V_litre`."""
        if P_bar <= 0:
            return 0.0
        return (P_bar * BAR_KPA) * V_litre / (self.Z(mix, P_bar, T) * R * T)


class GergEOS(EOS):
    """CoolProp HEOS multi-fluid (GERG-2008 mixing model).

    Uses the low-level ``AbstractState`` API with an imposed gas phase
    instead of ``PropsSI``.  All diving mixes at >0 C are supercritical
    (T above every component critical temperature), so the fluid is
    single-phase and the saturation/phase-determination step that makes
    ``PropsSI`` throw ``"One stationary point (not good)"`` on some
    CoolProp versions can be skipped safely.
    """
    name = "GERG-2008 (HEOS)"
    _NAME = {"He": "Helium", "O2": "Oxygen", "N2": "Nitrogen"}

    def __init__(self):
        import CoolProp                       # raises if not installed
        from CoolProp.CoolProp import AbstractState
        self._AbstractState = AbstractState
        self._PT = CoolProp.CoolProp.PT_INPUTS
        self._gas = CoolProp.CoolProp.iphase_gas
        self._cache = {}                       # component-tuple -> state

    def _state(self, comps):
        st = self._cache.get(comps)
        if st is None:
            st = self._AbstractState("HEOS", "&".join(comps))
            self._cache[comps] = st
        return st

    def molar_volume(self, mix: Mix, P_bar: float, T: float = T_STD) -> float:
        x = normalise(mix)
        comps = tuple(self._NAME[g] for g in x)
        st = self._state(comps)
        try:
            st.set_mole_fractions([x[g] for g in x])
            st.specify_phase(self._gas)        # skip phase-determination
            st.update(self._PT, P_bar * 1e5, T)
            return 1000.0 / st.rhomolar()       # mol/m3 -> L/mol
        except Exception as e:
            raise RuntimeError(
                f"GERG/CoolProp failed for {mix} at {P_bar} bar ({e}). "
                f"Upgrade CoolProp (pip install -U CoolProp).") from e


def get_eos(name: str = "gerg") -> EOS:
    """Factory.  Only GERG-2008 (CoolProp HEOS) is provided.
    Raises a clear error if CoolProp is unavailable."""
    key = name.lower()
    if key in ("gerg", "heos", "gerg2008", "coolprop", ""):
        try:
            return GergEOS()
        except Exception as e:
            raise RuntimeError(
                "GERG EOS requires CoolProp. Install it with "
                "`pip install CoolProp`.") from e
    raise ValueError(f"unknown EOS '{name}' (only 'gerg' is available)")
