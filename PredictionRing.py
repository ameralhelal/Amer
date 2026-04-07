from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QPen, QColor, QFont
from PyQt6.QtCore import Qt, QRectF

class PredictionRing(QWidget):
    def __init__(self):
        super().__init__()
        self.percent = 0  # 0 → 100

    def set_percent(self, value):
        self.percent = max(0, min(100, value))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # منع امتداد القوس/القلم خارج مستطيل الويدجت فيتداخل بصرياً مع الصف التالي عند ارتفاع الإطار الضيق
        painter.setClipRect(self.rect())

        # حلقة رفيعة + محيط قاتم + قوس أخضر/أحمر حسب النسبة
        w = self.width()
        h = self.height()
        size = min(w, h)
        margin = max(4, min(9, int(size * 0.14)))
        rect = QRectF(margin, margin, size - (margin * 2), size - (margin * 2))

        # سمك الحلقة يتناسب مع حجم الويدجت
        pen_width = max(3, min(6, int(size * 0.09)))
        pen = QPen()
        pen.setWidth(pen_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        # المحيط القاتم (خلفية ثابتة)
        pen.setColor(QColor(45, 45, 52))
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        # رسم القوس الملون من أعلى (12 o'clock)
        p = float(self.percent)
        angle = int((p / 100.0) * 360 * 16)

        if p >= 50:
            # أخضر يزيد مع النسبة
            pen.setColor(QColor(0, 220, 110))
            painter.setPen(pen)
            painter.drawArc(rect, -90 * 16, angle)
            # تلميح بسيط للأحمر (فقط عندما ليست 100%)
            if p < 100:
                pen.setColor(QColor(200, 60, 60))
                painter.setPen(pen)
                red_angle = int(((100 - p) / 100.0) * 360 * 16)
                painter.drawArc(rect, -90 * 16 + angle, red_angle)
        else:
            # أحمر يزيد عندما تقل النسبة
            pen.setColor(QColor(220, 70, 70))
            painter.setPen(pen)
            painter.drawArc(rect, -90 * 16, angle)
            # تلميح بسيط للأخضر (فقط عندما ليست 0%)
            if p > 0:
                pen.setColor(QColor(0, 200, 100))
                painter.setPen(pen)
                green_angle = int((p / 100.0) * 360 * 16)
                painter.drawArc(rect, -90 * 16, green_angle)

        # رسم الرقم في المنتصف — لون حسب الحالة (أخضر إذا ≥50، أحمر إذا <50)
        if p >= 50:
            text_color = QColor(0, 220, 110)
        else:
            text_color = QColor(220, 70, 70)
        painter.setPen(text_color)
        # خط أصغر داخل الدائرة (لوحة التوصية المضغوطة)
        font_size = max(7, min(11, int(size * 0.17) if size > 0 else 7))
        painter.setFont(QFont("Segoe UI", font_size, QFont.Weight.Bold))
        text = f"{int(self.percent)}%"
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
