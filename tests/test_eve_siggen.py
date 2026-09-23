"""The signal generator: the live source (tones, signal changes, on/off), the sweep's
levels, and a whole interactive generator session on the simulated radio: key, unkey,
rekey, level and signal changes while running, the amplifier off-time refusal, a clean
stop, and the report."""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import replace

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eve import siggen as G  # noqa: E402
from eve.modem import FrameMap  # noqa: E402
from eve.params import EveParams  # noqa: E402


def _params():
    return replace(EveParams.variant_a(), n_frames=4, pilot_frames=2)


def _sched(p):
    return G.build_generator_schedule("SIGGENTEST", time.time() + 2.0, 1296e6, p, "K0PRT K0PRT")


def _pull(src, n):
    out = np.zeros(n, dtype=np.complex64)
    k = 0
    while k < n:
        got = src.work([], [out[k:]])
        assert got > 0
        k += got
    return out


def _peaks(y, fs, k):
    spec = np.abs(np.fft.fft(y * np.hanning(y.size)))
    f = np.fft.fftfreq(y.size, 1 / fs)
    return sorted(np.round(f[np.argsort(spec)[-k:]] / 100).astype(int))


def test_source_is_silent_until_keyed_and_changes_live():
    p = _params()
    s = _sched(p)
    fs = 96000.0
    src = G.GeneratorSource(fs, FrameMap(s.symbols, p, pilot=True), kind="two_tone", offset_hz=1000.0, spacing_hz=400.0)
    assert np.all(_pull(src, 4096) == 0)                    # key up: silence
    src.set_on(True)
    assert _peaks(_pull(src, 65536), fs, 2) == [8, 12]      # 800 and 1200 Hz
    src.set_signal("cw", offset_hz=3000.0)
    y = _pull(src, 65536)
    assert _peaks(y, fs, 1) == [30] and np.allclose(np.abs(y), 0.8, atol=1e-3)
    src.set_signal("eve")
    y = _pull(src, 65536)
    assert np.all(np.abs(y) > 0) and src.frames_sent
    src.set_on(False)
    assert np.all(_pull(src, 4096) == 0)
    src.stop()
    assert src.work([], [np.zeros(16, np.complex64)]) == -1 and src.done


def test_eve_waveform_hops_at_the_frame_rate():
    p = _params()
    s = _sched(p)
    fs = p.modem_rate * 4
    fmap = FrameMap(s.symbols, p, pilot=True)
    src = G.GeneratorSource(fs, fmap, kind="eve")
    src.set_on(True)
    n_frame = int(round(fs / p.r_bw))
    y = _pull(src, n_frame * 3)
    # frame 0 is the pilot: its tone sits at f_IF + pilot_tone x spacing
    f0 = p.f_if + p.pilot_tone * p.spacing
    seg = y[n_frame // 4: 3 * n_frame // 4]
    spec = np.abs(np.fft.fft(seg))
    f = np.fft.fftfreq(seg.size, 1 / fs)
    assert abs(f[np.argmax(spec)] - f0) < 2 * fs / seg.size


def test_sweep_levels():
    assert G.SweepSpec(0, 20, 5, 1).levels() == [0, 5, 10, 15, 20]
    assert G.SweepSpec(30, 20, 5, 1).levels() == [30, 25, 20]
    assert G.SweepSpec(10, 10, 0, 1).levels() == [10]


class _Keyer:
    """Records the sequencer calls."""
    name = "test"
    fault = ""
    settle_s = release_s = 0.0

    def __init__(self):
        self.keyed = False
        self.calls = []

    def key(self, on):
        self.keyed = bool(on)
        self.calls.append(bool(on))

    def status_text(self):
        return "keyed" if self.keyed else "up"

    def open(self):
        pass

    def close(self):
        pass


def test_interactive_session_on_the_simulated_radio(tmp_path, monkeypatch):
    from eve.station import SimRadio, SessionOptions
    monkeypatch.setattr(G, "PA_T_OFF_MIN_S", 1.5)          # short amplifier limits for the test
    monkeypatch.setattr(G, "PA_T_ON_MAX_S", 2.0)
    p = _params()
    s = _sched(p)
    radio = SimRadio(p, cn0_db=None)
    k = _Keyer()
    seen = []
    sess = G.GeneratorSession(s, radio, SessionOptions(out_dir=str(tmp_path), live_decode=False, pa_in_chain=True,
                                                       tx_precompensate=False, realtime_mode=False),
                              signal="cw", offset_hz=5e3, scale_db=-6.0, tx_gain_db=10.0,
                              sweep=G.SweepSpec(10.0, 20.0, 5.0, 0.2), keyer=k, on_step=seen.append)
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("rep", sess.run()))
    th.start()
    time.sleep(1.0)
    assert sess.key(True) and k.keyed and sess.tone.on       # key down
    time.sleep(0.9)                                           # the sweep runs 10, 15, 20
    sess.set_signal("two_tone", spacing_hz=2e3)               # signal change while keyed
    sess.set_level(scale_db=-10.0)
    time.sleep(1.5)                                           # the 2 s on-time limit releases the key
    assert not k.keyed and not sess.tone.on
    assert not sess.key(True)                                 # refused: amplifier cooling
    time.sleep(1.6)
    assert sess.key(True)                                     # rekey after the off time
    time.sleep(0.3)
    assert sess.key(False) and not k.keyed                    # unkey
    sess.stop()
    th.join(20)
    rep = out["rep"]
    assert not rep.aborted and sess.key_downs == 2
    assert k.calls[:4] == [True, False, True, False] and not any(k.calls[4:])   # _finish releases once more
    whys = [d["why"] for d in sess.steps]
    assert whys[0] == "start"
    assert [d["tx_gain_db"] for d in sess.steps if d["why"] == "level: sweep"][:3] == [10.0, 15.0, 20.0]
    assert "signal" in whys and "key up: amplifier on-time limit" in whys
    assert len(seen) == len(sess.steps)
    assert "two tones" in sess.status_line()
    pdf = G.write_generator_report(sess, rep, tmp_path / "SIGGENTEST_report.pdf")
    assert pdf.exists() and pdf.stat().st_size > 1000
    sess.release()
