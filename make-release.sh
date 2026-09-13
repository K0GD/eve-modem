#!/usr/bin/env bash
# make-release.sh - build dist/eve-modem-<version>.zip (+ .sha256) on macOS / Linux.
# The work is in tools/make_release.py (one cross-platform builder; forward-slash zip).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$here/.conda/bin/python"
[ -x "$PY" ] || PY="${CONDA_PREFIX:-$HOME/radioconda}/bin/python"
exec "$PY" "$here/tools/make_release.py" "$@"
