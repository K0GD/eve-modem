#!/usr/bin/env bash
# mac_first_run.sh - first installation of the DSES EVE modem on a Mac or a Linux box
# that already has radioconda. Does the whole first-run sequence from the operator's
# guide, section 10, so nothing has to be typed:
#   1. find radioconda (or the env named in EVE_PYTHON / RADIOCONDA_ROOT)
#   2. add the pip extras the modem needs
#   3. download the published zip + its SHA-256 sidecar, verify them together
#   4. unzip into ~/Applications
#   5. start the program through its own launcher, and show the log if it dies
#
#   curl -fsSL https://gpstime.com/sw_distribution/eve-modem/mac_first_run.sh | bash
#   bash mac_first_run.sh [version]        # default: the version the manifest names
#   bash mac_first_run.sh --shortcut-only  # just (re)make the Desktop app for an installed copy
set -euo pipefail

BASE="https://gpstime.com/sw_distribution/eve-modem"
DEST="$HOME/Applications"
EXTRAS="galois sigmf hidapi pymupdf pyserial astropy jplephem"

say() { printf '\n== %s\n' "$*"; }

make_shortcut() {   # $1 = install folder
    local app="$1"
    if [ ! -f "$app/icons/eve_modem.icns" ]; then       # the 1.0.0 zip shipped without it
        mkdir -p "$app/icons"
        curl -fsSL -o "$app/icons/eve_modem.icns" "$BASE/eve_modem.icns" || rm -f "$app/icons/eve_modem.icns"
    fi
    # the shortcut maker in the 1.0.0 zip predates the native icon build: take the current one
    curl -fsSL -o "$app/install-shortcut.command.new" "$BASE/install-shortcut.command" \
        && mv "$app/install-shortcut.command.new" "$app/install-shortcut.command" \
        && chmod +x "$app/install-shortcut.command" || rm -f "$app/install-shortcut.command.new"
    say "making the Desktop shortcut"
    bash "$app/install-shortcut.command"
}

if [ "${1:-}" = "--shortcut-only" ]; then
    APP="$(ls -d "$DEST"/eve-modem-[0-9]* 2>/dev/null | grep -v '\.old$' | sort -V | tail -n 1 || true)"
    [ -n "$APP" ] || { echo "no eve-modem-<version> folder under $DEST" >&2; exit 1; }
    make_shortcut "$APP"
    exit 0
fi

# 1. the environment ---------------------------------------------------------
usable() { [ -n "${1:-}" ] && [ -x "$1/bin/python" ] && "$1/bin/python" -c "import gnuradio, PySide6" >/dev/null 2>&1; }
PY=""
if [ -n "${EVE_PYTHON:-}" ] && [ -x "$EVE_PYTHON" ] && "$EVE_PYTHON" -c "import gnuradio, PySide6" >/dev/null 2>&1; then
    PY="$EVE_PYTHON"
else
    for c in "${RADIOCONDA_ROOT:-}" "${CONDA_PREFIX:-}" "$HOME/radioconda" "/opt/radioconda" "$HOME/miniforge3/envs/radioconda"; do
        if usable "$c"; then PY="$c/bin/python"; break; fi
    done
fi
if [ -z "$PY" ] && command -v python3 >/dev/null && python3 -c "import gnuradio, PySide6" >/dev/null 2>&1; then
    PY="$(command -v python3)"          # a distribution GNU Radio (apt) with PySide6 installed
    SYSTEM_PY=1
fi
if [ -z "$PY" ]; then
    echo "No Python with GNU Radio and PySide6 found (looked in ~/radioconda, /opt/radioconda," >&2
    echo "RADIOCONDA_ROOT, CONDA_PREFIX, EVE_PYTHON, and python3 on PATH). Install radioconda first:" >&2
    echo "  https://github.com/ryanvolz/radioconda/releases   (pick your OS and CPU)" >&2
    echo "then run this script again." >&2
    exit 1
fi
say "using $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"

# 2. the extras ----------------------------------------------------------------
say "adding the pip extras: $EXTRAS"
if [ -n "${SYSTEM_PY:-}" ]; then
    "$PY" -m pip install --quiet --user --break-system-packages $EXTRAS 2>/dev/null || "$PY" -m pip install --quiet --user $EXTRAS
else
    "$PY" -m pip install --quiet $EXTRAS
fi
"$PY" -c "import galois, sigmf, hid, pymupdf, serial, astropy, jplephem; print('extras import OK')"

# 3. download + verify ----------------------------------------------------------
VER="${1:-}"
if [ -z "$VER" ]; then
    VER="$(curl -fsSL "$BASE/manifest.json" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["latest_version"])')"
fi
ZIP="eve-modem-$VER.zip"
SHA="eve-modem-$VER.sha256"
DL="$HOME/Downloads"
mkdir -p "$DL" "$DEST"
say "downloading $ZIP and $SHA to $DL"
curl -fSL --progress-bar -o "$DL/$ZIP" "$BASE/$ZIP"
curl -fsSL -o "$DL/$SHA" "$BASE/$SHA"
say "verifying"
# macOS has shasum (perl); Linux always has sha256sum, shasum only if perl is installed
if command -v sha256sum >/dev/null; then CHK="sha256sum -c"; else CHK="shasum -a 256 -c"; fi
( cd "$DL" && tr -d '\r' < "$SHA" > "$SHA.lf" && $CHK "$SHA.lf" && rm -f "$SHA.lf" )

# 4. unzip ----------------------------------------------------------------------
if [ -d "$DEST/eve-modem-$VER" ]; then
    say "$DEST/eve-modem-$VER already exists; moving it aside as eve-modem-$VER.old"
    rm -rf "$DEST/eve-modem-$VER.old"
    mv "$DEST/eve-modem-$VER" "$DEST/eve-modem-$VER.old"
fi
say "unzipping into $DEST"
if command -v unzip >/dev/null; then
    unzip -q "$DL/$ZIP" -d "$DEST"
else                                   # minimal Linux without unzip: Python does it
    "$PY" -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$DL/$ZIP" "$DEST"
fi
APP="$DEST/eve-modem-$VER"
chmod +x "$APP"/*.sh "$APP"/*.command 2>/dev/null || true
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
ls "$APP"

# 5. start it through the shipped launcher ---------------------------------------
say "starting $APP/launcher.sh (the program logs to ~/Library/Logs/DSES_EVE_Modem/app.log)"
export EVE_PYTHON="$PY"
"$APP/launcher.sh" &
LPID=$!
sleep 10
if kill -0 "$LPID" 2>/dev/null; then
    say "running (pid $LPID)"
    make_shortcut "$APP"
    if [ "$(uname)" = "Darwin" ]; then
        say "done. Quit the program window when you are finished; from now on start it from the Desktop app."
    else
        say "done. The program is in the applications menu (Science) and on the Desktop; some desktops"
        echo "   ask once to 'Allow Launching' a Desktop file (right-click it)."
        if ! command -v evince >/dev/null && ! command -v okular >/dev/null && ! command -v xreader >/dev/null && ! command -v atril >/dev/null; then
            echo "   No PDF viewer found: the reports and the guide open in a real viewer with"
            echo "     sudo apt install evince      (LibreOffice Draw re-flows PDFs with substitute fonts)"
        fi
    fi
else
    case "$(uname)" in Darwin) LOG="$HOME/Library/Logs/DSES_EVE_Modem/app.log" ;; *) LOG="${XDG_STATE_HOME:-$HOME/.local/state}/dses-eve-modem/app.log" ;; esac
    say "the program exited within 10 s; last lines of $LOG:"
    tail -n 40 "$LOG" 2>/dev/null || echo "(no log written)"
    exit 1
fi
