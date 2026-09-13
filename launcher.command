#!/usr/bin/env bash
# launcher.command - macOS double-click wrapper for launcher.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/launcher.sh"
