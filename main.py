import logging
import os
import sys
import time

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

from main_window import MainWindow
from config import load_config
from theme_loader import apply_theme

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
class _ThrottleWebsocketDnsNoise(logging.Filter):
    """
    مكتبة websocket-client تسجّل ERROR عند فشل DNS (ويندوز: [Errno 11001] getaddrinfo failed)
    لكل اتصال؛ لدينا خيطان (kline + ticker) فيُكرر السطر عدة مرات. نسمح برسالة واحدة كل فترة.
    """

    _last_emit = 0.0
    _interval_sec = 75.0

    def filter(self, record):
        try:
            text = record.getMessage().lower()
        except Exception:
            return True
        if "getaddrinfo" not in text and "11001" not in text:
            return True
        now = time.time()
        if now - self._last_emit < self._interval_sec:
            return False
        self._last_emit = now
        logging.getLogger("trading").warning(
            "WebSocket Binance: فشل DNS أو الشبكة (مثل ويندوز 11001 / getaddrinfo). "
            "تحقق من الإنترنت وVPN وجدار الحماية ووصول stream.binance.com — جرّب DNS مثل 8.8.8.8."
        )
        return False


# تخفيف رسائل websocket (اتصال/انقطاع) لتقليل الضجيج في السجل
for _name in ("websocket", "websocket-client"):
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.WARNING)
    _lg.addFilter(_ThrottleWebsocketDnsNoise())
log = logging.getLogger("trading")


def main():
    # اختياري: إن كانت النافذة سوداء/تتجمد عند شريط العنوان (ويندوز + GPU)، جرّب قبل التشغيل:
    # set CRYPTOTRADING_SW_OPENGL=1
    if os.environ.get("CRYPTOTRADING_SW_OPENGL", "").strip().lower() in ("1", "true", "yes", "on"):
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
        log.info("CRYPTOTRADING_SW_OPENGL=1 — تم تفعيل AA_UseSoftwareOpenGL")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    try:
        cfg = load_config()
        theme = cfg.get("theme", "dark")
        apply_theme(theme)
    except Exception as e:
        log.warning("Failed to load theme: %s", e)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()



