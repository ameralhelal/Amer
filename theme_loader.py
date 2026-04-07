# theme_loader.py — تحميل وتطبيق ثيم التطبيق (فاتح / قاتم)
import logging
import os

log = logging.getLogger("trading.theme")


def _qss_path(filename: str) -> str:
    """مسار ملف QSS بالنسبة لمجلد تشغيل التطبيق."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, filename)


def load_stylesheet(theme: str) -> str:
    """تحميل محتوى ملف QSS حسب الثيم: 'dark' أو 'light'."""
    filename = "theme_light.qss" if theme == "light" else "theme_dark.qss"
    path = _qss_path(filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        log.warning("Could not load theme %s: %s", theme, e)
        return ""


def apply_theme(theme: str) -> bool:
    """تطبيق الثيم على التطبيق بالكامل. theme = 'dark' أو 'light'. يُرجع True عند النجاح."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if not app:
        return False
    qss = load_stylesheet(theme)
    if qss:
        app.setStyleSheet(qss)
        log.info("Theme applied: %s", theme)
        return True
    return False
