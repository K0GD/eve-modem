#!/usr/bin/env bash
# install-shortcut.command - macOS: a "DSES EVE Modem" app bundle on the Desktop that runs
# launcher.sh; Linux: a desktop entry in ~/.local/share/applications and on the Desktop.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname)" in
    Darwin)
        app="$HOME/Desktop/DSES EVE Modem.app"
        mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
        cat > "$app/Contents/MacOS/DSES EVE Modem" <<EOF
#!/usr/bin/env bash
exec "$here/launcher.sh"
EOF
        chmod +x "$app/Contents/MacOS/DSES EVE Modem"
        cat > "$app/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>DSES EVE Modem</string>
  <key>CFBundleIdentifier</key><string>science.dses.eve-modem</string>
  <key>CFBundleExecutable</key><string>DSES EVE Modem</string>
  <key>CFBundleIconFile</key><string>eve_modem</string>
  <key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
EOF
        [ -f "$here/icons/eve_modem.icns" ] && cp "$here/icons/eve_modem.icns" "$app/Contents/Resources/"
        echo "Created: $app"
        ;;
    *)
        entry="$HOME/.local/share/applications/dses-eve-modem.desktop"
        mkdir -p "$(dirname "$entry")"
        sed "s|%INSTALL_DIR%|$here|g" "$here/eve-modem.desktop" > "$entry"
        chmod +x "$entry"
        if [ -d "$HOME/Desktop" ]; then cp "$entry" "$HOME/Desktop/dses-eve-modem.desktop"; chmod +x "$HOME/Desktop/dses-eve-modem.desktop"; fi
        echo "Created: $entry"
        ;;
esac
