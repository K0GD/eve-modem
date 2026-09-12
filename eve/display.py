"""Operator display (design document 5.3, 7.1, 7.3): the tone strip, the running symbol
decisions, chunk and keying state, radio status, and the target's ephemeris, for a
session running in a background thread.

PySide6 + pyqtgraph, the Workbench's binding rules (PySide6 only; the pyqtgraph backend
is forced before pyqtgraph is imported). The live view is for the operator; the decision
of record is the offline decode of the archive (5.3).

`OperatorPanel` is the widget; it can be bound to one session after another (the
application reuses it across runs). `OperatorWindow` wraps it in a main window for the
command-line tools:

    from eve.display import run_with_display
    run_with_display(session)          # blocks in the Qt event loop; returns the report
"""
from __future__ import annotations

import os
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import queue
import threading
import time
from typing import Optional

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .params import EveParams
from .doppler import iso_utc
from . import modem

TEAL, NAVY, GOLD = "#156082", "#0A2F40", "#B86A18"
STRIP_ROWS = 300            # frames kept in the tone strip (~105 s of Variant A)
STRIP_COLS = 512            # candidate bins collapsed 8:1 for display
MONO = "font-family: 'Source Code Pro', Consolas, monospace;"
ABORT_STYLE = ("QPushButton { background: #c0392b; color: white; font-weight: bold; padding: 6px; }"
               "QPushButton:disabled { background: #6e2f28; color: #c8b8b6; }")


def wrap_tooltips(root) -> None:
    """Qt shows plain-text tooltips on one line however long; rich text wraps. Convert
    every plain tooltip under `root` (and root's own) to wrapped rich text."""
    import html
    widgets = [root] + root.findChildren(QtWidgets.QWidget)
    for w in widgets:
        t = w.toolTip()
        if t and not t.lstrip().startswith("<"):
            w.setToolTip("<qt><p style='white-space:normal'>" + html.escape(t) + "</p></qt>")
    for a in root.findChildren(QtGui.QAction):
        t = a.toolTip()
        if t and not t.lstrip().startswith("<"):
            a.setToolTip("<qt><p style='white-space:normal'>" + html.escape(t) + "</p></qt>")


class OperatorPanel(QtWidgets.QWidget):
    """The live view of one session. Call bind(session) before the session runs (it
    registers the frame listener and takes the session log); unbind() afterwards or
    simply bind the next session."""

    def __init__(self, refresh_ms: int = 250, parent=None):
        super().__init__(parent)
        self.session = None
        self.sched = None
        self.p: Optional[EveParams] = None
        self.radio = None
        self.model = None
        self._q: "queue.Queue" = queue.Queue(maxsize=4000)
        self._strip = np.zeros((STRIP_ROWS, STRIP_COLS), dtype=np.float32)
        self._strip_k = np.full(STRIP_ROWS, -1, dtype=np.int64)
        self._row = 0
        self._last_metric: Optional[np.ndarray] = None
        self._last_k: Optional[int] = None
        self._frames = 0
        self._log_lines = []
        self._log_lock = threading.Lock()
        self._gpsdo = None
        self._gpsdo_absent = False
        self._build()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(refresh_ms)
        self._radio_timer = QtCore.QTimer(self)
        self._radio_timer.timeout.connect(self._refresh_radio)
        self._radio_timer.start(2000)
        self._show_idle()

    # ---- layout -----------------------------------------------------------------------------
    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        # header: session + phase + clocks
        self.lbl_title = QtWidgets.QLabel()
        self.lbl_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {NAVY};")
        self.lbl_phase = QtWidgets.QLabel("idle")
        self.lbl_phase.setStyleSheet(f"font-size: 15px; font-weight: bold; color: white; background: {TEAL}; padding: 3px 8px; border-radius: 3px;")
        self.lbl_clock = QtWidgets.QLabel()
        self.lbl_clock.setStyleSheet(MONO + " font-size: 12px;")
        hdr = QtWidgets.QHBoxLayout()
        hdr.addWidget(self.lbl_title, 1)
        hdr.addWidget(self.lbl_phase)
        hdr.addWidget(self.lbl_clock)
        outer.addLayout(hdr)

        # tone strip + current frame spectrum (left)
        strip_box = QtWidgets.QGroupBox("Tone strip (frames x candidate bins, newest at top). 4096-ary FSK sends ONE tone per frame: "
                                        "one dot per row is the whole signal")
        vb = QtWidgets.QVBoxLayout(strip_box)
        self.strip_plot = pg.PlotWidget()
        self.strip_img = pg.ImageItem()
        self.strip_plot.addItem(self.strip_img)
        self.strip_plot.setLabel("bottom", "tone index d (0 .. 4095)")
        self.strip_plot.setLabel("left", "frames ago")
        self.strip_plot.invertY(True)
        try:
            self.strip_img.setColorMap(pg.colormap.get("viridis"))
        except Exception:
            pass
        vb.addWidget(self.strip_plot, 3)
        self.spec_plot = pg.PlotWidget()
        self.spec_plot.setLabel("bottom", "tone index d")
        self.spec_plot.setLabel("left", "accumulated metric (dB rel. median)")
        self.spec_plot.setTitle("current symbol: frames summed so far (gold = expected tone, red = leader)", size="9pt")
        self.spec_curve = self.spec_plot.plot(pen=pg.mkPen(TEAL, width=1))
        self.spec_expected = pg.InfiniteLine(angle=90, pen=pg.mkPen(GOLD, width=2, style=QtCore.Qt.DashLine))
        self.spec_decided = pg.InfiniteLine(angle=90, pen=pg.mkPen("#D55E00", width=1))
        self.spec_plot.addItem(self.spec_expected)
        self.spec_plot.addItem(self.spec_decided)
        vb.addWidget(self.spec_plot, 2)

        # right column: decisions, schedule, radio
        right = QtWidgets.QWidget()
        rv_all = QtWidgets.QVBoxLayout(right)
        rv_all.setContentsMargins(0, 0, 0, 0)
        dec_box = QtWidgets.QGroupBox("Running decisions (live accumulator; the offline decode is the decision of record)")
        dv = QtWidgets.QVBoxLayout(dec_box)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["symbol", "expected", "decided", "margin dB", "frames", "state"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        dv.addWidget(self.table)
        self.lbl_decode = QtWidgets.QLabel("no frames yet")
        self.lbl_decode.setWordWrap(True)
        self.lbl_decode.setStyleSheet(MONO)
        dv.addWidget(self.lbl_decode)
        rv_all.addWidget(dec_box, 3)

        sch_box = QtWidgets.QGroupBox("Schedule")
        sv = QtWidgets.QVBoxLayout(sch_box)
        self.lbl_sched = QtWidgets.QLabel()
        self.lbl_sched.setStyleSheet(MONO + " font-size: 11px;")
        self.lbl_sched.setWordWrap(True)
        sv.addWidget(self.lbl_sched)
        self.key_lamp = QtWidgets.QLabel("KEY")
        self.key_lamp.setAlignment(QtCore.Qt.AlignCenter)
        self._lamp(False)
        sv.addWidget(self.key_lamp)
        rv_all.addWidget(sch_box, 1)

        rad_box = QtWidgets.QGroupBox("Radio and ephemeris")
        rv = QtWidgets.QVBoxLayout(rad_box)
        self.lbl_radio = QtWidgets.QLabel()
        self.lbl_radio.setStyleSheet(MONO + " font-size: 11px;")
        self.lbl_radio.setWordWrap(True)
        rv.addWidget(self.lbl_radio)
        self.lbl_eph = QtWidgets.QLabel()
        self.lbl_eph.setStyleSheet(MONO + " font-size: 11px;")
        rv.addWidget(self.lbl_eph)
        self.btn_abort = QtWidgets.QPushButton("ABORT — release key, stop")
        self.btn_abort.setStyleSheet(ABORT_STYLE)
        self.btn_abort.setEnabled(False)
        self.btn_abort.clicked.connect(self._abort_clicked)
        rv.addWidget(self.btn_abort)
        rv_all.addWidget(rad_box, 1)

        self.hsplit = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.hsplit.addWidget(strip_box)
        self.hsplit.addWidget(right)
        self.hsplit.setStretchFactor(0, 3)
        self.hsplit.setStretchFactor(1, 2)
        self.hsplit.setSizes([760, 500])

        # log below, in a vertical splitter
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setStyleSheet(MONO + " font-size: 11px;")
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.vsplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.vsplit.addWidget(self.hsplit)
        self.vsplit.addWidget(self.log)
        self.vsplit.setStretchFactor(0, 4)
        self.vsplit.setStretchFactor(1, 1)
        self.vsplit.setSizes([620, 160])
        outer.addWidget(self.vsplit, 1)
        # operator help
        strip_box.setToolTip("Each row is one frame (0.35 s); each column a candidate tone (4096 collapsed to 512). "
                             "The waveform sends ONE tone per frame, so a healthy signal is a single bright dot per "
                             "row that steps to a new column at every symbol boundary; the pilot is a steady column "
                             "before each pass. Background texture = receiver noise; adjust RX gain so it is visible "
                             "but not saturated. Nothing at all = no signal, wrong gain, or the receive window is closed.")
        self.spec_plot.setToolTip("The metric of the symbol now in progress, summed over its frames so far. Gold "
                                  "dashed = the tone we sent (expected); red = the current leader. At Venus strength "
                                  "single frames show nothing and only this sum climbs out of the noise as frames add up.")
        dec_box.setToolTip("Live decisions from the same accumulators: one row per symbol with the expected tone, the "
                           "leader so far, its margin over the runner-up in dB, and the frames summed. Green = agrees "
                           "with what we sent. The offline decode after the run is the decision of record; this view "
                           "may lag it or differ on a marginal symbol.")
        self.lbl_decode.setToolTip("The message the live accumulators would decode now, with the BCH correction count "
                                   "and CRC state. 'CRC ok' with the right text means the message is through.")
        sch_box.setToolTip("Where the schedule stands: the chunk in progress (transmitting, waiting for the echo, "
                           "receiving), chunks complete, session start and end, time remaining, frames wanted vs received.")
        self.key_lamp.setToolTip("Red = the key line to the sequencer is asserted (transmitting). It rises T_lead before "
                                 "the RF and drops T_lag after it; a watchdog drops it if a chunk overruns the PA limit.")
        rad_box.setToolTip("B210 state: reference lock (must read LOCKED on the air), rates, frequencies, gains, LO "
                           "offset health, PPS time-set error; the GPS clock's lock and signal-loss count; the target's "
                           "azimuth, elevation, round trip, Doppler and Doppler rate from the ephemeris.")
        self.btn_abort.setToolTip("Release the key line immediately, stop the streams, close the archive, write the log. "
                                  "Use it for any fault: reference unlock, amplifier trouble, wrong pointing. Dim = no "
                                  "session running.")
        self.log.setToolTip("The session log: controller steps, keying events, radio messages, decode results.")
        self.lbl_phase.setToolTip("Session phase: idle, preparing, armed, TX chunk n, listening, draining, finished, aborted.")
        self.lbl_clock.setToolTip("PC clock (UTC) and the B210's device time; they agree to milliseconds when the time was set on a PPS.")
        wrap_tooltips(self)

    def _abort_clicked(self):
        if self.session is not None:
            self.session.abort("operator abort from the display")

    def _lamp(self, on: bool):
        self.key_lamp.setStyleSheet(
            "font-size: 16px; font-weight: bold; padding: 6px; border-radius: 4px; color: white; background: "
            + ("#c0392b" if on else "#7f8c8d") + ";")
        self.key_lamp.setText("KEY DOWN — TRANSMITTING" if on else "key up")

    def _show_idle(self):
        self.lbl_title.setText("no session")
        self.lbl_phase.setText("idle")
        self.lbl_sched.setText("Set up a run on the Setup tab and press Start.")
        self.lbl_eph.setText("")
        self.lbl_radio.setText("")

    # ---- binding ----------------------------------------------------------------------------
    def bind(self, session):
        """Attach a session (before it runs). Resets the strip, the table, and the counters."""
        self.unbind()
        self.session = session
        self.sched = session.sched
        self.p = session.p
        self.radio = session.radio
        self.model = session.model
        self._strip[:] = 0
        self._strip_k[:] = -1
        self._row = 0
        self._last_metric = self._last_k = None
        self._frames = 0
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self.table.setRowCount(self.p.n_sym)
        for m in range(self.p.n_sym):
            for c in range(6):
                it = QtWidgets.QTableWidgetItem("")
                it.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table.setItem(m, c, it)
            self.table.item(m, 0).setText(str(m))
            self.table.item(m, 1).setText(str(self.sched.symbols[m]))
        self.lbl_decode.setText("no frames yet")
        self.lbl_decode.setStyleSheet(MONO)
        self.spec_curve.setData([], [])
        s = self.sched
        self.lbl_title.setText(f"{s.session_id}  ·  {s.target}  ·  {s.mode}  ·  {s.f_dial_hz / 1e6:.4f} MHz  ·  "
                               f"Variant {self.p.variant}  ·  '{s.text}'  ·  repeat {s.repeat_count}  ·  "
                               f"{self.p.n_frames} frames/symbol ({self.p.t_sym:.1f} s)")
        session.listeners.append(self._on_frame)
        session.log = self.append_log
        self.btn_abort.setEnabled(True)
        self._refresh_radio()

    def unbind(self):
        if self.session is not None:
            try:
                self.session.listeners.remove(self._on_frame)
            except ValueError:
                pass
        self.session = None
        self.radio = None
        self.model = None
        self.btn_abort.setEnabled(False)

    # ---- data in ------------------------------------------------------------------------------
    def _on_frame(self, k: int, x: np.ndarray, metric: np.ndarray):
        try:
            self._q.put_nowait((int(k), np.asarray(metric, dtype=np.float32)))
        except queue.Full:
            pass

    def append_log(self, text: str):
        with self._log_lock:
            self._log_lines.append(f"{iso_utc(time.time(), 0)[11:19]}  {text}")

    # ---- refresh ----------------------------------------------------------------------------
    def _refresh(self):
        with self._log_lock:
            lines, self._log_lines = self._log_lines, []
        for line in lines[-200:]:
            self.log.appendPlainText(line)
        if self.session is None:
            self.lbl_clock.setText(f"UTC {iso_utc(time.time(), 0)[11:19]}")
            return
        drained = 0
        while drained < 200:
            try:
                k, metric = self._q.get_nowait()
            except queue.Empty:
                break
            drained += 1
            self._frames += 1
            row = self._row % STRIP_ROWS
            m = metric.reshape(STRIP_COLS, -1).max(axis=1)
            self._strip[row] = m
            self._strip_k[row] = k
            self._row += 1
            self._last_metric, self._last_k = metric, k
        if drained:
            img = np.roll(self._strip, -(self._row % STRIP_ROWS), axis=0)[::-1]      # newest at top
            med = float(np.median(img[img > 0])) if np.any(img > 0) else 1.0
            self.strip_img.setImage(np.log10(np.maximum(img, 1e-12) / med).T, autoLevels=False, levels=(0.2, 1.1))
            self.strip_img.setRect(QtCore.QRectF(0, 0, self.p.m, STRIP_ROWS))
            # the accumulated metric of the symbol the latest frame belongs to (per-frame
            # spectra cannot show a tone at Venus strength; the sum over frames can)
            acc = self.session.acc
            r, m = modem.frame_symbol_index(self._last_k, self.p)
            row = None
            if acc is not None and (r, m) in acc.acc:
                row = acc.acc[(r, m)]
            if row is None or not np.any(row):
                row = self._last_metric
            spec = 20 * np.log10(np.maximum(row, 1e-12) / max(float(np.median(row)), 1e-12))
            self.spec_curve.setData(np.arange(self.p.m), spec)
            exp = self.sched.frame_map().tone(self._last_k)
            self.spec_expected.setPos(exp if exp != modem.OFF else -10)
            self.spec_decided.setPos(int(np.argmax(row)))
            self._refresh_decisions()
        self._refresh_schedule()

    def _refresh_decisions(self):
        acc = self.session.acc
        if acc is None or not acc.acc:
            return
        c = acc.combined()
        margins = acc.margin_db()
        ok_syms = 0
        for m in range(self.p.n_sym):
            row = c[m]
            n = sum(acc.count.get((r, m), 0) for r in acc.repetitions)
            if not row.any():
                self.table.item(m, 2).setText("—")
                self.table.item(m, 3).setText("")
                self.table.item(m, 4).setText("0")
                self.table.item(m, 5).setText("waiting")
                continue
            d = int(np.argmax(row))
            good = d == self.sched.symbols[m]
            ok_syms += int(good)
            self.table.item(m, 2).setText(str(d))
            self.table.item(m, 3).setText(f"{margins[m]:.1f}")
            self.table.item(m, 4).setText(str(n))
            self.table.item(m, 5).setText("✓" if good else "✗")
            color = QtGui.QColor("#D6F0DD") if good else QtGui.QColor("#F7D9D3")
            for col in range(6):
                self.table.item(m, col).setBackground(color)
        try:
            out = acc.decode()
            txt = (f"frames {acc.frames_seen}  passes {acc.repetitions}  symbols right {ok_syms}/{self.p.n_sym}  "
                   f"BCH {'ok' if out.bch_ok else 'fail'} ({out.bits_corrected} corrected)  CRC {'ok' if out.crc_ok else 'fail'}  "
                   f"text '{out.text}'")
            self.lbl_decode.setText(txt)
            self.lbl_decode.setStyleSheet(MONO + " font-weight: bold; color: " + ("#1e7d3a" if out.ok else "#7f2a1e") + ";")
        except Exception as e:
            self.lbl_decode.setText(f"decode error: {e}")

    def _refresh_schedule(self):
        s = self.sched
        try:
            now = self.radio.device_time()
        except Exception:
            now = time.time()
        self.lbl_clock.setText(f"UTC {iso_utc(time.time(), 0)[11:19]}   device {iso_utc(now, 0)[11:19]}")
        self.lbl_phase.setText(self.session.phase)
        self.btn_abort.setEnabled(self.session.phase not in ("finished", "aborted"))
        self._lamp(bool(getattr(self.radio, "keyed", False)))
        lines = []
        cur = None
        for c in s.chunks:
            if c.tx_start - 60 <= now <= c.rx_stop + 5:
                cur = c
                break
        if cur is None:
            nxt = next((c for c in s.chunks if c.tx_start > now), None)
            if nxt:
                lines.append(f"next chunk {nxt.index + 1} of {len(s.chunks)} in {nxt.tx_start - now:6.1f} s  ({iso_utc(nxt.tx_start, 0)[11:19]})")
            else:
                lines.append("no chunks remaining")
        else:
            c = cur
            if now < c.tx_start:
                lines.append(f"chunk {c.index + 1} of {len(s.chunks)}: TX in {c.tx_start - now:5.1f} s")
            elif now < c.tx_stop:
                lines.append(f"chunk {c.index + 1} of {len(s.chunks)}: TRANSMITTING, {c.tx_stop - now:5.1f} s left  frames {c.frame_first}-{c.frame_last}")
            elif now < c.rx_start:
                lines.append(f"chunk {c.index + 1} of {len(s.chunks)}: echo due in {c.rx_start - now:5.1f} s")
            elif now < c.rx_stop:
                lines.append(f"chunk {c.index + 1} of {len(s.chunks)}: RECEIVING echo, {c.rx_stop - now:5.1f} s left")
            else:
                lines.append(f"chunk {c.index + 1} of {len(s.chunks)}: done")
        done = sum(1 for c in s.chunks if now > c.rx_stop)
        remaining = max(0.0, s.t_end - now)
        lines.append(f"chunks complete {done}/{len(s.chunks)}   session {iso_utc(s.t_start, 0)[11:19]} .. {iso_utc(s.t_end, 0)[11:19]} UTC"
                     f"   remaining {int(remaining // 60):d}:{int(remaining % 60):02d}")
        lines.append(f"frames wanted {s.n_frames_wanted}  received {self._frames}")
        self.lbl_sched.setText("\n".join(lines))

    def _refresh_radio(self):
        if self.session is None:
            return
        try:
            if hasattr(self.radio, "status"):
                st = self.radio.status()
                txt = (f"B210 {st.serial}  ref {st.clock_source}/{st.time_source}  "
                       f"{'LOCKED' if st.ref_locked else 'not locked'}\n"
                       f"rx {st.rx_rate:.2f} S/s  {st.rx_freq / 1e6:.6f} MHz  gain {st.rx_gain:.0f} dB  {st.rx_antenna}\n"
                       f"tx {st.tx_rate:.2f} S/s  {st.tx_freq / 1e6:.6f} MHz  gain {st.tx_gain:.0f} dB  {st.tx_antenna}\n"
                       f"LO offset rx {'ok' if st.rx_lo_offset_ok else 'FELL BACK'} / tx {'ok' if st.tx_lo_offset_ok else 'FELL BACK'}"
                       f"   PPS set error {st.pps_error_s if st.pps_error_s is None else f'{st.pps_error_s:+.6f} s'}")
            else:
                txt = getattr(self.radio, "status_text", lambda: "radio")()
        except Exception as e:
            txt = f"radio status unavailable: {e}"
        try:
            from . import gpsdo as _gpsdo
            g = self._gpsdo
            if g is None and not self._gpsdo_absent:
                try:
                    g = self._gpsdo = _gpsdo.LeoBodnarGPSDO()
                except Exception:
                    self._gpsdo_absent = True
            if g is not None:
                st = g.status(500)
                txt += (f"\nGPS clock {g.serial}: sat {'LOCK' if st.sat_lock else 'no lock'}, "
                        f"PLL {'LOCK' if st.pll_lock else 'no lock'}, signal losses {st.loss_count}")
        except Exception as e:
            txt += f"\nGPS clock: {e}"
        self.lbl_radio.setText(txt)
        try:
            now = self.radio.device_time()
            if self.model is not None:
                f = self.sched.f_dial_hz
                self.lbl_eph.setText(
                    f"{self.sched.target}: az {self.model.azimuth_deg(now):6.1f}  el {self.model.elevation_deg(now):5.1f}  "
                    f"RTT {self.model.rtt_s(now):7.2f} s  Doppler {self.model.doppler_hz(now, f):+8.1f} Hz  "
                    f"rate {self.model.rate_hz_s(now, f):+.3f} Hz/s")
        except Exception as e:
            self.lbl_eph.setText(f"ephemeris: {e}")


class OperatorWindow(QtWidgets.QMainWindow):
    """A main window around one OperatorPanel, bound to one session (the command-line
    tools' --display)."""

    def __init__(self, session, refresh_ms: int = 250):
        super().__init__()
        self.panel = OperatorPanel(refresh_ms)
        self.setCentralWidget(self.panel)
        self.panel.bind(session)
        self.session = session
        self._done_at = None
        self._quit_app = None
        self.setWindowTitle(f"DSES EVE modem — {session.sched.session_id}")
        self.resize(1280, 820)
        self._t = QtCore.QTimer(self)
        self._t.timeout.connect(self._maybe_quit)
        self._t.start(250)

    def append_log(self, text: str):
        self.panel.append_log(text)

    def __getattr__(self, name):
        # the tools and tests reach the panel's widgets and counters through the window
        panel = self.__dict__.get("panel")
        if panel is None:
            raise AttributeError(name)
        return getattr(panel, name)

    def _maybe_quit(self):
        if self._done_at is not None and self._quit_app is not None and time.monotonic() - self._done_at > 1.5:
            self._quit_app.quit()
            self._quit_app = None


def run_with_display(session, app: Optional[QtWidgets.QApplication] = None, exit_when_done: bool = True,
                     screenshot: Optional[str] = None, screenshot_after_s: float = 20.0):
    """Run the session in a background thread with the operator window in the Qt loop.
    Returns the SessionReport."""
    app = app or QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = OperatorWindow(session)
    result = {}

    def worker():
        try:
            result["report"] = session.run()
        except Exception as e:      # noqa: BLE001
            result["error"] = e
            win.append_log(f"session error: {e!r}")
        finally:
            win.append_log("session finished" + ("" if exit_when_done else " - close the window to exit"))
            if exit_when_done:
                win._quit_app = app          # the GUI thread quits from its own timer (a
                win._done_at = time.monotonic()   # QTimer started from this thread would not fire)

    th = threading.Thread(target=worker, name="eve-session", daemon=True)
    win.show()
    if screenshot:
        QtCore.QTimer.singleShot(int(screenshot_after_s * 1000), lambda: win.grab().save(str(screenshot)))
    th.start()
    app.exec()
    th.join(timeout=5.0)
    if "error" in result:
        raise result["error"]
    return result.get("report", session.report)
