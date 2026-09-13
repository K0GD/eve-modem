#!/usr/bin/env bash
# mac_first_run.sh - first installation of the DSES EVE modem on a Mac (or Linux box)
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
ENV_DIR=""
if [ -n "${EVE_PYTHON:-}" ]; then ENV_DIR="$(dirname "$(dirname "$EVE_PYTHON")")"; fi
for c in "${ENV_DIR}" "${RADIOCONDA_ROOT:-}" "${CONDA_PREFIX:-}" "$HOME/radioconda" "/opt/radioconda" "$HOME/miniforge3/envs/radioconda"; do
    if usable "$c"; then ENV_DIR="$c"; break; fi
done
if [ -z "$ENV_DIR" ]; then
    echo "No radioconda with GNU Radio and PySide6 found (looked in ~/radioconda, /opt/radioconda," >&2
    echo "RADIOCONDA_ROOT, CONDA_PREFIX, EVE_PYTHON). Install radioconda first:" >&2
    echo "  https://github.com/ryanvolz/radioconda/releases   (macOS arm64 installer)" >&2
    echo "then run this script again." >&2
    exit 1
fi
PY="$ENV_DIR/bin/python"
say "using $ENV_DIR ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"

# 2. the extras ----------------------------------------------------------------
say "adding the pip extras: $EXTRAS"
"$PY" -m pip install --quiet $EXTRAS
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
( cd "$DL" && tr -d '\r' < "$SHA" > "$SHA.lf" && shasum -a 256 -c "$SHA.lf" && rm -f "$SHA.lf" )

# 4. unzip ----------------------------------------------------------------------
if [ -d "$DEST/eve-modem-$VER" ]; then
    say "$DEST/eve-modem-$VER already exists; moving it aside as eve-modem-$VER.old"
    rm -rf "$DEST/eve-modem-$VER.old"
    mv "$DEST/eve-modem-$VER" "$DEST/eve-modem-$VER.old"
fi
say "unzipping into $DEST"
unzip -q "$DL/$ZIP" -d "$DEST"
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
    say "done. Quit the program window when you are finished; from now on start it from the Desktop app."
else
    case "$(uname)" in Darwin) LOG="$HOME/Library/Logs/DSES_EVE_Modem/app.log" ;; *) LOG="${XDG_STATE_HOME:-$HOME/.local/state}/dses-eve-modem/app.log" ;; esac
    say "the program exited within 10 s; last lines of $LOG:"
    tail -n 40 "$LOG" 2>/dev/null || echo "(no log written)"
    exit 1
fi
