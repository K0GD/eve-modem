"""The key line to the sequencer: B210 GPIO or a USB relay (design document 7.2).

Two keyers, one interface:

  GpioKeyer(radio)                the B210's J504 GPIO_0 through an isolated driver (the
                                  original design; needs the header brought out)
  UsbRelayKeyer(port, channel)    an LCUS-type USB serial relay board (CH340, 9600 8N1):
                                  the 2-channel USB-C board Rick ordered 2026-09-13
                                  (Amazon B0DJVM768T). Its normally-open contact keys the
                                  sequencer. The keying is host-timed either way (D22),
                                  so the relay costs nothing in timing beyond its own
                                  ~10 ms operate time, which T_lead covers.

LCUS protocol (chinalctech LCUS-1/LCUS-2 family, as sold under many brand names):
  9600 baud, 8N1, no flow control. Four-byte frames:
      0xA0  channel  state  checksum        checksum = (0xA0 + channel + state) & 0xFF
  channel 0x01 or 0x02, state 0x01 = relay closed (on), 0x00 = open (off).
      relay 1 on   A0 01 01 A2        relay 1 off  A0 01 00 A1
      relay 2 on   A0 02 01 A3        relay 2 off  A0 02 00 A2
  A single byte 0xFF asks for the state; the board answers with ASCII text such as
  "CH1:ON" / "CH1:OFF" (one line per channel on the 2-channel board). The reply is used
  here only as a read-back check; keying never waits on it.

A keyer never raises out of key(): a failure to key is reported through .fault and the
session's watchdog / operator, not by killing the run mid-chunk. Opening the port is
where problems surface (wrong port, board unplugged), before any RF.
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional

LCUS_BAUD = 9600
LCUS_HEAD = 0xA0


def lcus_frame(channel: int, on: bool) -> bytes:
    if channel not in (1, 2, 3, 4):
        raise ValueError("LCUS channel is 1..4")
    state = 1 if on else 0
    return bytes([LCUS_HEAD, channel, state, (LCUS_HEAD + channel + state) & 0xFF])


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

    def __init__(self, port: str, channel: int = 1, serial_factory=None, readback: bool = True):
        super().__init__()
        self.port = port
        self.channel = int(channel)
        self._factory = serial_factory
        self.readback = readback
        self.ser = None
        self._lock = threading.Lock()
        self.name = f"USB relay {port} ch{channel}"

    def open(self) -> None:
        if self._factory is not None:
            self.ser = self._factory(self.port)
        else:
            import serial
            self.ser = serial.Serial(self.port, LCUS_BAUD, bytesize=8, parity="N", stopbits=1, timeout=0.3, write_timeout=0.5)
        time.sleep(0.05)
        self.key(False)                      # a known state before any RF
        if self.readback:
            txt = self.query()
            self.log(f"USB relay on {self.port}: {txt or 'no status reply (the board keys anyway)'}")

    def key(self, on: bool) -> None:
        if self.ser is None:
            self.fault = "port not open"
            return
        try:
            with self._lock:
                self.ser.write(lcus_frame(self.channel, on))
                try:
                    self.ser.flush()
                except Exception:
                    pass
            self.keyed = bool(on)
            self.fault = ""
        except Exception as e:      # noqa: BLE001
            self.fault = f"{type(e).__name__}: {e}"

    def query(self) -> str:
        """Ask the board for its relay states (0xFF); returns the ASCII reply or ''."""
        if self.ser is None:
            return ""
        try:
            with self._lock:
                try:
                    self.ser.reset_input_buffer()
                except Exception:
                    pass
                self.ser.write(b"\xff")
                time.sleep(0.15)
                data = self.ser.read(64)
            return data.decode("ascii", "replace").strip()
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


def list_serial_ports() -> List[str]:
    """COM ports with a description, CH340 boards first (the LCUS chip)."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    ports = []
    for p in list_ports.comports():
        desc = f"{p.device}  {p.description}"
        ports.append((0 if "CH340" in (p.description or "").upper() or "USB-SERIAL" in (p.description or "").upper() else 1, desc))
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
