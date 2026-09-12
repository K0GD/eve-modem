#!/usr/bin/env python3
"""DSES EVE modem application entry point (desktop icon target).

    python eve_app.py

Everything the command-line tools do (software simulation, B210 loopback bench, EME and
Venus sessions, GPS clock preflight, the operator display, offline decode, session report
PDF) from one window with remembered settings. See eve/app.py.
"""
import os
import sys

# Force PyQtGraph to use PySide6, not PyQt5 (Workbench rule; must precede any Qt import).
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import faulthandler  # noqa: E402

# A hard crash (access violation in a native library) leaves its traceback here.
_logdir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "DSES", "EVE_Modem")
os.makedirs(_logdir, exist_ok=True)
_fault = open(os.path.join(_logdir, "fault.log"), "a")
faulthandler.enable(file=_fault, all_threads=True)

from eve.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
