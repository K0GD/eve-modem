"""Session engine through the software bench (design document 5.4 stage 3/4 rehearsal):
a short schedule streams through EveToneSource, a delayed AWGN channel, the two-stage
decimator and EveRxSink; the archive decodes offline and the live accumulator agrees.

Run activated:  python -m pytest tests/test_eve_station_sim.py -q     (~40 s, needs GNU Radio)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from eve import EveParams  # noqa: E402
from eve import doppler as D  # noqa: E402
from eve import schedule as S  # noqa: E402

TEXT = "K0PRT K0PRT"
APPX_C_SYMBOLS = [1203, 80, 1317, 1056, 1203, 80, 1317, 1031, 289, 3894, 3168]


def _have_gr():
    try:
        import gnuradio  # noqa: F401
        return True
    except Exception:
        return False


def test_sim_session_decodes():
    if not _have_gr():
        return
    from eve.station import Session, SessionOptions, SimRadio
    from eve_decode import decode_archive
    p = replace(EveParams.variant_a(), n_frames=4, pilot_frames=2)
    now = time.time()
    model = D.DopplerModel(D.synthetic_table("sim", D.DSES_HASWELL, now - 60, now + 3600, range_km=375000.0))
    with tempfile.TemporaryDirectory() as d:
        sched = S.build_schedule("SIMTEST", "sim", model, now + 2.0, 1296e6, params=p, text=TEXT, repeat_count=2,
                                 pilot=True, chunk_s=None, rtt_guard_s=0.1, t_off_min_s=0.5, t_on_max_s=3600.0)
        assert sched.messages_complete() == 2
        radio = SimRadio(p, cn0_db=18.0, seed=3)
        opts = SessionOptions(out_dir=d, pa_in_chain=False, tx_precompensate=False, live_decode=True,
                              start_margin_s=1.0, realtime_mode=False)
        rep = Session(sched, model, radio, opts).run()
        assert not rep.aborted
        assert rep.frames_sent == 2 * p.n_frames_msg
        assert rep.rx["gap_events"] == 0 and len(rep.rx["files"]) == len(sched.chunks)
        assert rep.live_decode["ok"] and rep.live_decode["text"] == TEXT
        # every window archived at full length (within a sample of rounding)
        for c in sched.chunks:
            side = json.loads((Path(d) / f"SIMTEST_{c.index:02d}.json").read_text())
            n_expected = round((c.rx_stop - c.rx_start) * p.modem_rate)
            assert abs(side["n_samples"] - n_expected) <= 2, (c.index, side["n_samples"], n_expected)
            assert side["frame_first"] == c.frame_first
        # the session log exists and carries the schedule
        log = json.loads((Path(d) / "SIMTEST_session.json").read_text())
        assert log["schedule"]["session_id"] == "SIMTEST" and log["chunks_keyed"] >= 1
        # offline decode of the archive: both passes and the combination
        acc, _ = decode_archive(d, sched, verbose=False)
        assert acc.repetitions == [0, 1]
        for r in (0, 1):
            out = acc.decode([r])
            assert out.ok and out.symbols == APPX_C_SYMBOLS, r
        assert acc.decode().ok and min(acc.margin_db()) > 6.0


def test_preflight_enforces_pa_limits():
    if not _have_gr():
        return
    from eve.station import Session, SessionOptions, SimRadio
    p = replace(EveParams.variant_a(), n_frames=4)
    now = time.time()
    model = D.DopplerModel(D.synthetic_table("sim", D.DSES_HASWELL, now - 60, now + 3600, range_km=375000.0))
    sched = S.build_schedule("PF", "sim", model, now + 5.0, 1296e6, params=p, repeat_count=1, chunk_s=None,
                             rtt_guard_s=0.1, t_off_min_s=0.5, t_on_max_s=3600.0)
    sess = Session(sched, model, SimRadio(p), SessionOptions(pa_in_chain=True, tx_precompensate=False))
    problems = sess.preflight()
    assert any("off time" in x for x in problems)          # 0.5 s gaps violate the 240 s PA minimum
    sess2 = Session(sched, model, SimRadio(p), SessionOptions(pa_in_chain=False, tx_precompensate=False))
    assert sess2.preflight() == []


if __name__ == "__main__":
    for n in ("test_preflight_enforces_pa_limits", "test_sim_session_decodes"):
        t = time.time()
        globals()[n]()
        print(f"PASS {n} ({time.time() - t:.1f}s)")
