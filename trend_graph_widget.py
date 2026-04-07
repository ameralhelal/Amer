from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtCore import Qt


class TrendGraph(QWidget):
    def __init__(self):
        super().__init__()
        self.prices = []  # قائمة الأسعار للرسم

    def addPoint(self, price):
        self.prices.append(float(price))
        if len(self.prices) > 200:
            self.prices.pop(0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        margin_x = 12
        margin_y = 10
        inner_w = max(1, w - 2 * margin_x)
        inner_h = max(1, h - 2 * margin_y)

        # خلفية
        painter.fillRect(self.rect(), QColor(28, 28, 30))

        if len(self.prices) < 2:
            return

        max_price = max(self.prices)
        min_price = min(self.prices)
        diff = max_price - min_price if max_price != min_price else 1

        # شبكة أفقية خفيفة
        grid_pen = QPen(QColor(50, 52, 55), 1, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        for i in range(1, 4):
            y = margin_y + int(i * inner_h / 4)
            painter.drawLine(margin_x, y, w - margin_x, y)

        # لون الخط حسب الاتجاه (آخر segment: صعود أخضر، هبوط أحمر)
        last_up = self.prices[-1] >= self.prices[-2]
        if last_up:
            line_color = QColor(0, 200, 120)
        else:
            line_color = QColor(220, 80, 80)
        pen = QPen(line_color, 2)
        painter.setPen(pen)

        n = len(self.prices)
        # رسم الخط داخل الهوامش
        for i in range(1, n):
            t1 = (i - 1) / (n - 1) if n > 1 else 0
            t2 = i / (n - 1) if n > 1 else 1
            x1 = margin_x + int(t1 * inner_w)
            x2 = margin_x + int(t2 * inner_w)
            y1 = margin_y + inner_h - int(((self.prices[i - 1] - min_price) / diff) * inner_h)
            y2 = margin_y + inner_h - int(((self.prices[i] - min_price) / diff) * inner_h)
            painter.drawLine(x1, y1, x2, y2)
