"""
CCR TechDivePlanner — PyQt6 rewrite
main_qt.py — Main window + Databases tab (fully implemented)
"""

import sys
import json
import copy
from pathlib import Path
from cylindercalc import calc_gas_mass, z_mix, RHO_FW, RHO_SW, RHO_PB

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
    QScrollArea, QFrame, QDialog, QDialogButtonBox, QMessageBox,
    QCheckBox, QGroupBox, QSizePolicy, QSpacerItem, QComboBox,
    QHeaderView, QTableWidget, QTableWidgetItem, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont, QPalette

from dive_planner_tab import DivePlannerTab
from gas_calc_tab_qt import GasCalcTabQt

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "cylindercalc_db.json"

# ── Colours ───────────────────────────────────────────────────────────────────
CLR_BG         = "#f0f0f0"
CLR_HDR        = "#c8dde8"
CLR_TOOLBAR    = "#e8eaed"
CLR_TOOLBAR_EQ = "#f0ead8"
CLR_TOOLBAR_DV = "#e0f0e8"
CLR_RESULT     = "#e8f4e8"
CLR_ADD        = "#aaddaa"
CLR_DEL        = "#ffaaaa"
CLR_EDIT       = "#aabbff"
CLR_ROW_ODD    = "#ffffff"
CLR_ROW_EVEN   = "#f0f4f8"

# ── Stylesheet ────────────────────────────────────────────────────────────────
STYLE = """
QMainWindow { background: #f0f0f0; }
QTabWidget::pane { border: 1px solid #aaaaaa; }
QTabBar::tab {
    padding: 6px 18px;
    background: #dde8ee;
    border: 1px solid #aaaaaa;
    border-bottom: none;
    font-weight: bold;
}
QTabBar::tab:selected { background: #ffffff; }
QGroupBox {
    font-weight: bold;
    border: 1px solid #aaaaaa;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 6px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}
QPushButton {
    padding: 4px 12px;
    border: 1px solid #888;
    border-radius: 3px;
    font-weight: bold;
}
QPushButton:hover { border: 1px solid #444; }
QLineEdit {
    border: 1px solid #aaa;
    border-radius: 2px;
    padding: 2px 4px;
}
QTableWidget {
    gridline-color: #cccccc;
    selection-background-color: #bbddff;
    selection-color: black;
}
QHeaderView::section {
    background: #c8dde8;
    font-weight: bold;
    border: 1px solid #aaaaaa;
    padding: 4px;
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Database I/O
# ─────────────────────────────────────────────────────────────────────────────

def load_db() -> dict:
    if DB_PATH.exists():
        with open(DB_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"cylinders": [], "equipment": [], "divers": []}


def save_db(db: dict) -> None:
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def migrate_baseline_buoyancy_flag(db: dict) -> None:
    """One-time migration: set baseline_buoyancy=True on all existing diver records.
    Equipment items (lead, canisters) are intentionally excluded.
    """
    if db.get("_baseline_buoyancy_migrated"):
        return
    for diver in db.get("divers", []):
        diver.setdefault("baseline_buoyancy", True)
    db["_baseline_buoyancy_migrated"] = True
    save_db(db)


# ─────────────────────────────────────────────────────────────────────────────
# Helper widgets
# ─────────────────────────────────────────────────────────────────────────────

def _btn(text: str, color: str, callback) -> QPushButton:
    b = QPushButton(text)
    b.setStyleSheet(f"background:{color};")
    b.clicked.connect(callback)
    return b


def _hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


# ─────────────────────────────────────────────────────────────────────────────
# Edit dialogs
# ─────────────────────────────────────────────────────────────────────────────

def _compute_cyl(dry, wet, vbot, o2, he, pres, temp):
    """Replicate _compute() from cylindercalc_gui.py."""
    delta = dry - wet
    vol   = delta / RHO_FW
    sw    = vol * RHO_SW
    Z     = z_mix(pres + 1.0, temp + 273.15, o2, he)
    gm    = calc_gas_mass(vbot, pres, temp, o2, he)
    em    = dry - gm
    return {
        "dry_mass":      dry,      "wet_mass":      wet,
        "delta_mass":    delta,    "rho_fw":        RHO_FW,
        "volume":        vol,      "rho_sw":        RHO_SW,
        "sw_mass":       sw,       "volume_bottle": vbot,
        "o2":            o2,       "he":            he,
        "pressure":      pres,     "temp":          temp,
        "Z":             Z,        "gas_mass":      gm,
        "empty_mass":    em,
        "empty_buoy_fw": vol * RHO_FW - em,
        "empty_buoy_sw": vol * RHO_SW - em,
        "buoyancy_fw":   vol * RHO_FW - dry,
        "buoyancy_sw":   vol * RHO_SW - dry,
    }


def _fmt_cyl(key, val):
    if val is None:                        return "---"
    if key in ("o2", "he"):                return f"{int(val)}"
    if key in ("rho_fw", "rho_sw"):        return f"{val:.3f}"
    if key == "Z":                         return f"{val:.4f}"
    if key == "temp":                      return f"{val:.0f}"
    return f"{val:.3f}"


# Preview rows: (label, key, is_result_green)
_CYL_PREVIEW_ROWS = [
    ("Dry mass [kg]",                  "dry_mass",      False),
    ("Wet mass FW [kg]",               "wet_mass",      False),
    ("Delta mass [kg]",                "delta_mass",    False),
    ("Density fresh water [kg/L]",     "rho_fw",        False),
    ("Volume [L]",                     "volume",        False),
    ("Density saltwater [kg/L]",       "rho_sw",        False),
    ("Saltwater mass [kg]",            "sw_mass",       False),
    ("Volume bottle [L]",              "volume_bottle", False),
    ("O₂ [%]",                        "o2",            False),
    ("He [%]",                         "he",            False),
    ("Pressure [barg]",                "pressure",      False),
    ("Temperature [°C]",               "temp",          False),
    ("Z (compressibility)",            "Z",             False),
    ("Gas mass [kg]",                  "gas_mass",      False),
    ("Mass on land, empty [kg]",       "empty_mass",    False),
    ("Empty buoyancy FW ref [kg]",     "empty_buoy_fw", True),
    ("Empty buoyancy SW ref [kg]",     "empty_buoy_sw", True),
    ("Buoyancy in freshwater [kg]",    "buoyancy_fw",   True),
    ("Buoyancy in saltwater [kg]",     "buoyancy_sw",   True),
]


class CylinderDialog(QDialog):
    """Add / edit a cylinder — with live calculated preview."""

    INPUT_FIELDS = [
        ("Name",             "name",          str),
        ("Dry mass [kg]",    "dry_mass",      float),
        ("Wet mass FW [kg]", "wet_mass",      float),
        ("Volume bottle [L]","volume_bottle", float),
        ("O₂ [%]",          "o2",            int),
        ("He [%]",           "he",            int),
        ("Pressure [barg]",  "pressure",      float),
        ("Temperature [°C]", "temp",          float),
        ("Description",      "description",   str),
    ]

    def __init__(self, parent, data: dict | None = None):
        super().__init__(parent)
        self._is_edit = data is not None
        self.setWindowTitle("Edit Cylinder" if self._is_edit else "Add New Cylinder")
        self.setMinimumWidth(700)
        self._data = copy.deepcopy(data) if data else {}
        self._entries: dict[str, QLineEdit] = {}
        self._preview_labels: dict[str, QLabel] = {}

        main_lay = QHBoxLayout(self)

        # ── Left: inputs ──────────────────────────────────────────────────────
        left_box = QGroupBox("Input values")
        left_lay = QGridLayout(left_box)
        main_lay.addWidget(left_box)

        for r, (label, key, typ) in enumerate(self.INPUT_FIELDS):
            left_lay.addWidget(QLabel(label + ":"), r, 0)
            val = self._data.get(key, "")
            le = QLineEdit("" if val is None else str(val))
            le.setStyleSheet("background:#ffe0e0;")
            if key == "name":
                le.setMaxLength(10)
            le.textChanged.connect(self._recalc)
            self._entries[key] = le
            left_lay.addWidget(le, r, 1)

        # Category checkboxes
        self._cb_jj    = QCheckBox("JJ Cylinders")
        self._cb_stage = QCheckBox("Stage Buoyancy")
        cat = self._data.get("category", "") or ""
        self._cb_jj.setChecked("JJ Cylinders" in cat)
        self._cb_stage.setChecked("Stage Buoyancy" in cat)
        self._cb_jj.stateChanged.connect(self._recalc)
        self._cb_stage.stateChanged.connect(self._recalc)
        left_lay.addWidget(self._cb_jj,    len(self.INPUT_FIELDS),     0, 1, 2)
        left_lay.addWidget(self._cb_stage, len(self.INPUT_FIELDS) + 1, 0, 1, 2)

        # Counterweight for positive cylinders
        self._cb_cw = QCheckBox("Counterweight for positive cylinders")
        self._cb_cw.stateChanged.connect(self._on_cw_toggled)
        left_lay.addWidget(self._cb_cw, len(self.INPUT_FIELDS) + 2, 0, 1, 2)

        self._lbl_cw_dry = QLabel("Counterweight dry mass [kg]:")
        self._le_cw_dry  = QLineEdit("")
        self._le_cw_dry.setStyleSheet("background:#ffe0e0;")
        self._le_cw_dry.textChanged.connect(self._recalc)

        self._lbl_cw_wet = QLabel("Counterweight wet mass FW [kg]:")
        self._le_cw_wet  = QLineEdit("")
        self._le_cw_wet.setStyleSheet("background:#ffe0e0;")
        self._le_cw_wet.textChanged.connect(self._recalc)

        self._lbl_cw_combined = QLabel("Combined wet mass FW [kg]:")
        self._le_cw_combined  = QLineEdit("")
        self._le_cw_combined.setStyleSheet("background:#ffe0e0;")
        self._le_cw_combined.textChanged.connect(self._recalc)

        left_lay.addWidget(self._lbl_cw_dry,      len(self.INPUT_FIELDS) + 3, 0)
        left_lay.addWidget(self._le_cw_dry,        len(self.INPUT_FIELDS) + 3, 1)
        left_lay.addWidget(self._lbl_cw_wet,       len(self.INPUT_FIELDS) + 4, 0)
        left_lay.addWidget(self._le_cw_wet,        len(self.INPUT_FIELDS) + 4, 1)
        left_lay.addWidget(self._lbl_cw_combined,  len(self.INPUT_FIELDS) + 5, 0)
        left_lay.addWidget(self._le_cw_combined,   len(self.INPUT_FIELDS) + 5, 1)

        self._lbl_cw_dry.setVisible(False)
        self._le_cw_dry.setVisible(False)
        self._lbl_cw_wet.setVisible(False)
        self._le_cw_wet.setVisible(False)
        self._lbl_cw_combined.setVisible(False)
        self._le_cw_combined.setVisible(False)

        # Buttons
        btn_lay = QHBoxLayout()
        label_txt = "Save" if self._is_edit else "Add to database"
        ok_btn = QPushButton(label_txt)
        ok_btn.setStyleSheet(f"background:{CLR_ADD};")
        ok_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_lay.addWidget(ok_btn)
        btn_lay.addWidget(cancel_btn)
        left_lay.addLayout(btn_lay, len(self.INPUT_FIELDS) + 6, 0, 1, 2)

        # ── Right: calculated preview ─────────────────────────────────────────
        right_box = QGroupBox("Calculated preview")
        right_lay = QGridLayout(right_box)
        right_lay.setColumnStretch(0, 3)
        right_lay.setColumnStretch(1, 2)
        main_lay.addWidget(right_box)

        for r, (lbl, key, green) in enumerate(_CYL_PREVIEW_ROWS):
            right_lay.addWidget(QLabel(lbl + ":"), r, 0)
            val_lbl = QLabel("---")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                                 Qt.AlignmentFlag.AlignVCenter)
            val_lbl.setFrameShape(QFrame.Shape.StyledPanel)
            val_lbl.setMinimumWidth(90)
            bg = "#dff0df" if green else "#f0f0f0"
            val_lbl.setStyleSheet(f"background:{bg}; padding: 1px 4px;")
            right_lay.addWidget(val_lbl, r, 1)
            self._preview_labels[key] = val_lbl

        self._recalc()

    def _on_cw_toggled(self):
        visible = self._cb_cw.isChecked()
        self._lbl_cw_dry.setVisible(visible)
        self._le_cw_dry.setVisible(visible)
        self._lbl_cw_wet.setVisible(visible)
        self._le_cw_wet.setVisible(visible)
        self._lbl_cw_combined.setVisible(visible)
        self._le_cw_combined.setVisible(visible)
        wet_entry = self._entries["wet_mass"]
        if visible:
            wet_entry.setReadOnly(True)
            wet_entry.setStyleSheet("background:#e0e0e0;")
        else:
            wet_entry.setReadOnly(False)
            wet_entry.setStyleSheet("background:#ffe0e0;")
            wet_entry.clear()
        self._recalc()

    def _recalc(self):
        if self._cb_cw.isChecked():
            try:
                cw_wet     = float(self._le_cw_wet.text().replace(",","."))
                combined   = float(self._le_cw_combined.text().replace(",","."))
                cyl_wet    = combined - cw_wet
                self._entries["wet_mass"].setText(f"{cyl_wet:.4f}")
            except ValueError:
                self._entries["wet_mass"].setText("")
        try:
            dry  = float(self._entries["dry_mass"].text().replace(",","."))
            wet  = float(self._entries["wet_mass"].text().replace(",","."))
            vbot = float(self._entries["volume_bottle"].text().replace(",","."))
            o2   = int(float(self._entries["o2"].text().replace(",",".")))
            he   = int(float(self._entries["he"].text().replace(",",".")))
            pres = float(self._entries["pressure"].text().replace(",","."))
            temp = float(self._entries["temp"].text().replace(",","."))
            r = _compute_cyl(dry, wet, vbot, o2, he, pres, temp)
            for key, lbl in self._preview_labels.items():
                lbl.setText(_fmt_cyl(key, r.get(key)))
        except (ValueError, ZeroDivisionError, KeyError):
            fixed = {"rho_fw": f"{RHO_FW:.3f}", "rho_sw": f"{RHO_SW:.3f}"}
            for key, lbl in self._preview_labels.items():
                lbl.setText(fixed.get(key, "---"))

    def _category(self) -> str:
        tags = []
        if self._cb_jj.isChecked():    tags.append("JJ Cylinders")
        if self._cb_stage.isChecked(): tags.append("Stage Buoyancy")
        return " | ".join(tags)

    def _accept(self):
        for label, key, typ in self.INPUT_FIELDS:
            val = self._entries[key].text().strip().replace(",",".")
            try:
                self._data[key] = typ(val) if val else (typ() if typ != str else "")
            except ValueError:
                QMessageBox.warning(self, "Invalid input",
                                    f"'{label}' must be a number.")
                return
        self._data["category"] = self._category()
        self.accept()

    def result_data(self) -> dict:
        return self._data


def _compute_equip(dry, wet):
    delta = dry - wet
    vol   = delta / RHO_FW
    sw    = vol * RHO_SW
    return {
        "dry_mass":    dry,   "wet_mass":    wet,
        "delta_mass":  delta, "rho_fw":      RHO_FW,
        "volume":      vol,   "rho_sw":      RHO_SW,
        "sw_mass":     sw,
        "buoyancy_fw": vol * RHO_FW - dry,
        "buoyancy_sw": vol * RHO_SW - dry,
    }


_EQUIP_PREVIEW_ROWS = [
    ("Dry mass [kg]",              "dry_mass",    False),
    ("Wet mass FW [kg]",           "wet_mass",    False),
    ("Delta mass [kg]",            "delta_mass",  False),
    ("Density fresh water [kg/L]", "rho_fw",      False),
    ("Volume [L]",                 "volume",      False),
    ("Density saltwater [kg/L]",   "rho_sw",      False),
    ("Saltwater mass [kg]",        "sw_mass",     False),
    ("Buoyancy in freshwater [kg]","buoyancy_fw", True),
    ("Buoyancy in saltwater [kg]", "buoyancy_sw", True),
    ("Category",                   "category",    False),
    ("Description",                "description", False),
]


class EquipmentDialog(QDialog):
    """Add / edit an equipment item — with live calculated preview."""

    INPUT_FIELDS = [
        ("Name",             "name",        str),
        ("Dry mass [kg]",    "dry_mass",    float),
        ("Wet mass FW [kg]", "wet_mass",    float),
        ("Description",      "description", str),
    ]
    CHECKS = [
        ("JJ Core Buoyancy",    "jj_core"),
        ("JJ Modular Buoyancy", "jj_modular"),
        ("Diver Buoyancy",      "diver_buoyancy"),
        ("Stage Buoyancy",      "stage"),
        ("Baseline Buoyancy",   "baseline_buoyancy"),
    ]
    _BASELINE_TOOLTIP = (
        "Marks this element as the source of drysuit baseline lift in the inflation "
        "gas calculation. Only one element per Buoyancy Plan should have this flag."
    )

    def __init__(self, parent, data: dict | None = None, db: dict | None = None):
        super().__init__(parent)
        self._is_edit = data is not None
        self.setWindowTitle("Edit Equipment" if self._is_edit else "Add Equipment")
        self.setMinimumWidth(680)
        self._data = copy.deepcopy(data) if data else {}
        self._db   = db
        self._entries: dict[str, QLineEdit] = {}
        self._checks: dict[str, QCheckBox] = {}
        self._preview_labels: dict[str, QLabel] = {}

        main_lay = QHBoxLayout(self)

        # ── Left: inputs ──────────────────────────────────────────────────────
        left_box = QGroupBox("Input values")
        left_lay = QGridLayout(left_box)
        main_lay.addWidget(left_box)

        for r, (label, key, typ) in enumerate(self.INPUT_FIELDS):
            left_lay.addWidget(QLabel(label + ":"), r, 0)
            val = self._data.get(key, "")
            le = QLineEdit("" if val is None else str(val))
            le.setStyleSheet("background:#ffe0e0;")
            le.textChanged.connect(self._recalc)
            self._entries[key] = le
            left_lay.addWidget(le, r, 1)

        # Checkboxes
        r = len(self.INPUT_FIELDS)
        for label, key in self.CHECKS:
            cb = QCheckBox(label)
            cb.setChecked(bool(self._data.get(key, False)))
            cb.stateChanged.connect(self._recalc)
            if key == "baseline_buoyancy":
                cb.setToolTip(self._BASELINE_TOOLTIP)
            self._checks[key] = cb
            left_lay.addWidget(cb, r, 0, 1, 2)
            r += 1

        # Bly-kalkulator sub-section
        lead_box = QGroupBox("Bly-kalkulator")
        lead_lay = QGridLayout(lead_box)
        lead_lay.addWidget(QLabel("Bly tørrvekt [kg]:"), 0, 0)
        self._lead_entry = QLineEdit()
        self._lead_entry.setStyleSheet("background:#ffe0e0;")
        self._lead_entry.textChanged.connect(self._recalc_lead)
        lead_lay.addWidget(self._lead_entry, 0, 1)
        lead_lay.addWidget(QLabel("Wet mass FW [kg]:"), 1, 0)
        self._lead_wet_lbl = QLabel("---")
        self._lead_wet_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self._lead_wet_lbl.setStyleSheet("background:#f0f0f0; padding:2px 4px;")
        self._lead_wet_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                                        Qt.AlignmentFlag.AlignVCenter)
        lead_lay.addWidget(self._lead_wet_lbl, 1, 1)
        left_lay.addWidget(lead_box, r, 0, 1, 2)
        r += 1

        # Buttons
        btn_lay = QHBoxLayout()
        label_txt = "Save" if self._is_edit else "Add to database"
        ok_btn = QPushButton(label_txt)
        ok_btn.setStyleSheet(f"background:{CLR_ADD};")
        ok_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_lay.addWidget(ok_btn)
        btn_lay.addWidget(cancel_btn)
        left_lay.addLayout(btn_lay, r, 0, 1, 2)

        # ── Right: calculated preview ─────────────────────────────────────────
        right_box = QGroupBox("Calculated preview")
        right_lay = QGridLayout(right_box)
        right_lay.setColumnStretch(0, 3)
        right_lay.setColumnStretch(1, 2)
        main_lay.addWidget(right_box)

        for row, (lbl, key, green) in enumerate(_EQUIP_PREVIEW_ROWS):
            right_lay.addWidget(QLabel(lbl + ":"), row, 0)
            val_lbl = QLabel("---")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                                 Qt.AlignmentFlag.AlignVCenter)
            val_lbl.setFrameShape(QFrame.Shape.StyledPanel)
            val_lbl.setMinimumWidth(90)
            bg = "#dff0df" if green else "#f0f0f0"
            val_lbl.setStyleSheet(f"background:{bg}; padding:1px 4px;")
            right_lay.addWidget(val_lbl, row, 1)
            self._preview_labels[key] = val_lbl

        self._recalc()

    def _category(self) -> str:
        tags = [lbl for lbl, key in self.CHECKS if self._checks[key].isChecked()]
        return " | ".join(tags)

    def _recalc(self):
        try:
            dry = float(self._entries["dry_mass"].text().replace(",","."))
            wet = float(self._entries["wet_mass"].text().replace(",","."))
            r = _compute_equip(dry, wet)
            for key, lbl in self._preview_labels.items():
                if key == "category":
                    lbl.setText(self._category())
                elif key == "description":
                    lbl.setText(self._entries["description"].text())
                else:
                    lbl.setText(_fmt_cyl(key, r.get(key)))
        except (ValueError, ZeroDivisionError):
            fixed = {"rho_fw": f"{RHO_FW:.3f}", "rho_sw": f"{RHO_SW:.3f}"}
            for key, lbl in self._preview_labels.items():
                lbl.setText(fixed.get(key, "---"))

    def _recalc_lead(self):
        try:
            dry = float(self._lead_entry.text().replace(",","."))
            vol = dry / RHO_PB
            wet_fw = dry - vol * RHO_FW
            self._lead_wet_lbl.setText(f"{wet_fw:.3f}")
        except (ValueError, ZeroDivisionError):
            self._lead_wet_lbl.setText("---")

    def _accept(self):
        for label, key, typ in self.INPUT_FIELDS:
            val = self._entries[key].text().strip().replace(",",".")
            try:
                self._data[key] = typ(val) if val else (typ() if typ != str else "")
            except ValueError:
                QMessageBox.warning(self, "Invalid input",
                                    f"'{label}' must be a number.")
                return
        for label, key in self.CHECKS:
            self._data[key] = self._checks[key].isChecked()
        self._data["category"] = self._category()
        if self._data.get("baseline_buoyancy") and self._db is not None:
            current_name = self._data.get("name", "")
            conflict = next(
                (e["name"] for e in self._db.get("equipment", [])
                 if e.get("baseline_buoyancy") and e.get("name") != current_name),
                None,
            ) or next(
                (d["name"] for d in self._db.get("divers", [])
                 if d.get("baseline_buoyancy") and d.get("name") != current_name),
                None,
            )
            if conflict:
                reply = QMessageBox.question(
                    self, "Duplicate Baseline Buoyancy",
                    f"'{conflict}' already has Baseline Buoyancy flagged.\n"
                    "Continue and have two? (Multiple flags will cause a warning "
                    "in the inflation model.)",
                    QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
                )
                if reply != QMessageBox.StandardButton.Ok:
                    return
        self.accept()

    def result_data(self) -> dict:
        return self._data


def _compute_diver(lead_dry, diver_dry=None):
    v_lead = lead_dry / RHO_PB
    lb_sw  = v_lead * RHO_SW - lead_dry
    db_sw  = -lb_sw
    v_diver = db_fw = None
    if diver_dry is not None:
        v_diver = (diver_dry + lead_dry) / RHO_SW - v_lead
        db_fw   = v_diver * RHO_FW - diver_dry
    return {
        "lead_dry_mass":     lead_dry,
        "diver_dry_mass":    diver_dry,
        "rho_pb":            RHO_PB,
        "lead_volume":       v_lead,
        "diver_volume":      v_diver,
        "lead_buoyancy_sw":  lb_sw,
        "diver_buoyancy_fw": db_fw,
        "diver_buoyancy_sw": db_sw,
    }


_DIVER_PREVIEW_ROWS = [
    ("Lead dry weight [kg]",       "lead_dry_mass",     False),
    ("Diver dry mass [kg]",        "diver_dry_mass",    False),
    ("Lead density [kg/L]",        "rho_pb",            False),
    ("Lead volume [L]",            "lead_volume",       False),
    ("Diver volume [L]",           "diver_volume",      False),
    ("Lead buoyancy SW [kg]",      "lead_buoyancy_sw",  False),
    ("Diver buoyancy FW ref [kg]", "diver_buoyancy_fw", True),
    ("Diver buoyancy SW ref [kg]", "diver_buoyancy_sw", True),
    ("Category",                   "category",          False),
]


class DiverDialog(QDialog):
    """Add / edit a diver buoyancy profile — with live calculated preview."""

    INPUT_FIELDS = [
        ("Name",               "name",          str),
        ("Lead dry weight [kg]","lead_dry_mass", float),
        ("Diver dry mass [kg]", "diver_dry_mass",float),
    ]
    _BASELINE_TOOLTIP = (
        "Marks this element as the source of drysuit baseline lift in the inflation "
        "gas calculation. Only one element per Buoyancy Plan should have this flag."
    )

    def __init__(self, parent, data: dict | None = None, db: dict | None = None):
        super().__init__(parent)
        self._is_edit = data is not None
        self.setWindowTitle("Edit Diver" if self._is_edit else "Add Diver")
        self.setMinimumWidth(620)
        self._data = copy.deepcopy(data) if data else {}
        self._db   = db
        self._entries: dict[str, QLineEdit] = {}
        self._preview_labels: dict[str, QLabel] = {}

        main_lay = QHBoxLayout(self)

        # ── Left: inputs ──────────────────────────────────────────────────────
        left_box = QGroupBox("Input values")
        left_lay = QGridLayout(left_box)
        main_lay.addWidget(left_box)

        for r, (label, key, typ) in enumerate(self.INPUT_FIELDS):
            left_lay.addWidget(QLabel(label + ":"), r, 0)
            val = self._data.get(key, "")
            le = QLineEdit("" if val is None else str(val))
            le.setStyleSheet("background:#ffe0e0;")
            le.textChanged.connect(self._recalc)
            self._entries[key] = le
            left_lay.addWidget(le, r, 1)

        r_cb = len(self.INPUT_FIELDS)
        # Diver Buoyancy checkbox
        self._cb_diver = QCheckBox("Diver Buoyancy")
        cat = self._data.get("category", "") or ""
        self._cb_diver.setChecked("Diver Buoyancy" in cat)
        self._cb_diver.stateChanged.connect(self._recalc)
        left_lay.addWidget(self._cb_diver, r_cb, 0, 1, 2)

        # Baseline Buoyancy checkbox
        self._cb_baseline = QCheckBox("Baseline Buoyancy")
        self._cb_baseline.setChecked(bool(self._data.get("baseline_buoyancy", False)))
        self._cb_baseline.setToolTip(self._BASELINE_TOOLTIP)
        left_lay.addWidget(self._cb_baseline, r_cb + 1, 0, 1, 2)

        # Buttons
        btn_lay = QHBoxLayout()
        label_txt = "Save" if self._is_edit else "Add to database"
        ok_btn = QPushButton(label_txt)
        ok_btn.setStyleSheet(f"background:{CLR_ADD};")
        ok_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_lay.addWidget(ok_btn)
        btn_lay.addWidget(cancel_btn)
        left_lay.addLayout(btn_lay, r_cb + 2, 0, 1, 2)

        # ── Right: calculated preview ─────────────────────────────────────────
        right_box = QGroupBox("Calculated preview")
        right_lay = QGridLayout(right_box)
        right_lay.setColumnStretch(0, 3)
        right_lay.setColumnStretch(1, 2)
        main_lay.addWidget(right_box)

        for row, (lbl, key, green) in enumerate(_DIVER_PREVIEW_ROWS):
            right_lay.addWidget(QLabel(lbl + ":"), row, 0)
            val_lbl = QLabel("---")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                                 Qt.AlignmentFlag.AlignVCenter)
            val_lbl.setFrameShape(QFrame.Shape.StyledPanel)
            val_lbl.setMinimumWidth(90)
            bg = "#dff0df" if green else "#f0f0f0"
            val_lbl.setStyleSheet(f"background:{bg}; padding:1px 4px;")
            right_lay.addWidget(val_lbl, row, 1)
            self._preview_labels[key] = val_lbl

        self._recalc()

    def _recalc(self):
        try:
            lead = float(self._entries["lead_dry_mass"].text().replace(",","."))
            diver_str = self._entries["diver_dry_mass"].text().strip().replace(",",".")
            diver = float(diver_str) if diver_str else None
            r = _compute_diver(lead, diver)
            for key, lbl in self._preview_labels.items():
                if key == "category":
                    lbl.setText("Diver Buoyancy" if self._cb_diver.isChecked() else "")
                else:
                    val = r.get(key)
                    if val is None:
                        lbl.setText("---")
                    elif key == "rho_pb":
                        lbl.setText(f"{val:.3f}")
                    else:
                        lbl.setText(f"{val:.3f}")
        except (ValueError, ZeroDivisionError):
            self._preview_labels.get("rho_pb", QLabel()).setText(f"{RHO_PB:.3f}")
            for key, lbl in self._preview_labels.items():
                if key != "rho_pb":
                    lbl.setText("---")

    def _accept(self):
        for label, key, typ in self.INPUT_FIELDS:
            val = self._entries[key].text().strip().replace(",",".")
            if val == "":
                self._data[key] = None
            else:
                try:
                    self._data[key] = typ(val)
                except ValueError:
                    QMessageBox.warning(self, "Invalid input",
                                        f"'{label}' must be a number.")
                    return
        self._data["category"]          = "Diver Buoyancy" if self._cb_diver.isChecked() else ""
        self._data["baseline_buoyancy"] = self._cb_baseline.isChecked()
        if self._data["baseline_buoyancy"] and self._db is not None:
            current_name = self._data.get("name", "")
            conflict = next(
                (d["name"] for d in self._db.get("divers", [])
                 if d.get("baseline_buoyancy") and d.get("name") != current_name),
                None,
            ) or next(
                (e["name"] for e in self._db.get("equipment", [])
                 if e.get("baseline_buoyancy") and e.get("name") != current_name),
                None,
            )
            if conflict:
                reply = QMessageBox.question(
                    self, "Duplicate Baseline Buoyancy",
                    f"'{conflict}' already has Baseline Buoyancy flagged.\n"
                    "Continue and have two? (Multiple flags will cause a warning "
                    "in the inflation model.)",
                    QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
                )
                if reply != QMessageBox.StandardButton.Ok:
                    return
        self.accept()

    def result_data(self) -> dict:
        return self._data


# ─────────────────────────────────────────────────────────────────────────────
# Section widget: generic table + toolbar
# ─────────────────────────────────────────────────────────────────────────────

class _SectionTable(QWidget):
    """
    Generic section: toolbar (Add button) + QTableWidget.
    Subclass overrides: COLUMNS, _to_row(), _open_dialog()
    """
    COLUMNS: list[tuple[str, int]] = []   # (header, width)
    TOOLBAR_COLOR = CLR_TOOLBAR
    ADD_LABEL = "+ Add"

    def __init__(self, db: dict, db_key: str, parent_win, on_db_change=None):
        super().__init__()
        self._db = db
        self._db_key = db_key
        self._parent_win = parent_win
        self._on_db_change = on_db_change

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        # Toolbar
        tb = QWidget()
        tb.setStyleSheet(f"background:{self.TOOLBAR_COLOR};")
        tb_lay = QHBoxLayout(tb)
        tb_lay.setContentsMargins(6, 4, 6, 4)
        add_btn = _btn(self.ADD_LABEL, CLR_ADD, self._add)
        tb_lay.addWidget(add_btn)
        tb_lay.addStretch()
        lay.addWidget(tb)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLUMNS) + 2)  # +Edit +Delete
        hdrs = [h for h, _ in self.COLUMNS] + ["Edit", "Delete"]
        self._table.setHorizontalHeaderLabels(hdrs)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            "alternate-background-color: #f0f4f8; background: #ffffff;")

        for c, (_, w) in enumerate(self.COLUMNS):
            self._table.setColumnWidth(c, w)
        n = len(self.COLUMNS)
        self._table.setColumnWidth(n,     60)
        self._table.setColumnWidth(n + 1, 60)

        lay.addWidget(self._table)
        self.refresh()

    def _items(self) -> list:
        return self._db.get(self._db_key, [])

    def refresh(self):
        items = self._items()
        self._table.setRowCount(len(items))
        for r, item in enumerate(items):
            for c, val in enumerate(self._to_row(item)):
                cell = QTableWidgetItem(str(val) if val is not None else "")
                cell.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter if c > 0
                    else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(r, c, cell)

            n = len(self.COLUMNS)
            edit_btn = _btn("Edit", CLR_EDIT, lambda _, row=r: self._edit(row))
            del_btn  = _btn("Del",  CLR_DEL,  lambda _, row=r: self._delete(row))
            self._table.setCellWidget(r, n,     edit_btn)
            self._table.setCellWidget(r, n + 1, del_btn)
            self._table.setRowHeight(r, 28)

    def _to_row(self, item: dict) -> list:
        raise NotImplementedError

    def _open_dialog(self, data: dict | None) -> QDialog:
        raise NotImplementedError

    def _add(self):
        dlg = self._open_dialog(None)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._items().append(dlg.result_data())
            save_db(self._db)
            self.refresh()
            if self._on_db_change:
                self._on_db_change(None, None)

    def _edit(self, row: int):
        item = self._items()[row]
        old_name = item.get("name", "")
        dlg = self._open_dialog(item)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._items()[row] = dlg.result_data()
            new_name = self._items()[row].get("name", "")
            save_db(self._db)
            self.refresh()
            if self._on_db_change:
                self._on_db_change(old_name, new_name)

    def _delete(self, row: int):
        name = self._items()[row].get("name", f"item {row+1}")
        reply = QMessageBox.question(
            self, "Delete", f"Delete '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._items().pop(row)
            save_db(self._db)
            self.refresh()
            if self._on_db_change:
                self._on_db_change(name, None)


# ─────────────────────────────────────────────────────────────────────────────
# Concrete section tables
# ─────────────────────────────────────────────────────────────────────────────

class CylinderTable(_SectionTable):
    COLUMNS = [
        ("Name",        160),
        ("Category",    120),
        ("Vol [L]",      65),
        ("Dry [kg]",     70),
        ("Wet [kg]",     70),
        ("O₂ [%]",       60),
        ("He [%]",       60),
        ("Press [bar]",  80),
        ("Temp [°C]",    70),
        ("Description", 200),
    ]
    TOOLBAR_COLOR = CLR_TOOLBAR
    ADD_LABEL = "+ Add Cylinder"

    def _to_row(self, it):
        return [it.get("name",""), it.get("category",""),
                it.get("volume_bottle",""), it.get("dry_mass",""),
                it.get("wet_mass",""), it.get("o2",""), it.get("he",""),
                it.get("pressure",""), it.get("temp",""),
                it.get("description","")]

    def _open_dialog(self, data):
        return CylinderDialog(self, data)


class EquipmentTable(_SectionTable):
    COLUMNS = [
        ("Name",          160),
        ("Category",      120),
        ("Dry [kg]",       70),
        ("Wet [kg]",       70),
        ("Core",           50),
        ("Modular",        60),
        ("Diver Buoy.",    80),
        ("Stage",          50),
        ("Baseline",       65),
        ("Description",   200),
    ]
    TOOLBAR_COLOR = CLR_TOOLBAR_EQ
    ADD_LABEL = "+ Add Equipment"

    def _to_row(self, it):
        def b(k): return "✓" if it.get(k) else ""
        return [it.get("name",""), it.get("category",""),
                it.get("dry_mass",""), it.get("wet_mass",""),
                b("jj_core"), b("jj_modular"), b("diver_buoyancy"), b("stage"),
                b("baseline_buoyancy"),
                it.get("description","")]

    def _open_dialog(self, data):
        return EquipmentDialog(self, data, db=self._db)


class DiverTable(_SectionTable):
    COLUMNS = [
        ("Name",              200),
        ("Category",          140),
        ("Lead dry [kg]",     110),
        ("Diver dry [kg]",    110),
        ("Baseline",           65),
    ]
    TOOLBAR_COLOR = CLR_TOOLBAR_DV
    ADD_LABEL = "+ Add Diver"

    def _to_row(self, it):
        return [it.get("name",""), it.get("category",""),
                it.get("lead_dry_mass",""), it.get("diver_dry_mass",""),
                "✓" if it.get("baseline_buoyancy") else ""]

    def _open_dialog(self, data):
        return DiverDialog(self, data, db=self._db)


# ─────────────────────────────────────────────────────────────────────────────
# Databases tab
# ─────────────────────────────────────────────────────────────────────────────

class DatabasesTab(QWidget):
    def __init__(self, db: dict, on_db_change=None):
        super().__init__()
        self._db = db

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)

        # Cylinders
        cyl_box = QGroupBox("Cylinders")
        cyl_lay = QVBoxLayout(cyl_box)
        cyl_lay.setContentsMargins(4, 4, 4, 4)
        self._cyl_table = CylinderTable(db, "cylinders", self, on_db_change)
        cyl_lay.addWidget(self._cyl_table)
        lay.addWidget(cyl_box)

        # Equipment
        eq_box = QGroupBox("Equipment")
        eq_lay = QVBoxLayout(eq_box)
        eq_lay.setContentsMargins(4, 4, 4, 4)
        self._eq_table = EquipmentTable(db, "equipment", self, on_db_change)
        eq_lay.addWidget(self._eq_table)
        lay.addWidget(eq_box)

        # Divers
        dv_box = QGroupBox("Diver Buoyancy Profiles")
        dv_lay = QVBoxLayout(dv_box)
        dv_lay.setContentsMargins(4, 4, 4, 4)
        self._dv_table = DiverTable(db, "divers", self, on_db_change)
        dv_lay.addWidget(self._dv_table)
        lay.addWidget(dv_box)


# ─────────────────────────────────────────────────────────────────────────────
# Buoyancy Planner helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cyl_names(db) -> list:
    return ["—"] + [c["name"] for c in db.get("cylinders", [])]

def _diver_names(db) -> list:
    return ["—"] + [d["name"] for d in db.get("divers", [])]

def _equip_names(db, flag) -> list:
    return ["—"] + [e["name"] for e in db.get("equipment", []) if e.get(flag)]


class _CylCard(QGroupBox):
    """Cylinder / stage slot card with live calculated results."""

    RESULT_ROWS = [
        ("Buoyancy SW ref [kg]", "ref_buoy_sw", False),
        ("Volume bottle [L]",    "vol_bottle",  False),
        ("Gas mass [kg]",        "gas_mass",    False),
        ("Buoyancy SW [kg]",     "buoyancy_sw", True ),
        ("Z factor",             "Z",           False),
        ("Real gas [L]",         "real_gas",    False),
        ("Ideal gas [L]",        "ideal_gas",   False),
    ]

    def __init__(self, title: str, db: dict, on_change):
        super().__init__(title)
        self._db = db
        self._on_change = on_change
        self._res: dict[str, QLabel] = {}

        lay = QGridLayout(self)
        lay.setSpacing(2)
        lay.setContentsMargins(4, 10, 4, 4)
        r = 0

        self._cyl_cb = QComboBox()
        self._cyl_cb.addItems(_cyl_names(db))
        self._cyl_cb.currentTextChanged.connect(self._on_cyl_changed)
        lay.addWidget(self._cyl_cb, r, 0, 1, 2); r += 1

        lay.addWidget(QLabel("Gas (O₂/He):"), r, 0)
        self._gas_le = QLineEdit("21/0")
        self._gas_le.setFixedWidth(70)
        self._gas_le.setStyleSheet("background:#ffe0e0;")
        self._gas_le.textChanged.connect(self._on_gas_text)
        lay.addWidget(self._gas_le, r, 1); r += 1

        lay.addWidget(QLabel("Pressure [bar]:"), r, 0)
        self._pres_le = QLineEdit("")
        self._pres_le.setFixedWidth(70)
        self._pres_le.setStyleSheet("background:#ffe0e0;")
        self._pres_le.textChanged.connect(self._recalc)
        lay.addWidget(self._pres_le, r, 1); r += 1

        lay.addWidget(_hline(), r, 0, 1, 2); r += 1

        for lbl_txt, key, bold in self.RESULT_ROWS:
            lbl = QLabel(lbl_txt + ":")
            if bold:
                lbl.setStyleSheet("font-weight:bold;")
            lay.addWidget(lbl, r, 0)
            val = QLabel("---")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val.setFrameShape(QFrame.Shape.StyledPanel)
            val.setFixedWidth(70)
            bg = "#dff0df" if bold else "#f0f0f0"
            fw = "font-weight:bold;" if bold else ""
            val.setStyleSheet(f"background:{bg};{fw} padding:1px 4px;")
            lay.addWidget(val, r, 1)
            self._res[key] = val
            r += 1
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    def _on_gas_text(self, text):
        # Auto-insert "/" only when growing (not when user is deleting)
        prev = getattr(self, "_gas_prev", "")
        self._gas_prev = text
        if len(text) > len(prev) and "/" not in text:
            digits = text.replace(" ", "")
            if len(digits) >= 2:
                self._gas_le.blockSignals(True)
                new = digits[:2] + "/" + digits[2:]
                self._gas_le.setText(new)
                self._gas_le.setCursorPosition(len(new))
                self._gas_prev = new
                self._gas_le.blockSignals(False)
                self._recalc()
                return
        self._recalc()

    def refresh_names(self, old_name=None, new_name=None):
        """Refresh combobox after DB change. Maps old_name→new_name if renamed."""
        current = self._cyl_cb.currentText()
        new_names = _cyl_names(self._db)
        self._cyl_cb.blockSignals(True)
        self._cyl_cb.clear()
        self._cyl_cb.addItems(new_names)
        if old_name and current == old_name:
            self._cyl_cb.setCurrentText(new_name if new_name and new_name in new_names else "—")
        elif current in new_names:
            self._cyl_cb.setCurrentText(current)
        else:
            self._cyl_cb.setCurrentText("—")
        self._cyl_cb.blockSignals(False)
        self._recalc()

    def load(self, sd: dict):
        """Populate from a slot dict without triggering cascading recalcs."""
        self._cyl_cb.blockSignals(True)
        self._gas_le.blockSignals(True)
        self._pres_le.blockSignals(True)

        names = _cyl_names(self._db)
        sel = sd.get("sel", "—")
        self._cyl_cb.setCurrentText(sel if sel in names else "—")
        o2 = sd.get("o2", "21")
        he = sd.get("he", "0")
        self._gas_le.setText(f"{o2}/{he}")
        self._pres_le.setText(str(sd.get("pressure", "")))

        self._cyl_cb.blockSignals(False)
        self._gas_le.blockSignals(False)
        self._pres_le.blockSignals(False)
        self._recalc()

    def _on_cyl_changed(self, name):
        for c in self._db.get("cylinders", []):
            if c["name"] == name:
                self._gas_le.blockSignals(True)
                self._pres_le.blockSignals(True)
                self._gas_le.setText(f"{c.get('o2',21)}/{c.get('he',0)}")
                self._pres_le.setText(str(c.get("pressure", "")))
                self._gas_le.blockSignals(False)
                self._pres_le.blockSignals(False)
                break
        self._recalc()

    def _parse_gas(self):
        txt = self._gas_le.text().strip()
        if "/" in txt:
            a, b = txt.split("/", 1)
            return int(float(a)), int(float(b))
        return int(float(txt)), 0

    def _recalc(self):
        name = self._cyl_cb.currentText()
        cyl = next((c for c in self._db.get("cylinders", []) if c["name"] == name), None)
        if cyl is None or name == "—":
            for v in self._res.values():
                v.setText("---")
            if self._on_change:
                self._on_change()
            return
        try:
            o2, he = self._parse_gas()
            pres = float(self._pres_le.text().replace(",", "."))
            r = _compute_cyl(cyl["dry_mass"], cyl["wet_mass"], cyl["volume_bottle"],
                             o2, he, pres, cyl.get("temp", 20))
            r_ref = _compute_cyl(cyl["dry_mass"], cyl["wet_mass"], cyl["volume_bottle"],
                                 cyl.get("o2", 21), cyl.get("he", 0),
                                 cyl.get("pressure", 0), cyl.get("temp", 20))
            vbot = r["volume_bottle"]
            Z    = r["Z"]
            p_abs = pres + 1.0
            vals = {
                "ref_buoy_sw": r_ref["empty_buoy_sw"],
                "vol_bottle":  vbot,
                "o2":          o2,
                "he":          he,
                "pressure":    pres,
                "gas_mass":    r["gas_mass"],
                "buoyancy_sw": r_ref["empty_buoy_sw"] - r["gas_mass"],
                "Z":           Z,
                "real_gas":    vbot * p_abs / Z if Z else 0,
                "ideal_gas":   vbot * pres,
            }
            for key, lbl in self._res.items():
                v = vals[key]
                if key in ("o2", "he"):
                    lbl.setText(str(int(v)))
                elif key == "Z":
                    lbl.setText(f"{v:.2f}")
                elif key in ("real_gas", "ideal_gas", "pressure"):
                    lbl.setText(f"{v:.0f}")
                elif key == "vol_bottle":
                    lbl.setText(f"{v:.1f}")
                elif key in ("buoyancy_sw", "ref_buoy_sw", "gas_mass"):
                    lbl.setText(f"{v:.2f}")
                else:
                    lbl.setText(f"{v:.3f}")
        except (ValueError, ZeroDivisionError, KeyError):
            for v in self._res.values():
                v.setText("---")
        if self._on_change:
            self._on_change()

    def buoyancy_sw(self) -> float | None:
        try:
            return float(self._res["buoyancy_sw"].text())
        except ValueError:
            return None

    def dry_mass(self) -> float | None:
        name = self._cyl_cb.currentText()
        cyl = next((c for c in self._db.get("cylinders", []) if c["name"] == name), None)
        if cyl is None:
            return None
        return cyl.get("dry_mass")

    def slot_data(self) -> dict:
        txt = self._gas_le.text().strip()
        if "/" in txt:
            a, b = txt.split("/", 1)
        else:
            a, b = txt, "0"
        return {
            "sel": self._cyl_cb.currentText(),
            "valves_sel": "—",
            "o2": a.strip(),
            "he": b.strip(),
            "pressure": self._pres_le.text().strip(),
        }


class _EquipRow(QWidget):
    """Single equipment/diver slot row (left panel)."""

    def __init__(self, db: dict, choices: list, sd: dict, on_change, choices_fn=None):
        super().__init__()
        self._db = db
        self._on_change = on_change
        self._choices_fn = choices_fn

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._cb = QComboBox()
        self._cb.addItems(choices)
        sel = sd.get("sel", "—")
        if sel in choices:
            self._cb.setCurrentText(sel)
        self._cb.currentTextChanged.connect(self._recalc)
        self._cb.setFixedWidth(_COL_CB)
        lay.addWidget(self._cb)

        self._star_lbl = QLabel("★")
        self._star_lbl.setStyleSheet("color:#ffaa00; font-size:11px;")
        self._star_lbl.setToolTip("Used as drysuit baseline lift in inflation gas calculation")
        self._star_lbl.setFixedWidth(14)
        self._star_lbl.hide()
        lay.addWidget(self._star_lbl)

        self._dry_lbl = QLabel("---")
        self._dry_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._dry_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self._dry_lbl.setStyleSheet("background:#f0f0f0; padding:1px 3px;")
        self._dry_lbl.setFixedWidth(_COL_DRY)
        lay.addWidget(self._dry_lbl)

        self._buoy_lbl = QLabel("---")
        self._buoy_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._buoy_lbl.setFrameShape(QFrame.Shape.StyledPanel)
        self._buoy_lbl.setStyleSheet("background:#dff0df; padding:1px 3px;")
        self._buoy_lbl.setFixedWidth(_COL_BUOY)
        lay.addWidget(self._buoy_lbl)

        self._recalc()

    def refresh_names(self, old_name=None, new_name=None):
        """Refresh combobox after DB change. Maps old_name→new_name if renamed."""
        if not self._choices_fn:
            return
        current = self._cb.currentText()
        new_choices = self._choices_fn()
        self._cb.blockSignals(True)
        self._cb.clear()
        self._cb.addItems(new_choices)
        if old_name and current == old_name:
            self._cb.setCurrentText(new_name if new_name and new_name in new_choices else "—")
        elif current in new_choices:
            self._cb.setCurrentText(current)
        else:
            self._cb.setCurrentText("—")
        self._cb.blockSignals(False)
        self._recalc()

    def _recalc(self):
        name = self._cb.currentText()
        if name == "—":
            self._dry_lbl.setText("---")
            self._buoy_lbl.setText("---")
            if self._on_change:
                self._on_change()
            return

        diver = next((d for d in self._db.get("divers", []) if d["name"] == name), None)
        equip = next((e for e in self._db.get("equipment", []) if e["name"] == name), None)

        if diver:
            lead = diver.get("lead_dry_mass") or 0
            diver_dry = diver.get("diver_dry_mass")
            r = _compute_diver(lead, diver_dry)
            # Dry = diver body mass (not lead), empty if not set
            if diver_dry is not None:
                self._dry_lbl.setText(f"{diver_dry:.2f}")
            else:
                self._dry_lbl.setText("---")
            self._buoy_lbl.setText(f"{r['diver_buoyancy_sw']:.2f}")
        elif equip:
            dry = equip.get("dry_mass") or 0
            wet = equip.get("wet_mass") or 0
            r = _compute_equip(dry, wet)
            self._dry_lbl.setText(f"{dry:.2f}")
            self._buoy_lbl.setText(f"{r['buoyancy_sw']:.2f}")
        else:
            self._dry_lbl.setText("---")
            self._buoy_lbl.setText("---")

        item = diver or equip
        if item and item.get("baseline_buoyancy"):
            self._star_lbl.show()
        else:
            self._star_lbl.hide()

        if self._on_change:
            self._on_change()

    def buoyancy_sw(self) -> float | None:
        try:
            return float(self._buoy_lbl.text())
        except ValueError:
            return None

    def dry_mass(self) -> float | None:
        txt = self._dry_lbl.text()
        if txt == "---":
            return None
        try:
            return float(txt)
        except ValueError:
            return None

    def slot_data(self) -> dict:
        return {"sel": self._cb.currentText()}


# Fixed column widths used consistently across all left-panel sections
_COL_CB   = 130   # dropdown
_COL_DRY  = 62    # dry mass value
_COL_BUOY = 72    # buoy SW value (wide enough for "Buoy SW [kg]" header)


def _section_groupbox(title: str) -> tuple:
    """
    Returns (QGroupBox, inner_layout).
    Title row shows section name left + column headers right on same line.
    """
    # frame width = left+right margins(8) + CB(130) + 2×spacing(8) + DRY(62) + BUOY(72) = 280
    _FRAME_W = 8 + _COL_CB + 2 * 4 + _COL_DRY + _COL_BUOY
    box = QGroupBox()
    box.setFixedWidth(_FRAME_W)
    vl = QVBoxLayout(box)
    vl.setSpacing(2)
    vl.setContentsMargins(4, 4, 4, 4)

    hdr = QWidget()
    hl = QHBoxLayout(hdr)
    hl.setContentsMargins(0, 0, 0, 2)
    hl.setSpacing(4)
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet("font-weight:bold;")
    title_lbl.setFixedWidth(_COL_CB)
    hl.addWidget(title_lbl)
    for txt, w in (("Dry [kg]", _COL_DRY), ("Buoy SW [kg]", _COL_BUOY)):
        l = QLabel(txt)
        l.setStyleSheet("font-weight:bold; color:#333;")
        l.setFixedWidth(w)
        hl.addWidget(l)
    vl.addWidget(hdr)

    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    vl.addWidget(line)

    return box, vl


# ─────────────────────────────────────────────────────────────────────────────
# Buoyancy Planner tab
# ─────────────────────────────────────────────────────────────────────────────

class BuoyancyPlannerTab(QWidget):
    N_DIVER_EQ = 2    # diver equipment slots (after the diver profile slot)
    N_CORE     = 4    # JJ core equipment slots
    N_MOD      = 4    # JJ modular equipment slots
    N_JSLOTS   = 4
    N_SSLOTS   = 3

    def __init__(self, db: dict, on_plan_saved=None):
        super().__init__()
        self._db = db
        self._on_plan_saved = on_plan_saved
        self._e_rows: list[_EquipRow] = []
        self._j_cards: list[_CylCard] = []
        self._s_cards: list[_CylCard] = []
        self._sum_lbls: dict[str, QLabel] = {}
        self._loading = False

        if "users" not in db:
            db["users"] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        # ── Toolbar ───────────────────────────────────────────────────────────
        tb = QWidget()
        tb.setStyleSheet(f"background:{CLR_TOOLBAR};")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(8, 6, 8, 6)
        tbl.setSpacing(6)

        tbl.addWidget(QLabel("User:"))
        self._user_cb = QComboBox()
        self._user_cb.setMinimumWidth(140)
        self._user_cb.addItems(list(db.get("users", {}).keys()) or ["—"])
        self._user_cb.currentTextChanged.connect(self._on_user_changed)
        tbl.addWidget(self._user_cb)
        tbl.addWidget(_btn("+", CLR_ADD, self._add_user))
        tbl.addWidget(_btn("−", CLR_DEL, self._del_user))
        tbl.addWidget(_btn("✎", CLR_EDIT, self._rename_user))

        tbl.addSpacing(16)
        tbl.addWidget(QLabel("Plan:"))
        self._plan_cb = QComboBox()
        self._plan_cb.setMinimumWidth(140)
        self._plan_cb.currentTextChanged.connect(self._on_plan_changed)
        tbl.addWidget(self._plan_cb)
        tbl.addWidget(_btn("+", CLR_ADD, self._add_plan))
        tbl.addWidget(_btn("−", CLR_DEL, self._del_plan))
        tbl.addWidget(_btn("✎", CLR_EDIT, self._rename_plan))

        tbl.addSpacing(16)
        tbl.addWidget(_btn("Save Plan", CLR_EDIT, self._save_plan))
        tbl.addWidget(_btn("Save as new", CLR_ADD, self._save_plan_as_new))
        tbl.addStretch()
        outer.addWidget(tb)

        # ── Body ──────────────────────────────────────────────────────────────
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)

        # Left: equipment sections + summary (in scroll area)
        self._left_scroll = QScrollArea()
        self._left_scroll.setWidgetResizable(True)
        self._left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._left_scroll.setFixedWidth(284)
        bl.addWidget(self._left_scroll)

        # Middle: JJ cylinders 2×2
        mid_sa = QScrollArea()
        mid_sa.setWidgetResizable(True)
        mid_sa.setFrameShape(QFrame.Shape.NoFrame)
        mid_w = QWidget()
        mid_sa.setWidget(mid_w)
        mid_gl = QGridLayout(mid_w)
        mid_gl.setSpacing(8)
        mid_gl.setContentsMargins(0, 4, 0, 4)
        for i in range(self.N_JSLOTS):
            card = _CylCard(f"Cylinder {i+1}", db, self._recalc_summary)
            self._j_cards.append(card)
            mid_gl.addWidget(card, i // 2, i % 2)
        mid_gl.setRowStretch(2, 1)
        mid_sa.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        bl.addWidget(mid_sa)

        # Right: stages
        right_sa = QScrollArea()
        right_sa.setWidgetResizable(True)
        right_sa.setFrameShape(QFrame.Shape.NoFrame)
        right_w = QWidget()
        right_sa.setWidget(right_w)
        right_gl = QGridLayout(right_w)
        right_gl.setSpacing(8)
        right_gl.setContentsMargins(0, 4, 0, 4)
        for i in range(self.N_SSLOTS):
            card = _CylCard(f"Stage {i+1}", db, self._recalc_summary)
            self._s_cards.append(card)
            right_gl.addWidget(card, i // 2, i % 2)
        right_gl.setRowStretch(right_gl.rowCount(), 1)
        right_sa.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        bl.addWidget(right_sa)
        bl.addStretch(1)

        outer.addWidget(body, 1)

        # Init
        self._update_plan_combo()
        self._load_current_plan()

    # ── User / plan helpers ───────────────────────────────────────────────────

    def _current_user(self) -> str:
        return self._user_cb.currentText()

    def _current_plan(self) -> str:
        return self._plan_cb.currentText()

    def _plans(self) -> dict:
        u = self._current_user()
        return self._db["users"].setdefault(u, {"buoyancy_plans": {}}).setdefault(
            "buoyancy_plans", {})

    def _update_plan_combo(self):
        self._plan_cb.blockSignals(True)
        self._plan_cb.clear()
        plans = list(self._plans().keys())
        if not plans:
            plans = ["Default"]
            self._plans()["Default"] = {"eslots": [], "jslots": [], "sslots": []}
        self._plan_cb.addItems(plans)
        self._plan_cb.blockSignals(False)

    def _on_user_changed(self):
        self._update_plan_combo()
        self._load_current_plan()

    def _on_plan_changed(self):
        if not self._loading:
            self._load_current_plan()

    # ── Load plan ─────────────────────────────────────────────────────────────

    def _load_current_plan(self):
        self._loading = True
        plan = self._plans().get(self._current_plan(), {})
        eslots = plan.get("eslots", [])
        jslots = plan.get("jslots", [])
        sslots = plan.get("sslots", [])

        self._build_left_panel(eslots)

        for i, card in enumerate(self._j_cards):
            card.load(jslots[i] if i < len(jslots) else {})
        for i, card in enumerate(self._s_cards):
            card.load(sslots[i] if i < len(sslots) else {})

        self._loading = False
        self._recalc_summary()

    # ── Left panel ────────────────────────────────────────────────────────────

    def _build_left_panel(self, eslots: list):
        db = self._db
        self._e_rows = []

        left_w = QWidget()
        ll = QVBoxLayout(left_w)
        ll.setContentsMargins(4, 4, 4, 4)
        ll.setSpacing(8)

        # DIVER BUOYANCY
        dbox, dl = _section_groupbox("DIVER BUOYANCY")
        dl.setSpacing(2)
        row0 = _EquipRow(db, _diver_names(db),
                         eslots[0] if len(eslots) > 0 else {}, self._recalc_summary,
                         choices_fn=lambda: _diver_names(db))
        self._e_rows.append(row0)
        dl.addWidget(row0)
        dequ = _equip_names(db, "diver_buoyancy")
        for i in range(1, 1 + self.N_DIVER_EQ):
            row = _EquipRow(db, dequ,
                            eslots[i] if i < len(eslots) else {}, self._recalc_summary,
                            choices_fn=lambda: _equip_names(db, "diver_buoyancy"))
            self._e_rows.append(row)
            dl.addWidget(row)
        ll.addWidget(dbox)

        # JJ CORE BUOYANCY
        cbox, cl = _section_groupbox("CORE BUOYANCY")
        cl.setSpacing(2)
        core = _equip_names(db, "jj_core")
        base = 1 + self.N_DIVER_EQ
        for i in range(base, base + self.N_CORE):
            row = _EquipRow(db, core,
                            eslots[i] if i < len(eslots) else {}, self._recalc_summary,
                            choices_fn=lambda: _equip_names(db, "jj_core"))
            self._e_rows.append(row)
            cl.addWidget(row)
        ll.addWidget(cbox)

        # JJ MODULAR BUOYANCY
        mbox, ml = _section_groupbox("MODULAR BUOYANCY")
        ml.setSpacing(2)
        mod = _equip_names(db, "jj_modular")
        base2 = base + self.N_CORE
        for i in range(base2, base2 + self.N_MOD):
            row = _EquipRow(db, mod,
                            eslots[i] if i < len(eslots) else {}, self._recalc_summary,
                            choices_fn=lambda: _equip_names(db, "jj_modular"))
            self._e_rows.append(row)
            ml.addWidget(row)
        ll.addWidget(mbox)

        # BUOYANCY SUMMARY
        sbox, sv = _section_groupbox("BUOYANCY SUMMARY")
        sl = QGridLayout()
        sl.setSpacing(3)
        sv.addLayout(sl)
        SROWS = [
            ("jj_with_bottles",  "CCR with bottles"),
            ("jj_diving_ready",  "CCR diving ready"),
            ("diver",            "Diver"),
            ("jj_diver",         "CCR + Diver"),
            ("jj_diver_stages",  "CCR + Diver + Stages"),
            ("stage1",           "Stage 1"),
            ("stage2",           "Stage 2"),
            ("stage3",           "Stage 3"),
        ]
        self._sum_lbls = {}
        self._sum_dry_lbls = {}
        for sr, (key, lbl_txt) in enumerate(SROWS):
            row_lbl = QLabel(lbl_txt + ":")
            row_lbl.setFixedWidth(_COL_CB)
            sl.addWidget(row_lbl, sr, 0)
            dry_v = QLabel("---")
            dry_v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            dry_v.setFrameShape(QFrame.Shape.StyledPanel)
            dry_v.setStyleSheet("background:#f0f0f0; padding:1px 3px;")
            dry_v.setFixedWidth(_COL_DRY)
            sl.addWidget(dry_v, sr, 1)
            self._sum_dry_lbls[key] = dry_v
            v = QLabel("---")
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            v.setFrameShape(QFrame.Shape.StyledPanel)
            v.setStyleSheet("background:#dff0df; padding:1px 3px;")
            v.setFixedWidth(_COL_BUOY)
            sl.addWidget(v, sr, 2)
            self._sum_lbls[key] = v
        ll.addWidget(sbox)
        ll.addStretch(1)

        self._left_scroll.setWidget(left_w)

    # ── Summary recalc ────────────────────────────────────────────────────────

    def refresh_db(self, old_name=None, new_name=None):
        """Called when DB changes — refreshes all dropdowns preserving selections."""
        for card in self._j_cards + self._s_cards:
            card.refresh_names(old_name, new_name)
        for row in self._e_rows:
            row.refresh_names(old_name, new_name)

    def _recalc_summary(self):
        if self._loading or not self._sum_lbls:
            return

        n_diver_rows = 1 + self.N_DIVER_EQ        # rows 0..2
        n_core_rows  = self.N_CORE                  # rows 3..6
        n_mod_rows   = self.N_MOD                   # rows 7..10

        diver_buoy = sum(r.buoyancy_sw() or 0 for r in self._e_rows[:n_diver_rows])
        core_buoy  = sum(r.buoyancy_sw() or 0 for r in
                         self._e_rows[n_diver_rows : n_diver_rows + n_core_rows])
        mod_buoy   = sum(r.buoyancy_sw() or 0 for r in
                         self._e_rows[n_diver_rows + n_core_rows :
                                      n_diver_rows + n_core_rows + n_mod_rows])

        j_buoys = [c.buoyancy_sw() for c in self._j_cards]
        j_drys  = [c.dry_mass()    for c in self._j_cards]
        s_buoys = [c.buoyancy_sw() for c in self._s_cards]
        s_drys  = [c.dry_mass()    for c in self._s_cards]

        diver_dry  = sum(r.dry_mass() or 0 for r in self._e_rows[:n_diver_rows])
        core_dry   = sum(r.dry_mass() or 0 for r in
                         self._e_rows[n_diver_rows : n_diver_rows + n_core_rows])
        mod_dry    = sum(r.dry_mass() or 0 for r in
                         self._e_rows[n_diver_rows + n_core_rows :
                                      n_diver_rows + n_core_rows + n_mod_rows])

        j_buoy_sum       = sum(b for b in j_buoys if b is not None)
        j_dry_sum        = sum(d for d in j_drys  if d is not None)
        jj_with_bottles  = core_buoy + j_buoy_sum
        jj_with_dry      = core_dry  + j_dry_sum
        jj_diving_ready  = core_buoy + mod_buoy + j_buoy_sum
        jj_diving_dry    = core_dry  + mod_dry  + j_dry_sum
        jj_diver         = jj_diving_ready + diver_buoy
        jj_diver_dry     = jj_diving_dry   + diver_dry
        stages_sum       = sum(b for b in s_buoys if b is not None)
        stages_dry_sum   = sum(d for d in s_drys  if d is not None)
        jj_diver_stages  = jj_diver + stages_sum
        jj_diver_stages_dry = jj_diver_dry + stages_dry_sum

        def fmt(v): return f"{v:.2f}"
        def fmtd(v): return f"{v:.2f}" if v else "---"

        def set_row(key, dry, buoy):
            self._sum_lbls[key].setText(fmt(buoy))
            self._sum_dry_lbls[key].setText(fmtd(dry) if dry else "---")

        set_row("jj_with_bottles",  jj_with_dry,   jj_with_bottles)
        set_row("jj_diving_ready",  jj_diving_dry, jj_diving_ready)
        set_row("diver",            diver_dry,     diver_buoy)
        set_row("jj_diver",         jj_diver_dry,  jj_diver)
        set_row("jj_diver_stages",  jj_diver_stages_dry, jj_diver_stages)
        for i, key in enumerate(["stage1", "stage2", "stage3"]):
            b = s_buoys[i] if i < len(s_buoys) else None
            d = s_drys[i]  if i < len(s_drys)  else None
            self._sum_lbls[key].setText(fmt(b) if b is not None else "---")
            self._sum_dry_lbls[key].setText(fmtd(d) if d is not None else "---")

    # ── Save plan ─────────────────────────────────────────────────────────────

    def _save_plan(self):
        plan_name = self._current_plan()
        if not plan_name:
            return
        self._plans()[plan_name] = {
            "eslots": [r.slot_data() for r in self._e_rows],
            "jslots": [c.slot_data() for c in self._j_cards],
            "sslots": [c.slot_data() for c in self._s_cards],
        }
        save_db(self._db)
        if self._on_plan_saved:
            self._on_plan_saved()
        QMessageBox.information(self, "Saved", f"Plan '{plan_name}' saved.")

    def _save_plan_as_new(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save as new plan", "Plan name:",
                                        text=self._current_plan())
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._plans():
            reply = QMessageBox.question(self, "Overwrite?",
                                         f"Plan '{name}' already exists. Overwrite?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._plans()[name] = {
            "eslots": [r.slot_data() for r in self._e_rows],
            "jslots": [c.slot_data() for c in self._j_cards],
            "sslots": [c.slot_data() for c in self._s_cards],
        }
        save_db(self._db)
        self._plan_cb.blockSignals(True)
        if self._plan_cb.findText(name) < 0:
            self._plan_cb.addItem(name)
        self._plan_cb.setCurrentText(name)
        self._plan_cb.blockSignals(False)
        if self._on_plan_saved:
            self._on_plan_saved()
        QMessageBox.information(self, "Saved", f"Plan '{name}' saved.")

    # ── User / plan CRUD ──────────────────────────────────────────────────────

    def _add_user(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add User", "User name:")
        if ok and name.strip():
            name = name.strip()
            self._db["users"].setdefault(name, {"buoyancy_plans": {}})
            save_db(self._db)
            self._user_cb.blockSignals(True)
            self._user_cb.addItem(name)
            self._user_cb.setCurrentText(name)
            self._user_cb.blockSignals(False)
            self._update_plan_combo()
            self._load_current_plan()

    def _del_user(self):
        user = self._current_user()
        reply = QMessageBox.question(self, "Delete User", f"Delete user '{user}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._db["users"].pop(user, None)
            save_db(self._db)
            self._user_cb.removeItem(self._user_cb.currentIndex())
            self._update_plan_combo()
            self._load_current_plan()

    def _add_plan(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add Plan", "Plan name:")
        if ok and name.strip():
            name = name.strip()
            self._plans().setdefault(name, {"eslots": [], "jslots": [], "sslots": []})
            save_db(self._db)
            self._plan_cb.blockSignals(True)
            self._plan_cb.addItem(name)
            self._plan_cb.setCurrentText(name)
            self._plan_cb.blockSignals(False)
            self._load_current_plan()

    def _del_plan(self):
        plan = self._current_plan()
        reply = QMessageBox.question(self, "Delete Plan", f"Delete plan '{plan}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._plans().pop(plan, None)
            save_db(self._db)
            self._plan_cb.removeItem(self._plan_cb.currentIndex())
            self._load_current_plan()

    def _rename_user(self):
        from PyQt6.QtWidgets import QInputDialog
        old = self._current_user()
        new, ok = QInputDialog.getText(self, "Rename User", "New name:", text=old)
        if ok and new.strip() and new.strip() != old:
            new = new.strip()
            users = self._db.get("users", {})
            users[new] = users.pop(old)
            save_db(self._db)
            self._user_cb.blockSignals(True)
            self._user_cb.setItemText(self._user_cb.currentIndex(), new)
            self._user_cb.blockSignals(False)

    def _rename_plan(self):
        from PyQt6.QtWidgets import QInputDialog
        old = self._current_plan()
        new, ok = QInputDialog.getText(self, "Rename Plan", "New name:", text=old)
        if ok and new.strip() and new.strip() != old:
            new = new.strip()
            plans = self._plans()
            plans[new] = plans.pop(old)
            save_db(self._db)
            self._plan_cb.blockSignals(True)
            self._plan_cb.setItemText(self._plan_cb.currentIndex(), new)
            self._plan_cb.blockSignals(False)
            if self._on_plan_saved:
                self._on_plan_saved()


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CCR-TechDivePlanner")
        self.resize(1800, 900)
        self.setMinimumWidth(1720)

        self._db = load_db()
        migrate_baseline_buoyancy_flag(self._db)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self.setCentralWidget(self._tabs)

        # ── Buoyancy Planner tab ──────────────────────────────────────────────
        dp_scroll = QScrollArea()
        dp_scroll.setWidgetResizable(True)

        bp_tab = BuoyancyPlannerTab(self._db,
                                    on_plan_saved=lambda: dp_tab.refresh_from_saved_plan())
        self._tabs.addTab(bp_tab, "  Buoyancy Planner  ")

        # ── Dive Planner tab ──────────────────────────────────────────────────
        dp_tab = DivePlannerTab(self._db, bp_tab)
        dp_scroll.setWidget(dp_tab)
        self._tabs.addTab(dp_scroll, "  Dive Planner  ")

        # Connect bp_tab user/plan change → dp_tab refresh
        bp_tab._user_cb.currentTextChanged.connect(dp_tab.refresh_for_user)
        bp_tab._plan_cb.currentTextChanged.connect(dp_tab.sync_plan)

        # Load profiles for current user on startup
        user0 = bp_tab._current_user()
        profiles0 = (self._db.get("users", {}).get(user0, {})
                     .get("deco_profiles", {}))
        dp_tab.load_deco_profiles(profiles0)

        # ── Gas Calculator ────────────────────────────────────────────────────
        gc_tab = GasCalcTabQt(db=self._db, save_fn=lambda: save_db(self._db))
        self._tabs.addTab(gc_tab, "  Gas Calculator  ")

        # ── Databases tab ─────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        db_tab = DatabasesTab(self._db, on_db_change=bp_tab.refresh_db)
        scroll.setWidget(db_tab)
        self._tabs.addTab(scroll, "  Databases  ")
        self._tabs.setCurrentIndex(3)   # show Databases first


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
