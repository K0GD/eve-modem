#!/usr/bin/env python3
"""Build the distributable zip of the DSES EVE modem: eve-modem-<version>.zip.

Same conventions as the Workbench's make-release scripts (its lessons are baked in):
  * the version comes from eve/__init__.py (__version__);
  * the zip holds ONE top folder eve-modem-<version>/ with FORWARD-SLASH entry names
    (Python's zipfile always writes '/', unlike PowerShell's Compress-Archive);
  * Unix-consumed text files are normalized to LF in the staging tree, and the shell
    launchers get their executable bits in the zip;
  * the SHA-256 sidecar eve-modem-<version>.sha256 is written in the "<hex>  <name>"
    form the in-app updater fetches (updater.sha256_url_for: ...-<v>.zip -> ...-<v>.sha256);
  * the shared radio layer dses_radio.py is BUNDLED from the Workbench clone so the zip
    stands alone (eve/_workbench.py imports the copy beside eve_app.py first);
  * a completeness check imports every staged package module from the staging tree
    with the repo removed from sys.path, so a module that never made it into the zip
    fails the build here and not on a user's PC.

    python tools/make_release.py            # -> dist/eve-modem-<v>.zip + .sha256
    python tools/make_release.py --no-check # skip the staged-import check

Run it from the project env (activated, or PATH with .conda/Library/bin on Windows).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUG = "eve-modem"

SHIP_FILES = [
    "eve_app.py", "LICENSE", "README.md", "environment.yml",
    "launcher.bat", "launcher.ps1", "launcher.sh", "launcher.command",
    "install-shortcut.ps1", "install-shortcut.command", "eve-modem.desktop",
]
SHIP_DIRS = {            # source dir -> (published dir, file glob patterns)
    "eve": ("eve", ("*.py",)),
    "tools": ("tools", ("*.py",)),
    "icons": ("icons", ("eve_modem.ico", "eve_modem.png", "eve_modem.icns")),
    "docs/hardware": ("docs/hardware", ("*.md",)),
    "docs/figures": ("docs/figures", ("app_*.png",)),
    "link_budget": ("link_budget", ("*.py",)),
}
SHIP_DOCS = [            # the user-facing documents (PDF deliverables + the guide source the Help menu reads)
    "docs/DSES_EVE_Modem_Operators_Guide.md",
    "docs/DSES_EVE_Modem_Operators_Guide.pdf",
    "docs/DSES_EVE_Modem_Design_and_ICD.pdf",
]
LF_EXTS = {".sh", ".command", ".desktop", ".py", ".yml", ".md"}
EXECUTABLES = {"launcher.sh", "launcher.command", "install-shortcut.command"}
WORKBENCH_CANDIDATES = [
    Path(os.environ.get("DSES_WORKBENCH", "")) if os.environ.get("DSES_WORKBENCH") else None,
    ROOT.parent / "DSES_Workbench",
    Path.home() / "dev" / "dses-workbench",
]


def version() -> str:
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', (ROOT / "eve" / "__init__.py").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("eve/__init__.py has no __version__")
    return m.group(1)


def find_dses_radio() -> Path:
    for c in WORKBENCH_CANDIDATES:
        if c and (c / "dses_radio.py").is_file():
            return c / "dses_radio.py"
    raise SystemExit("dses_radio.py not found: set DSES_WORKBENCH to the Workbench clone (it is bundled into the zip)")


def stage(dist: Path, ver: str) -> Path:
    bundle = f"{SLUG}-{ver}"
    st = dist / bundle
    if st.exists():
        shutil.rmtree(st)
    st.mkdir(parents=True)
    missing = []
    for f in SHIP_FILES + SHIP_DOCS:
        src = ROOT / f
        if src.is_file():
            dst = st / (Path(f).name if f in SHIP_FILES else f)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            missing.append(f)
    for src_dir, (pub, pats) in SHIP_DIRS.items():
        s = ROOT / src_dir
        if not s.is_dir():
            missing.append(src_dir + "/")
            continue
        d = st / pub
        d.mkdir(parents=True, exist_ok=True)
        n = 0
        for pat in pats:
            for f in sorted(s.glob(pat)):
                if f.is_file() and "__pycache__" not in f.parts:
                    shutil.copy2(f, d / f.name)
                    n += 1
        if n == 0:
            missing.append(f"{src_dir}/{pats}")
    radio = find_dses_radio()
    shutil.copy2(radio, st / "dses_radio.py")
    print(f"bundled dses_radio.py from {radio}")
    for m in missing:
        print(f"WARNING missing (skipped): {m}")
    # line endings and executable bits
    for f in st.rglob("*"):
        if f.is_file() and f.suffix in LF_EXTS:
            b = f.read_bytes()
            if b"\r" in b:
                f.write_bytes(b.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
                print(f"  normalized to LF: {f.relative_to(st)}")
        if f.is_file() and f.name in EXECUTABLES:
            f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return st


def check_staged_imports(st: Path) -> None:
    """Import every eve.* module and the tools from the staging tree in a fresh
    interpreter with the repo off sys.path (the bundled dses_radio must satisfy
    eve.radio). Headless Qt for eve.app / eve.display."""
    mods = sorted("eve." + f.stem for f in (st / "eve").glob("*.py") if f.stem != "__init__")
    code = (
        "import os, sys, importlib\n"
        "os.environ['PYQTGRAPH_QT_LIB'] = 'PySide6'\n"
        "os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')\n"
        f"sys.path.insert(0, {str(st)!r})\n"
        "pref = os.path.abspath(sys.prefix).lower()\n"
        f"bad = [p for p in sys.path if p and os.path.abspath(p).lower().startswith({str(ROOT).lower()!r}) "
        f"and not os.path.abspath(p).lower().startswith(pref) and os.path.abspath(p) != {str(st)!r}]\n"
        "sys.path = [p for p in sys.path if p not in bad]\n"
        "os.environ.pop('DSES_WORKBENCH', None)\n"
        f"for m in {mods!r}:\n"
        "    importlib.import_module(m)\n"
        "import dses_radio, eve\n"
        f"assert os.path.abspath(dses_radio.__file__).lower().startswith({str(st).lower()!r}), dses_radio.__file__\n"
        "print('staged imports ok:', len(%r), 'modules; dses_radio from', dses_radio.__file__, '; version', eve.__version__)\n" % mods
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    env["PYTHONDONTWRITEBYTECODE"] = "1"      # no __pycache__ in the staging tree
    r = subprocess.run([sys.executable, "-c", code], cwd=str(st), env=env, capture_output=True, text=True)
    out = "\n".join(ln for ln in (r.stdout + r.stderr).splitlines() if "X300" not in ln and "forcibly" not in ln and "[INFO]" not in ln)
    if r.returncode != 0:
        print(out)
        raise SystemExit("staged import check FAILED")
    print(out.strip().splitlines()[-1])


def build_zip(st: Path, dist: Path) -> Path:
    zpath = dist / f"{st.name}.zip"
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(st.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                rel = f"{st.name}/{f.relative_to(st).as_posix()}"
                zi = zipfile.ZipInfo.from_file(f, rel)
                zi.compress_type = zipfile.ZIP_DEFLATED
                if f.name in EXECUTABLES:
                    zi.external_attr = (0o755 << 16)
                with open(f, "rb") as fh:
                    z.writestr(zi, fh.read())
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        assert all("\\" not in n for n in names), "backslash in a zip entry name"
        tops = {n.split("/", 1)[0] for n in names}
        assert tops == {st.name}, tops
    h = hashlib.sha256(zpath.read_bytes()).hexdigest()
    side = dist / f"{st.name}.sha256"
    side.write_text(f"{h}  {zpath.name}\n", encoding="ascii")
    print(f"wrote {zpath} ({zpath.stat().st_size / 1e6:.1f} MB, {len(names)} entries, sha256 {h[:12]}...)")
    print(f"wrote {side}")
    return zpath


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    a = ap.parse_args(argv)
    ver = version()
    print(f"building {SLUG} {ver}")
    dist = Path(a.dist)
    dist.mkdir(parents=True, exist_ok=True)
    st = stage(dist, ver)
    if not a.no_check:
        check_staged_imports(st)
    build_zip(st, dist)
    print(f"staging tree kept at {st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
