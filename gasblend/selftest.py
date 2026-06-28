"""Quick self-test for the gasblend engine.
Run from repo root:  python -m gasblend.selftest  (or: python selftest.py)
"""
from gasblend import Blender, CylinderState, get_eos, limits

def main():
    bl = Blender(get_eos("gerg"))
    print(f"=== {bl.eos.name} ===")
    for desc, st, o2, he, P in [
        ("empty -> Tx18/45@200", CylinderState(24.0, 0.0), .18, .45, 200),
        ("top-off Tx18/45 70->200", CylinderState(24.0, 70, {"O2":.18,"He":.45,"N2":.37}), .18, .45, 200),
        ("empty -> Heliox16/84@200", CylinderState(24.0, 0.0), .16, .84, 200),
        ("empty -> EAN32@200", CylinderState(11.1, 0.0), .32, 0.0, 200),
    ]:
        r = bl.blend(st, o2, he, P)
        seq = "  ".join(f"{s.gas}->{s.to_pressure:.0f}" for s in r.steps)
        ok = "OK" if r.feasible and not r.warnings else "!"+";".join(r.warnings)[:30]
        print(f"  {desc:26s}: {seq:40s} [{ok}]")
    m = {"O2":.18,"He":.45,"N2":.37}
    print(f"\nLimits Tx18/45: MOD@1.4={limits.mod(m,1.4):.0f}m  "
          f"END@60={limits.end(m,60):.0f}m  ppO2@60={limits.ppO2(m,60):.2f}")

if __name__ == "__main__":
    main()
