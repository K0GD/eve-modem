"""The signal generator: the source's tones and gating, the schedule builder's on-windows,
and a whole generator session on the simulated radio with a live level change and a sweep."""
from __future__ import annotations

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eve import siggen as G  # noqa: E402
from eve.params import EveParams  # noqa: E402


def _sched(duration=3.0, pa=False, t_start=None):
    p = EveParams.variant_a()
    from dataclasses import replace
    p = replace(p, n_frames=4, pilot_frames=2)
    t_start = t_start or time.time() + 5.0
    return G.build_generator_schedule("SIGGENTEST", t_start, 1296e6, p, "K0PRT K0PRT", duration, pa), p


def test_generator_schedule_covers_the_duration():
    s, p = _sched(3.0)
    assert len(s.chunks) == 1
    c = s.chunks[0]
    assert abs((c.tx_stop - c.tx_start) - 3.0) < p.t_frame           # one on-window of the duration (whole frames)
    s.validate()
    s2, _ = _sched(700.0, pa=True)
    assert len(s2.chunks) == 3                                        # 300 + 300 + 100 under the amplifier limits
    for a, b in zip(s2.chunks, s2.chunks[1:]):
        assert b.tx_start - a.tx_stop >= 240.0 - 1e-6
    assert all(c.tx_stop - c.tx_start <= 300.0 + 1e-6 for c in s2.chunks)


def test_generator_source_tones_and_gating():
    s, p = _sched(2.0, t_start=1000.0)
    fs = 96000.0
    src = G.GeneratorSource(s, fs, 999.0, kind="two_tone", offset_hz=1000.0, spacing_hz=400.0, amplitude=1.0, t_end=1002.5)
    out = np.zeros(int(fs * 3.5), dtype=np.complex64)
    n = 0
    while True:
        got = src.work([], [out[n:n + 65536]])
        if got < 0:
            break
        n += got
    assert src.done and abs(n - int(np.ceil(3.5 * fs))) <= 1
    # silent before the window (device 999..1000), on during it, silent after
    assert np.all(out[:int(0.9 * fs)] == 0)
    on = out[int(1.1 * fs):int(1.9 * fs)]
    assert np.all(np.abs(on) > 0)
    spec = np.abs(np.fft.fft(on * np.hanning(on.size)))
    f = np.fft.fftfreq(on.size, 1 / fs)
    peaks = f[np.argsort(spec)[-2:]]
    assert set(np.round(peaks / 100)) == {8, 12}                    # 800 Hz and 1200 Hz: 1000 +/- 200
    assert src.samples_on == int(np.sum(np.abs(out) > 0))


def test_sweep_levels():
    assert G.SweepSpec(0, 20, 5, 1).levels() == [0, 5, 10, 15, 20]
    assert G.SweepSpec(30, 20, 5, 1).levels() == [30, 25, 20]
    assert G.SweepSpec(10, 10, 0, 1).levels() == [10]


def test_generator_session_on_the_simulated_radio(tmp_path):
    from eve.station import SimRadio, SessionOptions
    s, p = _sched(4.0)
    radio = SimRadio(p, cn0_db=None)
    steps_seen = []
    sess = G.GeneratorSession(s, radio, SessionOptions(out_dir=str(tmp_path), live_decode=False, pa_in_chain=False,
                                                       tx_precompensate=False, start_margin_s=1.0, realtime_mode=False),
                              signal="cw", offset_hz=5e3, scale_db=-6.0, tx_gain_db=10.0,
                              sweep=G.SweepSpec(10.0, 20.0, 5.0, 0.5), on_step=steps_seen.append)
    assert not sess.preflight()
    rep = sess.run()
    assert not rep.aborted and rep.chunks_keyed == 1
    whys = [d["why"] for d in sess.steps]
    assert whys[0] == "start" and whys.count("sweep") == 3
    assert [d["tx_gain_db"] for d in sess.steps if d["why"] == "sweep"] == [10.0, 15.0, 20.0]
    assert len(steps_seen) == len(sess.steps)
    assert "GENERATOR: CW" in sess.status_line()
    pdf = G.write_generator_report(sess, rep, tmp_path / "SIGGENTEST_report.pdf")
    assert pdf.exists() and pdf.stat().st_size > 1000
    sess.release()
