"""Transmit one EVE tone from the B210 and measure it on the lab counter.

Stage 4's last gate (Design + ICD 5.4, O16): with the B210 locked to the station GPS
clock, the transmitted frequency must agree with the lab reference to 0.1 Hz. This tool
keys nothing (no PA, no sequencer): it drives the B210's TX/RX port straight into the
Keysight 53230A's channel 3 (option 106, 6 GHz) at a chosen TX gain, holds a constant
tone at the dial frequency plus the IF offset (tone 0 of the comb, 25 kHz above the dial
for Variant A), and reads the counter's frequency N times at its current gate time.

The counter is used the way the lab rules ask: its found state is recorded first, the
measurement runs with CONFigure/READ?, and it is handed back free-running and local in a
finally. Nothing is *RST.

Usage (project env active, or PATH prefixed with .conda/Library/bin):
    python tools/eve_txcw.py --tx-gain 60 --readings 10 [--gpsdo] [--clock external]
    python tools/eve_txcw.py --tone 2048            # the middle of the comb instead of tone 0
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

from eve import EveParams  # noqa: E402
from eve.radio import EveRadio, RadioConfig  # noqa: E402

COUNTER = ("192.168.10.40", 5025)


class Counter:
    """Minimal SCPI over the 53230A's LAN socket; one session per visit."""

    def __init__(self, addr=COUNTER, timeout=40.0):
        for attempt in range(4):        # the LAN socket is single-session; a just-closed one can refuse for a while
            try:
                self.s = socket.create_connection(addr, timeout=10.0)
                break
            except OSError as e:
                if attempt == 3:
                    raise
                print(f"counter connect failed ({e}); retrying")
                time.sleep(5)
        self.s.settimeout(timeout)

    def cmd(self, c: str) -> None:
        """Send one SCPI command (newline appended)."""
        self.s.sendall((c + "\n").encode())

    def ask(self, q: str) -> str:
        """Send a query and return the reply up to its newline, stripped."""
        self.cmd(q)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = self.s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.decode().strip()

    def close(self) -> None:
        """Close the socket (errors ignored)."""
        try:
            self.s.close()
        except Exception:
            pass


def main() -> int:
    """Open the B210, transmit the chosen comb tone at constant amplitude, optionally check
    the internal leakage on RX2, and read the counter --readings times at its found gate,
    printing each error and the mean against the 0.1 Hz gate of design 5.4. The counter's
    gate is restored and it is left running and local, the tone stopped and the radio closed,
    in a finally. Returns 0 on success, 3 GPS clock unlocked, 4 TX LO offset not honored, 5
    no reading."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="A")
    ap.add_argument("--f-dial", type=float, default=1296e6)
    ap.add_argument("--tone", type=int, default=0, help="comb tone index d (0..4095); 0 = f_dial + f_if")
    ap.add_argument("--tx-gain", type=float, default=60.0, help="B210 TX gain dB (0..89.75); the counter's channel 3 needs about -27 dBm or more")
    ap.add_argument("--amplitude", type=float, default=0.8, help="constant-envelope amplitude (the modem runs 0.8)")
    ap.add_argument("--clock", default="external")
    ap.add_argument("--serial", default="")
    ap.add_argument("--lo-offset", type=float, default=-300e3)
    ap.add_argument("--gpsdo", action="store_true", help="program and check the Leo Bodnar GPS clock first")
    ap.add_argument("--readings", type=int, default=10)
    ap.add_argument("--gate", type=float, default=0.0, help="counter gate time s (0 = use the gate found on the counter); the found gate is restored afterwards")
    ap.add_argument("--settle", type=float, default=3.0, help="seconds of tone before the first reading")
    ap.add_argument("--no-counter", action="store_true", help="just transmit for --hold seconds (manual reading)")
    ap.add_argument("--hold", type=float, default=30.0)
    ap.add_argument("--rx-monitor", action="store_true", help="also run the B210 receiver on RX2 and report the internal TX leakage at the tone (proves the transmitter without any cable)")
    ap.add_argument("--rx-gain", type=float, default=30.0)
    a = ap.parse_args()

    p = EveParams.named(a.variant)
    f_tone = a.f_dial + p.f_if + a.tone * p.spacing
    print(f"tone {a.tone}: f_dial {a.f_dial/1e6:.6f} MHz + IF {p.f_if:.2f} Hz + {a.tone} x {p.spacing:.4f} Hz "
          f"= {f_tone:,.4f} Hz expected on the air")

    if a.gpsdo:
        from eve import gpsdo
        rep = gpsdo.preflight()
        print("GPS clock:", rep["config"], "| locked", rep["locked"])
        if not rep["locked"]:
            print("GPS clock not locked; refusing to transmit", file=sys.stderr)
            return 3

    cfg = RadioConfig(serial=a.serial, f_dial_hz=a.f_dial, tx_gain_db=a.tx_gain, rx_gain_db=a.rx_gain,
                      clock_source=a.clock, require_ref_lock=(a.clock != "internal"), lo_offset_hz=a.lo_offset)
    radio = EveRadio(p, cfg)
    st = radio.open()
    print(st.summary())
    if not st.tx_lo_offset_ok:
        print("TX LO offset not honored; the tone frequency would be uncertain. Stopping.", file=sys.stderr)
        radio.close()
        return 4

    from gnuradio import analog, blocks, gr
    fs = radio.tx_rate                      # the ACTUAL rate, so the NCO phase step is exact
    f_bb = f_tone - float(radio.tx.get_center_freq(0))
    print(f"TX centre read back {radio.tx.get_center_freq(0):,.4f} Hz; baseband tone {f_bb:+,.4f} Hz at {fs:,.2f} S/s; "
          f"TX gain {radio.tx.get_gain(0):.2f} dB, amplitude {a.amplitude}")
    tb = gr.top_block("eve_txcw")
    src = analog.sig_source_c(fs, analog.GR_COS_WAVE, f_bb, a.amplitude, 0.0)
    src.set_min_output_buffer(int(fs))     # a deep edge so the sink never starves (Workbench lesson)
    tb.connect(src, radio.tx)
    rx_sink = None
    if a.rx_monitor:
        n_mon = int(radio.rx_rate * 0.5)
        rx_head = blocks.head(gr.sizeof_gr_complex, n_mon)
        rx_skip = blocks.skiphead(gr.sizeof_gr_complex, int(radio.rx_rate * a.settle))
        rx_sink = blocks.vector_sink_c()
        tb.connect(radio.rx.block, rx_skip, rx_head, rx_sink)
    tb.start()
    print(f"transmitting; settling {a.settle:.0f} s")
    time.sleep(a.settle + (0.8 if a.rx_monitor else 0.0))
    if rx_sink is not None:
        import numpy as np
        x = np.asarray(rx_sink.data(), dtype=np.complex64)
        if len(x) == 0:
            print("RX monitor: no samples")
        else:
            f_rx = f_tone - float(radio.rx.block.get_center_freq(0))
            n = len(x)
            spec = np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(n)))) ** 2 / n
            freqs = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / radio.rx_rate))
            k = int(np.argmin(np.abs(freqs - f_rx)))
            tone = spec[max(0, k - 3):k + 4].sum()
            noise = np.median(spec) * n
            peak_k = int(np.argmax(spec))
            print(f"RX monitor ({n} samples, RX gain {a.rx_gain:.0f} dB on RX2): power at the tone {10 * np.log10(tone / n + 1e-30):+.1f} dBFS, "
                  f"{10 * np.log10(tone / noise + 1e-30):+.1f} dB over the median floor; strongest bin at {freqs[peak_k]:+,.0f} Hz "
                  f"(tone expected at {f_rx:+,.0f} Hz); RX rms {np.sqrt(np.mean(np.abs(x) ** 2)):.4f}")

    counter = None
    found = {}
    try:
        if a.no_counter:
            print(f"holding the tone for {a.hold:.0f} s (read the counter by hand)")
            time.sleep(a.hold)
        else:
            counter = Counter()
            idn = counter.ask("*IDN?")
            found["conf"] = counter.ask("CONF?")
            found["gate"] = counter.ask("SENS:FREQ:GATE:TIME?")
            found["rosc"] = counter.ask("ROSC:SOUR?")
            print(f"counter {idn}; found CONF {found['conf']} gate {found['gate']} s ref {found['rosc']}")
            gate = a.gate if a.gate > 0 else (float(found["gate"]) if found["gate"] else 1.0)
            # The definitive test is a reading. (INP3:STR? only reports a level once a
            # measurement has run; asked cold it says 0 and queues -221/+263, which fooled
            # the first version of this tool into giving up at 80 dB.)
            counter.cmd("ABOR")
            counter.cmd(f"CONF:FREQ {f_tone:.1f},DEF,(@3)")
            counter.cmd(f"SENS:FREQ:GATE:TIME {gate}")
            counter.cmd("TRIG:COUN 1")
            counter.cmd("SAMP:COUN 1")
            counter.s.settimeout(gate * 4 + 10)
            print(f"first reading ({gate:.3g} s gate) ...")
            try:
                first = float(counter.ask("READ?"))
            except socket.timeout:
                counter.cmd("ABOR")
                counter.s.settimeout(30)
                counter.cmd("*CLS")
                print("no reading: the tone is below channel 3's -27 dBm floor at this gain (or not on the cable). "
                      "Raise --tx-gain (the B210 tops out near +8 dBm, under the +19 dBm limit) or check the path.")
                return 5
            strength = counter.ask("INP3:STR?")     # 0 too low (< -27 dBm), 1 weak, 2-3 good, 4 too high (> +19 dBm)
            print(f"first reading {first:,.4f} Hz (error {first - f_tone:+.4f} Hz); channel 3 signal strength "
                  f"{strength} = {{'+0': 'too low', '+1': 'weak', '+2': 'good', '+3': 'good', '+4': 'too high'}}.get(strength, '?')")
            counter.cmd(f"SAMP:COUN {a.readings}")
            counter.s.settimeout(max(40.0, gate * a.readings * 1.5 + 10))
            print(f"reading {a.readings} x {gate:.3g} s gate ...")
            raw = counter.ask("READ?")
            vals = [float(x) for x in raw.split(",")]
            if any(v > 1e30 for v in vals):
                print("counter returned 9.91E+37 (no reading) for some samples: level marginal")
                vals = [v for v in vals if v < 1e30]
            if not vals:
                return 5
            errs = [v - f_tone for v in vals]
            for i, (v, e) in enumerate(zip(vals, errs)):
                print(f"  {i:2d}: {v:,.4f} Hz  error {e:+.4f} Hz  ({e / f_tone * 1e9:+.3f} ppb)")
            import statistics
            m = statistics.mean(errs)
            sd = statistics.pstdev(errs) if len(errs) > 1 else 0.0
            print(f"mean error {m:+.4f} Hz ({m / f_tone * 1e9:+.3f} ppb), spread {sd:.4f} Hz rms over {len(vals)} readings; "
                  f"gate {gate:g} s; counter on its {found['rosc']} reference; "
                  f"{'PASS' if abs(m) <= 0.1 else 'FAIL'} against the 0.1 Hz gate of 5.4")
            err = counter.ask("SYST:ERR?")
            if not err.startswith("+0"):
                print("counter error:", err)
    finally:
        if counter is not None:
            try:
                counter.cmd("ABOR")
                counter.cmd("SAMP:COUN 1")
                if found.get("gate"):
                    counter.cmd(f"SENS:FREQ:GATE:TIME {float(found['gate'])}")
                counter.cmd("INIT")
                counter.cmd("SYST:LOC")
            finally:
                counter.close()
        tb.stop()
        tb.wait()
        radio.close()
        print("tone off, radio closed, counter handed back running and local")
    return 0


if __name__ == "__main__":
    sys.exit(main())
