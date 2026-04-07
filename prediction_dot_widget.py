from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QBrush


class PredictionDot(QWidget):
    """
    شريط توقّع مرتبط بتحليل الذكاء:
    - شراء (up): يمتلئ الشريط بالأخضر من اليسار، كلما زادت قوة الإشارة زاد الامتلاء.
    - بيع (down): يمتلئ الشريط بالأحمر من اليمين، كلما زادت قوة الإشارة زاد الامتلاء.
    - محايد (neutral): بلا لون (أو خط رمادي خفيف).
    """

    def __init__(self):
        super().__init__()
        self.direction = "neutral"   # up / down / neutral
        self.strength = 0            # 0 → 100
        self.setMinimumHeight(10)
        self.setMinimumWidth(60)

    def setPrediction(self, direction, strength):
        """يُستدعى من لوحة الذكاء حسب التحليل (شراء / بيع / انتظار)."""
        self.direction = direction
        self.strength = max(0, min(100, float(strength)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        margin = 2
        bar_h = max(4, h - 2 * margin)

        # خلفية الشريط: رمادي خفيف جداً (محايد)
        bg = QColor(50, 50, 55)
        painter.fillRect(margin, margin, w - 2 * margin, bar_h, bg)

        # في الحياد: لا لون إضافي (يبقى الرمادي فقط)
        if self.direction == "neutral" or self.strength <= 0:
            return

        # نسبة الامتلاء حسب قوة الإشارة (0–100 → 0–1)
        fill_ratio = self.strength / 100.0
        fill_w = int((w - 2 * margin) * fill_ratio)
        if fill_w <= 0:
            return

        y_bar = margin
        x_left = margin

        if self.direction == "up":
            # شراء: امتلاء بالأخضر من اليسار، كلما زادت إشارة الشراء زاد الخط
            color = QColor(0, 200, 100)
            painter.fillRect(x_left, y_bar, fill_w, bar_h, QBrush(color))
        elif self.direction == "down":
            # بيع: امتلاء بالأحمر من اليمين، كلما زادت إشارة البيع زاد الخط
            color = QColor(220, 70, 70)
            x_start = (w - 2 * margin) - fill_w + margin
            painter.fillRect(x_start, y_bar, fill_w, bar_h, QBrush(color))
