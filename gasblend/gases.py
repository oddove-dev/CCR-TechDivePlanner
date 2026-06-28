"""
gasblend.gases
==============
Pure-gas definitions and mixture helpers for the TechDivePlanner gas
blending engine.

A mixture is just a dict of mole fractions, e.g.
``{"O2": 0.18, "He": 0.45, "N2": 0.37}``.  Fractions need not sum to 1;
helpers normalise on demand.

Molar masses match CODATA.  The engine runs exclusively on GERG-2008.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict

Mix = Dict[str, float]

R = 8.314472          # kPa.L/(mol.K)  ==  J/(mol.K)
ATM_KPA = 101.325
BAR_KPA = 100.0
T_STD = 288.15        # 15 C reference


@dataclass(frozen=True)
class Gas:
    """A pure gas: symbol, display name and molar mass (g/mol)."""
    symbol: str
    name: str
    M: float


HE = Gas("He", "Helium",    4.0026)
O2 = Gas("O2", "Oxygen",   31.9988)
N2 = Gas("N2", "Nitrogen", 28.0134)

GASES: Dict[str, Gas] = {g.symbol: g for g in (HE, O2, N2)}

# Common source/top gases used when blending.
AIR: Mix = {"O2": 0.209, "N2": 0.791}
PURE_O2: Mix = {"O2": 1.0}
PURE_HE: Mix = {"He": 1.0}


def normalise(mix: Mix) -> Mix:
    """Return mole fractions summing to 1.0 (drops zero/negative entries)."""
    tot = sum(v for v in mix.values() if v > 0)
    if tot <= 0:
        raise ValueError("empty mixture")
    return {g: v / tot for g, v in mix.items() if v > 0}


def fO2(mix: Mix) -> float:
    return normalise(mix).get("O2", 0.0)


def fHe(mix: Mix) -> float:
    return normalise(mix).get("He", 0.0)


def fN2(mix: Mix) -> float:
    return normalise(mix).get("N2", 0.0)


def label(mix: Mix) -> str:
    """Human label: 'Air', 'EAN32', 'Tx 18/45', 'Heliox 16/84', etc."""
    x = normalise(mix)
    o, h = round(x.get("O2", 0) * 100), round(x.get("He", 0) * 100)
    if h == 0:
        if o == 21:
            return "Air"
        return f"EAN{o}" if o else "N2"
    if x.get("N2", 0) < 1e-6:
        return f"Heliox {o}/{h}"
    return f"Tx {o}/{h}"
