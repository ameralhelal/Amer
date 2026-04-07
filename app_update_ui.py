"""واجهة التحقق من تحديث التطبيق (قائمة إعدادات لوحة التوصية)."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal, QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox

from config import load_config
from translations import tr

log = logging.getLogger("trading.app_update")


class UpdateCheckThread(QThread):
    finished_with = pyqtSignal(dict)

    def __init__(self, manifest_url: str):
        super().__init__()
        self._manifest_url = manifest_url

    def run(self):
        try:
            from update_check import run_check_sync
            from app_version import APP_VERSION

            self.finished_with.emit(run_check_sync(self._manifest_url, APP_VERSION))
        except Exception as e:
            log.warning("Update check thread failed: %s", e, exc_info=True)
            self.finished_with.emit(
                {"status": "error", "message": str(e) or type(e).__name__}
            )


def show_update_check_result(parent, result: dict) -> None:
    st = (result or {}).get("status")
    if st == "no_url":
        QMessageBox.information(parent, tr("rec_update_title"), tr("rec_update_no_url"))
        return
    if st == "up_to_date":
        ver = (result or {}).get("remote_version") or ""
        QMessageBox.information(parent, tr("rec_update_title"), tr("rec_update_up_to_date").format(version=ver))
        return
    if st == "update_available":
        from app_version import APP_VERSION

        remote = (result or {}).get("remote_version") or ""
        notes = (result or {}).get("notes") or ""
        msg = tr("rec_update_available").format(remote=remote, current=APP_VERSION)
        if notes:
            msg = msg + "\n\n" + notes
        dl = (result or {}).get("download_url") or ""
        if dl:
            msg = msg + "\n\n" + tr("rec_update_open_download")
            ans = QMessageBox.question(
                parent,
                tr("rec_update_title"),
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(dl))
        else:
            QMessageBox.information(parent, tr("rec_update_title"), msg)
        return
    err = (result or {}).get("message") or ""
    QMessageBox.warning(parent, tr("rec_update_title"), tr("rec_update_error").format(msg=err))


def _emit_status(parent, text: str) -> None:
    try:
        sig = getattr(parent, "status_bar_message", None)
        if sig is not None and hasattr(sig, "emit"):
            sig.emit(text)
    except Exception:
        pass


def start_update_check(parent) -> None:
    """يبدأ فحص التحديث في خيط خلفي؛ parent يحتفظ بمرجع `_update_check_thread`."""
    t = getattr(parent, "_update_check_thread", None)
    if t is not None and t.isRunning():
        _emit_status(parent, tr("rec_check_update_busy"))
        return
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    url = str(cfg.get("update_manifest_url") or "").strip()
    if not url:
        show_update_check_result(parent, {"status": "no_url"})
        return

    _emit_status(parent, tr("rec_check_update_busy"))

    th = UpdateCheckThread(url)
    parent._update_check_thread = th

    def _on_result(r: dict) -> None:
        try:
            show_update_check_result(parent, r)
        finally:
            if getattr(parent, "_update_check_thread", None) is th:
                parent._update_check_thread = None

    th.finished_with.connect(_on_result, Qt.ConnectionType.QueuedConnection)
    th.finished.connect(th.deleteLater)
    th.start()
