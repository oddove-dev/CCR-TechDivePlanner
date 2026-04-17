#!/usr/bin/env python3
"""
Scuba cylinder buoyancy calculator and database.

Calculation chain:
  delta_mass        = dry_mass - wet_mass               (buoyancy force in fresh water)
  volume            = delta_mass / rho_fw                (external cylinder volume [L])
  sw_mass           = volume * rho_sw                    (displaced saltwater mass [kg])

  gas_mass          = P_abs * V_int * M / (Z * R * T)   (real gas law, Z from GERG-2008)
  empty_mass        = dry_mass - gas_mass                (bottle + valve, no gas)

  buoyancy_fw       = volume * rho_fw - dry_mass         (full cylinder in fresh water)
  buoyancy_sw       = volume * rho_sw - dry_mass         (full cylinder in saltwater)
  empty_buoy_fw_ref = volume * rho_fw - empty_mass       (empty cylinder in fresh water)
  empty_buoy_sw_ref = volume * rho_sw - empty_mass       (empty cylinder in saltwater)

  Positive buoyancy = cylinder floats / has upward force.
  Negative buoyancy = cylinder sinks / has downward force.

Gas mixture:
  Specify o2 (% O2) and he (% He) directly.  N2 is implicit: n2 = 100 - o2 - he.
  Examples:
    Air           o2=21, he=0   -> n2=79
    Nitrox 32     o2=32, he=0   -> n2=68
    Nitrox 65     o2=65, he=0   -> n2=35
    Trimix 21/35  o2=21, he=35  -> n2=44
    Trimix 18/45  o2=18, he=45  -> n2=37   (any mix you like)

Real gas model:
  Primary  : GERG-2008 multiparameter Helmholtz EOS via CoolProp.
             Accurate to ~0.1% for He/N2/O2 mixtures at diving pressures.
             Requires: pip install CoolProp
  Fallback : Peng-Robinson EOS for O2/N2, first-order virial for He.
             Used automatically if CoolProp is not installed.
"""

import sys
import json
from pathlib import Path

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8')

try:
    import CoolProp.CoolProp as _CP
    _COOLPROP_AVAILABLE = True
except ImportError:
    _CP = None
    _COOLPROP_AVAILABLE = False

DB_FILE = Path(__file__).parent / "cylindercalc_db.json"

# ── Physical constants ────────────────────────────────────────────────────────
R      = 8.314    # Universal gas constant [J/(mol·K) = Pa·m3/(mol·K)]
R_BAR  = 83.14    # Universal gas constant [cm3·bar/(mol·K)]
RHO_FW = 0.998    # Fresh water density [kg/L]
RHO_SW = 1.024    # Sea water density [kg/L]
RHO_PB = 11.34    # Lead (bly) density [kg/L]


# ── Equipment ─────────────────────────────────────────────────────────────────
class Equipment:
    """
    Diving equipment item with buoyancy from dry and fresh-water wet mass.
    """

    def __init__(self, name: str, dry_mass: float, wet_mass: float,
                 description: str = "", category: str = "",
                 jj_core: bool = False, jj_modular: bool = False,
                 diver_buoyancy: bool = False, stage: bool = False):
        self.name           = name
        self.dry_mass       = dry_mass      # [kg]
        self.wet_mass       = wet_mass      # [kg], apparent mass submerged in fresh water
        self.description    = description
        self.category       = category
        self.jj_core        = bool(jj_core)
        self.jj_modular     = bool(jj_modular)
        self.diver_buoyancy = bool(diver_buoyancy)
        self.stage          = bool(stage)

    @property
    def delta_mass(self) -> float:
        return self.dry_mass - self.wet_mass

    @property
    def volume(self) -> float:
        return self.delta_mass / RHO_FW

    @property
    def buoyancy_fw(self) -> float:
        return self.volume * RHO_FW - self.dry_mass

    @property
    def buoyancy_sw(self) -> float:
        return self.volume * RHO_SW - self.dry_mass


# ── Diver buoyancy ─────────────────────────────────────────────────────────────
class DiverBuoyancy:
    """
    Diver's positive buoyancy from lead dry weight (neutral in SW) and diver dry mass.

    Calculation chain:
      V_lead     = lead_dry_mass / RHO_PB
      V_diver    = (diver_dry_mass + lead_dry_mass) / RHO_SW - V_lead
                   (from neutral-buoyancy condition in saltwater)
      buoy_sw    = V_diver * RHO_SW - diver_dry_mass   (= -lead_buoyancy_sw, positive)
      buoy_fw    = V_diver * RHO_FW - diver_dry_mass   (smaller positive value)
    """

    def __init__(self, name: str, lead_dry_mass: float, diver_dry_mass: float = None,
                 category: str = ""):
        self.name           = name
        self.lead_dry_mass  = lead_dry_mass    # [kg]
        self.diver_dry_mass = diver_dry_mass   # [kg], optional
        self.category       = category

    @property
    def lead_volume(self) -> float:
        return self.lead_dry_mass / RHO_PB

    @property
    def lead_buoyancy_sw(self) -> float:
        return self.lead_volume * RHO_SW - self.lead_dry_mass

    @property
    def diver_buoyancy_sw(self) -> float:
        """Diver SW buoyancy = -lead_buoyancy_sw (neutral buoyancy condition)."""
        return -self.lead_buoyancy_sw

    @property
    def diver_volume(self):
        """Volume of diver [L]. Requires diver_dry_mass."""
        if self.diver_dry_mass is None:
            return None
        return (self.diver_dry_mass + self.lead_dry_mass) / RHO_SW - self.lead_volume

    @property
    def diver_buoyancy_fw(self):
        """Diver FW buoyancy. Requires diver_dry_mass."""
        if self.diver_dry_mass is None or self.diver_volume is None:
            return None
        return self.diver_volume * RHO_FW - self.diver_dry_mass


# ── Gas component properties ──────────────────────────────────────────────────
# Tc [K], Pc [bar], acentric factor [-], molar mass [kg/mol]
# He: PR EOS is unreliable far above Tc (5.2 K) — use virial instead.
#     B [cm3/mol]: experimental second virial coefficient at ~20 C.
_GAS_PROPS = {
    'O2': {'Tc': 154.60, 'Pc': 50.46, 'omega': 0.0221, 'M': 0.032},
    'N2': {'Tc': 126.19, 'Pc': 33.96, 'omega': 0.0372, 'M': 0.028},
    'He': {'M': 0.004, 'B': 12.0},
}


def _pr_Z(comp: str, P_bar: float, T_K: float) -> float:
    """
    Peng-Robinson Z for a pure gas (O2 or N2).
    Both are super-critical at 20 C so the cubic has one real root;
    Newton-Raphson from Z=1 converges reliably.
    """
    g = _GAS_PROPS[comp]
    Tc, Pc, omega = g['Tc'], g['Pc'], g['omega']
    kappa = 0.37464 + 1.54226 * omega - 0.26992 * omega ** 2
    alpha = (1.0 + kappa * (1.0 - (T_K / Tc) ** 0.5)) ** 2
    a = 0.45724 * R_BAR ** 2 * Tc ** 2 / Pc * alpha   # cm6·bar/mol2
    b = 0.07780 * R_BAR * Tc / Pc                       # cm3/mol

    A = a * P_bar / (R_BAR * T_K) ** 2
    B = b * P_bar / (R_BAR * T_K)

    c2 = -(1.0 - B)
    c1 = A - 3.0 * B * B - 2.0 * B
    c0 = -(A * B - B * B - B * B * B)

    Z = 1.0
    for _ in range(100):
        f  = Z ** 3 + c2 * Z ** 2 + c1 * Z + c0
        fp = 3.0 * Z ** 2 + 2.0 * c2 * Z + c1
        if abs(fp) < 1e-15:
            break
        dZ = -f / fp
        Z += dZ
        if abs(dZ) < 1e-10:
            break
    return Z


def z_mix(P_bar: float, T_K: float, o2: int, he: int) -> float:
    """
    Compressibility factor Z for a diving gas mixture.

    Uses GERG-2008 Helmholtz EOS via CoolProp when available,
    otherwise falls back to PR (O2/N2) + virial (He).

    Parameters
    ----------
    P_bar : absolute pressure [bar]
    T_K   : temperature [K]
    o2    : O2 percentage  (0-100)
    he    : He percentage  (0-100); N2 = 100 - o2 - he
    """
    if _COOLPROP_AVAILABLE:
        return _gerg_z_mix(P_bar, T_K, o2, he)
    n2 = 100 - o2 - he
    Z = 0.0
    if o2: Z += (o2 / 100) * _pr_Z('O2', P_bar, T_K)
    if n2: Z += (n2 / 100) * _pr_Z('N2', P_bar, T_K)
    if he: Z += (he / 100) * (1.0 + _GAS_PROPS['He']['B'] * P_bar / (R_BAR * T_K))
    return Z


def _gerg_state(P_bar: float, T_K: float, o2: int, he: int):
    """
    Build and return a CoolProp AbstractState for the gas mixture at (P, T).
    """
    n2 = 100 - o2 - he
    components, fractions = [], []
    if he > 0:
        components.append("Helium");   fractions.append(he / 100)
    if n2 > 0:
        components.append("Nitrogen"); fractions.append(n2 / 100)
    if o2 > 0:
        components.append("Oxygen");   fractions.append(o2 / 100)

    AS = _CP.AbstractState("HEOS", "&".join(components))
    AS.set_mole_fractions(fractions)
    AS.specify_phase(_CP.iphase_gas)
    AS.update(_CP.PT_INPUTS, P_bar * 1e5, T_K)
    return AS


def _gerg_z_mix(P_bar: float, T_K: float, o2: int, he: int) -> float:
    """Compressibility factor Z via GERG-2008 (CoolProp HEOS backend)."""
    return _gerg_state(P_bar, T_K, o2, he).compressibility_factor()


# ── Gas utilities ─────────────────────────────────────────────────────────────
def gas_label(o2: int, he: int) -> str:
    """Human-readable gas name."""
    if he == 0:
        return "Air" if o2 == 21 else f"Nitrox {o2}"
    return f"Trimix {o2}/{he}"


def gas_molar_mass(o2: int, he: int) -> float:
    """Molar mass of gas mix [kg/mol]."""
    n2 = 100 - o2 - he
    return (o2 * _GAS_PROPS['O2']['M'] + he * _GAS_PROPS['He']['M'] + n2 * _GAS_PROPS['N2']['M']) / 100


def calc_gas_mass(volume_bottle_L: float, pressure_barg: float,
                  temp_C: float, o2: int, he: int) -> float:
    """
    Mass of gas in a cylinder [kg].

    CoolProp (GERG-2008): mass = rhomass [kg/m³] × V [m³]  (density from EOS directly)
    Fallback             : mass = P_abs * V * M / (Z * R * T)  (real-gas law, PR/virial)
    """
    P_bar = pressure_barg + 1.0
    V     = volume_bottle_L * 1e-3     # m³
    T_K   = temp_C + 273.15

    if _COOLPROP_AVAILABLE:
        rho = _gerg_state(P_bar, T_K, o2, he).rhomass()   # kg/m³
        return rho * V

    M = gas_molar_mass(o2, he)
    Z = z_mix(P_bar, T_K, o2, he)
    return (P_bar * 1e5) * V * M / (Z * R * T_K)


# ── Cylinder class ────────────────────────────────────────────────────────────
class Cylinder:
    """
    Scuba cylinder with buoyancy calculations.

    Measurement inputs
    ------------------
    name          : descriptive label
    dry_mass      : mass in air with gas [kg]
    wet_mass      : apparent weight submerged in fresh water [kg]
                    (negative = cylinder floats in fresh water)
    volume_bottle : internal water capacity [L]
    o2            : O2 percentage of fill gas  (integer, e.g. 21)
    he            : He percentage of fill gas  (integer, e.g. 35); N2 is implicit
    pressure      : fill pressure [barg]
    temp          : gas temperature [C], default 20
    """

    def __init__(self, name: str,
                 dry_mass: float, wet_mass: float,
                 volume_bottle: float,
                 o2: int, he: int,
                 pressure: float, temp: float = 20.0,
                 description: str = "", category: str = ""):
        self.name          = name
        self.dry_mass      = dry_mass
        self.wet_mass      = wet_mass
        self.volume_bottle = volume_bottle
        self.o2            = o2
        self.he            = he
        self.pressure      = pressure
        self.temp          = temp
        self.description   = description
        self.category      = category

    @property
    def n2(self) -> int:
        return 100 - self.o2 - self.he

    # ── Derived properties ────────────────────────────────────────────────────
    @property
    def delta_mass(self) -> float:
        return self.dry_mass - self.wet_mass

    @property
    def volume(self) -> float:
        """External volume [L]."""
        return self.delta_mass / RHO_FW

    @property
    def sw_mass(self) -> float:
        """Displaced saltwater mass [kg]."""
        return self.volume * RHO_SW

    @property
    def gas_mass(self) -> float:
        """Mass of compressed gas [kg] (PR EOS)."""
        return calc_gas_mass(self.volume_bottle, self.pressure,
                             self.temp, self.o2, self.he)

    @property
    def empty_mass(self) -> float:
        """Cylinder + valve mass with no gas [kg]."""
        return self.dry_mass - self.gas_mass

    @property
    def empty_buoy_fw(self) -> float:
        return self.volume * RHO_FW - self.empty_mass

    @property
    def empty_buoy_sw(self) -> float:
        return self.volume * RHO_SW - self.empty_mass

    @property
    def buoyancy_fw(self) -> float:
        return self.volume * RHO_FW - self.dry_mass

    @property
    def buoyancy_sw(self) -> float:
        return self.volume * RHO_SW - self.dry_mass

    # ── Output ────────────────────────────────────────────────────────────────
    def print_report(self):
        W = 56

        def row(label, val):
            print(f"  {label:<42} {val:>7.2f}")

        Z = z_mix(self.pressure + 1.0, self.temp + 273.15, self.o2, self.he)
        thick = '═' * W
        thin  = '─' * W

        print(f"\n{thick}")
        print(f"  {self.name}")
        print(f"{thick}")
        row("Dry mass [kg]",                        self.dry_mass)
        row("Wet mass FW [kg]",                      self.wet_mass)
        row("Delta mass [kg]",                       self.delta_mass)
        row("Density fresh water [kg/L]",            RHO_FW)
        row("Volume [L]",                            self.volume)
        row("Density saltwater [kg/L]",              RHO_SW)
        row("Sea water mass [kg]",                   self.sw_mass)
        print(f"{thick}")
        row("Volume bottle [L]",                     self.volume_bottle)
        print(f"  {'Gas':<42} {gas_label(self.o2, self.he):>7}")
        row("Pressure [barg]",                       self.pressure)
        row("Temperature [C]",                       self.temp)
        eos_label = "GERG-2008" if _COOLPROP_AVAILABLE else "PR/virial"
        print(f"  {f'Z (compressibility, {eos_label})':<42} {Z:>7.4f}")
        row("Gas mass [kg]",                         self.gas_mass)
        row("Mass on land, empty bottle [kg]",       self.empty_mass)
        print(f"{thick}")
        row("Empty buoyancy in freshwater ref [kg]", self.empty_buoy_fw)
        row("Empty buoyancy in saltwater ref  [kg]",  self.empty_buoy_sw)
        row("Buoyancy in freshwater [kg]",           self.buoyancy_fw)
        row("Buoyancy in saltwater   [kg]",           self.buoyancy_sw)
        print(f"{thin}")


def _load_db():
    if DB_FILE.exists():
        try:
            data = json.loads(DB_FILE.read_text(encoding="utf-8"))
            cylinders = [Cylinder(**c) for c in data.get("cylinders", [])]
            equipment = [Equipment(**e) for e in data.get("equipment", [])]
            divers = [DiverBuoyancy(**d) for d in data.get("divers", [])]
            return cylinders, equipment, divers
        except Exception:
            pass
    return [], [], []

CYLINDERS, EQUIPMENT, DIVERS = _load_db()

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    W = 52
    thick = '═' * W
    thin  = '-' * W

    def row(label, value, unit):
        print(f"  {label:<32} {value:>10}  {unit}")

    print(f"\n{thick}")
    print(f"  cylindercalc.py")
    print(f"{thick}")
    print(f"  Database    : {DB_FILE.name}")
    print(f"  Cylindere   : {len(CYLINDERS)}")
    print(f"  Utstyr      : {len(EQUIPMENT)}")
    print(f"  Dykkere     : {len(DIVERS)}")
    print(f"{thin}")
    print(f"  Fysiske konstanter")
    print(f"{thin}")
    row("Gasskonstant R",              f"{R:.3f}",    "J/(mol·K)")
    row("Gasskonstant R",              f"{R_BAR:.2f}","cm³·bar/(mol·K)")
    row("Tetthet ferskvann ρ_fw",      f"{RHO_FW:.3f}","kg/L")
    row("Tetthet saltvann ρ_sw",       f"{RHO_SW:.3f}","kg/L")
    row("Tetthet bly ρ_pb",            f"{RHO_PB:.2f}","kg/L")
    print(f"{thin}")
    print(f"  Molarmasser")
    print(f"{thin}")
    row("Oksygen  M_O2",               f"{_GAS_PROPS['O2']['M']:.3f}", "kg/mol")
    row("Nitrogen M_N2",               f"{_GAS_PROPS['N2']['M']:.3f}", "kg/mol")
    row("Helium   M_He",               f"{_GAS_PROPS['He']['M']:.3f}", "kg/mol")
    print(f"{thin}")
    row("He virial B_He (~20 °C)",     f"{_GAS_PROPS['He']['B']:.1f}", "cm³/mol")
    print(f"{thick}")

    # ── EOS-eksempel ──────────────────────────────────────────────────────────
    ex_o2, ex_he, ex_P, ex_T = 10, 70, 300, 20
    P_abs = ex_P + 1.0
    T_K   = ex_T + 273.15
    M_mix = gas_molar_mass(ex_o2, ex_he)
    ex_n2 = 100 - ex_o2 - ex_he

    # GERG-2008 via CoolProp
    if _COOLPROP_AVAILABLE:
        _as      = _gerg_state(P_abs, T_K, ex_o2, ex_he)
        z_gerg   = _as.compressibility_factor()
        rho_gerg = _as.rhomass() / 1000          # kg/m³ → kg/L
    else:
        z_gerg, rho_gerg = None, None

    # PR/virial fallback (alltid beregnet)
    z_fb = 0.0
    if ex_o2: z_fb += (ex_o2 / 100) * _pr_Z('O2', P_abs, T_K)
    if ex_n2: z_fb += (ex_n2 / 100) * _pr_Z('N2', P_abs, T_K)
    if ex_he: z_fb += (ex_he / 100) * (1.0 + _GAS_PROPS['He']['B'] * P_abs / (R_BAR * T_K))
    rho_fb = (P_abs * 1e5) * M_mix / (z_fb * R * T_K) / 1000   # kg/L

    gerg_z   = f"{z_gerg:.4f}"   if z_gerg   is not None else "   N/A"
    gerg_rho = f"{rho_gerg:.4f}" if rho_gerg is not None else "   N/A"

    active   = "GERG-2008 (CoolProp)" if _COOLPROP_AVAILABLE else "PR/virial"
    fallback = "PR/virial (fallback)"
    print(f"  {'Aktiv EOS':<32} {active}")
    print(f"  {'Fallback EOS':<32} {fallback}")
    print(f"{thin}")
    print(f"  EOS-sjekk: {gas_label(ex_o2, ex_he)},  {ex_P} barg,  {ex_T} °C")
    print(f"{thin}")
    print(f"  {'':32} {'GERG-2008':>10}  {'PR/virial':>10}")
    print(f"{thin}")
    print(f"  {'Z-faktor [-]':<32} {gerg_z:>10}  {z_fb:>10.4f}")
    print(f"  {'Tetthet [kg/L]':<32} {gerg_rho:>10}  {rho_fb:>10.4f}")
    print(f"{thick}\n")
