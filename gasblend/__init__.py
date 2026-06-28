"""
gasblend - gas blending engine for TechDivePlanner
==================================================
Real-gas partial-pressure gas blending, running on GERG-2008 (CoolProp HEOS).

Quick start
-----------
    from gasblend import Blender, CylinderState, get_eos, limits

    blender = Blender(get_eos("gerg"))           # GERG-2008 via CoolProp
    start   = CylinderState(V=24.0, P=0.0)       # empty twin-12
    res     = blender.blend(start, target_O2=0.18, target_He=0.45,
                            target_P=200.0)
    print(res.pretty())
    print("MOD:", limits.mod({"O2":0.18,"He":0.45,"N2":0.37}, 1.4))

The engine is UI-framework-agnostic; build the PyQt6 tab on top of it.
"""
from .gases import (Gas, GASES, HE, O2, N2, AIR, PURE_O2, PURE_HE,
                    normalise, fO2, fHe, fN2, label, Mix)
from .eos import EOS, GergEOS, get_eos
from .cylinder import CylinderState
from .blender import Blender, FillStep, BlendResult
from . import limits

__all__ = [
    "Gas", "GASES", "HE", "O2", "N2", "AIR", "PURE_O2", "PURE_HE",
    "normalise", "fO2", "fHe", "fN2", "label", "Mix",
    "EOS", "GergEOS", "get_eos",
    "CylinderState", "Blender", "FillStep", "BlendResult", "limits",
]
__version__ = "0.1.0"
