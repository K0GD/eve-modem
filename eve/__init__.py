"""DSES Earth-Venus-Earth modem.

Implements the ORI EVE waveform (Pete Wyckoff KA3WCA, "Venus Bounce Transmitter
Spiral #2", GPL-3.0) as specified in the DSES Design Description and ICD
(docs/DSES_EVE_Modem_Design_and_ICD.md). Pure NumPy/SciPy core; GNU Radio only in
gr_blocks.py and radio.py.
"""
__version__ = "0.1.0"

from .params import EveParams  # noqa: F401
