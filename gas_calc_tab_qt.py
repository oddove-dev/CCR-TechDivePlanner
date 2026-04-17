"""
gas_calc_tab_qt.py — Gas Calculator UI tab for JJ TechDivePlanner (PyQt6)
=========================================================================

PyQt6 port of gas_calc_tab.py.  Single class GasCalcTabQt(QWidget) with
5 sub-tabs via QTabWidget.  All calculations use gas_calc (gc).
"""

from __future__ import annotations
import csv

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget, QGroupBox,
    QLabel, QPushButton, QLineEdit, QTextEdit, QTableWidget, QTableWidgetItem,
    QCheckBox, QComboBox, QScrollArea, QSizePolicy, QFileDialog, QMessageBox,
    QAbstractItemView, QHeaderView,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QPen, QColor, QFont
import gas_calc as gc

# ── Colour constants ──────────────────────────────────────────────────────────
CLR_INPUT   = "#ffe0e0"
CLR_RESULT  = "#dff0df"
CLR_HDR     = "#c8dde8"
CLR_LABEL   = "#ececec"
CLR_OK      = "#b8f0b8"
CLR_CAUTION = "#fff0a0"
CLR_WARN    = "#ffb0b0"
CLR_INFO    = "#d0e8ff"


# ── Module-level helpers ──────────────────────────────────────────────────────

def _flt(text: str, default: float = 0.0) -> float:
    """Parse string to float; returns default on failure."""
    try:
        return float(str(text).replace(",", "."))
    except (ValueError, TypeError):
        return default


def _int_val(text: str, default: int = 0) -> int:
    """Parse string to int; returns default on failure."""
    try:
        return int(str(text))
    except (ValueError, TypeError):
        return default


def _inp(parent: QWidget, default: str = "") -> QLineEdit:
    """Return a QLineEdit styled as an input field."""
    w = QLineEdit(parent)
    w.setText(str(default))
    w.setAlignment(Qt.AlignmentFlag.AlignRight)
    w.setStyleSheet(f"background-color: {CLR_INPUT};")
    w.setFixedWidth(90)
    return w


def _res_lbl(parent: QWidget, text: str = "—") -> QLabel:
    """Return a QLabel styled as a result field."""
    w = QLabel(text, parent)
    w.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    w.setStyleSheet(
        f"background-color: {CLR_RESULT}; border: 1px solid #aaa; padding: 2px 4px;"
    )
    w.setFixedWidth(90)
    w.setMinimumHeight(22)
    return w


def _hdr_lbl(parent: QWidget, text: str) -> QLabel:
    """Return a header QLabel."""
    w = QLabel(text, parent)
    w.setAlignment(Qt.AlignmentFlag.AlignCenter)
    w.setStyleSheet(
        f"background-color: {CLR_HDR}; border: 1px solid #aaa; padding: 2px 4px; font-weight: bold;"
    )
    return w


def _field_row(layout: QGridLayout, label_text: str, widget: QWidget, row: int) -> None:
    """Add a label + widget row to a QGridLayout at the given row."""
    lbl = QLabel(label_text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    lbl.setStyleSheet(
        f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px;"
    )
    layout.addWidget(lbl, row, 0)
    layout.addWidget(widget, row, 1)


def _set_bg(widget: QWidget, color: str) -> None:
    """Set widget background keeping existing style properties."""
    current = widget.styleSheet()
    # Replace background-color if present, else prepend
    import re
    new_style = re.sub(r"background-color\s*:\s*[^;]+;?", "", current)
    new_style = f"background-color: {color}; " + new_style.strip()
    widget.setStyleSheet(new_style)


def _parse_mix_str(text: str, default_o2: float = 21.0, default_he: float = 0.0):
    """Parse 'O2/He' string like '13/65' → (0.13, 0.65)."""
    txt = str(text).strip().replace(",", ".")
    parts = txt.split("/")
    try:
        o2 = float(parts[0]) if parts[0].strip() else default_o2
    except ValueError:
        o2 = default_o2
    try:
        he = float(parts[1]) if len(parts) > 1 and parts[1].strip() else default_he
    except ValueError:
        he = default_he
    return o2 / 100.0, he / 100.0


# ── ICD Chart widget ──────────────────────────────────────────────────────────

class _ICDChartWidget(QWidget):
    """Custom widget that draws the ICD ΔPN₂ vs Depth chart using QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.curves: list = []   # list of list[(depth, dpn2)]
        self.limit: float = 0.5
        self.labels: list = ["G1→G2", "G2→G3", "G3→G4"]
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 150)

    def set_data(self, curves: list, limit: float, labels: list = None) -> None:
        self.curves = curves
        self.limit = limit
        self.labels = labels or ["G1→G2", "G2→G3", "G3→G4"]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        W = self.width()
        H = self.height()
        if W < 60 or H < 60:
            return

        margin_l = 58
        margin_r = 15
        margin_t = 18
        margin_b = 38

        plot_w = W - margin_l - margin_r
        plot_h = H - margin_t - margin_b

        curves = self.curves
        limit  = self.limit

        all_vals = [v for curve in curves for _, v in curve]
        if not all_vals:
            # Draw empty axes
            painter.fillRect(margin_l, margin_t, plot_w, plot_h, QColor("#f8f8f8"))
            painter.setPen(QPen(QColor("#888888")))
            painter.drawRect(margin_l, margin_t, plot_w, plot_h)
            return

        max_depth = 100
        y_min = min(min(all_vals), -0.05)
        y_max = max(max(all_vals), limit * 1.5, 0.1)

        def _x(d: float) -> int:
            return int(margin_l + (d / max_depth) * plot_w)

        def _y(v: float) -> int:
            return int(margin_t + plot_h - (v - y_min) / (y_max - y_min) * plot_h)

        # Background
        painter.fillRect(margin_l, margin_t, plot_w, plot_h, QColor("#f8f8f8"))

        # Grid lines (every 10 m)
        small_font = QFont("Segoe UI", 7)
        painter.setFont(small_font)
        grid_pen = QPen(QColor("#e0e0e0"))
        grid_pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(grid_pen)
        for d in range(0, 101, 10):
            xp = _x(d)
            painter.drawLine(xp, margin_t, xp, margin_t + plot_h)
        for v_frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            v = y_min + v_frac * (y_max - y_min)
            yp = _y(v)
            painter.drawLine(margin_l, yp, margin_l + plot_w, yp)

        # X-axis tick labels
        painter.setPen(QPen(QColor("#333333")))
        for d in range(0, 101, 10):
            xp = _x(d)
            painter.drawText(xp - 12, margin_t + plot_h + 4, 24, 14,
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                             str(d))

        # Y-axis tick labels
        for v_frac in [0.0, 0.25, 0.5, 0.75, 1.0]:
            v = y_min + v_frac * (y_max - y_min)
            yp = _y(v)
            painter.drawText(0, yp - 8, margin_l - 5, 16,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{v:.2f}")

        # Zero line
        zero_pen = QPen(QColor("#aaaaaa"))
        zero_pen.setWidth(1)
        painter.setPen(zero_pen)
        painter.drawLine(margin_l, _y(0.0), margin_l + plot_w, _y(0.0))

        # Limit line (red dashed)
        lim_pen = QPen(QColor("#cc0000"))
        lim_pen.setWidth(2)
        lim_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(lim_pen)
        y_lim = _y(limit)
        painter.drawLine(margin_l, y_lim, margin_l + plot_w, y_lim)
        painter.setFont(small_font)
        painter.setPen(QPen(QColor("#cc0000")))
        painter.drawText(margin_l + plot_w - 90, y_lim - 14, 88, 14,
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                         f"Guideline {limit:.1f} bar")

        # Draw 3 curves + end labels
        CURVE_COLORS = ["#2277cc", "#229944", "#cc7700"]
        label_font = QFont("Segoe UI", 7)
        active_curves = []
        for idx, (curve, base_col_str) in enumerate(zip(curves, CURVE_COLORS)):
            if len(curve) < 2:
                continue
            active_curves.append((idx, curve, base_col_str))
            base_col = QColor(base_col_str)
            warn_col = QColor("#dd2222")
            prev_x = _x(curve[0][0])
            prev_y = _y(curve[0][1])
            for d, v in curve[1:]:
                cx = _x(d)
                cy = _y(v)
                pen = QPen(warn_col if v > limit else base_col)
                pen.setWidth(2)
                painter.setPen(pen)
                painter.drawLine(prev_x, prev_y, cx, cy)
                prev_x, prev_y = cx, cy

            # End-of-line label
            last_d, last_v = curve[-1]
            lx = _x(last_d)
            ly = _y(last_v)
            lbl = self.labels[idx] if idx < len(self.labels) else f"G{idx+1}→G{idx+2}"
            painter.setFont(label_font)
            painter.setPen(QPen(QColor(base_col_str)))
            painter.drawText(lx - 60, ly - 16, 58, 14,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             lbl)

        # Border
        painter.setPen(QPen(QColor("#888888")))
        painter.drawRect(margin_l, margin_t, plot_w, plot_h)

        # Legend box (top-left inside plot)
        if active_curves:
            leg_x = margin_l + 6
            leg_y = margin_t + 6
            leg_row_h = 16
            leg_w = 160
            leg_h = len(active_curves) * leg_row_h + 8
            painter.fillRect(leg_x, leg_y, leg_w, leg_h, QColor(255, 255, 255, 200))
            painter.setPen(QPen(QColor("#aaaaaa")))
            painter.drawRect(leg_x, leg_y, leg_w, leg_h)
            painter.setFont(label_font)
            for row_i, (idx, _curve, col_str) in enumerate(active_curves):
                rx = leg_x + 6
                ry = leg_y + 4 + row_i * leg_row_h
                line_pen = QPen(QColor(col_str))
                line_pen.setWidth(2)
                painter.setPen(line_pen)
                painter.drawLine(rx, ry + 6, rx + 18, ry + 6)
                lbl = self.labels[idx] if idx < len(self.labels) else f"G{idx+1}→G{idx+2}"
                painter.setPen(QPen(QColor("#222222")))
                painter.drawText(rx + 22, ry, leg_w - 28, leg_row_h,
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 lbl)

        # Axis labels
        axis_font = QFont("Segoe UI", 8)
        painter.setFont(axis_font)
        painter.setPen(QPen(QColor("#333333")))
        painter.drawText(margin_l, margin_t + plot_h + 20, plot_w, 16,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         "Depth [m]")

        # Rotated Y-axis label
        painter.save()
        painter.translate(12, margin_t + plot_h // 2)
        painter.rotate(-90)
        painter.drawText(-40, -6, 80, 16,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         "\u0394PN\u2082 [bar]")
        painter.restore()


# ═════════════════════════════════════════════════════════════════════════════
# Main GasCalcTabQt class
# ═════════════════════════════════════════════════════════════════════════════

class GasCalcTabQt(QWidget):
    """Gas Calculator — master tab housing 5 sub-tabs (PyQt6 port)."""

    def __init__(self, parent=None, db=None, save_fn=None):
        super().__init__(parent)
        self._db      = db if db is not None else {}
        self._save_fn = save_fn or (lambda: None)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._nb = QTabWidget(self)
        layout.addWidget(self._nb)

        self._tab_blend   = self._make_blend_tab()
        self._tab_limits  = self._make_limits_tab()
        self._tab_ccr     = self._make_ccr_tab()
        self._tab_icd     = self._make_icd_tab()
        self._tab_compare = self._make_compare_tab()

        self._nb.addTab(self._tab_blend,   "Blending & Filling")
        self._nb.addTab(self._tab_limits,  "Limits & Warnings")
        self._nb.addTab(self._tab_ccr,     "Rebreather (CCR)")
        self._nb.addTab(self._tab_icd,     "ICD Analysis")
        self._nb.addTab(self._tab_compare, "Comparison & Tables")

    # =========================================================================
    # Tab 1: Blending & Filling
    # =========================================================================

    def _make_blend_tab(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 6, 6, 6)

        left  = self._make_fill_seq_group()
        right = self._make_optimizer_group()
        h.addWidget(left, 1)
        h.addWidget(right, 1)
        return w

    # ── Partial-Pressure Fill Sequence ───────────────────────────────────────

    def _make_fill_seq_group(self) -> QGroupBox:
        gb = QGroupBox("Partial-Pressure Fill Sequence")
        vl = QVBoxLayout(gb)

        grid = QGridLayout()
        grid.setSpacing(3)

        self._fs_vol     = _inp(gb, "10")
        self._fs_pstart  = _inp(gb, "0")
        self._fs_ptarget = _inp(gb, "200")
        self._fs_o2      = _inp(gb, "21")
        self._fs_he      = _inp(gb, "35")

        fields = [
            ("Cylinder volume [L]",   self._fs_vol),
            ("Start pressure [bar]",  self._fs_pstart),
            ("Target pressure [bar]", self._fs_ptarget),
            ("Target O\u2082 [%]",    self._fs_o2),
            ("Target He [%]",         self._fs_he),
        ]
        for r, (lbl, w) in enumerate(fields):
            _field_row(grid, lbl, w, r)

        # Top-gas preset dropdown
        preset_lbl = QLabel("Top gas")
        preset_lbl.setStyleSheet(f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px;")
        self._fs_top_preset = QComboBox(gb)
        self._TOP_GAS_PRESETS = {
            "Air (21%)":  (0.21, 0.00),
            "Nitrox 32":  (0.32, 0.00),
            "Nitrox 36":  (0.36, 0.00),
            "Nitrox 50":  (0.50, 0.00),
            "Pure O\u2082": (1.00, 0.00),
            "Custom":     None,
        }
        for name in self._TOP_GAS_PRESETS:
            self._fs_top_preset.addItem(name)
        r = len(fields)
        grid.addWidget(preset_lbl, r, 0)
        grid.addWidget(self._fs_top_preset, r, 1)

        # Custom O2/He fields
        self._fs_custom_widget = QWidget(gb)
        cg = QGridLayout(self._fs_custom_widget)
        cg.setContentsMargins(0, 0, 0, 0)
        cg.setSpacing(3)
        self._fs_top_o2 = _inp(self._fs_custom_widget, "21")
        self._fs_top_he = _inp(self._fs_custom_widget, "0")
        _field_row(cg, "  Custom O\u2082 [%]", self._fs_top_o2, 0)
        _field_row(cg, "  Custom He [%]",  self._fs_top_he, 1)
        grid.addWidget(self._fs_custom_widget, r + 1, 0, 1, 2)
        self._fs_custom_widget.setVisible(False)

        self._fs_top_preset.currentTextChanged.connect(self._on_top_preset_changed)

        vl.addLayout(grid)

        btn = QPushButton("Calculate Fill Sequence")
        btn.clicked.connect(self._calc_fill)
        vl.addWidget(btn)

        self._fill_text = QTextEdit()
        self._fill_text.setReadOnly(True)
        self._fill_text.setFontFamily("Courier New")
        self._fill_text.setFontPointSize(9)
        vl.addWidget(self._fill_text, 1)
        return gb

    def _on_top_preset_changed(self, text: str) -> None:
        self._fs_custom_widget.setVisible(text == "Custom")

    def _calc_fill(self) -> None:
        vol      = _flt(self._fs_vol.text(),     10.0)
        p_start  = _flt(self._fs_pstart.text(),  0.0)
        p_target = _flt(self._fs_ptarget.text(), 200.0)
        fO2      = _flt(self._fs_o2.text(), 21.0) / 100.0
        fHe      = _flt(self._fs_he.text(), 0.0) / 100.0

        preset_name = self._fs_top_preset.currentText()
        preset_val  = self._TOP_GAS_PRESETS.get(preset_name)
        if preset_val is not None:
            top_o2, top_he = preset_val
        else:
            top_o2 = _flt(self._fs_top_o2.text(), 21.0) / 100.0
            top_he = _flt(self._fs_top_he.text(), 0.0)  / 100.0

        steps = gc.pp_fill_sequence(
            vol_L=vol, p_start=p_start, p_target=p_target,
            target_o2_frac=fO2, target_he_frac=fHe,
            avail_top_frac_o2=top_o2, avail_top_frac_he=top_he,
        )

        lines = []
        if steps and "error" in steps[0]:
            lines.append(f"ERROR: {steps[0]['error']}")
            self._fill_text.setPlainText("\n".join(lines))
            return

        col_w = [14, 10, 14, 10, 10, 10]
        hdrs  = ["Gas", "Add [bar]", "Running [bar]", "fO\u2082", "fHe", "fN\u2082"]
        hdr_line = "".join(h.ljust(w) for h, w in zip(hdrs, col_w))
        lines.append(hdr_line)
        lines.append("\u2500" * sum(col_w))

        for s in steps:
            if "error" in s:
                lines.append(f"ERROR: {s['error']}")
                continue
            line = (
                s["gas"].ljust(col_w[0]) +
                f"{s['p_added']:.1f}".rjust(col_w[1]) +
                f"{s['p_running']:.1f}".rjust(col_w[2]) +
                f"{s['fO2_running']*100:.1f}%".rjust(col_w[3]) +
                f"{s['fHe_running']*100:.1f}%".rjust(col_w[4]) +
                f"{s['fN2_running']*100:.1f}%".rjust(col_w[5])
            )
            lines.append(line)

        if steps:
            last = steps[-1]
            lines.append("")
            lines.append(
                f"Final mix: O\u2082 {last['fO2_running']*100:.1f}%  "
                f"He {last['fHe_running']*100:.1f}%  "
                f"N\u2082 {last['fN2_running']*100:.1f}%"
            )
            lines.append(f"Final pressure: {last['p_running']:.1f} bar")
            fO2_f = last["fO2_running"]
            fHe_f = last["fHe_running"]
            mod14 = gc.calc_mod(fO2_f, 1.4)
            mod16 = gc.calc_mod(fO2_f, 1.6)
            ead   = gc.calc_ead(fO2_f, fHe_f, 40.0)
            floor = gc.calc_hypoxic_floor(fO2_f)
            lines.append(
                f"MOD (PO\u2082 1.4): {mod14:.0f} m   MOD (PO\u2082 1.6): {mod16:.0f} m"
            )
            lines.append(
                f"EAD at 40 m: {ead:.0f} m   Hypoxic floor: {floor:.1f} m"
            )

        self._fill_text.setPlainText("\n".join(lines))

    # ── Blend Optimizer ───────────────────────────────────────────────────────

    def _make_optimizer_group(self) -> QGroupBox:
        gb = QGroupBox("Blend Optimizer")
        vl = QVBoxLayout(gb)

        grid = QGridLayout()
        grid.setSpacing(3)

        self._opt_depth    = _inp(gb, "50")
        self._opt_ppo2_bot = _inp(gb, "1.3")
        self._opt_ppo2_dec = _inp(gb, "1.4")
        self._opt_ead      = _inp(gb, "30")
        self._opt_he_step  = _inp(gb, "5")
        self._opt_o2_step  = _inp(gb, "1")

        opt_fields = [
            ("Target depth [m]",     self._opt_depth),
            ("PO\u2082 at bottom [bar]", self._opt_ppo2_bot),
            ("PO\u2082 deco limit [bar]", self._opt_ppo2_dec),
            ("EAD limit [m]",        self._opt_ead),
            ("He step [%]",          self._opt_he_step),
            ("O\u2082 step [%]",     self._opt_o2_step),
        ]
        for r, (lbl, w) in enumerate(opt_fields):
            _field_row(grid, lbl, w, r)

        vl.addLayout(grid)

        btn = QPushButton("Find Optimal Mixes")
        btn.clicked.connect(self._run_optimizer)
        vl.addWidget(btn)

        cols = ["Mix", "O\u2082%", "He%", "N\u2082%", "MOD [m]", "EAD [m]",
                "Hyp.Floor [m]", "PO\u2082 at depth"]
        self._opt_table = QTableWidget(0, len(cols))
        self._opt_table.setHorizontalHeaderLabels(cols)
        self._opt_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._opt_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._opt_table.horizontalHeader().setStretchLastSection(True)
        self._opt_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        vl.addWidget(self._opt_table, 1)

        self._opt_count_lbl = QLabel("No results")
        vl.addWidget(self._opt_count_lbl)
        return gb

    def _run_optimizer(self) -> None:
        depth     = _flt(self._opt_depth.text(),    50.0)
        ppo2_bot  = _flt(self._opt_ppo2_bot.text(), 1.3)
        ppo2_deco = _flt(self._opt_ppo2_dec.text(), 1.4)
        ead_lim   = _flt(self._opt_ead.text(),      30.0)
        he_step   = max(1, _int_val(self._opt_he_step.text(), 5))
        o2_step   = max(1, _int_val(self._opt_o2_step.text(), 1))

        results = gc.blend_optimizer(
            target_depth_m=depth, ppo2_bottom=ppo2_bot,
            ppo2_limit_deco=ppo2_deco, ead_limit_m=ead_lim,
            he_step=he_step, o2_step=o2_step,
        )

        self._opt_table.setRowCount(0)
        for r_data in results:
            row = self._opt_table.rowCount()
            self._opt_table.insertRow(row)
            vals = [
                r_data["label"],
                f"{r_data['fO2']*100:.0f}",
                f"{r_data['fHe']*100:.0f}",
                f"{r_data['fN2']*100:.0f}",
                f"{r_data['mod']:.0f}",
                f"{r_data['ead']:.0f}",
                f"{r_data['hypoxic_floor']:.1f}",
                f"{r_data['po2_at_depth']:.2f}",
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._opt_table.setItem(row, c, item)
        self._opt_count_lbl.setText(f"{len(results)} mixes found")

    # =========================================================================
    # Tab 2: Limits & Warnings
    # =========================================================================

    def _make_limits_tab(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(6)

        h.addWidget(self._make_gas_limits_group(w))
        h.addWidget(self._make_o2_tox_group(w))
        h.addWidget(self._make_profile_group(w))
        return w

    # ── Gas Limits ────────────────────────────────────────────────────────────

    def _make_gas_limits_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Gas Limits")
        vl = QVBoxLayout(gb)

        grid = QGridLayout()
        grid.setSpacing(3)

        self._gl_o2    = _inp(gb, "21")
        self._gl_he    = _inp(gb, "35")
        self._gl_ppo2  = _inp(gb, "1.4")
        self._gl_depth = _inp(gb, "40")

        for r, (lbl, w) in enumerate([
            ("O\u2082 [%]",       self._gl_o2),
            ("He [%]",            self._gl_he),
            ("PO\u2082 limit [bar]", self._gl_ppo2),
            ("Eval depth [m]",    self._gl_depth),
        ]):
            _field_row(grid, lbl, w, r)

        vl.addLayout(grid)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #aaaaaa;")
        vl.addWidget(sep)

        out_grid = QGridLayout()
        out_grid.setSpacing(3)

        self._gl_mod_lbl   = _res_lbl(gb, "\u2014")
        self._gl_ead_lbl   = _res_lbl(gb, "\u2014")
        self._gl_floor_lbl = _res_lbl(gb, "\u2014")

        for r, (lbl_txt, w) in enumerate([
            ("MOD [m]",           self._gl_mod_lbl),
            ("EAD at depth [m]",  self._gl_ead_lbl),
            ("Hypoxic floor [m]", self._gl_floor_lbl),
        ]):
            field_lbl = QLabel(lbl_txt)
            field_lbl.setStyleSheet(
                f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px; font-weight: bold;"
            )
            out_grid.addWidget(field_lbl, r, 0)
            out_grid.addWidget(w, r, 1)

        vl.addLayout(out_grid)
        vl.addStretch()

        for inp in [self._gl_o2, self._gl_he, self._gl_ppo2, self._gl_depth]:
            inp.textChanged.connect(self._update_limits)

        self._update_limits()
        return gb

    def _update_limits(self) -> None:
        fO2   = _flt(self._gl_o2.text(),    21.0) / 100.0
        fHe   = _flt(self._gl_he.text(),    0.0)  / 100.0
        ppo2  = _flt(self._gl_ppo2.text(),  1.4)
        depth = _flt(self._gl_depth.text(), 40.0)

        if fO2 <= 0:
            self._gl_mod_lbl.setText("\u2014")
            self._gl_ead_lbl.setText("\u2014")
            self._gl_floor_lbl.setText("\u2014")
            return

        mod   = gc.calc_mod(fO2, ppo2)
        ead   = gc.calc_ead(fO2, fHe, depth)
        floor = gc.calc_hypoxic_floor(fO2)

        self._gl_mod_lbl.setText(f"{mod:.0f} m" if mod < 999 else "\u221e")
        self._gl_ead_lbl.setText(f"{ead:.0f} m")
        self._gl_floor_lbl.setText(f"{floor:.1f} m")

        mod_col = CLR_OK if mod > depth + 5 else (CLR_CAUTION if mod >= depth else CLR_WARN)
        ead_col = CLR_OK if ead <= 30 else (CLR_CAUTION if ead <= 40 else CLR_WARN)
        floor_col = CLR_OK if floor <= 0 else (CLR_CAUTION if floor <= 6 else CLR_WARN)

        for w, col in [
            (self._gl_mod_lbl,   mod_col),
            (self._gl_ead_lbl,   ead_col),
            (self._gl_floor_lbl, floor_col),
        ]:
            w.setStyleSheet(
                f"background-color: {col}; border: 1px solid #aaa; padding: 2px 4px;"
            )

    # ── Oxygen Toxicity ───────────────────────────────────────────────────────

    def _make_o2_tox_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Oxygen Toxicity")
        vl = QVBoxLayout(gb)

        grid = QGridLayout()
        grid.setSpacing(3)

        self._ot_depth = _inp(gb, "40")
        self._ot_time  = _inp(gb, "30")
        self._ot_o2    = _inp(gb, "21")
        self._ot_sp    = _inp(gb, "1.3")

        for r, (lbl, w) in enumerate([
            ("Depth [m]",      self._ot_depth),
            ("Time [min]",     self._ot_time),
            ("O\u2082 [%]",    self._ot_o2),
            ("Setpoint [bar]", self._ot_sp),
        ]):
            _field_row(grid, lbl, w, r)

        vl.addLayout(grid)

        self._ot_ccr_chk = QCheckBox("CCR mode (use setpoint PO\u2082)")
        vl.addWidget(self._ot_ccr_chk)

        btn = QPushButton("Calculate")
        btn.clicked.connect(self._update_tox)
        vl.addWidget(btn)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #aaaaaa;")
        vl.addWidget(sep)

        out_grid = QGridLayout()
        out_grid.setSpacing(3)

        self._ot_po2_lbl  = _res_lbl(gb, "\u2014")
        self._ot_cns_lbl  = _res_lbl(gb, "\u2014")
        self._ot_otu_lbl  = _res_lbl(gb, "\u2014")
        self._ot_cnsr_lbl = _res_lbl(gb, "\u2014")
        self._ot_otur_lbl = _res_lbl(gb, "\u2014")

        for r, (lbl_txt, w) in enumerate([
            ("PO\u2082 [bar]",        self._ot_po2_lbl),
            ("CNS %",                 self._ot_cns_lbl),
            ("OTU",                   self._ot_otu_lbl),
            ("CNS rate [%/min]",      self._ot_cnsr_lbl),
            ("OTU rate [/min]",       self._ot_otur_lbl),
        ]):
            field_lbl = QLabel(lbl_txt)
            field_lbl.setStyleSheet(
                f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px; font-weight: bold;"
            )
            out_grid.addWidget(field_lbl, r, 0)
            out_grid.addWidget(w, r, 1)

        vl.addLayout(out_grid)
        vl.addStretch()
        return gb

    def _update_tox(self) -> None:
        depth = _flt(self._ot_depth.text(), 40.0)
        time  = _flt(self._ot_time.text(),  30.0)
        fO2   = _flt(self._ot_o2.text(),    21.0) / 100.0
        sp    = _flt(self._ot_sp.text(),    1.3)
        ccr   = self._ot_ccr_chk.isChecked()

        if ccr:
            po2 = sp
            p_amb = depth / 10.0 + 1.0
            fO2_eff = po2 / p_amb if p_amb > 0 else fO2
        else:
            p_amb = depth / 10.0 + 1.0
            po2 = fO2 * p_amb
            fO2_eff = fO2

        cns_r = gc._cns_rate_from_po2(po2)
        if ccr:
            otu_r = ((po2 - 0.5) / 0.5) ** 0.833 if po2 > 0.5 else 0.0
        else:
            otu_r = gc.calc_otu_rate(fO2_eff, depth)
        cns = cns_r * time
        otu = otu_r * time

        self._ot_po2_lbl.setText(f"{po2:.3f}")
        self._ot_cns_lbl.setText(f"{cns:.1f}%")
        self._ot_otu_lbl.setText(f"{otu:.1f}")
        self._ot_cnsr_lbl.setText(f"{cns_r:.4f}")
        self._ot_otur_lbl.setText(f"{otu_r:.4f}")

        col = CLR_OK if cns < 25 else (CLR_CAUTION if cns < 50 else CLR_WARN)
        self._ot_cns_lbl.setStyleSheet(
            f"background-color: {col}; border: 1px solid #aaa; padding: 2px 4px;"
        )

    # ── Profile O₂ Summary ────────────────────────────────────────────────────

    def _make_profile_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Profile O\u2082 Summary")
        vl = QVBoxLayout(gb)

        # Header row
        hdr = QWidget()
        hdr.setStyleSheet(f"background-color: {CLR_HDR};")
        hdr_h = QHBoxLayout(hdr)
        hdr_h.setContentsMargins(2, 2, 2, 2)
        for lbl_txt in ["Depth [m]", "Time [min]", "O\u2082 [%]"]:
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet("font-weight: bold;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedWidth(70)
            hdr_h.addWidget(lbl)
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(28)
        add_btn.clicked.connect(lambda: self._add_prof_row())
        hdr_h.addWidget(add_btn)
        hdr_h.addStretch()
        vl.addWidget(hdr)

        # Scrollable row area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._prof_row_container = QWidget()
        self._prof_vl = QVBoxLayout(self._prof_row_container)
        self._prof_vl.setSpacing(2)
        self._prof_vl.setContentsMargins(0, 0, 0, 0)
        self._prof_vl.addStretch()
        scroll.setWidget(self._prof_row_container)
        vl.addWidget(scroll, 1)

        self._prof_rows: list = []
        self._add_prof_row(depth="40", time="30", o2="21")
        self._add_prof_row(depth="21", time="5",  o2="50")
        self._add_prof_row(depth="6",  time="10", o2="100")

        btn = QPushButton("Calculate Profile Totals")
        btn.clicked.connect(self._calc_profile)
        vl.addWidget(btn)

        out_grid = QGridLayout()
        out_grid.setSpacing(3)
        self._prof_cns_lbl = _res_lbl(gb, "\u2014")
        self._prof_otu_lbl = _res_lbl(gb, "\u2014")

        for r, (lbl_txt, w) in enumerate([
            ("Total CNS %", self._prof_cns_lbl),
            ("Total OTU",   self._prof_otu_lbl),
        ]):
            field_lbl = QLabel(lbl_txt)
            field_lbl.setStyleSheet(
                f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px; font-weight: bold;"
            )
            out_grid.addWidget(field_lbl, r, 0)
            out_grid.addWidget(w, r, 1)

        vl.addLayout(out_grid)
        return gb

    def _add_prof_row(self, depth: str = "", time: str = "", o2: str = "21") -> None:
        row_w = QWidget()
        row_h = QHBoxLayout(row_w)
        row_h.setContentsMargins(0, 0, 0, 0)
        row_h.setSpacing(4)

        d_inp = _inp(row_w, depth)
        d_inp.setFixedWidth(70)
        t_inp = _inp(row_w, time)
        t_inp.setFixedWidth(70)
        o_inp = _inp(row_w, o2)
        o_inp.setFixedWidth(70)

        del_btn = QPushButton("\u2715")
        del_btn.setFixedWidth(28)
        del_btn.setStyleSheet("background-color: #ffaaaa;")

        row_h.addWidget(d_inp)
        row_h.addWidget(t_inp)
        row_h.addWidget(o_inp)
        row_h.addWidget(del_btn)
        row_h.addStretch()

        row_data = {"d": d_inp, "t": t_inp, "o": o_inp, "widget": row_w}
        del_btn.clicked.connect(lambda: self._del_prof_row(row_data))

        # Insert before the stretch at the end
        idx = self._prof_vl.count() - 1
        self._prof_vl.insertWidget(idx, row_w)
        self._prof_rows.append(row_data)

    def _del_prof_row(self, row_data: dict) -> None:
        row_data["widget"].deleteLater()
        if row_data in self._prof_rows:
            self._prof_rows.remove(row_data)

    def _calc_profile(self) -> None:
        segs = []
        for row in self._prof_rows:
            d = _flt(row["d"].text(), 0.0)
            t = _flt(row["t"].text(), 0.0)
            o = _flt(row["o"].text(), 21.0) / 100.0
            if t > 0 and o > 0:
                segs.append((d, t, o))
        if not segs:
            self._prof_cns_lbl.setText("\u2014")
            self._prof_otu_lbl.setText("\u2014")
            return
        result = gc.calc_cns_otu_profile(segs)
        cns = result["total_cns"]
        otu = result["total_otu"]
        self._prof_cns_lbl.setText(f"{cns:.1f}%")
        self._prof_otu_lbl.setText(f"{otu:.1f}")
        col = CLR_OK if cns < 25 else (CLR_CAUTION if cns < 50 else CLR_WARN)
        self._prof_cns_lbl.setStyleSheet(
            f"background-color: {col}; border: 1px solid #aaa; padding: 2px 4px;"
        )

    # =========================================================================
    # Tab 3: Rebreather (CCR)
    # =========================================================================

    def _make_ccr_tab(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(6)

        left  = QWidget()
        right = QWidget()
        lv = QVBoxLayout(left)
        lv.setSpacing(6)
        lv.setContentsMargins(0, 0, 0, 0)
        rv = QVBoxLayout(right)
        rv.setSpacing(6)
        rv.setContentsMargins(0, 0, 0, 0)

        lv.addWidget(self._make_o2_consumption_group(left))
        lv.addWidget(self._make_diluent_group(left))
        lv.addWidget(self._make_scrubber_group(left))
        lv.addStretch()

        rv.addWidget(self._make_setpoint_group(right))
        rv.addWidget(self._make_loop_group(right))

        h.addWidget(left, 1)
        h.addWidget(right, 1)
        return w

    # ── O₂ Consumption ───────────────────────────────────────────────────────

    def _make_o2_consumption_group(self, parent) -> QGroupBox:
        gb = QGroupBox("O\u2082 Consumption")
        vl = QVBoxLayout(gb)

        grid = QGridLayout()
        grid.setSpacing(3)

        self._o2c_rmv  = _inp(gb, "20")
        self._o2c_fo2  = _inp(gb, "100")
        self._o2c_time = _inp(gb, "60")

        for r, (lbl, w) in enumerate([
            ("RMV [L/min]", self._o2c_rmv),
            ("O\u2082 [%]", self._o2c_fo2),
            ("Time [min]",  self._o2c_time),
        ]):
            _field_row(grid, lbl, w, r)

        self._o2c_L_lbl = _res_lbl(gb, "\u2014")
        self._o2c_g_lbl = _res_lbl(gb, "\u2014")

        for r, (lbl_txt, w) in enumerate([
            ("O\u2082 consumed [L]", self._o2c_L_lbl),
            ("O\u2082 consumed [g]", self._o2c_g_lbl),
        ], start=3):
            field_lbl = QLabel(lbl_txt)
            field_lbl.setStyleSheet(
                f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px; font-weight: bold;"
            )
            grid.addWidget(field_lbl, r, 0)
            grid.addWidget(w, r, 1)

        vl.addLayout(grid)

        note = QLabel("O\u2082 = RMV \u00d7 fO\u2082 \u00d7 time.  Pure O\u2082 on CCR: RMV=20, O\u2082=100%")
        note.setStyleSheet("color: #555555; font-size: 8pt;")
        vl.addWidget(note)

        for inp in [self._o2c_rmv, self._o2c_fo2, self._o2c_time]:
            inp.textChanged.connect(self._update_o2c)

        self._update_o2c()
        return gb

    def _update_o2c(self) -> None:
        rmv  = _flt(self._o2c_rmv.text(),  20.0)
        fO2  = _flt(self._o2c_fo2.text(),  100.0) / 100.0
        time = _flt(self._o2c_time.text(), 60.0)
        r = gc.o2_consumption(rmv, fO2, time)
        self._o2c_L_lbl.setText(f"{r['liters']:.1f} L")
        self._o2c_g_lbl.setText(f"{r['grams']:.0f} g")

    # ── Diluent Consumption ───────────────────────────────────────────────────

    def _make_diluent_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Diluent Consumption")
        vl = QVBoxLayout(gb)

        grid = QGridLayout()
        grid.setSpacing(3)

        self._dil_flush_vol  = _inp(gb, "5")
        self._dil_n_flushes  = _inp(gb, "3")
        self._dil_depth      = _inp(gb, "40")
        self._dil_loop_vol   = _inp(gb, "5")
        self._dil_res_lbl    = _res_lbl(gb, "\u2014")

        for r, (lbl, w) in enumerate([
            ("Flush volume [L]",  self._dil_flush_vol),
            ("Number of flushes", self._dil_n_flushes),
            ("Depth [m]",         self._dil_depth),
            ("Loop volume [L]",   self._dil_loop_vol),
        ]):
            _field_row(grid, lbl, w, r)

        res_field_lbl = QLabel("Diluent consumed [L surf]")
        res_field_lbl.setStyleSheet(
            f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px; font-weight: bold;"
        )
        grid.addWidget(res_field_lbl, 4, 0)
        grid.addWidget(self._dil_res_lbl, 4, 1)

        vl.addLayout(grid)

        for inp in [self._dil_flush_vol, self._dil_n_flushes, self._dil_depth, self._dil_loop_vol]:
            inp.textChanged.connect(self._update_dil)

        self._update_dil()
        return gb

    def _update_dil(self) -> None:
        fv  = _flt(self._dil_flush_vol.text(), 5.0)
        nf  = _int_val(self._dil_n_flushes.text(), 3)
        dep = _flt(self._dil_depth.text(), 40.0)
        lv  = _flt(self._dil_loop_vol.text(), 5.0)
        res = gc.diluent_consumption(fv, nf, dep, lv)
        self._dil_res_lbl.setText(f"{res:.1f} L")

    # ── Scrubber Monitor ──────────────────────────────────────────────────────

    def _make_scrubber_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Scrubber Monitor (Sofnolime 797)")
        vl = QVBoxLayout(gb)

        warn = QLabel("WARNING: Empirical approximation only. Always apply \u226520% safety margin.")
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background-color: #fff0b0; color: #884400; border: 1px solid #aaa; "
            "padding: 3px 4px; font-weight: bold; font-size: 8pt;"
        )
        vl.addWidget(warn)

        grid = QGridLayout()
        grid.setSpacing(3)

        self._scr_mass    = _inp(gb, "1.5")
        self._scr_temp    = _inp(gb, "15")
        self._scr_depth   = _inp(gb, "40")
        self._scr_elapsed = _inp(gb, "60")

        for r, (lbl, w) in enumerate([
            ("Absorbent mass [kg]",       self._scr_mass),
            ("Water temp [\u00b0C]",       self._scr_temp),
            ("Representative depth [m]",  self._scr_depth),
            ("Elapsed time [min]",        self._scr_elapsed),
        ]):
            _field_row(grid, lbl, w, r)

        self._scr_cap_lbl = _res_lbl(gb, "\u2014")
        self._scr_rem_lbl = _res_lbl(gb, "\u2014")
        self._scr_pct_lbl = _res_lbl(gb, "\u2014")

        for r, (lbl_txt, w) in enumerate([
            ("Capacity [min]",  self._scr_cap_lbl),
            ("Remaining [min]", self._scr_rem_lbl),
            ("% used",          self._scr_pct_lbl),
        ], start=4):
            field_lbl = QLabel(lbl_txt)
            field_lbl.setStyleSheet(
                f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px; font-weight: bold;"
            )
            grid.addWidget(field_lbl, r, 0)
            grid.addWidget(w, r, 1)

        vl.addLayout(grid)

        for inp in [self._scr_mass, self._scr_temp, self._scr_depth, self._scr_elapsed]:
            inp.textChanged.connect(self._update_scrubber)

        self._update_scrubber()
        return gb

    def _update_scrubber(self) -> None:
        mass    = _flt(self._scr_mass.text(),    1.5)
        temp    = _flt(self._scr_temp.text(),    15.0)
        depth   = _flt(self._scr_depth.text(),   40.0)
        elapsed = _flt(self._scr_elapsed.text(), 60.0)
        r = gc.scrubber_remaining("Sofnolime 797", mass, temp, depth, elapsed)
        self._scr_cap_lbl.setText(f"{r['capacity_min']:.0f}")
        self._scr_rem_lbl.setText(f"{r['remaining_min']:.0f}")
        self._scr_pct_lbl.setText(f"{r['pct_used']:.1f}%")

        warn_map = {"ok": CLR_OK, "caution": CLR_CAUTION, "warning": CLR_WARN}
        col = warn_map.get(r["warning_level"], CLR_RESULT)
        self._scr_rem_lbl.setStyleSheet(
            f"background-color: {col}; border: 1px solid #aaa; padding: 2px 4px;"
        )

    # ── Setpoint & Crossover ──────────────────────────────────────────────────

    def _make_setpoint_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Setpoint & Crossover Depth")
        vl = QVBoxLayout(gb)

        grid = QGridLayout()
        grid.setSpacing(3)

        self._sp_dil_o2 = _inp(gb, "18")
        self._sp_sp     = _inp(gb, "1.3")
        self._sp_co_lbl = _res_lbl(gb, "\u2014")

        _field_row(grid, "Diluent O\u2082 [%]", self._sp_dil_o2, 0)
        _field_row(grid, "Setpoint [bar]",       self._sp_sp,     1)

        co_field_lbl = QLabel("Crossover depth [m]")
        co_field_lbl.setStyleSheet(
            f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px; font-weight: bold;"
        )
        grid.addWidget(co_field_lbl, 2, 0)
        grid.addWidget(self._sp_co_lbl, 2, 1)

        vl.addLayout(grid)

        note = QLabel(
            "Crossover: depth where diluent alone reaches setpoint PO\u2082.\n"
            "Below crossover: unit injects O\u2082.  Above: unit may vent O\u2082."
        )
        note.setStyleSheet("color: #555555; font-size: 8pt;")
        note.setWordWrap(True)
        vl.addWidget(note)
        vl.addStretch()

        for inp in [self._sp_dil_o2, self._sp_sp]:
            inp.textChanged.connect(self._update_setpoint)

        self._update_setpoint()
        return gb

    def _update_setpoint(self) -> None:
        fO2_dil = _flt(self._sp_dil_o2.text(), 18.0) / 100.0
        sp      = _flt(self._sp_sp.text(),      1.3)
        if fO2_dil <= 0:
            self._sp_co_lbl.setText("\u2014")
            return
        cd = gc.crossover_depth(fO2_dil, sp)
        self._sp_co_lbl.setText(f"{cd:.1f} m")
        col = CLR_OK if cd < 0 else CLR_INFO
        self._sp_co_lbl.setStyleSheet(
            f"background-color: {col}; border: 1px solid #aaa; padding: 2px 4px;"
        )

    # ── Loop Gas Fractions ────────────────────────────────────────────────────

    def _make_loop_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Loop Gas Fractions (CCR)")
        vl = QVBoxLayout(gb)

        grid = QGridLayout()
        grid.setSpacing(3)

        self._loop_dil_o2 = _inp(gb, "18")
        self._loop_dil_he = _inp(gb, "45")
        self._loop_sp     = _inp(gb, "1.3")

        for r, (lbl, w) in enumerate([
            ("Diluent O\u2082 [%]", self._loop_dil_o2),
            ("Diluent He [%]",      self._loop_dil_he),
            ("Setpoint [bar]",      self._loop_sp),
        ]):
            _field_row(grid, lbl, w, r)

        vl.addLayout(grid)

        # Segment row header
        seg_hdr = QWidget()
        seg_hdr.setStyleSheet(f"background-color: {CLR_HDR};")
        seg_hdr_h = QHBoxLayout(seg_hdr)
        seg_hdr_h.setContentsMargins(4, 2, 4, 2)
        for lbl_txt in ["Depth [m]", "Time [min]"]:
            lbl = QLabel(lbl_txt)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-weight: bold;")
            lbl.setFixedWidth(80)
            seg_hdr_h.addWidget(lbl)
        loop_add_btn = QPushButton("+")
        loop_add_btn.setFixedWidth(28)
        loop_add_btn.clicked.connect(lambda: self._add_loop_row())
        seg_hdr_h.addWidget(loop_add_btn)
        seg_hdr_h.addStretch()
        vl.addWidget(seg_hdr)

        # Scrollable segment rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(120)
        self._loop_row_container = QWidget()
        self._loop_vl = QVBoxLayout(self._loop_row_container)
        self._loop_vl.setSpacing(2)
        self._loop_vl.setContentsMargins(0, 0, 0, 0)
        self._loop_vl.addStretch()
        scroll.setWidget(self._loop_row_container)
        vl.addWidget(scroll)

        self._loop_seg_rows: list = []
        self._add_loop_row("40", "20")
        self._add_loop_row("21", "5")
        self._add_loop_row("6",  "10")

        btn = QPushButton("Analyse Loop Gas")
        btn.clicked.connect(self._calc_loop)
        vl.addWidget(btn)

        cols = ["Depth", "Time", "P_amb", "PO\u2082", "PN\u2082", "PHe",
                "fN\u2082", "fHe", "EAD [m]"]
        self._loop_table = QTableWidget(0, len(cols))
        self._loop_table.setHorizontalHeaderLabels(cols)
        self._loop_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._loop_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._loop_table.horizontalHeader().setStretchLastSection(True)
        self._loop_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        vl.addWidget(self._loop_table, 1)

        return gb

    def _add_loop_row(self, depth: str = "", time: str = "") -> None:
        row_w = QWidget()
        row_h = QHBoxLayout(row_w)
        row_h.setContentsMargins(0, 0, 0, 0)
        row_h.setSpacing(4)

        d_inp = _inp(row_w, depth)
        d_inp.setFixedWidth(80)
        t_inp = _inp(row_w, time)
        t_inp.setFixedWidth(80)

        del_btn = QPushButton("\u2715")
        del_btn.setFixedWidth(28)
        del_btn.setStyleSheet("background-color: #ffaaaa;")

        row_h.addWidget(d_inp)
        row_h.addWidget(t_inp)
        row_h.addWidget(del_btn)
        row_h.addStretch()

        row_data = {"d": d_inp, "t": t_inp, "widget": row_w}
        del_btn.clicked.connect(lambda: self._del_loop_row(row_data))

        idx = self._loop_vl.count() - 1
        self._loop_vl.insertWidget(idx, row_w)
        self._loop_seg_rows.append(row_data)

    def _del_loop_row(self, row_data: dict) -> None:
        row_data["widget"].deleteLater()
        if row_data in self._loop_seg_rows:
            self._loop_seg_rows.remove(row_data)

    def _calc_loop(self) -> None:
        segs = []
        for row in self._loop_seg_rows:
            d = _flt(row["d"].text(), 0.0)
            t = _flt(row["t"].text(), 0.0)
            if t > 0:
                segs.append((d, t))
        if not segs:
            return
        fO2_dil = _flt(self._loop_dil_o2.text(), 18.0) / 100.0
        fHe_dil = _flt(self._loop_dil_he.text(), 45.0) / 100.0
        sp      = _flt(self._loop_sp.text(),      1.3)
        results = gc.loop_gas_fractions(segs, fO2_dil, fHe_dil, sp)

        self._loop_table.setRowCount(0)
        for r_data in results:
            row = self._loop_table.rowCount()
            self._loop_table.insertRow(row)
            vals = [
                f"{r_data['depth']:.0f} m",
                f"{r_data['time']:.0f}",
                f"{r_data['p_amb']:.2f}",
                f"{r_data['pO2']:.3f}",
                f"{r_data['pN2']:.3f}",
                f"{r_data['pHe']:.3f}",
                f"{r_data['fN2_loop']*100:.1f}%",
                f"{r_data['fHe_loop']*100:.1f}%",
                f"{r_data['ead']:.0f} m",
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._loop_table.setItem(row, c, item)

    # =========================================================================
    # Tab 4: ICD Analysis
    # =========================================================================

    def _make_icd_tab(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(6)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)

        lv.addWidget(self._make_icd_inputs_group(left))
        lv.addWidget(self._make_icd_saved_mixes_group(left))
        lv.addWidget(self._make_icd_table_group(left))

        right = QGroupBox("\u0394PN\u2082 vs Depth")
        rv = QVBoxLayout(right)
        self._icd_chart = _ICDChartWidget()
        rv.addWidget(self._icd_chart)

        h.addWidget(left, 1)
        h.addWidget(right, 1)

        self._update_icd()
        return w

    # ── ICD Inputs & Switch Results ───────────────────────────────────────────

    def _make_icd_inputs_group(self, parent) -> QGroupBox:
        gb = QGroupBox("ICD Analysis \u2014 4 Gases / 3 Switches")
        vl = QVBoxLayout(gb)

        grid = QGridLayout()
        grid.setSpacing(3)

        COLORS = ["#d0e8ff", "#d0ffe8", "#fff0d0", "#f0d0ff"]
        gas_defaults = ["13/65", "21/35", "50/00", "100/00"]
        gas_labels   = ["Gas 1 O\u2082/He [%]", "Gas 2 O\u2082/He [%]",
                        "Gas 3 O\u2082/He [%]", "Gas 4 O\u2082/He [%]"]

        self._icd_gas_inputs: list = []   # QLineEdit
        self._icd_gas_checks: list = []   # QCheckBox

        for r, (lbl_txt, default, col) in enumerate(zip(gas_labels, gas_defaults, COLORS)):
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(
                f"background-color: {col}; border: 1px solid #aaa; padding: 2px 4px;"
            )
            grid.addWidget(lbl, r, 0)

            inp = QLineEdit(default)
            inp.setAlignment(Qt.AlignmentFlag.AlignRight)
            inp.setStyleSheet(f"background-color: {col};")
            inp.setFixedWidth(90)
            inp.textChanged.connect(self._update_icd)
            grid.addWidget(inp, r, 1)

            chk = QCheckBox()
            chk.setChecked(True)
            chk.stateChanged.connect(self._update_icd)
            grid.addWidget(chk, r, 2, Qt.AlignmentFlag.AlignHCenter)

            self._icd_gas_checks.append(chk)
            self._icd_gas_inputs.append(inp)

        self._icd_limit    = _inp(gb, "0.5")
        self._icd_interval = _inp(gb, "10")
        self._icd_limit.textChanged.connect(self._update_icd)
        self._icd_interval.textChanged.connect(self._update_icd)

        for r, (lbl_txt, w) in enumerate([
            ("\u0394PN\u2082 guideline [bar]",  self._icd_limit),
            ("Depth interval [m]",          self._icd_interval),
        ], start=4):
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px;")
            grid.addWidget(lbl, r, 0)
            grid.addWidget(w,   r, 1)

        vl.addLayout(grid)

        self._icd_switch_dpn2 = []
        self._icd_switch_safe = []

        return gb

    # ── ICD Interval Table ────────────────────────────────────────────────────

    def _make_icd_table_group(self, parent) -> QGroupBox:
        self._icd_table_gb = QGroupBox("\u0394PN\u2082 at N m Intervals")
        vl = QVBoxLayout(self._icd_table_gb)

        icd_cols = [
            "m", "PO\u2082 G1", "EAD G1", "\u0394PN\u2082 1\u21922",
            "PO\u2082 G2", "EAD G2", "\u0394PN\u2082 2\u21923",
            "PO\u2082 G3", "EAD G3", "\u0394PN\u2082 3\u21924",
            "PO\u2082 G4", "EAD G4",
        ]
        self._icd_dpn2_col_indices = {3, 6, 9}  # columns that are ΔPN₂

        self._icd_interval_table = QTableWidget(0, len(icd_cols))
        self._icd_interval_table.setHorizontalHeaderLabels(icd_cols)
        self._icd_interval_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._icd_interval_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._icd_interval_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        vl.addWidget(self._icd_interval_table)

        return self._icd_table_gb

    # ── ICD Update ────────────────────────────────────────────────────────────

    def _get_icd_gases(self):
        """Return list of (fO2, fHe) for all 4 gases."""
        defaults = [(13, 65), (21, 35), (50, 0), (100, 0)]
        result = []
        for inp, (do2, dhe) in zip(self._icd_gas_inputs, defaults):
            result.append(_parse_mix_str(inp.text(), float(do2), float(dhe)))
        return result

    def _update_icd(self) -> None:
        all_gases = self._get_icd_gases()
        enabled   = [chk.isChecked() for chk in self._icd_gas_checks]
        limit     = _flt(self._icd_limit.text(), 0.5)

        # Per-switch summaries
        for i, (dpn2_val, safe_val) in enumerate(
                zip(self._icd_switch_dpn2, self._icd_switch_safe)):
            if enabled[i] and enabled[i + 1]:
                fO2a, fHea = all_gases[i]
                fO2b, fHeb = all_gases[i + 1]
                dpn2_surf = gc.delta_pn2(fO2a, fHea, fO2b, fHeb, 0.0)
                safe_d    = gc.safe_switch_depth(fO2a, fHea, fO2b, fHeb, limit)
                dpn2_val.setText(f"{dpn2_surf:.3f}")
                if safe_d is None:
                    safe_val.setText("Always safe")
                    safe_val.setStyleSheet(
                        f"background-color: {CLR_OK}; border: 1px solid #aaa; padding: 2px 4px;"
                    )
                else:
                    safe_val.setText(f"{safe_d:.0f} m")
                    col = CLR_WARN if safe_d > 0 else CLR_OK
                    safe_val.setStyleSheet(
                        f"background-color: {col}; border: 1px solid #aaa; padding: 2px 4px;"
                    )
            else:
                dpn2_val.setText("\u2014")
                safe_val.setText("\u2014")
                safe_val.setStyleSheet(
                    f"background-color: {CLR_RESULT}; border: 1px solid #aaa; padding: 2px 4px;"
                )

        # Fill interval table
        interval = max(1, min(50, int(_flt(self._icd_interval.text(), 10.0))))
        self._icd_table_gb.setTitle(f"\u0394PN\u2082 at {interval} m Intervals")

        self._icd_interval_table.setRowCount(0)
        ROW_BG = ["#ffffff", "#eeeeee"]

        for r_idx, d in enumerate(range(0, 101, interval)):
            p_amb = d / 10.0 + 1.0
            po2_vals = [fo2 * p_amb for fo2, _ in all_gases]
            ead_vals = [gc.calc_ead(fo2, fhe, float(d)) for fo2, fhe in all_gases]
            row_bg_str = ROW_BG[r_idx % 2]

            row = self._icd_interval_table.rowCount()
            self._icd_interval_table.insertRow(row)

            # Build cell data: [depth, po2_g1, ead_g1, dpn2_12, po2_g2, ead_g2,
            #                   dpn2_23, po2_g3, ead_g3, dpn2_34, po2_g4, ead_g4]
            cell_vals = [str(d)]
            cell_bgs  = [row_bg_str]

            for i in range(3):
                cell_vals.append(f"{po2_vals[i]:.2f}" if enabled[i] else "\u2014")
                cell_bgs.append(row_bg_str)
                cell_vals.append(f"{ead_vals[i]:.0f}" if enabled[i] else "\u2014")
                cell_bgs.append(row_bg_str)
                if enabled[i] and enabled[i + 1]:
                    fO2a, fHea = all_gases[i]
                    fO2b, fHeb = all_gases[i + 1]
                    dpn2 = gc.delta_pn2(fO2a, fHea, fO2b, fHeb, float(d))
                    dpn2_s = f"{dpn2:.3f}"
                    if dpn2 > limit:
                        dpn2_bg = CLR_WARN
                    elif dpn2 > limit * 0.7:
                        dpn2_bg = CLR_CAUTION
                    else:
                        dpn2_bg = CLR_OK
                else:
                    dpn2_s  = "\u2014"
                    dpn2_bg = row_bg_str
                cell_vals.append(dpn2_s)
                cell_bgs.append(dpn2_bg)

            cell_vals.append(f"{po2_vals[3]:.2f}" if enabled[3] else "\u2014")
            cell_bgs.append(row_bg_str)
            cell_vals.append(f"{ead_vals[3]:.0f}" if enabled[3] else "\u2014")
            cell_bgs.append(row_bg_str)

            for c, (val, bg) in enumerate(zip(cell_vals, cell_bgs)):
                item = QTableWidgetItem(val)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignHCenter if c == 0
                    else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                item.setBackground(QColor(bg))
                self._icd_interval_table.setItem(row, c, item)

        # Update chart
        curves = []
        labels = []
        for i in range(3):
            if enabled[i] and enabled[i + 1]:
                fO2a, fHea = all_gases[i]
                fO2b, fHeb = all_gases[i + 1]
                curves.append(gc.icd_curve(fO2a, fHea, fO2b, fHeb, max_depth=100, step=1))
                o2a = int(round(fO2a * 100)); hea = int(round(fHea * 100))
                o2b = int(round(fO2b * 100)); heb = int(round(fHeb * 100))
                labels.append(f"G{i+1}→G{i+2}  {o2a}/{hea} → {o2b}/{heb}")
            else:
                curves.append([])
                labels.append(f"G{i+1}→G{i+2}")
        self._icd_chart.set_data(curves, limit, labels)

    # =========================================================================
    # Tab 5: Comparison & Tables
    # =========================================================================

    def _make_compare_tab(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(6, 6, 6, 6)
        vl.setSpacing(6)

        vl.addWidget(self._make_comparison_group(w))
        vl.addWidget(self._make_trimix_table_group(w), 1)
        return w

    # ── Mix Comparison ────────────────────────────────────────────────────────

    def _make_comparison_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Mix Comparison")
        vl = QVBoxLayout(gb)

        # Header
        mix_hdr = QWidget()
        mix_hdr.setStyleSheet(f"background-color: {CLR_HDR};")
        mix_hdr_h = QHBoxLayout(mix_hdr)
        mix_hdr_h.setContentsMargins(4, 2, 4, 2)
        for lbl_txt in ["O\u2082 [%]", "He [%]", "Label"]:
            lbl = QLabel(lbl_txt)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-weight: bold;")
            lbl.setFixedWidth(80)
            mix_hdr_h.addWidget(lbl)
        cmp_add_btn = QPushButton("+")
        cmp_add_btn.setFixedWidth(28)
        cmp_add_btn.clicked.connect(lambda: self._add_mix_row())
        mix_hdr_h.addWidget(cmp_add_btn)
        mix_hdr_h.addStretch()
        vl.addWidget(mix_hdr)

        # Scrollable mix rows
        self._mix_row_container = QWidget()
        self._mix_rows_vl = QVBoxLayout(self._mix_row_container)
        self._mix_rows_vl.setSpacing(2)
        self._mix_rows_vl.setContentsMargins(0, 0, 0, 0)
        self._mix_rows_vl.addStretch()
        vl.addWidget(self._mix_row_container)

        self._mix_rows: list = []
        for o2, he, lbl in [("21", "0", "Air"), ("21", "35", "Tx 21/35"),
                             ("18", "45", "Tx 18/45"), ("32", "0", "Nx 32")]:
            self._add_mix_row(o2, he, lbl)

        # Controls row
        ctrl = QWidget()
        ctrl_h = QHBoxLayout(ctrl)
        ctrl_h.setContentsMargins(0, 0, 0, 0)

        self._cmp_depth = _inp(gb, "40")
        self._cmp_ppo2  = _inp(gb, "1.4")

        for lbl_txt, w in [
            ("Eval depth [m]",   self._cmp_depth),
            ("PO\u2082 limit [bar]", self._cmp_ppo2),
        ]:
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px;")
            ctrl_h.addWidget(lbl)
            ctrl_h.addWidget(w)

        cmp_btn = QPushButton("Compare Mixes")
        cmp_btn.clicked.connect(self._run_comparison)
        ctrl_h.addWidget(cmp_btn)
        ctrl_h.addStretch()
        vl.addWidget(ctrl)

        cmp_cols = ["Label", "O\u2082%", "He%", "N\u2082%", "MOD [m]", "EAD [m]",
                    "Hyp.Floor", "PO\u2082", "CNS %/min", "OTU /min"]
        self._cmp_table = QTableWidget(0, len(cmp_cols))
        self._cmp_table.setHorizontalHeaderLabels(cmp_cols)
        self._cmp_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cmp_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._cmp_table.horizontalHeader().setStretchLastSection(True)
        self._cmp_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        vl.addWidget(self._cmp_table)

        return gb

    def _add_mix_row(self, o2: str = "21", he: str = "0", label: str = "") -> None:
        row_w = QWidget()
        row_h = QHBoxLayout(row_w)
        row_h.setContentsMargins(0, 0, 0, 0)
        row_h.setSpacing(4)

        o2_inp  = _inp(row_w, o2)
        o2_inp.setFixedWidth(80)
        he_inp  = _inp(row_w, he)
        he_inp.setFixedWidth(80)
        lbl_inp = QLineEdit(label)
        lbl_inp.setFixedWidth(110)

        del_btn = QPushButton("\u2715")
        del_btn.setFixedWidth(28)
        del_btn.setStyleSheet("background-color: #ffaaaa;")

        row_h.addWidget(o2_inp)
        row_h.addWidget(he_inp)
        row_h.addWidget(lbl_inp)
        row_h.addWidget(del_btn)
        row_h.addStretch()

        row_data = {"o2": o2_inp, "he": he_inp, "lbl": lbl_inp, "widget": row_w}
        del_btn.clicked.connect(lambda: self._del_mix_row(row_data))

        idx = self._mix_rows_vl.count() - 1
        self._mix_rows_vl.insertWidget(idx, row_w)
        self._mix_rows.append(row_data)

    def _del_mix_row(self, row_data: dict) -> None:
        row_data["widget"].deleteLater()
        if row_data in self._mix_rows:
            self._mix_rows.remove(row_data)

    def _run_comparison(self) -> None:
        mixes = []
        for row in self._mix_rows:
            fO2 = _flt(row["o2"].text(), 21.0) / 100.0
            fHe = _flt(row["he"].text(), 0.0)  / 100.0
            lbl = row["lbl"].text().strip() or f"Gas {len(mixes)+1}"
            if fO2 > 0:
                mixes.append((fO2, fHe, lbl))
        if not mixes:
            return
        depth = _flt(self._cmp_depth.text(), 40.0)
        ppo2  = _flt(self._cmp_ppo2.text(),  1.4)
        results = gc.compare_mixes(mixes, depth, ppo2)

        self._cmp_table.setRowCount(0)
        for r_data in results:
            row = self._cmp_table.rowCount()
            self._cmp_table.insertRow(row)
            vals = [
                r_data["label"],
                f"{r_data['fO2']*100:.0f}",
                f"{r_data['fHe']*100:.0f}",
                f"{r_data['fN2']*100:.0f}",
                f"{r_data['mod']:.0f}",
                f"{r_data['ead']:.0f}",
                f"{r_data['hypoxic_floor']:.1f}",
                f"{r_data['po2_at_depth']:.2f}",
                f"{r_data['cns_rate_at_depth']:.4f}",
                f"{r_data['otu_rate_at_depth']:.4f}",
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter if c == 0
                    else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._cmp_table.setItem(row, c, item)

    # ── Trimix Table Generator ────────────────────────────────────────────────

    def _make_trimix_table_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Trimix Table Generator")
        vl = QVBoxLayout(gb)

        # Parameters grid (2 columns of label+input pairs)
        param_grid = QGridLayout()
        param_grid.setSpacing(3)

        self._tt_depth   = _inp(gb, "50")
        self._tt_ppo2b   = _inp(gb, "1.3")
        self._tt_ppo2l   = _inp(gb, "1.4")
        self._tt_ead     = _inp(gb, "30")
        self._tt_he_step = _inp(gb, "5")
        self._tt_o2_step = _inp(gb, "1")

        tt_fields = [
            ("Target depth [m]",      self._tt_depth),
            ("PO\u2082 at bottom [bar]", self._tt_ppo2b),
            ("PO\u2082 limit [bar]",     self._tt_ppo2l),
            ("EAD limit [m]",            self._tt_ead),
            ("He step [%]",              self._tt_he_step),
            ("O\u2082 step [%]",         self._tt_o2_step),
        ]
        for i, (lbl_txt, w) in enumerate(tt_fields):
            row_g = i // 2
            col_offset = (i % 2) * 2
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(
                f"background-color: {CLR_LABEL}; border: 1px solid #aaa; padding: 2px 4px;"
            )
            param_grid.addWidget(lbl, row_g, col_offset)
            param_grid.addWidget(w,   row_g, col_offset + 1)

        vl.addLayout(param_grid)

        # Button row
        btn_row = QWidget()
        btn_row_h = QHBoxLayout(btn_row)
        btn_row_h.setContentsMargins(0, 0, 0, 0)

        gen_btn = QPushButton("Generate Table")
        gen_btn.clicked.connect(self._gen_trimix_table)
        btn_row_h.addWidget(gen_btn)

        exp_btn = QPushButton("Export CSV")
        exp_btn.setStyleSheet("background-color: #aaddff;")
        exp_btn.clicked.connect(self._export_csv)
        btn_row_h.addWidget(exp_btn)

        self._tt_count_lbl = QLabel("Click 'Generate Table' to start")
        btn_row_h.addWidget(self._tt_count_lbl)
        btn_row_h.addStretch()
        vl.addWidget(btn_row)

        # Filter row
        filt_row = QWidget()
        filt_row_h = QHBoxLayout(filt_row)
        filt_row_h.setContentsMargins(0, 0, 0, 0)
        filt_row_h.addWidget(QLabel("Filter (text):"))
        self._tt_filter = QLineEdit()
        self._tt_filter.setFixedWidth(200)
        self._tt_filter.textChanged.connect(self._apply_filter)
        filt_row_h.addWidget(self._tt_filter)
        filt_row_h.addStretch()
        vl.addWidget(filt_row)

        # Table
        tt_cols = ["Mix", "O\u2082%", "He%", "N\u2082%", "MOD [m]", "EAD [m]",
                   "Hyp.Floor", "PO\u2082 at depth", "CNS %/min", "OTU /min"]
        self._tt_table = QTableWidget(0, len(tt_cols))
        self._tt_table.setHorizontalHeaderLabels(tt_cols)
        self._tt_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tt_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._tt_table.horizontalHeader().setStretchLastSection(True)
        self._tt_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        vl.addWidget(self._tt_table, 1)

        self._trimix_data: list = []
        return gb

    def _gen_trimix_table(self) -> None:
        depth   = _flt(self._tt_depth.text(),   50.0)
        ppo2b   = _flt(self._tt_ppo2b.text(),   1.3)
        ppo2l   = _flt(self._tt_ppo2l.text(),   1.4)
        ead     = _flt(self._tt_ead.text(),      30.0)
        he_step = max(1, _int_val(self._tt_he_step.text(), 5))
        o2_step = max(1, _int_val(self._tt_o2_step.text(), 1))

        self._trimix_data = gc.generate_trimix_table(
            target_depth_m=depth, ppo2_bottom=ppo2b,
            ppo2_limit=ppo2l, ead_limit_m=ead,
            he_step=he_step, o2_step=o2_step,
        )
        self._tt_count_lbl.setText(f"{len(self._trimix_data)} mixes")
        self._apply_filter()

    def _apply_filter(self) -> None:
        filt = self._tt_filter.text().strip().lower()
        self._tt_table.setRowCount(0)
        for r_data in self._trimix_data:
            if filt:
                if filt not in r_data["label"].lower():
                    row_str = " ".join(str(v) for v in r_data.values()).lower()
                    if filt not in row_str:
                        continue
            row = self._tt_table.rowCount()
            self._tt_table.insertRow(row)
            vals = [
                r_data["label"],
                f"{r_data['o2_pct']}",
                f"{r_data['he_pct']}",
                f"{r_data['n2_pct']:.0f}",
                f"{r_data['mod']:.0f}",
                f"{r_data['ead']:.0f}",
                f"{r_data['hypoxic_floor']:.1f}",
                f"{r_data['po2_at_depth']:.2f}",
                f"{r_data['cns_rate']:.4f}",
                f"{r_data['otu_rate']:.4f}",
            ]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter if c == 0
                    else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._tt_table.setItem(row, c, item)

    def _export_csv(self) -> None:
        if not self._trimix_data:
            QMessageBox.information(self, "Export CSV", "Generate a table first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Trimix Table", "",
            "CSV files (*.csv);;All files (*.*)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self._trimix_data[0].keys())
                writer.writeheader()
                writer.writerows(self._trimix_data)
            QMessageBox.information(
                self, "Export CSV",
                f"Saved {len(self._trimix_data)} rows to:\n{path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export CSV", f"Failed to save:\n{e}")

    # ── Saved Mixes (inside ICD tab) ──────────────────────────────────────────

    def _make_icd_saved_mixes_group(self, parent) -> QGroupBox:
        gb = QGroupBox("Saved mixes")
        gb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        vl = QVBoxLayout(gb)
        vl.setContentsMargins(6, 4, 6, 4)
        vl.setSpacing(3)

        # Save row
        hl = QHBoxLayout()
        hl.setSpacing(4)
        hl.addWidget(QLabel("Name:"))
        self._mix_name_inp = QLineEdit()
        self._mix_name_inp.setPlaceholderText("e.g. Deep trimix 11/65 bailout")
        hl.addWidget(self._mix_name_inp, 1)
        save_btn = QPushButton("Save current")
        save_btn.setStyleSheet("background:#4a7a3a; color:white; font-weight:bold; padding:2px 10px;")
        save_btn.clicked.connect(self._save_current_mix)
        hl.addWidget(save_btn)
        vl.addLayout(hl)

        # Table of saved sets
        cols = ["Name", "Gas 1", "Gas 2", "Gas 3", "Gas 4", "", ""]
        self._mix_table = QTableWidget(0, len(cols))
        self._mix_table.setHorizontalHeaderLabels(cols)
        self._mix_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._mix_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._mix_table.setMaximumHeight(90)
        self._mix_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 5):
            self._mix_table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.ResizeToContents)
        for c in (5, 6):
            self._mix_table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.Fixed)
            self._mix_table.setColumnWidth(c, 70)
        vl.addWidget(self._mix_table)

        self._refresh_mix_table()
        if self._db.get("gas_mixes"):
            self._load_mix(0)
        return gb

    def _save_current_mix(self):
        name = self._mix_name_inp.text().strip()
        if not name:
            QMessageBox.warning(self, "Save mix", "Please enter a name.")
            return
        gases = [inp.text().strip() for inp in self._icd_gas_inputs]
        self._db.setdefault("gas_mixes", []).append({"name": name, "gases": gases})
        self._save_fn()
        self._mix_name_inp.clear()
        self._refresh_mix_table()

    def _load_mix(self, row_idx: int):
        mixes = self._db.get("gas_mixes", [])
        if row_idx >= len(mixes):
            return
        for inp, val in zip(self._icd_gas_inputs, mixes[row_idx].get("gases", [])):
            inp.setText(val)

    def _delete_mix(self, row_idx: int):
        mixes = self._db.get("gas_mixes", [])
        if row_idx >= len(mixes):
            return
        if QMessageBox.question(
            self, "Delete mix", f"Delete '{mixes[row_idx]['name']}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        mixes.pop(row_idx)
        self._save_fn()
        self._refresh_mix_table()

    def _refresh_mix_table(self):
        mixes = self._db.get("gas_mixes", [])
        self._mix_table.setRowCount(0)
        for i, entry in enumerate(mixes):
            r = self._mix_table.rowCount()
            self._mix_table.insertRow(r)
            self._mix_table.setItem(r, 0, QTableWidgetItem(entry.get("name", "")))
            for c, val in enumerate(entry.get("gases", [])[:4], start=1):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._mix_table.setItem(r, c, item)
            load_btn = QPushButton("Load")
            load_btn.setStyleSheet("background:#3a6a9a; color:white; font-size:9px; padding:2px 4px;")
            load_btn.clicked.connect(lambda _, idx=i: self._load_mix(idx))
            self._mix_table.setCellWidget(r, 5, load_btn)
            del_btn = QPushButton("Delete")
            del_btn.setStyleSheet("background:#8a2a2a; color:white; font-size:9px; padding:2px 4px;")
            del_btn.clicked.connect(lambda _, idx=i: self._delete_mix(idx))
            self._mix_table.setCellWidget(r, 6, del_btn)
