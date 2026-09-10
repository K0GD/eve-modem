#!/usr/bin/env python3
"""DSES EVE link-budget cases, computed with ORI's own link-budget classes.

Every row of the design document's link-budget table (section 2.2) comes from
here, so it can be re-run when a station number changes:

    python link_budget/dses_cases.py            # prints the table, writes dses_cases.md

The arithmetic is ORI's (ori_link_budget.py, extracted verbatim from
Link_Budget_Modeling.ipynb). This file only builds the cases: DSES monostatic
on each band and power, bistatic with a European receiver, and the Dwingeloo
cross-check against the CAMRAS measurement. Distances: 38,000,000 km is the
notebook's generic minimum; 40,800,000 km is Earth-Venus on 2026-10-24.
"""
from dataclasses import replace
from pathlib import Path

import ori_link_budget as L

D_NOTEBOOK = 38_000_000      # km, ORI notebook "minimum distance"
D_2026 = 40_800_000          # km, 2026-10-24 (0.2727 AU)
DESIGN_POINT_DBHZ = 0.0      # Pete Wyckoff's C/N0 design point


def cnr(rx, tx=None, dist_km=D_2026, elev=None):
    """C/N0 in 1 Hz (dB-Hz) and Tsys (K) for receiver params rx, transmitter
    params tx (None = monostatic), at distance dist_km."""
    calc = L.EVELinkBudget(rx, tx_params=tx)
    r = calc.calculate_link_budget(dist_km, elevation_deg=elev)
    return r["cnr_db_1hz"], r["system_noise_temperature"]


def dses(freq_mhz, power_w, lna_nf_db=None, receive_only=False):
    p = L.DSESLinkParameters()
    kw = dict(tx_frequency_mhz=freq_mhz, tx_power_w=power_w, receive_only=receive_only)
    if lna_nf_db is not None:
        kw["lna_noise_figure_db"] = lna_nf_db
        kw["lna_noise_figure_K"] = 290 * (10 ** (lna_nf_db / 10) - 1)
    return replace(p, **kw)


def european(cls, freq_mhz):
    p = cls()
    return replace(p, tx_frequency_mhz=freq_mhz, receive_only=True)


def main():
    rows = []

    def add(label, rx, tx=None, dist=D_2026, basis=""):
        c, t = cnr(rx, tx, dist)
        rows.append((label, c, t, dist, basis))

    # --- reproduce the notebook's own DSES number (sanity anchor) ----------------
    add("DSES monostatic, 2304 MHz, 1500 W (ORI notebook case, 38.0 Mkm)",
        dses(2304, 1500), dist=D_NOTEBOOK, basis="anchor: notebook prints +1.67")
    # --- 13 cm cases at the 2026 distance -----------------------------------------
    add("DSES monostatic, 2304 MHz, 1500 W", dses(2304, 1500), basis="13 cm package, next apparition")
    add("DSES monostatic, 2400 MHz, 1500 W", dses(2400, 1500), basis="13 cm alternative band")
    # --- 23 cm cases (the 2026 attempt) ------------------------------------------
    add("DSES monostatic, 1299.5 MHz, 1500 W", dses(1299.5, 1500), basis="23 cm package; power TBC (O2)")
    add("DSES monostatic, 1299.5 MHz, 1000 W", dses(1299.5, 1000), basis="23 cm package; power TBC (O2)")
    add("DSES monostatic, 1299.5 MHz, 500 W", dses(1299.5, 500), basis="23 cm package; power TBC (O2)")
    # --- cross-check against CAMRAS ------------------------------------------------
    dw = L.DwingelooLinkParameters()
    add("Dwingeloo monostatic, 1299.5 MHz, 1000 W (cross-check)", dw,
        basis="CAMRAS measured +0.65 dB-Hz mean, March 2025; ORI predicted +0.56")
    # --- bistatic: DSES transmits at 1299.5 MHz, a European station receives ----
    add("DSES 1500 W transmits, Dwingeloo 25 m receives", european(L.DwingelooLinkParameters, 1299.5),
        tx=dses(1299.5, 1500), basis="Stockert (25 m) similar")
    add("DSES 1000 W transmits, Dwingeloo 25 m receives", european(L.DwingelooLinkParameters, 1299.5),
        tx=dses(1299.5, 1000), basis="")
    add("DSES 1500 W transmits, Effelsberg 100 m receives", european(L.EffelsbergLinkParameters, 1299.5),
        tx=dses(1299.5, 1500), basis="opportunistic; ORI's Effelsberg proposal")
    add("DSES 1000 W transmits, Effelsberg 100 m receives", european(L.EffelsbergLinkParameters, 1299.5),
        tx=dses(1299.5, 1000), basis="")

    # --- the next apparition: inferior conjunction 2028-06-01, 43.2 Mkm, but only 1.1 deg
    # from the Sun that day; usable a week either side at ~44.5 Mkm and 10-12 deg separation
    D_2028 = 44_500_000
    add("2028 (Jun 1 +/- 7 d, 44.5 Mkm): DSES monostatic, 2304 MHz, 1500 W", dses(2304, 1500), dist=D_2028,
        basis="13 cm package, 10-12 deg from the Sun")
    add("2028: DSES monostatic, 2400 MHz, 1500 W", dses(2400, 1500), dist=D_2028, basis="")
    add("2028: DSES monostatic, 1299.5 MHz, 1500 W", dses(1299.5, 1500), dist=D_2028, basis="23 cm, for comparison")
    add("2028: DSES 1500 W 2304 MHz transmits, Effelsberg receives", european(L.EffelsbergLinkParameters, 2304),
        tx=dses(2304, 1500), dist=D_2028, basis="")

    lines = ["| Case | C/N0 (dB-Hz) | Tsys (K) | Margin vs 0 dB-Hz | Basis |",
             "|---|---|---|---|---|"]
    for label, c, t, dist, basis in rows:
        d = "" if dist == D_2026 else f" ({dist/1e6:.0f} Mkm)"
        lines.append(f"| {label}{d} | {c:+.1f} | {t:.0f} | {c - DESIGN_POINT_DBHZ:+.1f} | {basis} |")
    out = "\n".join(lines)
    print(out)
    here = Path(__file__).resolve().parent
    (here / "dses_cases.md").write_text(
        "# DSES EVE link-budget cases (ORI classes, run by dses_cases.py)\n\n"
        f"Distance {D_2026/1e6:.1f} million km unless noted (2026-10-24). Receiver elevation "
        "= each site's default (DSES 30 deg, Dwingeloo/Effelsberg 20 deg), clear sky, "
        "Venus radar albedo 0.152 (ORI default).\n\n" + out + "\n", encoding="utf-8")
    print("\nwrote", here / "dses_cases.md")


if __name__ == "__main__":
    main()
