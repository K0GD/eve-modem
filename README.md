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
- `eve/` — the modem package (module plan: design document section 5.1).
  Pure NumPy core: `params.py` (Variant A / B / MATLAB sets and derived
  quantities), `bch.py` (BCH(127,106) encoder and decoder, no galois
  dependency), `message.py` (text, CRC-16-CCITT, payload), `modem.py`
  (symbol packing, frame map, phase-continuous streaming synthesizer, FFT-bank
  receiver and accumulators), `channel.py` (Pete's channel.m plus a streaming
  channel with Doppler ramp, gaps, timing offset), `montecarlo.py` (ORI's
  chi-square model, Pete's frame channel, whole-message streaming FER),
  `doppler.py` (JPL Horizons primary, astropy fallback; round trip, two-way
  Doppler, elevation, visibility), `schedule.py` (the section 8.1 JSON
  contract: chunk planning against the round trip and the amplifier duty
  limits, frame-to-time map, validation), `sync.py` (pilot frequency and
  presence, whole-frame epoch check, known-symbol and blind grid search,
  block-accumulating frequency tracker, window receiver, repeat-and-combine
  across passes), `sigmf_io.py` (ORI-format export with the `ori:design` and
  `dses:schedule` blocks; import of ORI's files).
- `tests/` — stage 0-3 tests of the validation plan (design document 5.4):
  Appendix C vectors, galois cross-check, ORI-equation match, loopback on all
  variants, channel calibration, model agreement; conjunction-day geometry
  against the document, schedule contract, sync, Doppler pre-compensation,
  repeat-and-combine, SigMF round trip.
- `link_budget/` — the ORI link-budget classes and the DSES cases behind the
  document's tables.

## Environment

Project-local conda env, like the Workbench:

    C:\ProgramData\radioconda\Scripts\conda.exe env create --prefix .\.conda -f environment.yml
    conda activate .\.conda          (numpy's BLAS needs the env's Library\bin on PATH)
    python -m pytest tests -q
    .conda\python.exe -m eve.montecarlo                 # ORI-style link table, Variant A
    .conda\python.exe -m eve.montecarlo --variant B

The core (`eve/`) needs only NumPy; GNU Radio and UHD are used by the two radio
modules only.

## References

- ORI EVE repository: https://github.com/OpenResearchInstitute/EVE
  (`signal_design/` MATLAB simulation; `signal_design/Python_Implementation/`
  transmit-side SigMF generator, the interoperability baseline).
- The earlier proposal that this project grew from:
  `..\DSES_Workbench\eve\DESIGN.md`.
