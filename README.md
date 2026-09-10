# DSES EVE Modem

Earth-Venus-Earth (EVE) modem for the DSES 60-foot dish at Haswell, built on
the ORI "Spiral #2" waveform by Pete Wyckoff, KA3WCA (Open Research Institute,
GPL-3.0). Target: the Venus inferior conjunction of 2026-10-24, preceded by an
Earth-Moon-Earth proof test.

This is a **separate project** from the DSES Radio Astronomy Workbench
(`..\DSES_Workbench`), by decision of 2026-09-09: EVE is a station controller
(real transmit drive, keying interlocks, an ephemeris-driven schedule, 30-minute
frames), while the Workbench is a receive-side observing tool that locks its
transmitter at minimum. The two share code by factoring the Workbench's B210
radio classes into an importable module, not by living in one file.

## Layout

- `docs/DSES_EVE_Modem_Design_and_ICD.md` — the design description and
  interface control document (source of record). Build the PDF with
  `docs/build_docs.ps1` (uses the Workbench's `build_doc.py`, DSES house style).
- `docs/make_figures.py` — regenerates the document figures (system Python 3.12
  with astropy + matplotlib).
- `eve/` — the modem package (to come; module plan in the design document).

## References

- ORI EVE repository: https://github.com/OpenResearchInstitute/EVE
  (`signal_design/` MATLAB simulation; `signal_design/Python_Implementation/`
  transmit-side SigMF generator, the interoperability baseline).
- The earlier proposal that this project grew from:
  `..\DSES_Workbench\eve\DESIGN.md`.
