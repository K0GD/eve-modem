# DSES EVE link-budget cases (ORI classes, run by dses_cases.py)

Distance 40.8 million km unless noted (2026-10-24; the ORI notebook's Skyfield ephemeris gives 40.81 on 2026-10-25). Receiver elevation = each site's default (DSES 30 deg, Dwingeloo/Effelsberg 20 deg; 34 deg at Haswell changes the DSES rows by 0.04 dB), clear sky, Venus radar albedo 0.152 (ORI static default).

| Case | C/N0 (dB-Hz) | Tsys (K) | Margin vs 0 dB-Hz | Basis |
|---|---|---|---|---|
| DSES monostatic, 2304 MHz, 1500 W (ORI notebook opening bracket) (38 Mkm) | +1.7 | 76 | +1.7 | anchor: notebook prints +1.67 |
| DSES monostatic, 2304 MHz, 1500 W | +0.4 | 76 | +0.4 | 13 cm package, next apparition |
| DSES monostatic, 2400 MHz, 1500 W | +0.7 | 78 | +0.7 | 13 cm alternative band |
| DSES monostatic, 1299.5 MHz, 1500 W | -3.3 | 57 | -3.3 | 23 cm, for comparison |
| DSES monostatic, 1299.5 MHz, 1200 W | -4.2 | 57 | -4.2 | 23 cm package: SSPA rated 1200 W CW (Alex, 2026-09-10); delivered power TBC (O2) |
| DSES monostatic, 1299.5 MHz, 1000 W | -5.0 | 57 | -5.0 | 23 cm, below rating |
| DSES monostatic, 1299.5 MHz, 500 W | -8.0 | 57 | -8.0 | 23 cm, half power |
| Dwingeloo monostatic, 1299.5 MHz, 1000 W (cross-check) | -0.9 | 76 | -0.9 | CAMRAS measured +0.65 dB-Hz mean, March 2025; ORI predicted +0.56 |
| DSES 1200 W transmits, Dwingeloo 25 m receives | -2.8 | 76 | -2.8 | Stockert (25 m) similar |
| DSES 1000 W transmits, Dwingeloo 25 m receives | -3.6 | 76 | -3.6 |  |
| DSES 1200 W transmits, Effelsberg 100 m receives | +10.9 | 52 | +10.9 | opportunistic; ORI's Effelsberg proposal |
| DSES 1000 W transmits, Effelsberg 100 m receives | +10.1 | 52 | +10.1 |  |
| 2028 (Jun 1 +/- 7 d, 44.5 Mkm): DSES monostatic, 2304 MHz, 1500 W (44 Mkm) | -1.1 | 76 | -1.1 | 13 cm package, 10-12 deg from the Sun |
| 2028: DSES monostatic, 2400 MHz, 1500 W (44 Mkm) | -0.8 | 78 | -0.8 |  |
| 2028: DSES monostatic, 1299.5 MHz, 1500 W (44 Mkm) | -4.8 | 57 | -4.8 | 23 cm, for comparison |
| 2028: DSES 1500 W 2304 MHz transmits, Effelsberg receives (44 Mkm) | +13.9 | 73 | +13.9 |  |

## Venus albedo sensitivity (ORI static 0.152 vs ORI date-resolved 0.117 for 2026-10-25, Magellan map integrated over the Earth-facing hemisphere)

| Case, 2026-10-24 | Albedo 0.152 (static) | Albedo 0.117 (date-resolved) | Difference |
|---|---|---|---|
| DSES monostatic, 2304 MHz, 1500 W | +0.4 | -0.7 | -1.1 |
| DSES monostatic, 1299.5 MHz, 1500 W | -3.3 | -4.4 | -1.1 |
| DSES monostatic, 1299.5 MHz, 1200 W | -4.2 | -5.4 | -1.1 |
| DSES monostatic, 1299.5 MHz, 1000 W | -5.0 | -6.2 | -1.1 |
| DSES monostatic, 1299.5 MHz, 500 W | -8.0 | -9.2 | -1.1 |
