#!/usr/bin/env bash
# install-shortcut.command - macOS: a "DSES EVE Modem.app" on the Desktop that runs
# launcher.sh (no Terminal window), with the program icon; Linux: a desktop entry in
# ~/.local/share/applications and on the Desktop. Re-run after moving the program.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$here/launcher.sh" 2>/dev/null || true
case "$(uname)" in
    Darwin)
        app="$HOME/Desktop/DSES EVE Modem.app"
        png="$here/icons/eve_modem.png"
        icns="$here/icons/eve_modem.icns"
        ver="$(grep -oE '__version__\s*=\s*"[^"]+"' "$here/eve/__init__.py" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/')"
        [ -z "$ver" ] && ver="1.0.0"

        # Build the icon with Apple's own tools from the PNG (always a valid .icns);
        # fall back to the shipped .icns if sips/iconutil are not available.
        built=""
        if [ -f "$png" ] && command -v sips >/dev/null && command -v iconutil >/dev/null; then
            set_dir="$(mktemp -d)/eve_modem.iconset"; mkdir -p "$set_dir"
            sips -z 16  16  "$png" --out "$set_dir/icon_16x16.png"      >/dev/null
            sips -z 32  32  "$png" --out "$set_dir/icon_16x16@2x.png"   >/dev/null
            sips -z 32  32  "$png" --out "$set_dir/icon_32x32.png"      >/dev/null
            sips -z 64  64  "$png" --out "$set_dir/icon_32x32@2x.png"   >/dev/null
            sips -z 128 128 "$png" --out "$set_dir/icon_128x128.png"    >/dev/null
            sips -z 256 256 "$png" --out "$set_dir/icon_128x128@2x.png" >/dev/null
            sips -z 256 256 "$png" --out "$set_dir/icon_256x256.png"    >/dev/null
            built="$(dirname "$set_dir")/eve_modem.icns"
            iconutil -c icns "$set_dir" -o "$built" || built=""
        fi
        [ -n "$built" ] && [ -f "$built" ] && icns="$built"

        # Recreate the bundle from scratch so a re-run always refreshes it (Finder and
        # LaunchServices cache the icon of a bundle that is edited in place).
        rm -rf "$app"
        mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
        # A Finder-launched app inherits no shell variables: bake in the Python this
        # run was pointed at (EVE_PYTHON / RADIOCONDA_ROOT), if any.
        cat > "$app/Contents/MacOS/launch" <<EOF
#!/bin/bash
${EVE_PYTHON:+export EVE_PYTHON="$EVE_PYTHON"}
${RADIOCONDA_ROOT:+export RADIOCONDA_ROOT="$RADIOCONDA_ROOT"}
chmod +x "$here/launcher.sh" 2>/dev/null || true
exec "$here/launcher.sh"
EOF
        chmod +x "$app/Contents/MacOS/launch"
        icon_key=""
        if [ -f "$icns" ]; then
            cp "$icns" "$app/Contents/Resources/eve_modem.icns"
            icon_key="  <key>CFBundleIconFile</key><string>eve_modem</string>"
        else
            echo "note: no icon file could be found or built; the app gets a generic icon" >&2
        fi
        cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>DSES EVE Modem</string>
  <key>CFBundleDisplayName</key><string>DSES EVE Modem</string>
  <key>CFBundleIdentifier</key><string>science.dses.eve-modem</string>
  <key>CFBundleExecutable</key><string>launch</string>
$icon_key
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$ver</string>
  <key>CFBundleVersion</key><string>$ver</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF
        [ -n "$built" ] && rm -rf "$(dirname "$built")"
        # Tell LaunchServices about the fresh bundle and nudge Finder to redraw it.
        /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app" >/dev/null 2>&1 || true
        touch "$app"
        echo "Created: $app"
        ;;
    *)
        entry="$HOME/.local/share/applications/dses-eve-modem.desktop"
        mkdir -p "$(dirname "$entry")"
        sed "s|%INSTALL_DIR%|$here|g" "$here/eve-modem.desktop" > "$entry"
        # A menu-launched program inherits no shell variables: bake in the Python this
        # run was pointed at (EVE_PYTHON / RADIOCONDA_ROOT), if any.
        envs=""
        [ -n "${EVE_PYTHON:-}" ] && envs="$envs EVE_PYTHON=$EVE_PYTHON"
        [ -n "${RADIOCONDA_ROOT:-}" ] && envs="$envs RADIOCONDA_ROOT=$RADIOCONDA_ROOT"
        [ -n "$envs" ] && sed -i "s|^Exec=|Exec=env$envs |" "$entry"
        chmod +x "$entry"
        command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$entry")" 2>/dev/null || true
        if [ -d "$HOME/Desktop" ]; then
            cp "$entry" "$HOME/Desktop/dses-eve-modem.desktop"
            chmod +x "$HOME/Desktop/dses-eve-modem.desktop"
            # GNOME/Cinnamon: mark the Desktop copy trusted so it launches without a prompt
            command -v gio >/dev/null && gio set "$HOME/Desktop/dses-eve-modem.desktop" metadata::trusted true 2>/dev/null || true
            touch "$HOME/Desktop/dses-eve-modem.desktop"     # the desktop rereads the flag only on a file change
        fi
        echo "Created: $entry"
        ;;
esac
