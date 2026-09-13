"""The key line abstraction: LCUS frames, the USB relay keyer against a fake serial port,
the GPIO keyer against a fake radio, and make_keyer()."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eve import keyer as K  # noqa: E402


class FakeSerial:
    def __init__(self, port):
        self.port = port
        self.written = b""
        self.closed = False
        self.states = {1: False, 2: False}

    def write(self, b: bytes):
        self.written += b
        if len(b) == 4 and b[0] == 0xA0:
            assert b[3] == (b[0] + b[1] + b[2]) & 0xFF, "bad checksum"
            self.states[b[1]] = bool(b[2])
        return len(b)

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def read(self, n):
        return ("\r\n".join(f"CH{c}:{'ON' if s else 'OFF'}" for c, s in sorted(self.states.items())) + "\r\n").encode()

    def close(self):
        self.closed = True


def test_lcus_frames():
    assert K.lcus_frame(1, True) == bytes.fromhex("A00101A2")
    assert K.lcus_frame(1, False) == bytes.fromhex("A00100A1")
    assert K.lcus_frame(2, True) == bytes.fromhex("A00201A3")
    assert K.lcus_frame(2, False) == bytes.fromhex("A00200A2")


def test_usb_relay_keyer_with_fake_port():
    fakes = {}

    def factory(port):
        fakes[port] = FakeSerial(port)
        return fakes[port]
    k = K.UsbRelayKeyer("COM9", channel=2, serial_factory=factory)
    logs = []
    k.log = logs.append
    k.open()
    f = fakes["COM9"]
    assert f.written.endswith(bytes.fromhex("A00200A2")) or bytes.fromhex("A00200A2") in f.written   # opened with the relay off
    assert not k.keyed and k.fault == ""
    assert "CH2:OFF" in logs[0]
    k.key(True)
    assert k.keyed and f.states[2] is True and f.states[1] is False
    assert "CH2:ON" in k.query()
    k.key(False)
    assert not k.keyed and f.states[2] is False
    k.close()
    assert f.closed and not k.keyed


def test_usb_relay_keyer_reports_faults_instead_of_raising():
    class Broken(FakeSerial):
        def write(self, b):
            raise OSError("cable pulled")
    k = K.UsbRelayKeyer("COM3", channel=1, serial_factory=lambda p: Broken(p), readback=False)
    k.open()
    k.key(True)
    assert k.fault and "cable pulled" in k.fault
    assert "fault" in k.status_text()


def test_gpio_keyer_and_factory():
    class FakeRadio:
        def __init__(self):
            self.line = None

        def key(self, on):
            self.line = bool(on)
    r = FakeRadio()
    k = K.make_keyer("gpio", radio=r)
    k.key(True)
    assert r.line is True and k.keyed
    k.key(False)
    assert r.line is False
    n = K.make_keyer("none")
    n.key(True)
    assert isinstance(n, K.NullKeyer) and n.keyed
    try:
        K.make_keyer("usb_relay", port="")
        assert False, "port required"
    except ValueError:
        pass
    u = K.make_keyer("usb_relay", port="COM5  USB-SERIAL CH340 (COM5)", channel=1)
    assert isinstance(u, K.UsbRelayKeyer) and u.port == "COM5"


if __name__ == "__main__":
    for n in [k for k in dir() if k.startswith("test_")]:
        globals()[n]()
        print("PASS", n)
