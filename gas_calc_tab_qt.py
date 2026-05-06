"""
gas_calc_tab_qt.py — Gas Planner UI tab for JJ TechDivePlanner (PyQt6)
=======================================================================

Single class GasCalcTabQt(QWidget) — ICD Analysis.
"""

from __future__ import annotations
import csv

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
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
    """Gas Planner — ICD Analysis tab."""

    def __init__(self, parent=None, db=None, save_fn=None):
        super().__init__(parent)
        self._db      = db if db is not None else {}
        self._save_fn = save_fn or (lambda: None)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._make_icd_tab())

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
        if not hasattr(self, "_icd_table_gb"):
            return
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
