"""The key line to the sequencer: B210 GPIO or a USB relay (design document 7.2).

Two keyers, one interface:

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
        pass

    def key(self, on: bool) -> None:
        self.keyed = bool(on)

    def close(self) -> None:
        try:
            self.key(False)
        except Exception:
            pass

    def status_text(self) -> str:
        return f"{self.name}: {'KEYED' if self.keyed else 'up'}" + (f" (fault: {self.fault})" if self.fault else "")


class NullKeyer(Keyer):
    """No key line at all (bench loopback, receive-only, simulation)."""
    name = "no key line"


class GpioKeyer(Keyer):
    """The B210 GPIO line through EveRadio.key (J504 GPIO_0, FP0 line 0)."""
    name = "B210 GPIO"

    def __init__(self, radio):
        super().__init__()
        self.radio = radio

    def key(self, on: bool) -> None:
        try:
            self.radio.key(on)
            self.keyed = bool(on)
            self.fault = ""
        except Exception as e:      # noqa: BLE001
            self.fault = str(e)


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
        return serial.Serial(self.port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.3, write_timeout=0.5)

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
                    try:
                        self.ser.reset_input_buffer()
                    except Exception:
                        pass
                    self.ser.write(probe)
                    time.sleep(0.2)
                    data = self.ser.read(256)        # the 2-channel board runs the 8-channel firmware: 8 lines
                txt = data.decode("ascii", "replace").strip()
                if txt:
                    return txt
            return ""
        except Exception as e:      # noqa: BLE001
            self.fault = f"{type(e).__name__}: {e}"
            return ""

    def close(self) -> None:
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


def make_keyer(kind: str, radio=None, port: str = "", channel: int = 1, log=None) -> Keyer:
    """kind: 'none' | 'gpio' | 'usb_relay'."""
    if kind == "gpio":
        if radio is None or not hasattr(radio, "key"):
            raise ValueError("GPIO keyer needs the B210")
        k: Keyer = GpioKeyer(radio)
    elif kind == "usb_relay":
        if not port:
            raise ValueError("USB relay keyer needs a serial port (Setup: Keyer port)")
        k = UsbRelayKeyer(port.split()[0], channel)
    else:
        k = NullKeyer()
    if log is not None:
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
