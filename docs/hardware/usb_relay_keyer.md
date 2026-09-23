# USB relay keyer: the 2-channel USB-C relay board

> **2026-09-23: the boards arrived and are DIUSTOU DSTUR-T20 units, not LCUS clones.**
> The documentation of record is `diustou_dstur_t20.md` beside this file (with the
> vendor's PDF and example program). Differences from what is written below: the serial
> protocol runs at **115200 baud** (the board enumerates as a native USB "STM32 Virtual
> ComPort" rather than a CH340, so the rate is nominal), the status query is the framed
> `A0 0F 02 B1`, and address `0F` addresses both relays. `eve/keyer.py` handles both
> families. The wiring and sequencer sections below still apply.

Ordered 2026-09-13 (two units): Amazon B0DJVM768T, "2 Channels USB Relay Module, 5V Type C
Interface". It is the widely cloned **LCUS-2** design from chinalctech (sold as JESSINIE,
EC Buying, NOYITO, LM YN and others): a CH340 USB-to-serial chip, a small microcontroller,
two 5 V relays with status LEDs, screw terminals, USB-C on this variant. There is no
schematic published for any of the clones; the protocol below is the one every LCUS
listing documents and the one `eve/keyer.py` implements. Verify on arrival with a
terminal program before trusting it (section "Bench check").

## Protocol (from the LCUS documentation)

| Item | Value |
|---|---|
| Serial | 9600 baud, 8 data bits, no parity, 1 stop bit, no flow control (CH340 virtual COM port) |
| Frame | 4 bytes: `A0` `channel` `state` `checksum`; checksum = (A0 + channel + state) mod 256 |
| Relay 1 on / off | `A0 01 01 A2` / `A0 01 00 A1` |
| Relay 2 on / off | `A0 02 01 A3` / `A0 02 00 A2` |
| Status query | single byte `FF`; the board answers in ASCII, e.g. `CH1:OFF` `CH2:ON` (format varies by firmware; the modem only logs it) |
| Relay contacts | 10 A 250 V AC / 10 A 30 V DC per the listings; SPDT with NO, COM, NC on the terminal block |
| Power | from the USB port (the relays draw about 70 mA each when closed) |
| Driver | CH340 (Windows 10/11 have it in Windows Update; radioconda's Linux kernels have `ch341`) |

## Wiring (the modem is the sequencer; design D24, 2026-09-23)

- **Relay 1 = TX key**: COM and **NO** across the transmitter's key input (open = off,
  closed = transmit).
- **Relay 2 = LNA control**: COM and **NO** closed = LNA OFF; use COM/NC if the LNA
  wants a contact that is closed while it is active. Either way, both relays released
  (board unplugged, PC off) must leave the LNA active and the transmitter off.
- The B210 GPIO_0 (TX) and GPIO_1 (LNA) carry the same two signals in parallel for a
  station that keys through an external circuit instead of, or as well as, this board.
- Keep the relay board's USB cable short and off the RF cables; the CH340 has no
  isolation from the PC, and the relay contact is the isolation to the station.
- The board has no enclosure: a small ABS box with two cable glands, or heat-shrink over
  the board with the terminal block exposed.

## What the modem does with it

Setup → Keying: choose "USB relay board (LCUS)", pick the COM port (CH340 boards are
listed first), channel 1. The worker opens the port before the radio, commands the relay
OFF, reads the status once into the log, and from then on the session's keyer thread
closes the contact T_lead before each chunk's RF and opens it T_lag after; abort and the
watchdog open it at once. A write failure is reported as a fault in the Run tab's key
lamp text and the log; it does not stop the run by itself, the operator does.

Timing: the keying is host-timed (design document D22), so the relay's operate time
(about 10 ms) sits inside the 200 ms T_lead.

## Bench check on arrival

1. Plug in; Device Manager shows "USB-SERIAL CH340 (COMn)".
2. `python -m eve.keyer COMn` (or the Setup tab's "Test key" button): the relay clicks on
   for a second and off, and the status reply is printed.
3. Continuity across COM/NO follows the relay LED.
