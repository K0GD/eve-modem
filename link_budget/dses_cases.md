# DSES EVE link-budget cases (ORI classes, run by dses_cases.py)

Distance 40.8 million km unless noted (2026-10-24). Receiver elevation = each site's default (DSES 30 deg, Dwingeloo/Effelsberg 20 deg), clear sky, Venus radar albedo 0.152 (ORI default).

| Case | C/N0 (dB-Hz) | Tsys (K) | Margin vs 0 dB-Hz | Basis |
|---|---|---|---|---|
| DSES monostatic, 2304 MHz, 1500 W (ORI notebook case, 38.0 Mkm) (38 Mkm) | +1.7 | 76 | +1.7 | anchor: notebook prints +1.67 |
| DSES monostatic, 2304 MHz, 1500 W | +0.4 | 76 | +0.4 | 13 cm package, next apparition |
| DSES monostatic, 2400 MHz, 1500 W | +0.7 | 78 | +0.7 | 13 cm alternative band |
| DSES monostatic, 1299.5 MHz, 1500 W | -3.3 | 57 | -3.3 | 23 cm package; power TBC (O2) |
| DSES monostatic, 1299.5 MHz, 1000 W | -5.0 | 57 | -5.0 | 23 cm package; power TBC (O2) |
| DSES monostatic, 1299.5 MHz, 500 W | -8.0 | 57 | -8.0 | 23 cm package; power TBC (O2) |
| Dwingeloo monostatic, 1299.5 MHz, 1000 W (cross-check) | -0.9 | 76 | -0.9 | CAMRAS measured +0.65 dB-Hz mean, March 2025; ORI predicted +0.56 |
| DSES 1500 W transmits, Dwingeloo 25 m receives | -1.8 | 76 | -1.8 | Stockert (25 m) similar |
| DSES 1000 W transmits, Dwingeloo 25 m receives | -3.6 | 76 | -3.6 |  |
| DSES 1500 W transmits, Effelsberg 100 m receives | +11.9 | 52 | +11.9 | opportunistic; ORI's Effelsberg proposal |
| DSES 1000 W transmits, Effelsberg 100 m receives | +10.1 | 52 | +10.1 |  |
