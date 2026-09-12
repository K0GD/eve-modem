"""Operator display, headless (offscreen Qt): the window builds around a session, takes
frames from the listener hook, refreshes every panel, and renders.

Run activated:  python -m pytest tests/test_eve_display.py -q
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eve import EveParams, modem  # noqa: E402
from eve import doppler as D  # noqa: E402
from eve import schedule as S  # noqa: E402

TEXT = "K0PRT K0PRT"


def _have_qt():
    try:
        import PySide6  # noqa: F401
        import pyqtgraph  # noqa: F401
        from gnuradio import gr  # noqa: F401
        return True
    except Exception:
        return False


def test_display_builds_and_refreshes():
    if not _have_qt():
        return
    from PySide6 import QtWidgets
    from eve.station import Session, SessionOptions, SimRadio
    from eve.display import OperatorWindow
    p = replace(EveParams.variant_a(), n_frames=4, pilot_frames=1)
    now = time.time()
    model = D.DopplerModel(D.synthetic_table("sim", D.DSES_HASWELL, now - 60, now + 3600, range_km=375000.0))
    sched = S.build_schedule("DISP", "sim", model, now + 30.0, 1296e6, params=p, text=TEXT, repeat_count=1,
                             chunk_s=None, rtt_guard_s=0.1, t_off_min_s=0.5, t_on_max_s=3600.0)
    sess = Session(sched, model, SimRadio(p), SessionOptions(pa_in_chain=False, tx_precompensate=False))
    sess.acc = modem.SymbolAccumulator(p, sched.frame_map())
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = OperatorWindow(sess)
    assert len(sess.listeners) == 1
    # feed synthetic frames: the right tones with a little noise, through the listener hook
    bank = modem.FrameBank(p)
    fm = sched.frame_map()
    rng = np.random.default_rng(0)
    for k in range(0, 3 * p.n_frames + 2):
        d = fm.tone(k)
        x = modem.ToneSynthesizer(modem.FrameMap([d] * p.n_sym, p), p.modem_rate, p, include_if=False).generate(p.n_fft)
        x = x + 0.05 * (rng.standard_normal(p.n_fft) + 1j * rng.standard_normal(p.n_fft)).astype(np.complex64)
        metric = bank.frame_metric(x)
        sess.acc.add(k, metric)
        sess.listeners[0](k, x, metric)
    sess.phase = "listening for chunk 1"
    win._refresh()
    win._refresh_radio()
    assert win._frames == 3 * p.n_frames + 2
    assert win.table.item(0, 2).text() == str(sched.symbols[0])       # symbol 0 decided right
    assert win.table.item(1, 5).text() == "\u2713" and win.table.item(2, 5).text() == "\u2713"
    assert "symbols right" in win.lbl_decode.text()
    assert "SimRadio" in win.lbl_radio.text() and "RTT" in win.lbl_eph.text()
    assert "listening" in win.lbl_phase.text()
    img = win.grab()
    assert img.width() > 800 and img.height() > 500
    win.close()


if __name__ == "__main__":
    test_display_builds_and_refreshes()
    print("PASS")
