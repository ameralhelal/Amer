# trade_history.py — سجل الصفقات (حفظ وعرض)
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from collections.abc import Callable
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QHeaderView,
    QLabel,
    QGroupBox,
    QMessageBox,
)

from format_utils import format_price, format_currency
from translations import tr
from ui_palette import (
    TOP_PANEL_BG,
    TOP_PANEL_BORDER,
    TOP_INNER_BG,
    TOP_TEXT_PRIMARY,
    TOP_TEXT_MUTED,
)

log = logging.getLogger("trading.history")
_HISTORY_IO_LOCK = threading.RLock()

# أقصى عدد صفقات في trade_history.json؛ عند الامتلاء تُحذف الأقدم.
# (كان 500 فبقي «إجمالي الصفقات» عالقاً عند 500 حتى مع صفقات جديدة.)
MAX_HISTORY_ENTRIES = 10000

# تنسيق الجدول — نفس مرجع الصف العلوي (بدون رمادي دافئ / بني)
TABLE_BOX_STYLE = f"""
    QTableWidget {{
        background-color: {TOP_INNER_BG};
        color: {TOP_TEXT_PRIMARY};
        gridline-color: {TOP_PANEL_BORDER};
        selection-background-color: #232a36;
        border: 1px solid {TOP_PANEL_BORDER};
    }}
    QHeaderView::section {{
        background-color: {TOP_PANEL_BG};
        color: {TOP_TEXT_PRIMARY};
        padding: 6px;
        border: none;
    }}
    QTableWidget::item {{
        padding: 6px;
        border: 1px solid {TOP_PANEL_BORDER};
    }}
    QTableWidget::item:hover {{
        background-color: #232a36;
    }}
"""

# صف تجميع حسب التاريخ — شريط يميّز اليوم دون لون محايد دافئ
_HISTORY_DATE_ROW_BG = "#232a36"

GROUP_BOX_STYLE = f"""
    QGroupBox {{
        font-size: 12px;
        font-weight: bold;
        padding-top: 8px;
        color: {TOP_TEXT_PRIMARY};
        border: 1px solid {TOP_PANEL_BORDER};
        border-radius: 8px;
        margin-top: 8px;
        background-color: {TOP_PANEL_BG};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 8px;
        padding: 0 4px;
    }}
"""

DAILY_TOTAL_LABEL_STYLE = (
    f"font-weight: bold; font-size: 13px; padding: 6px; "
    f"background-color: {TOP_INNER_BG}; border: 1px solid {TOP_PANEL_BORDER}; border-radius: 4px;"
)


def _history_path():
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "trade_history.json")


def clear_all_trade_history(*, backup: bool = True) -> tuple[bool, str, str | None]:
    """
    مسح trade_history.json بالكامل.
    يُرجع (نجاح، مسار الملف أو رسالة خطأ، مسار النسخ الاحتياطي إن وُجد).
    """
    path = _history_path()
    bak_path: str | None = None
    try:
        with _HISTORY_IO_LOCK:
            if backup and os.path.isfile(path) and os.path.getsize(path) > 4:
                bak_path = os.path.join(
                    os.path.dirname(path),
                    f"trade_history_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                )
                shutil.copy2(path, bak_path)
            with open(path, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
        log.info("trade_history cleared path=%s backup=%s", path, bak_path)
        return True, path, bak_path
    except Exception as e:
        log.warning("clear_all_trade_history failed: %s", e)
        return False, str(e), bak_path


def _debug_log(msg: str) -> None:
    try:
        base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
        p = os.path.join(base, "CryptoTrading", "trading_debug.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except OSError:
        pass


def _norm_hist_symbol(s: str) -> str:
    """توحيد رمز السجل للمقارنة (BTC / BTCUSD / BTCUSDT → BTCUSDT)."""
    x = (s or "").strip().upper()
    if not x:
        return ""
    if x.endswith("USDT"):
        return x
    if x.endswith("USD"):
        return x[:-3] + "USDT"
    return x + "USDT"


def _parse_entry_dt(s: object) -> datetime | None:
    try:
        raw = str(s or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1]
        if "+" in raw:
            raw = raw.split("+", 1)[0]
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _duplicate_sell_burst(
    prev: dict,
    new_entry: dict,
    *,
    max_seconds: float = 8.0,
) -> bool:
    """
    يكتشف تكرار «تفجير» بيع لنفس الصفقة (خلل سابق أو ضربات مؤقت سريعة):
    نفس الرمز، SELL، زمن متقارب، وسعر وكمية متطابقان تقريباً.
    """
    if str(prev.get("side", "")).upper() != "SELL" or str(new_entry.get("side", "")).upper() != "SELL":
        return False
    if str(prev.get("symbol", "")) != str(new_entry.get("symbol", "")):
        return False
    t1 = _parse_entry_dt(prev.get("time"))
    t2 = _parse_entry_dt(new_entry.get("time"))
    if t1 is None or t2 is None:
        return False
    if abs((t2 - t1).total_seconds()) > max_seconds:
        return False
    try:
        p1 = float(prev.get("price") or 0)
        p2 = float(new_entry.get("price") or 0)
        q1 = float(prev.get("quantity") or 0)
        q2 = float(new_entry.get("quantity") or 0)
    except (TypeError, ValueError):
        return False
    if p1 <= 0 or p2 <= 0 or q1 <= 0 or q2 <= 0:
        return False
    price_close = abs(p1 - p2) <= max(0.05, 1.5e-5 * max(p1, p2))
    qty_close = abs(q1 - q2) <= max(1e-10, 1e-8 * max(q1, q2))
    return bool(price_close and qty_close)


def dedupe_consecutive_sell_bursts(path: str | None = None, *, max_seconds: float = 10.0) -> tuple[int, str]:
    """
    يمرّ على trade_history.json ويحذف صفوف SELL المتتالية المكرّرة (نفس الرمز/السعر/الكمية تقريباً).
    يُرجع (عدد المحذوف، مسار الملف). آمن للاستدعاء من وحدة تحكم أو زر لاحقاً.
    """
    p = path or _history_path()
    removed = 0
    with _HISTORY_IO_LOCK:
        data: list = []
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                return 0, f"load failed: {e}"
        if not isinstance(data, list):
            return 0, p
        out: list = []
        for row in data:
            if isinstance(row, dict) and out and isinstance(out[-1], dict):
                if _duplicate_sell_burst(out[-1], row, max_seconds=max_seconds):
                    removed += 1
                    continue
            out.append(row)
        if removed <= 0:
            return 0, p
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
        except Exception as e:
            return 0, f"write failed: {e}"
    log.info("trade_history: dedupe_consecutive_sell_bursts removed=%s path=%s", removed, p)
    return removed, p


def _sym_match(a: str, b: str) -> bool:
    return _norm_hist_symbol(a) == _norm_hist_symbol(b)


def _last_buy_price_from_data(data: list, symbol: str) -> float | None:
    """آخر سعر شراء في السجل لنفس الرمز (بعد توحيد الصيغة) — احتياط عند فشل FIFO."""
    if not isinstance(data, list):
        return None
    for e in reversed(data):
        if not isinstance(e, dict):
            continue
        if not _sym_match(str(e.get("symbol", "")), symbol):
            continue
        if str(e.get("side", "")).upper() != "BUY":
            continue
        try:
            p = float(e.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if p > 0:
            return p
    return None


def append_sell_forced(
    symbol: str,
    quantity: float,
    avg_buy: float,
    exit_price: float,
    mode: str = "live",
    reason: str = "",
) -> tuple[bool, str, float | None]:
    """
    إضافة صفقة بيع في السجل مباشرة (بدون FIFO) — يُستخدم بعد إغلاق الكل/الصف.
    يُرجع (نجح، مسار_أو_رسالة_خطأ، pnl).
    """
    path = _history_path()
    sym = _norm_hist_symbol(str(symbol or ""))
    q = float(quantity or 0)
    ab = float(avg_buy or 0)
    ex = float(exit_price or 0)
    if not sym or q <= 0:
        _debug_log(f"append_sell_forced SKIP sym={sym} q={q}")
        return False, "symbol/qty", None
    if ex <= 0:
        ex = ab if ab > 0 else 1.0
    pnl = None
    if ab > 0 and ex > 0:
        pnl = round((ex - ab) * q, 4)
    entry = {
        "symbol": sym,
        "side": "SELL",
        "price": ex,
        "quantity": q,
        "mode": mode,
        "time": datetime.now().isoformat(timespec="seconds"),
        "reason": str(reason or "").strip(),
        "pnl": pnl,
    }
    if ab > 0:
        entry["avg_buy_price"] = round(ab, 8)
    try:
        with _HISTORY_IO_LOCK:
            data = []
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, list):
                data = []
            data.append(entry)
            if len(data) > MAX_HISTORY_ENTRIES:
                data = data[-MAX_HISTORY_ENTRIES:]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
        _debug_log(f"append_sell_forced OK {sym} pnl={pnl} path={path}")
        return True, path, pnl
    except Exception as e:
        _debug_log(f"append_sell_forced FAIL {e}")
        return False, str(e), None


def patch_last_sell_pnl(
    symbol: str,
    pnl: float,
    *,
    avg_buy_price: float | None = None,
    exit_price: float | None = None,
    quantity: float | None = None,
) -> bool:
    """
    تحديث آخر صفقة بيع لنفس الرمز إن كان حقل pnl فارغاً — بعد حساب الربح لاحقاً.
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    path = _history_path()
    try:
        with _HISTORY_IO_LOCK:
            data = []
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, list) or not data:
                return False
            for i in range(len(data) - 1, -1, -1):
                e = data[i]
                if str(e.get("side", "")).upper() != "SELL":
                    continue
                if not _sym_match(str(e.get("symbol", "")), sym):
                    continue
                if e.get("pnl") is not None:
                    return False
                e["pnl"] = round(float(pnl), 4)
                if avg_buy_price is not None and float(avg_buy_price) > 0:
                    e["avg_buy_price"] = round(float(avg_buy_price), 8)
                if exit_price is not None and float(exit_price) > 0:
                    e["price"] = round(float(exit_price), 8)
                if quantity is not None and float(quantity) > 0:
                    e["quantity"] = round(float(quantity), 8)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                return True
            return False
    except Exception as ex:
        log.warning("patch_last_sell_pnl: %s", ex)
        return False


def get_last_buy_price_for_symbol(symbol: str) -> float | None:
    """آخر سعر شراء مسجّل لنفس الرمز — بديل متوسط الدخول عندما يكون جدول المراكز ناقصاً (مثل بيع البوت)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    path = _history_path()
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return None
        for e in reversed(data):
            if not _sym_match(str(e.get("symbol", "")), sym):
                continue
            if str(e.get("side", "")).upper() != "BUY":
                continue
            p = float(e.get("price") or 0)
            if p > 0:
                return p
        return None
    except Exception:
        return None


def suspect_placeholder_entry_price(entry: float, mark_price: float) -> bool:
    """
    True إذا بدا سعر الدخول placeholder (مثل 1.0 من مزامنة eToro الناقصة) مقارنة بسعر السوق.
    يمنع تفعيل حد البيع فوراً وتسجيل ربح وهمي ضخم.
    """
    try:
        e = float(entry or 0)
        m = float(mark_price or 0)
    except (TypeError, ValueError):
        return True
    if e <= 0 or m <= 0:
        return False
    if abs(e - 1.0) < 1e-2 and m > 30:
        return True
    if m >= 2000 * e and m > 20:
        return True
    return False


def get_last_buy_info_for_symbol(symbol: str) -> tuple[float | None, float | None]:
    """
    آخر صفقة شراء لنفس الرمز في السجل: (سعر_الشراء، الكمية).
    يُستعان به عندما جدول المراكز يعطي كمية/دخول صفر (مثل eToro) لحساب PnL وإشعار البيع.
    """
    sym = str(symbol or "").strip()
    if not sym:
        return None, None
    path = _history_path()
    try:
        if not os.path.isfile(path):
            return None, None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return None, None
        for e in reversed(data):
            if not _sym_match(str(e.get("symbol", "")), sym):
                continue
            if str(e.get("side", "")).upper() != "BUY":
                continue
            try:
                p = float(e.get("price") or 0)
                q = float(e.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
            if p > 0 and q > 0:
                return p, q
            if p > 0:
                return p, None
        return None, None
    except Exception:
        return None, None


def _backfill_sell_pnl_fifo(data: list) -> None:
    """
    يملأ pnl لصفقات البيع عندما يكون الحقل مفقوداً أو null، بمطابقة شراءات سابقة (FIFO) لكل رمز.
    يجب أن تكون data بالترتيب الزمني كما في الملف (الأقدم أولاً).
    """
    from collections import deque

    by_symbol: dict[str, deque] = {}
    for e in data:
        if not isinstance(e, dict):
            continue
        sym = str(e.get("symbol", "") or "").upper()
        if sym not in by_symbol:
            by_symbol[sym] = deque()
        queue = by_symbol[sym]
        s = str(e.get("side", "") or "").upper()
        try:
            p = float(e.get("price", 0) or 0)
            q = float(e.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            p, q = 0.0, 0.0
        if s == "BUY":
            queue.append((p, q))
        elif s == "SELL":
            cost = 0.0
            rem = q
            while rem > 1e-12 and queue:
                bp, bq = queue[0]
                take = min(bq, rem)
                cost += bp * take
                rem -= take
                if bq - take <= 1e-12:
                    queue.popleft()
                else:
                    queue[0] = (bp, bq - take)
            if e.get("pnl") is None and (rem <= 1e-4 or cost >= 1e-8):
                if q > 0 and p > 0:
                    e["pnl"] = round((p * q) - cost, 4)
                    if q and cost >= 0:
                        e["avg_buy_price"] = round(cost / q, 8)


def _compute_sell_pnl(data: list, symbol: str, sell_price: float, sell_qty: float) -> tuple[float | None, float | None]:
    """
    حساب الربح/الخسارة لصفقة بيع بمطابقة شراءات سابقة (FIFO) لنفس الرمز.
    يُرجع (pnl, cost_total) حيث cost_total = إجمالي تكلفة الشراء المطابق؛ أو (None, None).
    """
    from collections import deque
    sell_price = float(sell_price)
    sell_qty = float(sell_qty)
    queue = deque()  # (price, qty)
    for e in data:
        if not _sym_match(str(e.get("symbol", "")), symbol):
            continue
        s = str(e.get("side", "")).upper()
        p = float(e.get("price", 0))
        q = float(e.get("quantity", 0))
        if s == "BUY":
            queue.append((p, q))
        elif s == "SELL":
            remaining = q
            while remaining > 1e-12 and queue:
                bp, bq = queue[0]
                take = min(bq, remaining)
                remaining -= take
                if bq - take <= 1e-12:
                    queue.popleft()
                else:
                    queue[0] = (bp, bq - take)
    remaining_sell = sell_qty
    cost = 0.0
    while remaining_sell > 1e-12 and queue:
        bp, bq = queue[0]
        take = min(bq, remaining_sell)
        cost += bp * take
        remaining_sell -= take
        if bq - take <= 1e-12:
            queue.popleft()
        else:
            queue[0] = (bp, bq - take)
    # تسامح أخطاء التقريب العشرية (كميات صغيرة جداً)
    tol = max(1e-10, abs(float(sell_qty)) * 1e-7)
    if remaining_sell > tol:
        return None, None
    pnl = round((sell_price * sell_qty) - cost, 4)
    return pnl, cost


def record(
    symbol: str,
    side: str,
    price: float,
    quantity: float,
    mode: str = "live",
    reason: str = "",
    *,
    avg_buy_price: float | None = None,
    use_fifo: bool = True,
    etoro_position_id: int | None = None,
    etoro_open_order_id: int | None = None,
) -> float | None:
    """
    تسجيل صفقة في الملف.
    mode: "live" | "testnet" | "paper"
    reason: سبب الشراء أو البيع (مثل: هدف الربح، وقف الخسارة، شراء يدوي).
    use_fifo: عند البيع، إن كان False ومُمرَّر avg_buy_price (من مركز المنصة) يُحسب
    الربح كـ (سعر_البيع − متوسط_الدخول) × الكمية دون FIFO — يمنع ظهور «ربح وهمي»
    من مشتريات قديمة في السجل لا تخص هذا المركز.
    تُرجع الربح/الخسارة (USDT) إن كانت الصفقة بيعاً ومُحسوباً، وإلا None.
    """
    path = _history_path()
    price = float(price)
    quantity = float(quantity)
    entry = {
        "symbol": _norm_hist_symbol(str(symbol)),
        "side": str(side).upper(),
        "price": price,
        "quantity": quantity,
        "mode": mode,
        "time": datetime.now().isoformat(timespec="seconds"),
        "reason": str(reason or "").strip(),
    }
    try:
        _ep = int(etoro_position_id) if etoro_position_id is not None else 0
        if _ep > 0:
            entry["etoro_position_id"] = _ep
    except (TypeError, ValueError):
        pass
    try:
        _eo = int(etoro_open_order_id) if etoro_open_order_id is not None else 0
        if _eo > 0:
            entry["etoro_open_order_id"] = _eo
    except (TypeError, ValueError):
        pass
    pnl = None
    try:
        with _HISTORY_IO_LOCK:
            data = []
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, list):
                data = []
            if str(side).upper() == "SELL":
                ab = None
                if avg_buy_price is not None:
                    try:
                        ab = float(avg_buy_price)
                    except (TypeError, ValueError):
                        ab = None
                if (
                    not use_fifo
                    and ab is not None
                    and ab > 0
                    and quantity
                    and price > 0
                ):
                    pnl = round((float(price) - ab) * float(quantity), 4)
                    cost_total = ab * float(quantity)
                    entry["avg_buy_price"] = round(ab, 8)
                else:
                    pnl, cost_total = _compute_sell_pnl(data, symbol, price, quantity)
                    if pnl is None and ab is not None and ab > 0 and quantity and price > 0:
                        pnl = round((float(price) - ab) * float(quantity), 4)
                        entry["avg_buy_price"] = round(ab, 8)
                    # احتياط: FIFO فشل (كمية لا تطابق، إلخ) — آخر شراء لنفس الرمز في الملف
                    if pnl is None and price > 0 and quantity > 0:
                        lb = _last_buy_price_from_data(data, symbol)
                        if lb and lb > 0:
                            pnl = round((float(price) - lb) * float(quantity), 4)
                            entry["avg_buy_price"] = round(lb, 8)
                            log.info(
                                "trade_history: بيع — استُخدم آخر سعر شراء في السجل لحساب PnL (FIFO تعذّر) sym=%s lb=%s",
                                entry["symbol"],
                                lb,
                            )
                if pnl is not None:
                    entry["pnl"] = pnl
                if (
                    cost_total is not None
                    and quantity
                    and pnl is not None
                    and "avg_buy_price" not in entry
                ):
                    entry["avg_buy_price"] = round(float(cost_total) / float(quantity), 8)
            if str(side).upper() == "SELL" and data:
                last_e = data[-1]
                if isinstance(last_e, dict) and _duplicate_sell_burst(last_e, entry):
                    log.warning(
                        "trade_history: skipped duplicate SELL burst sym=%s price=%s qty=%s",
                        entry.get("symbol"),
                        entry.get("price"),
                        entry.get("quantity"),
                    )
                    try:
                        lp = last_e.get("pnl")
                        return float(lp) if lp is not None else pnl
                    except (TypeError, ValueError):
                        return pnl
            data.append(entry)
            if len(data) > MAX_HISTORY_ENTRIES:
                data = data[-MAX_HISTORY_ENTRIES:]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
        return pnl
    except Exception as e:
        log.warning("Could not save trade history: %s", e)
        return None


def _price_qty_fuzzy_match(a: float, b: float, rtol: float = 0.0008) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) <= max(rtol * max(a, b), 1e-10)


def backfill_missing_buys_from_etoro_positions(
    positions: list | None,
    *,
    mode: str,
    to_hist_symbol: Callable[[str], str],
) -> int:
    """
    عند مزامنة مراكز eToro: يضيف صفوف BUY ناقصة في السجل أو يُكمّل etoro_position_id
    على سجل شراء قديم بلا معرّف (نفس الرمز/السعر/الكمية تقريباً).
    يُرجع عدد التعديلات (إضافات + صفوف مُحدَّثة).
    """
    if not positions:
        return 0
    path = _history_path()
    changed = 0
    try:
        with _HISTORY_IO_LOCK:
            data: list = []
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, list):
                data = []

            def _has_buy_with_pid(pid: int) -> bool:
                if pid <= 0:
                    return False
                for e in data:
                    if not isinstance(e, dict):
                        continue
                    if str(e.get("side", "")).upper() != "BUY":
                        continue
                    try:
                        if int(e.get("etoro_position_id") or 0) == pid:
                            return True
                    except (TypeError, ValueError):
                        continue
                return False

            def _has_buy_with_oid(oid: int) -> bool:
                if oid <= 0:
                    return False
                for e in data:
                    if not isinstance(e, dict):
                        continue
                    if str(e.get("side", "")).upper() != "BUY":
                        continue
                    try:
                        if int(e.get("etoro_open_order_id") or 0) == oid:
                            return True
                    except (TypeError, ValueError):
                        continue
                return False

            for p in positions:
                if not isinstance(p, dict):
                    continue
                sym_raw = str(p.get("symbol") or "").strip()
                entry_px = float(p.get("entry_price") or 0)
                qty = float(p.get("quantity") or 0)
                if entry_px <= 0 or qty <= 0:
                    continue
                hs = to_hist_symbol(sym_raw)
                if not hs:
                    continue
                pid = 0
                try:
                    pv = p.get("position_id")
                    if pv is not None:
                        pid = int(pv)
                except (TypeError, ValueError):
                    pid = 0
                oid = 0
                for k in ("order_id", "orderID", "OrderID"):
                    if k in p and p.get(k) is not None:
                        try:
                            oid = int(p.get(k) or 0)
                        except (TypeError, ValueError):
                            oid = 0
                        if oid > 0:
                            break

                if pid > 0 and _has_buy_with_pid(pid):
                    continue
                if oid > 0 and _has_buy_with_oid(oid):
                    continue

                patched = False
                if pid > 0 or oid > 0:
                    for e in reversed(data):
                        if not isinstance(e, dict):
                            continue
                        if str(e.get("side", "")).upper() != "BUY":
                            continue
                        if not _sym_match(str(e.get("symbol", "")), hs):
                            continue
                        try:
                            ep = int(e.get("etoro_position_id") or 0)
                        except (TypeError, ValueError):
                            ep = 0
                        if ep > 0:
                            continue
                        try:
                            pr = float(e.get("price") or 0)
                            qq = float(e.get("quantity") or 0)
                        except (TypeError, ValueError):
                            continue
                        if not _price_qty_fuzzy_match(pr, entry_px) or not _price_qty_fuzzy_match(qq, qty):
                            continue
                        if pid > 0:
                            e["etoro_position_id"] = pid
                        if oid > 0:
                            e["etoro_open_order_id"] = oid
                        changed += 1
                        patched = True
                        log.info(
                            "trade_history: patched legacy BUY with eToro ids sym=%s pid=%s oid=%s",
                            hs,
                            pid or "—",
                            oid or "—",
                        )
                        break

                if patched:
                    continue
                if pid <= 0 and oid <= 0:
                    continue

                entry = {
                    "symbol": hs,
                    "side": "BUY",
                    "price": round(entry_px, 10),
                    "quantity": round(qty, 10),
                    "mode": mode,
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "reason": "تعويض سجل — مزامنة مركز من eToro",
                }
                if pid > 0:
                    entry["etoro_position_id"] = pid
                if oid > 0:
                    entry["etoro_open_order_id"] = oid
                data.append(entry)
                changed += 1
                log.info(
                    "trade_history: backfill BUY from eToro sync sym=%s pid=%s oid=%s",
                    hs,
                    pid or "—",
                    oid or "—",
                )

            if changed <= 0:
                return 0
            if len(data) > MAX_HISTORY_ENTRIES:
                data = data[-MAX_HISTORY_ENTRIES:]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
        return changed
    except Exception as e:
        log.warning("backfill_missing_buys_from_etoro_positions: %s", e)
        return 0


def get_today_summary() -> dict:
    """ملخص اليوم: كل صفقة (شراء/بيع) + إجمالي ربح / خسارة / صافي من صفقات البيع."""
    today = datetime.now().strftime("%Y-%m-%d")
    path = _history_path()
    empty = {"count": 0, "pnl": 0.0, "profit": 0.0, "loss": 0.0}
    if not os.path.isfile(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return empty
    if not isinstance(data, list):
        return empty
    _backfill_sell_pnl_fifo(data)
    count = 0
    net = 0.0
    profit = 0.0
    loss = 0.0
    for e in data:
        t = (e.get("time") or "")[:10]
        if t != today:
            continue
        if str(e.get("side", "")).upper() != "SELL":
            continue
        count += 1
        p = e.get("pnl")
        if p is not None:
            pf = float(p)
            net += pf
            if pf > 0:
                profit += pf
            elif pf < 0:
                loss += abs(pf)
    return {
        "count": count,
        "pnl": round(net, 2),
        "profit": round(profit, 2),
        "loss": round(loss, 2),
    }


def get_all_history_aggregate() -> dict:
    """إجمالي كل السجل: عدد كل الصفقات + مجموع الأرباح + مجموع الخسائر (قيمة موجبة) + الصافي."""
    path = _history_path()
    empty = {"n": 0, "profit": 0.0, "loss": 0.0, "net": 0.0}
    if not os.path.isfile(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return empty
    if not isinstance(data, list):
        return empty
    _backfill_sell_pnl_fifo(data)
    profit = 0.0
    loss = 0.0
    net = 0.0
    n_sell = 0
    for e in data:
        if str(e.get("side", "")).upper() != "SELL":
            continue
        n_sell += 1
        p = e.get("pnl")
        if p is not None:
            pf = float(p)
            net += pf
            if pf > 0:
                profit += pf
            elif pf < 0:
                loss += abs(pf)
    return {
        "n": n_sell,
        "profit": round(profit, 2),
        "loss": round(loss, 2),
        "net": round(net, 2),
    }


def count_buy_trades_today() -> int:
    """عدد أوامر الشراء المسجّلة اليوم (حسب توقيت الجهاز) — لاستخدام حد الصفقات اليومي."""
    today = datetime.now().strftime("%Y-%m-%d")
    path = _history_path()
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return 0
    if not isinstance(data, list):
        return 0
    count = 0
    for e in data:
        t = (e.get("time") or "")[:10]
        if t == today and str(e.get("side", "")).upper() == "BUY":
            count += 1
    return count


def count_consecutive_losses(limit: int = 500) -> int:
    """
    عدد الخسائر المتتالية من سجل التداول الفعلي (trade_history.json).
    - يعتمد فقط على صفقات SELL التي لها pnl.
    - يبدأ العد من الأحدث للخلف ويتوقف عند أول ربح.
    """
    rows = load_history(limit)
    losses = 0
    for e in reversed(rows):
        if str(e.get("side", "")).upper() != "SELL":
            continue
        p = e.get("pnl")
        if p is None:
            continue
        try:
            pf = float(p)
        except (TypeError, ValueError):
            continue
        if pf < 0:
            losses += 1
        elif pf > 0:
            break
    return losses


def get_last_closed_trade_pnl() -> float | None:
    """آخر صفقة بيع مغلقة (لها ربح/خسارة) — للعرض في لوحة التوصية."""
    rows = load_history(50)
    for e in reversed(rows):
        if str(e.get("side", "")).upper() != "SELL":
            continue
        p = e.get("pnl")
        if p is not None:
            return float(p)
    return None


def load_history(limit: int | None = None) -> list[dict]:
    """تحميل آخر limit صفقة (مع حساب الربح/الخسارة للصفقات التي لا تحتوي pnl)."""
    if limit is None:
        limit = MAX_HISTORY_ENTRIES
    path = _history_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.warning("Could not load trade history: %s", e)
        return []
    if not isinstance(data, list):
        return []
    data = data[-limit:]
    _backfill_sell_pnl_fifo(data)
    return data


def history_rows_closed_trades_only(rows: list[dict]) -> list[dict]:
    """صفوف عرض جدول سجل الصفقات: البيع فقط (تسوية كاملة)، دون صفوف الشراء التي تظهر أعمدة فارغة."""
    return [r for r in rows if str(r.get("side", "")).upper() == "SELL"]


# أسماء الأيام بالعربية (حسب توقيت الجهاز لا يُعتمد عليه للعربية)
_DAY_NAMES_AR = {
    "Monday": "الإثنين", "Tuesday": "الثلاثاء", "Wednesday": "الأربعاء",
    "Thursday": "الخميس", "Friday": "الجمعة", "Saturday": "السبت", "Sunday": "الأحد",
}


def get_daily_log(limit_days: int = 90) -> list[dict]:
    """
    سجل يومي: تجميع الصفقات حسب التاريخ مع ربح/خسارة لكل يوم وعملة.
    كل عنصر: {"date", "day_name", "symbols": [{symbol, pnl} صافي لكل رمز], "total_pnl",
              "gross_profit": مجموع أرباح كل صفقات البيع الموجبة في اليوم,
              "gross_loss": مجموع خسائر كل صفقات البيع السالبة (قيمة موجبة)}
    """
    rows = load_history()
    # gross_* تُحسب لكل صفقة بيع على حدة — لا تعتمد على صافي الرمز (عدة صفقات لنفس العملة).
    by_date = {}  # date -> {symbols, total_pnl, gross_profit, gross_loss}
    for e in rows:
        t = (e.get("time") or "")[:10]
        if not t or len(t) < 10:
            continue
        pnl = e.get("pnl")
        if pnl is None:
            continue
        if str(e.get("side", "")).upper() != "SELL":
            continue
        pnl_f = float(pnl)
        sym = str(e.get("symbol", "")).upper()
        if t not in by_date:
            by_date[t] = {
                "symbols": {},
                "total_pnl": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
            }
        info = by_date[t]
        info["symbols"][sym] = info["symbols"].get(sym, 0.0) + pnl_f
        info["total_pnl"] += pnl_f
        if pnl_f > 0:
            info["gross_profit"] += pnl_f
        elif pnl_f < 0:
            info["gross_loss"] += abs(pnl_f)
    out = []
    for date in sorted(by_date.keys(), reverse=True)[:limit_days]:
        info = by_date[date]
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            day_en = dt.strftime("%A")
            day_name = _DAY_NAMES_AR.get(day_en, day_en)
        except Exception:
            day_name = date
        symbols_list = [{"symbol": s, "pnl": round(p, 2)} for s, p in info["symbols"].items()]
        out.append({
            "date": date,
            "day_name": day_name,
            "symbols": symbols_list,
            "total_pnl": round(info["total_pnl"], 2),
            "gross_profit": round(info["gross_profit"], 2),
            "gross_loss": round(info["gross_loss"], 2),
        })
    return out


class TradeHistoryWindow(QDialog):
    """نافذة عرض سجل الصفقات — جدول أعمدة وصفوف مثل المراكز المفتوحة."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("history_title"))
        self.setMinimumSize(520, 400)
        layout = QVBoxLayout(self)
        group = QGroupBox(tr("history_title"))
        group.setStyleSheet(GROUP_BOX_STYLE)
        inner = QVBoxLayout(group)
        self.table = QTableWidget()
        self.table.setObjectName("TradeHistoryWindowTable")
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            tr("history_col_time"), tr("history_col_symbol"), tr("history_col_side"),
            tr("history_col_buy_price"), tr("history_col_sell_price"),
            tr("history_col_qty"), tr("history_col_value"),
            tr("history_col_pnl"), tr("history_col_mode"), tr("history_col_reason"),
        ])
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(TABLE_BOX_STYLE)
        self.table.setMinimumHeight(460)
        inner.addWidget(self.table, 1)
        self.today_summary_label = QLabel("")
        self.today_summary_label.setStyleSheet(
            f"color: {TOP_TEXT_MUTED}; padding: 4px; font-size: 12px;"
        )
        inner.addWidget(self.today_summary_label)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(
            f"font-weight: bold; padding: 6px; color: {TOP_TEXT_PRIMARY};"
        )
        inner.addWidget(self.summary_label)
        layout.addWidget(group, 1)
        btn_row = QHBoxLayout()
        clear_btn = QPushButton(tr("history_clear_all"))
        clear_btn.setToolTip(tr("history_clear_all_tip"))
        clear_btn.clicked.connect(self._on_clear_all_clicked)
        btn_row.addWidget(clear_btn)
        dedupe_btn = QPushButton(tr("history_dedupe_sells"))
        dedupe_btn.setToolTip(tr("history_dedupe_sells_tip"))
        dedupe_btn.clicked.connect(self._on_dedupe_sells_clicked)
        btn_row.addWidget(dedupe_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        path_lbl = QLabel(tr("history_file_path").format(path=_history_path()))
        path_lbl.setWordWrap(True)
        path_lbl.setStyleSheet(f"color: {TOP_TEXT_MUTED}; font-size: 10px;")
        layout.addWidget(path_lbl)
        close_btn = QPushButton(tr("history_close"))
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)
        self._refresh()

    def _on_clear_all_clicked(self):
        r = QMessageBox.question(
            self,
            tr("history_clear_title"),
            tr("history_clear_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        ok, info, bak = clear_all_trade_history(backup=True)
        if ok:
            msg = tr("history_cleared_ok").format(path=info)
            if bak:
                msg += "\n" + tr("history_backup_saved").format(path=bak)
            QMessageBox.information(self, tr("history_clear_title"), msg)
            self._refresh()
        else:
            QMessageBox.warning(self, tr("history_clear_title"), info)

    def _on_dedupe_sells_clicked(self):
        n, info = dedupe_consecutive_sell_bursts()
        if isinstance(n, int) and n > 0:
            QMessageBox.information(
                self,
                tr("history_dedupe_sells"),
                tr("history_dedupe_ok").format(n=n),
            )
            self._refresh()
            return
        if isinstance(info, str) and info and (info.startswith("load failed") or info.startswith("write failed")):
            QMessageBox.warning(
                self,
                tr("history_dedupe_sells"),
                tr("history_dedupe_fail").format(msg=info),
            )
            return
        QMessageBox.information(self, tr("history_dedupe_sells"), tr("history_dedupe_none"))

    def _refresh(self):
        self.table.clearSpans()
        self.table.setRowCount(0)
        rows = history_rows_closed_trades_only(load_history())
        groups = _group_rows_by_date(rows)
        num_cols = 10
        total_rows = sum(1 + len(trades) for _, trades in groups)
        self.table.setRowCount(total_rows)
        row_idx = 0
        for date_str, day_rows in groups:
            date_item = _centered_item(date_str)
            date_item.setBackground(QColor(_HISTORY_DATE_ROW_BG))
            date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row_idx, 0, date_item)
            self.table.setSpan(row_idx, 0, 1, num_cols)
            row_idx += 1
            for r in day_rows:
                price = float(r.get("price", 0))
                qty = float(r.get("quantity", 0))
                value = price * qty
                pnl = r.get("pnl")
                side = str(r.get("side", "")).upper()
                side_ar = tr("trading_side_buy") if side == "BUY" else tr("trading_side_sell")
                pnl_str = "—"
                if pnl is not None:
                    pnl_str = f"{float(pnl):+.2f}"
                buy_price_str = "—"
                sell_price_str = "—"
                if side == "BUY":
                    buy_price_str = format_price(price)
                else:
                    sell_price_str = format_price(price)
                    avg_buy = r.get("avg_buy_price")
                    if avg_buy is not None:
                        buy_price_str = format_price(float(avg_buy))
                pnl_item = _centered_item(pnl_str)
                if pnl is not None:
                    pnl_f = float(pnl)
                    if pnl_f > 0:
                        pnl_item.setForeground(QColor("#00aa00"))
                    elif pnl_f < 0:
                        pnl_item.setForeground(QColor("#cc0000"))
                mode_str = tr("history_mode_test") if r.get("mode") == "testnet" else str(r.get("mode", ""))
                self.table.setItem(row_idx, 0, _centered_item(_time_only(r.get("time", ""))))
                self.table.setItem(row_idx, 1, _centered_item(str(r.get("symbol", ""))))
                self.table.setItem(row_idx, 2, _centered_item(side_ar))
                self.table.setItem(row_idx, 3, _centered_item(buy_price_str))
                self.table.setItem(row_idx, 4, _centered_item(sell_price_str))
                self.table.setItem(row_idx, 5, _centered_item(f"{qty:.6f}"))
                self.table.setItem(row_idx, 6, _centered_item(f"{value:.2f}"))
                self.table.setItem(row_idx, 7, pnl_item)
                self.table.setItem(row_idx, 8, _centered_item(mode_str))
                self.table.setItem(row_idx, 9, _centered_item(r.get("reason", "") or "—"))
                row_idx += 1
        agg = get_all_history_aggregate()
        if hasattr(self, "summary_label") and self.summary_label:
            self.summary_label.setText(
                tr("history_summary").format(
                    n=agg["n"],
                    profit=agg["profit"],
                    loss=agg["loss"],
                    net=agg["net"],
                )
            )
        today = get_today_summary()
        if hasattr(self, "today_summary_label") and self.today_summary_label:
            self.today_summary_label.setText(
                tr("history_today_summary").format(
                    n=today["count"],
                    profit=today.get("profit", 0.0),
                    loss=today.get("loss", 0.0),
                    net=today.get("pnl", 0.0),
                )
            )
        try:
            self.table.verticalScrollBar().setValue(0)
        except Exception:
            pass


def _centered_item(text):
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return it


def _time_only(iso_or_datetime_str: str) -> str:
    """استخراج زمن الصفقة فقط (مثل 10:30 أو 14:25) من النص الكامل."""
    s = (iso_or_datetime_str or "").strip()
    if len(s) >= 16 and ("T" in s or " " in s):
        part = s.split("T")[-1].split(" ")[-1][:5]
        if len(part) == 5 and ":" in part:
            return part
    if len(s) >= 5:
        return s[11:16] if len(s) > 16 else s[-5:]
    return s


def _group_rows_by_date(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """تجميع الصفقات حسب التاريخ (الأحدث أولاً)، وكل تاريخ مع صفقاته (الأحدث أولاً)."""
    from collections import defaultdict
    by_date = defaultdict(list)
    for r in rows:
        date = (r.get("time") or "")[:10]
        if len(date) == 10:
            by_date[date].append(r)
    for date in by_date:
        by_date[date].sort(key=lambda x: x.get("time", ""), reverse=True)
    return [(d, by_date[d]) for d in sorted(by_date.keys(), reverse=True)]


class DailyLogPanel(QWidget):
    """صفحة السجل اليومي — جدول أعمدة وصفوف مثل المراكز المفتوحة."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        group = QGroupBox(tr("history_daily_log_title"))
        group.setStyleSheet(GROUP_BOX_STYLE)
        inner = QVBoxLayout(group)
        self.daily_table = QTableWidget()
        self.daily_table.setObjectName("DailyLogTable")
        self.daily_table.setColumnCount(6)
        self.daily_table.setHorizontalHeaderLabels([
            tr("history_daily_date"),
            tr("history_daily_day"),
            tr("history_daily_currencies"),
            tr("history_daily_profit_col"),
            tr("history_daily_loss_col"),
            tr("history_daily_net_col"),
        ])
        self.daily_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.daily_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.daily_table.verticalHeader().setVisible(False)
        self.daily_table.setStyleSheet(TABLE_BOX_STYLE)
        inner.addWidget(self.daily_table, 1)
        self.daily_total_label = QLabel("")
        self.daily_total_label.setStyleSheet(DAILY_TOTAL_LABEL_STYLE)
        inner.addWidget(self.daily_total_label)
        layout.addWidget(group, 1)
        refresh_btn = QPushButton(tr("history_refresh"))
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)
        self.refresh()

    def refresh(self):
        """ملء جدول السجل اليومي — إجمالي الربح، إجمالي الخسارة، والقيمة الإجمالية (ملونة)."""
        days = get_daily_log(limit_days=60)
        self.daily_table.setRowCount(len(days))
        total_profit = 0.0   # مجموع الأرباح فقط
        total_loss = 0.0     # مجموع الخسائر (قيمة موجبة)
        for i, day in enumerate(days):
            self.daily_table.setItem(i, 0, _centered_item(day["date"]))
            self.daily_table.setItem(i, 1, _centered_item(day["day_name"]))
            parts = []
            for s in day["symbols"]:
                p = s["pnl"]
                parts.append(f"{s['symbol']}: {format_currency(p, signed=True)}")
            day_profit = float(day.get("gross_profit", 0) or 0)
            day_loss = float(day.get("gross_loss", 0) or 0)
            curr_item = _centered_item("  |  ".join(parts) if parts else "—")
            self.daily_table.setItem(i, 2, curr_item)
            total_profit += day_profit
            total_loss += day_loss
            # عمود مجموع الربح
            profit_item = _centered_item(format_currency(day_profit))
            profit_item.setForeground(QColor("#00aa00"))
            self.daily_table.setItem(i, 3, profit_item)
            # عمود مجموع الخسارة
            loss_item = _centered_item(format_currency(day_loss))
            loss_item.setForeground(QColor("#cc0000"))
            self.daily_table.setItem(i, 4, loss_item)
            # عمود الصافي
            day_net = day_profit - day_loss
            net_item = _centered_item(format_currency(day_net, signed=True))
            if day_net > 0:
                net_item.setForeground(QColor("#00aa00"))
            elif day_net < 0:
                net_item.setForeground(QColor("#cc0000"))
            self.daily_table.setItem(i, 5, net_item)
        # القيمة الإجمالية = إجمالي الربح − إجمالي الخسارة
        grand_total = total_profit - total_loss
        self.daily_total_label.setTextFormat(Qt.TextFormat.RichText)
        if grand_total > 0:
            color = "#00aa00"
        elif grand_total < 0:
            color = "#cc0000"
        else:
            color = "#888"
        msg = tr("history_daily_profit_loss_net").format(
            profit=format_currency(total_profit),
            loss=format_currency(total_loss),
            net=format_currency(grand_total, signed=True),
            color=color,
        )
        self.daily_total_label.setText(msg)
        self.daily_total_label.setStyleSheet(DAILY_TOTAL_LABEL_STYLE)


class TradeHistoryPanel(QWidget):
    """صفحة سجل الصفقات — جدول أعمدة وصفوف مثل المراكز المفتوحة."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        group = QGroupBox(tr("history_title"))
        group.setStyleSheet(GROUP_BOX_STYLE)
        inner = QVBoxLayout(group)
        self.table = QTableWidget()
        self.table.setObjectName("TradeHistoryTable")
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            tr("history_col_time"),
            tr("history_col_symbol"),
            tr("history_col_side"),
            tr("history_col_buy_price"),
            tr("history_col_sell_price"),
            tr("history_col_value"),
            tr("history_col_pnl"),
            tr("history_col_mode"),
            tr("history_col_reason"),
        ])
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(TABLE_BOX_STYLE)
        inner.addWidget(self.table, 1)
        self.today_summary_label = QLabel("")
        self.today_summary_label.setStyleSheet(
            f"color: {TOP_TEXT_MUTED}; padding: 4px; font-size: 12px;"
        )
        inner.addWidget(self.today_summary_label)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(
            f"font-weight: bold; padding: 6px; color: {TOP_TEXT_PRIMARY};"
        )
        inner.addWidget(self.summary_label)
        layout.addWidget(group, 1)
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton(tr("history_refresh"))
        refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(refresh_btn)
        clear_btn = QPushButton(tr("history_clear_all"))
        clear_btn.setToolTip(tr("history_clear_all_tip"))
        clear_btn.clicked.connect(self._on_clear_all_clicked)
        btn_row.addWidget(clear_btn)
        dedupe_btn = QPushButton(tr("history_dedupe_sells"))
        dedupe_btn.setToolTip(tr("history_dedupe_sells_tip"))
        dedupe_btn.clicked.connect(self._on_dedupe_sells_clicked)
        btn_row.addWidget(dedupe_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        path_lbl = QLabel(tr("history_file_path").format(path=_history_path()))
        path_lbl.setWordWrap(True)
        path_lbl.setStyleSheet(f"color: {TOP_TEXT_MUTED}; font-size: 10px;")
        layout.addWidget(path_lbl)
        self.refresh()

    def _on_clear_all_clicked(self):
        r = QMessageBox.question(
            self,
            tr("history_clear_title"),
            tr("history_clear_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        ok, info, bak = clear_all_trade_history(backup=True)
        if ok:
            msg = tr("history_cleared_ok").format(path=info)
            if bak:
                msg += "\n" + tr("history_backup_saved").format(path=bak)
            QMessageBox.information(self, tr("history_clear_title"), msg)
            self.refresh()
        else:
            QMessageBox.warning(self, tr("history_clear_title"), info)

    def _on_dedupe_sells_clicked(self):
        n, info = dedupe_consecutive_sell_bursts()
        if isinstance(n, int) and n > 0:
            QMessageBox.information(
                self,
                tr("history_dedupe_sells"),
                tr("history_dedupe_ok").format(n=n),
            )
            self.refresh()
            return
        if isinstance(info, str) and info and (info.startswith("load failed") or info.startswith("write failed")):
            QMessageBox.warning(
                self,
                tr("history_dedupe_sells"),
                tr("history_dedupe_fail").format(msg=info),
            )
            return
        QMessageBox.information(self, tr("history_dedupe_sells"), tr("history_dedupe_none"))

    def refresh(self):
        """تحميل وعرض آخر الصفقات: تاريخ كل يوم في سطر منفصل (في المنتصف)، ثم زمن كل صفقة مع التفاصيل."""
        try:
            self.table.clearSpans()
            self.table.setRowCount(0)
        except Exception:
            pass
        rows = history_rows_closed_trades_only(load_history())
        groups = _group_rows_by_date(rows)
        num_cols = 9
        total_rows = sum(1 + len(trades) for _, trades in groups)
        self.table.setRowCount(total_rows)
        row_idx = 0
        for date_str, day_rows in groups:
            date_item = _centered_item(date_str)
            date_item.setBackground(QColor(_HISTORY_DATE_ROW_BG))
            date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row_idx, 0, date_item)
            self.table.setSpan(row_idx, 0, 1, num_cols)
            row_idx += 1
            for r in day_rows:
                price = float(r.get("price", 0))
                qty = float(r.get("quantity", 0))
                value = price * qty
                pnl = r.get("pnl")
                side = str(r.get("side", "")).upper()
                side_ar = tr("trading_side_buy") if side == "BUY" else tr("trading_side_sell")
                pnl_str = "—"
                if pnl is not None:
                    pnl_str = f"{float(pnl):+.2f}"
                buy_price_str = "—"
                sell_price_str = "—"
                if side == "BUY":
                    buy_price_str = format_price(price)
                else:
                    sell_price_str = format_price(price)
                    avg_buy = r.get("avg_buy_price")
                    if avg_buy is not None:
                        buy_price_str = format_price(float(avg_buy))
                buy_item = _centered_item(buy_price_str)
                sell_item = _centered_item(sell_price_str)
                value_item = _centered_item(f"{value:.2f}")
                pnl_item = _centered_item(pnl_str)
                if pnl is not None:
                    pnl_f = float(pnl)
                    if pnl_f > 0:
                        c = QColor("#00aa00")
                        pnl_item.setForeground(c)
                        buy_item.setForeground(c)
                        sell_item.setForeground(c)
                        value_item.setForeground(c)
                    elif pnl_f < 0:
                        c = QColor("#cc0000")
                        pnl_item.setForeground(c)
                        buy_item.setForeground(c)
                        sell_item.setForeground(c)
                        value_item.setForeground(c)
                mode_str = tr("history_mode_test") if r.get("mode") == "testnet" else str(r.get("mode", ""))
                self.table.setItem(row_idx, 0, _centered_item(_time_only(r.get("time", ""))))
                self.table.setItem(row_idx, 1, _centered_item(str(r.get("symbol", ""))))
                self.table.setItem(row_idx, 2, _centered_item(side_ar))
                self.table.setItem(row_idx, 3, buy_item)
                self.table.setItem(row_idx, 4, sell_item)
                self.table.setItem(row_idx, 5, value_item)
                self.table.setItem(row_idx, 6, pnl_item)
                self.table.setItem(row_idx, 7, _centered_item(mode_str))
                self.table.setItem(row_idx, 8, _centered_item(r.get("reason", "") or "—"))
                row_idx += 1
        agg = get_all_history_aggregate()
        if self.summary_label:
            self.summary_label.setText(
                tr("history_summary").format(
                    n=agg["n"],
                    profit=agg["profit"],
                    loss=agg["loss"],
                    net=agg["net"],
                )
            )
        today = get_today_summary()
        if getattr(self, "today_summary_label", None):
            self.today_summary_label.setText(
                tr("history_today_summary").format(
                    n=today["count"],
                    profit=today.get("profit", 0.0),
                    loss=today.get("loss", 0.0),
                    net=today.get("pnl", 0.0),
                )
            )
        try:
            self.table.verticalScrollBar().setValue(0)
        except Exception:
            pass
