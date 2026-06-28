"""
gas_blending_tab_qt.py
======================
Gas Blending tab for TechDivePlanner's Gas Planner, driven by the
`gasblend` engine (GERG-2008 via CoolProp).

Style matches gas_calc_tab_qt.py: QLineEdit inputs + manual parsing,
QTableWidget with per-cell setBackground, group-box layout, and a custom
QPainter chart (no matplotlib in the Gas Planner area).

Features: real-gas partial-pressure blend, fill sequence, diving limits,
best-mix-for-depth, and a Z-factor vs pressure plot.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QPolygonF
from PyQt6.QtWidgets import (
    QWidget, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QRadioButton, QButtonGroup, QGroupBox, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QHBoxLayout, QVBoxLayout, QGridLayout,
    QSizePolicy,
)

from gasblend import (
    Blender, CylinderState, get_eos, limits, label, normalise,
    AIR, PURE_O2,
)

# --- palette (mirrors gas_calc_tab_qt.py) --------------------------------
CLR_INPUT = "#ffe0e0"
CLR_RESULT = "#dff0df"
CLR_OK = "#b8f0b8"
CLR_CAUTION = "#fff0a0"
CLR_WARN = "#ffb0b0"


def _inp(default: str = "", width: int = 90) -> QLineEdit:
    w = QLineEdit()
    w.setText(str(default))
    w.setAlignment(Qt.AlignmentFlag.AlignRight)
    w.setStyleSheet(f"background-color: {CLR_INPUT};")
    w.setFixedWidth(width)
    return w


def _flt(text: str, default: float = 0.0) -> float:
    try:
        return float(str(text).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def _parse_mix(text: str):
    """'18/45' -> (0.18, 0.45); '32' -> (0.32, 0.0); '100/00' -> (1.0, 0.0).
    Returns (fO2, fHe) as fractions. Raises ValueError on garbage."""
    t = str(text).strip().replace(" ", "").replace(",", ".")
    if not t:
        raise ValueError("empty mix")
    if "/" in t:
        a, b = t.split("/", 1)
        o2 = float(a) / 100.0
        he = (float(b) / 100.0) if b not in ("", ".") else 0.0
    else:
        o2 = float(t) / 100.0
        he = 0.0
    if o2 < 0 or he < 0 or o2 + he > 1.0001:
        raise ValueError("O2 + He must be between 0 and 100 %")
    return o2, he


def _mix_dict(o2: float, he: float) -> dict:
    return {"O2": o2, "He": he, "N2": max(0.0, 1.0 - o2 - he)}


def _fmt_mix(o2: float, he: float) -> str:
    return f"{round(o2 * 100)}/{round(he * 100):02d}"


# ======================================================================= #
#  Z-factor vs pressure chart (custom QPainter widget, _ICDChartWidget-style)
# ======================================================================= #
class _ZChartWidget(QWidget):
    """Draws Z (compressibility factor) vs pressure for the blended mix."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points: list = []        # list of (P_bar, Z)
        self.title = "Z-factor"
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMinimumSize(220, 160)

    def set_data(self, points, title="Z-factor"):
        self.points = list(points)
        self.title = title
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor("#ffffff"))
        ml, mr, mt, mb = 56, 14, 22, 34
        x0, y0 = ml, H - mb
        x1, y1 = W - mr, mt
        if x1 <= x0 or y0 <= y1:
            return

        # axes frame
        p.setPen(QPen(QColor("#888888"), 1))
        p.drawLine(x0, y0, x1, y0)
        p.drawLine(x0, y0, x0, y1)

        f = QFont(); f.setPointSize(8); p.setFont(f)
        p.setPen(QColor("#333333"))
        p.drawText(int((x0 + x1) / 2 - 26), H - 8, "Pressure [bar]")
        p.save(); p.translate(14, int((y0 + y1) / 2 + 18)); p.rotate(-90)
        p.drawText(0, 0, "Z [-]"); p.restore()

        if not self.points:
            p.setPen(QColor("#999999"))
            p.drawText(int((x0 + x1) / 2 - 80), int((y0 + y1) / 2),
                       "Press «Blend» to show Z curve")
            return

        pmax = max(pp for pp, _ in self.points) or 1.0
        zs = [zz for _, zz in self.points]
        zmin, zmax = min(zs), max(zs)
        if zmax - zmin < 0.05:
            zmin, zmax = zmin - 0.05, zmax + 0.05
        zpad = (zmax - zmin) * 0.1
        zmin -= zpad; zmax += zpad

        def sx(P): return x0 + (P / pmax) * (x1 - x0)
        def sy(Z): return y0 - ((Z - zmin) / (zmax - zmin)) * (y0 - y1)

        # gridlines + y ticks
        for k in range(5):
            zz = zmin + (zmax - zmin) * k / 4
            yy = sy(zz)
            p.setPen(QPen(QColor("#eeeeee"), 1)); p.drawLine(x0, int(yy), x1, int(yy))
            p.setPen(QColor("#555555"))
            p.drawText(x0 - 50, int(yy) + 4, f"{zz:.3f}")
        # x ticks
        for k in range(5):
            pp = pmax * k / 4
            xx = sx(pp)
            p.setPen(QColor("#555555"))
            p.drawText(int(xx) - 12, y0 + 16, f"{pp:.0f}")

        # Z = 1 reference line
        if zmin < 1.0 < zmax:
            p.setPen(QPen(QColor("#c00000"), 1, Qt.PenStyle.DashLine))
            yy = sy(1.0); p.drawLine(x0, int(yy), x1, int(yy))

        # the curve
        poly = QPolygonF([QPointF(sx(P), sy(Z)) for P, Z in self.points])
        p.setPen(QPen(QColor("#1f6fd0"), 2))
        p.drawPolyline(poly)

        # title
        p.setPen(QColor("#333333"))
        tf = QFont(); tf.setPointSize(8); tf.setBold(True); p.setFont(tf)
        p.drawText(x0 + 4, y1 + 12, self.title)


# ======================================================================= #
#  Gas Blending tab
# ======================================================================= #
class GasBlendingTabQt(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_target = None  # (o2, he) for live limits refresh

        self._engine_ok = True
        self._engine_err = ""
        try:
            self._blender = Blender(get_eos("gerg"))
        except Exception as e:  # noqa: BLE001
            self._blender = None
            self._engine_ok = False
            self._engine_err = str(e)

        root = QHBoxLayout(self)
        root.addLayout(self._build_inputs(), 0)
        root.addLayout(self._build_outputs(), 1)

        if not self._engine_ok:
            self._set_warning(f"GERG/CoolProp unavailable: {self._engine_err}")
            self._blend_btn.setEnabled(False)

    # ------------------------------------------------------------------ #
    #  Input panel
    # ------------------------------------------------------------------ #
    def _build_inputs(self) -> QVBoxLayout:
        col = QVBoxLayout()

        # --- Cylinder -------------------------------------------------
        g_cyl = QGroupBox("Cylinder")
        gl = QGridLayout(g_cyl)
        self._in_V = _inp("24")
        self._in_T = _inp("15")
        gl.addWidget(QLabel("Volume [L]"), 0, 0)
        gl.addWidget(self._in_V, 0, 1)
        gl.addWidget(QLabel("Temperature [°C]"), 1, 0)
        gl.addWidget(self._in_T, 1, 1)
        col.addWidget(g_cyl)

        # --- Start state ---------------------------------------------
        g_start = QGroupBox("Start")
        sl = QGridLayout(g_start)
        self._rb_empty = QRadioButton("Empty cylinder")
        self._rb_topoff = QRadioButton("Top-off (existing mix)")
        self._rb_empty.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self._rb_empty)
        grp.addButton(self._rb_topoff)
        sl.addWidget(self._rb_empty, 0, 0, 1, 2)
        sl.addWidget(self._rb_topoff, 1, 0, 1, 2)
        self._in_Pstart = _inp("0")
        self._in_mixstart = _inp("21/00")
        sl.addWidget(QLabel("Start pressure [bar]"), 2, 0)
        sl.addWidget(self._in_Pstart, 2, 1)
        sl.addWidget(QLabel("Start mix O₂/He"), 3, 0)
        sl.addWidget(self._in_mixstart, 3, 1)
        col.addWidget(g_start)
        self._rb_empty.toggled.connect(self._sync_start_enabled)
        self._sync_start_enabled()

        # --- Target ---------------------------------------------------
        g_tgt = QGroupBox("Target")
        tl = QGridLayout(g_tgt)
        self._in_mixtgt = _inp("18/45")
        self._in_Ptgt = _inp("200")
        tl.addWidget(QLabel("Target mix O₂/He"), 0, 0)
        tl.addWidget(self._in_mixtgt, 0, 1)
        tl.addWidget(QLabel("Target pressure [bar]"), 1, 0)
        tl.addWidget(self._in_Ptgt, 1, 1)
        tl.addWidget(QLabel("Top gas"), 2, 0)
        self._cb_top = QComboBox()
        self._cb_top.addItems(["Air (21/79)", "O₂ (100/00)", "Custom…"])
        tl.addWidget(self._cb_top, 2, 1)
        self._in_topcustom = _inp("32/00")
        self._in_topcustom.setEnabled(False)
        tl.addWidget(QLabel("Custom O₂/He"), 3, 0)
        tl.addWidget(self._in_topcustom, 3, 1)
        self._cb_top.currentIndexChanged.connect(
            lambda i: self._in_topcustom.setEnabled(i == 2))
        self._chk_drain = QCheckBox("Allow drain")
        tl.addWidget(self._chk_drain, 4, 0, 1, 2)
        col.addWidget(g_tgt)

        # --- Best mix for depth --------------------------------------
        g_bm = QGroupBox("Best mix for depth")
        bl = QGridLayout(g_bm)
        self._in_bm_depth = _inp("45")
        self._in_bm_ppo2 = _inp("1.4")
        self._in_bm_end = _inp("30")
        bl.addWidget(QLabel("Max depth [m]"), 0, 0)
        bl.addWidget(self._in_bm_depth, 0, 1)
        bl.addWidget(QLabel("ppO₂ max [bar]"), 1, 0)
        bl.addWidget(self._in_bm_ppo2, 1, 1)
        bl.addWidget(QLabel("EAD max [m]"), 2, 0)
        bl.addWidget(self._in_bm_end, 2, 1)
        self._bm_btn = QPushButton("Compute best mix → Target")
        self._bm_btn.clicked.connect(self._on_best_mix)
        bl.addWidget(self._bm_btn, 3, 0, 1, 2)
        self._lbl_bm = QLabel("")
        self._lbl_bm.setWordWrap(True)
        bl.addWidget(self._lbl_bm, 4, 0, 1, 2)
        col.addWidget(g_bm)

        # --- Action ---------------------------------------------------
        self._blend_btn = QPushButton("Blend")
        self._blend_btn.clicked.connect(self._on_blend)
        col.addWidget(self._blend_btn)
        col.addStretch(1)
        return col

    def _sync_start_enabled(self):
        topoff = self._rb_topoff.isChecked()
        self._in_Pstart.setEnabled(topoff)
        self._in_mixstart.setEnabled(topoff)

    # ------------------------------------------------------------------ #
    #  Output panel
    # ------------------------------------------------------------------ #
    def _build_outputs(self) -> QVBoxLayout:
        col = QVBoxLayout()

        # --- Fill sequence -------------------------------------------
        g_fill = QGroupBox("Fill sequence")
        fl = QVBoxLayout(g_fill)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Gas", "Fill to [bar]"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        fl.addWidget(self._table)
        self._lbl_result = QLabel("")
        self._lbl_result.setWordWrap(True)
        fl.addWidget(self._lbl_result)
        self._lbl_warn = QLabel("")
        self._lbl_warn.setWordWrap(True)
        fl.addWidget(self._lbl_warn)
        col.addWidget(g_fill)

        # --- Limits ---------------------------------------------------
        g_lim = QGroupBox("Limits (target mix)")
        ll = QGridLayout(g_lim)
        self._in_depth = _inp("60")
        self._in_depth.editingFinished.connect(self._refresh_limits)
        ll.addWidget(QLabel("Planned depth [m]"), 0, 0)
        ll.addWidget(self._in_depth, 0, 1)

        def mk():
            w = QLabel("—")
            w.setStyleSheet(f"background-color: {CLR_RESULT}; padding: 2px 6px;")
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return w

        self._lbl_mod14 = mk()
        self._lbl_mod16 = mk()
        self._lbl_ppo2 = mk()
        self._lbl_ead = mk()
        ll.addWidget(QLabel("MOD @1.4 [m]"), 1, 0); ll.addWidget(self._lbl_mod14, 1, 1)
        ll.addWidget(QLabel("MOD @1.6 [m]"), 1, 2); ll.addWidget(self._lbl_mod16, 1, 3)
        ll.addWidget(QLabel("ppO₂ @ depth [bar]"), 2, 0); ll.addWidget(self._lbl_ppo2, 2, 1)
        ll.addWidget(QLabel("EAD @ depth [m]"), 2, 2); ll.addWidget(self._lbl_ead, 2, 3)
        col.addWidget(g_lim)

        # --- Z-factor plot -------------------------------------------
        g_z = QGroupBox("Z-factor vs pressure")
        zl = QVBoxLayout(g_z)
        self._zchart = _ZChartWidget()
        zl.addWidget(self._zchart)
        col.addWidget(g_z, 1)
        return col

    # ------------------------------------------------------------------ #
    #  Actions
    # ------------------------------------------------------------------ #
    def _set_warning(self, msg: str):
        self._lbl_warn.setText(msg)
        self._lbl_warn.setStyleSheet(
            f"background-color: {CLR_WARN}; padding: 4px; font-weight: bold;")

    def _clear_warning(self):
        self._lbl_warn.setText("")
        self._lbl_warn.setStyleSheet("")

    def _on_best_mix(self):
        try:
            depth = _flt(self._in_bm_depth.text(), 0.0)
            ppo2 = _flt(self._in_bm_ppo2.text(), 1.4)
            eadmax = _flt(self._in_bm_end.text(), 30.0)
            if depth <= 0:
                raise ValueError("depth must be > 0")
            fo2 = limits.best_mix_O2(depth, ppo2)
            fhe = limits.best_mix_He(depth, ppo2, eadmax, o2_narcotic=False)
        except ValueError as e:
            self._lbl_bm.setText(f"Error: {e}")
            self._lbl_bm.setStyleSheet(f"background-color: {CLR_WARN}; padding: 3px;")
            return
        self._in_mixtgt.setText(_fmt_mix(fo2, fhe))
        self._lbl_bm.setText(
            f"Suggested: <b>{label(_mix_dict(fo2, fhe))}</b> "
            f"(O₂ {round(fo2*100)}% / He {round(fhe*100)}%)")
        self._lbl_bm.setStyleSheet(f"background-color: {CLR_OK}; padding: 3px;")

    def _on_blend(self):
        if not self._engine_ok:
            return
        self._clear_warning()
        self._table.setRowCount(0)
        self._lbl_result.setText("")
        self._zchart.set_data([])

        try:
            V = _flt(self._in_V.text(), 0.0)
            T = _flt(self._in_T.text(), 15.0) + 273.15
            o2, he = _parse_mix(self._in_mixtgt.text())
            P_tgt = _flt(self._in_Ptgt.text(), 0.0)
            if V <= 0 or P_tgt <= 0:
                raise ValueError("Volume and target pressure must be > 0")

            if self._rb_topoff.isChecked():
                so2, she = _parse_mix(self._in_mixstart.text())
                P_start = _flt(self._in_Pstart.text(), 0.0)
                start = CylinderState(V, P_start, _mix_dict(so2, she), T)
            else:
                start = CylinderState(V, 0.0, {"N2": 1.0}, T)

            idx = self._cb_top.currentIndex()
            if idx == 0:
                top = AIR
            elif idx == 1:
                top = PURE_O2
            else:
                to2, the = _parse_mix(self._in_topcustom.text())
                top = _mix_dict(to2, the)
        except ValueError as e:
            self._set_warning(f"Invalid input: {e}")
            return

        self._last_target = (o2, he)
        self._refresh_limits()

        try:
            res = self._blender.blend(start, o2, he, P_tgt,
                                      top_gas=top,
                                      allow_drain=self._chk_drain.isChecked())
        except Exception as e:  # noqa: BLE001
            self._set_warning(f"Computation error: {e}")
            return

        if not res.feasible:
            self._set_warning(res.warnings[0] if res.warnings
                              else "Cannot reach target.")
            return

        # fill table
        self._table.setRowCount(len(res.steps))
        for r, step in enumerate(res.steps):
            bg = CLR_WARN if step.gas.upper().startswith("DRAIN") else CLR_RESULT
            c0 = QTableWidgetItem(step.gas)
            c0.setTextAlignment(Qt.AlignmentFlag.AlignVCenter
                                | Qt.AlignmentFlag.AlignLeft)
            c1 = QTableWidgetItem(f"{step.to_pressure:.0f}")
            c1.setTextAlignment(Qt.AlignmentFlag.AlignVCenter
                                | Qt.AlignmentFlag.AlignRight)
            for c in (c0, c1):
                c.setBackground(QColor(bg))
            self._table.setItem(r, 0, c0)
            self._table.setItem(r, 1, c1)

        fx = normalise(res.final.mix)
        self._lbl_result.setText(
            f"Result: <b>{label(res.final.mix)}</b> — "
            f"O₂ {fx.get('O2', 0) * 100:.1f}% / "
            f"He {fx.get('He', 0) * 100:.1f}% / "
            f"N₂ {fx.get('N2', 0) * 100:.1f}%")
        self._lbl_result.setStyleSheet(
            f"background-color: {CLR_OK}; padding: 4px; font-weight: bold;")
        if res.warnings:
            self._set_warning("  ".join(res.warnings))

        # Z-factor curve for the final mix, 1 bar .. target pressure
        self._update_zchart(res.final.mix, P_tgt)

    def _update_zchart(self, mix, P_max):
        try:
            eos = self._blender.eos
            n = 60
            pts = []
            for i in range(1, n + 1):
                P = P_max * i / n
                pts.append((P, eos.Z(mix, P)))
            self._zchart.set_data(pts, f"Z – {label(mix)}")
        except Exception:  # noqa: BLE001
            self._zchart.set_data([])

    def _refresh_limits(self):
        if not self._last_target:
            return
        o2, he = self._last_target
        mix = _mix_dict(o2, he)
        depth = _flt(self._in_depth.text(), 0.0)

        mod14 = limits.mod(mix, 1.4)
        mod16 = limits.mod(mix, 1.6)
        ppo2 = limits.ppO2(mix, depth)
        ead_v = limits.ead(mix, depth)

        self._lbl_mod14.setText(f"{mod14:.0f}")
        self._lbl_mod16.setText(f"{mod16:.0f}")
        self._lbl_ppo2.setText(f"{ppo2:.2f}")
        self._lbl_ead.setText(f"{ead_v:.0f}")

        self._lbl_mod14.setStyleSheet(self._lim_style(
            CLR_WARN if depth > mod14 else CLR_RESULT))
        self._lbl_mod16.setStyleSheet(self._lim_style(
            CLR_WARN if depth > mod16 else CLR_RESULT))
        if ppo2 > 1.6:
            c = CLR_WARN
        elif ppo2 > 1.4:
            c = CLR_CAUTION
        else:
            c = CLR_OK
        self._lbl_ppo2.setStyleSheet(self._lim_style(c))

    @staticmethod
    def _lim_style(bg: str) -> str:
        return f"background-color: {bg}; padding: 2px 6px;"
