"""Locate the DSES Radio Astronomy Workbench clone and import its shared radio layer.

The EVE modem reuses the Workbench's B210 code by import (design document section 9):
`dses_radio.py` lives in the Workbench repository and ships with it. Search order:
the DSES_WORKBENCH environment variable, the sibling clone (`../DSES_Workbench` on the
Windows box), `~/dev/dses-workbench` (the Mac), then the production installs.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Optional

_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "DSES_Workbench",
    Path.home() / "dev" / "dses-workbench",
    Path("C:/Users/rick/Documents/DSES/Science/DSES_Workbench"),
    Path("C:/ProgramFiles/DSES-Spectrum-Analyzer"),
    Path.home() / "Applications" / "dses-workbench",
    Path.home() / "Applications" / "dses-spectrum-analyzer",
)


def workbench_dir() -> Optional[Path]:
    env = os.environ.get("DSES_WORKBENCH")
    cands = ([Path(env)] if env else []) + list(_CANDIDATES)
    for c in cands:
        if (c / "dses_radio.py").is_file():
            return c
    return None


def import_dses_radio():
    """Import dses_radio from the Workbench clone (adds its directory to sys.path)."""
    try:
        return importlib.import_module("dses_radio")
    except ModuleNotFoundError:
        pass
    d = workbench_dir()
    if d is None:
        raise ImportError("dses_radio.py not found: set DSES_WORKBENCH to the Workbench clone "
                          "(the EVE modem shares the Workbench's B210 layer, design document section 9)")
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    return importlib.import_module("dses_radio")
