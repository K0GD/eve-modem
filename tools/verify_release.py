#!/usr/bin/env python3
"""Verify a published release exactly the way the in-app updater will: fetch the
manifest, download the zip it names, fetch the SHA-256 sidecar the updater derives,
verify, extract, and check the top folder and the version inside.

    python tools/verify_release.py                       # the live manifest
    python tools/verify_release.py --manifest URL        # a staging copy

Exit code 0 = a user's Install Update would succeed.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve import updater  # noqa: E402

DEFAULT = "https://gpstime.com/sw_distribution/eve-modem/manifest.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=DEFAULT)
    a = ap.parse_args(argv)
    print("manifest:", a.manifest)
    with updater.open_url(a.manifest, timeout=15, headers={"User-Agent": "DSES-EVE-Modem-verify"}) as r:
        man = json.loads(r.read().decode("utf-8"))
    latest, url, notes = man.get("latest_version"), man.get("download_url"), man.get("release_notes", "")
    print(f"latest_version {latest}; download_url {url}; notes {len(notes)} chars")
    if not latest or not url:
        print("manifest incomplete")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="eve_verify_"))
    try:
        z = tmp / "release.zip"
        updater.download(url, z)
        print(f"downloaded {z.stat().st_size:,} bytes")
        sha_url = updater.sha256_url_for(url)
        updater.verify(z, sha_url)
        print("sha256 verified against", sha_url)
        root = updater.extract_release(z, tmp / "x")
        print("top folder:", root.name)
        init = root / "eve" / "__init__.py"
        m = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
        ver = m.group(1) if m else "?"
        print("version inside the zip:", ver)
        ok = (root.name == f"eve-modem-{latest}") and ver == latest and (root / "dses_radio.py").is_file() and (root / "eve_app.py").is_file()
        print("PASS" if ok else "FAIL: folder/version/bundled files do not match the manifest")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
