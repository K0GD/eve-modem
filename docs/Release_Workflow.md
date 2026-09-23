# DSES EVE Modem — Release Workflow

The same machinery as the Workbench (its `Release_Workflow.md` is the long form; this is
the EVE-specific short form). One zip for Windows, macOS, and Linux; a manifest on
gpstime that the program polls; an in-app installer that verifies a SHA-256 sidecar.

## Where releases live

```text
URL:        https://gpstime.com/sw_distribution/eve-modem/
Filesystem: /var/www/html/sw_distribution/eve-modem/     (rick@gpstime.com)
```

The folder holds `manifest.json`, `eve-modem-<v>.zip`, `eve-modem-<v>.sha256`,
`DSES_EVE_Modem_Operators_Guide.pdf`, and the same `.htaccess` as the Workbench folder
(directory listing on). Create it once with the `.htaccess` copied from
`sw_distribution/dses-workbench/`.

## Cutting a release

1. **Bump the version** in `eve/__init__.py` (`__version__`). Feature release = second
   digit, bug fix = third (the 1.5.0 rule). Commit.
2. **Rebuild the documents**: `docs/build_docs.ps1` (both PDFs; the guide also ships
   as Markdown for the Help menu).
3. **Build the zip**: `make-release.ps1` (Windows) or `make-release.sh` (Mac/Linux).
   Both call `tools/make_release.py`, which stages the program, bundles the Workbench's
   `dses_radio.py` beside `eve_app.py` so the zip stands alone, normalizes line endings,
   imports every staged module in a fresh interpreter with the repo off the path, writes
   `dist/eve-modem-<v>.zip` with forward-slash entries and one top folder, and writes the
   sidecar in the updater's `<hex>  <name>` form.
4. **Upload** the zip, the sidecar, and the guide PDF (one line; the key is the gpstime
   key, not the default):

   ```bash
   scp -i ~/.ssh/id_ed25519_gpstime -o IdentitiesOnly=yes dist/eve-modem-<v>.zip dist/eve-modem-<v>.sha256 docs/DSES_EVE_Modem_Operators_Guide.pdf rick@gpstime.com:/var/www/html/sw_distribution/eve-modem/
   ```

5. **Write the manifest** on the server (single-quoted heredoc so nothing expands):

   ```bash
   cat > /var/www/html/sw_distribution/eve-modem/manifest.json << 'EOF'
   {
     "latest_version": "<v>",
     "download_url": "https://gpstime.com/sw_distribution/eve-modem/eve-modem-<v>.zip",
     "release_notes": "What changed, one line per item.\n"
   }
   EOF
   chmod 644 /var/www/html/sw_distribution/eve-modem/*
   ```

6. **Verify with the updater's own flow**, not by eye: fetch the manifest, download the
   zip it names, fetch the sidecar `updater.sha256_url_for()` derives, verify, extract.
   `python tools/verify_release.py` does exactly that against the live URL.
7. **Tag** the version-bump commit: `git tag -a v<v> <commit> -m "..."`, `git push origin v<v>`.
8. Record the cut in `CLAUDE.md`.
9. **Refresh the public GitHub mirror**: `bash tools/publish_github.sh`, after the
   release commit and tag are pushed to origin. The NAS bare repo stays the master;
   GitHub (https://github.com/K0GD/eve-modem, to move to a DSES organization once one
   exists — the same arrangement as the Workbench's `K0GD/dses-workbench`) holds a
   read-only copy of `main` and every release tag for the team and ORI, with the
   private working notes (`CLAUDE.md`) removed from every commit by `git filter-repo`
   and co-author trailers removed from every commit and tag message.
   The rewrite is deterministic, so the mirror's commit ids stay stable across runs.
   The script refuses to run on a dirty working tree (commit or stash first) and needs
   `git filter-repo` plus a GitHub login (`gh auth login`). Run it after **any** push to
   origin, not only at release time. Releases themselves are never published from
   GitHub — the zip, sidecar, guide and manifest live on gpstime (steps 4–6).

## How the program updates

Settings (in `%APPDATA%\DSES\EVE_Modem.ini`): `manifest_url` (default above),
`auto_check` (true), `check_interval_hours` (24), `last_check_iso`, `dismissed_version`.
At start-up, at most once a day, the program fetches the manifest on a background
thread; a newer version opens a non-modal dialog with the release notes. Help → Check
for updates… does it on demand and reports "nothing to do" or the error. Install Update
downloads, verifies the SHA-256 sidecar, extracts, and installs either over the running
copy (backup in `.dses_backup`, then offers a restart through the launcher) or as a new
copy in a chosen folder with an optional desktop shortcut. Nothing is written before the
checksum matches. The install refuses while a run is in progress.

## Environments

The zip carries no Python. Each machine needs an environment with GNU Radio, UHD,
PySide6, pyqtgraph, numpy, scipy, astropy, jplephem, matplotlib and the pip extras
(galois, sigmf, hidapi, pymupdf, pyserial), which `environment.yml` reproduces:
`conda env create --prefix .conda -f environment.yml` inside the install folder. The
launchers look for `.conda` beside the program first, then `EVE_PYTHON` /
`RADIOCONDA_ROOT` / `CONDA_PREFIX` / the usual radioconda locations.
