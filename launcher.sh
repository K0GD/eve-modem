#!/usr/bin/env bash
# launcher.sh - start the DSES EVE modem application on Linux (the Haswell Pi) or macOS.
# Python: a project env beside the program (.conda/), else EVE_PYTHON, else an activated
# conda env, else radioconda in the usual places. The env needs gnuradio + uhd + PySide6
# + pyqtgraph + numpy/scipy + astropy + jplephem, and the pip extras in environment.yml
# (galois, sigmf, hidapi, pymupdf, pyserial).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usable() { [ -n "${1:-}" ] && [ -x "$1/bin/python" ] && "$1/bin/python" -c "import gnuradio, PySide6" >/dev/null 2>&1; }

find_env() {
    if [ -d "$here/.conda" ] && usable "$here/.conda"; then printf '%s' "$here/.conda"; return 0; fi
    if [ -n "${RADIOCONDA_ROOT:-}" ] && usable "$RADIOCONDA_ROOT"; then printf '%s' "$RADIOCONDA_ROOT"; return 0; fi
    if [ -n "${CONDA_PREFIX:-}" ] && usable "$CONDA_PREFIX"; then printf '%s' "$CONDA_PREFIX"; return 0; fi
    for c in "$HOME/radioconda" "/opt/radioconda" "$HOME/miniforge3/envs/radioconda" "$HOME/radioconda/envs/dses-eve"; do
        if usable "$c"; then printf '%s' "$c"; return 0; fi
    done
    return 1
}

if [ -n "${EVE_PYTHON:-}" ] && [ -x "$EVE_PYTHON" ]; then
    PY="$EVE_PYTHON"                    # an explicit interpreter (conda env or system python3)
else
    ENV_DIR="$(find_env)" || { echo "No Python environment with GNU Radio and PySide6 found. Install radioconda, or create ./.conda from environment.yml, or set EVE_PYTHON to a python that imports gnuradio and PySide6." >&2; exit 1; }
    PY="$ENV_DIR/bin/python"
fi
case "$(uname)" in
    Darwin) LOG_DIR="$HOME/Library/Logs/DSES_EVE_Modem" ;;
    *)      LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/dses-eve-modem" ;;
esac
mkdir -p "$LOG_DIR"
# The env may have GNU Radio but not the modem's extras: say which, instead of dying in the log.
missing="$("$PY" -c "import importlib.util; print(' '.join(m for m in ['galois','sigmf','hid','pymupdf','serial','astropy','jplephem','pyqtgraph','scipy','matplotlib'] if not importlib.util.find_spec(m)))" 2>/dev/null || true)"
if [ -n "$missing" ]; then
    pipmods="$(printf '%s' "$missing" | sed -e 's/\bhid\b/hidapi/' -e 's/\bserial\b/pyserial/')"
    msg="The Python at $PY lacks: $missing. Add them with:  $(dirname "$PY")/pip install $pipmods"
    echo "$msg" >&2
    echo "$(date '+%Y-%m-%d %H:%M:%S')  $msg" >> "$LOG_DIR/app.log"
    if [ "$(uname)" = "Darwin" ]; then
        osascript -e "display dialog \"$msg\" with title \"DSES EVE modem\" buttons {\"OK\"} default button 1" >/dev/null 2>&1 || true
    fi
    exit 1
fi
export PYQTGRAPH_QT_LIB=PySide6
export PYTHONUNBUFFERED=1
cd "$here"
exec "$PY" "$here/eve_app.py" >> "$LOG_DIR/app.log" 2>&1
