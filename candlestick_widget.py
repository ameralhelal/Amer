from PyQt6.QtWidgets import QWidget, QMenu
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QWheelEvent, QMouseEvent, QPolygonF
from PyQt6.QtCore import Qt, QTimer, QRectF, QPoint, QPointF, pyqtSignal
from format_utils import format_price
from datetime import datetime, timezone
import math

# أقصى عدد شموع يُعرض ويُرسم في الشارت (أخف من 500؛ المحرّك/WebSocket يبقيان على حمولة أطول).
_MAX_CHART_CANDLES = 300


def _candle_open_time_ms(c) -> int | None:
    """وقت فتح الشمعة بالمللي ثانية (Binance) إن وُجد."""
    if isinstance(c, dict):
        t = c.get("open_time") or c.get("time") or c.get("openTime")
        if t is not None:
            try:
                return int(t)
            except (TypeError, ValueError):
                pass
        return None
    if isinstance(c, (list, tuple)) and len(c) > 5:
        try:
            return int(c[5])
        except (TypeError, ValueError):
            pass
    return None


def _candle_ms_to_local(ms: int) -> datetime:
    """وقت فتح الشمعة (UTC من المنصة) → عرض بتوقيت الجهاز المحلي."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).astimezone()


def _format_candle_datetime_ms(ms: int | None) -> str:
    if ms is None:
        return ""
    try:
        return _candle_ms_to_local(ms).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return ""


class CandlestickChart(QWidget):
    # إشارة: النقر على سعر التوصية (أخضر=حد الشراء، أحمر=حد البيع)
    recommendation_clicked = pyqtSignal(float, str)  # price, "buy" | "sell"
    # إشارة: كليك يمين على الشارت → اختيار حد شراء أو وقف خسارة
    right_click_stop_loss = pyqtSignal(float)
    right_click_limit_buy = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        # عرض عمود السعر (يمين الشارت) — تكبيره لعرض أرقام الأسعار الطويلة كاملة
        self._price_axis_width = 92
        self.candles = []
        self._current_price = None
        self._view_start = 0  # فهرس أول شمعة مرئية (≈ floor(_view_start_f))
        self._view_start_f = 0.0  # موضع أفقي سلس (كسور شمعة) — للتحريك عند التصغير
        self._scroll_frac = 0.0  # كسر داخل الشمعة الأولى المعروضة
        self._visible_count = 0
        self._y_zoom = 1.0
        self._y_pan = 0.0  # إزاحة عمودية (بالسعر) لتحريك الشارت لأعلى/أسفل
        self._drag_start_x = None
        self._drag_start_y = None
        self._drag_start_view = 0
        self._last_range_diff = 1.0
        self._last_chart_h = 300
        self._follow_latest = True  # تحريك تلقائي مع الشموع (مثل TradingView)
        self._right_padding_slots = 10  # فراغ يميناً (بعد آخر شمعة)
        # سلوك شبيه TradingView: overscroll ديناميكي حسب عدد الشموع الظاهرة.
        self._max_left_overscroll_candles = 24.0
        self._max_right_overscroll_candles = 24.0
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self._throttled_update)
        self._pending_repaint = False
        self._crosshair_timer = QTimer(self)
        self._crosshair_timer.setSingleShot(True)
        self._crosshair_timer.timeout.connect(self.update)
        self._candle_countdown_text = ""
        self._analysis_levels = []  # [(price, label, color), ...] للرسم على الشارت (دعم/مقاومة/محور)
        self._show_analysis_levels = True
        self._vwap_price = None  # خط VWAP على الشارت
        self._crosshair_y = None  # موقع Y للماوس لرسم خط التتبع المنقط + السعر
        self._crosshair_x = None
        # لتثبيت الحركة العمودية أثناء الزوم الأفقي (حتى لا "تقفز" الشموع للأعلى/للأسفل)
        self._last_display_center = None  # مركز النطاق المعروض (بعد الزوم/السحب)
        # أدوات الرسم: خط، قناة، فيبوناتشي، مستطيل
        self._drawings = []  # [{"type": "line"|"channel"|"fib"|"hline"|"rect", "data": ...}, ...]
        self._draw_mode = None  # None | "hline" | "line" | "channel" | "fib" | "rect"
        self._draw_start = None  # (candle_idx, price) عند بدء السحب
        self._draw_preview = None  # (candle_idx, price) أثناء السحب
        # تعديل رسم موجود: سحب مقبض نهاية الخط (وضع التحريك فقط)
        self._edit_draw = None  # {"idx": int, "handle": int} | None
        self._draw_handle_radius_px = 7.0
        self._rec_buy_price = None   # سعر الشراء من التوصية — مثلث أخضر على المقياس
        self._rec_sell_price = None  # سعر البيع من التوصية — مثلث أحمر على المقياس
        # نوع الشارت (مثل TradingView): candle | heikin_ashi | line | area | hollow
        self._chart_type = "candle"
        self._overlay_ma20_val = None
        self._overlay_ma50_val = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._chart_interval = "1m"  # لعرض الفترة في شريط الحالة الزمني
        self._hover_axis_text = ""  # تاريخ/وقت الشمعة تحت المؤشر (مثل TradingView)
        self._profile_reserved = 52  # عرض ملف الحجم + فراغ قبل عمود السعر
        # شارة المؤشر المركّب (زاوية الشارت)
        self._composite_badge_text = ""
        self._composite_badge_bg = "#2d3748"
        self._composite_badge_fg = "#ffffff"
        self._chart_interaction_tooltip = (
            "عجلة: تكبير | سحب: تحريك | دبل كليك: إعادة العرض | كليك يمين: حد شراء أو وقف خسارة"
        )
        self.setToolTip(self._chart_interaction_tooltip)
        # قائمة السياق عبر الإشارة الرسمية — أوثق من معالجة الزر الأيمن داخل mousePressEvent فقط
        # (وتسمح بقراءة السعر من عمود الأسعار حيث كان xy_to_candle_price يعيد None).
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_custom_context_menu)

    def _context_menu_price_at(self, px: float, py: float) -> float | None:
        """سعر المستوى عند (px,py) لقائمة حد الشراء/SL؛ يشمل منطقة الشموع وعمود السعر."""
        geo = self._compute_geometry()
        if not geo:
            return None
        chart_top = int(geo["chart_top"])
        chart_bottom = int(geo["chart_bottom"])
        left_margin = int(geo["left_margin"])
        w = int(self.width())
        if not (chart_top <= py <= chart_bottom):
            return None
        if px < left_margin or px > max(left_margin, w - 1):
            return None
        pt = geo["xy_to_candle_price"](px, py)
        if pt is not None:
            return float(pt[1])
        range_min = float(geo["range_min"])
        range_diff = float(geo["range_diff"])
        chart_h = max(1, int(geo["chart_h"]))
        price = range_min + (float(chart_bottom) - py) / float(chart_h) * range_diff
        return float(price)

    def _on_custom_context_menu(self, pos: QPoint):
        if not self.candles:
            return
        price = self._context_menu_price_at(float(pos.x()), float(pos.y()))
        if price is None or not math.isfinite(price) or price <= 0:
            return
        menu = QMenu(self)
        act_buy = menu.addAction("تعيين كحد شراء")
        act_sl = menu.addAction("تعيين كوقف خسارة")
        act_buy.triggered.connect(lambda _=False, p=price: self.right_click_limit_buy.emit(p))
        act_sl.triggered.connect(lambda _=False, p=price: self.right_click_stop_loss.emit(p))
        menu.exec(self.mapToGlobal(pos))

    def _resolved_visible_count(self, n: int) -> int:
        vc = int(self._visible_count)
        if vc <= 0:
            vc = n
        return max(1, min(vc, max(1, n)))

    def _horizontal_scroll_bounds(self, n: int, vc: int) -> tuple[float, float]:
        """(أدنى, أقصى) لـ _view_start_f. السالب = فراغ يسار أول شمعة دون بيانات إضافية."""
        vc = max(1, min(int(vc), max(1, n)))
        base_hi = float(max(0, n - vc))
        # ديناميكي: تقريبا عرض شاشة واحد؛ طبيعي أكثر عند zoom out/zoom in.
        dyn = max(8.0, min(80.0, vc * 0.9))
        left_over = min(dyn, float(getattr(self, "_max_left_overscroll_candles", 24.0)))
        right_over = min(dyn, float(getattr(self, "_max_right_overscroll_candles", 24.0)))
        hi = base_hi + right_over
        lo = -left_over
        return lo, hi

    def _clamp_view_start_f(self, n: int, vc: int, vs_f: float) -> float:
        lo, hi = self._horizontal_scroll_bounds(n, vc)
        return max(lo, min(hi, float(vs_f)))

    def _apply_horizontal_view(self, n: int, vc: int) -> list:
        """يضبط _view_start_f ضمن الحدود و _view_start و _scroll_frac؛ يُرجع الشموع المرئية."""
        if not n:
            self._scroll_frac = 0.0
            return []
        vc = max(1, min(int(vc), n))
        lo, hi = self._horizontal_scroll_bounds(n, vc)
        vs_f = max(lo, min(hi, float(self._view_start_f)))
        self._view_start_f = vs_f
        max_start = float(max(0, n - vc))
        if vs_f < 0:
            start = 0
            self._scroll_frac = vs_f
        elif vs_f > max_start:
            # فراغ يمين آخر شمعة (future space) — لا نقصّ الشموع، فقط إزاحة أفقية موجبة.
            start = int(max_start)
            self._scroll_frac = vs_f - max_start
        else:
            start = int(math.floor(vs_f))
            self._scroll_frac = vs_f - start
        # حصر الإزاحة داخل مجال منطقي (تمنع حركة مبالغ فيها وغير طبيعية).
        max_frac = max(0.0, min(float(vc) * 0.9, 80.0))
        self._scroll_frac = max(-max_frac, min(max_frac, float(self._scroll_frac)))
        self._view_start = start
        end = min(start + vc, n)
        return self.candles[start:end]

    def _compute_visible_price_center_no_pan(self, visible: list):
        """حساب مركز نطاق السعر للشموع المرئية (بعد y_zoom وقبل y_pan)."""
        if not visible:
            return None
        all_prices = []
        for c in visible:
            all_prices.extend([c.get("high", 0), c.get("low", 0), c.get("open", 0), c.get("close", 0)])
        if self._current_price is not None:
            all_prices.append(self._current_price)
        max_price = max(all_prices)
        min_price = min(all_prices)
        diff = max_price - min_price if max_price != min_price else 1.0
        margin = max(diff * 0.02, (max_price + min_price) * 0.0001)
        range_min = min_price - margin
        range_max = max_price + margin
        center = (range_min + range_max) / 2.0
        half = (range_max - range_min) / 2.0
        half = half / max(0.2, self._y_zoom)
        range_min = center - half
        range_max = center + half
        return (range_min + range_max) / 2.0

    def addCandle(self, candle):
        """إضافة شمعة واحدة (للتوافق مع الاستدعاءات القديمة)."""
        self.candles.append(candle)
        if len(self.candles) > _MAX_CHART_CANDLES:
            self.candles.pop(0)
        self.update()

    def _throttled_update(self):
        self._pending_repaint = False
        self.update()

    def setCandles(self, candles_list: list):
        """تحديث قائمة الشموع مع تخفيف التكرار لتقليل التجمّد."""
        if not candles_list:
            return
        self.candles = []
        for c in candles_list:
            if isinstance(c, dict):
                self.candles.append(c)
            else:
                o, h, low, close, v = c[0], c[1], c[2], c[3], c[4]
                d = {"open": o, "high": h, "low": low, "close": close, "volume": v}
                if len(c) > 5:
                    try:
                        d["open_time"] = int(c[5])
                    except (TypeError, ValueError):
                        pass
                self.candles.append(d)
        if len(self.candles) > _MAX_CHART_CANDLES:
            self.candles = self.candles[-_MAX_CHART_CANDLES:]
        n = len(self.candles)
        if self._visible_count > 0 and n:
            vc = self._resolved_visible_count(n)
            max_start = float(max(0, n - vc))
            if self._follow_latest:
                self._view_start_f = max_start
            else:
                self._view_start_f = self._clamp_view_start_f(n, vc, float(self._view_start_f))
            self._apply_horizontal_view(n, vc)
        if not self._pending_repaint:
            self._pending_repaint = True
            self._update_timer.start(50)

    def resetView(self):
        """إعادة العرض الافتراضي: متابعة آخر الشموع مع فراغ يميناً (سريع وواضح)."""
        n = len(self.candles)
        self._follow_latest = True
        # عرض عدد مناسب من الشموع، مع فراغ يميناً لسهولة القراءة
        self._visible_count = min(90, n) if n else 0
        vs = max(0, n - self._visible_count) if self._visible_count else 0
        self._view_start_f = float(vs)
        self._view_start = vs
        self._y_zoom = 1.0
        self._y_pan = 0.0
        self.update()

    def _visible_candles(self):
        """الشموع المعروضة حالياً (حسب التكبير والسحب)."""
        n = len(self.candles)
        if not n:
            self._scroll_frac = 0.0
            return []
        vc = self._resolved_visible_count(n)
        return self._apply_horizontal_view(n, vc)

    def set_chart_interval(self, interval: str):
        """فترة الشارت (1m، 5m، …) لعرضها مع الوقت عند التمرير."""
        self._chart_interval = str(interval or "1m")

    def wheelEvent(self, event: QWheelEvent):
        """مثل TradingView: عجلة على عمود السعر = تكبير عمودي، عجلة على منطقة الشارت = تكبير أفقي."""
        if not self.candles:
            return
        delta = event.angleDelta().y()
        w = self.width()
        price_axis_width = int(getattr(self, "_price_axis_width", 92))
        # موقع المؤشر بالنسبة للشارت (حتى لو الحدث مُمرَّر من ويدجت آخر)
        try:
            global_pt = event.globalPosition()
            local_pt = self.mapFromGlobal(QPoint(int(global_pt.x()), int(global_pt.y())))
            mouse_x = local_pt.x()
        except Exception:
            mouse_x = event.position().x()
        on_price_axis = mouse_x >= (w - price_axis_width)

        if on_price_axis:
            # تكبير عمودي — من عند عمود السعر (مثل TradingView)
            if delta > 0:
                self._y_zoom = min(5.0, self._y_zoom * 1.15)
            else:
                self._y_zoom = max(0.2, self._y_zoom / 1.15)
        else:
            # تكبير أفقي — إذا كنت عند آخر الشمعة نُثبّت اليمين؛ وإلا نُبقي موضع التمرير (لا نفرض follow_latest)
            n = len(self.candles)
            old_center = getattr(self, "_last_display_center", None)
            old_vc = int(self._visible_count) if self._visible_count > 0 else 0
            if old_vc <= 0:
                old_vc = max(1, n - 1) if n > 1 else 1
            _, old_hi = self._horizontal_scroll_bounds(n, old_vc)
            at_right = self._view_start_f >= float(old_hi) - 0.12

            if self._visible_count <= 0:
                self._visible_count = max(1, n - 1) if n > 1 else 1
                self._view_start_f = float(max(0, n - self._visible_count))

            if delta > 0:
                self._visible_count = max(10, int(self._visible_count * 0.85))
            else:
                self._visible_count = min(n, int(self._visible_count * 1.2))
                # اسمح بعرض كل الشموع عند التصغير الشديد (كان n-1 فيخفي أول/آخر شمعة في حالات)
                if self._visible_count > n:
                    self._visible_count = n

            new_vc = max(1, min(int(self._visible_count), n))
            self._visible_count = new_vc
            _, new_hi = self._horizontal_scroll_bounds(n, new_vc)
            if at_right:
                self._follow_latest = True
                self._view_start_f = float(new_hi)
            else:
                self._follow_latest = False
                self._view_start_f = self._clamp_view_start_f(n, new_vc, float(self._view_start_f))
            self._apply_horizontal_view(n, new_vc)
            # منع "قفز" الشموع عمودياً بسبب تغيّر auto-range عند تغيير عدد الشموع
            if old_center is not None:
                new_visible = self._visible_candles()
                new_center_no_pan = self._compute_visible_price_center_no_pan(new_visible)
                if new_center_no_pan is not None:
                    self._y_pan = float(old_center) - float(new_center_no_pan)
        event.accept()
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        px, py = event.position().x(), event.position().y()
        if event.button() != Qt.MouseButton.LeftButton or not self.candles:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        for rx, ry, rw, rh, pval, side in getattr(self, "_rec_click_rects", []):
            if rx <= px <= rx + rw and ry <= py <= ry + rh:
                self.recommendation_clicked.emit(float(pval), side)
                return
        geo = self._compute_geometry()
        # مقابض نهايات الخط — في وضع التحريك (✋) فقط
        if not self._draw_mode and geo:
            hit = self._drawing_handle_hit(px, py, geo)
            if hit is not None:
                self._edit_draw = {"idx": int(hit[0]), "handle": int(hit[1])}
                self._drag_start_x = None
                self._drag_start_y = None
                self._follow_latest = False
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
                return
        if self._draw_mode and geo and self._draw_mode not in ("hline",):
            pt = geo["xy_to_candle_price"](event.position().x(), event.position().y())
            if pt is not None:
                self._draw_start = pt
                self._draw_preview = pt
                self.update()
                return
        if self._draw_mode == "hline":
            return  # لا نبدأ التحريك — النقرة للخط الأفقي فقط
        self._drag_start_x = event.position().x()
        self._drag_start_y = event.position().y()
        self._drag_start_view = self._view_start
        self._drag_start_view_f = self._view_start_f
        self._follow_latest = False
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._draw_start is not None and self._draw_mode:
            geo = self._compute_geometry()
            if geo:
                pt = geo["xy_to_candle_price"](event.position().x(), event.position().y())
                if pt is not None:
                    self._draw_preview = pt
            self.update()
            return
        if getattr(self, "_edit_draw", None) is not None:
            self._apply_edit_draw_move(event.position().x(), event.position().y())
            self.update()
            return
        if self._drag_start_x is not None and self._drag_start_y is not None:
            n = len(self.candles)
            vc = int(self._visible_count)
            if vc <= 0:
                vc = max(1, n - 1) if n > 1 else 1
            vc = max(1, min(vc, n))
            price_axis_width = int(getattr(self, "_price_axis_width", 92))
            profile_reserved = int(getattr(self, "_profile_reserved", 52))
            chart_right = self.width() - price_axis_width
            left_margin = 28
            right_margin = 8
            plot_right = chart_right - right_margin - profile_reserved
            w_plot = max(1, (plot_right - left_margin) * 0.96)
            candle_width = w_plot / vc
            delta_x = event.position().x() - self._drag_start_x
            # تحريك أفقي سلس: نحدّث view_start_f (المعتمد في الرسم) وليس view_start فقط.
            delta_candles_f = -(delta_x / max(1e-9, candle_width))
            base_view_f = float(getattr(self, "_drag_start_view_f", float(self._drag_start_view)))
            self._view_start_f = self._clamp_view_start_f(n, vc, base_view_f + delta_candles_f)
            self._visible_count = vc
            # اجعل الاتجاه طبيعي: تحريك الماوس للأعلى يرفع الشارت للأعلى
            delta_y = event.position().y() - self._drag_start_y
            if self._last_chart_h > 0 and self._last_range_diff != 0:
                self._y_pan += delta_y * (self._last_range_diff / self._last_chart_h)
            self._drag_start_y = event.position().y()
            self.update()
        else:
            self._crosshair_x = int(event.position().x())
            self._crosshair_y = int(event.position().y())
            geo = self._compute_geometry()
            if geo and self.candles:
                pt = geo["xy_to_candle_price"](event.position().x(), event.position().y())
                if pt is not None:
                    ci, _ = pt
                    ms = _candle_open_time_ms(self.candles[ci])
                    self._hover_axis_text = _format_candle_datetime_ms(ms)
                else:
                    self._hover_axis_text = ""
            else:
                self._hover_axis_text = ""
            self._crosshair_timer.stop()
            self._crosshair_timer.start(24)
            mx, my = float(event.position().x()), float(event.position().y())
            if (
                not self._draw_mode
                and self._drawings
                and geo
                and self._drawing_handle_hit(mx, my, geo) is not None
            ):
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._edit_draw = None
            # خط أفقي: نقرة واحدة → رسم خط عند السعر
            if self._draw_mode == "hline":
                geo = self._compute_geometry()
                if geo and self.candles:
                    pt = geo["xy_to_candle_price"](event.position().x(), event.position().y())
                    if pt is not None:
                        self._drawings.append({"type": "hline", "data": (float(pt[1]),)})
                        self.update()
                return
            if self._draw_start is not None and self._draw_preview is not None and self._draw_mode:
                i0, p0 = self._draw_start
                i1, p1 = self._draw_preview
                if (i0, p0) != (i1, p1):
                    if self._draw_mode == "line":
                        self._drawings.append({"type": "line", "data": (i0, p0, i1, p1)})
                    elif self._draw_mode == "channel":
                        self._drawings.append({"type": "channel", "data": (i0, p0, i1, p1)})
                    elif self._draw_mode == "fib":
                        ph, pl = max(p0, p1), min(p0, p1)
                        self._drawings.append({"type": "fib", "data": (min(i0, i1), ph, max(i0, i1), pl)})
                    elif self._draw_mode == "rect":
                        ph, pl = max(p0, p1), min(p0, p1)
                        self._drawings.append({"type": "rect", "data": (min(i0, i1), ph, max(i0, i1), pl)})
                self._draw_start = None
                self._draw_preview = None
                self.update()
                return
            self._drag_start_x = None
            self._drag_start_y = None
            self.unsetCursor()

    def leaveEvent(self, event):
        """إخفاء خط التتبع عند خروج الماوس من الشارت."""
        super().leaveEvent(event)
        try:
            self._crosshair_timer.stop()
        except Exception:
            pass
        self._crosshair_y = None
        self._crosshair_x = None
        self._hover_axis_text = ""
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """نقرة مزدوجة = إعادة العرض الافتراضي (بداية الشموع + مقياس عادي)."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.resetView()

    def setCurrentPrice(self, price: float):
        """تحديث السعر الحالي لرسم الخط الأفقي (فوري)."""
        self._current_price = float(price) if price is not None else None
        # لا نُخضِع خط السعر للتأخير حتى لا يبدو متأخراً عن الشموع
        self.update()

    def setCandleCountdown(self, seconds_left: int, interval: str = ""):
        """تحديث نص انتهاء الشمعة ليظهر تحت السعر داخل نفس مربع السعر."""
        try:
            sec = max(0, int(seconds_left))
        except (TypeError, ValueError):
            sec = 0
        m, s = divmod(sec, 60)
        iv = f" {interval}" if interval else ""
        self._candle_countdown_text = f"{m}:{s:02d}{iv}"
        self.update()

    def setDrawMode(self, mode: str):
        """وضع الرسم: None (تحريك)، 'hline' (خط أفقي)، 'line'، 'channel'، 'fib'، 'rect' (مستطيل منطقة)."""
        allowed = (None, "hline", "line", "channel", "fib", "rect")
        self._draw_mode = mode if mode in allowed else None
        self._draw_start = None
        self._draw_preview = None
        self._edit_draw = None
        self.update()

    def setCompositeBadge(
        self,
        text: str = "",
        bg_hex: str = "#2d3748",
        fg_hex: str = "#ffffff",
        *,
        explainer: str | None = None,
    ):
        """نص قصير في زاوية الشارت (مؤشر مركّب). نص فارغ = إخفاء."""
        self._composite_badge_text = (text or "").strip()
        self._composite_badge_bg = str(bg_hex or "#2d3748")
        self._composite_badge_fg = str(fg_hex or "#ffffff")
        base = getattr(self, "_chart_interaction_tooltip", "") or ""
        ex = (explainer or "").strip()
        if self._composite_badge_text and ex:
            self.setToolTip(f"{ex}\n\n{base}".strip())
        else:
            self.setToolTip(base)
        self.update()

    def setChartType(self, chart_type: str):
        """نوع الشارت: 'candle' | 'heikin_ashi' | 'line' | 'area' | 'hollow' (مثل TradingView)."""
        # تثبيت عرض الشموع على Candles فقط (كما طلبت)
        self._chart_type = "candle"
        self.update()

    def setOverlayMAs(self, ma20: float = None, ma50: float = None):
        """تعيين قيم MA20 و MA50 الحالية (لرسم خط أفقي إن لم تُحسب السلسلة من الشموع)."""
        self._overlay_ma20_val = float(ma20) if ma20 is not None and float(ma20) > 0 else None
        self._overlay_ma50_val = float(ma50) if ma50 is not None and float(ma50) > 0 else None
        self.update()

    def _compute_heikin_ashi(self, candles: list) -> list:
        """تحويل شموع OHLC إلى شموع هايكين آشي."""
        if not candles:
            return []
        out = []
        ha_prev_close = None
        for c in candles:
            o = c.get("open", 0)
            h = c.get("high", 0)
            l_ = c.get("low", 0)
            cl = c.get("close", 0)
            if ha_prev_close is None:
                ha_open = o
            else:
                ha_open = (ha_prev_close + out[-1]["close"]) / 2.0
            ha_close = (o + h + l_ + cl) / 4.0
            ha_high = max(h, ha_open, ha_close)
            ha_low = min(l_, ha_open, ha_close)
            ha_prev_close = ha_close
            out.append({"open": ha_open, "high": ha_high, "low": ha_low, "close": ha_close, "volume": c.get("volume", 0)})
        return out

    def _moving_average(self, values: list, period: int) -> list:
        """متوسط متحرك بسيط — يرجع قائمة بنفس الطول (قيم أولية None)."""
        n = len(values)
        result = [None] * n
        if period < 1 or n < period:
            return result
        for i in range(period - 1, n):
            result[i] = sum(values[i - period + 1 : i + 1]) / period
        return result

    def clearDrawings(self):
        """مسح كل الرسومات (خطوط، قنوات، فيبوناتشي) وإيقاف أداة الرسم حتى تختفي معاينة الخط المنقط."""
        self._drawings.clear()
        self._edit_draw = None
        self.setDrawMode(None)
        self.update()

    def _clamp_candle_idx(self, ci: int) -> int:
        n = len(self.candles)
        if n <= 0:
            return 0
        try:
            i = int(ci)
        except (TypeError, ValueError):
            i = 0
        return max(0, min(i, n - 1))

    def _drawing_handle_hit(self, mx: float, my: float, geo: dict):
        """هل النقرة قريبة من مقبض؟ يُرجع (فهرس الرسم, رقم المقبض) أو None."""
        if not self._drawings:
            return None
        lm = geo["left_margin"]
        pr = geo["plot_right"]
        ct, cb = geo["chart_top"], geo["chart_bottom"]
        if not (lm <= mx <= pr):
            return None
        map_price = geo["map_price"]
        cw = float(geo["candle_width"])
        vsf = float(geo["view_start_f"])
        r = float(getattr(self, "_draw_handle_radius_px", 7.0))

        def c2x(ci: float) -> float:
            return lm + (float(ci) - vsf) * cw + cw / 2.0

        def dist(ax: float, ay: float) -> float:
            return ((mx - ax) ** 2 + (my - ay) ** 2) ** 0.5

        for idx in range(len(self._drawings) - 1, -1, -1):
            obj = self._drawings[idx]
            t, data = obj["type"], obj["data"]
            if t == "hline":
                (pv,) = data
                y = float(map_price(float(pv)))
                hx = (lm + pr) / 2.0
                if ct - r <= my <= cb + r and dist(hx, y) <= r:
                    return (idx, 0)
            elif t == "line":
                i0, p0, i1, p1 = data
                pts = [(c2x(i0), float(map_price(float(p0)))), (c2x(i1), float(map_price(float(p1))))]
                for hi, (pxp, pyp) in enumerate(pts):
                    if ct - r <= pyp <= cb + r and dist(pxp, pyp) <= r:
                        return (idx, hi)
            elif t == "channel":
                i0, p0, i1, p1 = data
                if i1 == i0:
                    continue
                pts = [(c2x(i0), float(map_price(float(p0)))), (c2x(i1), float(map_price(float(p1))))]
                for hi, (pxp, pyp) in enumerate(pts):
                    if ct - r <= pyp <= cb + r and dist(pxp, pyp) <= r:
                        return (idx, hi)
            elif t == "fib":
                i0, ph, i1, pl = data
                pts = [(c2x(i0), float(map_price(float(ph)))), (c2x(i1), float(map_price(float(pl))))]
                for hi, (pxp, pyp) in enumerate(pts):
                    if ct - r <= pyp <= cb + r and dist(pxp, pyp) <= r:
                        return (idx, hi)
            elif t == "rect":
                i0, ph, i1, pl = data
                x0, x1 = c2x(i0), c2x(i1)
                yh, yl = float(map_price(float(ph))), float(map_price(float(pl)))
                pts = [(x0, yh), (x1, yh), (x0, yl), (x1, yl)]
                for hi, (pxp, pyp) in enumerate(pts):
                    if ct - r <= pyp <= cb + r and dist(pxp, pyp) <= r:
                        return (idx, hi)
        return None

    def _apply_edit_draw_move(self, mx: float, my: float) -> None:
        """تحديث نقطة الرسم أثناء سحب المقبض."""
        ed = getattr(self, "_edit_draw", None)
        if not ed or not self._drawings:
            return
        geo = self._compute_geometry()
        if not geo:
            return
        pt = geo["xy_to_candle_price"](mx, my)
        if pt is None:
            return
        ci = self._clamp_candle_idx(pt[0])
        price = float(pt[1])
        idx = int(ed["idx"])
        h = int(ed["handle"])
        if idx < 0 or idx >= len(self._drawings):
            return
        obj = self._drawings[idx]
        t = obj["type"]

        if t == "hline":
            obj["data"] = (price,)
        elif t == "line":
            i0, p0, i1, p1 = obj["data"]
            if h == 0:
                obj["data"] = (ci, price, i1, p1)
            else:
                obj["data"] = (i0, p0, ci, price)
        elif t == "channel":
            i0, p0, i1, p1 = obj["data"]
            if h == 0:
                obj["data"] = (ci, price, i1, p1)
            else:
                obj["data"] = (i0, p0, ci, price)
        elif t == "fib":
            i0, ph, i1, pl = obj["data"]
            p_new, phf, plf = float(price), float(ph), float(pl)
            if h == 0:
                li, ri = sorted([ci, int(i1)])
                top, bot = max(p_new, plf), min(p_new, plf)
                if top < bot:
                    top, bot = bot, top
                obj["data"] = (li, top, ri, bot)
            else:
                li, ri = sorted([int(i0), ci])
                top, bot = max(phf, p_new), min(phf, p_new)
                if top < bot:
                    top, bot = bot, top
                obj["data"] = (li, top, ri, bot)
        elif t == "rect":
            i0, ph, i1, pl = obj["data"]
            corners = [(int(i0), float(ph)), (int(i1), float(ph)), (int(i0), float(pl)), (int(i1), float(pl))]
            corners[h] = (ci, price)
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            obj["data"] = (min(xs), max(ys), max(xs), min(ys))

    def _compute_geometry(self):
        """حساب إحداثيات الشارت لتحويل (x,y) ↔ (candle_idx, price). يُرجع None إن لم توجد شموع."""
        visible = self._visible_candles()
        if not visible:
            return None
        w, h = self.width(), self.height()
        price_axis_width = int(getattr(self, "_price_axis_width", 92))
        bottom_axis_height = 24
        profile_reserved = int(getattr(self, "_profile_reserved", 52))
        chart_right = w - price_axis_width
        left_margin, right_margin = 28, 8
        plot_right = chart_right - right_margin - profile_reserved
        chart_width = max(1, plot_right - left_margin)
        available_width = chart_width * 0.96
        n_vis = len(visible)
        if getattr(self, "_follow_latest", True) and self._visible_count > 0:
            pad = self._right_padding_slots
        else:
            pad = 0
        candle_width = max(2, available_width / max(1, n_vis + pad)) if n_vis else 8
        all_prices = []
        for c in visible:
            all_prices.extend([c.get("high", 0), c.get("low", 0), c.get("open", 0), c.get("close", 0)])
        if self._current_price is not None:
            all_prices.append(self._current_price)
        max_price, min_price = max(all_prices), min(all_prices)
        diff = max_price - min_price if max_price != min_price else 1
        margin = max(diff * 0.02, (max_price + min_price) * 0.0001)
        range_min = min_price - margin
        range_max = max_price + margin
        center = (range_min + range_max) / 2
        half = (range_max - range_min) / 2 / max(0.2, self._y_zoom)
        range_min = center - half
        range_max = center + half
        range_min += self._y_pan
        range_max += self._y_pan
        range_diff = range_max - range_min
        chart_top = 10
        chart_bottom = h - bottom_axis_height
        chart_h = chart_bottom - chart_top

        def map_price(p):
            return chart_bottom - int(((p - range_min) / range_diff) * chart_h)

        def xy_to_candle_price(px, py):
            if not (left_margin <= px <= plot_right and chart_top <= py <= chart_bottom):
                return None
            price = range_min + (chart_bottom - py) / max(1, chart_h) * range_diff
            idx_float = (px - left_margin - candle_width / 2) / max(1e-9, candle_width)
            vs_f = float(getattr(self, "_view_start_f", float(self._view_start)))
            candle_idx = int(round(vs_f + idx_float))
            candle_idx = max(0, min(len(self.candles) - 1, candle_idx))
            return (candle_idx, price)

        return {
            "left_margin": left_margin, "right_margin": right_margin, "chart_right": chart_right,
            "plot_right": plot_right, "profile_reserved": profile_reserved,
            "chart_top": chart_top, "chart_bottom": chart_bottom, "chart_h": chart_h,
            "candle_width": candle_width, "view_start": self._view_start, "view_start_f": getattr(self, "_view_start_f", float(self._view_start)), "n_vis": n_vis,
            "range_min": range_min, "range_max": range_max, "range_diff": range_diff,
            "map_price": map_price, "xy_to_candle_price": xy_to_candle_price,
        }

    def setAnalysisLevels(self, pivot=None, r1=None, r2=None, s1=None, s2=None, r3=None, s3=None):
        """تعيين مستويات التحليل (دعم/مقاومة/محور) لرسمها على الشارت. R3/S3 تُرسم تلقائياً عند تجاوز السعر لـ R2/S2."""
        self._analysis_levels = []
        for price, label, color in [
            (s2, "S2", QColor(0, 180, 100)),
            (s1, "S1", QColor(0, 200, 120)),
            (pivot, "Pivot", QColor(220, 180, 60)),
            (r1, "R1", QColor(220, 100, 80)),
            (r2, "R2", QColor(200, 70, 70)),
        ]:
            if price is not None and float(price) > 0:
                self._analysis_levels.append((float(price), label, color))
        self._pivot_r2 = float(r2) if r2 is not None and float(r2) > 0 else None
        self._pivot_s2 = float(s2) if s2 is not None and float(s2) > 0 else None
        self._pivot_r3 = float(r3) if r3 is not None and float(r3) > 0 else None
        self._pivot_s3 = float(s3) if s3 is not None and float(s3) > 0 else None
        self.update()

    def setShowAnalysisLevels(self, show: bool):
        self._show_analysis_levels = bool(show)
        self.update()

    def setVwap(self, price):
        """تعيين سعر VWAP لرسمه على الشارت كخط أفقي (متوسط مرجح بالحجم)."""
        self._vwap_price = float(price) if price is not None and float(price) > 0 else None
        self.update()

    def setRecommendationPrices(self, buy_price, sell_price):
        """تعيين سعري الشراء والبيع من التوصية لرسم مثلثين صغيرين على مقياس السعر (أخضر شراء، أحمر بيع)."""
        self._rec_buy_price = float(buy_price) if buy_price is not None and float(buy_price) > 0 else None
        self._rec_sell_price = float(sell_price) if sell_price is not None and float(sell_price) > 0 else None
        self.update()

    def get_chart_state(self):
        """إرجاع حالة الشارت (لحفظها عند الإغلاق)."""
        return {
            "visible_count": getattr(self, "_visible_count", 0),
            "view_start": getattr(self, "_view_start", 0),
            "y_zoom": getattr(self, "_y_zoom", 1.0),
            "y_pan": getattr(self, "_y_pan", 0.0),
            "follow_latest": getattr(self, "_follow_latest", True),
        }

    def set_chart_state(self, visible_count=0, view_start=0, y_zoom=1.0, y_pan=0.0, follow_latest=None):
        """تطبيق حالة الشارت المحفوظة (عند فتح البرنامج)."""
        n = len(self.candles)
        if visible_count > 0 and n > 0:
            self._visible_count = min(visible_count, n)
            lo, hi = self._horizontal_scroll_bounds(n, self._visible_count)
            vs = max(int(math.floor(lo)), min(int(view_start), int(math.ceil(hi))))
            self._view_start = vs
            self._view_start_f = float(vs)
        if y_zoom > 0:
            self._y_zoom = max(0.2, min(5.0, float(y_zoom)))
        self._y_pan = float(y_pan)
        if follow_latest is not None:
            self._follow_latest = bool(follow_latest)
        self.update()

    def paintEvent(self, event):
        visible = self._visible_candles()
        if not visible:
            return

        painter = QPainter(self)
        # بدون Antialiasing: رسم أخف على الأجهزة الضعيفة؛ الحواف أوضح قليلاً (أكثر «مسننة»).

        w = self.width()
        h = self.height()

        price_axis_width = int(getattr(self, "_price_axis_width", 92))
        bottom_axis_height = 24
        profile_reserved = int(getattr(self, "_profile_reserved", 52))
        chart_right = w - price_axis_width
        left_margin = 28
        right_margin = 8
        # منطقة الشموع تنتهي قبل ملف الحجم (VP) وعمود السعر — لا ترسم الشموع فوق الأرقام
        plot_right = chart_right - right_margin - profile_reserved
        chart_width = max(1, plot_right - left_margin)
        available_width = chart_width * 0.96

        painter.fillRect(self.rect(), QColor(20, 20, 24))

        n_vis = len(visible)
        # فراغ يميناً بعد آخر شمعة:
        # - نُبقيه فقط في الوضع الافتراضي (متابعة آخر الشموع) لتحسين القراءة
        # - عند السحب/التحريك (استعراض الماضي) نُلغي الفراغ حتى لا تختفي الشموع بعيداً عن عمود السعر
        if getattr(self, "_follow_latest", True):
            pad = self._right_padding_slots if self._visible_count > 0 else max(6, int(n_vis * 0.12))
        else:
            pad = 0
        candle_width = max(2, available_width / max(1, (n_vis + pad))) if n_vis else 8
        scroll_frac = float(getattr(self, "_scroll_frac", 0.0))

        # رسم الشموع فقط (بدون Heikin-Ashi/Line/Area/Hollow)
        chart_type = "candle"
        draw_candles = visible

        all_prices = []
        for c in draw_candles:
            all_prices.extend(
                [c.get("high", 0), c.get("low", 0), c.get("open", 0), c.get("close", 0)]
            )
        if self._current_price is not None:
            all_prices.append(self._current_price)

        max_price = max(all_prices)
        min_price = min(all_prices)
        diff = max_price - min_price if max_price != min_price else 1
        margin = max(diff * 0.02, (max_price + min_price) * 0.0001)
        range_min = min_price - margin
        range_max = max_price + margin
        # تطبيق التكبير العمودي (Ctrl + عجلة)
        center = (range_min + range_max) / 2
        half = (range_max - range_min) / 2
        half = half / max(0.2, self._y_zoom)
        range_min = center - half
        range_max = center + half
        # تطبيق السحب العمودي (تحريك الماوس لأعلى/أسفل)
        range_min += self._y_pan
        range_max += self._y_pan
        range_diff = range_max - range_min
        # حفظ مركز النطاق المعروض لتثبيته أثناء الزوم الأفقي
        self._last_display_center = (range_min + range_max) / 2.0

        chart_top = 10
        chart_bottom = h - bottom_axis_height
        chart_h = chart_bottom - chart_top

        def map_price(p):
            return chart_bottom - int(((p - range_min) / range_diff) * chart_h)

        clip_rect = QRectF(float(left_margin), float(chart_top), float(max(1, plot_right - left_margin)), float(chart_bottom - chart_top))
        painter.save()
        painter.setClipRect(clip_rect)

        grid_pen = QPen(QColor(50, 50, 60), 1, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        for i in range(1, 5):
            y = chart_top + int(i * chart_h / 5)
            painter.drawLine(left_margin, y, plot_right, y)

        # نوع الشارت: candle | heikin_ashi | line | area | hollow (draw_candles و chart_type محسوبان أعلاه)

        if chart_type == "line":
            # خط سعر الإغلاق (مثل TradingView)
            points = []
            for i, c in enumerate(draw_candles):
                x = left_margin + (i + 0.5 - scroll_frac) * candle_width
                y = map_price(c.get("close", 0))
                points.append(QPointF(x, y))
            if len(points) >= 2:
                painter.setPen(QPen(QColor(0, 180, 255), 2, Qt.PenStyle.SolidLine))
                for j in range(len(points) - 1):
                    painter.drawLine(int(points[j].x()), int(points[j].y()), int(points[j + 1].x()), int(points[j + 1].y()))
        elif chart_type == "area":
            # منطقة تحت خط الإغلاق (مملوءة)
            points = [QPointF(left_margin, chart_bottom)]
            for i, c in enumerate(draw_candles):
                x = left_margin + (i + 0.5 - scroll_frac) * candle_width
                y = map_price(c.get("close", 0))
                points.append(QPointF(x, y))
            points.append(QPointF(left_margin + (len(draw_candles) - scroll_frac) * candle_width, chart_bottom))
            if len(points) >= 3:
                poly = QPolygonF(points)
                painter.setPen(QPen(QColor(0, 160, 220), 1))
                painter.setBrush(QBrush(QColor(0, 120, 180, 120)))
                painter.drawPolygon(poly)
            if len(points) > 2:
                painter.setPen(QPen(QColor(0, 180, 255), 2, Qt.PenStyle.SolidLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for j in range(1, len(points) - 1):
                    painter.drawLine(int(points[j].x()), int(points[j].y()), int(points[j + 1].x()), int(points[j + 1].y()))
        else:
            # candle | heikin_ashi | hollow — رسم شموع
            for i, c in enumerate(draw_candles):
                open_ = c.get("open", 0)
                close = c.get("close", 0)
                high = c.get("high", 0)
                low = c.get("low", 0)

                x = left_margin + (i - scroll_frac) * candle_width
                cw = int(candle_width) - 1
                if cw < 1:
                    cw = 1

                if chart_type == "hollow":
                    # شموع مجوفة: أخضر حد فقط للصعود، أحمر مملوء للهبوط
                    if close >= open_:
                        painter.setPen(QPen(QColor(0, 200, 120), 1))
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                    else:
                        painter.setPen(QPen(QColor(220, 70, 70), 1))
                        painter.setBrush(QBrush(QColor(220, 70, 70)))
                else:
                    color = QColor(0, 200, 120) if close >= open_ else QColor(220, 70, 70)
                    painter.setPen(QPen(color, 1))
                    painter.setBrush(QBrush(color))

                y_open = map_price(open_)
                y_close = map_price(close)
                y_high = map_price(high)
                y_low = map_price(low)
                mid_x = x + candle_width / 2

                painter.drawLine(int(mid_x), y_high, int(mid_x), y_low)
                rect_height = max(2, abs(y_close - y_open))
                painter.drawRect(int(x), min(y_open, y_close), cw, rect_height)

        # متوسطات متحركة على الشارت (مثل TradingView) — محسوبة من الشموع المرئية
        closes = [c.get("close", 0) for c in visible]
        ma20_list = self._moving_average(closes, 20)
        ma50_list = self._moving_average(closes, 50)
        for ma_list, color in [(ma20_list, QColor(255, 180, 0)), (ma50_list, QColor(0, 200, 255))]:
            pts = []
            for i, val in enumerate(ma_list):
                if val is not None and range_min <= val <= range_max:
                    x_pt = left_margin + (i + 0.5 - scroll_frac) * candle_width
                    pts.append((x_pt, map_price(val)))
            if len(pts) >= 2:
                painter.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine))
                for j in range(len(pts) - 1):
                    painter.drawLine(int(pts[j][0]), int(pts[j][1]), int(pts[j + 1][0]), int(pts[j + 1][1]))

        price_line = self._current_price if self._current_price is not None else (
            visible[-1].get("close", 0) if visible else None
        )
        # خط السعر الحالي: أبيض (سنرسم المستطيل بعد أرقام عمود السعر حتى لا تتداخل)
        line_color = QColor(255, 255, 255)
        # نص السعر + العد التنازلي يُرسمان في إطار ثابت بعمود السعر؛ الخط الأفقي وحده يتبع السعر
        _price_box_labels = None  # (price_txt, countdown_txt)
        if price_line is not None and range_min <= price_line <= range_max:
            y_line = map_price(price_line)
            painter.setPen(QPen(line_color, 0.7, Qt.PenStyle.SolidLine))
            painter.drawLine(left_margin, y_line, plot_right, y_line)
            price_txt = format_price(price_line)
            countdown_txt = self._candle_countdown_text or ""
            _price_box_labels = (price_txt, countdown_txt)

        # خط تتبع الماوس: خط أبيض منقط (سنرسم مستطيل السعر بعد أرقام عمود السعر)
        cy = getattr(self, "_crosshair_y", None)
        _crosshair_box = None  # (cy, price_txt)
        if cy is not None and chart_top <= cy <= chart_bottom:
            # السعر عند ارتفاع المؤشر
            crosshair_price = range_min + (chart_bottom - cy) / max(1, chart_h) * range_diff
            # خط أبيض منقط أفقي + عمودي (تقاطع مع عمود السعر)
            dot_pen = QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DotLine)
            painter.setPen(dot_pen)
            painter.drawLine(left_margin, cy, plot_right, cy)
            cx = getattr(self, "_crosshair_x", None)
            if cx is not None and left_margin <= cx <= plot_right:
                painter.drawLine(int(cx), chart_top, int(cx), chart_bottom)
            _crosshair_box = (int(cy), format_price(crosshair_price))

        # رسم مستويات التحليل (دعم، مقاومة، محور) كخطوط أفقية مع تسميات
        if getattr(self, "_show_analysis_levels", True):
            for price_val, label, line_color in (self._analysis_levels or []):
                if not (range_min <= price_val <= range_max):
                    continue
                y_level = map_price(price_val)
                painter.setPen(QPen(line_color, 1.2, Qt.PenStyle.DashLine))
                painter.drawLine(left_margin, y_level, plot_right, y_level)
                painter.setPen(line_color)
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Normal))
                painter.drawText(left_margin, y_level - 2, 110, 20, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, f"{label} {format_price(price_val)}")

        # أدوات الرسم: خط، قناة، فيبوناتشي
        vs_f_paint = float(getattr(self, "_view_start_f", float(self._view_start)))

        def candle_to_x(ci):
            return left_margin + (ci - vs_f_paint) * candle_width + candle_width / 2

        for obj in (self._drawings or []):
            t, data = obj["type"], obj["data"]
            if t == "hline":
                (price_val,) = data
                y = map_price(price_val)
                painter.setPen(QPen(QColor(0, 220, 180), 2, Qt.PenStyle.SolidLine))
                painter.drawLine(left_margin, int(y), plot_right, int(y))
            elif t == "line":
                i0, p0, i1, p1 = data
                x0 = candle_to_x(i0)
                x1 = candle_to_x(i1)
                y0, y1 = map_price(p0), map_price(p1)
                painter.setPen(QPen(QColor(0, 180, 255), 2, Qt.PenStyle.SolidLine))
                painter.drawLine(int(x0), int(y0), int(x1), int(y1))
            elif t == "channel":
                i0, p0, i1, p1 = data
                if i1 == i0:
                    continue
                # الخط الثاني موازٍ ويمر بأعلى/أدنى نقطة في النطاق
                start, end = min(i0, i1), max(i0, i1)
                seg = self.candles[max(0, start):min(len(self.candles), end + 1)]
                if not seg:
                    continue
                max_h = max(c.get("high", 0) for c in seg)
                min_l = min(c.get("low", 0) for c in seg)
                mid_i = (i0 + i1) / 2.0
                mid_p = p0 + (p1 - p0) * (mid_i - i0) / (i1 - i0) if (i1 - i0) != 0 else p0
                off = (max_h - mid_p) if abs(max_h - mid_p) > abs(min_l - mid_p) else (min_l - mid_p)
                x0, x1 = candle_to_x(i0), candle_to_x(i1)
                painter.setPen(QPen(QColor(0, 180, 255), 2, Qt.PenStyle.SolidLine))
                painter.drawLine(int(x0), int(map_price(p0)), int(x1), int(map_price(p1)))
                painter.setPen(QPen(QColor(100, 200, 255), 1.5, Qt.PenStyle.DashLine))
                painter.drawLine(int(x0), int(map_price(p0 + off)), int(x1), int(map_price(p1 + off)))
            elif t == "fib":
                i0, ph, i1, pl = data
                diff = ph - pl
                if diff <= 0:
                    continue
                levels = [(0, ph), (0.236, ph - 0.236 * diff), (0.382, ph - 0.382 * diff), (0.5, ph - 0.5 * diff),
                          (0.618, ph - 0.618 * diff), (0.786, ph - 0.786 * diff), (1, pl)]
                x0, x1 = candle_to_x(i0), candle_to_x(i1)
                fib_line_color = QColor(255, 180, 0)
                fib_text_color = QColor(255, 255, 255)
                painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Normal))
                for frac, pr in levels:
                    y = map_price(pr)
                    if not (chart_top <= y <= chart_bottom):
                        continue
                    painter.setPen(QPen(fib_line_color, 1, Qt.PenStyle.DashLine))
                    painter.drawLine(int(x0), int(y), int(x1), int(y))
                    painter.setPen(fib_text_color)
                    painter.drawText(int(x0) - 56, int(y) - 11, 54, 24, Qt.AlignmentFlag.AlignRight, f"{frac*100:.0f}%")
            elif t == "rect":
                i0, ph, i1, pl = data
                x0, x1 = candle_to_x(i0), candle_to_x(i1)
                y_high, y_low = map_price(ph), map_price(pl)
                rect_color = QColor(0, 180, 255, 80)
                painter.setPen(QPen(QColor(0, 180, 255), 1.5, Qt.PenStyle.SolidLine))
                painter.setBrush(QBrush(rect_color))
                painter.drawRect(int(min(x0, x1)), int(min(y_high, y_low)), int(abs(x1 - x0)), int(abs(y_high - y_low)))

        # دوائر على نهايات الخطوط — تعديل بالسحب في وضع التحريك (✋)
        if self._draw_mode is None and (self._drawings or []):
            rad = float(getattr(self, "_draw_handle_radius_px", 7.0))
            handle_pen = QPen(QColor(255, 255, 255), 2)
            handle_brush = QBrush(QColor(70, 150, 235))
            painter.setPen(handle_pen)
            painter.setBrush(handle_brush)

            def _draw_handle_dot(xp: float, yp: float) -> None:
                if not (chart_top - rad <= yp <= chart_bottom + rad):
                    return
                painter.drawEllipse(int(xp - rad), int(yp - rad), int(2 * rad), int(2 * rad))

            for obj in self._drawings:
                t, data = obj["type"], obj["data"]
                if t == "hline":
                    (price_val,) = data
                    y = float(map_price(float(price_val)))
                    _draw_handle_dot((left_margin + plot_right) / 2.0, y)
                elif t == "line":
                    i0, p0, i1, p1 = data
                    _draw_handle_dot(candle_to_x(i0), float(map_price(float(p0))))
                    _draw_handle_dot(candle_to_x(i1), float(map_price(float(p1))))
                elif t == "channel":
                    i0, p0, i1, p1 = data
                    if i1 != i0:
                        _draw_handle_dot(candle_to_x(i0), float(map_price(float(p0))))
                        _draw_handle_dot(candle_to_x(i1), float(map_price(float(p1))))
                elif t == "fib":
                    i0, ph, i1, pl = data
                    _draw_handle_dot(candle_to_x(i0), float(map_price(float(ph))))
                    _draw_handle_dot(candle_to_x(i1), float(map_price(float(pl))))
                elif t == "rect":
                    i0, ph, i1, pl = data
                    x0, x1 = candle_to_x(i0), candle_to_x(i1)
                    yh, yl = float(map_price(float(ph))), float(map_price(float(pl)))
                    for xp, yp in ((x0, yh), (x1, yh), (x0, yl), (x1, yl)):
                        _draw_handle_dot(xp, yp)

        # رسم تلقائي من الشموع المرئية — يظهر عند اختيار الأداة ويُخفى بالضغط على التحريك (✋) أو أداة أخرى
        dm = getattr(self, "_draw_mode", None)
        if dm and visible:
            n_vis = len(visible)
            i0 = self._view_start
            i1 = self._view_start + n_vis - 1
            x0_auto = candle_to_x(i0)
            x1_auto = candle_to_x(i1)
            c0, c1 = visible[0], visible[-1]
            p0_high, p0_low = c0.get("high", 0), c0.get("low", 0)
            p1_high, p1_low = c1.get("high", 0), c1.get("low", 0)

            if dm == "line":
                # خط ترند تلقائي: من قاع أول شمعة إلى قاع آخر شمعة (دعم)
                auto_line_color = QColor(100, 200, 255)
                painter.setPen(QPen(auto_line_color, 1.5, Qt.PenStyle.DotLine))
                y0, y1 = map_price(p0_low), map_price(p1_low)
                painter.drawLine(int(x0_auto), int(y0), int(x1_auto), int(y1))

            elif dm == "channel":
                # قناة تلقائية: خط علوي (قمم) + خط سفلي (قيعان)
                ch_top = QColor(100, 200, 255)
                ch_bot = QColor(100, 220, 255)
                painter.setPen(QPen(ch_top, 1.5, Qt.PenStyle.DotLine))
                painter.drawLine(int(x0_auto), int(map_price(p0_high)), int(x1_auto), int(map_price(p1_high)))
                painter.setPen(QPen(ch_bot, 1.5, Qt.PenStyle.DotLine))
                painter.drawLine(int(x0_auto), int(map_price(p0_low)), int(x1_auto), int(map_price(p1_low)))

            elif dm == "fib":
                v_high = max(c.get("high", 0) for c in visible)
                v_low = min(c.get("low", 0) for c in visible)
                diff = v_high - v_low
                if diff > 0:
                    levels = [(0, v_high), (0.236, v_high - 0.236 * diff), (0.382, v_high - 0.382 * diff),
                             (0.5, v_high - 0.5 * diff), (0.618, v_high - 0.618 * diff), (0.786, v_high - 0.786 * diff), (1, v_low)]
                    auto_fib_line = QColor(255, 200, 80)
                    auto_fib_text = QColor(255, 255, 255)
                    painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Normal))
                    x_f0, x_f1 = left_margin, plot_right
                    for frac, pr in levels:
                        y = map_price(pr)
                        if not (chart_top <= y <= chart_bottom):
                            continue
                        painter.setPen(QPen(auto_fib_line, 1, Qt.PenStyle.DotLine))
                        painter.drawLine(int(x_f0), int(y), int(x_f1), int(y))
                        painter.setPen(auto_fib_text)
                        painter.drawText(int(x_f0), int(y) - 11, 54, 24, Qt.AlignmentFlag.AlignLeft, f"{frac*100:.0f}%")

        # معاينة الرسم أثناء السحب
        if self._draw_start is not None and self._draw_preview is not None and getattr(self, "_draw_mode", None):
            i0, p0 = self._draw_start
            i1, p1 = self._draw_preview
            x0 = candle_to_x(i0)
            x1 = candle_to_x(i1)
            y0, y1 = map_price(p0), map_price(p1)
            painter.setPen(QPen(QColor(255, 255, 100), 1.5, Qt.PenStyle.DashLine))
            if self._draw_mode == "rect":
                ph, pl = max(p0, p1), min(p0, p1)
                y_high, y_low = map_price(ph), map_price(pl)
                painter.setBrush(QBrush(QColor(255, 255, 100, 60)))
                painter.drawRect(int(min(x0, x1)), int(min(y_high, y_low)), int(abs(x1 - x0)), int(abs(y_high - y_low)))
            else:
                painter.drawLine(int(x0), int(y0), int(x1), int(y1))
            if self._draw_mode == "fib":
                ph, pl = max(p0, p1), min(p0, p1)
                d = ph - pl
                if d > 0:
                    for frac in (0, 0.236, 0.382, 0.5, 0.618, 0.786, 1):
                        py = map_price(ph - frac * d)
                        if chart_top <= py <= chart_bottom:
                            painter.drawLine(int(x0), int(py), int(x1), int(py))

        # مستويات ممتدة R3/S3: تظهر تلقائياً عند تجاوز السعر لـ R2 أو انخفاضه عن S2
        cur = getattr(self, "_current_price", None)
        r2 = getattr(self, "_pivot_r2", None)
        s2 = getattr(self, "_pivot_s2", None)
        r3 = getattr(self, "_pivot_r3", None)
        s3 = getattr(self, "_pivot_s3", None)
        if cur is not None and cur > 0:
            if r3 is not None and r2 is not None and cur > r2 and range_min <= r3 <= range_max:
                y_r3 = map_price(r3)
                ext_color = QColor(255, 160, 60)
                painter.setPen(QPen(ext_color, 1.5, Qt.PenStyle.DashDotLine))
                painter.drawLine(left_margin, y_r3, plot_right, y_r3)
                painter.setPen(ext_color)
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.drawText(left_margin, y_r3 - 2, 150, 20, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, f"R3 {format_price(r3)}")
            if s3 is not None and s2 is not None and cur < s2 and range_min <= s3 <= range_max:
                y_s3 = map_price(s3)
                ext_color = QColor(60, 200, 160)
                painter.setPen(QPen(ext_color, 1.5, Qt.PenStyle.DashDotLine))
                painter.drawLine(left_margin, y_s3, plot_right, y_s3)
                painter.setPen(ext_color)
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.drawText(left_margin, y_s3 - 2, 150, 20, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, f"S3 {format_price(s3)}")

        # خط VWAP (متوسط مرجح بالحجم) — لون مميز
        if getattr(self, "_vwap_price", None) and range_min <= self._vwap_price <= range_max:
            y_vwap = map_price(self._vwap_price)
            vwap_color = QColor(0, 200, 255)
            painter.setPen(QPen(vwap_color, 1.5, Qt.PenStyle.SolidLine))
            painter.drawLine(left_margin, y_vwap, plot_right, y_vwap)
            painter.setPen(vwap_color)
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(left_margin, y_vwap - 2, 140, 20, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, f"VWAP {format_price(self._vwap_price)}")

        painter.restore()  # إلغاء القص — ملف الحجم والمحاور تُرسم في الشريط بجانب عمود السعر

        # Volume Profile مبسّط: توزيع الحجم على مستويات السعر (بين منطقة الشموع وعمود السعر)
        try:
            n_buckets = 24
            bucket_vol = [0.0] * n_buckets
            for c in visible:
                typical = (c.get("high", 0) + c.get("low", 0) + c.get("close", 0)) / 3.0
                vol = float(c.get("volume", 0) or 0)
                if range_diff > 0 and vol > 0:
                    t = (typical - range_min) / range_diff
                    idx = max(0, min(n_buckets - 1, int(t * n_buckets)))
                    bucket_vol[idx] += vol
            max_vol = max(bucket_vol) if bucket_vol else 1.0
            profile_w = max(4, chart_right - right_margin - plot_right - 4)
            bar_h = 5
            px0 = plot_right + 2
            for i in range(n_buckets):
                if bucket_vol[i] <= 0:
                    continue
                p = range_min + (i + 0.5) * (range_diff / n_buckets)
                yp = map_price(p)
                w = max(4, int(profile_w * (bucket_vol[i] / max_vol)))
                painter.fillRect(px0 + profile_w - w, yp - bar_h // 2, w, bar_h, QColor(80, 120, 180, 200))
            painter.setPen(QColor(100, 140, 200))
            painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Normal))
            painter.drawText(px0, chart_bottom - 12, profile_w, 10, Qt.AlignmentFlag.AlignCenter, "VP")
        except Exception:
            pass

        painter.setPen(QColor(180, 180, 190))
        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        for i in range(7):
            t = i / 6
            p = range_min + (1 - t) * range_diff
            y = chart_top + int(t * chart_h)
            txt = format_price(p)
            painter.drawText(chart_right + 2, y + 4, price_axis_width - 6, 20, Qt.AlignmentFlag.AlignLeft, txt)

        # مستطيل السعر: موضع عمودي ثابت في منتصف منطقة الشموع (لا يتبع y_line) — القيمة تتحدّث فقط
        if _price_box_labels is not None:
            price_txt, countdown_txt = _price_box_labels
            box_w = price_axis_width - 2
            box_h = 32  # سطران: السعر + العد التنازلي
            tx = chart_right + 1
            ty = chart_top + max(0, (chart_h - box_h) // 2)
            bg_color = QColor(255, 255, 255, 255)
            border_color = QColor(210, 210, 215)
            painter.setPen(QPen(border_color, 1))
            painter.setBrush(QBrush(bg_color))
            painter.drawRoundedRect(QRectF(tx, ty, box_w, box_h), 6, 6)
            # السعر (سطر علوي، خط عريض)
            painter.setPen(QColor(0, 0, 0))
            painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            painter.drawText(
                tx + 4,
                ty + 1,
                box_w - 8,
                box_h // 2,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                price_txt,
            )
            # عدّاد انتهاء الشمعة (سطر سفلي بخط أصغر، إن وُجد)
            if countdown_txt:
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Normal))
                painter.drawText(
                    tx + 4,
                    ty + box_h // 2,
                    box_w - 8,
                    box_h // 2 - 2,
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                    countdown_txt,
                )

        if _crosshair_box is not None:
            cy, price_txt = _crosshair_box
            box_w2 = price_axis_width - 2
            box_h2 = 24
            tx2 = chart_right + 1
            ty2 = int(cy) - (box_h2 // 2)
            ty2 = max(chart_top, min(chart_bottom - box_h2, ty2))
            painter.setPen(QPen(QColor(210, 210, 215), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
            painter.drawRoundedRect(QRectF(tx2, ty2, box_w2, box_h2), 5, 5)
            painter.setPen(QColor(0, 0, 0))
            painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            painter.drawText(int(tx2) + 4, int(ty2) + 2, box_w2 - 8, box_h2 - 4, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, price_txt)

        # لا نرسم مستطيلات/مثلثات التوصية (أخضر/أحمر) — كانت تتبع السعر دون تنبؤ فعلي
        self._rec_click_rects = []

        # شارة المؤشر المركّب — أعلى يسار منطقة الشموع
        _badge = getattr(self, "_composite_badge_text", "") or ""
        if _badge:
            try:
                bg_b = QColor(str(getattr(self, "_composite_badge_bg", "#2d3748")))
                fg_b = QColor(str(getattr(self, "_composite_badge_fg", "#ffffff")))
            except Exception:
                bg_b, fg_b = QColor("#2d3748"), QColor("#ffffff")
            bf_b = QFont("Segoe UI", 9, QFont.Weight.Bold)
            painter.setFont(bf_b)
            fm_b = painter.fontMetrics()
            pad_xb, pad_yb = 8, 4
            tw_b = fm_b.horizontalAdvance(_badge) + pad_xb * 2
            th_b = fm_b.height() + pad_yb * 2
            bx_b = int(left_margin + 4)
            by_b = int(chart_top + 4)
            painter.setPen(QPen(QColor(255, 255, 255, 100), 1))
            painter.setBrush(QBrush(bg_b))
            painter.drawRoundedRect(QRectF(bx_b, by_b, tw_b, th_b), 6, 6)
            painter.setPen(fg_b)
            painter.drawText(
                bx_b + pad_xb,
                by_b + pad_yb - 1,
                tw_b - pad_xb * 2,
                th_b - pad_yb * 2,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                _badge,
            )

        painter.drawLine(left_margin, chart_bottom, plot_right, chart_bottom)
        ht = getattr(self, "_hover_axis_text", "") or ""
        cx_hover = getattr(self, "_crosshair_x", None)
        # تاريخ/وقت الشمعة تحت المؤشر — مستطيل أبيض يتحرك أفقياً مثل صندوق السعر (TradingView)
        if ht and cx_hover is not None:
            time_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
            painter.setFont(time_font)
            tw = painter.fontMetrics().horizontalAdvance(ht) + 16
            box_w_time = max(120, min(int(plot_right - left_margin - 8), tw))
            bx = int(cx_hover - box_w_time / 2)
            bx = max(left_margin + 2, min(int(plot_right - box_w_time - 2), bx))
            by = chart_bottom + 2
            box_h_time = 22
            painter.setPen(QPen(QColor(210, 210, 215), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
            painter.drawRoundedRect(QRectF(bx, by, box_w_time, box_h_time), 5, 5)
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(
                bx + 6,
                by + 2,
                box_w_time - 12,
                box_h_time - 4,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                ht,
            )
        painter.setPen(QColor(180, 180, 190))
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        tick_y = chart_bottom + (28 if ht else 2)
        for i in range(6):
            j = int(i * (n_vis - 1) / 5) if n_vis > 1 else 0
            x = left_margin + (j - scroll_frac) * candle_width + candle_width / 2
            if x > plot_right - 20:
                continue
            idx = self._view_start + j
            if 0 <= idx < len(self.candles):
                ms = _candle_open_time_ms(self.candles[idx])
                if ms:
                    txt = _candle_ms_to_local(ms).strftime("%m/%d %H:%M")
                else:
                    txt = str(idx)
            else:
                txt = str(idx)
            painter.drawText(
                int(x) - 36,
                tick_y,
                72,
                bottom_axis_height - 4,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                txt,
            )
        painter.drawText(left_margin, tick_y, 50, bottom_axis_height - 4, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "قديم")
        painter.drawText(plot_right - 44, tick_y, 44, bottom_axis_height - 4, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, "حديث")
        self._last_range_diff = range_diff
        self._last_chart_h = chart_h