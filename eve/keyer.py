"""The station's transmit/receive switching: the modem IS the sequencer (design document
7.2, decision D24 of 2026-09-23). Two signals, driven on two outputs in parallel:

  TX key    relay 1 of the USB relay board  /  B210 GPIO_0     energized / high = transmit
  LNA       relay 2 of the USB relay board  /  B210 GPIO_1     energized / high = LNA OFF

Both de-energized (board unplugged, PC off, GPIO low) = LNA active, transmitter off: the
feed is in receive mode whenever nothing is driving it. The GPIO pins need an external
circuit; a typical use is GPIO_0 alone driving a DB6NT-style external sequencer, in which
case GPIO_1 is simply ignored. The USB relay board is found by itself on whatever COM port
it has today; if it is absent the run continues on the GPIO lines and the operator is told.

Sequence: LNA off -> lna_guard -> TX on -> (T_lead) -> RF ... RF off -> (T_lag) -> TX off
-> lna_release -> LNA on. Abort and finish take the same way out, transmitter first.

The building blocks, one interface (Keyer.key(on) is the whole sequence):

  GpioKeyer(radio)                the B210's J504 GPIO_0 through an isolated driver (the
                                  original design; needs the header brought out)
  UsbRelayKeyer(port, channel)    a USB serial relay board: the DIUSTOU DSTUR-T20 (2
                                  channels, USB-C, optocoupler isolated; Amazon
                                  B0DJVM768T, two received 2026-09-23) or any LCUS-type
                                  clone. Its normally-open contact keys the sequencer.
                                  The keying is host-timed either way (D22), so the
                                  relay costs nothing in timing beyond its own ~10 ms
                                  operate time, which T_lead covers.

Protocol (DIUSTOU "USB Relay (TC, n, Opto)" family, docs/hardware/diustou_dstur_t20.md;
the LCUS clones use the same switch frames):
  8N1, no flow control; 115200 baud on the DIUSTOU boards, 9600 on LCUS clones (open()
  probes 115200 then 9600 with a status query and keeps the one that answers).
  Four-byte frames:
      0xA0  address  operation  checksum      checksum = (0xA0 + address + operation) & 0xFF
  address 0x01.. = channel, 0x0F = all channels; operation 0x01 = relay closed (on),
  0x00 = open (off), 0x02 = query.
      relay 1 on   A0 01 01 A2        relay 1 off  A0 01 00 A1
      relay 2 on   A0 02 01 A3        relay 2 off  A0 02 00 A2
      all off      A0 0F 00 AF        query all    A0 0F 02 B1
  A query is answered in ASCII, one line per channel, "CH1:ON\r\n" / "CH2:OFF\r\n".
  LCUS clones answer a single 0xFF byte instead; query() tries the frame first, then
  0xFF. The reply is used here only as a read-back check; keying never waits on it.

A keyer never raises out of key(): a failure to key is reported through .fault and the
session's watchdog / operator, not by killing the run mid-chunk. Opening the port is
where problems surface (wrong port, board unplugged), before any RF.
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional

LCUS_BAUD = 9600
RELAY_BAUDS = (115200, 9600)        # DIUSTOU boards, then LCUS clones
LCUS_HEAD = 0xA0
RELAY_ALL = 0x0F
OP_OFF, OP_ON, OP_QUERY = 0x00, 0x01, 0x02


def relay_frame(address: int, op: int) -> bytes:
    """A0 address op checksum: address 1..8 or RELAY_ALL, op OP_OFF / OP_ON / OP_QUERY."""
    if address != RELAY_ALL and address not in range(1, 9):
        raise ValueError("relay channel is 1..8 or 0x0F (all)")
    if op not in (OP_OFF, OP_ON, OP_QUERY):
        raise ValueError("relay operation is 0 (off), 1 (on) or 2 (query)")
    return bytes([LCUS_HEAD, address, op, (LCUS_HEAD + address + op) & 0xFF])


def lcus_frame(channel: int, on: bool) -> bytes:
    """Switch frame for one channel (the LCUS name is kept for the callers and tests)."""
    return relay_frame(channel, OP_ON if on else OP_OFF)


class Keyer:
    """Interface. keyed is the last commanded state; fault is the last error text or ''."""
    name = "none"

    def __init__(self):
        self.keyed = False
        self.fault = ""
        self.log = lambda s: None

    def open(self) -> None:
        """Open the hardware, if any; problems surface here, before any RF."""
        pass

    def key(self, on: bool) -> None:
        """Key (True) or release (False) the transmitter; for a Sequencer this is the whole
        LNA / guard / TX sequence. Records the commanded state in `keyed` and never raises:
        a failure goes to `fault`. The base class only records the state."""
        self.keyed = bool(on)

    def close(self) -> None:
        """Release the key line (errors swallowed), then let subclasses release the hardware."""
        try:
            self.key(False)
        except Exception:
            pass

    def status_text(self) -> str:
        """One line for the display and the log: name, KEYED or up, and any fault."""
        return f"{self.name}: {'KEYED' if self.keyed else 'up'}" + (f" (fault: {self.fault})" if self.fault else "")


class NullKeyer(Keyer):
    """No key line at all (bench loopback, receive-only, simulation)."""
    name = "no key line"


class GpioKeyer(Keyer):
    """The B210 GPIO line through EveRadio.key (J504 GPIO_0, FP0 line 0). Kept for the
    tools and tests; the station uses Sequencer([GpioLines(radio)])."""
    name = "B210 GPIO"

    def __init__(self, radio):
        super().__init__()
        self.radio = radio

    def key(self, on: bool) -> None:
        """Drive GPIO_0 through the radio's key(); a failure is stored in `fault`, not raised."""
        try:
            self.radio.key(on)
            self.keyed = bool(on)
            self.fault = ""
        except Exception as e:      # noqa: BLE001
            self.fault = str(e)


# ---------------------------------------------------------------------------------------
# the two station signals on their two kinds of output
# ---------------------------------------------------------------------------------------
class Output:
    """One place the TX and LNA signals go. set_tx(True) = transmitter keyed;
    set_lna(True) = LNA active (the de-energized / low state)."""
    name = "output"
    latency_s = 0.0          # worst-case time one set_tx / set_lna takes (the sequencer allows for it)

    def __init__(self):
        self.fault = ""
        self.tx = False
        self.lna = True

    def open(self) -> None:
        """Open the hardware behind this output; the base class has none."""
        pass

    def set_tx(self, on: bool) -> None:
        """Drive the TX key signal: True = transmitter keyed. The base class records it only."""
        self.tx = bool(on)

    def set_lna(self, active: bool) -> None:
        """Drive the LNA signal: True = LNA active (the released / low state), False = LNA
        off (energized / high). The base class records it only."""
        self.lna = bool(active)

    def close(self) -> None:
        """Release the hardware behind this output; the base class has none."""
        pass


class GpioLines(Output):
    """B210 J504: GPIO_0 = TX key (high = transmit), GPIO_1 = LNA (high = LNA off). Through
    EveRadio.set_lines when the radio has it (both bits at once), else EveRadio.key for
    the TX bit alone (older radio objects, fakes)."""
    name = "B210 GPIO"

    def __init__(self, radio):
        super().__init__()
        self.radio = radio
        if getattr(radio, "sim", False):
            self.name = "simulated GPIO"      # the software radio records the lines instead of driving pins

    def _write(self) -> None:
        try:
            if hasattr(self.radio, "set_lines"):
                self.radio.set_lines(self.tx, self.lna)
            else:
                self.radio.key(self.tx)
            self.fault = ""
        except Exception as e:      # noqa: BLE001
            self.fault = f"{type(e).__name__}: {e}"

    def set_tx(self, on: bool) -> None:
        """Set GPIO_0 (high = transmit) and rewrite both lines to the radio."""
        self.tx = bool(on)
        self._write()

    def set_lna(self, active: bool) -> None:
        """Set GPIO_1 (high = LNA off, low = LNA active) and rewrite both lines to the radio."""
        self.lna = bool(active)
        self._write()


class UsbRelayBoard(Output):
    """The DIUSTOU DSTUR-T20 (or an LCUS clone): relay 1 = TX key, relay 2 = LNA control.
    Relay 2 energized = LNA OFF, so both relays off = receive."""
    name = "USB relay board"
    TX_RELAY, LNA_RELAY = 1, 2
    ACK_WAIT_S = 0.25        # the DIUSTOU board answers each switch frame with "CHn:STATE" ~125 ms later
    latency_s = ACK_WAIT_S

    def __init__(self, port: str, serial_factory=None, baud: Optional[int] = None):
        super().__init__()
        self.port = port
        self.name = f"USB relay board {port}"
        self._k = UsbRelayKeyer(port, self.TX_RELAY, serial_factory=serial_factory, readback=True, baud=baud)

    @property
    def log(self):
        """The log callable, kept on the underlying UsbRelayKeyer that owns the port."""
        return self._k.log

    @log.setter
    def log(self, fn):
        self._k.log = fn

    def open(self) -> None:
        """Open the port through the underlying keyer (baud probe, every relay off, reply
        logged) and take over its fault text."""
        self._k.open()                       # probes the baud rate, every relay off, logs the reply
        self.fault = self._k.fault

    def _set(self, relay: int, energize: bool) -> None:
        """Send the switch frame and wait for the board's acknowledgment line for that relay
        (a frame sent while the board is still talking can be missed; measured 2026-09-23).
        No acknowledgment within ACK_WAIT_S: send once more, then carry on (the state is
        read back by the session's status query anyway)."""
        want = f"CH{relay}:{'ON' if energize else 'OFF'}"
        try:
            for attempt in range(2):
                with self._k._lock:
                    try:
                        self._k.ser.reset_input_buffer()
                    except Exception:
                        pass
                    self._k.ser.write(relay_frame(relay, OP_ON if energize else OP_OFF))
                    try:
                        self._k.ser.flush()
                    except Exception:
                        pass
                    seen = b""
                    t0 = time.monotonic()
                    while time.monotonic() - t0 < self.ACK_WAIT_S:
                        chunk = self._k.ser.read(256)
                        if chunk:
                            seen += chunk
                            if want.encode() in seen:
                                break
                if want.encode() in seen:
                    break
                self._k.log(f"USB relay {self.port}: no acknowledgment for {want}, sending again" if attempt == 0
                            else f"USB relay {self.port}: {want} still not acknowledged")
            self.fault = ""
        except Exception as e:      # noqa: BLE001
            self.fault = f"{type(e).__name__}: {e}"

    def set_tx(self, on: bool) -> None:
        """Energize (True) or release relay 1, the TX key, with acknowledgment."""
        self.tx = bool(on)
        self._set(self.TX_RELAY, self.tx)

    def set_lna(self, active: bool) -> None:
        """LNA active (True) releases relay 2; LNA off energizes it, with acknowledgment."""
        self.lna = bool(active)
        self._set(self.LNA_RELAY, not self.lna)

    def query(self) -> str:
        """The board's status reply (one 'CHn:ON' / 'CHn:OFF' line per channel) or ''."""
        return self._k.query()

    def close(self) -> None:
        """Release both relays (the receive state) and close the port."""
        try:
            self._set(self.TX_RELAY, False)
            self._set(self.LNA_RELAY, False)
        except Exception:
            pass
        self._k.close()


class Sequencer(Keyer):
    """The station sequencer: drives every Output in parallel in the safe order.
    key(True): LNA off, wait lna_guard_s, TX on. key(False): TX off, wait lna_release_s,
    LNA on. `settle_s` (= lna_guard_s) is what the session adds to T_lead so the RF still
    starts T_lead after the transmitter is keyed."""
    name = "sequencer"

    def __init__(self, outputs: List[Output], lna_guard_s: float = 0.05, lna_release_s: float = 0.05,
                 usb_missing: bool = False):
        super().__init__()
        self.outputs = list(outputs)
        self.lna_guard_s = float(lna_guard_s)
        self.lna_release_s = float(lna_release_s)
        self.usb_missing = usb_missing
        self.lna_active = True
        self.name = "sequencer: " + (" + ".join(o.name for o in self.outputs) if self.outputs else "no outputs")
        if usb_missing:
            self.name += " (USB relay board NOT FOUND)"

    @property
    def settle_s(self) -> float:
        """What the session adds ahead of T_lead: the LNA guard plus the slowest output's
        latency for the two switches key(True) performs."""
        slow = max((o.latency_s for o in self.outputs), default=0.0)
        return self.lna_guard_s + 2.0 * slow

    @property
    def release_s(self) -> float:
        """How long key(False) takes after T_lag: the LNA release plus two switches."""
        slow = max((o.latency_s for o in self.outputs), default=0.0)
        return self.lna_release_s + 2.0 * slow

    def open(self) -> None:
        """Open every output, then put the station in receive (TX released, LNA active)
        whatever the hardware was left at; the outputs' faults are collected into `fault`."""
        for o in self.outputs:
            o.log = self.log
            o.open()
        self._collect()
        # receive state to begin with, whatever the hardware was left at
        for o in self.outputs:
            o.set_tx(False)
            o.set_lna(True)
        self._collect()

    def _collect(self) -> None:
        self.fault = "; ".join(f"{o.name}: {o.fault}" for o in self.outputs if o.fault)

    def key(self, on: bool) -> None:
        """The sequence of design document 7.2 (D24) on every output in parallel. on:
        LNA off, sleep lna_guard_s, TX on. off: TX off, sleep lna_release_s, LNA on.
        The transmitter is always the last thing on and the first thing off. Output
        faults are collected into `fault`, never raised."""
        if on:
            for o in self.outputs:
                o.set_lna(False)
            self.lna_active = False
            time.sleep(self.lna_guard_s)
            for o in self.outputs:
                o.set_tx(True)
            self.keyed = True
        else:
            for o in self.outputs:
                o.set_tx(False)
            self.keyed = False
            time.sleep(self.lna_release_s)
            for o in self.outputs:
                o.set_lna(True)
            self.lna_active = True
        self._collect()

    def close(self) -> None:
        """Release the transmitter and restore the LNA (the key(False) sequence), then
        close every output."""
        try:
            self.key(False)
        except Exception:
            pass
        for o in self.outputs:
            try:
                o.close()
            except Exception:
                pass

    def status_text(self) -> str:
        """'TX KEYED / off, LNA active / OFF | <outputs>' plus any fault."""
        s = f"TX {'KEYED' if self.keyed else 'off'}, LNA {'active' if self.lna_active else 'OFF'} | {self.name}"
        return s + (f" (fault: {self.fault})" if self.fault else "")


def sequencer_gap_s(lna_guard_s: float, lna_release_s: float, usb_board: bool = True,
                    t_lead_s: float = 0.2, t_lag_s: float = 0.1) -> float:
    """The silence two chunks need between their RF for the sequencer: key lead and lag,
    the LNA guard and release, and the USB board's acknowledged switches (two each way).
    The session's preflight checks exactly this; the Setup page sets the off time from it."""
    ack = UsbRelayBoard.ACK_WAIT_S if usb_board else 0.0
    return t_lead_s + t_lag_s + lna_guard_s + lna_release_s + 4.0 * ack


def find_relay_board(port_hint: str = "", serial_factory=None, ports=None) -> str:
    """The COM port of a relay board that answers a status query: the hint first (if it
    answers), then every port that looks like one (DIUSTOU's STM32-style VCP, a CH340).
    Returns '' when none answers. The board may sit on a different port every day."""
    candidates: List[str] = []
    if port_hint and port_hint.strip() and port_hint.strip().lower() != "auto":
        candidates.append(port_hint.split()[0])
    if ports is None:
        try:
            from serial.tools import list_ports
            for p in list_ports.comports():
                d = (p.description or "").upper()
                if (p.vid, p.pid) in RELAY_VIDPIDS or "CH340" in d or "USB-SERIAL" in d or "VIRTUAL COMPORT" in d:
                    candidates.append(p.device)
        except Exception:
            pass
    else:
        candidates.extend(ports)
    seen = set()
    for port in candidates:
        if port in seen:
            continue
        seen.add(port)
        k = UsbRelayKeyer(port, 1, serial_factory=serial_factory, readback=False)
        for baud in RELAY_BAUDS:                 # DIUSTOU at 115200, LCUS clones at 9600
            try:
                k.ser = k._open_port(baud)
                time.sleep(0.05)
                txt = k.query()                  # framed query first, then the clones' 0xFF
                k.ser.close()
            except Exception:
                break
            if txt.upper().startswith("CH"):
                return port
    return ""


class UsbRelayKeyer(Keyer):
    """An LCUS-type USB serial relay board. `port` like 'COM7' or '/dev/ttyUSB0';
    `channel` 1 or 2; `serial_factory` lets tests substitute a fake port."""
    name = "USB relay"

    def __init__(self, port: str, channel: int = 1, serial_factory=None, readback: bool = True,
                 baud: Optional[int] = None):
        super().__init__()
        self.port = port
        self.channel = int(channel)
        self._factory = serial_factory
        self.readback = readback
        self.baud = baud                    # None = probe RELAY_BAUDS at open()
        self.ser = None
        self._lock = threading.Lock()
        self.name = f"USB relay {port} ch{channel}"

    def _open_port(self, baud: int):
        if self._factory is not None:
            return self._factory(self.port)
        import serial
        return serial.Serial(self.port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.05, write_timeout=0.5)

    def open(self) -> None:
        """Open the port, find the baud rate (a status query must answer), put every relay
        in a known OFF state before any RF, and log the board's reply."""
        bauds = (self.baud,) if self.baud else RELAY_BAUDS
        txt = ""
        for i, baud in enumerate(bauds):
            self.ser = self._open_port(baud)
            time.sleep(0.05)
            txt = self.query()
            if txt.upper().startswith("CH") or i == len(bauds) - 1:
                self.baud = baud
                break
            try:
                self.ser.close()
            except Exception:
                pass
        try:
            self._write(relay_frame(RELAY_ALL, OP_OFF))  # a known state before any RF
            self.fault = ""
        except Exception as e:      # noqa: BLE001
            self.fault = f"{type(e).__name__}: {e}"
        self.keyed = False
        if self.readback:
            self.log(f"USB relay on {self.port} at {self.baud} baud: "
                     f"{txt.replace(chr(13), '').replace(chr(10), ' ').strip() or 'no status reply (the board keys anyway)'}")

    def _write(self, frame: bytes) -> None:
        with self._lock:
            self.ser.write(frame)
            try:
                self.ser.flush()
            except Exception:
                pass

    def key(self, on: bool) -> None:
        """Send the switch frame for this keyer's channel (no wait for the reply); `fault`
        is set, not raised, if the port is not open or the write fails."""
        if self.ser is None:
            self.fault = "port not open"
            return
        try:
            self._write(lcus_frame(self.channel, on))
            self.keyed = bool(on)
            self.fault = ""
        except Exception as e:      # noqa: BLE001
            self.fault = f"{type(e).__name__}: {e}"

    def query(self) -> str:
        """Ask the board for its relay states; returns the ASCII reply ('CH1:ON\r\nCH2:OFF')
        or ''. DIUSTOU frame first (A0 0F 02 B1), then the LCUS clones' single 0xFF."""
        if self.ser is None:
            return ""
        try:
            for probe in (relay_frame(RELAY_ALL, OP_QUERY), b"\xff"):
                with self._lock:
                    # The DIUSTOU board also acknowledges every switch frame with a "CHn:STATE"
                    # line about 100 ms later (measured 2026-09-23), so drain first, then read
                    # until the line goes quiet (the reply is 8 lines on this firmware).
                    time.sleep(0.3)             # two switch acknowledgments, ~125 ms each, may still be coming
                    try:
                        self.ser.reset_input_buffer()
                    except Exception:
                        pass
                    self.ser.write(probe)
                    data = b""
                    t0 = time.monotonic()
                    while time.monotonic() - t0 < 0.8:
                        chunk = self.ser.read(256)
                        if chunk:
                            data += chunk
                        elif data:
                            break
                txt = data.decode("ascii", "replace").strip()
                if txt:
                    return txt
            return ""
        except Exception as e:      # noqa: BLE001
            self.fault = f"{type(e).__name__}: {e}"
            return ""

    def close(self) -> None:
        """Release the relay, then close the serial port."""
        super().close()
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None


RELAY_VIDPIDS = {(0x1A86, 0x7523), (0x0483, 0x5740)}     # CH340; STM32/GD32 virtual COM port


def list_serial_ports() -> List[str]:
    """COM ports with a description, relay boards first: the DIUSTOU DSTUR-T20 enumerates
    as an STM32-style virtual COM port (VID 0483 PID 5740, Windows calls it 'USB Serial
    Device'), LCUS clones as a CH340."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    ports = []
    for p in list_ports.comports():
        d = (p.description or "").upper()
        first = (p.vid, p.pid) in RELAY_VIDPIDS or "CH340" in d or "USB-SERIAL" in d or "VIRTUAL COMPORT" in d
        ports.append((0 if first else 1, f"{p.device}  {p.description}"))
    return [d for _, d in sorted(ports)]


def make_keyer(kind: str, radio=None, port: str = "", channel: int = 1, log=None,
               lna_guard_s: float = 0.05, lna_release_s: float = 0.05, serial_factory=None, ports=None) -> Keyer:
    """kind: 'none' | 'sequencer' (USB relay board if one answers + B210 GPIO lines when the
    radio has them) | 'gpio' (GPIO lines only) | 'usb_relay' (the board only, must exist).
    The USB board's port is `port` ('' or 'auto' = search). A sequencer whose USB board was
    wanted but not found carries usb_missing=True: the caller tells the operator."""
    if log is None:
        log = lambda s: None       # noqa: E731
    if kind in ("sequencer", "usb_relay"):
        found = find_relay_board(port, serial_factory=serial_factory, ports=ports)
        outputs: List[Output] = []
        if found:
            outputs.append(UsbRelayBoard(found, serial_factory=serial_factory))
        elif kind == "usb_relay":
            raise ValueError("no USB relay board answered on any port (Setup: Keying)")
        gpio_ok = radio is not None and (hasattr(radio, "set_lines") or hasattr(radio, "key"))   # real or simulated
        if kind == "sequencer" and gpio_ok:
            outputs.append(GpioLines(radio))
        k: Keyer = Sequencer(outputs, lna_guard_s, lna_release_s, usb_missing=(kind == "sequencer" and not found))
    elif kind == "gpio":
        if radio is None or not (hasattr(radio, "set_lines") or hasattr(radio, "key")):
            raise ValueError("GPIO keyer needs the B210")
        k = Sequencer([GpioLines(radio)], lna_guard_s, lna_release_s)
    else:
        k = NullKeyer()
    k.log = log
    return k


def _main(argv=None) -> int:
    """python -m eve.keyer [COMn [channel]]  - list ports, or click the relay once."""
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="USB relay keyer bench check")
    ap.add_argument("port", nargs="?", default="")
    ap.add_argument("channel", nargs="?", type=int, default=1)
    ap.add_argument("--hold", type=float, default=1.0)
    a = ap.parse_args(argv)
    if not a.port:
        print("serial ports:")
        for d in list_serial_ports():
            print("  " + d)
        return 0
    k = UsbRelayKeyer(a.port, a.channel)
    k.log = print
    k.open()
    print("status:", repr(k.query()))
    print("key DOWN")
    k.key(True)
    time.sleep(a.hold)
    print("status:", repr(k.query()))
    print("key up")
    k.key(False)
    time.sleep(0.2)
    print("status:", repr(k.query()), "| fault:", k.fault or "none")
    k.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
