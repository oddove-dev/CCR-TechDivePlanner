"""
Chart demo — 4 visualisations for the CCR dive planner.
Run standalone: python chart_demo.py
"""

import sys
import numpy as np
import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from matplotlib.patches import FancyArrowPatch
import matplotlib.patheffects as pe

# ── Realistic sample data ────────────────────────────────────────────────────

# Dive profile waypoints: (time_min, depth_m)
# 40m dive, 30 min bottom, deco stops at 15/12/9/6/3m
PROFILE_CCR = [
    (0,   0),
    (2,  40),   # descent 20 m/min
    (30, 40),   # bottom
    (32, 15),   # fast ascent 9 m/min (to first stop)
    (34, 15),   # stop 2 min
    (37, 12),   # 3m ascent deco rate
    (39, 12),   # stop 2 min
    (43,  9),
    (47,  9),   # stop 4 min
    (53,  6),
    (61,  6),   # stop 8 min
    (73,  3),
    (85,  3),   # stop 12 min
    (89,  0),   # surface
]

PROFILE_BAILOUT = [
    (0,   0),
    (2,  40),
    (30, 40),   # switch to OC at bottom
    (33, 15),
    (35, 15),
    (38, 12),
    (41, 12),
    (46,  9),
    (51,  9),
    (58,  6),
    (68,  6),
    (82,  3),
    (96,  3),
    (100, 0),
]

# Setpoint changes (time, sp)
SP_CHANGES = [
    (0,   0.70),  # descent
    (2,   1.30),  # bottom
    (32,  1.60),  # deco
]

# 16 Bühlmann half-times
HALFTIMES = [5, 8, 12.5, 18.5, 27, 38.3, 54.3, 77, 109, 146, 187, 239, 305, 390, 498, 635]

# Simulated tissue saturation over time (simplified)
def _tissue_sat_over_time(profile, n_tissues=16):
    times = [p[0] for p in profile]
    depths = [p[1] for p in profile]
    t_end = times[-1]
    t_grid = np.linspace(0, t_end, 300)

    d_interp = np.interp(t_grid, times, depths)
    amps = [p * 0.79 * 0.1 + 1.0 for p in d_interp]  # ambient N2 partial pressure

    result = []
    for ht in HALFTIMES:
        sat = np.zeros(len(t_grid))
        sat[0] = 0.79  # surface N2
        k = np.log(2) / ht
        for i in range(1, len(t_grid)):
            dt = t_grid[i] - t_grid[i - 1]
            sat[i] = amps[i] + (sat[i-1] - amps[i]) * np.exp(-k * dt)
        result.append(sat)
    return t_grid, result

t_ccr, sats_ccr = _tissue_sat_over_time(PROFILE_CCR)

# Gas consumption (realistic example)
GAS_DATA = {
    "Diluent\n11/70": {"used": 85,  "capacity": 489, "color": "#4a90d9"},
    "O₂":             {"used": 50,  "capacity": 150, "color": "#e84040"},
    "Inflation":      {"used": 30,  "capacity": 200, "color": "#7bc47b"},
    "BCD":            {"used": 14,  "capacity": 200, "color": "#f0a030"},
}

# ── Figure setup ─────────────────────────────────────────────────────────────

fig = plt.figure(figsize=(16, 9), facecolor="#1e1e2e")
fig.suptitle("CCR TechDivePlanner — Chart examples   (40 m / 30 min  |  CCR 11/70  |  SP 0.7→1.3→1.6  |  GF 55/70)",
             color="white", fontsize=13, fontweight="bold", y=0.98)

gs = GridSpec(2, 2, figure=fig, hspace=0.42, wspace=0.32,
              left=0.06, right=0.97, top=0.93, bottom=0.07)

AX_PROFILE  = fig.add_subplot(gs[0, 0])
AX_PO2      = fig.add_subplot(gs[0, 1])
AX_TISSUE   = fig.add_subplot(gs[1, 0])
AX_GAS      = fig.add_subplot(gs[1, 1])

PANEL_BG    = "#2a2a3e"
GRID_COLOR  = "#44445a"
TEXT_COLOR  = "#ddddee"

for ax in [AX_PROFILE, AX_PO2, AX_TISSUE, AX_GAS]:
    ax.set_facecolor(PANEL_BG)
    ax.tick_params(colors=TEXT_COLOR, labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(GRID_COLOR)

# ── Chart 1: Dive Profile ─────────────────────────────────────────────────────

ax = AX_PROFILE
ax.set_title("1 — Dykkeprofil  (CCR vs Bailout)", color=TEXT_COLOR, fontsize=10, pad=6)

t_c = [p[0] for p in PROFILE_CCR]
d_c = [p[1] for p in PROFILE_CCR]
t_b = [p[0] for p in PROFILE_BAILOUT]
d_b = [p[1] for p in PROFILE_BAILOUT]

# Fill under CCR curve
ax.fill_between(t_c, d_c, alpha=0.18, color="#4a90d9")
ax.plot(t_c, d_c, color="#6abaff", linewidth=2.2, label="CCR", zorder=3)
ax.plot(t_b, d_b, color="#ffaa44", linewidth=1.5, linestyle="--", label="Bailout OC", zorder=3, alpha=0.85)

# Stop markers on CCR
stop_depths = [15, 12, 9, 6, 3]
stop_colors = ["#aaddff", "#88ccff", "#66bbff", "#44aaff", "#2299ff"]
for depth, col in zip(stop_depths, stop_colors):
    pts = [(t, d) for t, d in zip(t_c, d_c) if d == depth]
    if len(pts) >= 2:
        t0, t1 = pts[0][0], pts[-1][0]
        ax.hlines(depth, t0, t1, colors=col, linewidth=4, alpha=0.7, zorder=4)
        ax.text(t1 + 0.5, depth, f"{depth}m", color=col, fontsize=7.5, va="center")

# Setpoint labels
sp_colors = {"0.70": "#ffdd44", "1.30": "#44ff88", "1.60": "#ff6666"}
for t_sp, sp in SP_CHANGES:
    d_at = float(np.interp(t_sp, t_c, d_c))
    col = sp_colors.get(f"{sp:.2f}", "white")
    ax.annotate(f"SP {sp}", xy=(t_sp, d_at), xytext=(t_sp + 1, d_at + 3),
                color=col, fontsize=7, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=col, lw=0.8))

ax.invert_yaxis()
ax.set_xlabel("Tid [min]", color=TEXT_COLOR, fontsize=8)
ax.set_ylabel("Dybde [m]", color=TEXT_COLOR, fontsize=8)
ax.grid(True, color=GRID_COLOR, linewidth=0.5, linestyle=":")
ax.legend(fontsize=8, facecolor=PANEL_BG, edgecolor=GRID_COLOR,
          labelcolor=TEXT_COLOR, loc="lower right")
ax.set_xlim(0, max(t_b) + 2)

# ── Chart 2: PO₂ profile ──────────────────────────────────────────────────────

ax = AX_PO2
ax.set_title("2 — PO₂-profil over tid", color=TEXT_COLOR, fontsize=10, pad=6)

t_fine = np.linspace(0, max(t_c), 500)
d_fine = np.interp(t_fine, t_c, d_c)

# CCR PO2: setpoint based
sp_vals = []
for t in t_fine:
    sp = 0.70
    for t_sp, s in SP_CHANGES:
        if t >= t_sp:
            sp = s
    sp_vals.append(sp)
sp_arr = np.array(sp_vals)

# Ambient PO2 on OC (bailout reference)
d_fine_b = np.interp(t_fine, t_b, [p[1] for p in PROFILE_BAILOUT])
po2_oc_dil = (d_fine_b / 10 + 1.0) * 0.11   # 11% O2 in diluent

# CNS limit
ax.axhline(1.6, color="#ff4444", linewidth=1.2, linestyle="-", alpha=0.7, label="CNS limit 1.6")
ax.axhline(1.4, color="#ffaa00", linewidth=0.8, linestyle="--", alpha=0.5, label="Caution 1.4")

# Fill between 0 and SP
ax.fill_between(t_fine, sp_arr, alpha=0.15, color="#44ff88")
ax.plot(t_fine, sp_arr, color="#44ff88", linewidth=2, label="CCR PO₂ (setpoint)")
ax.plot(t_fine, po2_oc_dil, color="#ffaa44", linewidth=1.4,
        linestyle="--", alpha=0.75, label="OC diluent PO₂ (bailout)")

# SP change annotations
for t_sp, sp in SP_CHANGES:
    ax.axvline(t_sp, color="#ffffff", linewidth=0.5, alpha=0.3)
    ax.text(t_sp + 0.3, sp + 0.04, f"SP→{sp}", color="white", fontsize=7, alpha=0.8)

ax.set_xlabel("Tid [min]", color=TEXT_COLOR, fontsize=8)
ax.set_ylabel("PO₂ [bar]", color=TEXT_COLOR, fontsize=8)
ax.set_ylim(0, 1.85)
ax.grid(True, color=GRID_COLOR, linewidth=0.5, linestyle=":")
ax.legend(fontsize=7.5, facecolor=PANEL_BG, edgecolor=GRID_COLOR, labelcolor=TEXT_COLOR)
ax.set_xlim(0, max(t_c) + 2)

# ── Chart 3: Tissue saturations ───────────────────────────────────────────────

ax = AX_TISSUE
ax.set_title("3 — Vevsmettning  (16 Bühlmann-vev)", color=TEXT_COLOR, fontsize=10, pad=6)

# Colour map: fast tissues = red, slow = blue
cmap = plt.cm.get_cmap("coolwarm", 16)
for i, (ht, sat) in enumerate(zip(HALFTIMES, sats_ccr)):
    col = cmap(i / 15)
    alpha = 0.55 if i not in (0, 4, 8, 12, 15) else 0.95
    lw = 1.0 if i not in (0, 4, 8, 12, 15) else 1.8
    ax.plot(t_ccr, sat, color=col, linewidth=lw, alpha=alpha)

# M-value reference line (simplified: 3.0 bar at surface)
ax.axhline(3.0, color="#ff4444", linewidth=1.0, linestyle="--", alpha=0.6, label="M-value ref (surf)")
ax.axhline(0.79, color="#aaaaff", linewidth=0.8, linestyle=":", alpha=0.5, label="Surface N₂")

# Colorbar for half-times
sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(vmin=5, vmax=635))
sm.set_array([])
cbar = fig.colorbar(sm, ax=ax, pad=0.01, fraction=0.03)
cbar.set_label("t½ [min]", color=TEXT_COLOR, fontsize=7)
cbar.ax.tick_params(colors=TEXT_COLOR, labelsize=7)
cbar.outline.set_edgecolor(GRID_COLOR)

ax.set_xlabel("Tid [min]", color=TEXT_COLOR, fontsize=8)
ax.set_ylabel("PN₂ vev [bar]", color=TEXT_COLOR, fontsize=8)
ax.grid(True, color=GRID_COLOR, linewidth=0.5, linestyle=":")
ax.legend(fontsize=7.5, facecolor=PANEL_BG, edgecolor=GRID_COLOR, labelcolor=TEXT_COLOR)
ax.set_xlim(0, max(t_ccr))

# ── Chart 4: Gas consumption ──────────────────────────────────────────────────

ax = AX_GAS
ax.set_title("4 — Gassforbruk  (faktisk vs kapasitet)", color=TEXT_COLOR, fontsize=10, pad=6)

names = list(GAS_DATA.keys())
used  = [GAS_DATA[k]["used"]     for k in names]
cap   = [GAS_DATA[k]["capacity"] for k in names]
cols  = [GAS_DATA[k]["color"]    for k in names]

x = np.arange(len(names))
bar_w = 0.38

# Capacity bars (background)
bars_cap = ax.bar(x, cap, width=bar_w * 2 + 0.05, color="#33334a",
                  edgecolor=GRID_COLOR, linewidth=0.6, zorder=2)
# Used bars
bars_used = ax.bar(x, used, width=bar_w * 2 + 0.05,
                   color=cols, edgecolor="none", alpha=0.85, zorder=3)

# Percentage labels
for xi, (u, c, col) in enumerate(zip(used, cap, cols)):
    pct = u / c * 100
    ax.text(xi, u + 4, f"{pct:.0f}%", ha="center", va="bottom",
            color=col, fontsize=9, fontweight="bold")
    ax.text(xi, -12, f"{u} / {c} L", ha="center", va="top",
            color=TEXT_COLOR, fontsize=7.5, alpha=0.8)

# Reserve line at 50 bar equiv (rough)
ax.set_xticks(x)
ax.set_xticklabels(names, color=TEXT_COLOR, fontsize=9)
ax.set_ylabel("Gass [L]", color=TEXT_COLOR, fontsize=8)
ax.set_ylim(-25, max(cap) * 1.18)
ax.grid(True, axis="y", color=GRID_COLOR, linewidth=0.5, linestyle=":")

used_patch = mpatches.Patch(color="#aaaaaa", alpha=0.85, label="Brukt")
cap_patch  = mpatches.Patch(color="#33334a", edgecolor=GRID_COLOR, label="Kapasitet")
ax.legend(handles=[used_patch, cap_patch], fontsize=8,
          facecolor=PANEL_BG, edgecolor=GRID_COLOR, labelcolor=TEXT_COLOR)

# ── Show ─────────────────────────────────────────────────────────────────────

plt.show()
