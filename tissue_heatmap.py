"""Tissue saturation heatmap popup for the Dive Planner.

Tabs
----
1. Heatmap (time × compartment)   – saturation fraction, colour-coded
2. Classic grid (time × T1–T16)   – numeric % in cells
3. Leading compartment            – which tissue is closest to M-value over time
4. GF corridor                    – leading sat vs GF envelope line
5. N2 / He split heatmap          – separate N2 and He contribution per tissue
6. Off-gassing rate               – Δ(pt)/Δt: loading (red) vs off-gassing (blue)
7. Animated radar                 – 16-arm spider chart animated through timeline
8. Animated bar chart             – horizontal bars animated through timeline
9. Phase bands                    – heatmap overlaid with dive-phase colour bands
"""
from __future__ import annotations
import math
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QTabWidget, QSlider, QLineEdit,
    QComboBox, QFrame, QGridLayout
)
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QPolygonF
from PyQt6.QtCore import QPointF

try:
    from buhlmann import TISSUES, P_SURF, WATER_DENSITY
except ImportError:
    P_SURF        = 1.013
    WATER_DENSITY = 1.025
    TISSUES       = []

# N2 half-times for compartment labels (ZHL-16C)
_N2_HT = [4, 8, 12.5, 18.5, 27, 38.3, 54.3, 77, 109, 146, 187, 239, 305, 390, 498, 635]

# Phase colours (RGBA)
_PHASE_COLORS = {
    "surface":  QColor(60,  60,  60,  80),
    "transit":  QColor(30,  80, 160,  70),
    "bottom":   QColor(20, 120,  40,  70),
    "bailout":  QColor(160,  60,  20,  70),
    "deco":     QColor(140, 100,   0,  70),
}


# ── Core helpers ──────────────────────────────────────────────────────────────

def _p_amb(depth: float) -> float:
    return P_SURF + depth * WATER_DENSITY / 10.0


def _sat_fractions(snap: list, depth: float, surface_mv: bool) -> list[float]:
    """Saturation fraction pt/M-value for each of 16 compartments."""
    p_amb = P_SURF if surface_mv else _p_amb(depth)
    fracs = []
    for i, (p_n2, p_he) in enumerate(snap):
        if i >= len(TISSUES):
            fracs.append(0.0)
            continue
        _, N2_a, N2_b, _, He_a, He_b = TISSUES[i]
        pt = p_n2 + p_he
        if pt > 0:
            a = (N2_a * p_n2 + He_a * p_he) / pt
            b = (N2_b * p_n2 + He_b * p_he) / pt
        else:
            a, b = N2_a, N2_b
        mv = a + p_amb / b
        fracs.append(pt / mv if mv > 0 else 0.0)
    return fracs


def _norm_fractions(snap: list, depth: float) -> list[float]:
    """Normalised saturation: 0% = P_amb, 100% = depth M-value."""
    p_amb = _p_amb(depth)
    fracs = []
    for i, (p_n2, p_he) in enumerate(snap):
        if i >= len(TISSUES):
            fracs.append(0.0)
            continue
        _, N2_a, N2_b, _, He_a, He_b = TISSUES[i]
        pt = p_n2 + p_he
        if pt > 0:
            a = (N2_a * p_n2 + He_a * p_he) / pt
            b = (N2_b * p_n2 + He_b * p_he) / pt
        else:
            a, b = N2_a, N2_b
        mv = a + p_amb / b
        denom = mv - p_amb
        fracs.append((pt - p_amb) / denom if denom > 0 else 0.0)
    return fracs


def _mv(i: int, snap_i: tuple, depth: float, surface_mv: bool) -> float:
    """M-value for compartment i given ambient conditions."""
    p_amb = P_SURF if surface_mv else _p_amb(depth)
    p_n2, p_he = snap_i
    pt = p_n2 + p_he
    _, N2_a, N2_b, _, He_a, He_b = TISSUES[i]
    if pt > 0:
        a = (N2_a * p_n2 + He_a * p_he) / pt
        b = (N2_b * p_n2 + He_b * p_he) / pt
    else:
        a, b = N2_a, N2_b
    return a + p_amb / b


def _nice_tick(t_max: float) -> float:
    for step in (5, 10, 15, 20, 30, 60, 120):
        if t_max / step <= 12:
            return float(step)
    return 60.0


# ── Colour helpers ────────────────────────────────────────────────────────────

def _frac_color_new(f: float) -> QColor:
    """Smooth gradient: blue → green → yellow → orange → red → purple → white."""
    stops = [
        (0.00, (40,  80, 200)),
        (0.50, (40, 180,  60)),
        (0.75, (220, 220,  0)),
        (0.90, (255, 140,  0)),
        (1.00, (220,  30,  30)),
        (1.50, (140,   0, 140)),
        (2.00, (180,   0, 180)),
        (3.00, (255, 200, 255)),
    ]
    f = max(0.0, f)
    for j in range(len(stops) - 1):
        f0, c0 = stops[j]
        f1, c1 = stops[j + 1]
        if f <= f1:
            t = (f - f0) / (f1 - f0) if f1 > f0 else 0.0
            return QColor(int(c0[0] + t * (c1[0] - c0[0])),
                          int(c0[1] + t * (c1[1] - c0[1])),
                          int(c0[2] + t * (c1[2] - c0[2])))
    return QColor(140, 0, 140)


def _frac_color_classic(f: float) -> QColor:
    """Discrete stops matching the old Tkinter GUI palette."""
    if f <= 0:    return QColor(26,  58,  26)
    if f < 0.50:  return QColor(26,  74,  26)
    if f < 0.75:  return QColor(74, 106,   0)
    if f < 0.90:  return QColor(138, 106,   0)
    if f < 1.00:  return QColor(170,  51,   0)
    return            QColor(204,  17,   0)


def _rate_color(rate: float) -> QColor:
    """Legacy absolute-scale version (kept for other callers)."""
    SCALE = 0.04
    norm = max(-1.0, min(1.0, rate / SCALE))
    return _rate_color_norm(norm)


def _rate_color_norm(t: float) -> QColor:
    """
    Diverging colour scale, t in [-1, 1]:
      +1 (max loading)   : bright yellow-white
       0 (zero change)   : dark grey
      -1 (max off-gas)   : bright cyan-white
    """
    if t > 0:
        if t < 0.5:
            s = t * 2
            return QColor(int(120 + 100*s), int(40 + 80*s), 20)
        else:
            s = (t - 0.5) * 2
            return QColor(int(220 + 35*s), int(120 + 100*s), int(20 + 180*s))
    elif t < 0:
        t = abs(t)
        if t < 0.5:
            s = t * 2
            return QColor(10, int(60 + 80*s), int(100 + 100*s))
        else:
            s = (t - 0.5) * 2
            return QColor(int(10 + 180*s), int(140 + 80*s), int(200 + 55*s))
    return QColor(55, 55, 60)


# ── Tab 1: New-style heatmap (time on X, compartments on Y) ──────────────────

class _HeatmapWidget(QWidget):
    PAD_L   = 52
    PAD_R   = 16
    PAD_T   = 8
    DEPTH_H = 28
    ROW_H   = 22
    PAD_B   = 72   # extra room for time axis + legend bar + labels

    def __init__(self, timeline: list, surface_mv: bool, stops: list = None,
                 phase_list: list = None, parent=None):
        super().__init__(parent)
        self._timeline   = timeline
        self._surface_mv = surface_mv
        self._stops      = stops or []
        self._phase_list = phase_list or []
        self._precompute()
        h = self.PAD_T + self.DEPTH_H + 4 + 16 * self.ROW_H + self.PAD_B
        self.setMinimumHeight(h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _precompute(self):
        tl = self._timeline
        self._times  = [t[0] for t in tl]
        self._depths = [t[1] for t in tl]
        self._fracs  = [_sat_fractions(t[2], t[1], self._surface_mv) for t in tl]
        self._max_depth = max(self._depths) if self._depths else 1.0
        self._t_max     = self._times[-1]   if self._times   else 1.0

    def sizeHint(self) -> QSize:
        return QSize(self.PAD_L + max(400, len(self._times) * 4) + self.PAD_R,
                     self.PAD_T + self.DEPTH_H + 4 + 16 * self.ROW_H + self.PAD_B)

    def paintEvent(self, _event):
        if not self._times:
            return
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        W  = self.width()
        gw = W - self.PAD_L - self.PAD_R
        gh = 16 * self.ROW_H
        n  = len(self._times)
        cw = gw / n if n > 0 else 1

        dp_top = self.PAD_T
        for i, d in enumerate(self._depths):
            x = int(self.PAD_L + i * cw)
            ratio = d / self._max_depth if self._max_depth > 0 else 0
            p.fillRect(x, dp_top, max(1, int(cw) + 1), self.DEPTH_H,
                       QColor(0, 50, int(30 + 180 * ratio)))

        hm_top = dp_top + self.DEPTH_H + 4
        for i in range(n):
            x = int(self.PAD_L + i * cw)
            w = max(1, int(cw) + 1)
            for comp in range(16):
                f = self._fracs[i][comp] if comp < len(self._fracs[i]) else 0.0
                p.fillRect(x, hm_top + comp * self.ROW_H, w, self.ROW_H,
                           _frac_color_new(f))

        p.setPen(Qt.GlobalColor.black)
        p.setFont(QFont("Arial", 7))
        for comp in range(16):
            p.drawText(0, hm_top + comp * self.ROW_H, self.PAD_L - 2, self.ROW_H,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"C{comp+1} {_N2_HT[comp]}m")

        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for comp in range(1, 16):
            y = hm_top + comp * self.ROW_H
            p.drawLine(self.PAD_L, y, W - self.PAD_R, y)

        p.setPen(Qt.GlobalColor.black)
        p.setFont(QFont("Arial", 8))
        step = _nice_tick(self._t_max)
        t = 0.0
        while t <= self._t_max + 0.5:
            x = int(self.PAD_L + (t / self._t_max) * gw)
            p.drawLine(x, hm_top + gh, x, hm_top + gh + 4)
            p.drawText(x - 16, hm_top + gh + 6, 32, 16,
                       Qt.AlignmentFlag.AlignCenter, f"{t:.0f}")
            t += step
        p.drawText(self.PAD_L, hm_top + gh + 18, gw, 14,
                   Qt.AlignmentFlag.AlignCenter, "Runtime [min]")

        if self._stops and self._t_max > 0:
            prev_x = -999
            for stop in self._stops:
                rt    = stop.get("runtime", 0)
                depth = stop.get("depth", 0)
                x = int(self.PAD_L + (rt / self._t_max) * gw)
                pen = QPen(QColor(255, 255, 255, 110), 1, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(x, hm_top, x, hm_top + gh)
                if x - prev_x > 22:
                    p.setPen(QColor(255, 230, 80))
                    p.setFont(QFont("Arial", 7))
                    p.drawText(x - 14, self.PAD_T + self.DEPTH_H - 13, 28, 12,
                               Qt.AlignmentFlag.AlignCenter, f"{depth:.0f}m")
                    prev_x = x

        # ── Inline legend bar (drawn at bottom of widget, inside scroll area) ──
        leg_y   = hm_top + gh + 40
        bx      = self.PAD_L
        bw      = gw
        bh      = 16
        leg_max = 3.0 if self._surface_mv else 1.0
        steps   = 300
        for i in range(steps):
            f  = i / (steps - 1) * leg_max
            lx = bx + int(i / steps * bw)
            p.fillRect(lx, leg_y, max(1, bw // steps + 1), bh, _frac_color_new(f))
        p.setPen(Qt.GlobalColor.black)
        p.drawRect(bx, leg_y, bw, bh)
        p.setFont(QFont("Arial", 9))
        p.drawText(0, leg_y, bx - 4, bh,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "0%")
        if self._surface_mv:
            tick_vals = (0.25, 0.50, 0.75, 1.0, 1.5, 2.0, 3.0)
        else:
            tick_vals = (0.25, 0.50, 0.75, 0.90, 1.0)
        tick_fracs = [(f"{int(v*100)}%", v/leg_max) for v in tick_vals if v <= leg_max]
        for lbl, pos in tick_fracs:
            lx = bx + int(pos * bw)
            p.drawLine(lx, leg_y + bh, lx, leg_y + bh + 4)
            p.drawText(lx - 22, leg_y + bh + 5, 44, 12,
                       Qt.AlignmentFlag.AlignCenter, lbl)

        # Phase transition labels in the depth strip
        # Colours: descent=cyan, bottom=lime green, ascent=orange
        _phase_col = {
            "transit": QColor(100, 220, 255),   # cyan — descent/ascent transit
            "bottom":  QColor(100, 255, 120),   # lime green — bottom
            "bailout": QColor(255, 140,  60),   # orange — bailout ascent
            "deco":    QColor(255, 230,  80),   # yellow — deco (same as stops)
        }
        if self._phase_list and self._t_max > 0:
            prev_px = -999
            prev_phase = None
            for entry in self._phase_list:
                rt, depth, label = entry[0], entry[1], entry[2]
                if label == "surface":
                    prev_phase = label
                    continue
                col = _phase_col.get(label, QColor(200, 200, 200))
                x = int(self.PAD_L + (rt / self._t_max) * gw)
                if x - prev_px > 28 and label != prev_phase:
                    p.setPen(col)
                    p.setFont(QFont("Arial", 7))
                    p.drawText(x - 14, self.PAD_T + 2, 28, 12,
                               Qt.AlignmentFlag.AlignCenter, f"{depth:.0f}m")
                    prev_px = x
                prev_phase = label
        p.end()


# ── Tab 2: Classic grid (compartments on Y, time on X) ───────────────────────

class _ClassicHeatmapWidget(QWidget):
    """
    Transposed layout matching the saturation heatmap orientation:
    - X axis : time (one column per snapshot)
    - Y axis : 16 tissue compartments (rows), labelled on the left
    - Top strip : depth colour band (dark→bright blue)
    - Cells show saturation % of M-value as a number
    - Deco stop markers as vertical dashed lines
    """
    PAD_L   = 56   # compartment label area
    PAD_R   = 4
    DEPTH_H = 22   # depth strip height
    HDR_H   = 18   # time-axis tick area at bottom
    ROW_H   = 18   # height of each compartment row

    def __init__(self, timeline: list, surface_mv: bool, stops: list = None, parent=None):
        super().__init__(parent)
        self._surface_mv = surface_mv
        self._stops      = stops or []
        self._precompute(timeline)
        total_h = self.DEPTH_H + 16 * self.ROW_H + self.HDR_H + 4
        self.setMinimumHeight(total_h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _precompute(self, timeline):
        self._cols = []   # list of (runtime, depth, fracs[16])
        for entry in timeline:
            rt, depth, snap = entry[0], entry[1], entry[2]
            fracs = _sat_fractions(snap, depth, self._surface_mv)
            self._cols.append((rt, depth, fracs))
        self._max_depth = max((c[1] for c in self._cols), default=1.0)
        self._t_max     = self._cols[-1][0] if self._cols else 1.0

    def paintEvent(self, _event):
        if not self._cols:
            return
        p    = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        n    = len(self._cols)
        gw   = self.width() - self.PAD_L - self.PAD_R
        cw   = gw / n if n > 0 else 1
        fnt_lbl = QFont("Arial", 7)
        fnt_cel = QFont("Arial", 6)

        # ── depth strip ──
        for i, (rt, depth, _) in enumerate(self._cols):
            x     = int(self.PAD_L + i * cw)
            w     = max(1, int(cw) + 1)
            ratio = depth / self._max_depth if self._max_depth > 0 else 0
            p.fillRect(x, 0, w, self.DEPTH_H,
                       QColor(0, 50, int(30 + 180 * ratio)))
        p.setPen(QColor(200, 220, 255))
        p.setFont(fnt_lbl)
        p.drawText(self.PAD_L + 16, 0, gw, self.DEPTH_H,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"Depth  (max {self._max_depth:.0f} m)")

        # ── compartment rows ──
        for comp in range(16):
            y = self.DEPTH_H + comp * self.ROW_H
            p.fillRect(0, y, self.PAD_L - 2, self.ROW_H, QColor(22, 27, 34))
            p.setPen(QColor(180, 180, 180))
            p.setFont(fnt_lbl)
            p.drawText(2, y, self.PAD_L - 4, self.ROW_H,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"T{comp+1}  {_N2_HT[comp]}m")
            p.setFont(fnt_cel)
            for i, (rt, depth, fracs) in enumerate(self._cols):
                f  = fracs[comp] if comp < len(fracs) else 0.0
                x  = int(self.PAD_L + i * cw)
                w  = max(1, int(cw) + 1)
                p.fillRect(x, y + 1, w, self.ROW_H - 1, _frac_color_classic(f))
                if cw >= 14:
                    pct     = int(f * 100)
                    txt_col = QColor(255, 255, 255) if f >= 0.75 else QColor(180, 180, 180)
                    p.setPen(txt_col)
                    p.drawText(x, y + 1, w, self.ROW_H - 1,
                               Qt.AlignmentFlag.AlignCenter, str(pct))

        # ── horizontal grid lines between compartments ──
        p.setPen(QPen(QColor(0, 0, 0, 80), 1))
        for comp in range(1, 16):
            y = self.DEPTH_H + comp * self.ROW_H
            p.drawLine(self.PAD_L, y, self.PAD_L + gw, y)

        # ── time axis ──
        t_axis_y = self.DEPTH_H + 16 * self.ROW_H
        p.setPen(QColor(140, 140, 140))
        p.setFont(fnt_lbl)
        step = _nice_tick(self._t_max)
        t    = 0.0
        while t <= self._t_max + 0.5:
            x = self.PAD_L + int(t / self._t_max * gw)
            p.drawLine(x, t_axis_y, x, t_axis_y + 4)
            p.drawText(x - 14, t_axis_y + 4, 28, 14,
                       Qt.AlignmentFlag.AlignCenter, f"{t:.0f}")
            t += step
        p.drawText(self.PAD_L, t_axis_y + 4, gw, 14,
                   Qt.AlignmentFlag.AlignCenter, "Runtime [min]")

        # ── deco stop markers ──
        if self._stops and self._t_max > 0:
            prev_x = -999
            for stop in self._stops:
                rt    = stop.get("runtime", 0)
                depth = stop.get("depth", 0)
                x     = self.PAD_L + int(rt / self._t_max * gw)
                p.setPen(QPen(QColor(255, 255, 255, 110), 1, Qt.PenStyle.DashLine))
                p.drawLine(x, self.DEPTH_H, x, t_axis_y)
                if x - prev_x > 22:
                    p.setPen(QColor(255, 230, 80))
                    p.setFont(QFont("Arial", 7))
                    p.drawText(x - 14, 4, 28, self.DEPTH_H - 8,
                               Qt.AlignmentFlag.AlignCenter, f"{depth:.0f}m")
                    prev_x = x
        p.end()


# ── Tab 3: Leading compartment tracker ───────────────────────────────────────

def _leading_precompute(tl, surface_mv):
    """Shared precompute for both leading pane widgets."""
    times   = [e[0] for e in tl]
    depths  = [e[1] for e in tl]
    leading = []
    for e in tl:
        fracs = _sat_fractions(e[2], e[1], surface_mv)
        best  = max(range(16), key=lambda i: fracs[i])
        leading.append((best, fracs[best]))
    t_max = times[-1] if times else 1.0
    return times, depths, leading, t_max


def _draw_stop_markers(p, stops, t_max, pad_l, gw, y_top, y_bot):
    """Draw deco stop vertical lines with depth/runtime labels."""
    if not stops or t_max <= 0:
        return
    prev_x = -999
    for stop in stops:
        rt    = stop.get("runtime", 0)
        depth = stop.get("depth", 0)
        x = pad_l + int(rt / t_max * gw)
        p.setPen(QPen(QColor(255, 230, 80, 120), 1, Qt.PenStyle.DashLine))
        p.drawLine(x, y_top, x, y_bot)
        if x - prev_x > 24:
            p.setPen(QColor(255, 230, 80))
            p.setFont(QFont("Arial", 7))
            p.drawText(x - 14, y_top + 2, 28, 11,
                       Qt.AlignmentFlag.AlignCenter, f"{depth:.0f}m")
            p.drawText(x - 16, y_bot + 2, 32, 11,
                       Qt.AlignmentFlag.AlignCenter, f"{rt:.0f}'")
            prev_x = x


def _draw_time_axis_fn(p, pad_l, gw, t_max, y_base):
    p.setPen(QColor(160, 160, 160))
    p.setFont(QFont("Arial", 8))
    step = _nice_tick(t_max)
    t = 0.0
    while t <= t_max + 0.5:
        x = pad_l + int(t / t_max * gw)
        p.drawLine(x, y_base, x, y_base + 4)
        p.drawText(x - 16, y_base + 6, 32, 14,
                   Qt.AlignmentFlag.AlignCenter, f"{t:.0f}")
        t += step
    p.drawText(pad_l, y_base + 20, gw, 14,
               Qt.AlignmentFlag.AlignCenter, "Runtime [min]")


class _LeadingTopWidget(QWidget):
    """Top pane: leading compartment index over time."""
    PAD_L = 52; PAD_R = 20; PAD_T = 24; PAD_B = 8

    def __init__(self, timeline, surface_mv, stops=None, parent=None):
        super().__init__(parent)
        self._stops = stops or []
        self._times, self._depths, self._leading, self._t_max = \
            _leading_precompute(timeline, surface_mv)
        self.setMinimumSize(500, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _event):
        if not self._times:
            return
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        gw   = W - self.PAD_L - self.PAD_R
        ph   = H - self.PAD_T - self.PAD_B

        def tx(t):
            return self.PAD_L + int(t / self._t_max * gw)

        p.fillRect(self.PAD_L, self.PAD_T, gw, ph, QColor(22, 27, 34))

        # horizontal grid lines
        p.setPen(QColor(60, 60, 80))
        for row in range(1, 17):
            y = self.PAD_T + ph - int(row / 16 * ph)
            p.drawLine(self.PAD_L, y, self.PAD_L + gw, y)

        # leading compartment line
        pts = []
        for i, t in enumerate(self._times):
            comp = self._leading[i][0]
            pts.append((tx(t), self.PAD_T + ph - int((comp + 1) / 16 * ph)))
        p.setPen(QPen(QColor(80, 180, 255), 2))
        for i in range(len(pts) - 1):
            p.drawLine(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])

        # label column
        p.fillRect(0, self.PAD_T, self.PAD_L - 2, ph, QColor(220, 220, 220))
        p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        p.setPen(QColor(0, 0, 0))
        for ci in range(1, 17):
            y = self.PAD_T + ph - int(ci / 16 * ph)
            p.drawText(2, y - 6, self.PAD_L - 4, 12,
                       Qt.AlignmentFlag.AlignRight, f"C{ci}")

        # title
        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        p.drawText(self.PAD_L + 16, self.PAD_T + 18, "Leading compartment")

        # deco stop markers (depth label only, no runtime at bottom)
        if self._stops and self._t_max > 0:
            prev_x = -999
            for stop in self._stops:
                rt    = stop.get("runtime", 0)
                depth = stop.get("depth", 0)
                x = tx(rt)
                p.setPen(QPen(QColor(255, 230, 80, 120), 1, Qt.PenStyle.DashLine))
                p.drawLine(x, self.PAD_T, x, self.PAD_T + ph)
                if x - prev_x > 24:
                    p.setPen(QColor(255, 230, 80))
                    p.setFont(QFont("Arial", 7))
                    p.drawText(x - 14, self.PAD_T + 2, 28, 11,
                               Qt.AlignmentFlag.AlignCenter, f"{depth:.0f}m")
                    prev_x = x
        p.end()


class _LeadingBottomWidget(QWidget):
    """Bottom pane: leading compartment saturation fraction + GF corridor."""
    PAD_L = 52; PAD_R = 60; PAD_T = 24; PAD_B = 36

    def __init__(self, timeline, surface_mv, stops=None,
                 gf_low=0.30, gf_high=0.80, first_stop=0.0,
                 first_stop_rt=None, parent=None):
        super().__init__(parent)
        self._stops      = stops or []
        self._gf_low     = gf_low
        self._gf_high    = gf_high
        self._first_stop = first_stop
        self._surface_mv = surface_mv
        self._times, self._depths, self._leading, self._t_max = \
            _leading_precompute(timeline, surface_mv)
        if first_stop_rt is not None:
            self._first_stop_rt = first_stop_rt
        else:
            self._first_stop_rt = min(
                (s.get("runtime", 0) - s.get("time", 0) for s in self._stops),
                default=self._t_max
            ) if self._stops else self._t_max

        # Precompute per-timepoint: depth M-value, ambient pressure, and
        # equilibrium floor (P_amb/M_depth) for the leading compartment.
        self._lead_m_depth  = []   # depth M-value of leading compartment
        self._lead_p_amb    = []   # ambient pressure at that depth
        self._lead_equil    = []   # P_amb/M_depth — equilibrium floor fraction
        for idx, (rt, depth, snap, *_) in enumerate(timeline):
            lead_i = self._leading[idx][0] if idx < len(self._leading) else 0
            m_d = _mv(lead_i, snap[lead_i], depth, False)
            p_a = _p_amb(depth)
            self._lead_m_depth.append(m_d)
            self._lead_p_amb.append(p_a)
            self._lead_equil.append(p_a / m_d if m_d > 0 else 0.0)

        self.setMinimumSize(500, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _gf_threshold_frac(self, i: int, gf: float) -> float:
        """
        The GF ceiling expressed as a fraction of the depth M-value for time
        index i.  The correct formula is:
            threshold = GF + P_amb × (1 − GF) / M_depth
        This is always ≥ GF, and is what the tissue saturation fraction must
        stay below during a correctly computed deco.
        """
        if i >= len(self._lead_m_depth):
            return gf
        M = self._lead_m_depth[i]
        if M <= 0:
            return gf
        return gf + self._lead_p_amb[i] * (1.0 - gf) / M

    def update_gf(self, gf_low, gf_high):
        self._gf_low  = gf_low
        self._gf_high = gf_high
        self.update()

    def paintEvent(self, _event):
        if not self._times:
            return
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        gw   = W - self.PAD_L - self.PAD_R
        ph   = H - self.PAD_T - self.PAD_B

        def tx(t):
            return self.PAD_L + int(t / self._t_max * gw)

        p.fillRect(self.PAD_L, self.PAD_T, gw, ph, QColor(22, 27, 34))

        # Compute dynamic Y max from actual data + GF thresholds
        max_sat = max((f for _, f in self._leading), default=1.0)
        max_thresh = 0.0
        for i in range(len(self._times)):
            d = self._depths[i]
            if self._first_stop > 0:
                ratio = max(0.0, min(1.0, d / self._first_stop))
                gf = self._gf_high + (self._gf_low - self._gf_high) * ratio
            else:
                gf = self._gf_high
            max_thresh = max(max_thresh, self._gf_threshold_frac(i, gf))
        y_max = max(1.2, max_sat * 1.05, max_thresh * 1.05)
        # round up to a nice value
        for nice in (1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
            if y_max <= nice:
                y_max = nice
                break
        else:
            y_max = math.ceil(y_max)

        def fy(val):
            return self.PAD_T + ph - int(min(val, y_max) / y_max * ph)

        # fixed reference lines
        ref_lines = [(1.0, "100%", QColor(220, 60, 60)),
                     (0.9, "90%",  QColor(200, 140, 0)),
                     (0.75, "75%", QColor(80, 160, 80))]
        if y_max > 1.5:
            ref_lines += [(1.5, "150%", QColor(160, 0, 160)),
                          (2.0, "200%", QColor(180, 0, 180))]
        if y_max > 2.5:
            ref_lines.append((3.0, "300%", QColor(200, 100, 200)))
        for ref, lbl, col in ref_lines:
            if ref > y_max:
                continue
            y = fy(ref)
            p.setPen(QPen(col, 1, Qt.PenStyle.DashLine))
            p.drawLine(self.PAD_L, y, self.PAD_L + gw, y)
            p.setPen(col)
            p.setFont(QFont("Arial", 7))
            p.drawText(self.PAD_L + gw + 2, y - 5, self.PAD_R - 4, 10,
                       Qt.AlignmentFlag.AlignLeft, lbl)

        # GF corridor
        gf_pts = []
        for i, t in enumerate(self._times):
            d = self._depths[i]
            if self._first_stop > 0:
                ratio = max(0.0, min(1.0, d / self._first_stop))
                gf = self._gf_high + (self._gf_low - self._gf_high) * ratio
            else:
                gf = self._gf_high
            threshold = self._gf_threshold_frac(i, gf)
            x = tx(t)
            y = fy(threshold)
            gf_pts.append((x, y, t >= self._first_stop_rt))

        # Shaded danger zone above the corridor (deco phase only)
        deco_pts = [QPointF(x, y) for x, y, is_deco in gf_pts if is_deco]
        if deco_pts:
            poly = QPolygonF()
            poly.append(QPointF(deco_pts[0].x(), self.PAD_T))
            poly.append(QPointF(deco_pts[-1].x(), self.PAD_T))
            for pt in reversed(deco_pts):
                poly.append(pt)
            p.setBrush(QColor(200, 60, 60, 40))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(poly)

        p.setPen(QPen(QColor(100, 100, 100), 1, Qt.PenStyle.DotLine))
        for i in range(len(gf_pts) - 1):
            if not gf_pts[i][2]:
                p.drawLine(gf_pts[i][0], gf_pts[i][1], gf_pts[i+1][0], gf_pts[i+1][1])
        p.setPen(QPen(QColor(255, 120, 60), 2, Qt.PenStyle.DashLine))
        for i in range(len(gf_pts) - 1):
            if gf_pts[i][2]:
                p.drawLine(gf_pts[i][0], gf_pts[i][1], gf_pts[i+1][0], gf_pts[i+1][1])
        if gf_pts:
            p.setPen(QColor(255, 120, 60))
            p.setFont(QFont("Arial", 7))
            p.drawText(self.PAD_L + gw + 2, gf_pts[-1][1] - 5, self.PAD_R - 4, 10,
                       Qt.AlignmentFlag.AlignLeft,
                       f"GF {int(self._gf_low*100)}/{int(self._gf_high*100)}")

        # equilibrium floor: P_amb/M_depth — tissue would reach this after
        # infinite bottom time.  Below this line = loading; above = off-gassing.
        p.setPen(QPen(QColor(80, 180, 255), 1, Qt.PenStyle.DotLine))
        for i in range(len(self._times) - 1):
            if i < len(self._lead_equil) and i+1 < len(self._lead_equil):
                x0 = tx(self._times[i])
                x1 = tx(self._times[i+1])
                y0 = fy(self._lead_equil[i])
                y1 = fy(self._lead_equil[i+1])
                p.drawLine(x0, y0, x1, y1)
        # label on right edge
        if self._lead_equil:
            p.setPen(QColor(80, 180, 255))
            p.setFont(QFont("Arial", 7))
            last_y = fy(self._lead_equil[-1])
            p.drawText(self.PAD_L + gw + 2, last_y - 5, self.PAD_R - 4, 10,
                       Qt.AlignmentFlag.AlignLeft, "equil")

        # leading saturation line (colour-coded)
        for i in range(len(self._times) - 1):
            f  = self._leading[i][1]
            x0 = tx(self._times[i])
            y0 = fy(f)
            f1 = self._leading[i+1][1]
            x1 = tx(self._times[i+1])
            y1 = fy(f1)
            p.setPen(QPen(_frac_color_new(f), 2))
            p.drawLine(x0, y0, x1, y1)

        # title
        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        p.drawText(self.PAD_L + 4, self.PAD_T + 18,
                   "Leading Compartment Saturation (pt / M-value)")

        # Y axis labels
        p.setPen(QColor(180, 180, 180))
        p.setFont(QFont("Arial", 7))
        tick_vals = [v for v in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0)
                     if v <= y_max]
        for ref in tick_vals:
            y = fy(ref)
            p.drawText(2, y - 6, self.PAD_L - 4, 12,
                       Qt.AlignmentFlag.AlignRight, f"{int(ref*100)}%")

        # time axis
        _draw_time_axis_fn(p, self.PAD_L, gw, self._t_max, self.PAD_T + ph)

        # deco stop markers (depth label at bottom)
        if self._stops and self._t_max > 0:
            prev_x = -999
            for stop in self._stops:
                rt    = stop.get("runtime", 0)
                depth = stop.get("depth", 0)
                x = tx(rt)
                p.setPen(QPen(QColor(255, 230, 80, 120), 1, Qt.PenStyle.DashLine))
                p.drawLine(x, self.PAD_T, x, self.PAD_T + ph)
                if x - prev_x > 24:
                    p.setPen(QColor(255, 255, 255))
                    p.setFont(QFont("Arial", 8, QFont.Weight.Bold))
                    p.drawText(x - 16, self.PAD_T + ph - 14, 32, 12,
                               Qt.AlignmentFlag.AlignCenter, f"{depth:.0f}m")
                    prev_x = x
        p.end()


# Keep old name as alias so nothing else breaks
class _LeadingCompartmentWidget(_LeadingTopWidget):
    pass


# ── Normalised leading-compartment chart ──────────────────────────────────────

class _LeadingNormWidget(QWidget):
    """
    Leading compartment saturation on a normalised scale:
        0%   = P_amb  (equilibrium — tissue exactly in balance with ambient gas)
        100% = M_depth (Bühlmann M-value at current depth)
    On this scale GF_low and GF_high map directly to those percentages, making
    the gradient-factor corridor immediately readable.
    """
    PAD_L = 52; PAD_R = 60; PAD_T = 24; PAD_B = 36

    def __init__(self, timeline, surface_mv, stops=None,
                 gf_low=0.30, gf_high=0.80, first_stop=0.0,
                 first_stop_rt=None, parent=None):
        super().__init__(parent)
        self._surface_mv = surface_mv
        self._stops      = stops or []
        self._gf_low     = gf_low
        self._gf_high    = gf_high
        self._first_stop = first_stop
        self._timeline   = timeline

        if first_stop_rt is not None:
            self._first_stop_rt = first_stop_rt
        else:
            self._first_stop_rt = min(
                (s.get("runtime", 0) - s.get("time", 0) for s in self._stops),
                default=0.0
            ) if self._stops else 0.0

        self._precompute(timeline)
        self.setMinimumSize(500, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _precompute(self, timeline):
        times, depths, leading, t_max = _leading_precompute(timeline, self._surface_mv)
        self._times   = times
        self._depths  = depths
        self._t_max   = t_max

        # For each timepoint: compute normalised fraction
        # norm = (pt - P_amb) / (M_depth - P_amb)
        self._norm = []
        self._lead_p_amb   = []
        self._lead_m_depth = []
        for idx, (rt, depth, snap, *_) in enumerate(timeline):
            lead_i    = leading[idx][0] if idx < len(leading) else 0
            snap_i    = snap[lead_i]          # tuple (pN2, pHe)
            pt        = snap_i[0] + snap_i[1] # total tissue pressure
            m_d       = _mv(lead_i, snap_i, depth, False)
            p_a       = _p_amb(depth)
            window    = m_d - p_a
            norm      = (pt - p_a) / window if window > 0 else 0.0
            self._norm.append(norm)
            self._lead_p_amb.append(p_a)
            self._lead_m_depth.append(m_d)

    def update_gf(self, gf_low, gf_high):
        self._gf_low  = gf_low
        self._gf_high = gf_high
        self.update()

    def paintEvent(self, _event):
        if not self._times:
            return
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        gw   = W - self.PAD_L - self.PAD_R
        ph   = H - self.PAD_T - self.PAD_B

        def tx(t):
            return self.PAD_L + int(t / self._t_max * gw)

        p.fillRect(self.PAD_L, self.PAD_T, gw, ph, QColor(22, 27, 34))

        # Y range: −25% to 125% so lines at 0% and 100% have margin
        Y_MIN = -0.25
        Y_MAX =  1.25

        def fy(val):
            clamped = max(Y_MIN, min(Y_MAX, val))
            frac = (clamped - Y_MIN) / (Y_MAX - Y_MIN)
            return self.PAD_T + ph - int(frac * ph)

        # Reference lines — DotLine so they don't visually clash with the
        # GF corridor (which is DashLine)
        ref_lines = [
            (0.0,  "0%",   QColor(80, 180, 255)),
            (0.25, "25%",  QColor(70, 130, 70)),
            (0.50, "50%",  QColor(80, 160, 80)),
            (0.75, "75%",  QColor(180, 120, 0)),
            (1.0,  "100%", QColor(220, 60, 60)),
        ]
        for ref, lbl, col in ref_lines:
            y = fy(ref)
            style = Qt.PenStyle.DotLine if ref not in (0.0, 1.0) else Qt.PenStyle.SolidLine
            p.setPen(QPen(col, 1, style))
            p.drawLine(self.PAD_L, y, self.PAD_L + gw, y)
            p.setPen(col)
            p.setFont(QFont("Arial", 7))
            p.drawText(self.PAD_L + gw + 2, y - 5, self.PAD_R - 4, 10,
                       Qt.AlignmentFlag.AlignLeft, lbl)

        # GF corridor lines — on this scale they sit at exactly GF_low% and GF_high%
        # Interpolate GF based on depth (same logic as leading bottom widget)
        gf_pts = []
        for i, t in enumerate(self._times):
            d = self._depths[i]
            if self._first_stop > 0:
                ratio = max(0.0, min(1.0, d / self._first_stop))
                gf = self._gf_high + (self._gf_low - self._gf_high) * ratio
            else:
                gf = self._gf_high
            x = tx(t)
            y = fy(gf)
            gf_pts.append((x, y, t >= self._first_stop_rt))

        # Shaded danger zone above GF corridor (deco phase only)
        # Reference lines at GF_low and GF_high — horizontal dotted guides
        # showing the corridor bounds so the user can read the values directly.
        for gf_ref, lbl_ref in ((self._gf_low, f"GF_L {int(self._gf_low*100)}%"),
                                (self._gf_high, f"GF_H {int(self._gf_high*100)}%")):
            yr = fy(gf_ref)
            p.setPen(QPen(QColor(255, 140, 40, 100), 1, Qt.PenStyle.DotLine))
            p.drawLine(self.PAD_L, yr, self.PAD_L + gw, yr)
            p.setPen(QColor(255, 140, 40))
            p.setFont(QFont("Arial", 7))
            p.drawText(self.PAD_L + gw + 2, yr - 5, self.PAD_R - 4, 10,
                       Qt.AlignmentFlag.AlignLeft, lbl_ref)

        # GF corridor — depth-based interpolation (linear in depth, not time).
        # On this normalised scale the ceiling at any moment = exactly GF_interpolated,
        # which guarantees the tissue line always stays below it.
        deco_pts = [QPointF(x, y) for x, y, is_deco in gf_pts if is_deco]
        if deco_pts:
            poly = QPolygonF()
            poly.append(QPointF(deco_pts[0].x(), self.PAD_T))
            poly.append(QPointF(deco_pts[-1].x(), self.PAD_T))
            for pt_f in reversed(deco_pts):
                poly.append(pt_f)
            p.setBrush(QColor(200, 60, 60, 40))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(poly)

        p.setPen(QPen(QColor(255, 140, 40), 2, Qt.PenStyle.DashLine))
        for i in range(len(gf_pts) - 1):
            if gf_pts[i][2]:
                p.drawLine(gf_pts[i][0], gf_pts[i][1], gf_pts[i+1][0], gf_pts[i+1][1])
        if gf_pts:
            last_deco = next((gf_pts[i] for i in range(len(gf_pts)-1, -1, -1)
                              if gf_pts[i][2]), None)
            if last_deco:
                p.setPen(QColor(255, 140, 40))
                p.setFont(QFont("Arial", 7))
                p.drawText(self.PAD_L + gw + 2, last_deco[1] - 5, self.PAD_R - 4, 10,
                           Qt.AlignmentFlag.AlignLeft,
                           f"GF {int(self._gf_low*100)}/{int(self._gf_high*100)}")

        # Leading saturation line (colour-coded by normalised value)
        for i in range(len(self._times) - 1):
            n0 = self._norm[i]
            n1 = self._norm[i + 1]
            x0 = tx(self._times[i])
            x1 = tx(self._times[i + 1])
            y0 = fy(n0)
            y1 = fy(n1)
            p.setPen(QPen(_frac_color_new(n0), 2))
            p.drawLine(x0, y0, x1, y1)

        # Title
        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        p.drawText(self.PAD_L + 4, self.PAD_T + 18,
                   "Leading Compartment — Normalised (0% = P_amb, 100% = M-value)")

        # Y axis labels
        p.setPen(QColor(180, 180, 180))
        p.setFont(QFont("Arial", 7))
        for ref in (-0.25, 0.0, 0.25, 0.50, 0.75, 1.0, 1.25):
            y = fy(ref)
            p.drawText(2, y - 6, self.PAD_L - 4, 12,
                       Qt.AlignmentFlag.AlignRight, f"{int(ref*100)}%")

        # Time axis
        _draw_time_axis_fn(p, self.PAD_L, gw, self._t_max, self.PAD_T + ph)

        # Deco stop markers
        if self._stops and self._t_max > 0:
            prev_x = -999
            for stop in self._stops:
                rt    = stop.get("runtime", 0)
                depth = stop.get("depth", 0)
                x = tx(rt)
                p.setPen(QPen(QColor(255, 230, 80, 120), 1, Qt.PenStyle.DashLine))
                p.drawLine(x, self.PAD_T, x, self.PAD_T + ph)
                if x - prev_x > 24:
                    p.setPen(QColor(255, 255, 255))
                    p.setFont(QFont("Arial", 8, QFont.Weight.Bold))
                    p.drawText(x - 16, self.PAD_T + ph - 14, 32, 12,
                               Qt.AlignmentFlag.AlignCenter, f"{depth:.0f}m")
                    prev_x = x
        p.end()


# ── Tab 4: GF corridor chart ──────────────────────────────────────────────────

class _GFCorridorWidget(QWidget):
    """
    Plots leading compartment saturation fraction over time together with the
    gradient-factor ceiling line that interpolates between GF_low (at first stop
    depth) and GF_high (at surface).  The diver must stay below the corridor.
    """
    PAD_L = 56; PAD_R = 60; PAD_T = 30; PAD_B = 44

    def __init__(self, timeline: list, surface_mv: bool,
                 stops: list = None, gf_low: float = 0.30, gf_high: float = 0.80,
                 parent=None):
        super().__init__(parent)
        self._surface_mv = surface_mv
        self._gf_low     = gf_low
        self._gf_high    = gf_high
        self._stops      = stops or []
        self._precompute(timeline)
        self.setMinimumSize(500, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _precompute(self, tl):
        self._times   = [e[0] for e in tl]
        self._depths  = [e[1] for e in tl]
        # first stop depth = deepest stop in the stop list
        self._first_stop = 0.0
        if self._stops:
            self._first_stop = max(s.get("depth", 0) for s in self._stops)
        # first stop runtime = start of deco phase (stop runtime minus stop time)
        self._first_stop_rt = min(
            (s.get("runtime", 0) - s.get("time", 0) for s in self._stops),
            default=self._times[-1] if self._times else 0.0
        ) if self._stops else (self._times[-1] if self._times else 0.0)

        self._leading_frac = []
        self._gf_limit     = []
        for i, e in enumerate(tl):
            fracs = _sat_fractions(e[2], e[1], self._surface_mv)
            best  = max(range(16), key=lambda j: fracs[j])
            self._leading_frac.append(fracs[best])
            # GF corridor: interpolate between gf_low @ first_stop and gf_high @ surface
            d = self._depths[i]
            if self._first_stop > 0:
                ratio = max(0.0, min(1.0, d / self._first_stop))
                gf = self._gf_high + (self._gf_low - self._gf_high) * ratio
            else:
                gf = self._gf_high
            self._gf_limit.append(gf)

        self._t_max = self._times[-1] if self._times else 1.0

    def update_gf(self, gf_low: float, gf_high: float):
        """Re-compute the corridor with new GF values and repaint."""
        self._gf_low  = gf_low
        self._gf_high = gf_high
        # re-build corridor limit list
        self._gf_limit = []
        for i in range(len(self._times)):
            d = self._depths[i]
            if self._first_stop > 0:
                ratio = max(0.0, min(1.0, d / self._first_stop))
                gf = self._gf_high + (self._gf_low - self._gf_high) * ratio
            else:
                gf = self._gf_high
            self._gf_limit.append(gf)
        self.update()

    def paintEvent(self, _event):
        if not self._times:
            return
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        gw   = W - self.PAD_L - self.PAD_R
        gh   = H - self.PAD_T - self.PAD_B

        p.fillRect(self.PAD_L, self.PAD_T, gw, gh, QColor(22, 27, 34))

        def tx(t):
            return self.PAD_L + int(t / self._t_max * gw)
        def ty(f):
            return self.PAD_T + gh - int(min(f, 1.05) / 1.05 * gh)

        # horizontal reference lines
        p.setPen(QColor(60, 60, 80))
        for ref in (0.25, 0.5, 0.75, 1.0):
            y = ty(ref)
            p.drawLine(self.PAD_L, y, self.PAD_L + gw, y)
            p.setPen(QColor(130, 130, 130))
            p.setFont(QFont("Arial", 7))
            p.drawText(self.PAD_L + gw + 4, y - 6, self.PAD_R - 6, 12,
                       Qt.AlignmentFlag.AlignLeft, f"{int(ref*100)}%")
            p.setPen(QColor(60, 60, 80))

        # Split corridor into bottom phase (not enforced) and deco phase (enforced)
        corridor_pts = [(QPointF(tx(self._times[i]), ty(self._gf_limit[i])),
                         self._times[i] >= self._first_stop_rt)
                        for i in range(len(self._times))]

        # Shaded "danger zone" fill only over the deco portion
        deco_pts = [pt for pt, is_deco in corridor_pts if is_deco]
        if deco_pts:
            poly = QPolygonF()
            poly.append(QPointF(deco_pts[0].x(),  self.PAD_T))
            poly.append(QPointF(deco_pts[-1].x(), self.PAD_T))
            for pt in reversed(deco_pts):
                poly.append(pt)
            p.setBrush(QColor(200, 60, 60, 40))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(poly)

        # Grey dotted line during bottom/descent — corridor not enforced
        p.setPen(QPen(QColor(100, 100, 100), 1, Qt.PenStyle.DotLine))
        for i in range(len(corridor_pts) - 1):
            if not corridor_pts[i][1]:
                p.drawLine(corridor_pts[i][0], corridor_pts[i+1][0])

        # Orange dashed line during deco — corridor is enforced
        p.setPen(QPen(QColor(255, 100, 80), 2, Qt.PenStyle.DashLine))
        for i in range(len(corridor_pts) - 1):
            if corridor_pts[i][1]:
                p.drawLine(corridor_pts[i][0], corridor_pts[i+1][0])

        # leading sat line, colour-coded
        for i in range(len(self._times) - 1):
            f = self._leading_frac[i]
            p.setPen(QPen(_frac_color_new(f), 2))
            p.drawLine(tx(self._times[i]),   ty(f),
                       tx(self._times[i+1]), ty(self._leading_frac[i+1]))

        # legend
        p.setFont(QFont("Arial", 8))
        p.setPen(QPen(QColor(255, 100, 80), 2, Qt.PenStyle.DashLine))
        p.drawLine(self.PAD_L + 8, self.PAD_T + 12, self.PAD_L + 30, self.PAD_T + 12)
        p.setPen(QColor(200, 200, 200))
        p.drawText(self.PAD_L + 34, self.PAD_T + 6, 400, 14,
                   Qt.AlignmentFlag.AlignLeft,
                   f"GF corridor — enforced during deco  (GFlow {int(self._gf_low*100)}% → GFhigh {int(self._gf_high*100)}%)   "
                   f"grey dotted = bottom/descent phase (not enforced)")

        p.setPen(QPen(QColor(80, 200, 255), 2))
        p.drawLine(self.PAD_L + 8, self.PAD_T + 28, self.PAD_L + 30, self.PAD_T + 28)
        p.setPen(QColor(200, 200, 200))
        p.drawText(self.PAD_L + 34, self.PAD_T + 22, 200, 14,
                   Qt.AlignmentFlag.AlignLeft, "Leading compartment saturation")

        # time axis
        p.setPen(QColor(160, 160, 160))
        p.setFont(QFont("Arial", 8))
        step = _nice_tick(self._t_max)
        t    = 0.0
        while t <= self._t_max + 0.5:
            x = tx(t)
            p.drawLine(x, self.PAD_T + gh, x, self.PAD_T + gh + 4)
            p.drawText(x - 16, self.PAD_T + gh + 6, 32, 14,
                       Qt.AlignmentFlag.AlignCenter, f"{t:.0f}")
            t += step
        p.drawText(self.PAD_L, self.PAD_T + gh + 22, gw, 14,
                   Qt.AlignmentFlag.AlignCenter, "Runtime [min]")

        # Y axis label
        p.save()
        p.translate(14, self.PAD_T + gh // 2)
        p.rotate(-90)
        p.setPen(QColor(200, 200, 200))
        p.drawText(-60, -4, 120, 14, Qt.AlignmentFlag.AlignCenter, "Saturation fraction")
        p.restore()

        p.end()


# ── Tab 5: N2 / He split — two separate full heatmaps ────────────────────────

class _SingleGasHeatmap(QWidget):
    """
    Full-height heatmap for a single gas (N₂ or He).
    colour_fn(fraction) → QColor.
    gas_idx: 0 = N₂ (p_n2), 1 = He (p_he).
    """
    PAD_L = 56; PAD_R = 16; PAD_T = 8; DEPTH_H = 22; ROW_H = 22; PAD_B = 28

    def __init__(self, timeline, surface_mv, gas_idx, title, colour_fn, stops=None, parent=None):
        super().__init__(parent)
        self._surface_mv = surface_mv
        self._gas_idx    = gas_idx
        self._title      = title
        self._colour_fn  = colour_fn
        self._stops      = stops or []
        self._precompute(timeline)
        h = self.PAD_T + self.DEPTH_H + 4 + 16 * self.ROW_H + self.PAD_B
        self.setMinimumHeight(h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _precompute(self, tl):
        self._times  = [e[0] for e in tl]
        self._depths = [e[1] for e in tl]
        self._fracs  = []   # list of 16 fracs (gas partial pressure / M-value)
        for e in tl:
            snap  = e[2]
            depth = e[1]
            p_amb = P_SURF if self._surface_mv else _p_amb(depth)
            row = []
            for i, (p_n2, p_he) in enumerate(snap):
                if i >= len(TISSUES):
                    row.append(0.0)
                    continue
                _, N2_a, N2_b, _, He_a, He_b = TISSUES[i]
                pt = p_n2 + p_he
                if pt > 0:
                    a = (N2_a * p_n2 + He_a * p_he) / pt
                    b = (N2_b * p_n2 + He_b * p_he) / pt
                else:
                    a, b = N2_a, N2_b
                mv  = a + p_amb / b
                val = (p_n2 if self._gas_idx == 0 else p_he)
                row.append(val / mv if mv > 0 else 0.0)
            self._fracs.append(row)
        self._max_depth = max(self._depths) if self._depths else 1.0
        self._t_max     = self._times[-1]   if self._times   else 1.0

    def sizeHint(self):
        return QSize(self.PAD_L + max(400, len(self._times) * 4) + self.PAD_R,
                     self.PAD_T + self.DEPTH_H + 4 + 16 * self.ROW_H + self.PAD_B)

    def paintEvent(self, _event):
        if not self._times:
            return
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        W  = self.width()
        gw = W - self.PAD_L - self.PAD_R
        n  = len(self._times)
        cw = gw / n if n > 0 else 1

        # depth strip
        dp_top = self.PAD_T
        for i, d in enumerate(self._depths):
            x     = int(self.PAD_L + i * cw)
            ratio = d / self._max_depth if self._max_depth > 0 else 0
            p.fillRect(x, dp_top, max(1, int(cw)+1), self.DEPTH_H,
                       QColor(0, 50, int(30 + 180 * ratio)))
        p.setPen(QColor(200, 220, 255))
        p.setFont(QFont("Arial", 8))
        p.drawText(self.PAD_L + 4, dp_top, gw, self.DEPTH_H,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"Depth  (max {self._max_depth:.0f} m)")

        # title inside top-left of heatmap area
        hm_top = dp_top + self.DEPTH_H + 4
        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        p.drawText(self.PAD_L + 4, hm_top + 16, self._title)

        # heatmap cells
        for i in range(n):
            x = int(self.PAD_L + i * cw)
            w = max(1, int(cw) + 1)
            for comp in range(16):
                f = self._fracs[i][comp] if comp < len(self._fracs[i]) else 0.0
                p.fillRect(x, hm_top + comp * self.ROW_H, w, self.ROW_H,
                           self._colour_fn(f))

        # compartment labels
        p.setFont(QFont("Arial", 7))
        for comp in range(16):
            p.setPen(QColor(200, 200, 200))
            p.drawText(0, hm_top + comp * self.ROW_H, self.PAD_L - 2, self.ROW_H,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"C{comp+1} {_N2_HT[comp]}m")

        # grid lines
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for comp in range(1, 16):
            y = hm_top + comp * self.ROW_H
            p.drawLine(self.PAD_L, y, W - self.PAD_R, y)

        # deco stop markers
        gh = 16 * self.ROW_H
        if self._stops and self._t_max > 0:
            prev_x = -999
            for stop in self._stops:
                rt    = stop.get("runtime", 0)
                depth = stop.get("depth", 0)
                x     = int(self.PAD_L + (rt / self._t_max) * gw)
                p.setPen(QPen(QColor(255, 255, 255, 110), 1, Qt.PenStyle.DashLine))
                p.drawLine(x, hm_top, x, hm_top + gh)
                if x - prev_x > 22:
                    p.setPen(QColor(255, 230, 80))
                    p.setFont(QFont("Arial", 7))
                    p.drawText(x - 14, dp_top + 3, 28, self.DEPTH_H - 6,
                               Qt.AlignmentFlag.AlignCenter, f"{depth:.0f}m")
                    prev_x = x

        # time axis
        p.setPen(QColor(160, 160, 160))
        p.setFont(QFont("Arial", 8))
        step = _nice_tick(self._t_max)
        t    = 0.0
        while t <= self._t_max + 0.5:
            x = int(self.PAD_L + (t / self._t_max) * gw)
            p.drawLine(x, hm_top + gh, x, hm_top + gh + 4)
            p.drawText(x - 16, hm_top + gh + 6, 32, 14,
                       Qt.AlignmentFlag.AlignCenter, f"{t:.0f}")
            t += step
        p.drawText(self.PAD_L, hm_top + gh + 20, gw, 12,
                   Qt.AlignmentFlag.AlignCenter, "Runtime [min]")
        p.end()


def _n2_colour(f: float) -> QColor:
    """Dark blue → bright cyan, scaled to N₂ fraction."""
    v = min(1.0, f / 0.9)
    return QColor(int(20 + 180*v), int(60 + 160*v), int(160 + 80*v))


def _he_colour(f: float) -> QColor:
    """Dark green → bright lime, scaled to He fraction."""
    v = min(1.0, f / 0.5) if f > 0 else 0.0
    return QColor(int(10 + 60*v), int(120 + 120*v), int(10 + 40*v))


class _N2HeHeatmapWidget(QWidget):
    """Two stacked full heatmaps: N₂ on top, He below."""
    def __init__(self, timeline, surface_mv, stops=None, parent=None):
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(8)
        vl.addWidget(_SingleGasHeatmap(timeline, surface_mv, 0,
                                       "N₂ partial pressure (fraction of M-value)",
                                       _n2_colour, stops=stops))
        vl.addWidget(_SingleGasHeatmap(timeline, surface_mv, 1,
                                       "He partial pressure (fraction of M-value)",
                                       _he_colour, stops=stops))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


# ── Tab 6: Off-gassing rate heatmap ──────────────────────────────────────────

class _OffgasRateWidget(QWidget):
    """
    Δ(pN2+pHe) / Δt per compartment, colour-coded:
    red  = loading (positive rate)
    blue = off-gassing (negative rate)
    grey = near-zero change
    """
    PAD_L = 52; PAD_R = 16; PAD_T = 8; DEPTH_H = 28; ROW_H = 22; PAD_B = 32

    def __init__(self, timeline: list, parent=None):
        super().__init__(parent)
        self._precompute(timeline)
        h = self.PAD_T + self.DEPTH_H + 4 + 16 * self.ROW_H + self.PAD_B
        self.setMinimumHeight(h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _precompute(self, tl):
        self._times  = [e[0] for e in tl]
        self._depths = [e[1] for e in tl]
        n = len(tl)
        # rates[i][comp] = Δpt / Δt  (bar/min)
        self._rates  = []
        for i in range(n):
            row = []
            for comp in range(16):
                if i == 0 or comp >= len(tl[i][2]):
                    row.append(0.0)
                    continue
                dt = self._times[i] - self._times[i-1]
                if dt <= 0:
                    row.append(0.0)
                    continue
                p_n2_now, p_he_now = tl[i][2][comp]
                p_n2_prv, p_he_prv = tl[i-1][2][comp]
                dp = (p_n2_now + p_he_now) - (p_n2_prv + p_he_prv)
                row.append(dp / dt)
            self._rates.append(row)

        # Per-compartment normalisation: each row uses its own max absolute rate
        # so slow tissues (tiny bar/min) still show full colour contrast.
        self._comp_scale = []
        for comp in range(16):
            max_abs = max((abs(self._rates[i][comp])
                           for i in range(n) if comp < len(self._rates[i])),
                          default=1e-9)
            self._comp_scale.append(max_abs if max_abs > 1e-9 else 1e-9)

        self._max_depth = max(self._depths) if self._depths else 1.0
        self._t_max     = self._times[-1]   if self._times   else 1.0

    def sizeHint(self) -> QSize:
        return QSize(self.PAD_L + max(400, len(self._times) * 4) + self.PAD_R,
                     self.PAD_T + self.DEPTH_H + 4 + 16 * self.ROW_H + self.PAD_B)

    def paintEvent(self, _event):
        if not self._times:
            return
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        W  = self.width()
        gw = W - self.PAD_L - self.PAD_R
        n  = len(self._times)
        cw = gw / n if n > 0 else 1

        dp_top = self.PAD_T
        for i, d in enumerate(self._depths):
            x = int(self.PAD_L + i * cw)
            ratio = d / self._max_depth if self._max_depth > 0 else 0
            p.fillRect(x, dp_top, max(1, int(cw)+1), self.DEPTH_H,
                       QColor(0, 50, int(30 + 180*ratio)))
        # Loading / Off-gassing legend in top-right of depth strip
        p.setFont(QFont("Arial", 8))
        p.fillRect(W - self.PAD_R - 120, dp_top + 6, 12, 10, QColor(220, 140, 20))
        p.setPen(QColor(255, 230, 150))
        p.drawText(W - self.PAD_R - 106, dp_top + 6, 50, 10,
                   Qt.AlignmentFlag.AlignLeft, "Loading")
        p.fillRect(W - self.PAD_R - 55, dp_top + 6, 12, 10, QColor(10, 160, 210))
        p.setPen(QColor(150, 220, 255))
        p.drawText(W - self.PAD_R - 41, dp_top + 6, 60, 10,
                   Qt.AlignmentFlag.AlignLeft, "Off-gassing")

        hm_top = dp_top + self.DEPTH_H + 4
        for i in range(n):
            x = int(self.PAD_L + i * cw)
            w = max(1, int(cw)+1)
            for comp in range(16):
                rate = self._rates[i][comp] if comp < len(self._rates[i]) else 0.0
                # normalise to [-1, 1] using per-compartment max
                norm = max(-1.0, min(1.0, rate / self._comp_scale[comp]))
                p.fillRect(x, hm_top + comp * self.ROW_H, w, self.ROW_H,
                           _rate_color_norm(norm))

        p.setFont(QFont("Arial", 7))
        for comp in range(16):
            p.setPen(Qt.GlobalColor.black)
            p.drawText(0, hm_top + comp * self.ROW_H, self.PAD_L - 2, self.ROW_H,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"C{comp+1} {_N2_HT[comp]}m")

        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for comp in range(1, 16):
            y = hm_top + comp * self.ROW_H
            p.drawLine(self.PAD_L, y, W - self.PAD_R, y)

        # legend
        lx = W - self.PAD_R - 180
        p.fillRect(lx,      dp_top+3, 20, 9, QColor(180, 30, 20))
        p.setPen(QColor(220, 220, 255))
        p.setFont(QFont("Arial", 7))
        p.drawText(lx+22, dp_top+3, 70, 9, Qt.AlignmentFlag.AlignLeft, "Loading")
        p.fillRect(lx+80, dp_top+3, 20, 9, QColor(20, 80, 200))
        p.drawText(lx+102, dp_top+3, 80, 9, Qt.AlignmentFlag.AlignLeft, "Off-gassing")

        gh = 16 * self.ROW_H
        p.setPen(QColor(160, 160, 160))
        p.setFont(QFont("Arial", 8))
        step = _nice_tick(self._t_max)
        t    = 0.0
        while t <= self._t_max + 0.5:
            x = int(self.PAD_L + (t / self._t_max) * gw)
            p.drawLine(x, hm_top + gh, x, hm_top + gh + 4)
            p.drawText(x - 16, hm_top + gh + 6, 32, 14,
                       Qt.AlignmentFlag.AlignCenter, f"{t:.0f}")
            t += step
        p.drawText(self.PAD_L, hm_top + gh + 20, gw, 12,
                   Qt.AlignmentFlag.AlignCenter, "Runtime [min]")
        p.end()


# ── Tab 7: Animated radar (spider) chart ─────────────────────────────────────

class _RadarWidget(QWidget):
    """
    16 spokes arranged in a circle, each scaled to that compartment's
    saturation fraction.  A QTimer animates through the timeline.
    """
    def __init__(self, timeline: list, surface_mv: bool, parent=None):
        super().__init__(parent)
        self._surface_mv = surface_mv
        self._frame      = 0
        self._playing    = False
        self._precompute(timeline)
        self.setMinimumSize(420, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

    def _precompute(self, tl):
        self._times  = [e[0] for e in tl]
        self._depths = [e[1] for e in tl]
        self._fracs  = [_sat_fractions(e[2], e[1], self._surface_mv) for e in tl]
        self._t_max  = self._times[-1] if self._times else 1.0
        self._n      = len(tl)

    def _advance(self):
        self._frame = (self._frame + 1) % self._n
        self.update()

    def set_frame(self, frame: int):
        self._frame = frame
        self.update()

    def toggle_play(self, playing: bool):
        self._playing = playing
        if playing:
            self._timer.start(60)
        else:
            self._timer.stop()

    def paintEvent(self, _event):
        if not self._fracs:
            return
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        cx, cy = W // 2, H // 2
        R = min(W, H) // 2 - 50

        f = self._frame
        fracs  = self._fracs[f]
        depth  = self._depths[f]
        t      = self._times[f]

        # background
        p.fillRect(0, 0, W, H, QColor(18, 22, 30))

        # reference rings
        p.setFont(QFont("Arial", 7))
        for ref in (0.25, 0.5, 0.75, 1.0):
            r = int(ref * R)
            col = QColor(220, 60, 60, 180) if ref == 1.0 else QColor(60, 60, 80)
            p.setPen(QPen(col, 1, Qt.PenStyle.DashLine if ref < 1.0 else Qt.PenStyle.SolidLine))
            p.drawEllipse(cx - r, cy - r, r*2, r*2)
            p.setPen(QColor(120, 120, 120))
            p.drawText(cx + r + 2, cy - 5, 30, 12,
                       Qt.AlignmentFlag.AlignLeft, f"{int(ref*100)}%")

        # spokes + polygon
        n_comp = 16
        angles = [2 * math.pi * i / n_comp - math.pi / 2 for i in range(n_comp)]
        pts = []
        for i, angle in enumerate(angles):
            fv  = min(fracs[i], 1.2) / 1.2 if i < len(fracs) else 0.0
            r   = fv * R
            pts.append(QPointF(cx + r * math.cos(angle),
                                cy + r * math.sin(angle)))

        # fill polygon, colour by max saturation
        max_f  = max(fracs) if fracs else 0.0
        fill_c = _frac_color_new(max_f)
        fill_c.setAlpha(120)
        poly = QPolygonF(pts)
        p.setBrush(fill_c)
        p.setPen(QPen(_frac_color_new(max_f), 2))
        p.drawPolygon(poly)

        # spoke labels
        p.setFont(QFont("Arial", 7))
        for i, angle in enumerate(angles):
            lx = cx + int((R + 26) * math.cos(angle))
            ly = cy + int((R + 26) * math.sin(angle))
            col = _frac_color_new(fracs[i] if i < len(fracs) else 0.0)
            p.setPen(col)
            p.drawText(lx - 16, ly - 7, 32, 14,
                       Qt.AlignmentFlag.AlignCenter, f"C{i+1}")

        # info label
        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        p.drawText(0, 8, W, 20, Qt.AlignmentFlag.AlignCenter,
                   f"t = {t:.0f} min   depth = {depth:.0f} m   "
                   f"peak = {max_f*100:.0f}%  (frame {f+1}/{self._n})")
        p.end()


class _RadarTab(QWidget):
    def __init__(self, timeline: list, surface_mv: bool, parent=None):
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setContentsMargins(6, 6, 6, 6)

        self._radar = _RadarWidget(timeline, surface_mv)
        vl.addWidget(self._radar, 1)

        hl = QHBoxLayout()
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, max(0, len(timeline) - 1))
        self._slider.valueChanged.connect(self._on_slider)

        self._play_btn = QPushButton("▶ Play")
        self._play_btn.setFixedWidth(80)
        self._play_btn.setCheckable(True)
        self._play_btn.toggled.connect(self._on_play)

        hl.addWidget(QLabel("Frame:"))
        hl.addWidget(self._slider, 1)
        hl.addWidget(self._play_btn)
        vl.addLayout(hl)

        note = QLabel(
            "RADAR CHART — Each of the 16 spokes represents one Bühlmann tissue compartment, "
            "ordered by half-time: C1 (4 min, fastest) at the top, clockwise to C16 (635 min, slowest).  "
            "Spoke length = that compartment's saturation fraction (pt / M-value).  "
            "The outer red ring = 100% M-value — touching it means the tissue is at its decompression limit.  "
            "The shape of the polygon tells the story: a narrow spike at the top means fast tissues are loaded "
            "(typical during descent); a wide, rounded shape means slow tissues are loaded (typical in deco).  "
            "Use ▶ Play to animate through the dive, or scrub the slider to inspect any moment."
        )
        note.setStyleSheet("font-size:9px; color:#888;")
        note.setWordWrap(True)
        vl.addWidget(note)

    def _on_slider(self, val):
        self._radar.set_frame(val)

    def _on_play(self, playing):
        self._radar.toggle_play(playing)
        self._play_btn.setText("⏸ Pause" if playing else "▶ Play")
        if playing:
            self._radar._timer.timeout.connect(self._sync_slider)
        else:
            try:
                self._radar._timer.timeout.disconnect(self._sync_slider)
            except RuntimeError:
                pass

    def _sync_slider(self):
        self._slider.setValue(self._radar._frame)


# ── Tab 8: Animated bar chart ─────────────────────────────────────────────────

class _BarWidget(QWidget):
    """Horizontal bar chart — 16 compartments, animated over timeline."""
    PAD_L = 56; PAD_R = 20; PAD_T = 30; PAD_B = 20

    def __init__(self, timeline: list, surface_mv: bool,
                 gf_low: float = 0.55, gf_high: float = 0.80,
                 first_stop_depth: float = 0.0, parent=None):
        super().__init__(parent)
        self._surface_mv       = surface_mv
        self._gf_low           = gf_low
        self._gf_high          = gf_high
        self._first_stop_depth = first_stop_depth
        self._frame      = 0
        self._playing    = False
        self._precompute(timeline)
        self.setMinimumSize(380, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

    def _precompute(self, tl):
        self._times  = [e[0] for e in tl]
        self._depths = [e[1] for e in tl]
        if self._surface_mv:
            self._fracs = [_sat_fractions(e[2], e[1], True) for e in tl]
        else:
            self._fracs = [_norm_fractions(e[2], e[1]) for e in tl]
        self._n = len(tl)

    def _advance(self):
        self._frame = (self._frame + 1) % self._n
        self.update()

    def set_frame(self, frame: int):
        self._frame = frame
        self.update()

    def toggle_play(self, playing: bool):
        if playing:
            self._timer.start(60)
        else:
            self._timer.stop()

    def paintEvent(self, _event):
        if not self._fracs:
            return
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H   = self.width(), self.height()
        gw     = W - self.PAD_L - self.PAD_R
        gh     = H - self.PAD_T - self.PAD_B
        bar_h  = (gh - 16 * 3) // 16

        f     = self._frame
        fracs = self._fracs[f]
        depth = self._depths[f]
        t     = self._times[f]

        p.fillRect(0, 0, W, H, QColor(18, 22, 30))

        if self._surface_mv:
            # Surface mode: dynamic scale, 75%/90%/M-value reference lines
            max_frac = max(fracs) if fracs else 1.2
            scale    = max(1.2, max_frac * 1.08)

            mv_x = self.PAD_L + int(1.0 / scale * gw)
            p.setPen(QPen(QColor(220, 60, 60), 2, Qt.PenStyle.DashLine))
            p.drawLine(mv_x, self.PAD_T, mv_x, self.PAD_T + gh)
            p.setPen(QColor(220, 60, 60))
            p.setFont(QFont("Arial", 7))
            p.drawText(mv_x - 20, self.PAD_T - 12, 40, 12,
                       Qt.AlignmentFlag.AlignCenter, "M-value")

            for ref, lbl in ((0.75, "75%"), (0.90, "90%")):
                rx = self.PAD_L + int(ref / scale * gw)
                p.setPen(QPen(QColor(100, 100, 60), 1, Qt.PenStyle.DashLine))
                p.drawLine(rx, self.PAD_T, rx, self.PAD_T + gh)
                p.setPen(QColor(140, 140, 80))
                p.drawText(rx - 14, self.PAD_T - 12, 28, 12,
                           Qt.AlignmentFlag.AlignCenter, lbl)

            p.setPen(QColor(120, 120, 140))
            p.setFont(QFont("Arial", 7))
            p.drawText(W - self.PAD_R - 70, self.PAD_T - 12, 70, 12,
                       Qt.AlignmentFlag.AlignRight, f"scale: {scale*100:.0f}%")
        else:
            # Depth mode: normalised (0%=P_amb, 100%=M-value), fixed scale 0–120%
            scale = 1.2

            # M-value line at 100%
            mv_x = self.PAD_L + int(1.0 / scale * gw)
            p.setPen(QPen(QColor(220, 60, 60), 2, Qt.PenStyle.DashLine))
            p.drawLine(mv_x, self.PAD_T, mv_x, self.PAD_T + gh)
            p.setPen(QColor(220, 60, 60))
            p.setFont(QFont("Arial", 7))
            p.drawText(mv_x - 20, self.PAD_T - 12, 40, 12,
                       Qt.AlignmentFlag.AlignCenter, "M-value")

            # GF High line
            gfh_x = self.PAD_L + int(self._gf_high / scale * gw)
            p.setPen(QPen(QColor(255, 140, 0), 1, Qt.PenStyle.DashLine))
            p.drawLine(gfh_x, self.PAD_T, gfh_x, self.PAD_T + gh)
            p.setPen(QColor(255, 160, 40))
            p.setFont(QFont("Arial", 7))
            p.drawText(gfh_x - 18, self.PAD_T - 12, 36, 12,
                       Qt.AlignmentFlag.AlignCenter, f"GFh {int(self._gf_high*100)}%")

            # GF Low line
            gfl_x = self.PAD_L + int(self._gf_low / scale * gw)
            p.setPen(QPen(QColor(100, 200, 100), 1, Qt.PenStyle.DashLine))
            p.drawLine(gfl_x, self.PAD_T, gfl_x, self.PAD_T + gh)
            p.setPen(QColor(120, 210, 120))
            p.setFont(QFont("Arial", 7))
            p.drawText(gfl_x - 18, self.PAD_T - 12, 36, 12,
                       Qt.AlignmentFlag.AlignCenter, f"GFl {int(self._gf_low*100)}%")

            # Dynamic current-GF line (interpolated between GF_low and GF_high)
            if self._first_stop_depth > 0:
                cur_gf = (self._gf_high
                          + (self._gf_low - self._gf_high)
                          * (depth / self._first_stop_depth))
                cur_gf = max(self._gf_low, min(self._gf_high, cur_gf))
            else:
                cur_gf = self._gf_high
            cgf_x = self.PAD_L + int(cur_gf / scale * gw)
            p.setPen(QPen(QColor(0, 220, 255), 2, Qt.PenStyle.SolidLine))
            p.drawLine(cgf_x, self.PAD_T, cgf_x, self.PAD_T + gh)
            p.setPen(QColor(0, 220, 255))
            p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
            p.drawText(cgf_x - 22, self.PAD_T - 12, 44, 12,
                       Qt.AlignmentFlag.AlignCenter,
                       f"GF {cur_gf*100:.0f}%")

            p.setPen(QColor(120, 120, 140))
            p.setFont(QFont("Arial", 7))
            p.drawText(W - self.PAD_R - 120, self.PAD_T - 12, 120, 12,
                       Qt.AlignmentFlag.AlignRight, "0% = P_amb  |  100% = M-val")

        for i in range(16):
            fv = fracs[i] if i < len(fracs) else 0.0
            bar_w = max(0, int(fv / scale * gw))
            y     = self.PAD_T + i * (bar_h + 3)

            p.fillRect(self.PAD_L, y, gw, bar_h, QColor(35, 40, 50))
            if bar_w > 0:
                p.fillRect(self.PAD_L, y, bar_w, bar_h, _frac_color_new(fv))

            p.setPen(QColor(200, 200, 200))
            p.setFont(QFont("Arial", 7))
            p.drawText(2, y, self.PAD_L - 4, bar_h,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"C{i+1}")
            p.drawText(self.PAD_L + bar_w + 4, y, 40, bar_h,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{fv*100:.0f}%")

        p.setPen(QColor(220, 220, 220))
        p.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        p.drawText(0, H - self.PAD_B + 2, W, 16,
                   Qt.AlignmentFlag.AlignCenter,
                   f"t = {t:.0f} min   depth = {depth:.0f} m   (frame {f+1}/{self._n})")
        p.end()


class _BarTab(QWidget):
    def __init__(self, timeline: list, surface_mv: bool,
                 gf_low: float = 0.55, gf_high: float = 0.80,
                 first_stop_depth: float = 0.0, parent=None):
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setContentsMargins(6, 6, 6, 6)

        self._bar = _BarWidget(timeline, surface_mv, gf_low=gf_low, gf_high=gf_high,
                               first_stop_depth=first_stop_depth)
        vl.addWidget(self._bar, 1)

        hl = QHBoxLayout()
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, max(0, len(timeline) - 1))
        self._slider.valueChanged.connect(self._on_slider)

        self._play_btn = QPushButton("▶ Play")
        self._play_btn.setFixedWidth(80)
        self._play_btn.setCheckable(True)
        self._play_btn.toggled.connect(self._on_play)

        hl.addWidget(QLabel("Frame:"))
        hl.addWidget(self._slider, 1)
        hl.addWidget(self._play_btn)
        vl.addLayout(hl)

        if surface_mv:
            note_text = (
                "ANIMATED BAR CHART (Surface M-value) — Each horizontal bar is one tissue compartment "
                "(C1 top = fastest, C16 bottom = slowest).  Bar length = saturation fraction (pt / Surface M-value).  "
                "The red dashed line marks 100% of the surface M-value — any bar reaching it means the tissue "
                "is supersaturated beyond the surface limit.  The yellow 90% and 75% lines are common conservative "
                "reference thresholds.  Scale adjusts dynamically to always fit the largest bar.  "
                "Bar colour: blue = low load, green = moderate, yellow/orange = approaching limit, red/purple = at or over limit.  "
                "Use ▶ Play to watch how gas loads shift from fast to slow tissues as the dive progresses."
            )
        else:
            note_text = (
                "ANIMATED BAR CHART (Depth M-value, normalised) — Each horizontal bar is one tissue compartment "
                "(C1 top = fastest, C16 bottom = slowest).  Scale: 0% = ambient pressure (P_amb), 100% = depth-adjusted M-value.  "
                "Negative values = tissue is still loading (below ambient equilibrium) — normal during descent and bottom phase.  "
                "The red dashed line marks 100% (M-value limit).  "
                "The orange GF High and green GF Low lines show the gradient factor corridor — "
                "during deco, bars should be held at or below GF High.  "
                "Bar colour: blue = low load, green = moderate, yellow/orange = approaching limit, red/purple = at or over limit.  "
                "Use ▶ Play to watch how gas loads shift from fast to slow tissues as the dive progresses."
            )
        note = QLabel(note_text)
        note.setStyleSheet("font-size:9px; color:#888;")
        note.setWordWrap(True)
        vl.addWidget(note)

    def _on_slider(self, val):
        self._bar.set_frame(val)

    def _on_play(self, playing):
        self._bar.toggle_play(playing)
        self._play_btn.setText("⏸ Pause" if playing else "▶ Play")
        if playing:
            self._bar._timer.timeout.connect(self._sync_slider)
        else:
            try:
                self._bar._timer.timeout.disconnect(self._sync_slider)
            except RuntimeError:
                pass

    def _sync_slider(self):
        self._slider.setValue(self._bar._frame)


# ── Tab 9: Phase-banded heatmap ───────────────────────────────────────────────

class _PhaseBandWidget(_HeatmapWidget):
    """
    Inherits the standard heatmap and overlays coloured vertical bands
    for each dive phase (surface, transit, bottom, bailout, deco).
    Phase list format: [(runtime, depth, label, snapshot), ...]
    """
    def __init__(self, timeline: list, surface_mv: bool,
                 phase_list: list = None, stops: list = None, parent=None):
        self._phase_list = phase_list or []
        super().__init__(timeline, surface_mv, stops=stops, parent=parent)

    def paintEvent(self, event):
        # draw base heatmap first
        super().paintEvent(event)
        if not self._phase_list or not self._times or self._t_max <= 0:
            return

        p  = QPainter(self)
        W  = self.width()
        gw = W - self.PAD_L - self.PAD_R
        hm_top = self.PAD_T + self.DEPTH_H + 4
        gh     = 16 * self.ROW_H

        def tx(t):
            return self.PAD_L + int(t / self._t_max * gw)

        # draw phase bands
        prev_rt    = 0.0
        prev_label = self._phase_list[0][2] if self._phase_list else "surface"
        for entry in self._phase_list[1:]:
            rt, _d, label, _snap = entry
            x0 = tx(prev_rt)
            x1 = tx(rt)
            col = _PHASE_COLORS.get(prev_label, QColor(80, 80, 80, 50))
            p.fillRect(x0, hm_top, x1 - x0, gh, col)
            prev_rt    = rt
            prev_label = label
        # last segment to end
        x0 = tx(prev_rt)
        x1 = tx(self._t_max)
        col = _PHASE_COLORS.get(prev_label, QColor(80, 80, 80, 50))
        p.fillRect(x0, hm_top, x1 - x0, gh, col)

        # phase label at band start
        p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        prev_rt = 0.0
        prev_label = self._phase_list[0][2] if self._phase_list else "surface"
        for entry in self._phase_list[1:]:
            rt, _d, label, _snap = entry
            x0 = tx(prev_rt)
            p.setPen(QColor(255, 255, 255, 180))
            p.drawText(x0 + 2, hm_top + gh - 14, 80, 12,
                       Qt.AlignmentFlag.AlignLeft, prev_label)
            prev_rt    = rt
            prev_label = label

        # phase legend
        legend_x = self.PAD_L + 4
        legend_y  = self.PAD_T + 2
        p.setFont(QFont("Arial", 7))
        col_order = ["surface", "transit", "bottom", "bailout", "deco"]
        for lbl in col_order:
            col = _PHASE_COLORS.get(lbl, QColor(80, 80, 80, 80))
            col.setAlpha(200)
            p.fillRect(legend_x, legend_y, 10, 8, col)
            p.setPen(QColor(230, 230, 230))
            p.drawText(legend_x + 12, legend_y - 1, 50, 10,
                       Qt.AlignmentFlag.AlignLeft, lbl)
            legend_x += 62

        p.end()


# ── Legend bar ────────────────────────────────────────────────────────────────

class _LegendWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setMinimumWidth(300)

    def paintEvent(self, _event):
        p = QPainter(self)
        W = self.width()
        bx, by, bh = 80, 8, 14
        bw = W - bx - 60
        steps = 200
        for i in range(steps):
            f = i / (steps - 1) * 1.2
            x = bx + int(i / steps * bw)
            p.fillRect(x, by, max(1, bw // steps + 1), bh, _frac_color_new(f))
        p.setPen(Qt.GlobalColor.black)
        p.setFont(QFont("Arial", 8))
        p.drawRect(bx, by, bw, bh)
        p.drawText(0, by, bx - 2, bh,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "0%")
        for lbl, frac in (("50%", 0.5), ("75%", 0.75), ("90%", 0.90), ("100%", 1.0)):
            x = bx + int(frac / 1.2 * bw)
            p.drawLine(x, by + bh, x, by + bh + 3)
            p.drawText(x - 16, by + bh + 4, 32, 10,
                       Qt.AlignmentFlag.AlignCenter, lbl)
        p.drawText(bx + bw + 4, by, 50, bh,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "120%+")
        p.end()


# ── GF input bar (reusable) ───────────────────────────────────────────────────

def _gf_input_bar(widget, gf_low: float, gf_high: float,
                  label: str = "Override GF corridor:",
                  on_apply=None) -> QHBoxLayout:
    """
    Returns a QHBoxLayout with GF Low / GF High fields and an Apply button.
    If on_apply is provided it is called with (gf_low, gf_high) — typically
    triggers a full re-simulation.  Otherwise widget.update_gf() is called
    for a visual-only corridor update.
    """
    row = QHBoxLayout()
    row.setSpacing(6)

    row.addWidget(QLabel(label))

    row.addWidget(QLabel("GF Low %:"))
    le_lo = QLineEdit(str(int(round(gf_low * 100))))
    le_lo.setFixedWidth(44)
    le_lo.setToolTip("Gradient Factor Low — applied at the deepest deco stop (0–100)")
    row.addWidget(le_lo)

    row.addWidget(QLabel("GF High %:"))
    le_hi = QLineEdit(str(int(round(gf_high * 100))))
    le_hi.setFixedWidth(44)
    le_hi.setToolTip("Gradient Factor High — applied at the surface (0–100)")
    row.addWidget(le_hi)

    resim = on_apply is not None
    btn = QPushButton("Apply")
    btn.setFixedWidth(60)
    btn.setToolTip(
        "Re-run full decompression simulation with new GF values" if resim
        else "Update the GF corridor line without re-running the simulation"
    )

    def _apply():
        try:
            lo = max(1, min(100, int(le_lo.text()))) / 100.0
            hi = max(1, min(100, int(le_hi.text()))) / 100.0
            if resim:
                on_apply(lo, hi)
            else:
                widget.update_gf(lo, hi)
        except ValueError:
            pass

    btn.clicked.connect(_apply)
    le_lo.returnPressed.connect(_apply)
    le_hi.returnPressed.connect(_apply)

    row.addWidget(btn)
    row.addStretch()

    note_txt = (
        "(will re-run full decompression simulation — all tabs update)" if resim
        else "(defaults from Settings — changes here are visual only, not a re-simulation)"
    )
    note = QLabel(note_txt)
    note.setStyleSheet("font-size:8px; color:#555; font-style:italic;")
    row.addWidget(note)

    return row


# ── Compartment Explorer ─────────────────────────────────────────────────────

class _CompartmentExplorer(QWidget):
    """
    Step-by-step calculation window for a chosen compartment at a chosen time.
    Shows tissue parameters, loading state, M-value, saturation, and GF maths.
    """
    def __init__(self, timeline, surface_mv, stops, gf_low, gf_high,
                 first_stop, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Compartment Calculation Explorer")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(780, 680)

        self._timeline   = timeline
        self._surface_mv = surface_mv
        self._stops      = stops or []
        self._gf_low     = gf_low
        self._gf_high    = gf_high
        self._first_stop = first_stop

        times = [e[0] for e in timeline]
        self._t_max = times[-1] if times else 1.0

        vl = QVBoxLayout(self)
        vl.setContentsMargins(12, 12, 12, 12)
        vl.setSpacing(8)

        # ── Controls ──────────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Compartment:"))
        self._comp_cb = QComboBox()
        for i in range(16):
            self._comp_cb.addItem(f"C{i+1}  ({_N2_HT[i]} min half-time)")
        ctrl.addWidget(self._comp_cb)
        ctrl.addSpacing(20)
        ctrl.addWidget(QLabel("Time:"))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(max(0, len(timeline) - 1))
        self._slider.setValue(len(timeline) // 2)
        ctrl.addWidget(self._slider, 1)
        self._time_lbl = QLabel()
        self._time_lbl.setFixedWidth(70)
        ctrl.addWidget(self._time_lbl)
        vl.addLayout(ctrl)

        # ── GF override fields ────────────────────────────────────────────────
        gf_row = QHBoxLayout()
        gf_row.addWidget(QLabel("GF Low %:"))
        self._gf_lo_le = QLineEdit(str(int(gf_low * 100)))
        self._gf_lo_le.setFixedWidth(44)
        gf_row.addWidget(self._gf_lo_le)
        gf_row.addWidget(QLabel("GF High %:"))
        self._gf_hi_le = QLineEdit(str(int(gf_high * 100)))
        self._gf_hi_le.setFixedWidth(44)
        gf_row.addWidget(self._gf_hi_le)
        gf_row.addStretch()
        vl.addLayout(gf_row)

        # ── Separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        vl.addWidget(sep)

        # ── Results area (monospaced grid) ────────────────────────────────────
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(4)
        vl.addLayout(self._grid)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        vl.addWidget(sep2)

        # ── Mini chart: pt/M_depth (classic) ─────────────────────────────────
        self._chart = _ExplorerChart(timeline, surface_mv, stops,
                                     gf_low, gf_high, first_stop)
        self._chart.setMinimumHeight(140)
        vl.addWidget(self._chart, 1)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        vl.addWidget(sep3)

        # ── Mini chart: normalized (0=equil, 100%=M-value) ───────────────────
        vl.addWidget(QLabel(
            "Normalized scale: 0% = equilibrium (no off-gassing drive)  |  "
            "100% = Bühlmann M-value  |  GF% lines are exact on this scale"
        ))
        self._norm_chart = _ExplorerNormChart(timeline, stops, gf_low, gf_high,
                                              first_stop)
        self._norm_chart.setMinimumHeight(140)
        vl.addWidget(self._norm_chart, 1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        btn = QPushButton("Close"); btn.setFixedWidth(80)
        btn.clicked.connect(self.close)
        close_row.addWidget(btn)
        vl.addLayout(close_row)

        self._comp_cb.currentIndexChanged.connect(self._update)
        self._slider.valueChanged.connect(self._update)
        self._gf_lo_le.textChanged.connect(self._update)
        self._gf_hi_le.textChanged.connect(self._update)
        self._update()

    def _current_gf(self):
        try:
            lo = max(1, min(100, int(self._gf_lo_le.text()))) / 100.0
            hi = max(1, min(100, int(self._gf_hi_le.text()))) / 100.0
        except ValueError:
            lo, hi = self._gf_low, self._gf_high
        return lo, hi

    def _update(self):
        idx  = self._slider.value()
        if idx >= len(self._timeline):
            return
        comp = self._comp_cb.currentIndex()
        gf_lo, gf_hi = self._current_gf()

        entry   = self._timeline[idx]
        rt      = entry[0]
        depth   = entry[1]
        snap    = entry[2]
        self._time_lbl.setText(f"{rt:.1f} min")

        p_n2, p_he = snap[comp] if comp < len(snap) else (0.0, 0.0)
        pt = p_n2 + p_he

        _, N2_a, N2_b, _, He_a, He_b = TISSUES[comp]
        if pt > 0:
            a = (N2_a * p_n2 + He_a * p_he) / pt
            b = (N2_b * p_n2 + He_b * p_he) / pt
        else:
            a, b = N2_a, N2_b

        p_amb_depth   = P_SURF + depth * WATER_DENSITY / 10.0
        p_amb_surface = P_SURF

        mv_depth   = a + p_amb_depth   / b
        mv_surface = a + p_amb_surface / b

        sat_depth   = pt / mv_depth   if mv_depth   > 0 else 0.0
        sat_surface = pt / mv_surface if mv_surface > 0 else 0.0

        # which compartment is leading right now?
        fracs_depth = _sat_fractions(snap, depth, False)
        leading_i   = fracs_depth.index(max(fracs_depth))
        is_leading  = (leading_i == comp)

        # GF interpolation at current depth
        if self._first_stop > 0:
            ratio = max(0.0, min(1.0, depth / self._first_stop))
            gf_now = gf_hi + (gf_lo - gf_hi) * ratio
        else:
            gf_now = gf_hi

        # GF ceiling threshold as fraction of depth M-value
        gf_threshold = gf_now + p_amb_depth * (1.0 - gf_now) / mv_depth if mv_depth > 0 else gf_now
        # GF ceiling pressure (minimum allowable ambient pressure)
        if b > 0:
            denom = gf_now / b + 1.0 - gf_now
            gf_ceil_bar = (pt - a * gf_now) / denom if denom != 0 else 0.0
            gf_ceil_depth = max(0.0, (gf_ceil_bar - P_SURF) * 10.0 / WATER_DENSITY)
        else:
            gf_ceil_bar = 0.0
            gf_ceil_depth = 0.0

        above_gf = sat_depth > gf_threshold

        # ── Rebuild grid ──────────────────────────────────────────────────────
        # clear old widgets
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        def row(r, label, value, note="", color=None):
            lbl = QLabel(label)
            lbl.setStyleSheet("font-family:monospace; font-size:12px; color:#555;")
            val = QLabel(value)
            val.setStyleSheet(
                f"font-family:monospace; font-size:13px; font-weight:bold;"
                f"{'color:' + color + ';' if color else ''}"
            )
            nt  = QLabel(note)
            nt.setStyleSheet("font-size:11px; color:#777; font-style:italic;")
            self._grid.addWidget(lbl, r, 0)
            self._grid.addWidget(val, r, 1)
            self._grid.addWidget(nt,  r, 2)

        He_ht = TISSUES[comp][3]
        r = 0
        row(r, "── COMPARTMENT PARAMETERS ──────────────────", "", ""); r+=1
        row(r, f"  C{comp+1} N₂ half-time",  f"{_N2_HT[comp]} min",
            "time for 50% equilibration with N₂"); r+=1
        row(r, f"  C{comp+1} He half-time",  f"{He_ht:.2f} min",
            "He diffuses ~2.65× faster than N₂"); r+=1
        row(r, "  N₂ Bühlmann a",  f"{N2_a:.4f} bar",  "intercept coefficient"); r+=1
        row(r, "  N₂ Bühlmann b",  f"{N2_b:.4f}",      "slope coefficient (1/b = M-value slope)"); r+=1
        row(r, "  He Bühlmann a",  f"{He_a:.4f} bar"); r+=1
        row(r, "  He Bühlmann b",  f"{He_b:.4f}"); r+=1
        if pt > 0 and p_n2 > 0 and p_he > 0:
            row(r, "  Mixed a (weighted)", f"{a:.4f} bar",
                f"= (N₂a×pN₂ + Hea×pHe) / pt"); r+=1
            row(r, "  Mixed b (weighted)", f"{b:.4f}"); r+=1

        row(r, "── TISSUE LOADING ───────────────────────────", "", ""); r+=1
        row(r, "  Depth",          f"{depth:.1f} m"); r+=1
        row(r, "  P_amb (depth)",  f"{p_amb_depth:.4f} bar",
            f"= {P_SURF} + {depth:.1f} × {WATER_DENSITY}/10"); r+=1
        row(r, "  pN₂",  f"{p_n2:.4f} bar",  "N₂ partial pressure in tissue"); r+=1
        row(r, "  pHe",  f"{p_he:.4f} bar",  "He partial pressure in tissue"); r+=1
        row(r, "  pt = pN₂ + pHe",f"{pt:.4f} bar", "total inert gas pressure"); r+=1

        row(r, "── M-VALUE CALCULATION ──────────────────────", "", ""); r+=1
        row(r, "  M_depth = a + P_amb/b",
            f"{a:.4f} + {p_amb_depth:.4f}/{b:.4f} = {mv_depth:.4f} bar",
            "max allowed pt at this depth"); r+=1
        row(r, "  M_surface = a + P_surf/b",
            f"{a:.4f} + {p_amb_surface:.4f}/{b:.4f} = {mv_surface:.4f} bar",
            "max allowed pt at surface"); r+=1
        row(r, "  Sat (depth M-val) = pt/M_depth",
            f"{pt:.4f} / {mv_depth:.4f} = {sat_depth*100:.1f}%",
            color="#cc4400" if sat_depth > 1.0 else ("#888800" if sat_depth > 0.85 else "#006600")); r+=1
        row(r, "  Sat (surface M-val) = pt/M_surf",
            f"{pt:.4f} / {mv_surface:.4f} = {sat_surface*100:.1f}%",
            color="#cc0000" if sat_surface > 1.0 else None); r+=1
        row(r, "  Leading compartment?",
            f"{'✓ YES — C' + str(comp+1) + ' is controlling' if is_leading else '✗ NO — C' + str(leading_i+1) + ' is leading'}",
            color="#006600" if is_leading else "#555555"); r+=1

        row(r, "── GRADIENT FACTOR CALCULATION ──────────────", "", ""); r+=1
        row(r, f"  GF Low / GF High",
            f"{int(gf_lo*100)}% / {int(gf_hi*100)}%"); r+=1
        row(r, f"  First stop depth", f"{self._first_stop:.0f} m",
            "GF_low applied here, GF_high at surface"); r+=1
        row(r, f"  GF at {depth:.0f} m",
            f"{gf_hi:.2f} + ({gf_lo:.2f} − {gf_hi:.2f}) × {depth:.1f}/{self._first_stop:.1f} = {gf_now:.3f}"
            if self._first_stop > 0 else f"{gf_hi:.3f} (no deco stops yet)",
            "linear interpolation between GFL and GFH"); r+=1
        row(r, "  GF threshold (pt/M_depth)",
            f"{gf_now:.3f} + {p_amb_depth:.4f}×(1−{gf_now:.3f})/{mv_depth:.4f} = {gf_threshold:.3f}  ({gf_threshold*100:.1f}%)",
            "= GF + P_amb×(1−GF)/M_depth"); r+=1
        row(r, "  GF ceiling pressure",
            f"{gf_ceil_bar:.4f} bar  →  {gf_ceil_depth:.1f} m",
            "must not ascend above this depth right now"); r+=1
        row(r, "  Status",
            f"{'⚠ ABOVE GF LIMIT — deco required' if above_gf else '✓ Within GF limit'}",
            color="#cc0000" if above_gf else "#006600"); r+=1

        # update mini charts
        self._chart.set_state(comp, idx, gf_lo, gf_hi)
        self._norm_chart.set_state(comp, idx, gf_lo, gf_hi)


class _ExplorerChart(QWidget):
    """Mini chart showing one compartment's saturation over the full dive + GF corridor."""
    PAD_L = 44; PAD_R = 8; PAD_T = 8; PAD_B = 24

    def __init__(self, timeline, surface_mv, stops, gf_low, gf_high,
                 first_stop, parent=None):
        super().__init__(parent)
        self._timeline    = timeline
        self._surface_mv  = surface_mv
        self._stops       = stops or []
        self._gf_low      = gf_low
        self._gf_high     = gf_high
        self._first_stop  = first_stop
        self._comp        = 0
        self._cursor_idx  = 0
        times = [e[0] for e in timeline]
        self._t_max = times[-1] if times else 1.0
        self.setMinimumSize(400, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_state(self, comp, cursor_idx, gf_low, gf_high):
        self._comp       = comp
        self._cursor_idx = cursor_idx
        self._gf_low     = gf_low
        self._gf_high    = gf_high
        self.update()

    def paintEvent(self, _):
        if not self._timeline:
            return
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        gw   = W - self.PAD_L - self.PAD_R
        ph   = H - self.PAD_T - self.PAD_B

        p.fillRect(self.PAD_L, self.PAD_T, gw, ph, QColor(22, 27, 34))

        n   = len(self._timeline)
        comp = self._comp

        # compute saturation for this compartment over time
        sats = []
        for entry in self._timeline:
            rt, depth, snap = entry[0], entry[1], entry[2]
            if comp < len(snap):
                p_n2, p_he = snap[comp]
                pt = p_n2 + p_he
                _, N2_a, N2_b, _, He_a, He_b = TISSUES[comp]
                if pt > 0:
                    a = (N2_a * p_n2 + He_a * p_he) / pt
                    b = (N2_b * p_n2 + He_b * p_he) / pt
                else:
                    a, b = N2_a, N2_b
                p_amb = P_SURF if self._surface_mv else (P_SURF + depth * WATER_DENSITY / 10.0)
                mv = a + p_amb / b
                sats.append(pt / mv if mv > 0 else 0.0)
            else:
                sats.append(0.0)

        y_max = max(1.2, max(sats) * 1.05) if sats else 1.2
        for nice in (1.2, 1.5, 2.0, 3.0):
            if y_max <= nice:
                y_max = nice
                break

        def tx(t): return self.PAD_L + int(t / self._t_max * gw)
        def fy(v): return self.PAD_T + ph - int(min(v, y_max) / y_max * ph)

        # 100% reference line
        p.setPen(QPen(QColor(220, 60, 60), 1, Qt.PenStyle.DashLine))
        p.drawLine(self.PAD_L, fy(1.0), self.PAD_L + gw, fy(1.0))

        # GF corridor for this compartment
        for i in range(n - 1):
            depth = self._timeline[i][1]
            snap  = self._timeline[i][2]
            if comp < len(snap):
                p_n2, p_he = snap[comp]
                pt = p_n2 + p_he
                _, N2_a, N2_b, _, He_a, He_b = TISSUES[comp]
                a_c = (N2_a * p_n2 + He_a * p_he) / pt if pt > 0 else N2_a
                b_c = (N2_b * p_n2 + He_b * p_he) / pt if pt > 0 else N2_b
                p_amb = P_SURF + depth * WATER_DENSITY / 10.0
                mv_d  = a_c + p_amb / b_c if b_c > 0 else 1.0
                if self._first_stop > 0:
                    ratio = max(0.0, min(1.0, depth / self._first_stop))
                    gf = self._gf_high + (self._gf_low - self._gf_high) * ratio
                else:
                    gf = self._gf_high
                thresh = gf + p_amb * (1.0 - gf) / mv_d if mv_d > 0 else gf
                x0 = tx(self._timeline[i][0])
                x1 = tx(self._timeline[i+1][0])
                y0 = fy(thresh)
                p.setPen(QPen(QColor(255, 120, 60), 1, Qt.PenStyle.DashLine))
                p.drawLine(x0, y0, x1, y0)

        # saturation line
        for i in range(n - 1):
            x0 = tx(self._timeline[i][0])
            x1 = tx(self._timeline[i+1][0])
            y0 = fy(sats[i])
            y1 = fy(sats[i+1])
            p.setPen(QPen(_frac_color_new(sats[i]), 2))
            p.drawLine(x0, y0, x1, y1)

        # cursor
        if 0 <= self._cursor_idx < n:
            cx = tx(self._timeline[self._cursor_idx][0])
            p.setPen(QPen(QColor(255, 255, 255, 180), 1))
            p.drawLine(cx, self.PAD_T, cx, self.PAD_T + ph)

        # Y axis ticks
        p.setPen(QColor(160, 160, 160))
        p.setFont(QFont("Arial", 7))
        for ref in (0.0, 0.5, 1.0):
            if ref <= y_max:
                y = fy(ref)
                p.drawText(0, y - 6, self.PAD_L - 3, 12,
                           Qt.AlignmentFlag.AlignRight, f"{int(ref*100)}%")

        # title
        p.setPen(QColor(200, 200, 200))
        p.setFont(QFont("Arial", 8))
        mv_txt = "Surface M-val" if self._surface_mv else "Depth M-val"
        p.drawText(self.PAD_L + 4, self.PAD_T + 10,
                   f"C{comp+1} saturation ({mv_txt})  — orange dashed = GF corridor")

        # time axis
        _draw_time_axis_fn(p, self.PAD_L, gw, self._t_max, self.PAD_T + ph)
        p.end()


class _ExplorerNormChart(QWidget):
    """
    Normalized saturation chart: 0% = equilibrium, 100% = Bühlmann M-value.
    Formula: norm = (pt - P_equil) / (M_depth - P_equil)
    where P_equil = P_amb × inspired_inert_fraction ≈ P_amb × (pt / M_depth at equil).
    We approximate P_equil as the minimum pt seen in the timeline for that compartment
    (i.e. the most equilibrated state = surface pre-dive).
    More precisely: P_equil = P_amb × (1 - fO2_inspired), but we don't have inspired
    gas here, so we use P_amb as the ceiling for the off-gassing drive.
    """
    PAD_L = 44; PAD_R = 50; PAD_T = 8; PAD_B = 24

    def __init__(self, timeline, stops, gf_low, gf_high, first_stop, parent=None):
        super().__init__(parent)
        self._timeline   = timeline
        self._stops      = stops or []
        self._gf_low     = gf_low
        self._gf_high    = gf_high
        self._first_stop = first_stop
        self._comp       = 0
        self._cursor_idx = 0
        times = [e[0] for e in timeline]
        self._t_max = times[-1] if times else 1.0
        self.setMinimumSize(400, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_state(self, comp, cursor_idx, gf_low, gf_high):
        self._comp       = comp
        self._cursor_idx = cursor_idx
        self._gf_low     = gf_low
        self._gf_high    = gf_high
        self.update()

    def paintEvent(self, _):
        if not self._timeline:
            return
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = self.width(), self.height()
        gw   = W - self.PAD_L - self.PAD_R
        ph   = H - self.PAD_T - self.PAD_B
        n    = len(self._timeline)
        comp = self._comp

        p.fillRect(self.PAD_L, self.PAD_T, gw, ph, QColor(22, 27, 34))

        def tx(t): return self.PAD_L + int(t / self._t_max * gw)
        def fy(v): return self.PAD_T + ph - int(max(0.0, min(v, 1.5)) / 1.5 * ph)

        # compute normalized saturation for each timepoint
        norms = []
        gf_thresholds = []
        for i, entry in enumerate(self._timeline):
            rt, depth, snap = entry[0], entry[1], entry[2]
            if comp >= len(snap):
                norms.append(0.0); gf_thresholds.append(0.0); continue
            p_n2, p_he = snap[comp]
            pt = p_n2 + p_he
            _, N2_a, N2_b, _, He_a, He_b = TISSUES[comp]
            if pt > 0:
                a = (N2_a * p_n2 + He_a * p_he) / pt
                b = (N2_b * p_n2 + He_b * p_he) / pt
            else:
                a, b = N2_a, N2_b
            p_amb  = P_SURF + depth * WATER_DENSITY / 10.0
            m_d    = a + p_amb / b if b > 0 else 1.0
            # equilibrium floor: tissue in perfect equilibrium with inspired gas
            # at this depth = P_amb (conservative upper bound for loading)
            p_equil = p_amb
            window  = m_d - p_equil
            norm    = (pt - p_equil) / window if window > 0 else 0.0
            norms.append(norm)
            # GF threshold on this scale = exactly GF%
            if self._first_stop > 0:
                ratio = max(0.0, min(1.0, depth / self._first_stop))
                gf = self._gf_high + (self._gf_low - self._gf_high) * ratio
            else:
                gf = self._gf_high
            gf_thresholds.append(gf)

        # reference lines at 0%, GF_low, GF_high, 100%
        for ref, lbl, col in (
            (0.0,           "0% (equil)",  QColor(80, 180, 255)),
            (self._gf_low,  f"GF_low {int(self._gf_low*100)}%", QColor(255, 180, 60)),
            (self._gf_high, f"GF_hi {int(self._gf_high*100)}%", QColor(255, 120, 60)),
            (1.0,           "100% M-val",  QColor(220, 60, 60)),
        ):
            y = fy(ref)
            p.setPen(QPen(col, 1, Qt.PenStyle.DashLine))
            p.drawLine(self.PAD_L, y, self.PAD_L + gw, y)
            p.setPen(col)
            p.setFont(QFont("Arial", 7))
            p.drawText(self.PAD_L + gw + 2, y - 5, self.PAD_R - 4, 10,
                       Qt.AlignmentFlag.AlignLeft, lbl)

        # GF corridor line (interpolated)
        p.setPen(QPen(QColor(255, 120, 60), 1, Qt.PenStyle.DashLine))
        for i in range(n - 1):
            x0 = tx(self._timeline[i][0])
            x1 = tx(self._timeline[i+1][0])
            p.drawLine(x0, fy(gf_thresholds[i]), x1, fy(gf_thresholds[i+1]))

        # normalized saturation line
        for i in range(n - 1):
            x0 = tx(self._timeline[i][0])
            x1 = tx(self._timeline[i+1][0])
            y0 = fy(norms[i])
            y1 = fy(norms[i+1])
            # colour: below 0 = loading (cyan), above 0 = off-gassing (gradient)
            col = _frac_color_new(max(0.0, norms[i])) if norms[i] >= 0 else QColor(80, 180, 255)
            p.setPen(QPen(col, 2))
            p.drawLine(x0, y0, x1, y1)

        # cursor
        if 0 <= self._cursor_idx < n:
            cx = tx(self._timeline[self._cursor_idx][0])
            p.setPen(QPen(QColor(255, 255, 255, 180), 1))
            p.drawLine(cx, self.PAD_T, cx, self.PAD_T + ph)

        # Y axis ticks
        p.setPen(QColor(160, 160, 160))
        p.setFont(QFont("Arial", 7))
        for ref in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = fy(ref)
            p.drawText(0, y - 6, self.PAD_L - 3, 12,
                       Qt.AlignmentFlag.AlignRight, f"{int(ref*100)}%")

        # title
        p.setPen(QColor(200, 200, 200))
        p.setFont(QFont("Arial", 8))
        p.drawText(self.PAD_L + 4, self.PAD_T + 10,
                   f"C{comp+1} normalized  (0% = equilibrium, 100% = M-value)  "
                   f"— GF lines are exact on this scale")

        _draw_time_axis_fn(p, self.PAD_L, gw, self._t_max, self.PAD_T + ph)
        p.end()


# ── Main window ───────────────────────────────────────────────────────────────

class TissueHeatmapWindow(QWidget):
    """
    Popup with tabs covering different views of tissue saturation.
    Pass resimulate_fn(gf_low, gf_high) → (timeline, stops, phase_list) to
    enable full re-simulation when the user changes GF values.
    """

    def __init__(self, parent, timeline: list, surface_mv: bool = True,
                 title: str = "Tissue Saturation Heatmap", stops: list = None,
                 phase_list: list = None, gf_low: float = 0.30, gf_high: float = 0.80,
                 resimulate_fn=None, first_stop_depth: float = 0.0):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1000, 620)

        self._surface_mv    = surface_mv
        self._title         = title
        self._resimulate_fn = resimulate_fn

        vl = QVBoxLayout(self)
        vl.setContentsMargins(8, 8, 8, 8)
        vl.setSpacing(4)

        self._hdr = QLabel()
        self._hdr.setStyleSheet("font-size:11px; padding:2px 4px;")
        vl.addWidget(self._hdr)

        self._tabs = QTabWidget()
        vl.addWidget(self._tabs, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        vl.addLayout(btn_row)

        self._first_stop_depth = first_stop_depth
        self._build_tabs(timeline, stops or [], phase_list or [], gf_low, gf_high)

    # ── GF re-simulation callback ─────────────────────────────────────────────

    def _on_gf_apply(self, gf_low: float, gf_high: float):
        """Called by the GF Apply button — re-runs simulation and rebuilds all tabs."""
        try:
            result = self._resimulate_fn(gf_low, gf_high)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Simulation Error", str(e))
            return
        if result is None:
            return
        timeline, stops, phase_list = result
        self._build_tabs(timeline, stops, phase_list, gf_low, gf_high)

    # ── Tab builder (called on init and after re-simulation) ─────────────────

    def _build_tabs(self, timeline: list, stops: list, phase_list: list,
                    gf_low: float, gf_high: float):
        # remember which tab was active so we can restore it
        cur_idx = self._tabs.currentIndex()

        # remove old tab widgets cleanly
        while self._tabs.count():
            w = self._tabs.widget(0)
            self._tabs.removeTab(0)
            if w is not None:
                w.deleteLater()

        # update header
        self._hdr.setText(
            f"<b>{self._title}</b> &nbsp;|&nbsp; "
            f"{'Surface' if self._surface_mv else 'Depth'} M-values &nbsp;|&nbsp; "
            f"{len(timeline)} time points"
            + (f" &nbsp;|&nbsp; {len(stops)} deco stops" if stops else "")
            + (f" &nbsp;|&nbsp; GF {int(gf_low*100)}/{int(gf_high*100)}" if stops else "")
        )

        surface_mv = self._surface_mv
        tabs       = self._tabs
        _mv_tag    = " · Depth M-val" if not surface_mv else " · Surface M-val"

        # ── Tab 1: saturation heatmap ─────────────────────────────────────────
        # Put heatmap + note together in one container inside the scroll area
        # so the scroll area gets all available height and no external widgets
        # steal space from it.
        tab1        = QWidget()
        t1l         = QVBoxLayout(tab1)
        t1l.setContentsMargins(4, 4, 4, 4)
        scroll1     = QScrollArea(); scroll1.setWidgetResizable(True)
        _hm_container = QWidget()
        _hm_vl        = QVBoxLayout(_hm_container)
        _hm_vl.setContentsMargins(0, 0, 0, 0)
        _hm_vl.setSpacing(4)
        _hm_vl.addWidget(_HeatmapWidget(timeline, surface_mv, stops=stops,
                                        phase_list=phase_list))
        note1 = QLabel(
            "SATURATION HEATMAP — Time runs left→right; the 16 Bühlmann tissue compartments run top→bottom "
            "(C1 = 4 min half-time at top, C16 = 635 min at bottom).  "
            "Each cell colour shows how loaded that compartment is relative to its M-value (maximum allowed "
            "inert gas pressure before bubble formation risk): "
            "blue = well within limits, green = moderate, yellow = approaching limit, "
            "orange/red = near or at M-value, purple = over M-value (theoretical supersaturation).  "
            "The depth strip at the top gives context for when you were deep vs shallow.  "
            "White dashed vertical lines mark deco stop times.  "
            "Watch how colour spreads downward (into slower tissues) over time — that is gas migrating "
            "from fast tissues into slow ones during the dive."
        )
        note1.setStyleSheet("font-size:13px; color:#000; padding:24px 4px 6px 4px;")
        note1.setWordWrap(True)
        _hm_vl.addWidget(note1)
        _hm_vl.addStretch()
        scroll1.setWidget(_hm_container)
        t1l.addWidget(scroll1, 1)
        _gf_apply1 = self._on_gf_apply if self._resimulate_fn else None
        t1l.addLayout(_gf_input_bar(None, gf_low, gf_high,
                                    label="Recalculate with GF:",
                                    on_apply=_gf_apply1))
        tabs.addTab(tab1, "Saturation heatmap" + _mv_tag)

        # Derive first stop depth for the GF corridor interpolation.
        # IMPORTANT: the simulation interpolates GF using *its own* first_stop,
        # which is the raw ceiling rounded up to the stop interval — even if that
        # stop has 0 minutes of wait time and therefore does NOT appear in `stops`.
        # Using max(stops depth) would give a shallower value whenever early stops
        # have zero duration, making the GF corridor appear too low and the tissue
        # look like it exceeds the limit.  Instead we recover the simulation's
        # first_stop from the phase_list: it is the "transit" phase that immediately
        # follows the last "bottom" phase (= arrival at the first deco stop).
        _first_stop    = max((s.get("depth", 0) for s in stops), default=0.0)
        _first_stop_rt = min(
            (s.get("runtime", 0) - s.get("time", 0) for s in stops), default=0.0
        ) if stops else 0.0
        if phase_list:
            _last_bottom_idx = -1
            for _i, _pe in enumerate(phase_list):
                if _pe[2] == "bottom":
                    _last_bottom_idx = _i
            if _last_bottom_idx >= 0:
                for _pe in phase_list[_last_bottom_idx + 1:]:
                    if _pe[2] == "transit":
                        # This transit entry is the arrival at the simulation's
                        # first_stop depth — use both depth and runtime from it.
                        _first_stop    = _pe[1]
                        _first_stop_rt = _pe[0]
                        break

        # ── Tab 3: leading compartment ────────────────────────────────────────
        tab3 = QWidget()
        t3l  = QVBoxLayout(tab3)
        t3l.setContentsMargins(4, 4, 4, 4)
        t3l.setSpacing(4)

        _lead_top = _LeadingTopWidget(timeline, surface_mv, stops=stops)
        t3l.addWidget(_lead_top, 1)
        note3_top = QLabel(
            "LEADING COMPARTMENT — At any moment during a dive, one tissue compartment is closest "
            "to its M-value limit; this is the 'leading' or 'controlling' compartment that dictates "
            "when you must stop or slow your ascent.  "
            "Y-axis = C1 (4 min half-time, fastest) to C16 (635 min, slowest).  "
            "On descent and at depth, fast compartments (C1–C4) fill quickly and lead.  "
            "As the dive progresses, control passes to progressively slower compartments.  "
            "During long deco, the very slow tissues (C12–C16) become limiting — they saturate "
            "slowly but also off-gas very slowly, requiring long shallow stops."
        )
        note3_top.setStyleSheet("font-size:13px; color:#000;")
        note3_top.setWordWrap(True)
        t3l.addWidget(note3_top)

        _lead_bot = _LeadingBottomWidget(timeline, surface_mv, stops=stops,
                                         gf_low=gf_low, gf_high=gf_high,
                                         first_stop=_first_stop,
                                         first_stop_rt=_first_stop_rt)
        t3l.addWidget(_lead_bot, 1)
        _gf_apply = self._on_gf_apply if self._resimulate_fn else None
        t3l.addLayout(_gf_input_bar(_lead_bot, gf_low, gf_high,
                                    label="GF corridor line:",
                                    on_apply=_gf_apply))
        note3_bot_txt = (
            "LEADING COMPARTMENT SATURATION — The saturation fraction (pt / M-value) of whichever "
            "compartment is currently leading.  Colour follows the heatmap scale: green = safe, "
            "yellow = approaching limit, orange/red = at or near M-value.  "
            "The orange dashed line is the GF corridor — the red shaded zone above it is the 'danger zone' "
            "where saturation exceeds the GF limit.  The diver should stay below the corridor during ascent.  "
            "Grey dotted = corridor during bottom/descent (not enforced there).  "
            "A flat line near 100% during deco means the algorithm is holding that compartment "
            "right at its GF limit — working as intended.  " + (
            "Change GFL/GFH above and press Apply to re-run the full simulation with new GF values — "
            "all tabs will update with the recalculated dive."
            if self._resimulate_fn else
            "Change GFL/GFH above and press Apply to visualise a different GF corridor (visual only)."
            )
        )
        note3_bot = QLabel(note3_bot_txt)
        note3_bot.setStyleSheet("font-size:13px; color:#000;")
        note3_bot.setWordWrap(True)
        t3l.addWidget(note3_bot)

        # ── Normalised chart (0% = P_amb, 100% = M-value) ────────────────────
        _sep3 = QFrame(); _sep3.setFrameShape(QFrame.Shape.HLine)
        _sep3.setStyleSheet("color:#555;"); t3l.addWidget(_sep3)

        _lead_norm = _LeadingNormWidget(timeline, surface_mv, stops=stops,
                                        gf_low=gf_low, gf_high=gf_high,
                                        first_stop=_first_stop,
                                        first_stop_rt=_first_stop_rt)
        t3l.addWidget(_lead_norm, 1)
        _gf_apply_norm = self._on_gf_apply if self._resimulate_fn else None
        t3l.addLayout(_gf_input_bar(_lead_norm, gf_low, gf_high,
                                    label="GF corridor line:",
                                    on_apply=_gf_apply_norm))

        note3_norm = QLabel(
            "NORMALISED SATURATION — Same leading compartment data rescaled so that "
            "the ambient pressure (P_amb) sits at 0% and the Bühlmann M-value sits at 100%.  "
            "On this scale the GF corridor lines fall at exactly GF_low% and GF_high%, "
            "making it easy to read how close the tissue is to the allowed limit.  "
            "Negative values = tissue is still loading (below ambient equilibrium).  "
            "Values above 100% = tissue exceeds M-value (should not happen during a safe dive)."
        )
        note3_norm.setStyleSheet("font-size:13px; color:#000;")
        note3_norm.setWordWrap(True)
        t3l.addWidget(note3_norm)

        # Explore button
        _explore_btn = QPushButton("🔬  Explore Compartment Calculations…")
        _explore_btn.setStyleSheet("font-size:12px; padding:4px 12px;")
        def _open_explorer(_checked=False, _tl=timeline, _smv=surface_mv, _st=stops,
                           _gfl=gf_low, _gfh=gf_high, _fs=_first_stop):
            try:
                w = _CompartmentExplorer(_tl, _smv, _st, _gfl, _gfh, _fs, parent=self)
                w.show()
            except Exception as _e:
                import traceback
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(None, "Explorer Error", traceback.format_exc())
        _explore_btn.clicked.connect(_open_explorer)
        _btn_row = QHBoxLayout()
        _btn_row.addWidget(_explore_btn)
        _btn_row.addStretch()
        t3l.addLayout(_btn_row)
        t3l.addStretch()
        tabs.addTab(tab3, "Leading compartment" + _mv_tag)



        # ── Tab 5: animated radar ─────────────────────────────────────────────

        # ── Tab 7: animated bar chart ─────────────────────────────────────────
        tab7 = QWidget()
        t7l  = QVBoxLayout(tab7)
        t7l.setContentsMargins(4, 4, 4, 4)
        t7l.addWidget(_BarTab(timeline, surface_mv, gf_low=gf_low, gf_high=gf_high,
                              first_stop_depth=self._first_stop_depth), 1)
        _gf_apply7 = self._on_gf_apply if self._resimulate_fn else None
        t7l.addLayout(_gf_input_bar(None, gf_low, gf_high,
                                    label="Recalculate with GF:",
                                    on_apply=_gf_apply7))
        tabs.addTab(tab7, "Bar chart (animated)" + _mv_tag)

        # restore previous tab position if possible
        if 0 <= cur_idx < tabs.count():
            tabs.setCurrentIndex(cur_idx)
