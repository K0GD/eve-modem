"""updater.py -- in-app upgrade helper for the DSES EVE modem (a copy of the Workbench's
updater.py, 2026-09-13; keep the two in step when one changes).

OS-neutral core: download a release zip, verify its SHA-256 against the
published `.sha256`, extract it, and install it either OVER the current install
(in place, with a rollback backup) or as a NEW copy in a chosen folder. Pure
Python (urllib / zipfile / shutil / hashlib / pathlib) so it can be tested
without Qt or a running GUI; the Qt install dialog and the per-OS desktop-
shortcut / relaunch steps live in the app and reuse install-shortcut.* .

Safety model: the SHA-256 is checked before anything is written, and an
in-place install backs up every file it overwrites into <target>/.dses_backup/
so a mid-way failure is rolled back rather than leaving a half-updated install.
"""

import hashlib
import shutil
import ssl
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

_CHUNK = 1 << 16
_USER_AGENT = "DSES-EVE-Modem-Updater"


# OpenSSL's X509_V_ERR_CERT_HAS_EXPIRED. The stdlib ssl module doesn't export
# the X509_V_ERR_* codes, so we hard-code it; the value 10 has been stable in
# OpenSSL for its entire history. It's the *only* verification failure the
# certifi fallback below is meant to rescue.
_CERT_HAS_EXPIRED = 10


def _is_expired_cert_error(reason):
    """True only for an OpenSSL "certificate has expired" verification failure —
    the stale/expired cached-intermediate case the certifi fallback exists for.

    Deliberately NOT true for "unable to get local issuer" / "self-signed
    certificate in chain" / other verification failures. Those are how an
    OS-level *distrust* of a CA surfaces (an admin removing or distrusting a
    root drops it from the loaded set, so a chain through it fails to build) —
    and the fallback must never override a deliberate distrust decision."""
    if not isinstance(reason, ssl.SSLCertVerificationError):
        return False
    if getattr(reason, "verify_code", None) == _CERT_HAS_EXPIRED:
        return True
    # verify_code can be absent on some construction paths; match the text too.
    return "certificate has expired" in (
        getattr(reason, "verify_message", "") or "").lower()


def open_url(url, timeout, headers=None):
    """Open `url` (returning the urllib response) verifying the server
    certificate against the OS trust store first, then falling back to the
    bundled certifi roots if — and only if — the OS store rejected it with a
    ``certificate has expired`` verification error.

    Why the fallback exists: on Windows the OS trust store can accumulate a
    stale, expired cached intermediate — e.g. the 2020-2025 cross-signed
    ``ISRG Root X2`` that Let's Encrypt retired on 2025-09-15. OpenSSL's path
    builder can then pick that expired copy instead of chaining to the still
    valid root and abort with ``certificate has expired``, even though the
    server's certificate is perfectly valid. certifi ships a clean, curated
    root set with no such stale cache, so retrying against it rescues exactly
    that case.

    Security: BOTH attempts fully verify the certificate (hostname + chain);
    the fallback never disables verification, it only swaps one trusted root
    set for another. It is scoped to the *expired* verify code specifically, so
    it cannot override an administrator's deliberate removal/distrust of a CA
    (which surfaces as a different verify error and re-raises here), and it
    cannot accept a genuinely expired/invalid *server* certificate (certifi
    rejects that too and the error propagates). The OS store is tried FIRST so
    enterprise private CAs and antivirus/proxy HTTPS-scanning roots (which live
    only in the OS store) keep working; certifi is a fallback, not a
    replacement. Non-certificate failures (timeouts, DNS, HTTP errors) are
    re-raised immediately without a second attempt."""
    def _open(context):
        req = urllib.request.Request(url, headers=headers or {})
        return urllib.request.urlopen(req, timeout=timeout, context=context)

    try:
        return _open(None)  # context=None -> OS default trust store
    except urllib.error.URLError as first_err:
        if not _is_expired_cert_error(getattr(first_err, "reason", None)):
            raise  # not the stale-expired-cache case — don't second-guess it
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            raise first_err  # can't fall back — surface the original error
        return _open(ctx)


def download(url, dest, progress=None):
    """Stream `url` to `dest`. progress(bytes_so_far, total_or_0) if given."""
    dest = Path(dest)
    with open_url(url, timeout=30, headers={"User-Agent": _USER_AGENT}) as r:
        total = int(r.headers.get("Content-Length", 0) or 0)
        got = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
    return dest


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_published_sha256(sha_url):
    """Read a `<hex>  filename` .sha256 file from a URL; return the hex digest."""
    with open_url(sha_url, timeout=15, headers={"User-Agent": _USER_AGENT}) as r:
        text = r.read().decode("utf-8", "replace")
    parts = text.split()
    if not parts:
        raise ValueError(f"empty or malformed .sha256 at {sha_url}")
    return parts[0].strip().lower()


def verify(zip_path, sha_url):
    """Raise ValueError unless `zip_path`'s SHA-256 matches the published one."""
    want = fetch_published_sha256(sha_url)
    got = sha256_file(zip_path).lower()
    if got != want:
        raise ValueError(f"checksum mismatch: downloaded {got}, expected {want}")
    return True


def sha256_url_for(download_url):
    """The `.sha256` sidecar URL for a release zip URL (…-<v>.zip -> …-<v>.sha256)."""
    if download_url.endswith(".zip"):
        return download_url[:-4] + ".sha256"
    return download_url + ".sha256"


def extract_release(zip_path, dest_dir):
    """Safely extract `zip_path` into `dest_dir`; return the single top-level
    folder inside it (the `eve-modem-<version>/` the bundle uses).

    Normalizes Windows-style backslash separators to '/': PowerShell's
    Compress-Archive writes non-spec zip entries using '\\', which
    zipfile.extractall() would otherwise turn into literal-backslash *filenames*
    on macOS/Linux (no folder, nothing overwritten, a silently-broken update).
    Also restores any Unix mode bits stored in the entry (so launcher.sh stays
    executable), and guards against absolute / '..' member paths (zip-slip)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    tops = set()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")   # normalize Windows seps
            if not name.strip("/"):
                continue
            p = Path(name)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"unsafe path in zip: {info.filename}")
            tops.add(name.split("/", 1)[0])
            target = dest_dir / name
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            mode = (info.external_attr >> 16) & 0o777   # Unix mode if present
            if mode:
                target.chmod(mode)
    if len(tops) == 1:
        return dest_dir / next(iter(tops))
    return dest_dir


# Files large enough that re-copying an identical one on every patch is wasteful
# (the bundled SigMF playback sample is ~160 MB and never changes between
# releases). When the size matches, an in-place install skips it.
_SKIP_IF_SAME_OVER = 50_000_000

# Shell launchers that MUST stay executable after an install. A zip built on
# Windows carries no Unix mode bits, so these would otherwise land non-executable
# and the macOS .app (which does `exec launcher.sh`) — or a plain ./launcher.sh —
# would fail after an update. chmod +x here is harmless on Windows.
_LAUNCHERS = ("launcher.sh", "launcher.command", "install-shortcut.command")


def _make_launchers_executable(install_dir):
    install_dir = Path(install_dir)
    for name in _LAUNCHERS:
        f = install_dir / name
        if f.is_file():
            try:
                f.chmod(f.stat().st_mode | 0o111)  # add exec for u/g/o
            except OSError:
                pass


def install_in_place(src_dir, target_dir):
    """Copy every entry from `src_dir` over `target_dir`, backing up anything it
    overwrites into `<target_dir>/.dses_backup/`. Rolls back on any error.
    Returns the backup directory (kept so the user can discard it later)."""
    src_dir = Path(src_dir)
    target_dir = Path(target_dir)
    backup = target_dir / ".dses_backup"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True)

    done = []  # (name, existed_before) for rollback
    try:
        for item in src_dir.iterdir():
            name = item.name
            dst = target_dir / name
            if (item.is_file() and dst.is_file()
                    and item.stat().st_size == dst.stat().st_size
                    and item.stat().st_size > _SKIP_IF_SAME_OVER):
                continue  # unchanged large file (the sample) — skip the churn
            existed = dst.exists()
            if existed:
                if dst.is_dir():
                    shutil.copytree(dst, backup / name)
                else:
                    shutil.copy2(dst, backup / name)
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
            done.append((name, existed))
        _make_launchers_executable(target_dir)
        return backup
    except Exception:
        # Roll back everything we touched, newest first.
        for name, existed in reversed(done):
            dst = target_dir / name
            if dst.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
            elif dst.exists():
                dst.unlink()
            saved = backup / name
            if existed and saved.exists():
                if saved.is_dir():
                    shutil.copytree(saved, dst)
                else:
                    shutil.copy2(saved, dst)
        raise


def install_new_copy(src_dir, parent_dir):
    """Copy the versioned release folder `src_dir` into `parent_dir`. Returns the
    new install path. Raises FileExistsError if it's already there."""
    src_dir = Path(src_dir)
    new_dir = Path(parent_dir) / src_dir.name
    if new_dir.exists():
        raise FileExistsError(f"{new_dir} already exists")
    shutil.copytree(src_dir, new_dir)
    _make_launchers_executable(new_dir)
    return new_dir
