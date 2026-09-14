"""In-app updates the Workbench way: a manifest.json on gpstime names the latest version
and its zip; the program checks at start-up (debounced) and on Help -> Check for updates,
shows the release notes, and can download, verify (SHA-256 sidecar), extract, and install
over itself or as a new copy, then relaunch. The pure-Python download/verify/install
core is eve/updater.py (a copy of the Workbench's); this module is the Qt side, ported
from dses_workbench.py 2026-09-13.

Manifest format (identical to the Workbench's):
    {"latest_version": "0.2.0",
     "download_url": "https://gpstime.com/sw_distribution/eve-modem/eve-modem-0.2.0.zip",
     "release_notes": "..."}
"""
from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt, Signal

from . import __version__
from . import updater

APP_NAME = "DSES-EVE-Modem"
GUIDE_PDF_BASENAME = "DSES_EVE_Modem_Operators_Guide.pdf"
DEFAULT_MANIFEST_URL = "https://gpstime.com/sw_distribution/eve-modem/manifest.json"


def parse_version(v: str):
    out = []
    for part in str(v).strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def friendly_check_error(exc, manifest_url: str = "") -> str:
    reason = getattr(exc, "reason", None)
    is_cert = (isinstance(exc, ssl.SSLCertVerificationError) or isinstance(reason, ssl.SSLCertVerificationError)
               or "CERTIFICATE_VERIFY_FAILED" in str(exc))
    if not is_cert:
        return f"{type(exc).__name__}: {exc}"
    raw = reason if reason is not None else exc
    detail = str(raw)
    if detail.startswith("(") and getattr(raw, "args", None):
        detail = str(raw.args[0])
    msg = ("The update server's security certificate could not be verified:\n"
           f"{detail}\n\nThis usually means a problem on THIS PC (antivirus HTTPS scanning, or a stale root "
           "certificate in the system store), but it can also mean the server's certificate has expired.")
    if "/" in manifest_url:
        msg += f"\n\nYou can still download the update by hand from:\n{manifest_url.rsplit('/', 1)[0]}/"
    return msg


def guide_url_from_download(download_url: str) -> str:
    download_url = (download_url or "").strip()
    if "/" not in download_url:
        return ""
    return f"{download_url.rsplit('/', 1)[0]}/{GUIDE_PDF_BASENAME}"


class UpdateChecker(QtCore.QObject):
    """Fetch the manifest on a background thread and compare with the running version."""
    update_available = Signal(str, str, str)   # version, url, notes
    no_update = Signal(str)
    check_failed = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings                # eve.app.Settings

    def check_now(self) -> None:
        threading.Thread(target=self._do_check, name="eve-update-check", daemon=True).start()

    def _do_check(self) -> None:
        url = str(self.settings.get("manifest_url")).strip()
        if not url:
            return
        # gpstime sometimes takes 10-15 s to accept the FIRST connection and is instant after
        # that (measured 11.3 s then 0.17 s, 2026-09-14; the Workbench saw the same in 2026-09).
        # An 8 s single try reported "urlopen error timed out" for a healthy server: allow 30 s
        # and try twice.
        data = None
        last_exc = None
        for attempt in range(2):
            try:
                with updater.open_url(url, timeout=30, headers={"User-Agent": f"{APP_NAME}/{__version__}"}) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except Exception as exc:        # noqa: BLE001
                last_exc = exc
        if data is None:
            self.check_failed.emit(friendly_check_error(last_exc, url))
            return
        latest = str(data.get("latest_version", "")).strip()
        download_url = str(data.get("download_url", "")).strip()
        notes = str(data.get("release_notes", "")).strip()
        if not latest:
            self.check_failed.emit("The manifest has no 'latest_version' field.")
            return
        self.settings.set("last_check_iso", datetime.now().isoformat(timespec="seconds"))
        self.settings.sync()
        if parse_version(latest) > parse_version(__version__):
            self.update_available.emit(latest, download_url, notes)
        else:
            self.no_update.emit(latest)


class UpdateNotificationDialog(QtWidgets.QDialog):
    dismissed_for_version = Signal(str)
    install_requested = Signal(str, str)

    def __init__(self, latest_version: str, download_url: str, release_notes: str, parent=None):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowTitle("Update available")
        self.setMinimumWidth(520)
        self._latest = latest_version
        self._url = download_url
        self._guide_url = guide_url_from_download(download_url)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(f"<h3>DSES EVE modem {latest_version} is available.</h3>"
                                       f"<p>You are running {__version__}.</p>"))
        lay.addWidget(QtWidgets.QLabel("<p><b>Install Update</b> downloads the zip, checks its SHA-256 against the "
                                       "published sidecar, and installs it over this copy (with a backup) or as a new "
                                       "copy beside it. Nothing is changed before the checksum matches.</p>"))
        if release_notes:
            notes = QtWidgets.QTextEdit()
            notes.setReadOnly(True)
            notes.setPlainText(release_notes)
            notes.setFixedHeight(150)
            lay.addWidget(QtWidgets.QLabel("<b>Release notes:</b>"))
            lay.addWidget(notes)
        if download_url:
            url_lbl = QtWidgets.QLabel(f"Download: <code>{download_url}</code>")
            url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            url_lbl.setWordWrap(True)
            lay.addWidget(url_lbl)
        btns = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("Install Update…")
        b.setEnabled(bool(download_url))
        b.setDefault(True)
        b.clicked.connect(lambda: self.install_requested.emit(self._url, self._latest))
        btns.addWidget(b)
        g = QtWidgets.QPushButton("Operator's guide (PDF)")
        g.setEnabled(bool(self._guide_url))
        g.clicked.connect(lambda: QtGui_open(self._guide_url))
        btns.addWidget(g)
        d = QtWidgets.QPushButton("Download .zip")
        d.setEnabled(bool(download_url))
        d.clicked.connect(lambda: QtGui_open(self._url))
        btns.addWidget(d)
        btns.addStretch(1)
        s = QtWidgets.QPushButton("Skip this version")
        s.clicked.connect(lambda: (self.dismissed_for_version.emit(self._latest), self.close()))
        btns.addWidget(s)
        r = QtWidgets.QPushButton("Remind me later")
        r.clicked.connect(self.close)
        btns.addWidget(r)
        lay.addLayout(btns)


def QtGui_open(url: str) -> None:
    from PySide6 import QtGui
    if url:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))


class InstallUpdateDialog(QtWidgets.QDialog):
    """Where to install: over this copy, or a new copy in a chosen folder."""

    def __init__(self, current_dir: Path, new_version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install update")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._current_dir = Path(current_dir)
        self._new_parent = Path.home()
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(f"<h3>Install version {new_version}</h3>"
                                       "<p>The download is checksum-verified before anything is changed.</p>"))
        self._rb_over = QtWidgets.QRadioButton("Update this installation (replace the current version; a backup is kept in .dses_backup)")
        self._rb_over.setChecked(True)
        lay.addWidget(self._rb_over)
        cur = QtWidgets.QLabel(f"&nbsp;&nbsp;&nbsp;&nbsp;<code>{self._current_dir}</code>")
        cur.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cur.setWordWrap(True)
        lay.addWidget(cur)
        self._rb_new = QtWidgets.QRadioButton("Install a new copy and keep the current version")
        lay.addWidget(self._rb_new)
        row = QtWidgets.QHBoxLayout()
        row.addSpacing(24)
        row.addWidget(QtWidgets.QLabel("Into:"))
        self._folder_lbl = QtWidgets.QLabel(str(self._new_parent))
        self._folder_lbl.setEnabled(False)
        row.addWidget(self._folder_lbl, 1)
        self._folder_btn = QtWidgets.QPushButton("Choose folder…")
        self._folder_btn.setEnabled(False)
        self._folder_btn.clicked.connect(self._choose)
        row.addWidget(self._folder_btn)
        lay.addLayout(row)
        self._shortcut = QtWidgets.QCheckBox("Add a desktop shortcut for the new copy")
        self._shortcut.setChecked(True)
        self._shortcut.setEnabled(False)
        srow = QtWidgets.QHBoxLayout()
        srow.addSpacing(24)
        srow.addWidget(self._shortcut)
        srow.addStretch(1)
        lay.addLayout(srow)
        self._rb_new.toggled.connect(lambda on: [w.setEnabled(on) for w in (self._folder_lbl, self._folder_btn, self._shortcut)])
        bb = QtWidgets.QDialogButtonBox()
        bb.addButton("Install", QtWidgets.QDialogButtonBox.AcceptRole)
        bb.addButton(QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _choose(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Install into folder", str(self._new_parent))
        if d:
            self._new_parent = Path(d)
            self._folder_lbl.setText(d)

    def result_choice(self):
        if self._rb_new.isChecked():
            return ("new_copy", self._new_parent, self._shortcut.isChecked())
        return ("in_place", self._current_dir, False)


class UpdateInstaller(QtCore.QObject):
    progress = Signal(int, int)
    status = Signal(str)
    done = Signal(bool, str, str)

    def __init__(self, download_url: str, mode: str, dest: Path, make_shortcut: bool, version_label: str, parent=None):
        super().__init__(parent)
        self._url, self._mode, self._dest, self._shortcut, self._version = download_url, mode, Path(dest), make_shortcut, version_label

    def start(self) -> None:
        threading.Thread(target=self._run, name="eve-update-install", daemon=True).start()

    def _run(self) -> None:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="eve_update_"))
        try:
            self.status.emit("Downloading the update…")
            zip_path = tmp / "update.zip"
            updater.download(self._url, zip_path, progress=lambda g, t: self.progress.emit(g, t))
            self.status.emit("Verifying the download (SHA-256)…")
            updater.verify(zip_path, updater.sha256_url_for(self._url))
            self.status.emit("Extracting…")
            root = updater.extract_release(zip_path, tmp / "x")
            if self._mode == "in_place":
                self.status.emit("Installing over the current version…")
                updater.install_in_place(root, self._dest)
                self.done.emit(True, "The update was installed over the current version.", str(self._dest))
            else:
                self.status.emit("Installing a new copy…")
                new_dir = updater.install_new_copy(root, self._dest)
                if self._shortcut:
                    self.status.emit("Creating the desktop shortcut…")
                    create_desktop_shortcut(new_dir)
                self.done.emit(True, f"Installed a new copy at:\n{new_dir}", str(new_dir))
        except Exception as exc:        # noqa: BLE001
            self.done.emit(False, f"{type(exc).__name__}: {exc}", "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def create_desktop_shortcut(install_dir: Path) -> None:
    install_dir = Path(install_dir)
    try:
        if sys.platform == "win32":
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(install_dir / "install-shortcut.ps1")], cwd=str(install_dir), timeout=60)
        else:
            subprocess.run(["bash", str(install_dir / "install-shortcut.command")], cwd=str(install_dir), timeout=60)
    except Exception as exc:        # noqa: BLE001
        print(f"shortcut creation failed: {exc}", file=sys.stderr)


def relaunch(install_dir: Path) -> None:
    """Start a fresh copy through the platform launcher, detached from this process."""
    install_dir = Path(install_dir)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", "launcher.bat"], cwd=str(install_dir),
                             creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)))
        else:
            subprocess.Popen(["bash", str(install_dir / "launcher.sh")], cwd=str(install_dir), start_new_session=True)
    except Exception as exc:        # noqa: BLE001
        print(f"relaunch failed: {exc}", file=sys.stderr)
