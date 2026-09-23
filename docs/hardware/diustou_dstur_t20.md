# DIUSTOU USB relay DSTUR-T20 (the sequencer / key-line board)

Two units received 2026-09-23 (Amazon B0DJVM768T, "2 Channels USB Relay Module, 5V Type C
Interface"). The board is DIUSTOU's **USB/TTL Relay (TC, 4PIN, Opto, 2 Way)**, SKU
**DSTUR-T20** (en.diustou.com; $26.99; 60 g; box 72 x 72 x 36 mm). The printed card in
the box is unreadable; this note is the documentation of record, taken from the vendor's
wiki on 2026-09-23. Local copies beside this file:

- `DIUSTOU_USB_Relay_Wiki.pdf` — the wiki page as a PDF (vendor upload of 2026-05-13, 6 pp).
- `vendor/DIUSTOU_USB_Relay.py` — the vendor's Python control example (from the wiki's
  `USB_Relay.zip`); reference only, the modem has its own driver in `eve/keyer.py`.

Sources: <https://wiki.diustou.com/en/USB_Relay> (product family page, protocol, manual),
<https://en.diustou.com/usb-ttl-relay-tc-4pin-opto-2-way.html> (the DSTUR-T20 product
page), <https://wiki.diustou.com/en/USB_Relay_PRO> (the housed "PRO" variant, same
protocol). Amazon listing: <https://www.amazon.com/dp/B0DJVM768T>.

## What it is

An "industrial-grade isolated USB relay developed based on GD32F103C8T6" (vendor). The
family is 1, 2, 3, 4, and 8 channels; ours is the 2-channel one with a USB-C port and a
4-pin TTL header. Each channel has its own optocoupler isolation and overcurrent
protection; the relay coil is driven by a transistor with a freewheeling diode. The USB
side enumerates through a **CH340** USB-to-serial chip (Windows 10/11 install the driver
from Windows Update; Linux has `ch341` in the kernel; radioconda needs nothing extra).

| Item | Value (vendor) |
|---|---|
| Relay contacts | SPDT, dry contact, **10 A 250 V AC / 10 A 30 V DC resistive**; not optimized for inductive loads (add suppression externally) |
| Terminals per channel | **COM** (common), **NC** (closed to COM while the coil is off), **NO** (closed to COM while the coil is on) |
| Power | from USB, or 5 V on the TTL header; the 8-channel model adds a 5 to 32 V DC jack |
| Control | USB virtual COM port, or TTL UART on the 4-pin header (5 V, GND, TX, RX) |
| Serial | **115200 baud**, 8 data bits, no parity, 1 stop bit, no flow control (the vendor's SSCOM walkthrough mentions 9600 in one sentence; 115200 is stated as the default for every TC model and is what their program uses) |
| Indicators | one power LED, one LED per relay (lit = coil energized = COM to NO) |
| Enclosure | none on this variant (the "PRO" family adds a DIN-rail housing) |

## Protocol

Four-byte binary frames, no line ending:

```
A0  <address>  <operation>  <checksum>       checksum = (A0 + address + operation) mod 256
address:   01 = channel 1, 02 = channel 2 ... 08; 0F = all channels
operation: 00 = OFF (coil off, COM-NC), 01 = ON (coil on, COM-NO), 02 = query
```

| Action | Bytes |
|---|---|
| Channel 1 ON / OFF | `A0 01 01 A2` / `A0 01 00 A1` |
| Channel 2 ON / OFF | `A0 02 01 A3` / `A0 02 00 A2` |
| All ON / all OFF | `A0 0F 01 B0` / `A0 0F 00 AF` |
| Query channel 1 / 2 | `A0 01 02 A3` / `A0 02 02 A4` |
| Query all | `A0 0F 02 B1` |

A query is answered in ASCII, one line per channel, CR LF terminated:

```
CH1:ON\r\n        43 48 31 3A 4F 4E 0D 0A
CH2:OFF\r\n       43 48 32 3A 4F 46 46 0D 0A
```

Switch commands are not acknowledged. The vendor's program waits 200 ms after a query
and 50 ms after a switch command; the relays operate in about 10 ms.

## Differences from the LCUS protocol the modem first assumed

The board was ordered as an "LCUS-2 type". The switch frames are identical, but:

| | LCUS (chinalctech clones) | DIUSTOU DSTUR-T20 |
|---|---|---|
| Baud | 9600 | **115200** |
| Status query | single byte `FF` | framed: `A0 ch 02 sum`, or `A0 0F 02 B1` for all |
| All-channels address | none | `0F` |
| Isolation | none | optocoupler per channel |

`eve/keyer.py` (from 2026-09-23) speaks the DIUSTOU form and finds the baud rate by
probing 115200 then 9600 with a status query, so either family works.

## Use in the station

The modem uses the board as **both the key line and the sequencer** (decision of
2026-09-15: the station has no separate sequencer). One relay keys the amplifier, the
other switches the LNA; the modem orders them with guard times. The wiring and the
timing values are set in `usb_relay_keyer.md` once Alex has confirmed what the LNA side
needs and which contact (NO or NC) leaves the station safe with the PC off.

## Bench check

1. Plug in: Device Manager shows a USB-SERIAL CH340 port; `python -m eve.keyer` lists it first.
2. `python -m eve.keyer COMn 1`: opens the port, probes the baud rate, prints the status
   reply, closes relay 1 for a second (LED on, COM-NO continuity), opens it.
3. Repeat with channel 2. Then the scope on the contacts against the modem's key line
   for the sequencer timing.
