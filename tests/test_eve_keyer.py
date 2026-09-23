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
            if b[2] in (0, 1):                                   # switch; 2 = query
                for ch in (self.states if b[1] == 0x0F else [b[1]]):
                    self.states[ch] = bool(b[2])
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
    assert K.relay_frame(K.RELAY_ALL, K.OP_OFF) == bytes.fromhex("A00F00AF")
    assert K.relay_frame(K.RELAY_ALL, K.OP_QUERY) == bytes.fromhex("A00F02B1")
    assert K.relay_frame(2, K.OP_QUERY) == bytes.fromhex("A00202A4")
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
    assert bytes.fromhex("A00F00AF") in f.written                # opened with every relay off
    assert not k.keyed and k.fault == ""
    assert "CH2:OFF" in logs[0] and "115200" in logs[0]        # the first probe answered
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


class FakeRadio2:
    """A radio with the two-line interface; records every write."""
    def __init__(self):
        self.calls = []

    def set_lines(self, tx, lna_active):
        self.calls.append((bool(tx), bool(lna_active)))


def test_sequencer_orders_the_two_outputs():
    fakes = {}

    def factory(port):
        fakes[port] = FakeSerial(port)
        return fakes[port]
    r = FakeRadio2()
    k = K.make_keyer("sequencer", radio=r, port="auto", serial_factory=factory, ports=["COM77"],
                     lna_guard_s=0.01, lna_release_s=0.01)
    assert isinstance(k, K.Sequencer) and not k.usb_missing and len(k.outputs) == 2
    logs = []
    k.log = logs.append
    k.open()
    f = fakes["COM77"]
    assert f.states == {1: False, 2: False} and r.calls[-1] == (False, True)     # receive state
    assert "CH1:OFF" in logs[0]
    f.written = b""
    r.calls.clear()
    k.key(True)
    # LNA relay (2) energized before the TX relay (1); GPIO likewise (lna off first, then tx)
    frames = [f.written[i:i + 4] for i in range(0, len(f.written), 4) if f.written[i] == 0xA0 and f.written[i + 2] != 2]
    assert frames[0] == bytes.fromhex("A00201A3") and frames[-1] == bytes.fromhex("A00101A2")
    assert r.calls[0] == (False, False) and r.calls[-1] == (True, False)
    assert k.keyed and not k.lna_active and "TX KEYED" in k.status_text() and "LNA OFF" in k.status_text()
    f.written = b""
    r.calls.clear()
    k.key(False)
    frames = [f.written[i:i + 4] for i in range(0, len(f.written), 4) if f.written[i] == 0xA0 and f.written[i + 2] != 2]
    assert frames[0] == bytes.fromhex("A00100A1") and frames[-1] == bytes.fromhex("A00200A2")
    assert r.calls[0] == (False, False) and r.calls[-1] == (False, True)
    assert not k.keyed and k.lna_active and f.states == {1: False, 2: False}
    assert abs(k.settle_s - (0.01 + 2 * K.UsbRelayBoard.ACK_WAIT_S)) < 1e-9
    k.close()
    assert f.closed


def test_sequencer_without_the_usb_board_falls_back_to_gpio():
    r = FakeRadio2()
    k = K.make_keyer("sequencer", radio=r, port="auto", serial_factory=lambda p: (_ for _ in ()).throw(OSError("no port")), ports=[])
    assert k.usb_missing and len(k.outputs) == 1 and "NOT FOUND" in k.name
    k.open()
    k.key(True)
    assert r.calls[-1] == (True, False)
    k.key(False)
    assert r.calls[-1] == (False, True)


def test_find_relay_board_picks_the_port_that_answers():
    class Mute(FakeSerial):
        def read(self, n):
            return b""

    def factory(port):
        return Mute(port) if port == "COM1" else FakeSerial(port)
    assert K.find_relay_board("", serial_factory=factory, ports=["COM1", "COM2"]) == "COM2"
    assert K.find_relay_board("COM2", serial_factory=factory, ports=["COM1"]) == "COM2"
    assert K.find_relay_board("", serial_factory=factory, ports=["COM1"]) == ""


def test_gpio_keyer_and_factory():
    class FakeRadio:
        def __init__(self):
            self.line = None

        def key(self, on):
            self.line = bool(on)
    r = FakeRadio()
    k = K.make_keyer("gpio", radio=r, lna_guard_s=0.0, lna_release_s=0.0)
    k.key(True)
    assert r.line is True and k.keyed
    k.key(False)
    assert r.line is False
    n = K.make_keyer("none")
    n.key(True)
    assert isinstance(n, K.NullKeyer) and n.keyed
    nothing = lambda p: (_ for _ in ()).throw(OSError("no port"))      # noqa: E731
    try:
        K.make_keyer("usb_relay", port="", serial_factory=nothing, ports=[])
        assert False, "a board is required for the usb_relay kind"
    except ValueError:
        pass
    u = K.make_keyer("usb_relay", port="COM5  USB-SERIAL CH340 (COM5)", serial_factory=FakeSerial, ports=[])
    assert isinstance(u, K.Sequencer) and u.outputs[0].port == "COM5" and not u.usb_missing


if __name__ == "__main__":
    for n in [k for k in dir() if k.startswith("test_")]:
        globals()[n]()
        print("PASS", n)
