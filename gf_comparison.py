"""Fixed Bottom Time GF Comparison — Mitchell/NEDU methodology."""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QScrollArea,
    QSizePolicy, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from buhlmann import simulate_dive, CCRConfig, OCGas


# ── Background worker ─────────────────────────────────────────────────────────

class _Worker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, jobs: list):
        super().__init__()
        self._jobs = jobs

    def run(self):
        results = []
        n = len(self._jobs)
        for i, job in enumerate(self._jobs):
            gflo = job["gflo"]
            self.progress.emit(
                f"Computing GF {gflo}/{int(job['gf_hi']*100)}  ({i+1}/{n}) …")
            try:
                r = simulate_dive(
                    segments   = job["segments"],
                    mode       = "ccr",
                    ccr        = job["ccr"],
                    oc_gases   = job["oc_gases"],
                    gf_low     = gflo / 100.0,
                    gf_high    = job["gf_hi"],
                    desc_rate  = job["desc_r"],
                    asc_rate   = job["asc_r"],
                    deco_rate  = job["deco_r"],
                    snap_interval = 1.0,
                )
                i_surf  = _compute_sat_integral(r.tissue_timeline, surface_mv=True)
                i_depth = _compute_sat_integral(r.tissue_timeline, surface_mv=False)
                surf_mv = _snap_mv(r.tissue_at_surface, 0.0, surface=True)
                surf_sum = sum(pt / mv for pt, mv in surf_mv if mv > 0)
                results.append({
                    "gflo": gflo, "valid": True,
                    "rt": r.runtime, "tts": r.tts,
                    "surf_sum": surf_sum,
                    "i_surf": i_surf, "i_depth": i_depth,
                    "otu": r.otu, "cns": r.cns,
                })
            except Exception as e:
                results.append({"gflo": gflo, "valid": False, "err": str(e)})
        self.finished.emit(results)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_sat_integral(timeline, surface_mv=True):
    from buhlmann import TISSUES, P_SURF
    if not timeline or len(timeline) < 2:
        return None
    total = 0.0
    for idx, (rt, depth, snap, *_) in enumerate(timeline):
        dt_prev = (rt - timeline[idx-1][0]) / 2 if idx > 0 else 0.0
        dt_next = (timeline[idx+1][0] - rt) / 2 if idx < len(timeline)-1 else 0.0
        dt = dt_prev + dt_next
        mv_list = _snap_mv(snap, depth, surface=surface_mv)
        compartment_sum = sum(pt / mv for pt, mv in mv_list if mv > 0)
        total += compartment_sum * dt
    return total


def _snap_mv(snap, depth, surface=True):
    from buhlmann import TISSUES, P_SURF
    p_amb = P_SURF if surface else (P_SURF + depth / 10.0)
    out = []
    for i, (p_n2, p_he) in enumerate(snap):
        if i >= len(TISSUES):
            break
        _, N2_a, N2_b, _, He_a, He_b = TISSUES[i]
        pt = p_n2 + p_he
        if pt > 0:
            a = (N2_a * p_n2 + He_a * p_he) / pt
            b = (N2_b * p_n2 + He_b * p_he) / pt
        else:
            a, b = N2_a, N2_b
        out.append((pt, a + p_amb / b))
    return out


def _grad_colors(val, all_vals):
    """(bg, fg) — green=low/best → red=high/worst."""
    valid = [v for v in all_vals if v is not None]
    if not valid or val is None:
        return "#1a1a1a", "#555555"
    mn, mx = min(valid), max(valid)
    ratio = (val - mn) / (mx - mn) if mx > mn else 0.0
    if ratio < 0.25:  return "#1a4a1a", "#aaffaa"
    if ratio < 0.50:  return "#3a5a00", "#eeff88"
    if ratio < 0.75:  return "#7a5000", "#ffdd66"
    return "#882200", "#ffaaaa"


def _le(default="", width=60) -> QLineEdit:
    e = QLineEdit(str(default))
    e.setFixedWidth(width)
    e.setAlignment(Qt.AlignmentFlag.AlignRight)
    return e


# ── Main window ───────────────────────────────────────────────────────────────

class GFComparisonWindow(QWidget):
    """Fixed Bottom Time GF Comparison (Mitchell/NEDU methodology)."""

    COLS = ["GF_low", "GF_high", "Runtime [min]", "TTS [min]",
            "Σ at surface", "∫ Surface Sat", "∫ Depth Sat", "OTU", "CNS %"]

    def __init__(self, parent, segments, ccr, oc_gases,
                 gf_hi=0.80, desc_r=20.0, asc_r=9.0, deco_r=3.0):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("GF Comparison — Fixed Bottom Time  (Mitchell methodology)")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1100, 680)

        self._segments = segments
        self._ccr      = ccr
        self._oc_gases = oc_gases
        self._gf_hi    = gf_hi
        self._desc_r   = desc_r
        self._asc_r    = asc_r
        self._deco_r   = deco_r
        self._worker   = None

        vl = QVBoxLayout(self)
        vl.setContentsMargins(10, 10, 10, 10)
        vl.setSpacing(6)

        # ── Description ──────────────────────────────────────────────────────
        desc = QLabel(
            "Same depth and bottom time for every row.  GF_low controls stop depth.  "
            "Mitchell/NEDU result: high GF_low = shorter total dive AND lower ∫ sat — "
            "deep stops add time without reducing tissue loading.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#888888; font-size:9px;")
        vl.addWidget(desc)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        vl.addWidget(sep)

        # ── Input row ────────────────────────────────────────────────────────
        inp = QHBoxLayout()
        inp.setSpacing(10)

        depth = max((s[0] for s in segments), default=0)
        bt    = sum(s[1] for s in segments)

        for label, val, attr in [
            ("Depth [m]:",        f"{depth:.0f}", "_inp_depth"),
            ("Bottom time [min]:", f"{bt:.0f}",   "_inp_bt"),
            ("GF_high [%]:",      f"{int(gf_hi*100)}", "_inp_gfhi"),
            ("GF_low from [%]:",  "20",           "_inp_from"),
            ("GF_low to [%]:",    "90",           "_inp_to"),
            ("Step [%]:",         "10",           "_inp_step"),
        ]:
            inp.addWidget(QLabel(label))
            w = _le(val)
            setattr(self, attr, w)
            inp.addWidget(w)

        self._run_btn = QPushButton("▶  Run")
        self._run_btn.setStyleSheet(
            "background:#4a7a3a; color:white; font-weight:bold; padding:4px 14px;")
        self._run_btn.clicked.connect(self._start_run)
        inp.addWidget(self._run_btn)
        inp.addStretch()
        vl.addLayout(inp)

        # ── Status bar ───────────────────────────────────────────────────────
        self._status_lbl = QLabel("Configure parameters and click Run.")
        self._status_lbl.setStyleSheet("color:#aaaaaa; font-size:9px;")
        vl.addWidget(self._status_lbl)

        # ── Results table (scrollable) ────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._results_area = QWidget()
        self._results_area.setStyleSheet("background:#111111;")
        self._results_layout = QVBoxLayout(self._results_area)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(0)
        self._results_layout.addStretch()
        scroll.setWidget(self._results_area)
        vl.addWidget(scroll, 1)

        # ── Legend footer ────────────────────────────────────────────────────
        foot = QLabel(
            "Green = lowest (best)  →  Red = highest (worst).  "
            "Mitchell result: high GF_low → ∫ Surface Sat + ∫ Depth Sat ↓ (green),  "
            "Σ at surface ↑ (red).  Both Σ and ∫ lower = better decompression.")
        foot.setWordWrap(True)
        foot.setStyleSheet("color:#666666; font-size:9px;")
        vl.addWidget(foot)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _start_run(self):
        if self._worker and self._worker.isRunning():
            return
        try:
            depth   = float(self._inp_depth.text())
            bt      = float(self._inp_bt.text())
            gf_hi   = float(self._inp_gfhi.text()) / 100.0
            gf_from = int(self._inp_from.text())
            gf_to   = int(self._inp_to.text())
            gf_step = max(1, int(self._inp_step.text()))
        except ValueError:
            self._status_lbl.setText("Invalid input — check all fields.")
            return

        gflo_values = list(range(gf_from, gf_to + 1, gf_step))
        if not gflo_values:
            self._status_lbl.setText("No GF_low values in range.")
            return

        segments = [(depth, bt)]
        jobs = [
            {
                "gflo": gflo, "gf_hi": gf_hi,
                "segments": segments,
                "ccr": self._ccr, "oc_gases": self._oc_gases,
                "desc_r": self._desc_r, "asc_r": self._asc_r,
                "deco_r": self._deco_r,
            }
            for gflo in gflo_values
        ]

        self._run_btn.setEnabled(False)
        self._clear_results()
        self._draw_header()

        self._worker = _Worker(jobs)
        self._worker.progress.connect(self._status_lbl.setText)
        self._worker.finished.connect(self._on_results)
        self._worker.start()

    def _on_results(self, results: list):
        self._run_btn.setEnabled(True)
        self._status_lbl.setText(
            f"Bottom time fixed.  Green = lowest (best) → Red = highest (worst).  "
            f"Mitchell: high GF_low → ↓ ∫ sat  (less tissue-weighted deco time).")
        self._draw_results(results)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _clear_results(self):
        while self._results_layout.count() > 1:
            item = self._results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _make_cell(self, text: str, bg: str = "#1a1a1a", fg: str = "#cccccc",
                   bold: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont("Arial", 9)
        if bold:
            font.setBold(True)
        lbl.setFont(font)
        lbl.setStyleSheet(
            f"background:{bg}; color:{fg}; padding:3px 6px; border:1px solid #333;")
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return lbl

    def _draw_header(self):
        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(0)
        for col in self.COLS:
            row_l.addWidget(self._make_cell(col, bg="#2a3a4a", fg="#ffffff", bold=True))
        self._results_layout.insertWidget(
            self._results_layout.count() - 1, row_w)

    def _draw_results(self, results: list):
        valid = [r for r in results if r.get("valid")]

        # extract per-column value lists for colour grading
        col_vals = {
            "Runtime [min]":  [r["rt"]        for r in valid],
            "TTS [min]":      [r["tts"]       for r in valid],
            "Σ at surface":   [r["surf_sum"]  for r in valid],
            "∫ Surface Sat":  [r["i_surf"]    for r in valid],
            "∫ Depth Sat":    [r["i_depth"]   for r in valid],
            "OTU":            [r["otu"]       for r in valid],
            "CNS %":          [r["cns"]       for r in valid],
        }

        gf_hi_all = [r["gflo"] for r in valid]  # GF_hi is fixed per run

        for res in results:
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(0)

            if not res.get("valid"):
                row_l.addWidget(self._make_cell(
                    f"GF {res['gflo']}  —  Error: {res.get('err','?')}",
                    bg="#3a1a1a", fg="#ff8888"))
                self._results_layout.insertWidget(
                    self._results_layout.count() - 1, row_w)
                continue

            cells = {
                "GF_low":         (f"{res['gflo']} %",          "#223344", "#aaddff"),
                "GF_high":        (f"{int(self._gf_hi*100)} %", "#223344", "#aaddff"),
                "Runtime [min]":  (f"{res['rt']:.0f}",          *_grad_colors(res["rt"],       col_vals["Runtime [min]"])),
                "TTS [min]":      (f"{res['tts']:.0f}",         *_grad_colors(res["tts"],      col_vals["TTS [min]"])),
                "Σ at surface":   (f"{res['surf_sum']:.2f}",    *_grad_colors(res["surf_sum"], col_vals["Σ at surface"])),
                "∫ Surface Sat":  (f"{res['i_surf']:.0f}",      *_grad_colors(res["i_surf"],   col_vals["∫ Surface Sat"])),
                "∫ Depth Sat":    (f"{res['i_depth']:.0f}",     *_grad_colors(res["i_depth"],  col_vals["∫ Depth Sat"])),
                "OTU":            (f"{res['otu']:.0f}",         *_grad_colors(res["otu"],      col_vals["OTU"])),
                "CNS %":          (f"{res['cns']:.1f}",         *_grad_colors(res["cns"],      col_vals["CNS %"])),
            }

            for col in self.COLS:
                text, bg, fg = cells[col]
                row_l.addWidget(self._make_cell(text, bg=bg, fg=fg))

            self._results_layout.insertWidget(
                self._results_layout.count() - 1, row_w)
