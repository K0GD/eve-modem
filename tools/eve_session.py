#!/usr/bin/env python3
"""Plan and run EVE sessions (design document 5.1 tools).

Plan a session (writes the schedule JSON and, with Horizons, the ephemeris CSV):
    python tools/eve_session.py plan --target venus --start 2026-10-24T15:30:00Z \\
        --f-dial 1299.5e6 --repeat 5 --stop 2026-10-24T21:20:00Z --out sessions/

Run a schedule on the B210 (the modem owns the radio for the session):
    python tools/eve_session.py run sessions/DSES-EVE-20261024-A.json --tx-gain 60 --rx-gain 40 \\
        --clock external --archive archive/

Software bench (no radio): a short schedule through SimRadio, then the offline decode:
    python tools/eve_session.py sim --cn0 20 --n-frames 6

Add --now to shift a planned schedule so its first chunk starts a few seconds from now
(bench and EME rehearsals).
"""
from __future__ import annotations

import os
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")   # entry point: force the pyqtgraph backend

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve import EveParams  # noqa: E402
from eve import doppler as D  # noqa: E402
from eve import schedule as S  # noqa: E402
from eve.doppler import iso_utc  # noqa: E402


def cmd_plan(a):
    site = D.DSES_HASWELL
    t0 = D.to_unix(a.start)
    t1 = D.to_unix(a.stop) if a.stop else t0 + 8 * 3600
    table_path = None
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        table_path = Path(a.out) / f"{a.source}_{a.target}_haswell_{iso_utc(t0, 0)[:10].replace('-', '')}.csv"
    model = D.make_model(a.target, site, t0 - 1800, t1 + 1800, source=a.source, table_path=table_path, step="1 m")
    p = EveParams.named(a.variant)
    if a.n_frames:
        p = replace(p, n_frames=a.n_frames)
    sid = a.session_id or f"DSES-EVE-{iso_utc(t0, 0)[:10].replace('-', '')}-{a.suffix}"
    sched = S.build_schedule(sid, a.target, model, t0, a.f_dial, params=p, text=a.message, repeat_count=a.repeat,
                             mode=a.mode, pilot=not a.no_pilot, chunk_s=a.chunk, t_stop=a.stop,
                             doppler_table=table_path.name if table_path else "", rtt_guard_s=a.rtt_guard,
                             t_on_max_s=a.t_on_max, t_off_min_s=a.t_off_min)
    print(S.describe(sched, model))
    if a.out:
        out = Path(a.out) / f"{sid}.json"
        sched.to_json(out)
        print("wrote", out, "and", table_path if table_path else "(no table)")
    return 0


def _shift_to_now(sched: S.Schedule, model, lead_s: float) -> S.Schedule:
    now = time.time()
    return S.build_schedule(sched.session_id, sched.target, model, now + lead_s, sched.f_dial_hz, params=sched.params,
                            text=sched.text, repeat_count=sched.repeat_count, mode=sched.mode,
                            pilot=sched.pilot_enabled, chunk_s=None, t_on_max_s=sched.t_on_max_s,
                            t_off_min_s=sched.t_off_min_s, doppler_table=sched.doppler_table)


def cmd_run(a):
    from eve.station import Session, SessionOptions
    from eve.radio import EveRadio, RadioConfig
    sched = S.Schedule.from_json(a.schedule)
    table = Path(a.schedule).parent / sched.doppler_table if sched.doppler_table else None
    if table and table.exists():
        model = D.DopplerModel(D.EphemerisTable.load(table))
    else:
        t0, t1 = sched.t_start - 1800, sched.t_end + 1800
        model = D.make_model(sched.target, sched.transmitter, t0, t1, source="auto")
    if a.now:
        sched = _shift_to_now(sched, model, 8.0)
    print(S.describe(sched, model))
    cfg = RadioConfig(serial=a.serial, f_dial_hz=sched.f_dial_hz, tx_gain_db=a.tx_gain, rx_gain_db=a.rx_gain,
                      clock_source=a.clock, require_ref_lock=not a.no_ref_check, lo_offset_hz=a.lo_offset)
    radio = EveRadio(sched.params, cfg)
    st = radio.open()
    print(st.summary())
    if not radio.verify_pps():
        print("PPS did not verify (no second edge seen)", file=sys.stderr)
        if not a.no_ref_check:
            return 2
    opts = SessionOptions(out_dir=a.archive, pa_in_chain=not a.no_pa, rx_doppler_removal=a.rx_doppler,
                          tx_precompensate=not a.no_precomp, live_decode=True)
    sess = Session(sched, model, radio, opts)
    try:
        rep = _run(sess, a)
    finally:
        radio.close()
    _print_report(rep)
    return 0 if not rep.aborted else 1


def cmd_sim(a):
    from eve.station import Session, SessionOptions, SimRadio
    p = replace(EveParams.named(a.variant), n_frames=a.n_frames, pilot_frames=a.pilot_frames)
    site = D.DSES_HASWELL
    now = time.time()
    tab = D.synthetic_table("sim", site, now - 60, now + 4 * 3600, range_km=a.range_km)
    model = D.DopplerModel(tab)
    sched = S.build_schedule(a.session_id, "sim", model, now + 3.0, a.f_dial, params=p, text=a.message,
                             repeat_count=a.repeat, pilot=not a.no_pilot, chunk_s=None, rtt_guard_s=0.1,
                             t_off_min_s=a.t_off_min, t_on_max_s=3600.0)
    print(S.describe(sched, model))
    Path(a.archive).mkdir(parents=True, exist_ok=True)
    sched.to_json(Path(a.archive) / f"{sched.session_id}.json")
    radio = SimRadio(p, cn0_db=a.cn0, seed=a.seed)
    opts = SessionOptions(out_dir=a.archive, pa_in_chain=False, tx_precompensate=False, live_decode=True,
                          start_margin_s=1.0, realtime_mode=False)
    rep = _run(Session(sched, model, radio, opts), a)
    _print_report(rep)
    return 0


def _run(sess, a):
    if getattr(a, "display", False):
        from eve.display import run_with_display
        return run_with_display(sess, screenshot=getattr(a, "screenshot", None),
                                screenshot_after_s=getattr(a, "screenshot_after", 20.0))
    return sess.run()


def _print_report(rep):
    print(f"\nsession {rep.session_id}: {rep.started_utc} .. {rep.finished_utc}, aborted={rep.aborted} {rep.abort_reason}")
    print(f"  chunks keyed {rep.chunks_keyed}, tone samples on {rep.tone_samples_on}, frames sent {rep.frames_sent}")
    print(f"  rx: {rep.rx}")
    print(f"  live decode: {rep.live_decode}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="EVE session planner / runner / software bench")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("plan")
    pl.add_argument("--target", default="venus")
    pl.add_argument("--start", required=True)
    pl.add_argument("--stop", default=None)
    pl.add_argument("--f-dial", type=float, default=1299.5e6)
    pl.add_argument("--variant", default="A")
    pl.add_argument("--n-frames", type=int, default=None)
    pl.add_argument("--message", default="K0PRT K0PRT")
    pl.add_argument("--repeat", type=int, default=5)
    pl.add_argument("--mode", default="monostatic")
    pl.add_argument("--chunk", type=float, default=240.0)
    pl.add_argument("--rtt-guard", type=float, default=30.0)
    pl.add_argument("--t-on-max", type=float, default=300.0)
    pl.add_argument("--t-off-min", type=float, default=240.0)
    pl.add_argument("--no-pilot", action="store_true")
    pl.add_argument("--source", default="auto", help="horizons | astropy | auto")
    pl.add_argument("--session-id", default=None)
    pl.add_argument("--suffix", default="A")
    pl.add_argument("--out", default="sessions")
    pl.set_defaults(fn=cmd_plan)

    rn = sub.add_parser("run")
    rn.add_argument("schedule")
    rn.add_argument("--serial", default="")
    rn.add_argument("--tx-gain", type=float, default=0.0)
    rn.add_argument("--rx-gain", type=float, default=40.0)
    rn.add_argument("--clock", default="external", help="external | gpsdo | internal")
    rn.add_argument("--no-ref-check", action="store_true")
    rn.add_argument("--lo-offset", type=float, default=-300e3)
    rn.add_argument("--archive", default="archive")
    rn.add_argument("--no-pa", action="store_true", help="no amplifier in the chain: relax the 7.2 limits (EME with the bare B210)")
    rn.add_argument("--rx-doppler", action="store_true", help="remove the model Doppler on receive (not the pre-compensated receiver)")
    rn.add_argument("--no-precomp", action="store_true")
    rn.add_argument("--now", action="store_true", help="shift the schedule to start 8 s from now")
    rn.add_argument("--display", action="store_true", help="operator display (PySide6)")
    rn.add_argument("--screenshot", default=None, help=argparse.SUPPRESS)
    rn.add_argument("--screenshot-after", type=float, default=20.0, help=argparse.SUPPRESS)
    rn.set_defaults(fn=cmd_run)

    sm = sub.add_parser("sim")
    sm.add_argument("--variant", default="A")
    sm.add_argument("--n-frames", type=int, default=6)
    sm.add_argument("--pilot-frames", type=int, default=2)
    sm.add_argument("--repeat", type=int, default=2)
    sm.add_argument("--cn0", type=float, default=20.0)
    sm.add_argument("--range-km", type=float, default=375000.0)
    sm.add_argument("--t-off-min", type=float, default=0.5)
    sm.add_argument("--f-dial", type=float, default=1296e6)
    sm.add_argument("--message", default="K0PRT K0PRT")
    sm.add_argument("--no-pilot", action="store_true")
    sm.add_argument("--seed", type=int, default=1)
    sm.add_argument("--session-id", default="SIM")
    sm.add_argument("--archive", default="archive_sim")
    sm.add_argument("--display", action="store_true", help="operator display (PySide6)")
    sm.add_argument("--screenshot", default=None, help=argparse.SUPPRESS)
    sm.add_argument("--screenshot-after", type=float, default=20.0, help=argparse.SUPPRESS)
    sm.set_defaults(fn=cmd_sim)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
