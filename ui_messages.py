from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QMessageBox, QWidget


def show_auto_close_message(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    icon: QMessageBox.Icon = QMessageBox.Icon.Information,
    timeout_ms: int = 5000,
) -> QMessageBox:
    """
    نافذة منبثقة غير مُعيقة تُغلق تلقائياً بعد مدة.
    تُستخدم بدل QMessageBox.information/warning/critical لتجنب تراكم النوافذ.
    """
    box = QMessageBox(parent)
    try:
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    except Exception:
        pass
    box.setIcon(icon)
    box.setWindowTitle(title or "")
    box.setText(text or "")
    box.setStandardButtons(QMessageBox.StandardButton.NoButton)
    box.setModal(False)
    box.show()
    QTimer.singleShot(max(250, int(timeout_ms or 5000)), box.close)
    return box

