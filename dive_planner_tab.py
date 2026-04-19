"""
DivePlannerTab — PyQt6 port of the Bühlmann ZHL-16C Decompression Planner.
Functionally identical to the Tkinter DecoTab in cylindercalc_gui.py.
"""

import copy
import sys
import numpy as _np

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as _FigCanvas
from matplotlib.figure import Figure as _Figure

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QFrame, QGroupBox, QSizePolicy, QSplitter,
    QListWidget, QAbstractItemView, QCheckBox, QComboBox, QSpacerItem, QTabWidget,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from cylindercalc import z_mix, calc_gas_mass, RHO_SW, RHO_FW, RHO_PB
from buhlmann import (
    simulate_dive, simulate_bailout_from_bottom,
    CCRConfig, OCGas, TissueState,
    _PN2_SURF, PH2O,
    select_oc_gas, _oc_ascent_waypoints,
    TISSUES, P_SURF,
)

# ── Colours ───────────────────────────────────────────────────────────────────
CLR_BG      = "#f4f4f4"
CLR_HDR     = "#c8dde8"
CLR_STOP    = "#ffffff"
CLR_DROP    = "#b8e8b8"
CLR_INPUT   = "#ffe0e0"
CLR_TOOLBAR = "#e8eaed"
CLR_ADD     = "#aaddaa"
CLR_DEL     = "#ffaaaa"
CLR_EDIT    = "#aabbff"
CLR_GREY    = "#c8c8c8"

TEMP_C_DEFAULT = 20.0


# ── Standalone helpers ────────────────────────────────────────────────────────

def _flt(s, default=0.0):
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return default


def _int_val(s, default=0):
    try:
        return int(float(str(s).strip()))
    except (ValueError, TypeError):
        return default


def _cyl_buoy_from_slot(cyl: dict, slot: dict, temp_c: float = TEMP_C_DEFAULT) -> float:
    """Compute cylinder buoyancy [kg] matching the Buoyancy Planner formula:
       buoy = ref_empty_buoy_sw - actual_gas_mass
       ref_empty_buoy_sw = vol*RHO_SW - (dry - gm_ref)
    """
    dry  = float(cyl.get("dry_mass", 0))
    wet  = float(cyl.get("wet_mass", 0))
    vbot = float(cyl.get("volume_bottle", 0))
    temp = float(cyl.get("temp", temp_c))
    vol  = (dry - wet) / RHO_FW if RHO_FW else 0.0

    # Reference (rated) fill for this cylinder
    ref_o2   = int(cyl.get("o2", 21))
    ref_he   = int(cyl.get("he", 0))
    ref_pres = float(cyl.get("pressure", 0))
    try:
        gm_ref = calc_gas_mass(vbot, ref_pres, temp, ref_o2, ref_he) if ref_pres > 0 else 0.0
    except Exception:
        gm_ref = 0.0
    empty_buoy_sw = vol * RHO_SW - (dry - gm_ref)

    # Actual fill from slot
    o2i  = _int_val(slot.get("o2",  "21"))
    hei  = _int_val(slot.get("he",  "0"))
    pres = _flt(slot.get("pressure", "0"))
    try:
        gm_actual = calc_gas_mass(vbot, pres, temp, o2i, hei) if pres > 0 else 0.0
    except Exception:
        gm_actual = 0.0

    return empty_buoy_sw - gm_actual


def _find_cyl(db: dict, name: str):
    if not name or name == "—":
        return None
    return next((c for c in db.get("cylinders", []) if c.get("name") == name), None)


def _find_equip(db: dict, name: str):
    if not name or name == "—":
        return None
    return next((e for e in db.get("equipment", []) if e.get("name") == name), None)


def _find_diver(db: dict, name: str):
    if not name or name == "—":
        return None
    return next((d for d in db.get("divers", []) if d.get("name") == name), None)


def _compute_buoyancy_from_plan(db: dict, plan_state: dict,
                                temp_c: float = TEMP_C_DEFAULT) -> float | None:
    """Compute total in-water buoyancy [kg] from plan state. Returns None if no data."""
    total = 0.0
    has_any = False

    def _slot_buoy(slot):
        cyl = _find_cyl(db, slot.get("sel", ""))
        if cyl is None:
            return None
        b = _cyl_buoy_from_slot(cyl, slot, temp_c)
        vname = slot.get("valves_sel", "—")
        eq = _find_equip(db, vname) if vname and vname != "—" else None
        b += float(eq.get("buoyancy_sw", 0)) if eq else 0.0
        return b

    for s in plan_state.get("jslots", []):
        if s.get("sel", "—") not in ("—", ""):
            b = _slot_buoy(s)
            if b is not None:
                total += b
                has_any = True

    for s in plan_state.get("sslots", []):
        if s.get("sel", "—") not in ("—", ""):
            b = _slot_buoy(s)
            if b is not None:
                total += b
                has_any = True

    for s in plan_state.get("eslots", []):
        name = s.get("sel", "—")
        if not name or name == "—":
            continue
        d = _find_diver(db, name)
        if d is not None:
            lead = float(d.get("lead_dry_mass") or 0)
            v_lead = lead / RHO_PB if RHO_PB else 0.0
            lb_sw = v_lead * RHO_SW - lead
            total += -lb_sw   # diver_buoyancy_sw = -lead_buoyancy_sw
            has_any = True
        else:
            eq = _find_equip(db, name)
            if eq:
                dry = float(eq.get("dry_mass") or 0)
                wet = float(eq.get("wet_mass") or 0)
                vol = (dry - wet) / RHO_FW if RHO_FW else 0.0
                total += vol * RHO_SW - dry   # buoyancy_sw
                has_any = True

    return total if has_any else None


def _stage_gases_from_plan(db: dict, plan_state: dict,
                           temp_c: float = TEMP_C_DEFAULT) -> list:
    """Return list of {name, o2, he, real_gas_L, pressure} for active sslots."""
    result = []
    for s in plan_state.get("sslots", []):
        name = s.get("sel", "—")
        if not name or name == "—":
            continue
        cyl = _find_cyl(db, name)
        if cyl is None:
            continue
        try:
            o2   = _int_val(s.get("o2",  "0"))
            he   = _int_val(s.get("he",  "0"))
            pres = _flt(s.get("pressure", "0"))
            if pres <= 0:
                rg = 0.0
            else:
                Z  = z_mix(pres + 1.0, temp_c + 273.15, o2, he)
                rg = float(cyl.get("volume_bottle", 0)) * (pres + 1.0) / Z
        except Exception:
            continue
        result.append({"name": name, "o2": o2, "he": he,
                        "real_gas_L": rg, "pressure": pres})
    return result


def _gas_label(o2: int, he: int) -> str:
    if he == 0:
        return "Air" if o2 == 21 else f"Nitrox {o2}"
    return f"Trimix {o2}/{he}"


def _rgl_from_slot(plan_copy: dict, si: int, cyl: dict,
                   o2i: int, hei: int, jslot: bool,
                   temp_c: float = TEMP_C_DEFAULT) -> float:
    slots = plan_copy["jslots"] if jslot else plan_copy["sslots"]
    pres  = float(slots[si].get("pressure", "0") or "0")
    try:
        Z = z_mix(pres + 1.0, temp_c + 273.15, o2i, hei)
        return float(cyl.get("volume_bottle", 0)) * (pres + 1.0) / Z
    except Exception:
        return 0.0


def _solve_pres(remaining_L: float, cyl: dict, o2i: int, hei: int,
                temp_c: float = TEMP_C_DEFAULT) -> float:
    """Bisect to find pressure [barg] from remaining real-gas litres."""
    T_K = temp_c + 273.15
    vol = float(cyl.get("volume_bottle", 0))
    lo, hi = 0.0, 700.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        try:
            Z = z_mix(mid + 1.0, T_K, o2i, hei)
            if vol * (mid + 1.0) / Z < remaining_L:
                lo = mid
            else:
                hi = mid
        except Exception:
            break
    return max(0.0, (lo + hi) / 2.0)


# ── Widget helpers ────────────────────────────────────────────────────────────

def _hline():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:#cccccc;")
    return f


def _btn(text: str, color: str, slot=None) -> QPushButton:
    b = QPushButton(text)
    b.setStyleSheet(f"background:{color}; padding:3px 8px;")
    if slot:
        b.clicked.connect(slot)
    return b


def _lbl_hdr(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"background:{CLR_HDR}; font-weight:bold; padding:2px 4px;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


# ── DivePlannerTab ────────────────────────────────────────────────────────────

class DivePlannerTab(QWidget):
    """PyQt6 port of the Bühlmann decompression planner (DecoTab)."""

    def __init__(self, db: dict, bp_tab):
        super().__init__()
        self._db              = db
        self._bp_tab          = bp_tab          # BuoyancyPlannerTab reference
        self._seg_rows        = []              # list of {depth_le, time_le, frame}
        self._gas_rows        = []              # list of gas row dicts
        self._deco_profiles   = {}
        self._selected_profile = ""
        self._loading         = False
        self._calc_timer      = QTimer(self)
        self._calc_timer.setSingleShot(True)
        self._calc_timer.timeout.connect(self._recalc_and_run)
        self._saved_stops           = []
        self._bail_stops_data       = []
        self._bail_saved_stops      = []
        self._tissue_timeline       = []
        self._bail_tissue_timeline  = []
        self._tissue_phase_list     = []
        self._bail_phase_list       = []
        self._ccr_first_stop_depth  = 0.0
        self._bail_first_stop_depth = 0.0
        self._last_gf_low          = 0.30
        self._last_gf_high         = 0.80
        self._last_sim_args        = None   # cached for GF re-simulation
        self._dil_o2               = "—"   # diluent O2% read from buoyancy plan
        self._dil_he               = "—"   # diluent He% read from buoyancy plan
        self._bail_gas_chart_data  = []    # [(label, initial_L, used_L)] for chart 4
        # Chart canvases (created during build)
        self._profile_canvas: _FigCanvas | None = None
        self._tissue_canvas:  _FigCanvas | None = None
        self._gas_canvas:     _FigCanvas | None = None
        self._chart_tabs:     QTabWidget | None = None

        self._build()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        # Single QGridLayout: 3 rows × 5 columns
        # Row 0: [sidebar|profile|settings|CCR tissue sat|Bail tissue sat]
        # Row 1: [onboard gas ────────────|bailout gas ──────────────────]
        # Row 2: [dive plan CCR ──────────|bailout plan OC ───────────────]
        # The col-2/col-3 boundary is shared by all rows → perfect midline alignment.
        grid_l = QGridLayout(self)
        grid_l.setContentsMargins(6, 6, 6, 6)
        grid_l.setSpacing(6)
        # Equal stretch for all 5 columns so panels share the same column widths
        for c in range(5):
            grid_l.setColumnStretch(c, 1)
        grid_l.setRowStretch(0, 1)   # top row holds consistent height
        grid_l.setRowStretch(1, 3)   # plan row expands more

        # Row 0: sidebar (col 0), onboard (cols 1-4, depth profile inside)
        self._build_sidebar(grid_l, 0, 0)
        self._build_onboard(grid_l, 0, 1)   # spans cols 1-4

        # Row 1: dive plans
        self._build_ccr_plan(grid_l, 1, 0)
        self._build_bail_plan(grid_l, 1, 3)

    # ── Sidebar (Deco Profiles) ───────────────────────────────────────────────

    def _build_sidebar(self, grid_layout, grid_row, grid_col):
        # Outer container: tabs on top, shared controls below
        outer_w  = QWidget()
        outer_vl = QVBoxLayout(outer_w)
        outer_vl.setContentsMargins(0, 0, 0, 0)
        outer_vl.setSpacing(4)

        # ── Tab widget ───────────────────────────────────────────────────────
        tabs = QTabWidget()
        tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #aabbcc; background: #f0f4f8; }"
            "QTabBar::tab { padding: 3px 10px; font-size: 11px; }"
            "QTabBar::tab:selected { background: #d0e4f0; font-weight: bold; }"
        )
        self._sidebar_tabs = tabs

        # Tab 0: Dive Profiles (list only — controls live outside tabs)
        profiles_w = QWidget()
        profiles_vl = QVBoxLayout(profiles_w)
        profiles_vl.setContentsMargins(6, 6, 6, 6)
        profiles_vl.setSpacing(4)

        self._profile_list = QListWidget()
        self._profile_list.setMinimumHeight(80)
        self._profile_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._profile_list.currentRowChanged.connect(self._on_profile_select)
        profiles_vl.addWidget(self._profile_list, stretch=1)

        tabs.addTab(profiles_w, "Dive Profiles")

        # Tab 1: Depth profile
        tabs.addTab(self._build_profile_panel(), "Depth profile")

        outer_vl.addWidget(tabs, stretch=1)

        # ── Shared controls (always visible below tabs) ───────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_row.addWidget(_btn("Save",        CLR_EDIT,  self._save_profile))
        btn_row.addWidget(_btn("Save as new", "#aaffcc", self._save_profile_as_new))
        btn_row.addWidget(_btn("Delete",      CLR_DEL,   self._delete_profile))
        outer_vl.addLayout(btn_row)

        bottom_hl = QHBoxLayout()
        bottom_hl.setSpacing(4)
        bottom_hl.setContentsMargins(0, 0, 0, 0)

        active_box = QGroupBox("Active dive")
        active_bl  = QVBoxLayout(active_box)
        active_bl.setContentsMargins(6, 8, 6, 4)
        self._profile_lbl = QLabel("No profile selected")
        self._profile_lbl.setStyleSheet("font-weight:bold; font-size:11px;")
        self._profile_lbl.setWordWrap(True)
        active_bl.addWidget(self._profile_lbl)
        bottom_hl.addWidget(active_box, stretch=1)

        bp_box = QGroupBox("Buoyancy plan")
        bp_bl  = QVBoxLayout(bp_box)
        bp_bl.setContentsMargins(6, 8, 6, 4)
        self._stage_plan_cb = QComboBox()
        self._stage_plan_cb.addItem("—")
        self._stage_plan_cb.currentTextChanged.connect(self._on_stage_plan_change)
        bp_bl.addWidget(self._stage_plan_cb)
        bottom_hl.addWidget(bp_box, stretch=1)

        outer_vl.addLayout(bottom_hl)

        self._sidebar_outer = outer_w
        grid_layout.addWidget(outer_w, grid_row, grid_col)
        QTimer.singleShot(0, self._lock_sidebar_height)

    # ── Depth profile panel ───────────────────────────────────────────────────

    def _build_profile_panel(self):
        """Build depth profile widget. Returns the widget."""
        box = QWidget()
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        vl  = QVBoxLayout(box)
        vl.setContentsMargins(6, 6, 6, 6)
        vl.setSpacing(2)

        hdr = QWidget()
        hdr.setStyleSheet(f"background:{CLR_HDR};")
        hl  = QHBoxLayout(hdr)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(4)
        lbl_d = QLabel("Depth [m]")
        lbl_d.setFixedWidth(70)
        lbl_d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_d.setStyleSheet("font-weight:bold;")
        lbl_t = QLabel("Time [min]")
        lbl_t.setFixedWidth(70)
        lbl_t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_t.setStyleSheet("font-weight:bold;")
        add_btn = QPushButton("+")
        add_btn.setFixedSize(24, 24)
        add_btn.setStyleSheet("font-size:14px; font-weight:bold; padding:0px;")
        add_btn.clicked.connect(lambda: self._add_seg_row())
        hl.addWidget(lbl_d)
        hl.addWidget(lbl_t)
        hl.addWidget(add_btn)
        hl.addStretch()
        vl.addWidget(hdr)

        self._seg_container = QWidget()
        self._seg_vl = QVBoxLayout(self._seg_container)
        self._seg_vl.setContentsMargins(0, 0, 0, 0)
        self._seg_vl.setSpacing(2)
        vl.addWidget(self._seg_container)
        vl.addStretch()

        self._add_seg_row(depth=60, time=20)
        return box

    def _add_seg_row(self, depth="", time=""):
        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(4)

        d_le = QLineEdit(str(depth))
        d_le.setFixedWidth(70)
        d_le.setAlignment(Qt.AlignmentFlag.AlignRight)
        d_le.setStyleSheet(f"background:{CLR_INPUT};")
        t_le = QLineEdit(str(time))
        t_le.setFixedWidth(70)
        t_le.setAlignment(Qt.AlignmentFlag.AlignRight)
        t_le.setStyleSheet(f"background:{CLR_INPUT};")

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(24, 24)
        del_btn.setStyleSheet(f"background:{CLR_DEL}; padding:0px;")

        row_l.addWidget(d_le)
        row_l.addWidget(t_le)
        row_l.addWidget(del_btn)
        row_l.addStretch()

        row = {"depth_le": d_le, "time_le": t_le, "widget": row_w}
        del_btn.clicked.connect(lambda: self._del_seg_row(row))
        # depth/time do NOT trigger live recalc — only explicit Save does

        self._seg_vl.addWidget(row_w)
        self._seg_rows.append(row)

    def _del_seg_row(self, row):
        row["widget"].deleteLater()
        self._seg_rows.remove(row)

    def _lock_sidebar_height(self):
        """Called once after first layout pass — fixes sidebar height so tab
        switching never changes the top-row height."""
        h = self._sidebar_outer.height()
        if h > 0:
            self._sidebar_outer.setFixedHeight(h)
        else:
            QTimer.singleShot(50, self._lock_sidebar_height)

    # ── Settings panel ────────────────────────────────────────────────────────

    # Settings field definitions (label, attr, default)
    _SETTINGS_FIELDS = [
        ("CCR Descend PO2 [bar]",    "_sp_desc",     "0.7"),
        ("CCR Bottom PO2 [bar]",     "_sp",          "1.3"),
        ("CCR Deco PO2 [bar]",       "_sp_deco",     "1.6"),
        ("CCR Deko last stop [m]",   "_last_stop",   "3"),
        ("GF Low [%]",               "_gf_lo",       "30"),
        ("GF High [%]",              "_gf_hi",       "80"),
        ("Descent rate [m/min]",     "_desc_r",      "20"),
        ("Ascent rate [m/min]",      "_asc_r",       "9"),
        ("Deco rate [m/min]",        "_deco_r",      "3"),
        ("SAC rate [L/min]",         "_sac",         "20"),
        ("Deco switch PO2 BO [bar]", "_deko_sw",     "1.6"),
        ("Stop interval [m]",        "_stop_int",    "3"),
        ("Display interval [m]",     "_display_int", "3"),
        ("Heatmap interval [min]",   "_heatmap_int", "0.1"),
    ]

    def _build_settings(self):
        """Build settings widget (used as a tab). Returns the widget."""
        box = QGroupBox("Settings")
        outer_vl = QVBoxLayout(box)
        outer_vl.setContentsMargins(6, 8, 6, 6)
        outer_vl.setSpacing(4)

        fields_w = QWidget()
        box_l = QHBoxLayout(fields_w)
        box_l.setContentsMargins(0, 0, 0, 0)
        box_l.setSpacing(8)

        fields = self._SETTINGS_FIELDS
        mid = (len(fields) + 1) // 2

        for col_fields in [fields[:mid], fields[mid:]]:
            col_w = QWidget()
            col_w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            col_l = QGridLayout(col_w)
            col_l.setContentsMargins(0, 0, 0, 0)
            col_l.setSpacing(2)
            for r, (label, attr, default) in enumerate(col_fields):
                lbl = QLabel(label + ":")
                lbl.setFixedWidth(180)
                le  = QLineEdit(default)
                le.setFixedWidth(60)
                le.setAlignment(Qt.AlignmentFlag.AlignRight)
                le.setStyleSheet(f"background:{CLR_INPUT};")
                setattr(self, attr + "_le", le)
                le.textChanged.connect(self._on_input_change)
                col_l.addWidget(lbl, r, 0)
                col_l.addWidget(le,  r, 1)
            box_l.addWidget(col_w)

        box_l.addStretch()
        outer_vl.addWidget(fields_w)

        # Save button row
        btn_row = QHBoxLayout()
        self._settings_status_lbl = QLabel("")
        self._settings_status_lbl.setStyleSheet("color:#44cc88; font-size:9px;")
        save_btn = QPushButton("Save settings")
        save_btn.setFixedWidth(110)
        save_btn.setStyleSheet("background:#2a5a2a; color:white; font-weight:bold; padding:3px 8px;")
        save_btn.clicked.connect(self._save_global_settings)
        btn_row.addStretch()
        btn_row.addWidget(self._settings_status_lbl)
        btn_row.addWidget(save_btn)
        outer_vl.addLayout(btn_row)

        return box

    def _save_global_settings(self):
        """Save current settings fields to db as global settings."""
        gs = {}
        for _, attr, default in self._SETTINGS_FIELDS:
            le = getattr(self, attr + "_le", None)
            gs[attr] = le.text() if le else default
        self._db["global_settings"] = gs
        from main_qt import save_db
        save_db(self._db)
        self._settings_status_lbl.setText("Saved ✓")
        QTimer.singleShot(2000, lambda: self._settings_status_lbl.setText(""))

    def _load_global_settings(self):
        """Load global settings from db into settings fields."""
        gs = self._db.get("global_settings", {})
        for _, attr, default in self._SETTINGS_FIELDS:
            le = getattr(self, attr + "_le", None)
            if le:
                le.setText(gs.get(attr, default))

    # ── CCR Tissue Saturations panel ──────────────────────────────────────────

    def _build_ccr_tissue_sat(self):
        ccr_ts = QGroupBox("CCR Tissue Saturations")
        ccr_ts.setStyleSheet("QGroupBox { color: #44aacc; }")
        ccr_ts_l = QVBoxLayout(ccr_ts)
        ccr_ts_l.setContentsMargins(6, 8, 6, 6)
        ccr_ts_l.setSpacing(4)

        _TAB_NAMES = ["Saturation heatmap", "Leading compartment", "Bar chart (animated)"]

        surf_box = QGroupBox("Surface M-value")
        surf_box.setStyleSheet("QGroupBox { color: #4488cc; }")
        surf_l = QHBoxLayout(surf_box)
        surf_l.setContentsMargins(4, 8, 4, 4)
        surf_l.setSpacing(4)
        for _ti, _tn in enumerate(_TAB_NAMES):
            _btn = QPushButton(_tn)
            _btn.setStyleSheet("background:#556688; color:white; font-weight:bold; padding:3px 8px;")
            _btn.clicked.connect(lambda _chk=False, _t=_ti: self._open_tissue_window(surface_mv=True, bailout=False, tab=_t))
            surf_l.addWidget(_btn)
        surf_l.addStretch()
        ccr_ts_l.addWidget(surf_box)

        dep_box = QGroupBox("Depth M-value")
        dep_box.setStyleSheet("QGroupBox { color: #226644; }")
        dep_l = QHBoxLayout(dep_box)
        dep_l.setContentsMargins(4, 8, 4, 4)
        dep_l.setSpacing(4)
        for _ti, _tn in enumerate(_TAB_NAMES):
            _btn = QPushButton(_tn)
            _btn.setStyleSheet("background:#3d6655; color:white; font-weight:bold; padding:3px 8px;")
            _btn.clicked.connect(lambda _chk=False, _t=_ti: self._open_tissue_window(surface_mv=False, bailout=False, tab=_t))
            dep_l.addWidget(_btn)
        dep_l.addStretch()
        ccr_ts_l.addWidget(dep_box)

        gf_box = QGroupBox("GF Comparison")
        gf_box.setStyleSheet("QGroupBox { color: #aa8844; }")
        gf_l = QHBoxLayout(gf_box)
        gf_l.setContentsMargins(4, 8, 4, 4)
        b_gf = QPushButton("Fixed Bottom Time Comparison")
        b_gf.setStyleSheet("background:#7a5c00; color:white; font-weight:bold; padding:3px 8px;")
        b_gf.clicked.connect(self._open_gf_comparison)
        gf_l.addWidget(b_gf); gf_l.addStretch()
        ccr_ts_l.addWidget(gf_box)
        return ccr_ts

    # ── Bailout Tissue Saturations panel ──────────────────────────────────────

    def _build_bail_tissue_sat(self):
        bail_ts = QGroupBox("Bailout Tissue Saturations")
        bail_ts.setStyleSheet("QGroupBox { color: #cc8844; }")
        bail_ts_l = QVBoxLayout(bail_ts)
        bail_ts_l.setContentsMargins(6, 8, 6, 6)
        bail_ts_l.setSpacing(4)

        _TAB_NAMES = ["Saturation heatmap", "Leading compartment", "Bar chart (animated)"]

        bail_surf_box = QGroupBox("Surface M-value")
        bail_surf_box.setStyleSheet("QGroupBox { color: #4488cc; }")
        bail_surf_l = QHBoxLayout(bail_surf_box)
        bail_surf_l.setContentsMargins(4, 8, 4, 4)
        bail_surf_l.setSpacing(4)
        for _ti, _tn in enumerate(_TAB_NAMES):
            _btn = QPushButton(_tn)
            _btn.setStyleSheet("background:#556688; color:white; font-weight:bold; padding:3px 8px;")
            _btn.clicked.connect(lambda _chk=False, _t=_ti: self._open_tissue_window(surface_mv=True, bailout=True, tab=_t))
            bail_surf_l.addWidget(_btn)
        bail_surf_l.addStretch()
        bail_ts_l.addWidget(bail_surf_box)

        bail_dep_box = QGroupBox("Depth M-value")
        bail_dep_box.setStyleSheet("QGroupBox { color: #226644; }")
        bail_dep_l = QHBoxLayout(bail_dep_box)
        bail_dep_l.setContentsMargins(4, 8, 4, 4)
        bail_dep_l.setSpacing(4)
        for _ti, _tn in enumerate(_TAB_NAMES):
            _btn = QPushButton(_tn)
            _btn.setStyleSheet("background:#3d6655; color:white; font-weight:bold; padding:3px 8px;")
            _btn.clicked.connect(lambda _chk=False, _t=_ti: self._open_tissue_window(surface_mv=False, bailout=True, tab=_t))
            bail_dep_l.addWidget(_btn)
        bail_dep_l.addStretch()
        bail_ts_l.addWidget(bail_dep_box)

        rep_box = QGroupBox("Report")
        rep_box.setStyleSheet("QGroupBox { color: #aa8844; }")
        rep_l = QHBoxLayout(rep_box)
        rep_l.setContentsMargins(4, 8, 4, 4)
        b_rep = QPushButton("Generate Report")
        b_rep.setStyleSheet("background:#7a5c00; color:white; font-weight:bold; padding:3px 8px;")
        b_rep.clicked.connect(self._open_report)
        rep_l.addWidget(b_rep); rep_l.addStretch()
        bail_ts_l.addWidget(rep_box)
        return bail_ts

    # ── Report ───────────────────────────────────────────────────────────────

    def _open_report(self):
        try:
            text = self._build_report()
        except Exception:
            import traceback
            text = "Feil ved generering av rapport:\n" + traceback.format_exc()
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QFileDialog
        from PyQt6.QtGui import QFont
        dlg = QDialog(self)
        dlg.setWindowTitle("Deco Report")
        dlg.resize(900, 700)
        vl = QVBoxLayout(dlg)
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("Courier New", 9))
        txt.setStyleSheet("background:#1e1e1e; color:#d4d4d4;")
        txt.setPlainText(text)
        vl.addWidget(txt)
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save to file...")
        btn_save.setStyleSheet("background:#aaaadd; font-weight:bold; padding:4px 12px;")
        btn_close = QPushButton("Close")
        btn_close.setStyleSheet("padding:4px 12px;")
        btn_row.addWidget(btn_save); btn_row.addStretch(); btn_row.addWidget(btn_close)
        vl.addLayout(btn_row)
        def _save():
            path, _ = QFileDialog.getSaveFileName(dlg, "Save Report", "rapport.txt",
                                                   "Text files (*.txt);;All files (*.*)")
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(txt.toPlainText())
        btn_save.clicked.connect(_save)
        btn_close.clicked.connect(dlg.accept)
        dlg.exec()

    def _build_report(self) -> str:
        import io, copy as _copy
        from cylindercalc import _COOLPROP_AVAILABLE
        buf = io.StringIO()

        def p(*args, **kw):
            kw["file"] = buf
            print(*args, **kw)

        # ── Collect settings ─────────────────────────────────────────────────
        segments = []
        for row in self._seg_rows:
            try:
                d = float(row["depth_le"].text())
                t = float(row["time_le"].text())
                if d > 0:
                    segments.append((d, t))
            except ValueError:
                pass
        if not segments:
            return "Ingen dive-segmenter definert."

        CCR_SP   = self._get_setting("_sp",       1.3)
        DIL_O2   = _int_val(self._dil_o2, 21)
        DIL_HE   = _int_val(self._dil_he, 35)
        GF_LO    = self._get_setting("_gf_lo",   55)
        GF_HI    = self._get_setting("_gf_hi",   70)
        DESC_R   = self._get_setting("_desc_r",  22)
        ASC_R    = self._get_setting("_asc_r",    9)
        DECO_R   = self._get_setting("_deco_r",   3)
        SAC      = self._get_setting("_sac",     20)
        STOP_INT = max(1.0, self._get_setting("_stop_int", 3.0))
        T_c      = TEMP_C_DEFAULT

        plan_name = self._stage_plan_cb.currentText()
        user_now  = self._bp_tab._current_user()
        plan_state = None
        if plan_name != "—" and user_now in self._db.get("users", {}):
            plan_state = self._db["users"][user_now].get("buoyancy_plans", {}).get(plan_name)
        if not plan_state:
            return "Ingen buoyancy-plan valgt. Velg en plan i 'Buoyancy plan'-feltet."

        # ── Active sslots ────────────────────────────────────────────────────
        active_sslots = []
        for si, s in enumerate(plan_state.get("sslots", [])):
            nm = s.get("sel", "—")
            if not nm or nm == "—":
                continue
            cyl = _find_cyl(self._db, nm)
            if cyl is None:
                continue
            active_sslots.append((si, s, cyl))

        # ── Bailout gases from gas rows ──────────────────────────────────────
        from buhlmann import CCRConfig, OCGas, simulate_bailout_from_bottom
        oc_gases      = []
        bailout_gases = []
        for row_i, row in enumerate(self._gas_rows):
            if not row.get("active", True):
                continue
            o2i = row.get("o2", 0)
            hei = row.get("he", 0)
            sw  = _flt(row["sw_le"].text(), 999)
            if o2i <= 0:
                continue
            oc_gas = OCGas(o2=o2i / 100, he=hei / 100, switch_depth=sw)
            oc_gases.append(oc_gas)
            # Match to sslot by stage index from dropdown
            sel = row["stage_cb"].currentText()
            if sel.startswith("Stage"):
                try:
                    stage_idx = int(sel.split()[-1]) - 1
                except (ValueError, IndexError):
                    stage_idx = -1
                matching = next((item for item in active_sslots if item[0] == stage_idx), None)
                if matching:
                    si, s, cyl = matching
                    bailout_gases.append({
                        "si": si, "label": oc_gas.label(),
                        "o2i": o2i, "hei": hei, "sw": sw, "cyl": cyl,
                    })

        if not oc_gases:
            oc_gases = [OCGas(o2=0.21, he=0.0, switch_depth=999)]

        # ── Helpers ──────────────────────────────────────────────────────────
        W  = 74
        TH = "═" * W

        def hdr(title):
            p(f"\n{TH}\n  {title}\n{TH}")

        def solve_pressure(remaining_L, cyl, o2i, hei):
            lo, hi = 0.0, 700.0
            vol = float(cyl.get("volume_bottle", 0))
            for _ in range(60):
                mid = (lo + hi) / 2.0
                Z = z_mix(mid + 1.0, T_c + 273.15, o2i, hei)
                if vol * (mid + 1.0) / Z < remaining_L:
                    lo = mid
                else:
                    hi = mid
            return max(0.0, (lo + hi) / 2.0)

        def rgl_from_plan(plan_copy, si, cyl, o2i, hei):
            pres = float(plan_copy["sslots"][si].get("pressure", "0") or "0")
            Z = z_mix(pres + 1.0, T_c + 273.15, o2i, hei)
            return float(cyl.get("volume_bottle", 0)) * (pres + 1.0) / Z

        def consume_gas(plan_copy, si, cyl, o2i, hei, used_L):
            rgl = rgl_from_plan(plan_copy, si, cyl, o2i, hei)
            remaining = max(0.0, rgl - used_L)
            new_pres = solve_pressure(remaining, cyl, o2i, hei)
            plan_copy["sslots"][si]["pressure"] = f"{new_pres:.4f}"
            return rgl_from_plan(plan_copy, si, cyl, o2i, hei)

        def _gas_label(o2i, hei):
            if hei == 0:
                return f"Nitrox {o2i}" if o2i != 21 else "Air"
            return f"Trimix {o2i}/{hei}"

        # ── SEKSJON 1 ────────────────────────────────────────────────────────
        eos = "GERG-2008 (CoolProp)" if _COOLPROP_AVAILABLE else "PR/Virial fallback"
        hdr("SEKSJON 1 — INNDATA")
        p(f"  EOS              : {eos}")
        p(f"  Buoyancy plan    : {plan_name}")
        p(f"  Diluent          : {_gas_label(DIL_O2, DIL_HE)}")
        p(f"  CCR setpoint     : {CCR_SP} bar")
        p(f"  GF               : {GF_LO:.0f} / {GF_HI:.0f}")
        p(f"  Nedstigning      : {DESC_R} m/min")
        p(f"  Stigning         : {ASC_R} m/min")
        p(f"  Deko-stigning    : {DECO_R} m/min")
        p(f"  SAC (bailout)    : {SAC} L/min")
        p(f"  Stop intervall   : {STOP_INT:.0f} m")
        p(f"  Temp (gass)      : {T_c} °C")
        p(f"\n  Dykke-segmenter:")
        for d, t in segments:
            p(f"    {d:.0f} m  /  {t:.0f} min")

        # ── SEKSJON 2 ────────────────────────────────────────────────────────
        hdr(f"SEKSJON 2 — OPPDRIFT VED START  ({plan_name}, hentet fra buoyancy-modellen)")
        bt0 = _compute_buoyancy_from_plan(self._db, plan_state, T_c)
        p()
        for si, s in enumerate(plan_state.get("sslots", [])):
            nm = s.get("sel", "—")
            if not nm or nm == "—":
                p(f"  Stage {si+1}                        : Not active")
                continue
            o2i  = _int_val(s.get("o2", "0"))
            hei  = _int_val(s.get("he", "0"))
            pres = _flt(s.get("pressure", "0"))
            cyl  = _find_cyl(self._db, nm)
            if cyl is None:
                p(f"  Stage {si+1}  {_gas_label(o2i, hei):<14} {pres:.0f} barg  : (cylinder not found)")
                continue
            b = _cyl_buoy_from_slot(cyl, s, T_c)
            p(f"  Stage {si+1}  {_gas_label(o2i, hei):<14} {pres:.0f} barg  : {b:+.2f} kg")
        p()
        p(f"  TOTAL  CCR + Diver + Stages   : {bt0:+.2f} kg" if bt0 is not None else "  TOTAL: N/A")

        # ── SEKSJON 3 ────────────────────────────────────────────────────────
        hdr("SEKSJON 3 — BÜHLMANN BAILOUT-SIMULERING")
        segs_str = "  |  ".join(f"{d:.0f} m / {t:.0f} min" for d, t in segments)
        p(f"  Profil : {segs_str}  |  CCR  |  {_gas_label(DIL_O2, DIL_HE)}"
          f"  |  SP {CCR_SP}  |  GF {GF_LO:.0f}/{GF_HI:.0f}")

        ccr = CCRConfig(setpoint=CCR_SP, diluent_o2=DIL_O2/100, diluent_he=DIL_HE/100)
        bail = simulate_bailout_from_bottom(
            segments=segments, ccr=ccr, oc_gases=oc_gases,
            gf_low=GF_LO/100, gf_high=GF_HI/100,
            desc_rate=DESC_R, asc_rate=ASC_R, deco_rate=DECO_R,
            stop_interval=STOP_INT,
        )
        p(f"\n  Bunntid (CCR):    {bail.bottom_time:.1f} min")
        p(f"  TTS (OC bailout): {bail.tts:.1f} min")
        p(f"  Total runtime:    {bail.runtime:.1f} min")
        p(f"  OTU:              {bail.otu:.0f}")
        p(f"  CNS:              {bail.cns:.1f} %")
        p(f"\n  {'Dybde':>8}  {'Tid':>6}  {'Runtime':>9}  Gass")
        p(f"  {'-'*8}  {'-'*6}  {'-'*9}  {'-'*20}")
        for s in bail.stops:
            p(f"  {s.depth:>6.0f} m  {s.time:>5.0f} min  {s.runtime:>7.1f} min  {s.gas}")

        # ── SEKSJON 4 ────────────────────────────────────────────────────────
        hdr("SEKSJON 4 — GASSFORBRUK → NYTT TRYKK → OPPDRIFT  (steg for steg)")
        p("""
  Prinsipp:
    1. Beregn forbruk_L  =  SAC × tid × bar_abs
    2. remaining_L  =  real_gas_L  -  forbruk_L
    3. Løs nytt trykk via biseksjon:
         finn P slik at  vol × (P+1) / Z(P+1)  =  remaining_L
    4. Oppdater P i plan-kopi
    5. Kall compute_buoyancy_from_plan(plan_kopi)  →  total oppdrift

  JJ-flasker, utstyr og diver endres IKKE — de hentes uendret fra plan-kopien.
""")

        def _trk(label):
            return next((g for g in bailout_gases if g["label"] == label), None)

        plan_base = _copy.deepcopy(plan_state)
        bail_stops = [{"depth": s.depth, "time": s.time, "runtime": s.runtime, "gas": s.gas}
                      for s in bail.stops]
        max_d = max(d for d, _ in segments)
        rows  = []

        # Transit
        if bail_stops and max_d > bail_stops[0]["depth"]:
            first_d      = bail_stops[0]["depth"]
            transit_time = (max_d - first_d) / ASC_R
            transit_avg  = (max_d + first_d) / 2.0
            transit_bar  = transit_avg / 10.0 + 1.0
            transit_L    = SAC * transit_time * transit_bar
            best_sw, transit_label = -1, None
            for og in oc_gases:
                if og.switch_depth <= max_d and og.switch_depth > best_sw:
                    best_sw = og.switch_depth
                    transit_label = og.label()
            te = _trk(transit_label)
            if te:
                pres_before = float(plan_base["sslots"][te["si"]].get("pressure", "0") or "0")
                rgl_before  = rgl_from_plan(plan_base, te["si"], te["cyl"], te["o2i"], te["hei"])
                p(f"  {'═'*70}")
                p(f"  TRANSIT  {max_d:.0f} m → {first_d:.0f} m  |  Gass: {transit_label}")
                p(f"  {'─'*70}")
                p(f"    Trykk FØR           : {pres_before:.2f} barg")
                p(f"    real_gas_L FØR      : {rgl_before:.2f} L")
                p(f"    bar_abs             = {transit_avg:.1f}/10 + 1  =  {transit_bar:.3f} bar")
                p(f"    forbruk_L           = {SAC} × {transit_time:.3f} × {transit_bar:.3f}"
                  f"  =  {transit_L:.2f} L")
                remaining = max(0.0, rgl_before - transit_L)
                p(f"    remaining_L         = {rgl_before:.2f} - {transit_L:.2f}"
                  f"  =  {remaining:.2f} L")
                rest_L    = consume_gas(plan_base, te["si"], te["cyl"], te["o2i"], te["hei"], transit_L)
                pres_after = float(plan_base["sslots"][te["si"]].get("pressure", "0") or "0")
                Z_after   = z_mix(pres_after + 1.0, T_c + 273.15, te["o2i"], te["hei"])
                vol       = float(te["cyl"].get("volume_bottle", 0))
                p(f"    Biseksjon → P_ny    : {pres_after:.4f} barg")
                p(f"    Kontroll: vol×(P+1)/Z = {vol}×{pres_after+1:.4f}/{Z_after:.4f}"
                  f"  =  {rest_L:.2f} L  ✓")
                bt = _compute_buoyancy_from_plan(self._db, plan_base, T_c)
                p(f"\n    compute_buoyancy_from_plan(plan_kopi):")
                p(f"      TOTAL     : {bt:+.4f} kg  ← CCR + Diver + Stages" if bt is not None else "      TOTAL: N/A")
                rows.append({"fase": f"↓{max_d:.0f}→{first_d:.0f}m", "dybde": max_d,
                             "tid": transit_time, "forbruk": transit_L,
                             "rest": rest_L, "pres_etter": pres_after, "total": bt})

        # Deko-stopp
        for stop in bail_stops:
            bar_abs = stop["depth"] / 10.0 + 1.0
            used_L  = SAC * stop["time"] * bar_abs
            te      = _trk(stop["gas"])
            p(f"\n  {'═'*70}")
            p(f"  STOPP  {stop['depth']:.0f} m / {stop['time']:.0f} min"
              f"  |  Runtime: {stop['runtime']:.1f} min  |  Gass: {stop['gas']}")
            p(f"  {'─'*70}")
            if te:
                pres_before = float(plan_base["sslots"][te["si"]].get("pressure", "0") or "0")
                rgl_before  = rgl_from_plan(plan_base, te["si"], te["cyl"], te["o2i"], te["hei"])
                p(f"    Trykk FØR           : {pres_before:.4f} barg")
                p(f"    real_gas_L FØR      : {rgl_before:.2f} L")
                p(f"    bar_abs             = {stop['depth']:.0f}/10 + 1  =  {bar_abs:.2f} bar")
                p(f"    forbruk_L           = {SAC} × {stop['time']:.0f} × {bar_abs:.2f}"
                  f"  =  {used_L:.2f} L")
                remaining = max(0.0, rgl_before - used_L)
                p(f"    remaining_L         = {rgl_before:.2f} - {used_L:.2f}"
                  f"  =  {remaining:.2f} L")
                rest_L    = consume_gas(plan_base, te["si"], te["cyl"], te["o2i"], te["hei"], used_L)
                pres_after = float(plan_base["sslots"][te["si"]].get("pressure", "0") or "0")
                Z_after   = z_mix(pres_after + 1.0, T_c + 273.15, te["o2i"], te["hei"])
                vol       = float(te["cyl"].get("volume_bottle", 0))
                p(f"    Biseksjon → P_ny    : {pres_after:.4f} barg")
                p(f"    Kontroll: vol×(P+1)/Z = {vol}×{pres_after+1:.4f}/{Z_after:.4f}"
                  f"  =  {rest_L:.2f} L  ✓")
            else:
                rest_L, pres_after = None, None
                p(f"    bar_abs   = {bar_abs:.2f} bar")
                p(f"    forbruk_L = {used_L:.2f} L  (ingen stage-tracking for denne gassen)")
            bt = _compute_buoyancy_from_plan(self._db, plan_base, T_c)
            p(f"\n    compute_buoyancy_from_plan(plan_kopi):")
            p(f"      TOTAL     : {bt:+.4f} kg  ← CCR + Diver + Stages" if bt is not None else "      TOTAL: N/A")
            rows.append({"fase": f"{stop['depth']:.0f} m", "dybde": stop["depth"],
                         "tid": stop["time"], "forbruk": used_L,
                         "rest": rest_L, "pres_etter": pres_after, "total": bt})

        # ── SEKSJON 5 ────────────────────────────────────────────────────────
        hdr("SEKSJON 5 — SAMLET OVERSIKT")
        p(f"\n  {'Fase':<14} {'Dybde':>7} {'Tid':>6} {'Forbruk L':>10} "
          f"{'Rest L':>8} {'P_ny barg':>10} {'Oppdrift kg':>12}")
        p(f"  {'-'*14} {'-'*7} {'-'*6} {'-'*10} {'-'*8} {'-'*10} {'-'*12}")
        for r in rows:
            rest_s = f"{r['rest']:.0f}"       if r["rest"]       is not None else "—"
            pres_s = f"{r['pres_etter']:.1f}" if r["pres_etter"] is not None else "—"
            tot_s  = f"{r['total']:+.3f}"     if r["total"]      is not None else "—"
            p(f"  {r['fase']:<14} {r['dybde']:>7.0f} {r['tid']:>6.1f} "
              f"{r['forbruk']:>10.0f} {rest_s:>8} {pres_s:>10} {tot_s:>12}")
        p(f"  {'-'*70}")
        if rows:
            p(f"\n  Startoppdrift (ved bunnen) : {bt0:+.3f} kg" if bt0 is not None else "  Startoppdrift: N/A")
            last_tot = rows[-1]["total"]
            p(f"  Sluttoppdrift (etter deko) : {last_tot:+.3f} kg" if last_tot is not None else "  Sluttoppdrift: N/A")
            if bt0 is not None and last_tot is not None:
                p(f"  Endring gjennom bailout    : {last_tot - bt0:+.3f} kg")
        p(f"\n  Trykk-status stage-flasker ved slutten:")
        for g in bailout_gases:
            pres = float(plan_base["sslots"][g["si"]].get("pressure", "0") or "0")
            rgl  = rgl_from_plan(plan_base, g["si"], g["cyl"], g["o2i"], g["hei"])
            p(f"    {g['label']:<18}  P = {pres:.1f} barg  |  {rgl:.0f} L igjen")

        p(f"\n{'═'*W}\n  Rapport ferdig.\n{'═'*W}")
        return buf.getvalue()

    def _get_setting(self, attr: str, default: float) -> float:
        le = getattr(self, attr + "_le", None)
        if le is None:
            return default
        return _flt(le.text(), default)

    # ── Onboard Gas panel ─────────────────────────────────────────────────────

    def _build_onboard(self, grid_layout, grid_row, grid_col):
        box = QGroupBox("Onboard Gas")
        outer_hl = QHBoxLayout(box)
        outer_hl.setContentsMargins(6, 8, 6, 6)
        outer_hl.setSpacing(8)

        # ── Onboard Gas tab widget ───────────────────────────────────────────
        left_w = QWidget()
        vl = QVBoxLayout(left_w)
        vl.setContentsMargins(4, 4, 4, 4)
        vl.setSpacing(2)

        # Header row
        hdr_w = QWidget()
        hdr_w.setStyleSheet(f"background:{CLR_HDR};")
        hdr_l = QHBoxLayout(hdr_w)
        hdr_l.setContentsMargins(2, 2, 2, 2)
        hdr_l.setSpacing(4)
        for txt, w in [("Role", 90), ("Source", 120), ("Name", 130),
                       ("Press[bar]", 75), ("Mix", 110)]:
            h = QLabel(txt)
            h.setFixedWidth(w)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet("font-weight:bold;")
            hdr_l.addWidget(h)
        hdr_l.addStretch()
        vl.addWidget(hdr_w)

        self._onboard_cbs  = []
        self._onboard_info = []   # list of (name_lbl, pres_lbl, mix_lbl) per row

        def _cyl_opts():
            return ["Cylinder 1", "Cylinder 2", "Cylinder 3", "Cylinder 4"]

        for i, role_txt in enumerate(("Diluent gas:", "Oxygen:", "Inflation gas:", "BCD gas:")):
            row_w = QWidget()
            rl    = QHBoxLayout(row_w)
            rl.setContentsMargins(2, 1, 2, 1)
            rl.setSpacing(4)

            role_lbl = QLabel(role_txt)
            role_lbl.setFixedWidth(90)
            cb = QComboBox()
            cb.setFixedWidth(120)
            cb.addItems(_cyl_opts())
            cb.currentTextChanged.connect(self._on_input_change)

            name_lbl = QLabel("—")
            name_lbl.setFixedWidth(130)
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pres_lbl = QLabel("—")
            pres_lbl.setFixedWidth(75)
            pres_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mix_lbl  = QLabel("—")
            mix_lbl.setFixedWidth(110)
            mix_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            rl.addWidget(role_lbl)
            rl.addWidget(cb)
            rl.addWidget(name_lbl)
            rl.addWidget(pres_lbl)
            rl.addWidget(mix_lbl)
            rl.addStretch()
            vl.addWidget(row_w)

            self._onboard_cbs.append(cb)
            self._onboard_info.append((name_lbl, pres_lbl, mix_lbl))

        self._onboard_cbs[0].currentTextChanged.connect(self._on_dil_cyl_change)
        vl.addStretch()

        # ── Tabbed area (Onboard Gas + charts) ──────────────────────────────
        self._chart_tabs = QTabWidget()
        self._chart_tabs.setMinimumHeight(185)
        self._chart_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #aabbcc; background: #f0f4f8; }"
            "QTabBar::tab { padding: 3px 10px; font-size: 11px; }"
            "QTabBar::tab:selected { background: #d0e4f0; font-weight: bold; }"
        )

        # Tab 0 — Onboard Gas
        self._chart_tabs.addTab(left_w, "Onboard Gas")

        # Tab 1 — Bailout Gas
        bailout_gas_w = self._build_bailout_gas()
        self._chart_tabs.addTab(bailout_gas_w, "Bailout Gas")

        # Tab 2 — Dive profile (matplotlib)
        fig1 = _Figure(figsize=(4, 2.2), facecolor="#f0f4f8")
        fig1.subplots_adjust(left=0.10, right=0.97, top=0.88, bottom=0.18)
        self._profile_canvas = _FigCanvas(fig1)
        tab1 = QWidget()
        QVBoxLayout(tab1).addWidget(self._profile_canvas)
        tab1.layout().setContentsMargins(0, 0, 0, 0)
        self._chart_tabs.addTab(tab1, "Dive profile")

        # Tab 3 — Tissue Saturations (CCR + Bailout side by side)
        tissue_tab = QWidget()
        tissue_hl  = QHBoxLayout(tissue_tab)
        tissue_hl.setContentsMargins(4, 4, 4, 4)
        tissue_hl.setSpacing(6)
        tissue_hl.addWidget(self._build_ccr_tissue_sat())
        tissue_hl.addWidget(self._build_bail_tissue_sat())
        self._chart_tabs.addTab(tissue_tab, "Tissue saturations")

        # Tab 2 — Tissue saturation
        fig2 = _Figure(figsize=(4, 2.2), facecolor="#1a1a2e")
        fig2.subplots_adjust(left=0.10, right=0.97, top=0.88, bottom=0.18)
        self._tissue_canvas = _FigCanvas(fig2)
        tab2 = QWidget()
        QVBoxLayout(tab2).addWidget(self._tissue_canvas)
        tab2.layout().setContentsMargins(0, 0, 0, 0)
        self._chart_tabs.addTab(tab2, "Vevsmettning")

        # Tab 3 — Gas consumption
        fig3 = _Figure(figsize=(4, 2.2), facecolor="#1a1a2e")
        fig3.subplots_adjust(left=0.10, right=0.97, top=0.88, bottom=0.22)
        self._gas_canvas = _FigCanvas(fig3)
        tab3 = QWidget()
        QVBoxLayout(tab3).addWidget(self._gas_canvas)
        tab3.layout().setContentsMargins(0, 0, 0, 0)
        self._chart_tabs.addTab(tab3, "Gassforbruk")

        outer_hl.addWidget(self._chart_tabs, stretch=1)
        outer_hl.setContentsMargins(0, 0, 0, 0)

        # ── Settings group box (to the right of Onboard Gas) ────────────────
        settings_box = self._build_settings()

        # ── Container holding both group boxes side by side ──────────────────
        container = QWidget()
        container_hl = QHBoxLayout(container)
        container_hl.setContentsMargins(0, 0, 0, 0)
        container_hl.setSpacing(4)

        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        settings_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        container_hl.addWidget(box, stretch=1)
        container_hl.addWidget(settings_box, stretch=0)

        grid_layout.addWidget(container, grid_row, grid_col, 1, 4)   # spans cols 1-4

    # ── Bailout gas table ─────────────────────────────────────────────────────

    def _build_bailout_gas(self):
        """Build bailout gas widget (used as a tab). Returns the widget."""
        w  = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(4, 4, 4, 4)
        vl.setSpacing(2)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet(f"background:{CLR_HDR};")
        hl  = QHBoxLayout(hdr)
        hl.setContentsMargins(2, 2, 2, 2)
        hl.setSpacing(2)
        for txt, width in [("Role", 100), ("Source", 90), ("Name", 130),
                            ("Press[bar]", 70), ("Mix", 100), ("Gas[L]", 55),
                            ("Switch[m]", 70), ("PO2", 50),
                            ("Drop", 40), ("ΔPN₂ ICD", 60)]:
            lbl = QLabel(txt)
            lbl.setFixedWidth(width)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-weight:bold;")
            hl.addWidget(lbl)
        hl.addStretch()
        vl.addWidget(hdr)

        # Gas rows container
        self._gas_frame = QWidget()
        self._gas_gl    = QVBoxLayout(self._gas_frame)
        self._gas_gl.setContentsMargins(0, 0, 0, 0)
        self._gas_gl.setSpacing(2)
        vl.addWidget(self._gas_frame)
        vl.addStretch()

        # Build initial 4 rows
        self._rebuild_gas_rows([])
        return w

    def _add_gas_row(self, idx: int, stage, sw="", drop=False,
                     role=None, stage_sel=None, sw_manual=None):
        _default_roles = {0: "Bailout gas", 1: "Interstage gas",
                          2: "Deco gas 1",  3: "Deco gas 2"}
        if role is None:
            role = _default_roles.get(idx, "—")
        if stage_sel is None:
            stage_sel = f"Stage {idx + 1}"
        _is_bailout = (idx == 0)
        if _is_bailout:
            _user_edited = [False]
        elif sw_manual is not None:
            _user_edited = [sw_manual]
        else:
            _user_edited = [bool(str(sw).strip())]

        row_w = QWidget()
        hl    = QHBoxLayout(row_w)
        hl.setContentsMargins(2, 1, 2, 1)
        hl.setSpacing(2)

        # Role label
        role_lbl = QLabel(role)
        role_lbl.setFixedWidth(100)
        hl.addWidget(role_lbl)

        # Source combobox
        stage_opts = ["—", "Stage 1", "Stage 2", "Stage 3", "Stage 4",
                      "Cylinder 1", "Cylinder 2", "Cylinder 3", "Cylinder 4"]
        stage_cb = QComboBox()
        stage_cb.addItems(stage_opts)
        stage_cb.setFixedWidth(90)
        if stage_sel in stage_opts:
            stage_cb.setCurrentText(stage_sel)
        hl.addWidget(stage_cb)

        # Display fields (read-only labels)
        name_lbl = QLabel("")
        name_lbl.setFixedWidth(130)
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pres_lbl = QLabel("")
        pres_lbl.setFixedWidth(70)
        pres_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mix_lbl  = QLabel("")
        mix_lbl.setFixedWidth(100)
        gasl_lbl = QLabel("")
        gasl_lbl.setFixedWidth(55)
        gasl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for lbl in (name_lbl, pres_lbl, mix_lbl, gasl_lbl):
            hl.addWidget(lbl)

        # Switch depth
        sw_le = QLineEdit(str(sw) if sw else "")
        sw_le.setFixedWidth(70)
        sw_le.setAlignment(Qt.AlignmentFlag.AlignRight)
        sw_bg = CLR_GREY if _is_bailout or not _user_edited[0] else CLR_INPUT
        sw_le.setStyleSheet(f"background:{sw_bg};")
        hl.addWidget(sw_le)

        # PO2 label
        po2_lbl = QLabel("")
        po2_lbl.setFixedWidth(50)
        po2_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(po2_lbl)

        # Drop checkbox
        drop_cb = QCheckBox()
        drop_cb.setFixedWidth(40)
        drop_cb.setChecked(bool(drop))
        hl.addWidget(drop_cb)

        # ICD warning label
        icd_lbl = QLabel("—")
        icd_lbl.setFixedWidth(60)
        icd_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(icd_lbl)
        hl.addStretch()

        row = {
            "widget":    row_w,
            "stage_cb":  stage_cb,
            "name_lbl":  name_lbl,
            "pres_lbl":  pres_lbl,
            "mix_lbl":   mix_lbl,
            "gasl_lbl":  gasl_lbl,
            "sw_le":     sw_le,
            "po2_lbl":   po2_lbl,
            "drop_cb":   drop_cb,
            "icd_lbl":   icd_lbl,
            "o2":        0,
            "he":        0,
            "active":    (stage_sel != "—"),
            "_is_bailout": _is_bailout,
            "_user_edited": _user_edited,
        }

        def _on_sw_edit(row=row):
            if row["_is_bailout"]:
                return
            row["_user_edited"][0] = True
            row["sw_le"].setStyleSheet(f"background:{CLR_INPUT};")
            self._on_input_change()

        def _on_sw_focusout(row=row):
            if row["_is_bailout"]:
                return
            if not row["sw_le"].text().strip():
                row["_user_edited"][0] = False
                row["sw_le"].setStyleSheet(f"background:{CLR_GREY};")
                self._auto_sw_depth(row)

        sw_le.textChanged.connect(lambda _: self._on_sw_changed(row))
        sw_le.editingFinished.connect(lambda row=row: _on_sw_focusout(row))

        def _on_stage_change(_, row=row):
            if not self._loading:
                row["_user_edited"][0] = False
                self._on_gas_row_stage_change(row)
                self._on_input_change()

        stage_cb.currentTextChanged.connect(_on_stage_change)
        drop_cb.stateChanged.connect(lambda _: self._on_input_change())

        self._gas_gl.addWidget(row_w)
        self._gas_rows.append(row)
        self._on_gas_row_stage_change(row)

    def _on_sw_changed(self, row):
        """Update PO2 label when switch depth changes."""
        try:
            d  = float(row["sw_le"].text())
            o2 = row.get("o2", 0)
            if o2 > 0:
                po2 = (d / 10.0 + 1.0) * (o2 / 100.0)
                row["po2_lbl"].setText(f"{po2:.2f}")
            else:
                row["po2_lbl"].setText("")
        except (ValueError, TypeError):
            row["po2_lbl"].setText("")
        if not self._loading:
            self._on_input_change()

    def _auto_sw_depth(self, row):
        """Auto-compute switch depth from deco PO2 and O2 fraction."""
        if row["_is_bailout"] or row["_user_edited"][0]:
            return
        o2 = row.get("o2", 0)
        if o2 <= 0:
            return
        try:
            po2 = _flt(self._deko_sw_le.text(), 1.6)
            sw  = int(round((po2 / (o2 / 100.0) - 1) * 10))
            row["sw_le"].blockSignals(True)
            row["sw_le"].setText(str(sw))
            row["sw_le"].setStyleSheet(f"background:{CLR_GREY};")
            row["sw_le"].blockSignals(False)
            self._on_sw_changed(row)
        except (ValueError, ZeroDivisionError):
            pass

    def _on_gas_row_stage_change(self, row):
        """Update displayed gas data when stage source changes."""
        sel = row["stage_cb"].currentText()
        gas = self._get_gas_for_sel(sel)
        if gas:
            row["name_lbl"].setText(gas["name"])
            row["pres_lbl"].setText(f"{gas['pressure']:.0f}" if gas.get("pressure") else "")
            row["mix_lbl"].setText(_gas_label(gas["o2"], gas["he"]))
            row["gasl_lbl"].setText(f"{gas['real_gas_L']:.0f}")
            row["o2"] = gas["o2"]
            row["he"] = gas["he"]
            row["active"] = True
            self._auto_sw_depth(row)
        else:
            row["name_lbl"].setText("")
            row["pres_lbl"].setText("")
            row["mix_lbl"].setText("")
            row["gasl_lbl"].setText("")
            row["o2"] = 0
            row["he"] = 0
            row["active"] = (sel != "—")
            if sel == "—":
                row["sw_le"].blockSignals(True)
                row["sw_le"].setText("")
                row["sw_le"].blockSignals(False)
        # Update PO2 label
        self._on_sw_changed(row)

    def _get_gas_for_sel(self, sel: str):
        """Return {name, o2, he, real_gas_L, pressure} or None for a stage/cyl selection."""
        if not sel or sel == "—":
            return None
        plan_name = self._stage_plan_cb.currentText()
        user      = self._bp_tab._current_user()
        T_c       = TEMP_C_DEFAULT

        if sel.startswith("Stage"):
            try:
                idx = int(sel.split()[-1]) - 1
            except (ValueError, IndexError):
                return None
            if user not in self._db.get("users", {}) or plan_name == "—":
                return None
            plan_state = (self._db["users"][user]
                          .get("buoyancy_plans", {}).get(plan_name))
            if not plan_state:
                return None
            sslots = plan_state.get("sslots", [])
            if idx >= len(sslots):
                return None
            s    = sslots[idx]
            name = s.get("sel", "—")
            if not name or name == "—":
                return None
            cyl = _find_cyl(self._db, name)
            if cyl is None:
                return None
            try:
                # Stage slots only store "sel"; fall back to cylinder defaults
                o2   = _int_val(s.get("o2",  None) or cyl.get("o2",  "21"))
                he   = _int_val(s.get("he",  None) or cyl.get("he",  "0"))
                pres = _flt(s.get("pressure", None) or cyl.get("pressure", "0"))
                rg   = 0.0
                if pres > 0:
                    Z  = z_mix(pres + 1.0, T_c + 273.15, o2, he)
                    rg = float(cyl.get("volume_bottle", 0)) * (pres + 1.0) / Z
            except Exception:
                return None
            return {"name": name, "o2": o2, "he": he,
                    "real_gas_L": rg, "pressure": pres}

        if sel.startswith("Cylinder"):
            try:
                idx = int(sel.split()[-1]) - 1
            except (ValueError, IndexError):
                return None
            if user not in self._db.get("users", {}) or plan_name == "—":
                return None
            plan_state = (self._db["users"][user]
                          .get("buoyancy_plans", {}).get(plan_name))
            if not plan_state:
                return None
            jslots = plan_state.get("jslots", [])
            if idx >= len(jslots):
                return None
            s    = jslots[idx]
            name = s.get("sel", "—")
            if not name or name == "—":
                return None
            cyl = _find_cyl(self._db, name)
            if cyl is None:
                return None
            try:
                o2   = _int_val(s.get("o2",  "0"))
                he   = _int_val(s.get("he",  "0"))
                pres = _flt(s.get("pressure", "0"))
                rg   = 0.0
                if pres > 0:
                    Z  = z_mix(pres + 1.0, T_c + 273.15, o2, he)
                    rg = float(cyl.get("volume_bottle", 0)) * (pres + 1.0) / Z
            except Exception:
                return None
            return {"name": name, "o2": o2, "he": he,
                    "real_gas_L": rg, "pressure": pres}
        return None

    def _rebuild_gas_rows(self, stages: list):
        """Rebuild 4 bailout gas rows, preserving existing sw/drop/role/stage_sel."""
        prev = self._gas_rows[:]
        prev_sw      = [r["sw_le"].text()          for r in prev]
        prev_sw_man  = [r["_user_edited"][0]        for r in prev]
        prev_drop    = [r["drop_cb"].isChecked()    for r in prev]
        prev_role    = [r.get("_role", f"Gas {i+1}") for i, r in enumerate(prev)]
        prev_stage   = [r["stage_cb"].currentText() for r in prev]

        # Remove old widgets
        for r in self._gas_rows:
            r["widget"].deleteLater()
        self._gas_rows.clear()

        _default_roles = {0: "Bailout gas", 1: "Interstage gas",
                          2: "Deco gas 1",  3: "Deco gas 2"}
        max_d = self._max_depth()
        default_sw = [str(int(max_d)) if max_d > 0 else "", "21", "6", "3"]

        for i in range(4):
            sw       = prev_sw[i]     if i < len(prev_sw)    else default_sw[i]
            sw_man   = prev_sw_man[i] if i < len(prev_sw_man)else None
            drop     = prev_drop[i]   if i < len(prev_drop)  else False
            role     = (prev_role[i]  if i < len(prev_role)
                        else _default_roles.get(i, ""))
            sel      = prev_stage[i]  if i < len(prev_stage) else f"Stage {i + 1}"
            if i == 0:
                sw = str(int(max_d)) if max_d > 0 else ""
            self._add_gas_row(i, None, sw=sw, drop=drop,
                              role=role, stage_sel=sel, sw_manual=sw_man)

        if self._gas_rows:
            self._gas_rows[0]["sw_le"].setStyleSheet(f"background:{CLR_GREY};")

    # ── CCR Dive Plan result panel ────────────────────────────────────────────

    def _build_ccr_plan(self, grid_layout, row, col):
        box = QGroupBox("Dive plan  (CCR)")
        vl  = QVBoxLayout(box)
        vl.setContentsMargins(6, 8, 6, 6)
        vl.setSpacing(2)

        self._ccr_summary_lbl = QLabel("—")
        self._ccr_summary_lbl.setStyleSheet("font-weight:bold;")
        vl.addWidget(self._ccr_summary_lbl)

        self._ccr_table_area = self._build_result_table(
            vl,
            base_cols=[("Depth / Label", 130, "w"), ("Stop", 50, "e"),
                       ("Runtime", 62, "e"), ("SP", 60, "w"),
                       ("Gas", 78, "w")],
            extra_cols=[("Dil[bar]", 55, "e"), ("Dil[L]", 45, "e"),
                        ("O2[bar]",  55, "e"), ("O2[L]",  45, "e"),
                        ("Infl[bar]", 60, "e"), ("Infl[L]", 45, "e"),
                        ("BCD[bar]", 60, "e"), ("BCD[L]", 45, "e"),
                        ("Buoyancy[kg]", 95, "e")],
        )

        grid_layout.addWidget(box, row, col, 1, 3)   # spans cols 0-2

    # ── Bailout Plan result panel ─────────────────────────────────────────────

    def _build_bail_plan(self, grid_layout, row, col):
        box = QGroupBox("Bailout plan  (OC)")
        vl  = QVBoxLayout(box)
        vl.setContentsMargins(6, 8, 6, 6)
        vl.setSpacing(2)

        self._bail_summary_lbl = QLabel("—")
        self._bail_summary_lbl.setStyleSheet("font-weight:bold;")
        vl.addWidget(self._bail_summary_lbl)

        self._bail_table_area = self._build_result_table(
            vl,
            base_cols=[("Depth / Label", 130, "w"), ("Stop", 50, "e"),
                       ("Runtime", 62, "e"), ("Gas", 95, "w")],
            extra_cols=[("PO2[bar]", 65, "e"), ("Press[bar]", 75, "e"),
                        ("Used[L]", 70, "e"), ("Remain[L]", 80, "e"),
                        ("Buoyancy[kg]", 95, "e"), ("m/drop[kg]", 85, "e")],
        )

        grid_layout.addWidget(box, row, col, 1, 2)   # spans cols 3-4

    def _build_result_table(self, parent_vl, base_cols, extra_cols):
        """Build a scrollable result table. Returns the inner content widget."""
        all_cols = base_cols + extra_cols
        # Header
        hdr_w = QWidget()
        hdr_w.setStyleSheet(f"background:{CLR_HDR};")
        hdr_l = QHBoxLayout(hdr_w)
        hdr_l.setContentsMargins(2, 2, 2, 2)
        hdr_l.setSpacing(6)
        for txt, w, a in all_cols:
            lbl = QLabel(txt)
            lbl.setFixedWidth(w)
            lbl.setStyleSheet("font-weight:bold; border-right: 1px solid #aaaaaa;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hdr_l.addWidget(lbl)
        hdr_l.addStretch()
        parent_vl.addWidget(hdr_w)

        # Scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(180)

        inner = QWidget()
        inner._cols = all_cols
        inner._vl   = QVBoxLayout(inner)
        inner._vl.setContentsMargins(0, 0, 0, 0)
        inner._vl.setSpacing(1)
        inner._vl.addStretch()

        scroll.setWidget(inner)
        parent_vl.addWidget(scroll, 1)
        return inner

    # ── Plan change callbacks ─────────────────────────────────────────────────

    def _on_stage_plan_change(self, plan_name: str):
        if self._loading:
            return
        self._update_dil_from_plan(plan_name)
        self._rebuild_gas_rows([])
        self._update_cyl_dropdowns(plan_name)
        self._schedule_recalc()

    def _on_dil_cyl_change(self, _):
        if self._loading:
            return
        self._update_dil_from_plan(self._stage_plan_cb.currentText())
        self._on_input_change()

    def _update_dil_from_plan(self, plan_name: str):
        """Read O2/He from the selected diluent cylinder slot."""
        user = self._bp_tab._current_user()
        plan_state = None
        if plan_name and plan_name != "—" and user in self._db.get("users", {}):
            plan_state = (self._db["users"][user]
                          .get("buoyancy_plans", {}).get(plan_name))
        if not plan_state:
            self._dil_o2 = "—"
            self._dil_he = "—"
            return
        jslots = plan_state.get("jslots", [])
        cb_txt = self._onboard_cbs[0].currentText()
        try:
            idx = int(cb_txt.split()[-1]) - 1
        except (ValueError, IndexError):
            idx = 0
        if 0 <= idx < len(jslots):
            self._dil_o2 = jslots[idx].get("o2", "—") or "—"
            self._dil_he = jslots[idx].get("he", "—") or "—"
        else:
            self._dil_o2 = "—"
            self._dil_he = "—"

    def _update_onboard_info(self, plan_state_bp):
        """Fill Name/Press/Mix labels in the Onboard Gas tab from the buoyancy plan."""
        for i, (name_lbl, pres_lbl, mix_lbl) in enumerate(self._onboard_info):
            cb_txt = self._onboard_cbs[i].currentText()
            name_lbl.setText("—"); pres_lbl.setText("—"); mix_lbl.setText("—")
            if not plan_state_bp:
                continue
            try:
                idx    = int(cb_txt.split()[-1]) - 1
                jslots = plan_state_bp.get("jslots", [])
                if idx < 0 or idx >= len(jslots):
                    continue
                s   = jslots[idx]
                cyl = _find_cyl(self._db, s.get("sel", ""))
                if cyl is None:
                    continue
                name_lbl.setText(cyl.get("name", "—"))
                pres_lbl.setText(str(s.get("pressure", "—")))
                o2i = _int_val(s.get("o2", "21"))
                hei = _int_val(s.get("he", "0"))
                mix_lbl.setText(_gas_label(o2i, hei))
            except (ValueError, IndexError):
                pass

    def _update_cyl_dropdowns(self, plan_name: str):
        """Rebuild cylinder dropdowns to show only active slots in the plan."""
        user  = self._bp_tab._current_user()
        plan_state = None
        if plan_name and plan_name != "—" and user in self._db.get("users", {}):
            plan_state = (self._db["users"][user]
                          .get("buoyancy_plans", {}).get(plan_name))
        cyl_opts = []
        if plan_state:
            for i, s in enumerate(plan_state.get("jslots", []), 1):
                if s.get("sel", "—") not in ("—", ""):
                    cyl_opts.append(f"Cylinder {i}")
        if not cyl_opts:
            cyl_opts = ["Cylinder 1", "Cylinder 2", "Cylinder 3", "Cylinder 4"]

        for cb in self._onboard_cbs:
            cur = cb.currentText()
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(cyl_opts)
            if cur in cyl_opts:
                cb.setCurrentText(cur)
            cb.blockSignals(False)

    # ── Profile management ────────────────────────────────────────────────────

    def load_deco_profiles(self, profiles: dict):
        self._deco_profiles = profiles or {}
        self._refresh_stage_plan_cb()   # must run before _refresh_profile_list triggers _load_state
        self._refresh_profile_list()
        # After loading profiles, ensure the plan matches the buoyancy planner's active plan
        # (a loaded profile may have had "—" saved as its plan)
        if self._stage_plan_cb.currentText() == "—":
            bp_plan = self._bp_tab._current_plan()
            if bp_plan and bp_plan != "—":
                self.sync_plan(bp_plan)
        # Always restore global settings (profiles must not override them)
        self._load_global_settings()

    def _refresh_stage_plan_cb(self):
        user  = self._bp_tab._current_user()
        plans = {}
        if user in self._db.get("users", {}):
            plans = self._db["users"][user].get("buoyancy_plans", {})
        cur = self._stage_plan_cb.currentText()
        self._stage_plan_cb.blockSignals(True)
        self._stage_plan_cb.clear()
        self._stage_plan_cb.addItem("—")
        for name in sorted(plans.keys()):
            self._stage_plan_cb.addItem(name)
        # Auto-select the buoyancy planner's active plan if no plan was previously selected
        bp_plan = self._bp_tab._current_plan()
        if cur == "—" and bp_plan and bp_plan in plans:
            self._stage_plan_cb.setCurrentText(bp_plan)
        elif cur in plans:
            self._stage_plan_cb.setCurrentText(cur)
        self._stage_plan_cb.blockSignals(False)

    def _refresh_profile_list(self):
        cur = self._selected_profile
        self._profile_list.blockSignals(True)
        self._profile_list.clear()
        for name in sorted(self._deco_profiles.keys()):
            state = self._deco_profiles[name]
            segs  = [s for s in state.get("segments", [])
                     if _flt(s.get("depth", "0")) > 0]
            label = f"{name}  [MULTI]" if len(segs) > 1 else name
            self._profile_list.addItem(label)
        self._profile_list.blockSignals(False)
        # Re-select
        names = sorted(self._deco_profiles.keys())
        select = cur if cur in names else (names[0] if names else None)
        if select:
            idx = names.index(select)
            self._profile_list.setCurrentRow(idx)
            if select != cur:
                self._selected_profile = select
                state = self._deco_profiles.get(select)
                if state:
                    self._load_state(state)
        self._profile_lbl.setText(self._selected_profile or "No profile selected")

    def _on_profile_select(self, row: int):
        if row < 0:
            return
        items = self._profile_list.findItems("*", Qt.MatchFlag.MatchWildcard)
        names = sorted(self._deco_profiles.keys())
        if row >= len(names):
            return
        name  = names[row]
        self._selected_profile = name
        self._profile_lbl.setText(name)
        state = self._deco_profiles.get(name)
        if state:
            self._load_state(state)

    def _auto_profile_name(self) -> str:
        # Use the deepest segment as the representative depth/time
        best_depth, best_time = "", ""
        max_d = -1.0
        for row in self._seg_rows:
            try:
                d = float(row["depth_le"].text().strip())
                t = row["time_le"].text().strip()
                if d > max_d:
                    max_d = d
                    best_depth = row["depth_le"].text().strip()
                    best_time  = t
            except ValueError:
                pass
        o2 = self._dil_o2.strip()
        he = self._dil_he.strip()
        seg = f"{best_depth}m {best_time}min" if best_depth and best_time else "?m ?min"
        mix = f"{o2}/{he}"
        gflo = self._gf_lo_le.text().strip()
        gfhi = self._gf_hi_le.text().strip()
        gf   = f" GF{gflo}/{gfhi}" if gflo and gfhi else ""
        return f"{seg} {mix}{gf}"

    def _save_profile(self):
        user = self._bp_tab._current_user()
        if user == "—":
            return
        new_name = self._auto_profile_name()
        old_name = self._selected_profile
        # Rename old entry if name changed
        if old_name and old_name in self._deco_profiles and old_name != new_name:
            self._deco_profiles.pop(old_name)
        self._selected_profile = new_name
        self._profile_lbl.setText(new_name)
        self._deco_profiles[new_name] = self._get_state()
        self._db["users"].setdefault(user, {})["deco_profiles"] = dict(self._deco_profiles)
        from main_qt import save_db
        save_db(self._db)
        self._refresh_profile_list()
        self._schedule_recalc()

    def _save_profile_as_new(self):
        user = self._bp_tab._current_user()
        if user == "—":
            return
        name = self._auto_profile_name()
        base, counter = name, 2
        while name in self._deco_profiles:
            name = f"{base} ({counter})"
            counter += 1
        self._selected_profile = name
        self._profile_lbl.setText(name)
        self._deco_profiles[name] = self._get_state()
        self._db["users"].setdefault(user, {})["deco_profiles"] = dict(self._deco_profiles)
        from main_qt import save_db
        save_db(self._db)
        self._refresh_profile_list()
        self._schedule_recalc()

    def _delete_profile(self):
        user = self._bp_tab._current_user()
        if user == "—" or not self._selected_profile:
            return
        self._deco_profiles.pop(self._selected_profile, None)
        self._selected_profile = ""
        self._profile_lbl.setText("No profile selected")
        self._db["users"].setdefault(user, {})["deco_profiles"] = dict(self._deco_profiles)
        from main_qt import save_db
        save_db(self._db)
        self._refresh_profile_list()

    def _get_state(self) -> dict:
        return {
            "setpoint":   self._sp_le.text(),
            "sp_descend": self._sp_desc_le.text(),
            "sp_deko":    self._sp_deco_le.text(),
            "gf_lo":      self._gf_lo_le.text(),
            "gf_hi":      self._gf_hi_le.text(),
            "desc_r":     self._desc_r_le.text(),
            "asc_r":      self._asc_r_le.text(),
            "deco_r":     self._deco_r_le.text(),
            "sac":        self._sac_le.text(),
            "deko_sw_po2":self._deko_sw_le.text(),
            "stop_int":     self._stop_int_le.text(),
            "display_int":  self._display_int_le.text(),
            "segments": [{"depth": r["depth_le"].text(),
                          "time":  r["time_le"].text()}
                         for r in self._seg_rows],
            "stage_plan":      self._stage_plan_cb.currentText(),
            "dil_cyl_idx":     self._onboard_cbs[0].currentText(),
            "oxy_cyl_idx":     self._onboard_cbs[1].currentText(),
            "inflate_cyl_idx": self._onboard_cbs[2].currentText(),
            "bcd_cyl_idx":     self._onboard_cbs[3].currentText(),
            "oc_gases": [
                {"sw": r["sw_le"].text(),
                 "sw_manual": r["_user_edited"][0],
                 "drop": r["drop_cb"].isChecked(),
                 "stage_sel": r["stage_cb"].currentText()}
                for r in self._gas_rows
            ],
            "result_summary": self._ccr_summary_lbl.text(),
            "result_stops":   self._saved_stops,
        }

    def _load_state(self, s: dict):
        self._loading = True
        # Settings are global — not loaded from profiles

        # Segments
        for row in list(self._seg_rows):
            row["widget"].deleteLater()
        self._seg_rows.clear()
        for seg in s.get("segments", []):
            self._add_seg_row(depth=seg.get("depth", ""),
                              time=seg.get("time", ""))

        # Stage plan
        plan = s.get("stage_plan", "—")
        idx = self._stage_plan_cb.findText(plan)
        self._stage_plan_cb.blockSignals(True)
        self._stage_plan_cb.setCurrentIndex(max(0, idx))
        self._stage_plan_cb.blockSignals(False)

        # Onboard cylinders
        for i, key in enumerate(("dil_cyl_idx", "oxy_cyl_idx",
                                  "inflate_cyl_idx", "bcd_cyl_idx")):
            val = s.get(key, f"Cylinder {i+1}")
            idx_cb = self._onboard_cbs[i].findText(val)
            if idx_cb >= 0:
                self._onboard_cbs[i].setCurrentIndex(idx_cb)

        self._update_dil_from_plan(plan)

        # Gas rows
        saved_oc = s.get("oc_gases", [])
        for r in self._gas_rows:
            r["widget"].deleteLater()
        self._gas_rows.clear()
        _default_roles = {0: "Bailout gas", 1: "Interstage gas",
                          2: "Deco gas 1",  3: "Deco gas 2"}
        for i in range(4):
            oc   = saved_oc[i] if i < len(saved_oc) else {}
            sw   = oc.get("sw", "")
            sw_m = oc.get("sw_manual", None)
            drop = oc.get("drop", False)
            sel  = oc.get("stage_sel", f"Stage {i+1}")
            self._add_gas_row(i, None, sw=sw, drop=drop,
                              role=_default_roles.get(i, ""),
                              stage_sel=sel, sw_manual=sw_m)
        if self._gas_rows:
            self._gas_rows[0]["sw_le"].setStyleSheet(f"background:{CLR_GREY};")

        # Restored stops
        self._saved_stops = s.get("result_stops", [])
        self._ccr_summary_lbl.setText("—")
        self._render_stops(self._saved_stops, self._ccr_table_area,
                           extra_data=None, has_pre_gas=True)
        self._render_stops([], self._bail_table_area,
                           extra_data=None, has_pre_gas=False)

        self._loading = False
        self._schedule_recalc()

    # ── Input change / schedule ───────────────────────────────────────────────

    def refresh_from_saved_plan(self):
        """Called when Buoyancy Planner saves a plan — sync plan selection and recalc."""
        self.sync_plan(self._bp_tab._current_plan())

    def sync_plan(self, plan_name: str):
        """Sync the dive planner to the given buoyancy plan name."""
        if not plan_name or plan_name == "—":
            return
        idx = self._stage_plan_cb.findText(plan_name)
        if idx < 0:
            # Plan not in list yet — refresh list first
            self._refresh_stage_plan_cb()
            idx = self._stage_plan_cb.findText(plan_name)
        if idx >= 0 and self._stage_plan_cb.currentText() != plan_name:
            self._stage_plan_cb.setCurrentIndex(idx)  # triggers _on_stage_plan_change
        else:
            # Plan already selected — just refresh gas values
            self._update_dil_from_plan(plan_name)
            self._update_cyl_dropdowns(plan_name)
            for row in self._gas_rows:
                self._on_gas_row_stage_change(row)
            self._on_input_change()

    def _on_input_change(self, *_):
        if self._loading:
            return
        self._schedule_recalc()

    def _schedule_recalc(self):
        self._calc_timer.start(700)

    def _autosave_current_profile(self):
        if not self._selected_profile:
            return
        user = self._bp_tab._current_user()
        if user == "—":
            return
        new_name = self._auto_profile_name()
        if new_name != self._selected_profile and self._selected_profile in self._deco_profiles:
            self._deco_profiles[new_name] = self._deco_profiles.pop(self._selected_profile)
            self._selected_profile = new_name
            self._profile_lbl.setText(new_name)
            self._refresh_profile_list()
        self._deco_profiles[self._selected_profile] = self._get_state()
        self._db["users"].setdefault(user, {})["deco_profiles"] = dict(self._deco_profiles)
        from main_qt import save_db
        save_db(self._db)

    def _max_depth(self) -> float:
        max_d = 0.0
        for row in self._seg_rows:
            try:
                d = float(row["depth_le"].text())
                if d > max_d:
                    max_d = d
            except ValueError:
                pass
        return max_d

    # ── Tissue saturation helpers ─────────────────────────────────────────────

    @staticmethod
    def _snap_mv(snap, depth, surface=True):
        """Convert raw snapshot [(p_n2, p_he)] → [(p_total, m_value)]."""
        p_amb = P_SURF if surface else (P_SURF + depth / 10.0)
        out = []
        for i, (p_n2, p_he) in enumerate(snap):
            _, N2_a, N2_b, _, He_a, He_b = TISSUES[i]
            pt = p_n2 + p_he
            if pt > 0:
                a = (N2_a * p_n2 + He_a * p_he) / pt
                b = (N2_b * p_n2 + He_b * p_he) / pt
            else:
                a, b = N2_a, N2_b
            out.append((pt, a + p_amb / b))
        return out

    # ── Embedded chart updates ────────────────────────────────────────────────

    def _update_charts(self, segments=None):
        """Redraw all three embedded matplotlib charts."""
        self._draw_profile_chart(segments)
        self._draw_tissue_chart()
        self._draw_gas_chart()

    def _draw_profile_chart(self, segments=None):
        if self._profile_canvas is None:
            return
        fig = self._profile_canvas.figure
        fig.clear()
        ax = fig.add_subplot(111)
        ax.set_facecolor("#e8f0f8")

        BG_GRID = "#c8d8e8"
        ccr_tl  = self._tissue_timeline
        bail_tl = self._bail_tissue_timeline

        def _profile_from_tl(tl):
            if not tl:
                return [], []
            ts = [p[0] for p in tl]
            ds = [p[1] for p in tl]
            return ts, ds

        t_ccr, d_ccr = _profile_from_tl(ccr_tl)
        t_bail, d_bail = _profile_from_tl(bail_tl)

        if not t_ccr and not segments:
            ax.text(0.5, 0.5, "No dive data", ha="center", va="center",
                    transform=ax.transAxes, color="#888888", fontsize=9)
            ax.set_axis_off()
            self._profile_canvas.draw()
            return

        # Build profile from segments if no timeline yet
        if not t_ccr and segments:
            rate = 20.0
            t_ccr, d_ccr = [0.0], [0.0]
            cur_t, cur_d = 0.0, 0.0
            for dep, dur in segments:
                t_desc = abs(dep - cur_d) / rate
                cur_t += t_desc; cur_d = dep
                t_ccr.append(cur_t); d_ccr.append(cur_d)
                cur_t += dur
                t_ccr.append(cur_t); d_ccr.append(cur_d)
            t_ccr.append(cur_t + cur_d / 9.0); d_ccr.append(0.0)

        max_t = max((t_ccr[-1] if t_ccr else 0),
                    (t_bail[-1] if t_bail else 0), 1.0)
        max_d = max((max(d_ccr) if d_ccr else 0),
                    (max(d_bail) if d_bail else 0), 1.0)

        # Fill + CCR line
        if t_ccr:
            ax.fill_between(t_ccr, d_ccr, alpha=0.18, color="#3a80cc")
            ax.plot(t_ccr, d_ccr, color="#2278cc", linewidth=1.8, label="CCR")

        # Bailout line
        if t_bail:
            ax.plot(t_bail, d_bail, color="#e07a20", linewidth=1.4,
                    linestyle="--", alpha=0.85, label="Bailout OC")

        # Deco stop markers from saved stops
        for s in self._saved_stops:
            dep = s.get("depth", 0)
            rt  = s.get("runtime", 0)
            t_s = rt - s.get("time", 0)
            if dep > 0 and s.get("time", 0) > 0:
                ax.hlines(dep, t_s, rt, colors="#ff8c00", linewidth=3, alpha=0.7, zorder=4)

        ax.invert_yaxis()
        ax.set_xlabel("Tid [min]", fontsize=7, color="#334455")
        ax.set_ylabel("Dybde [m]", fontsize=7, color="#334455")
        ax.set_xlim(0, max_t * 1.03)
        ax.set_ylim(max_d * 1.08, -max_d * 0.05)
        ax.tick_params(labelsize=7, colors="#334455")
        ax.grid(True, color=BG_GRID, linewidth=0.5, linestyle=":")
        ax.set_title("Dykkeprofil", fontsize=8, color="#223344", pad=3)
        for sp in ax.spines.values():
            sp.set_edgecolor("#aabbcc")
        if t_bail:
            ax.legend(fontsize=6.5, loc="lower right",
                      facecolor="#e8f0f8", edgecolor="#aabbcc")
        self._profile_canvas.draw()

    def _draw_tissue_chart(self):
        if self._tissue_canvas is None:
            return
        fig = self._tissue_canvas.figure
        fig.clear()
        ax = fig.add_subplot(111)
        ax.set_facecolor("#12122a")

        tl = self._tissue_timeline
        if not tl or len(tl) < 2:
            ax.text(0.5, 0.5, "No tissue data", ha="center", va="center",
                    transform=ax.transAxes, color="#667799", fontsize=8)
            ax.set_axis_off()
            self._tissue_canvas.draw()
            return

        import matplotlib.cm as _cm
        cmap = _cm.get_cmap("coolwarm", 16) if hasattr(_cm, "get_cmap") else \
               _cm.colormaps["coolwarm"].resampled(16)

        times = _np.array([p[0] for p in tl])
        depths = _np.array([p[1] for p in tl])

        # Ambient inspirert inert gass-trykk på CCR:
        # P_inert_inspired = P_amb - SP - PH2O  (P_amb = depth/10 + 1.0)
        sp = self._get_setting("_sp", 1.3)
        p_amb = depths / 10.0 + 1.0
        p_inert_inspired = _np.maximum(0.0, p_amb - sp - PH2O)

        # Tissue lines
        n_tis = len(tl[0][2]) if tl[0][2] else 0
        for ti in range(n_tis):
            sats = _np.array([p[2][ti][0] + p[2][ti][1]
                              if len(p[2][ti]) > 1 else p[2][ti][0]
                              for p in tl])
            col = cmap(ti / max(n_tis - 1, 1))
            alpha = 0.5 if ti not in (0, 3, 7, 11, 15) else 0.95
            lw = 0.8 if ti not in (0, 3, 7, 11, 15) else 1.6
            ax.plot(times, sats, color=col, linewidth=lw, alpha=alpha)

        # Ambient inert pressure line
        ax.plot(times, p_inert_inspired, color="#ffdd44", linewidth=1.4,
                linestyle="--", alpha=0.85, label="P inert ambient (CCR)", zorder=5)

        ax.axhline(0.79, color="#6688ff", linewidth=0.7, linestyle=":", alpha=0.5)

        ax.legend(fontsize=7, facecolor="#1a1a2e", edgecolor="#2a2a4a",
                  labelcolor="#ffdd44", loc="upper right")
        ax.set_xlabel("Tid [min]", fontsize=7, color="#99aacc")
        ax.set_ylabel("P inert [bar]", fontsize=7, color="#99aacc")
        ax.tick_params(labelsize=7, colors="#99aacc")
        ax.set_title("Vevsmettning  (CCR)", fontsize=8, color="#aabbdd", pad=3)
        ax.grid(True, color="#1e1e3a", linewidth=0.5, linestyle=":")
        for sp in ax.spines.values():
            sp.set_edgecolor("#2a2a4a")
        self._tissue_canvas.draw()

    def _draw_gas_chart(self):
        if self._gas_canvas is None:
            return
        fig = self._gas_canvas.figure
        fig.clear()
        ax = fig.add_subplot(111)
        ax.set_facecolor("#12122a")

        COLS = ["#4a90d9", "#e04040", "#50c060", "#f0a030"]
        _role_labels = ["Bailout gas", "Interstage gas", "Deco gas 1", "Deco gas 2"]

        # Pad/trim to always exactly 4 slots
        data = (self._bail_gas_chart_data + [{} ] * 4)[:4]
        xs   = list(range(4))
        bar_w = 0.6
        max_init = max((d.get("initial_L", 0) for d in data), default=1.0)
        if max_init == 0:
            max_init = 1.0

        for i, (d, col) in enumerate(zip(data, COLS)):
            init    = d.get("initial_L", 0.0)
            used    = d.get("used_L",    0.0)
            gas_lbl = d.get("label", "")
            role    = _role_labels[i]

            # Background bar (capacity) — always drawn
            ax.bar(i, max(init, max_init * 0.04), width=bar_w,
                   color="#22224a", edgecolor="#3a3a6a", linewidth=0.6, zorder=2)

            if init > 0:
                ax.bar(i, used, width=bar_w, color=col, alpha=0.85, zorder=3)
                pct = used / init * 100
                ax.text(i, used + max_init * 0.03, f"{pct:.0f}%",
                        ha="center", va="bottom", color=col,
                        fontsize=7, fontweight="bold")
            else:
                ax.text(i, max_init * 0.06, "—", ha="center", va="bottom",
                        color="#445566", fontsize=9)

            # Two-line x-label: role on top, gas name below
            tick_lbl = f"{role}\n{gas_lbl}" if gas_lbl else role
            ax.text(i, -max_init * 0.12, tick_lbl, ha="center", va="top",
                    color="#99aacc", fontsize=6.5, transform=ax.transData)

        ax.set_xticks([])
        ax.set_ylim(-max_init * 0.05, max_init * 1.18)
        ax.set_ylabel("L", fontsize=7, color="#99aacc")
        ax.tick_params(labelsize=7, colors="#99aacc")
        ax.set_title("Gassforbruk  (Bailout OC)", fontsize=8, color="#aabbdd", pad=3)
        ax.grid(True, axis="y", color="#1e1e3a", linewidth=0.5, linestyle=":")
        for sp in ax.spines.values():
            sp.set_edgecolor("#2a2a4a")
        self._gas_canvas.draw()

    def _open_tissue_window(self, surface_mv=True, bailout=False, tab=0):
        """Open a tissue heatmap popup window at the given tab index."""
        timeline = self._bail_tissue_timeline if bailout else self._tissue_timeline
        if not timeline:
            QMessageBox.information(self, "No Data",
                "Run a dive calculation first to see tissue saturation.")
            return
        label = ("Bailout" if bailout else "CCR") + " — " + \
                ("Surface M-value" if surface_mv else "Depth M-value")
        try:
            from tissue_heatmap import TissueHeatmapWindow
            stops      = self._bail_saved_stops if bailout else self._saved_stops
            phase_list = self._bail_phase_list if bailout else self._tissue_phase_list
            first_stop = (self._bail_first_stop_depth if bailout
                          else self._ccr_first_stop_depth)
            win = TissueHeatmapWindow(
                self, timeline, surface_mv=surface_mv, title=label,
                stops=stops, phase_list=phase_list,
                gf_low=self._last_gf_low, gf_high=self._last_gf_high,
                resimulate_fn=None if bailout else self.resimulate_with_gf,
                first_stop_depth=first_stop,
            )
            win.show()
            win._tabs.setCurrentIndex(tab)
        except ImportError:
            QMessageBox.information(self, "Info",
                "Tissue heatmap visualization not yet implemented.")
        except Exception as _e:
            import traceback
            QMessageBox.critical(self, "Tissue Heatmap Error",
                traceback.format_exc())

    def resimulate_with_gf(self, gf_low: float, gf_high: float):
        """Re-run CCR simulation with new GF values for the tissue window.
        Returns (timeline, stops_list, phase_list) or raises on error."""
        args = self._last_sim_args
        if not args:
            raise RuntimeError("No simulation data cached — run a dive first.")
        result = simulate_dive(**args, gf_low=gf_low, gf_high=gf_high)
        stops = [
            {"depth": s.depth, "time": s.time, "runtime": s.runtime,
             "gas": s.gas, "tissue": s.tissue_snapshot}
            for s in result.stops
        ]
        return result.tissue_timeline, stops, result.tissue_phase_list

    def _open_gf_comparison(self):
        """Open Fixed Bottom Time GF Comparison window."""
        segments = []
        for row in self._seg_rows:
            try:
                d = float(row["depth_le"].text())
                t = float(row["time_le"].text())
                if d > 0:
                    segments.append((d, t))
            except ValueError:
                pass
        if not segments:
            QMessageBox.information(self, "No Data",
                "Define at least one dive segment first.")
            return
        ccr = CCRConfig(
            setpoint   = self._get_setting("_sp",      1.3),
            diluent_o2 = _int_val(self._dil_o2, 21) / 100,
            diluent_he = _int_val(self._dil_he, 35) / 100,
            sp_descend = self._get_setting("_sp_desc", 0.7),
            sp_deco    = self._get_setting("_sp_deco", 1.6),
        )
        from gf_comparison import GFComparisonWindow
        win = GFComparisonWindow(
            self, segments=segments, ccr=ccr, oc_gases=[],
            gf_hi   = self._get_setting("_gf_hi",  80) / 100,
            desc_r  = self._get_setting("_desc_r", 20),
            asc_r   = self._get_setting("_asc_r",   9),
            deco_r  = self._get_setting("_deco_r",  3),
        )
        win.show()

    def _update_icd_warnings(self):
        ICD_LIMIT   = 0.5
        CLR_SAFE    = "#228822"
        CLR_WARN    = "#cc2222"
        CLR_NEUTRAL = "#888888"

        for i, row in enumerate(self._gas_rows):
            icd_lbl = row.get("icd_lbl")
            if icd_lbl is None:
                continue
            o2 = row.get("o2", 0)
            if i == 0 or o2 <= 0:
                icd_lbl.setText("—")
                icd_lbl.setStyleSheet(f"color:{CLR_NEUTRAL};")
                continue
            prev_row = next((self._gas_rows[j]
                             for j in range(i - 1, -1, -1)
                             if self._gas_rows[j].get("o2", 0) > 0), None)
            if prev_row is None:
                icd_lbl.setText("—")
                icd_lbl.setStyleSheet(f"color:{CLR_NEUTRAL};")
                continue
            try:
                o2_from = prev_row["o2"] / 100.0
                he_from = prev_row["he"] / 100.0
                fN2_from = max(0.0, 1.0 - o2_from - he_from)
                o2_to   = row["o2"] / 100.0
                he_to   = row["he"] / 100.0
                fN2_to  = max(0.0, 1.0 - o2_to - he_to)
                sw_depth = float(row["sw_le"].text() or "0")
                p_amb    = sw_depth / 10.0 + 1.0
                delta    = (fN2_to - fN2_from) * p_amb
                if delta > ICD_LIMIT:
                    icd_lbl.setText(f"⚠{delta:.2f}")
                    icd_lbl.setStyleSheet(f"color:{CLR_WARN};")
                elif delta > 0:
                    icd_lbl.setText(f"{delta:.2f}")
                    icd_lbl.setStyleSheet(f"color:{CLR_SAFE};")
                else:
                    icd_lbl.setText(f"{delta:.2f}")
                    icd_lbl.setStyleSheet(f"color:{CLR_NEUTRAL};")
            except (ValueError, TypeError):
                icd_lbl.setText("—")
                icd_lbl.setStyleSheet(f"color:{CLR_NEUTRAL};")

    # ── Recalculation ─────────────────────────────────────────────────────────

    def _recalc_and_run(self):
        max_d = self._max_depth()
        if self._gas_rows:
            self._loading = True
            self._gas_rows[0]["sw_le"].blockSignals(True)
            self._gas_rows[0]["sw_le"].setText(str(int(max_d)) if max_d > 0 else "")
            self._gas_rows[0]["sw_le"].blockSignals(False)
            self._gas_rows[0]["sw_le"].setStyleSheet(f"background:{CLR_GREY};")
            self._loading = False
        self._update_icd_warnings()
        self._run()

    def _run(self):
        # ── Collect segments ─────────────────────────────────────────────────
        segments = []
        for row in self._seg_rows:
            try:
                d = float(row["depth_le"].text())
                t = float(row["time_le"].text())
                if d > 0:
                    segments.append((d, t))
            except ValueError:
                pass
        if not segments:
            self._ccr_summary_lbl.setText("No segments defined.")
            self._render_stops([], self._ccr_table_area,  None, True)
            self._render_stops([], self._bail_table_area, None, False)
            return

        # ── CCR config ───────────────────────────────────────────────────────
        ccr = CCRConfig(
            setpoint   = self._get_setting("_sp",       1.3),
            diluent_o2 = _int_val(self._dil_o2, 21) / 100,
            diluent_he = _int_val(self._dil_he, 35) / 100,
            sp_descend = self._get_setting("_sp_desc", 0.7),
            sp_deco    = self._get_setting("_sp_deco", 1.6),
        )

        # ── OC gases and stage tracking ──────────────────────────────────────
        oc_gases       = []
        stage_tracking = []   # parallel list, entry per oc_gas
        T_c            = TEMP_C_DEFAULT
        plan_name      = self._stage_plan_cb.currentText()
        user_now       = self._bp_tab._current_user()
        plan_state_bp  = None
        if plan_name != "—" and user_now in self._db.get("users", {}):
            plan_state_bp = (self._db["users"][user_now]
                             .get("buoyancy_plans", {}).get(plan_name))

        # Active sslots
        active_sslots = []
        if plan_state_bp:
            for si, s in enumerate(plan_state_bp.get("sslots", [])):
                nm = s.get("sel", "—")
                if not nm or nm == "—":
                    continue
                cyl = _find_cyl(self._db, nm)
                if cyl is None:
                    continue
                active_sslots.append((si, s, cyl))

        for row in self._gas_rows:
            if not row.get("active", True):
                continue
            sel = row["stage_cb"].currentText()
            if sel == "—":
                continue
            o2 = row.get("o2", 0) / 100.0
            he = row.get("he", 0) / 100.0
            sw = _flt(row["sw_le"].text(), 999)
            if o2 <= 0:
                continue
            oc_gas = OCGas(o2=o2, he=he, switch_depth=sw)
            oc_gases.append(oc_gas)
            trk = None
            o2i = int(o2 * 100)
            hei = int(he * 100)
            if sel.startswith("Cylinder") and plan_state_bp:
                try:
                    jslot_idx = int(sel.split()[-1]) - 1
                    jslots    = plan_state_bp.get("jslots", [])
                    if jslot_idx < len(jslots):
                        s   = jslots[jslot_idx]
                        cyl = _find_cyl(self._db, s.get("sel", ""))
                        if cyl:
                            pres = _flt(s.get("pressure", "0"))
                            Z    = z_mix(pres + 1.0, T_c + 273.15, o2i, hei)
                            rg   = float(cyl.get("volume_bottle", 0)) * (pres + 1.0) / Z
                            trk  = {"label": oc_gas.label(),
                                    "sslot_idx": None, "jslot_idx": jslot_idx,
                                    "cyl": cyl, "o2i": o2i, "hei": hei,
                                    "real_gas_L": rg}
                except Exception:
                    pass
            elif sel.startswith("Stage") and plan_state_bp:
                try:
                    stage_idx = int(sel.split()[-1]) - 1
                    matching  = next((item for item in active_sslots
                                      if item[0] == stage_idx), None)
                    if matching:
                        si, s, cyl = matching
                        pres = _flt(s.get("pressure", "0"))
                        Z    = z_mix(pres + 1.0, T_c + 273.15, o2i, hei)
                        rg   = float(cyl.get("volume_bottle", 0)) * (pres + 1.0) / Z
                        trk  = {"label": oc_gas.label(),
                                "sslot_idx": si, "jslot_idx": None,
                                "cyl": cyl, "o2i": o2i, "hei": hei,
                                "real_gas_L": rg}
                except Exception:
                    pass
            stage_tracking.append(trk)

        if not oc_gases:
            oc_gases = [OCGas(o2=0.21, he=0.0, switch_depth=999)]

        gf_lo  = self._get_setting("_gf_lo", 30) / 100
        gf_hi  = self._get_setting("_gf_hi", 80) / 100
        desc_r = self._get_setting("_desc_r", 20)
        asc_r  = self._get_setting("_asc_r",  9)
        deco_r = self._get_setting("_deco_r",  3)

        # ── CCR simulation ───────────────────────────────────────────────────
        _snap_iv   = max(0.1, self._get_setting("_heatmap_int", 1.0))
        _stop_iv   = max(1.0, self._get_setting("_stop_int",    3.0))
        _last_stop = max(0.0, self._get_setting("_last_stop",   3.0))
        result = None
        try:
            result = simulate_dive(
                segments=segments, mode="ccr", ccr=ccr, oc_gases=oc_gases,
                gf_low=gf_lo, gf_high=gf_hi,
                desc_rate=desc_r, asc_rate=asc_r, deco_rate=deco_r,
                snap_interval=_snap_iv, stop_interval=_stop_iv,
                last_stop=_last_stop,
            )
            self._ccr_summary_lbl.setText(
                f"Bottom time: {result.bottom_time:.0f} min  |  "
                f"TTS: {result.tts:.0f} min  |  "
                f"Runtime: {result.runtime:.0f} min  |  "
                f"OTU: {result.otu:.0f}  |  CNS: {result.cns:.1f} %"
            )
            self._saved_stops = [
                {"depth": s.depth, "time": s.time, "runtime": s.runtime,
                 "gas": s.gas, "tissue": s.tissue_snapshot}
                for s in result.stops
            ]
            self._tissue_timeline     = result.tissue_timeline
            self._tissue_phase_list   = result.tissue_phase_list
            self._ccr_first_stop_depth = result.first_stop_depth
            self._last_gf_low       = gf_lo
            self._last_gf_high      = gf_hi
            # cache args for GF re-simulation from tissue window
            self._last_sim_args = dict(
                segments=segments, mode="ccr", ccr=ccr, oc_gases=oc_gases,
                desc_rate=desc_r, asc_rate=asc_r, deco_rate=deco_r,
                snap_interval=_snap_iv, stop_interval=_stop_iv,
                last_stop=_last_stop,
            )
        except Exception as e:
            self._ccr_summary_lbl.setText(f"Error: {e}")
            self._saved_stops       = []
            self._tissue_timeline   = []
            self._tissue_phase_list = []
            result = None

        # ── Onboard gas volumes ──────────────────────────────────────────────
        onboard_pres, onboard_vols = [], []
        for cb in self._onboard_cbs:
            label = cb.currentText()
            pres_v, vol_v = "—", "—"
            if plan_state_bp:
                try:
                    idx = int(label.split()[-1]) - 1
                    jslots = plan_state_bp.get("jslots", [])
                    if idx < len(jslots):
                        s    = jslots[idx]
                        cyl  = _find_cyl(self._db, s.get("sel", ""))
                        if cyl:
                            pres = _flt(s.get("pressure", "0"))
                            o2i  = _int_val(s.get("o2", "21"))
                            hei  = _int_val(s.get("he", "0"))
                            Z    = z_mix(pres + 1.0, T_c + 273.15, o2i, hei)
                            rg   = float(cyl.get("volume_bottle", 0)) * (pres + 1.0) / Z
                            pres_v = f"{pres:.0f}"
                            vol_v  = f"{rg:.0f}"
                except Exception:
                    pass
            onboard_pres.append(pres_v)
            onboard_vols.append(vol_v)

        try:
            ccr_buoy = float(self._bp_tab._sum_lbls["jj_diver_stages"].text())
            ccr_buoy_str = f"{ccr_buoy:+.2f}"
        except (ValueError, AttributeError, KeyError):
            ccr_buoy = None
            ccr_buoy_str = "—"

        self._update_onboard_info(plan_state_bp)

        dil_v, o2_v, infl_v, bcd_v = (onboard_vols + ["—", "—", "—", "—"])[:4]
        dil_p, o2_p, infl_p, bcd_p = (onboard_pres + ["—", "—", "—", "—"])[:4]
        dil_mix       = ccr.dil_label()
        sp_bottom_lbl = ccr.sp_label(sp=ccr.setpoint)
        sp_desc_lbl   = ccr.sp_label(sp=ccr.sp_descend)
        sp_deco_lbl   = ccr.sp_label(sp=ccr.sp_deco) if hasattr(ccr, "sp_deco") else sp_bottom_lbl

        def _onboard_extra(sp_label=""):
            return [(sp_label, 60, "w"),
                    (dil_p, 55, "e"), (dil_v, 45, "e"),
                    (o2_p,  55, "e"), (o2_v,  45, "e"),
                    (infl_p, 60, "e"), (infl_v, 45, "e"),
                    (bcd_p, 60, "e"), (bcd_v, 45, "e"),
                    (ccr_buoy_str, 95, "e")]

        # Build CCR display rows (pre-deco + deco stops)
        ccr_display, ccr_extra = [], []
        for s in self._saved_stops:
            sp_lbl = s["gas"].split(" dil")[0] if " dil" in s["gas"] else s["gas"]
            ccr_display.append({**s, "gas": dil_mix})
            ccr_extra.append(_onboard_extra(sp_label=sp_lbl))

        if ccr_display and segments:
            # Use the simulation's actual first_stop depth (the rounded ceiling at
            # GF_low) so the ascent transit time is correct even when early deco
            # stops have 0-minute wait time and don't appear in the display list.
            real_first_stop = self._ccr_first_stop_depth or ccr_display[0]["depth"]
            pre_rows, pre_extra = [], []
            cur_d, cur_rt = 0.0, 0.0

            pre_rows.append({"_label": "@ 0m", "depth": 0.0, "time": 0,
                              "runtime": 0, "gas": dil_mix})
            pre_extra.append(_onboard_extra(sp_label=""))

            for seg_depth, total_seg_time in segments:
                t_travel = 0.0
                if seg_depth != cur_d:
                    going_down = seg_depth > cur_d
                    rate       = desc_r if going_down else asc_r
                    t_travel   = abs(seg_depth - cur_d) / rate
                    sp_tr      = sp_desc_lbl if going_down else sp_bottom_lbl
                    label_tr   = f"{'↓' if going_down else '↑'}{cur_d:.0f}→{seg_depth:.0f}m"
                    cur_rt    += t_travel
                    pre_rows.append({"_label": label_tr, "depth": seg_depth,
                                     "time": t_travel, "runtime": cur_rt, "gas": dil_mix})
                    pre_extra.append(_onboard_extra(sp_label=sp_tr))
                    cur_d = seg_depth
                t_at = max(0.0, total_seg_time - t_travel)
                if t_at > 0:
                    cur_rt += t_at
                    pre_rows.append({"_label": f"{seg_depth:.0f}m", "depth": seg_depth,
                                     "time": t_at, "runtime": cur_rt, "gas": dil_mix,
                                     "_show_time": True})
                    pre_extra.append(_onboard_extra(sp_label=sp_bottom_lbl))

            if cur_d > real_first_stop:
                # Ascent from bottom to simulation's first_stop at ascent rate
                t_tr = (cur_d - real_first_stop) / asc_r
                cur_rt += t_tr
                pre_rows.append({"_label": f"↑{cur_d:.0f}→{real_first_stop:.0f}m",
                                  "depth": real_first_stop, "time": t_tr, "runtime": cur_rt,
                                  "gas": dil_mix})
                pre_extra.append(_onboard_extra(sp_label=sp_bottom_lbl))

                # If 0-min stops exist between first_stop and first stop with
                # actual wait time, show them as a deco-rate transit row so the
                # diver can see that deco does NOT start at the displayed first stop.
                first_wait_depth = ccr_display[0]["depth"] if ccr_display else real_first_stop
                if real_first_stop > first_wait_depth:
                    t_deco_tr = (real_first_stop - first_wait_depth) / deco_r
                    cur_rt += t_deco_tr
                    pre_rows.append({"_label": f"↑{real_first_stop:.0f}→{first_wait_depth:.0f}m (deco rate)",
                                      "depth": first_wait_depth, "time": t_deco_tr,
                                      "runtime": cur_rt, "gas": dil_mix})
                    pre_extra.append(_onboard_extra(sp_label=sp_deco_lbl))

            ccr_display = pre_rows + ccr_display
            ccr_extra   = pre_extra + ccr_extra

        # Append surface row
        if ccr_display and result:
            ccr_display.append({"_label": "↑ Surface", "depth": 0.0, "time": "",
                                "runtime": result.runtime, "gas": dil_mix})
            ccr_extra.append(_onboard_extra(sp_label=""))

        self._render_stops(ccr_display, self._ccr_table_area,
                           extra_data=ccr_extra if ccr_extra else None,
                           has_pre_gas=True)

        # ── Bailout simulation ───────────────────────────────────────────────
        bail_stops = []
        bail_total_runtime = None
        if oc_gases:
            try:
                bail = simulate_bailout_from_bottom(
                    segments=segments, ccr=ccr, oc_gases=oc_gases,
                    gf_low=gf_lo, gf_high=gf_hi,
                    desc_rate=desc_r, asc_rate=asc_r, deco_rate=deco_r,
                    snap_interval=max(0.1, self._get_setting("_heatmap_int", 1.0)),
                    stop_interval=max(1.0, self._get_setting("_stop_int", 3.0)),
                )
                self._bail_summary_lbl.setText(
                    f"Bailout at: {bail.bottom_time:.0f} min  |  "
                    f"TTS: {bail.tts:.0f} min  |  "
                    f"Runtime: {bail.runtime:.0f} min  |  "
                    f"OTU: {bail.otu:.0f}  |  CNS: {bail.cns:.1f} %"
                )
                bail_stops = [
                    {"depth": s.depth, "time": s.time, "runtime": s.runtime, "gas": s.gas}
                    for s in bail.stops
                ]
                self._bail_tissue_timeline  = bail.tissue_timeline
                self._bail_phase_list       = bail.tissue_phase_list
                self._bail_first_stop_depth = bail.first_stop_depth
                self._bail_saved_stops      = bail_stops
                bail_total_runtime          = bail.runtime
            except Exception as e:
                self._bail_summary_lbl.setText(f"Error: {e}")
                bail_stops                  = []
                self._bail_tissue_timeline  = []
                self._bail_phase_list       = []
                self._bail_first_stop_depth = 0.0
        else:
            self._bail_summary_lbl.setText("No bailout gases defined.")
            self._bail_tissue_timeline = []
            self._bail_phase_list      = []

        # ── Per-stop gas consumption + buoyancy simulation ───────────────────
        sac        = self._get_setting("_sac", 20.0)
        bail_extra = None

        if bail_stops and sac > 0 and stage_tracking and plan_state_bp:
            plan_base = copy.deepcopy(plan_state_bp)
            plan_drop = copy.deepcopy(plan_state_bp)

            # Which gas labels are marked "drop stage"
            drop_gases = {}
            for ri, row in enumerate(self._gas_rows):
                if (ri < len(stage_tracking) and stage_tracking[ri] is not None
                        and row["drop_cb"].isChecked()):
                    drop_gases[stage_tracking[ri]["label"]] = ri + 1

            has_drops = bool(drop_gases)
            _last = {}
            for _i, _s in enumerate(bail_stops):
                if _s["gas"] in drop_gases:
                    _last[_s["gas"]] = _i
            drop_at = {idx: lbl for lbl, idx in _last.items()}
            _transit_drop_gases = {lbl for lbl in drop_gases if lbl not in _last}

            def _trk(label):
                return next((t for t in stage_tracking if t and t["label"] == label), None)

            def _rgl(plan_copy, trk_entry, jslot=None):
                si = trk_entry.get("sslot_idx")
                ji = trk_entry.get("jslot_idx")
                is_j  = (si is None)
                sidx  = ji if is_j else si
                return _rgl_from_slot(plan_copy, sidx, trk_entry["cyl"],
                                      trk_entry["o2i"], trk_entry["hei"], is_j, T_c)

            def _consume(plan_copy, trk_entry, used_L):
                if trk_entry is None:
                    return None
                si = trk_entry.get("sslot_idx")
                ji = trk_entry.get("jslot_idx")
                if si is None and ji is None:
                    return None
                is_j = (si is None)
                sidx = ji if is_j else si
                slots = plan_copy["jslots"] if is_j else plan_copy["sslots"]
                cyl   = trk_entry["cyl"]
                o2i   = trk_entry["o2i"]
                hei   = trk_entry["hei"]
                rg    = _rgl_from_slot(plan_copy, sidx, cyl, o2i, hei, is_j, T_c)
                remaining = max(0.0, rg - used_L)
                new_pres  = _solve_pres(remaining, cyl, o2i, hei, T_c)
                slots[sidx]["pressure"] = f"{new_pres:.4f}"
                return _rgl_from_slot(plan_copy, sidx, cyl, o2i, hei, is_j, T_c)

            # bp_base = JJ+Diver+Stages from Buoyancy Planner (equipment/diver included)
            try:
                bp_base = float(self._bp_tab._sum_lbls["jj_diver_stages"].text())
            except (ValueError, AttributeError, KeyError):
                bp_base = None

            # Cyl-only buoyancy at full starting pressures (for computing deltas)
            _cyl_base_total = _compute_buoyancy_from_plan(self._db, plan_base, T_c)

            def _total(plan_copy):
                """Return absolute buoyancy = bp_base + delta_from_gas_consumption."""
                if bp_base is None:
                    return _compute_buoyancy_from_plan(self._db, plan_copy, T_c)
                cyl_now = _compute_buoyancy_from_plan(self._db, plan_copy, T_c)
                if cyl_now is None or _cyl_base_total is None:
                    return bp_base
                return bp_base + (cyl_now - _cyl_base_total)

            def _get_pres(plan_copy, trk_entry):
                if trk_entry is None:
                    return None
                si = trk_entry.get("sslot_idx")
                ji = trk_entry.get("jslot_idx")
                if si is not None:
                    return float(plan_copy["sslots"][si].get("pressure", "0") or "0")
                if ji is not None:
                    return float(plan_copy["jslots"][ji].get("pressure", "0") or "0")
                return None

            max_depth_seg = max((d for d, _ in segments), default=0.0)
            bail_display  = []
            bail_extra    = []

            # ── Prepend surface + descent rows to match CCR plan row count ───
            _empty_extra = [("—", 65, "e"), ("—", 75, "e"), ("—", 70, "e"),
                            ("—", 80, "e"), ("—", 95, "e"), ("—", 85, "e")]
            bail_display.append({"_label": "@ 0m", "depth": 0.0,
                                  "time": 0, "runtime": 0, "gas": ""})
            bail_extra.append(_empty_extra)
            _t_desc = max_depth_seg / desc_r if desc_r > 0 else 0.0
            bail_display.append({"_label": f"↓0→{max_depth_seg:.0f}m",
                                  "depth": max_depth_seg, "time": _t_desc,
                                  "runtime": _t_desc, "gas": ""})
            bail_extra.append(_empty_extra)

            # Snapshot row at bailout depth
            _bailout_gas = select_oc_gas(max_depth_seg, oc_gases) if oc_gases else None
            snap_gas_lbl = _bailout_gas.label() if _bailout_gas else (bail_stops[0]["gas"] if bail_stops else "")
            snap_buoy    = _total(plan_base)
            snap_te      = _trk(snap_gas_lbl)
            snap_pres    = _get_pres(plan_base, snap_te)
            snap_rgl     = _rgl(plan_base, snap_te) if snap_te else None
            _snap_o2i    = snap_te.get("o2i", 0) if snap_te else 0
            _snap_po2    = (max_depth_seg / 10.0 + 1.0) * (_snap_o2i / 100.0)
            bail_display.append({"_label": f"@ {max_depth_seg:.0f}m",
                                  "depth": max_depth_seg, "time": 0,
                                  "runtime": 0, "gas": snap_gas_lbl})
            bail_extra.append([(f"{_snap_po2:.2f}", 65, "e"),
                                (f"{snap_pres:.0f}" if snap_pres is not None else "—", 75, "e"),
                                ("0", 70, "e"),
                                (f"{snap_rgl:.0f}" if snap_rgl is not None else "—", 80, "e"),
                                (f"{snap_buoy:+.2f}" if snap_buoy is not None else "—", 95, "e"),
                                ("—", 85, "e")])

            # Transit from bottom to first stop — use simulation's actual first_stop
            # depth (may be deeper than first stop with wait time when early stops are 0 min)
            bail_real_first = (self._bail_first_stop_depth
                               if self._bail_first_stop_depth > 0 and bail_stops
                               else (bail_stops[0]["depth"] if bail_stops else 0.0))
            if bail_stops and max_depth_seg > bail_real_first:
                first_stop_d = bail_real_first
                waypoints = _oc_ascent_waypoints(max_depth_seg, first_stop_d, oc_gases)
                total_transit_time = (max_depth_seg - first_stop_d) / asc_r
                seg_runtime_offset = (bail_stops[0]["runtime"]
                                      - bail_stops[0]["time"] - total_transit_time)
                seg_top = max_depth_seg
                transit_gas_label = (select_oc_gas(max_depth_seg, oc_gases).label()
                                     if oc_gases else None)

                for seg_bot in waypoints:
                    if seg_bot >= seg_top:
                        continue
                    seg_time = (seg_top - seg_bot) / asc_r
                    seg_avg  = (seg_top + seg_bot) / 2.0
                    seg_L    = sac * seg_time * (seg_avg / 10.0 + 1.0)
                    seg_gas_obj   = select_oc_gas(seg_top, oc_gases)
                    seg_gas_label = seg_gas_obj.label()
                    te = _trk(seg_gas_label)
                    _consume(plan_drop, te, seg_L)
                    seg_rest       = _consume(plan_base, te, seg_L)
                    seg_total_base = _total(plan_base)
                    seg_total_drop = _total(plan_drop)
                    seg_runtime_offset += seg_time
                    seg_pres = _get_pres(plan_base, te)
                    _seg_o2i = te.get("o2i", 0) if te else 0
                    _seg_po2 = (seg_top / 10.0 + 1.0) * (_seg_o2i / 100.0)
                    bail_display.append({"_label": f"↑{seg_top:.0f}→{seg_bot:.0f}m",
                                          "depth": seg_top, "time": seg_time,
                                          "runtime": seg_runtime_offset,
                                          "gas": seg_gas_label})
                    drop_col = (f"{seg_total_drop:+.2f}"
                                if has_drops and seg_total_drop is not None else "—")
                    bail_extra.append([
                        (f"{_seg_po2:.2f}", 65, "e"),
                        (f"{seg_pres:.0f}" if seg_pres is not None else "—", 75, "e"),
                        (f"{seg_L:.0f}", 70, "e"),
                        (f"{seg_rest:.0f}" if seg_rest is not None else "—", 80, "e"),
                        (f"{seg_total_base:+.2f}" if seg_total_base is not None else "—", 95, "e"),
                        (drop_col, 85, "e"),
                    ])
                    seg_top = seg_bot

                # Drop stages whose gas was only used in transit
                for _tdg in list(_transit_drop_gases):
                    if _tdg != transit_gas_label:
                        continue
                    stage_num_td = drop_gases[_tdg]
                    te_td        = _trk(_tdg)
                    buoy_before  = _total(plan_drop)
                    if te_td and te_td.get("sslot_idx") is not None:
                        plan_drop["sslots"][te_td["sslot_idx"]]["sel"] = "—"
                    buoy_after = _total(plan_drop)
                    delta_td = ((buoy_after - buoy_before)
                                if buoy_before is not None and buoy_after is not None else None)
                    delta_str_td = (f"{'↑' if delta_td >= 0 else '↓'} {abs(delta_td):.2f} kg"
                                   if delta_td is not None else "—")
                    bail_display.append({"_label": f"  ↓ Stage {stage_num_td} dropped",
                                          "_drop_marker": True,
                                          "depth": 0, "time": 0,
                                          "runtime": seg_runtime_offset,
                                          "gas": delta_str_td})
                    bail_extra.append([
                        ("", 65, "e"), ("", 75, "e"), ("", 70, "e"), ("", 80, "e"),
                        ("", 95, "e"),
                        (f"{buoy_after:+.2f}" if buoy_after is not None else "—", 85, "e"),
                    ])

            # Deco stops
            for i, stop in enumerate(bail_stops):
                bar_abs = stop["depth"] / 10.0 + 1.0
                used_L  = sac * stop["time"] * bar_abs
                te      = _trk(stop["gas"])
                _consume(plan_drop, te, used_L)
                rest_L     = _consume(plan_base, te, used_L)
                pres_after = _get_pres(plan_base, te)
                # Buoyancy after consuming this stop's gas AND transit to next stop/surface
                import copy as _copy
                _plan_tmp = _copy.deepcopy(plan_base)
                if i + 1 < len(bail_stops):
                    _next_depth = bail_stops[i + 1]["depth"]
                    _next_gas   = bail_stops[i + 1]["gas"]
                else:
                    _next_depth = 0.0
                    _next_gas   = stop["gas"]
                _transit_time = max(0.0, (stop["depth"] - _next_depth) / asc_r)
                _transit_avg  = (stop["depth"] + _next_depth) / 2.0
                _transit_L    = sac * _transit_time * (_transit_avg / 10.0 + 1.0)
                _consume(_plan_tmp, _trk(_next_gas), _transit_L)
                total_base = _total(_plan_tmp)
                total_drop = _total(plan_drop)
                drop_col   = (f"{total_drop:+.2f}"
                              if has_drops and total_drop is not None else "—")
                _stop_o2i  = te.get("o2i", 0) if te else 0
                _stop_po2  = bar_abs * (_stop_o2i / 100.0)
                bail_display.append(stop)
                bail_extra.append([
                    (f"{_stop_po2:.2f}", 65, "e"),
                    (f"{pres_after:.0f}" if pres_after is not None else "—", 75, "e"),
                    (f"{used_L:.0f}", 70, "e"),
                    (f"{rest_L:.0f}" if rest_L is not None else "—", 80, "e"),
                    (f"{total_base:+.2f}" if total_base is not None else "—", 95, "e"),
                    (drop_col, 85, "e"),
                ])

                # Drop stage after this stop?
                if i in drop_at:
                    gas_lbl_d = drop_at[i]
                    stage_num = drop_gases[gas_lbl_d]
                    te_d      = _trk(gas_lbl_d)
                    buoy_before = _total(plan_drop)
                    if te_d and te_d.get("sslot_idx") is not None:
                        plan_drop["sslots"][te_d["sslot_idx"]]["sel"] = "—"
                    buoy_after = _total(plan_drop)
                    delta = ((buoy_after - buoy_before)
                             if buoy_before is not None and buoy_after is not None else None)
                    delta_str = (f"{'↑' if delta >= 0 else '↓'} {abs(delta):.2f} kg"
                                 if delta is not None else "—")
                    bail_display.append({"_label": f"  ↓ Stage {stage_num} dropped",
                                          "_drop_marker": True,
                                          "depth": 0, "time": 0,
                                          "runtime": stop["runtime"],
                                          "gas": delta_str})
                    bail_extra.append([
                        ("", 65, "e"), ("", 75, "e"), ("", 70, "e"), ("", 80, "e"),
                        ("", 95, "e"),
                        (f"{buoy_after:+.2f}" if buoy_after is not None else "—", 85, "e"),
                    ])

            display_list = bail_display
        else:
            display_list = bail_stops

        # Append surface row
        if display_list and bail_total_runtime is not None:
            display_list = list(display_list) + [
                {"_label": "↑ Surface", "depth": 0.0, "time": "", "runtime": bail_total_runtime, "gas": ""}
            ]
            if bail_extra is not None:
                last_bail_extra = bail_extra[-1] if bail_extra else []
                # Compute true end-of-dive buoyancy from fully-consumed plan_base
                _final_buoy = _total(plan_base) if bail_stops and sac > 0 and plan_state_bp else None
                _final_buoy_str = (f"{_final_buoy:+.2f}" if _final_buoy is not None else "—")
                surface_extra = []
                for _si, (_v, _w, _a) in enumerate(last_bail_extra):
                    if _si == 0 or _si == 2:
                        surface_extra.append(("—", _w, _a))
                    elif _si == 4:
                        surface_extra.append((_final_buoy_str, _w, _a))
                    else:
                        surface_extra.append((_v, _w, _a))
                bail_extra = list(bail_extra) + [surface_extra]

        # Collect gas chart data — always 4 slots matching the 4 bailout gas rows
        _role_labels = ["Bailout gas", "Interstage gas", "Deco gas 1", "Deco gas 2"]
        self._bail_gas_chart_data = []
        for slot_i in range(4):
            row_d = self._gas_rows[slot_i] if slot_i < len(self._gas_rows) else None
            trk   = stage_tracking[slot_i] if (bail_stops and sac > 0 and stage_tracking
                                                and slot_i < len(stage_tracking)) else None
            if trk is not None and plan_state_bp:
                init_L = trk.get("real_gas_L", 0)
                rem_L  = _rgl(plan_base, trk) if init_L > 0 else 0.0
                used_L = max(0.0, init_L - (rem_L or 0.0))
                gas_lbl = trk.get("label", "?")
            else:
                init_L, used_L, rem_L = 0.0, 0.0, 0.0
                # Try to get gas name from row even if no tracking
                gas_lbl = ""
                if row_d:
                    o2 = row_d.get("o2", 0)
                    he = row_d.get("he", 0)
                    if o2 > 0:
                        gas_lbl = _gas_label(o2, he)
            self._bail_gas_chart_data.append({
                "role":      _role_labels[slot_i],
                "label":     gas_lbl,
                "initial_L": init_L,
                "used_L":    used_L,
                "remain_L":  rem_L,
            })

        self._render_stops(display_list, self._bail_table_area,
                           extra_data=bail_extra, has_pre_gas=False)

        self._update_charts(segments)

        # (no autosave — profile is only written on explicit Save / Save as new)

    # ── Render stop rows ──────────────────────────────────────────────────────

    def _render_stops(self, stops: list, inner: QWidget,
                      extra_data: list, has_pre_gas: bool):
        """Render result rows into the inner widget."""
        # Apply display interval filter — keep nav/drop markers always;
        # for real stops only keep depths that are multiples of display_interval.
        disp_int = max(1, int(self._get_setting("_display_int", 3)))
        filtered_pairs = [
            (s, extra_data[i] if (extra_data and i < len(extra_data)) else [])
            for i, s in enumerate(stops)
            if s.get("_drop_marker") or s.get("_label")
            or (round(s.get("depth", 0)) % disp_int == 0)
        ]
        stops      = [p[0] for p in filtered_pairs]
        extra_data = [p[1] for p in filtered_pairs]

        # Remove old rows (keep stretch at end)
        vl = inner._vl
        while vl.count() > 1:
            item = vl.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not stops:
            lbl = QLabel("No decompression required.")
            lbl.setStyleSheet("color:#666; padding:4px;")
            vl.insertWidget(0, lbl)
            return

        cols = inner._cols

        for idx, stop in enumerate(stops):
            is_drop = stop.get("_drop_marker", False)
            is_nav  = bool(stop.get("_label"))
            row_bg  = CLR_DROP if is_drop else (CLR_STOP if not is_nav else "#f8f8f8")

            depth_str   = stop.get("_label") or f"{stop['depth']:.0f} m"
            show_time   = stop.get("_show_time", False)
            time_str    = "" if (is_drop or (is_nav and not show_time)) else f"{stop['time']:.0f}"
            runtime_str = "" if is_drop else f"{stop['runtime']:.0f}"
            bold        = is_drop

            row_w = QWidget()
            row_w.setStyleSheet(f"background:{row_bg};")
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(2, 1, 2, 1)
            row_l.setSpacing(6)

            row_extra = extra_data[idx] if (extra_data and idx < len(extra_data)) else []

            # Build all column values
            if has_pre_gas:
                # CCR: base = [depth, stop, runtime] + pre_gas=[SP] + [gas] + extra
                base_vals = [
                    (depth_str,   cols[0][1], "w"),
                    (time_str,    cols[1][1], "e"),
                    (runtime_str, cols[2][1], "e"),
                ]
                # SP and Gas from extra_data
                if row_extra:
                    sp_val = row_extra[0][0] if row_extra else ""
                    post   = row_extra[1:]
                else:
                    sp_val = ""
                    post   = []
                gas_str  = stop.get("gas", "")
                all_vals = (base_vals
                            + [(sp_val, cols[3][1], "w"),
                               (gas_str, cols[4][1], "w")]
                            + [(v, w, a) for v, w, a in post])
            else:
                # Bailout: base = [depth, stop, runtime, gas] + extra
                base_vals = [
                    (depth_str,   cols[0][1], "w"),
                    (time_str,    cols[1][1], "e"),
                    (runtime_str, cols[2][1], "e"),
                    (stop.get("gas", ""), cols[3][1], "w"),
                ]
                all_vals = base_vals + [(v, w, a) for v, w, a in row_extra]

            for val, w, a in all_vals:
                lbl = QLabel(str(val))
                lbl.setFixedWidth(w)
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                border = "border-right: 1px dashed #cccccc;"
                if bold:
                    lbl.setStyleSheet(f"font-weight:bold; {border}")
                else:
                    lbl.setStyleSheet(border)
                row_l.addWidget(lbl)

            row_l.addStretch()
            vl.insertWidget(vl.count() - 1, row_w)

    # ── Public refresh (called by MainWindow when user/plan changes) ──────────

    def refresh_for_user(self, user: str):
        """Called when the active user changes in BuoyancyPlannerTab."""
        self._refresh_stage_plan_cb()
        profiles = {}
        if user in self._db.get("users", {}):
            profiles = self._db["users"][user].get("deco_profiles", {})
        self._selected_profile = ""
        self.load_deco_profiles(profiles)
