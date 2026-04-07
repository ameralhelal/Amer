# exchange_etoro.py — عميل eToro API (تداول بالرصيد الوهمي أو الحقيقي)
from __future__ import annotations

import json
import logging
import os
from typing import NamedTuple
import re
import time
import uuid
import requests

log = logging.getLogger("trading.exchange_etoro")

BASE_URL = "https://public-api.etoro.com"


class EtoroBalanceBreakdown(NamedTuple):
    """تفاصيل رصيد USD من استجابة PnL (eToro)."""

    credits: float
    pending_orders_for_open: float
    pending_orders_list: float
    pending_total_applied: float
    available: float
    ignored_stale_orders_list: bool


_ETORO_ORDER_STATUS_CLOSED = frozenset(
    {
        "filled",
        "cancelled",
        "canceled",
        "rejected",
        "completed",
        "closed",
        "done",
        "expired",
        "settled",
        "executed",
        "failed",
        "inactive",
    }
)


def _etoro_dict_order_id(o: dict) -> int | None:
    for key in ("orderID", "orderId", "OrderID", "OrderId", "id", "Id"):
        v = o.get(key)
        if v is None:
            continue
        try:
            i = int(float(v))
            if i > 0:
                return i
        except (TypeError, ValueError):
            continue
    return None


def _etoro_norm_order_status(o: dict) -> str:
    for key in (
        "orderStatus",
        "OrderStatus",
        "status",
        "Status",
        "state",
        "State",
        "orderState",
        "OrderState",
        "executionStatus",
        "ExecutionStatus",
    ):
        v = o.get(key)
        if v is not None:
            s = str(v).strip().lower().replace(" ", "").replace("_", "")
            if s:
                return s
    return ""


def _etoro_order_explicitly_closed(o: dict) -> bool:
    return _etoro_norm_order_status(o) in _ETORO_ORDER_STATUS_CLOSED


def _etoro_order_counts_in_generic_orders_list(o: dict) -> bool:
    """
    قائمة clientPortfolio.orders غالباً تضمّ تنفيذات قديمة؛ لا نطرح مبلغاً منها إلا إن وُجدت حالة «مفتوحة».
    غياب الحالة = لا نحسب (تفادي طرح آلاف الدولارات كـ «محجوز» بعد بيع/إغلاق).
    """
    if not isinstance(o, dict):
        return False
    if _etoro_order_explicitly_closed(o):
        return False
    st = _etoro_norm_order_status(o)
    return bool(st)

# تراجع أسي عند 429: 1s، 2s، 4s، 8s، 16s (أفضل الممارسات)
_ETORO_RATE_LIMIT_MAX_RETRIES = 5

# تحذير «لا مطابقة للرمز — مركز واحد» يُستدعى كثيراً؛ الخانقة على مستوى العملية وليس على كائن العميل
# (يُنشأ عميل جديد أحياناً فيُفقد العداد على self).
_ETORO_SINGLE_POS_SYM_WARN_LAST: dict[tuple[str, int, str], float] = {}
_ETORO_SINGLE_POS_SYM_WARN_INTERVAL_SEC = 1800.0


def _etoro_should_log_symbol_single_fb(want: str, pid: int, tag: str) -> bool:
    key = (str(want or "").strip().upper(), int(pid), str(tag))
    now = time.monotonic()
    last = _ETORO_SINGLE_POS_SYM_WARN_LAST.get(key, 0.0)
    if now - last >= _ETORO_SINGLE_POS_SYM_WARN_INTERVAL_SEC:
        _ETORO_SINGLE_POS_SYM_WARN_LAST[key] = now
        return True
    return False


def _etoro_parse_error_body(r: requests.Response) -> str | None:
    """استخراج message أو error من جسم الاستجابة (ردود الأخطاء الشائعة: { \"error\": \"...\", \"message\": \"...\" })."""
    try:
        j = r.json()
        if isinstance(j, dict):
            msg = j.get("message") or j.get("error") or j.get("msg") or j.get("errorMessage")
            if msg and isinstance(msg, str) and msg.strip():
                return msg.strip()
    except Exception:
        pass
    return None


def _etoro_check_response(r: requests.Response, endpoint_name: str) -> tuple[requests.Response | None, str]:
    """
    معالجة رموز حالة HTTP الشائعة (400، 401، 403، 404، 429، 500) مع قراءة error/message من الجسم.
    يُرجع (response, "") عند النجاح، أو (None, "رسالة خطأ") عند الفشل.
    """
    if r.status_code == 200:
        return r, ""
    # 304: نجاح مشروط — يُعالج في get_positions قبل التحليل (جسم غالباً فارغ)
    if r.status_code == 304:
        return r, ""
    api_msg = _etoro_parse_error_body(r)
    if r.status_code == 400:
        msg = api_msg or "معلمات الطلب غير صالحة أو تفتقر إلى الحقول المطلوبة."
        log.warning("eToro %s: 400 Bad Request — %s", endpoint_name, msg)
        return None, f"طلب غير صالح (400): {msg}"
    if r.status_code == 401:
        msg = api_msg or "مفتاح API غير صالح أو مفقود."
        log.warning("eToro %s: 401 Unauthorized — %s", endpoint_name, msg)
        return None, f"غير مصرح (401): {msg}"
    if r.status_code == 403:
        msg = api_msg or "الصلاحيات غير كافية."
        log.warning("eToro %s: 403 Forbidden — %s", endpoint_name, msg)
        return None, f"ممنوع (403): {msg}"
    if r.status_code == 404:
        msg = api_msg or "المورد المطلوب غير موجود."
        log.warning("eToro %s: 404 Not Found — %s", endpoint_name, msg)
        return None, f"غير موجود (404): {msg}"
    if r.status_code == 429:
        msg = api_msg or "تجاوز حد الطلبات. انتظر ثم أعد المحاولة."
        log.warning("eToro %s: 429 Rate limit — %s", endpoint_name, msg)
        return None, f"طلبات كثيرة جداً (429): {msg}"
    if r.status_code >= 500:
        msg = api_msg or "خطأ من جانب الخادم. تواصل مع الدعم إذا استمرت المشكلة."
        log.warning("eToro %s: HTTP %s — %s", endpoint_name, r.status_code, msg)
        return None, f"خطأ الخادم ({r.status_code}): {msg}"
    if not r.ok:
        msg = api_msg or r.text.strip() if (r.text and r.text.strip()) else f"HTTP {r.status_code}"
        log.warning("eToro %s: API error HTTP %s — %s", endpoint_name, r.status_code, (msg or "")[:200])
        return None, msg or f"خطأ من المنصة: HTTP {r.status_code}"
    return None, r.text or f"HTTP {r.status_code}"


def _etoro_debug_response_path() -> str:
    """مسار حفظ استجابة PnL للتحقق من بنية الاستجابة عند ظهور مشكلة المراكز."""
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "etoro_pnl_response_debug.json")


def _etoro_orders_debug_path() -> str:
    """مسار حفظ استجابة orders/{orderId} للتحقق من بنية الاستجابة عند فشل استخراج positionID."""
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "etoro_orders_response_debug.json")


def _etoro_business_error_from_body(j) -> tuple[int | None, str]:
    """
    eToro قد تعيد HTTP 200 مع errorCode في الجسم عند رفض فتح المركز (مثل 716: تجاوز حد الوحدات).
    يفحص الجذر و orderForOpen و response المتداخلة.
    """
    if not isinstance(j, dict):
        return None, ""
    blocks: list[dict] = [j]
    for k in ("orderForOpen", "OrderForOpen", "orderForClose", "OrderForClose"):
        sub = j.get(k)
        if isinstance(sub, dict):
            blocks.append(sub)
    resp = j.get("response")
    if isinstance(resp, dict):
        blocks.append(resp)
    for d in blocks:
        for ck in ("errorCode", "ErrorCode", "error_code"):
            if d.get(ck) is None:
                continue
            try:
                c = int(d[ck])
            except (TypeError, ValueError):
                continue
            if c == 0:
                continue
            em = (
                d.get("errorMessage")
                or d.get("ErrorMessage")
                or d.get("message")
                or ""
            )
            if not isinstance(em, str):
                em = str(em or "")
            return c, em.strip()
    return None, ""


def _etoro_parse_leverage_bounds_from_error(msg: str) -> tuple[int | None, int | None]:
    """
    يفصل حدّي الرافعة من رسالة eToro — مهم جداً ألا نخلط بينهما:
    - «MaxLeverage setting [100]» = سقف مسموح؛ لا يُخزَّن كـ «حد أقصى» إن قرأنا خطأ MinLeverage.
    - «MinLeverage setting [2]» = أدنى مسموح؛ لو عُولج كسقف، تصبح كل الطلبات مقيّدة بـ 2x (خلل شائع).
    يُرجع: (max_leverage, min_leverage) — أحدهما أو كلاهما None.
    """
    try:
        s = str(msg or "")
        max_v = None
        min_v = None
        # أمثلة: «MaxLeverage setting [5]» أو «User MaxLeverage setting [2]»
        mm = re.search(r"(?:User\s+)?MaxLeverage\s+setting\s*\[(\d+)\]", s, re.IGNORECASE)
        if mm:
            v = int(mm.group(1))
            if v > 0:
                max_v = max(1, min(v, 100))
        mm = re.search(r"(?:User\s+)?MinLeverage\s+setting\s*\[(\d+)\]", s, re.IGNORECASE)
        if mm:
            v = int(mm.group(1))
            if v > 0:
                min_v = max(1, min(v, 100))
        return max_v, min_v
    except Exception:
        return None, None


def _etoro_parse_requested_value_exceeds_max_leverage(msg: str) -> float | None:
    """مثال: Requested value: 5 exceeds User MaxLeverage setting [2] — الرقم 5 غالباً رافعة مطلوبة."""
    try:
        m = re.search(
            r"Requested\s+value:\s*([0-9]+(?:\.[0-9]+)?)\s+exceeds\s+User\s+MaxLeverage",
            str(msg or ""),
            re.IGNORECASE,
        )
        if not m:
            return None
        v = float(m.group(1))
        return v if v > 0 else None
    except Exception:
        return None


def _etoro_parse_amount_limit_from_error(msg: str) -> tuple[float | None, str | None]:
    """
    استخراج حد المبلغ من رسالة eToro إن وُجد.
    يرجع (value, kind) حيث kind إحدى: "max", "min", أو None.
    أمثلة متوقعة:
    - Requested value: 2500 exceeds MaxAmount setting [1000]
    - Requested value: 5 below MinAmount setting [10]
    """
    try:
        s = str(msg or "")
        m = re.search(r"(MaxAmount|MinAmount)\s+setting\s*\[([0-9]+(?:\.[0-9]+)?)\]", s, re.IGNORECASE)
        if not m:
            return None, None
        kind = "max" if "maxamount" in m.group(1).lower() else "min"
        v = float(m.group(2))
        if v <= 0:
            return None, None
        return v, kind
    except Exception:
        return None, None


def _etoro_human_limit_message(
    *,
    ec: int | None,
    em: str,
    requested_leverage: int | float | None = None,
    requested_amount: float | None = None,
) -> str:
    """
    تحويل أخطاء الحدود (رافعة/مبلغ) إلى رسالة عربية واضحة للمستخدم.
    """
    err_msg = (em or "").strip()
    # 1) الرافعة (حد أقصى وحد أدنى منفصلان)
    max_l, min_l = _etoro_parse_leverage_bounds_from_error(err_msg)
    req_exceed = _etoro_parse_requested_value_exceeds_max_leverage(err_msg)
    if max_l is not None and req_exceed is not None and req_exceed > float(max_l):
        disp = int(req_exceed) if req_exceed == int(req_exceed) else req_exceed
        return (
            f"[eToro] القيمة/الرافعة المطلوبة ({disp}) تتجاوز الحد الأقصى للرافعة في حسابك ({max_l}x)."
        )
    req_lev = int(requested_leverage or 0)
    if max_l is not None and req_lev > 0 and req_lev > max_l:
        return (
            f"[eToro] تجاوز حد الرافعة: المطلوب {req_lev}x بينما الحد الأقصى المسموح {max_l}x."
        )
    if min_l is not None and req_lev > 0 and req_lev < min_l:
        return (
            f"[eToro] الرافعة أقل من الحد الأدنى المسموح: المطلوب {req_lev}x بينما الحد الأدنى {min_l}x."
        )
    if max_l is not None:
        return f"[eToro] قيود الرافعة في الحساب: الحد الأقصى المسموح حالياً {max_l}x."
    if min_l is not None:
        return f"[eToro] قيود الرافعة في الحساب: الحد الأدنى المسموح حالياً {min_l}x."
    # 2) المبلغ
    amt_limit, amt_kind = _etoro_parse_amount_limit_from_error(err_msg)
    if amt_limit is not None:
        req_amt = float(requested_amount or 0)
        if amt_kind == "max":
            return (
                f"[eToro] تجاوز حد المبلغ: المطلوب {req_amt:.2f}$ بينما الحد الأقصى المسموح {amt_limit:.2f}$."
            )
        if amt_kind == "min":
            return (
                f"[eToro] المبلغ أقل من الحد الأدنى: المطلوب {req_amt:.2f}$ بينما الحد الأدنى المسموح {amt_limit:.2f}$."
            )
    # 3) fallback
    if ec is not None:
        return f"[eToro] رمز الخطأ {ec}: {err_msg or 'فشل فتح المركز'}"
    return err_msg or "فشل فتح المركز بسبب قيود المنصة."


def _etoro_find_positions_list_anywhere(obj, depth: int = 0) -> list | None:
    """البحث في أي مستوى عن مصفوفة عناصرها تحتوي على positionID/positionId (لأي بنية ترجعها eToro)."""
    if depth > 15 or obj is None:
        return None
    if isinstance(obj, list):
        if len(obj) == 0:
            return None
        for item in obj[:3]:
            if isinstance(item, dict):
                for k, v in item.items():
                    klo = str(k).lower()
                    if "position" in klo and "id" in klo and v is not None:
                        try:
                            if 0 < int(float(v)) < 10**15:
                                return obj
                        except (TypeError, ValueError):
                            pass
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            found = _etoro_find_positions_list_anywhere(v, depth + 1)
            if found is not None:
                return found
    return None


# أساس الرمز بعد قص USDT/USD — إن لم تُوجَد الأداة على eToro بنفس الاسم (مثل PAXG) نبحث بالاسم البديل.
_ETORO_SEARCH_SYMBOL_BY_BASE: dict[str, str] = {
    "PAXG": "GOLD",  # ذهب Binance (PAXGUSDT) → أداة الذهب على eToro
    "XAU": "GOLD",  # بديل شائع للذهب في التطبيق
}


def _symbol_to_etoro(symbol: str) -> str:
    """تحويل رمز التطبيق (مثل BTCUSDT) إلى internalSymbolFull لبحث eToro (مثل BTC أو GOLD)."""
    s = (symbol or "").upper()
    if s.endswith("USDT"):
        base = s[:-4]
    elif s.endswith("USD"):
        base = s[:-3]
    else:
        base = s
    return _ETORO_SEARCH_SYMBOL_BY_BASE.get(base, base)


def _normalize_position_symbol(pos: dict) -> str:
    """استخراج رمز المركز من استجابة PnL وتوحيده إلى صيغة التطبيق (مثل BTCUSDT)."""
    sym = (
        pos.get("internalSymbolFull")
        or pos.get("InternalSymbolFull")
        or pos.get("symbol")
        or pos.get("Symbol")
        or ""
    )
    sym = (sym or "").strip().upper()
    if not sym:
        return ""
    if sym.endswith("USD") and not sym.endswith("USDT"):
        sym = sym[:-3] + "USDT"
    elif not sym.endswith("USDT") and not sym.endswith("USD"):
        sym = sym + "USDT"
    return sym


def etoro_extract_position_id(pos, _depth: int = 0) -> int | None:
    """
    استخراج معرّف المركز من استجابة eToro — المفتاح يختلف بين الإصدارات (PnL، Portfolio، إلخ).
    """
    if not isinstance(pos, dict) or _depth > 8:
        return None
    direct_keys = (
        "positionID", "positionId", "PositionID", "PositionId",
        "openPositionID", "OpenPositionID", "openPositionId", "OpenPositionId",
        "cfdPositionID", "CfdPositionID", "cocoPositionID", "CocoPositionID",
        "executionPositionId", "ExecutionPositionId", "netOpenPositionId",
        "cocoOpenPositionId", "CocoOpenPositionId",
    )
    # "id" قد يكون instrumentId — نستخدمه فقط كملاذ أخير
    last_resort_keys = ("id", "Id", "ID")
    for k in direct_keys:
        v = pos.get(k)
        if v is not None and v != "":
            try:
                i = int(float(str(v).strip()))
                if 0 < i < 10**15:
                    return i
            except (TypeError, ValueError):
                pass
    for k in last_resort_keys:
        v = pos.get(k)
        if v is not None and v != "":
            try:
                i = int(float(str(v).strip()))
                if 0 < i < 10**15:
                    return i
            except (TypeError, ValueError):
                pass
    for k, v in pos.items():
        kl = str(k).lower().replace("_", "")
        if "position" in kl and "id" in kl and v is not None and not isinstance(v, (dict, list)):
            try:
                i = int(float(str(v).strip()))
                if 0 < i < 10**15:
                    return i
            except (TypeError, ValueError):
                pass
    for v in pos.values():
        if isinstance(v, dict) and len(v) <= 120:
            x = etoro_extract_position_id(v, _depth + 1)
            if x is not None:
                return x
    return None


def etoro_flatten_position_dict(item: dict) -> dict:
    """دمج الحقول من كائنات متداخلة (مثل position داخل العنصر) كما ترجعها واجهة eToro."""
    if not isinstance(item, dict):
        return {}
    merged = dict(item)
    for k in (
        "position",
        "Position",
        "openPosition",
        "OpenPosition",
        "open_position",
        "details",
        "data",
        "instrument",
        "Instrument",
        "instrumentDto",
        "InstrumentDto",
        "openInstrument",
        "OpenInstrument",
    ):
        inner = item.get(k)
        if isinstance(inner, dict):
            for a, b in inner.items():
                if merged.get(a) in (None, "", 0) and b not in (None, ""):
                    merged[a] = b
    return merged


def _etoro_pos_entry_units(p: dict) -> tuple[float, float]:
    entry = float(
        p.get("openRate") or p.get("open_rate") or p.get("OpenRate")
        or p.get("openPrice") or p.get("OpenPrice") or p.get("averageOpenPrice")
        or p.get("AverageOpenPrice") or p.get("executionOpenRate") or p.get("openUnitRate")
        or p.get("OpenUnitRate") or p.get("rate") or p.get("Rate") or 0
    )
    units = float(
        p.get("units") or p.get("Units") or p.get("positionUnits") or p.get("PositionUnits")
        or p.get("openUnits") or p.get("OpenUnits") or p.get("totalUnits") or p.get("cfdUnits")
        or p.get("leveragedUnits") or p.get("LeveragedUnits") or p.get("volume") or 0
    )
    if units <= 0 and entry > 0:
        invested = float(
            p.get("amount") or p.get("Amount") or p.get("invested") or p.get("Invested")
            or p.get("investedAmount") or p.get("InvestedAmount") or p.get("totalInvested")
            or p.get("initialInvestmentInDollars") or p.get("initialAmountInDollars")
            or p.get("totalAmount") or 0
        )
        units = invested / entry if invested > 0 else 0.0
    return entry, units


def _etoro_guess_position_id(p: dict) -> int | None:
    """إن فشل الاستخراج المعتاد: أي مفتاح يشبه معرّف مركز."""
    x = etoro_extract_position_id(p)
    if x is not None:
        return x
    skip_keys = {"instrumentid", "instrument_id", "userid", "user_id"}
    for k, v in (p or {}).items():
        kl = str(k).lower().replace("_", "")
        if kl in skip_keys:
            continue
        if "instrument" in kl and "id" in kl:
            continue
        if "position" in kl and "id" in kl:
            try:
                i = int(float(v))
                if 1 <= i < 10**15:
                    return i
            except (TypeError, ValueError):
                pass
    for k, v in (p or {}).items():
        if str(k).lower() in ("cocoorderid", "orderid", "order_id", "openingorderid"):
            try:
                i = int(float(v))
                if 1 <= i < 10**15:
                    return i
            except (TypeError, ValueError):
                pass
    return None


def etoro_deep_find_position_id(obj, depth: int = 0) -> int | None:
    """بحث متكرر عن معرّف مركز في JSON متداخل (بعض ردود eToro تضع positionId داخل position)."""
    if depth > 10 or obj is None:
        return None
    keys = (
        "positionID", "positionId", "PositionID", "PositionId",
        "openPositionID", "OpenPositionID", "cocoPositionID", "CocoPositionID",
        "executionPositionId", "netOpenPositionId",
    )
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if v is not None and v != "":
                try:
                    i = int(float(str(v).strip()))
                    if 1 <= i < 10**15:
                        return i
                except (TypeError, ValueError):
                    pass
        for v in obj.values():
            if isinstance(v, (dict, list)):
                x = etoro_deep_find_position_id(v, depth + 1)
                if x is not None:
                    return x
    elif isinstance(obj, list):
        for it in obj[:30]:
            x = etoro_deep_find_position_id(it, depth + 1)
            if x is not None:
                return x
    return None


def _etoro_orders_response_to_position_id(j: dict, order_id: int) -> int | None:
    """
    استخراج positionID من استجابة GET .../orders/{orderId} بأكبر عدد من الأشكال الممكنة.
    استجابة eToro الفعلية قد تحتوي referenceID أو CID بدل positionID.
    """
    if not isinstance(j, dict):
        return None
    # المستوى الأعلى — بما فيها ما ترجعه eToro: referenceID, CID (مرشحان لمعرّف المركز)
    for key in (
        "positionID", "positionId", "PositionID", "PositionId",
        "openPositionID", "OpenPositionID", "aggregatedPositionId", "executionPositionId",
        "referenceID", "referenceId", "ReferenceID",
    ):
        v = j.get(key)
        if v is not None and v != "":
            try:
                i = int(float(str(v).strip()))
                if 1 <= i < 10**15:
                    # تجنّب إرجاع orderID أو instrumentID (عادة أصغر من 10^6 للمؤشرات)
                    if key in ("instrumentID", "instrumentId", "InstrumentID") and i < 2_000_000:
                        continue
                    if key in ("orderID", "orderId", "OrderID") and i == int(order_id):
                        continue
                    return i
            except (TypeError, ValueError):
                pass
    # orderForOpen / OrderForOpen / order / Order (كائن الطلب قد يحتوي positionID)
    for parent in ("orderForOpen", "OrderForOpen", "order", "Order", "openOrder", "OpenOrder"):
        obj = j.get(parent)
        if not isinstance(obj, dict):
            continue
        for key in (
            "positionID", "positionId", "PositionID", "PositionId",
            "openPositionID", "aggregatedPositionId",
        ):
            v = obj.get(key)
            if v is not None and v != "":
                try:
                    i = int(float(str(v).strip()))
                    if 1 <= i < 10**15:
                        return i
                except (TypeError, ValueError):
                    pass
    # data / result / orderDetails
    for parent in ("data", "Data", "result", "Result", "orderDetails", "OrderDetails"):
        obj = j.get(parent)
        if isinstance(obj, dict):
            pid = _etoro_orders_response_to_position_id(obj, order_id)
            if pid is not None:
                return pid
    # مصفوفة positions قد تكون بأسماء أخرى
    for arr_key in ("positions", "Positions", "openPositions", "OpenPositions", "items", "Items"):
        arr = j.get(arr_key)
        if not isinstance(arr, list) or not arr:
            continue
        for p in arr[:5]:
            if not isinstance(p, dict):
                continue
            pid = (
                p.get("positionID") or p.get("PositionID") or p.get("positionId")
                or etoro_extract_position_id(p)
            )
            if pid is None:
                continue
            try:
                i = int(pid)
                if i <= 0:
                    continue
                if p.get("isOpen") is False or p.get("IsOpen") is False:
                    continue
                return i
            except (TypeError, ValueError):
                pass
    deep = etoro_deep_find_position_id(j)
    if deep is not None:
        return deep
    # ملاذ أخير: جمع كل الأعداد في نطاق معرّف مركز/طلب من الاستجابة (واستبعاد orderId المعروف)
    return _etoro_scalar_id_fallback(j, order_id)


def _etoro_scalar_id_fallback(obj, known_order_id: int) -> int | None:
    """
    يجمع من JSON كل قيم عددية في نطاق معقول لمعرّف مركز (مثلاً 10^5–10^12)
    ويستبعد known_order_id. إن وُجد عدد واحد فقط يُعاد كمرشح لـ positionID.
    إن وُجد أكثر من واحد نفضل القيمة المرتبطة بمفتاح يحتوي "position".
    """
    candidates: set[int] = set()
    position_key_value: int | None = None  # قيمة تحت مفتاح يشبه position
    known = int(known_order_id) if known_order_id else 0

    def _walk(o, depth: int = 0):
        nonlocal position_key_value
        if depth > 12 or o is None:
            return
        try:
            if isinstance(o, dict):
                for k, v in o.items():
                    key_lower = str(k).lower()
                    if isinstance(v, (dict, list)):
                        _walk(v, depth + 1)
                    else:
                        try:
                            i = int(float(str(v).strip()))
                            if 100_000 <= i < 10**13 and i != known:
                                candidates.add(i)
                                if "position" in key_lower and "id" in key_lower.replace("_", ""):
                                    position_key_value = i
                        except (TypeError, ValueError):
                            pass
            elif isinstance(o, list):
                for x in o[:50]:
                    _walk(x, depth + 1)
        except Exception:
            pass

    _walk(obj)
    if position_key_value is not None:
        return position_key_value
    if len(candidates) == 1:
        return candidates.pop()
    return None


def etoro_extract_open_order_id(item: dict) -> int | None:
    """معرّف طلب فتح المركز من عنصر /pnl — للتصفية بعد الإغلاق عندما يتأخر حذف المركز من الاستجابة."""
    if not isinstance(item, dict):
        return None
    p = etoro_flatten_position_dict(item)
    for key in (
        "cocoOrderID",
        "CocoOrderID",
        "openOrderID",
        "OpenOrderID",
        "openingOrderID",
        "OpeningOrderID",
        "orderID",
        "OrderID",
        "orderId",
        "OrderId",
    ):
        for src in (p, item):
            if not isinstance(src, dict):
                continue
            v = src.get(key)
            if v is None or v == "":
                continue
            try:
                i = int(float(str(v).strip()))
                if i > 0:
                    return i
            except (TypeError, ValueError):
                continue
    return None


def etoro_row_from_pnl_item(item: dict) -> dict | None:
    """
    صف واحد للواجهة + مطابقة الإغلاق: نفس المنطق لكل من المزامنة و find_position_id.
    يُرجع symbol (BTCUSDT), entry_price, quantity و position_id عند توفره.
    """
    if not isinstance(item, dict):
        return None
    p = etoro_flatten_position_dict(item)
    for key in (
        "positionID", "positionId", "PositionID", "PositionId",
        "openPositionID", "OpenPositionID", "cocoPositionID", "CocoPositionID",
    ):
        ov, pv = item.get(key), p.get(key)
        if pv in (None, "", 0) and ov not in (None, "", 0):
            p[key] = ov
    pid = (
        etoro_extract_position_id(p)
        or etoro_deep_find_position_id(item)
        or etoro_deep_find_position_id(p)
        or _etoro_guess_position_id(p)
    )
    sym = (
        (p.get("internalSymbolFull") or p.get("InternalSymbolFull") or p.get("symbol") or p.get("Symbol") or "")
        .strip()
        .upper()
    )
    if not sym:
        iid = p.get("instrumentID") or p.get("instrumentId") or p.get("InstrumentID") or p.get("InstrumentId")
        sym = f"ETORO_{iid}" if iid is not None else ""
    if sym and sym != "ETORO" and not str(sym).startswith("ETORO_"):
        if sym.endswith("USD") and not sym.endswith("USDT"):
            sym = sym[:-3] + "USDT"
        elif not sym.endswith("USDT") and not sym.endswith("USD"):
            sym = sym + "USDT"
    entry = float(
        p.get("openRate") or p.get("open_rate") or p.get("OpenRate")
        or p.get("openPrice") or p.get("open_price") or p.get("OpenPrice")
        or p.get("averageOpenPrice") or p.get("AverageOpenPrice")
        or p.get("openUnitRate") or p.get("OpenUnitRate")
        or p.get("executionOpenRate") or 0
    )
    units = float(
        p.get("units") or p.get("Units") or p.get("positionUnits") or p.get("PositionUnits")
        or p.get("openUnits") or p.get("OpenUnits") or p.get("leveragedUnits") or p.get("LeveragedUnits")
        or p.get("cfdUnits") or p.get("volume") or 0
    )
    if units <= 0 and entry > 0:
        invested = float(
            p.get("amount") or p.get("Amount") or p.get("invested") or p.get("Invested")
            or p.get("investedAmount") or p.get("InvestedAmount")
            or p.get("initialInvestmentInDollars") or p.get("initialAmountInDollars")
            or p.get("totalInvested") or p.get("totalAmount") or 0
        )
        units = invested / entry if invested > 0 else 0.0
    if entry <= 0 or units <= 0:
        return None
    row = {"symbol": sym, "entry_price": entry, "quantity": units}
    if pid is not None and int(pid) > 0:
        row["position_id"] = int(pid)
    oid = etoro_extract_open_order_id(item)
    if oid is not None:
        row["order_id"] = oid
    return row


def etoro_row_from_pnl_item_minimal(item: dict) -> dict | None:
    """
    ملاذ أخير: بناء صف من أي حقول متوفرة (positionId + مبلغ أو وحدات) عندما تفشل الدالة الأساسية.
    يسمح بعرض المراكز وإغلاقها حتى لو كانت استجابة eToro بصيغة مختلفة.
    """
    if not isinstance(item, dict):
        return None
    p = etoro_flatten_position_dict(item)
    pid = (
        etoro_extract_position_id(p)
        or etoro_deep_find_position_id(item)
        or etoro_deep_find_position_id(p)
        or _etoro_guess_position_id(p)
    )
    if pid is None or int(pid) <= 0:
        return None
    sym = (
        (p.get("internalSymbolFull") or p.get("InternalSymbolFull") or p.get("symbol") or p.get("Symbol") or "")
        .strip()
        .upper()
    )
    if not sym:
        iid = p.get("instrumentID") or p.get("instrumentId") or p.get("InstrumentID") or p.get("InstrumentId")
        sym = f"ETORO_{iid}" if iid is not None else "ETORO_UNKNOWN"
    if sym.endswith("USD") and not sym.endswith("USDT"):
        sym = sym[:-3] + "USDT"
    elif not sym.endswith("USDT") and not sym.endswith("USD"):
        sym = sym + "USDT"
    entry = float(
        p.get("openRate") or p.get("open_rate") or p.get("OpenRate")
        or p.get("openPrice") or p.get("open_price") or p.get("OpenPrice")
        or p.get("averageOpenPrice") or p.get("AverageOpenPrice")
        or p.get("openUnitRate") or p.get("OpenUnitRate") or 0
    )
    units = float(
        p.get("units") or p.get("Units") or p.get("positionUnits") or p.get("openUnits")
        or p.get("leveragedUnits") or p.get("cfdUnits") or p.get("volume") or 0
    )
    if units <= 0 and entry > 0:
        inv = float(
            p.get("amount") or p.get("Amount") or p.get("investedAmount")
            or p.get("initialInvestmentInDollars") or p.get("totalInvested") or 0
        )
        units = inv / entry if inv > 0 and entry > 0 else 0.0
    if units <= 0:
        inv = float(
            p.get("amount") or p.get("Amount") or p.get("initialInvestmentInDollars")
            or p.get("initialAmountInDollars") or 0
        )
        if inv > 0 and entry > 0:
            units = inv / entry
        # لا تستخدم entry=1 وهمياً: يفسد حد البيع/السجل (PnL بملايين)
        elif inv > 0 and entry <= 0:
            return None
    if units <= 0:
        return None
    if entry <= 0:
        return None
    row = {
        "symbol": sym,
        "entry_price": entry,
        "quantity": units,
        "position_id": int(pid),
    }
    oid = etoro_extract_open_order_id(item)
    if oid is not None:
        row["order_id"] = oid
    return row


class EtoroClient:
    """
    عميل eToro: مصادقة بـ User Key + API Key، أوامر سوق (بالمبلغ)، رصيد، إغلاق مراكز.
    demo=True يستخدم نقاط نهاية الحساب الوهمي، demo=False للحساب الحقيقي.
    """
    _pnl_403_logged = False
    _pnl_debug_saved_session = False
    _empty_positions_warn_ts = 0.0

    def __init__(self, user_key: str, api_key: str, demo: bool = True, debug: bool = False):
        self.user_key = (user_key or "").strip()
        self.api_key = (api_key or "").strip()
        self.demo = bool(demo)
        self.debug = bool(debug)
        self._instrument_cache = {}
        self._user_max_leverage: int | None = None
        self._user_min_leverage: int | None = None
        self._pnl_etag: str | None = None
        self._pnl_cached_positions: list | None = None
        try:
            from config import load_config

            _cfg = load_config()
            _mx = int(_cfg.get("etoro_user_max_leverage") or 0)
            _mn = int(_cfg.get("etoro_user_min_leverage") or 0)
            if _mx > 0:
                self._user_max_leverage = min(100, _mx)
            if _mn > 0:
                self._user_min_leverage = min(100, _mn)
        except Exception:
            pass
        if self.debug:
            log.setLevel(logging.DEBUG)
            log.info("[eToro] وضع التصحيح مُفعّل (DEBUG)")

    def _persist_etoro_leverage_limits(
        self, max_l: int | None, min_l: int | None
    ) -> None:
        """حفظ حدود الرافعة المكتشفة من API في الذاكرة وفي config.json حتى تُطبَّق في الطلبات التالية."""
        if max_l is not None:
            self._user_max_leverage = int(max_l)
        if min_l is not None:
            self._user_min_leverage = int(min_l)
        try:
            from config import load_config, save_config

            cfg = load_config()
            changed = False
            if max_l is not None:
                cfg["etoro_user_max_leverage"] = int(max_l)
                changed = True
            if min_l is not None:
                cfg["etoro_user_min_leverage"] = int(min_l)
                changed = True
            if (cfg.get("exchange") or "").lower() == "etoro":
                cur = int(cfg.get("leverage") or 1)
                new_lev = cur
                if max_l is not None:
                    new_lev = min(new_lev, int(max_l))
                if min_l is not None:
                    new_lev = max(new_lev, int(min_l))
                new_lev = max(1, min(100, new_lev))
                if new_lev != cur:
                    cfg["leverage"] = new_lev
                    changed = True
                    log.warning(
                        "[eToro] تعديل إعداد «الرافعة» في الملف من %sx إلى %sx ليتوافق مع حد الحساب.",
                        cur,
                        new_lev,
                    )
            if changed:
                save_config(cfg)
        except Exception as e:
            log.debug("eToro _persist_etoro_leverage_limits: %s", e)

    def _headers(self) -> dict:
        return {
            "x-user-key": self.user_key,
            "x-api-key": self.api_key,
            "x-request-id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def _exec_prefix(self) -> str:
        return "demo" if self.demo else "real"

    def _request_with_429_backoff(self, request_fn, endpoint_name: str = ""):
        """
        تنفيذ الطلب مع تراجع أسي عند 429: 1s، 2s، 4s، 8s، 16s (أفضل الممارسات).
        request_fn() يُستدعى بدون معاملات ويُرجع requests.Response.
        """
        r = None
        for attempt in range(_ETORO_RATE_LIMIT_MAX_RETRIES):
            r = request_fn()
            if r.status_code != 429:
                return r
            wait = 2 ** attempt
            log.warning(
                "eToro 429 (%-12s): انتظار %ss قبل إعادة المحاولة (%s/%s)",
                endpoint_name or "api",
                wait,
                attempt + 1,
                _ETORO_RATE_LIMIT_MAX_RETRIES,
            )
            time.sleep(wait)
        return r

    def get_instrument_id(self, symbol: str) -> int | None:
        """استخراج instrumentId من البحث (مثل BTC -> 100000)."""
        raw = (symbol or "").strip().upper()
        # رمز واجهة eToro التقني: ETORO_100000 = instrumentId مباشرة (لا يُبحث بـ internalSymbolFull)
        if raw.startswith("ETORO_"):
            try:
                iid = int(raw.split("_", 1)[1])
                if iid > 0:
                    self._instrument_cache[raw] = iid
                    return iid
            except (ValueError, IndexError, TypeError):
                pass
        key = _symbol_to_etoro(symbol)
        if key in self._instrument_cache:
            return self._instrument_cache[key]
        try:
            r = requests.get(
                f"{BASE_URL}/api/v1/market-data/search",
                headers=self._headers(),
                params={"internalSymbolFull": key},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("items") or []
            for item in items:
                if (item.get("internalSymbolFull") or "").upper() == key:
                    iid = item.get("instrumentId")
                    if iid is not None:
                        self._instrument_cache[key] = int(iid)
                        return int(iid)
            if items:
                iid = items[0].get("instrumentId")
                if iid is not None:
                    self._instrument_cache[key] = int(iid)
                    return int(iid)
        except Exception as e:
            log.warning("eToro search instrument failed for %s: %s", key, e)
        return None

    def get_last_price(self, symbol: str) -> float:
        """آخر سعر تنفيذ للأداة (من rates أو من البحث)."""
        iid = self.get_instrument_id(symbol)
        if iid is None:
            return 0.0
        try:
            r = requests.get(
                f"{BASE_URL}/api/v1/market-data/instruments/rates",
                headers=self._headers(),
                params={"instrumentIds": iid},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            for rate in (data.get("rates") or []):
                if rate.get("instrumentID") == iid:
                    return float(rate.get("lastExecution") or rate.get("ask") or 0)
        except Exception as e:
            log.warning("eToro rates failed: %s", e)
        return 0.0

    def _get_number(self, d: dict, *keys) -> float:
        """أول قيمة رقمية موجودة من القاموس بأي من المفاتيح (بدون تمييز حالة)."""
        if not d:
            return 0.0
        d_lower = {str(k).lower(): k for k in d}
        for key in keys:
            k = str(key).lower()
            if k in d_lower:
                v = d.get(d_lower[k])
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
        return 0.0

    def _find_credit_in_json(self, obj, target_keys: set, depth: int = 0) -> float:
        """البحث في أي مستوى من JSON عن مفتاح رصيد وإرجاع أول قيمة رقمية موجبة."""
        if depth > 12:
            return 0.0
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k).lower() in target_keys and v is not None:
                    try:
                        n = float(v)
                        if n > 0:
                            return n
                    except (TypeError, ValueError):
                        pass
            # أولوية الدخول إلى الحاويات المعروفة
            for key in ("clientportfolio", "clientportfolios", "portfolio", "portfolios", "data", "result", "body", "response"):
                for k, v in obj.items():
                    if str(k).lower() == key and v is not None:
                        found = self._find_credit_in_json(v, target_keys, depth + 1)
                        if found > 0:
                            return found
            for v in obj.values():
                found = self._find_credit_in_json(v, target_keys, depth + 1)
                if found > 0:
                    return found
        elif isinstance(obj, list) and obj:
            for item in obj[:5]:
                found = self._find_credit_in_json(item, target_keys, depth + 1)
                if found > 0:
                    return found
        return 0.0

    def _save_pnl_debug(self, data: dict) -> None:
        """حفظ استجابة PnL عند الرصيد 0 لتشخيص البنية (بدون بيانات حساسة)."""
        try:
            base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
            folder = os.path.join(base, "CryptoTrading")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "etoro_pnl_response.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            log.info("eToro PnL response saved to %s (balance was 0)", path)
        except Exception as e:
            log.debug("Could not save PnL debug file: %s", e)

    def _fetch_pnl_json(self) -> dict | None:
        """GET .../pnl أو None عند 403/فشل (مع نفس تسجيل الأخطاء السابق)."""
        path = f"{BASE_URL}/api/v1/trading/info/{self._exec_prefix()}/pnl"
        try:
            r = requests.get(path, headers=self._headers(), timeout=15)
            if r.status_code == 403:
                err_msg_api = ""
                try:
                    err_body = r.text.strip()
                    if err_body:
                        try:
                            err_json = r.json()
                            if not EtoroClient._pnl_403_logged:
                                self._save_pnl_debug({"403_response": err_json, "url": path})
                            err_msg_api = (err_json.get("errorMessage") or err_json.get("error_message") or "").strip()
                            if not EtoroClient._pnl_403_logged:
                                if err_json.get("errorCode") or err_json.get("error_code"):
                                    log.warning(
                                        "eToro 403: %s — %s",
                                        err_json.get("errorCode") or err_json.get("error_code") or "Forbidden",
                                        err_msg_api or err_body[:200],
                                    )
                                else:
                                    log.warning("eToro 403 response: %s", err_body[:500])
                        except Exception:
                            if not EtoroClient._pnl_403_logged:
                                self._save_pnl_debug({"403_response_text": err_body[:2000], "url": path})
                                log.warning("eToro 403 response: %s", err_body[:500])
                except Exception:
                    pass
                hint = (
                    "أضف صلاحية Read للمفتاح في eToro (الإعدادات > Trading > API Key Management)."
                    if "permission" in (err_msg_api or "").lower() or "InsufficientPermissions" in (err_msg_api or "")
                    else "تأكد من صلاحية Read للمفتاح وأن نوع المفتاح يطابق الحساب (Demo للحساب الوهمي)."
                )
                if not EtoroClient._pnl_403_logged:
                    log.warning("eToro 403 Forbidden: %s — %s", err_msg_api or "لا وصول", hint)
                EtoroClient._pnl_403_logged = True
                return None
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else None
        except Exception as e:
            log.warning("eToro PnL fetch failed: %s", e)
        return None

    def _compute_etoro_balance_from_pnl(self, data: dict) -> EtoroBalanceBreakdown:
        """تحليل استجابة PnL: ائتمان المنصة، محجوز الطلبات، متاح للشراء بنسبة/قيمة."""
        portfolio = data.get("clientPortfolio") or data.get("ClientPortfolio") or data
        if isinstance(portfolio, list) and portfolio:
            portfolio = portfolio[0]
        if not isinstance(portfolio, dict):
            portfolio = {}
        credits = self._get_number(portfolio, "credit", "credits", "Credit", "Credits")
        bonus = self._get_number(portfolio, "bonusCredit", "BonusCredit")
        if credits <= 0:
            credits = self._find_credit_in_json(
                data,
                {
                    "credit",
                    "credits",
                    "availablebalance",
                    "availablecash",
                    "balance",
                    "cash",
                    "equity",
                    "totalequity",
                    "virtualbalance",
                },
            )
        if credits <= 0 and bonus > 0:
            credits = bonus
        elif credits > 0 and bonus > 0:
            credits += bonus

        orders_for_open = (
            portfolio.get("ordersForOpen")
            or portfolio.get("OrdersForOpen")
            or data.get("ordersForOpen")
            or []
        )
        orders = portfolio.get("orders") or portfolio.get("Orders") or data.get("orders") or []
        if not isinstance(orders_for_open, list):
            orders_for_open = []
        if not isinstance(orders, list):
            orders = []

        seen_order_ids: set[int] = set()
        pending_ofo = 0.0
        for o in orders_for_open:
            if not isinstance(o, dict):
                continue
            if (o.get("mirrorID") or o.get("mirrorId") or 0) != 0:
                continue
            if _etoro_order_explicitly_closed(o):
                continue
            oid = _etoro_dict_order_id(o)
            if oid is not None:
                seen_order_ids.add(oid)
            pending_ofo += float(self._get_number(o, "amount", "Amount") or 0)

        pending_ord = 0.0
        for o in orders:
            if not isinstance(o, dict):
                continue
            oid = _etoro_dict_order_id(o)
            if oid is not None and oid in seen_order_ids:
                continue
            if not _etoro_order_counts_in_generic_orders_list(o):
                continue
            pending_ord += float(self._get_number(o, "amount", "Amount") or 0)

        ignored_stale = False
        pending_total = pending_ofo + pending_ord
        if credits > 0 and pending_ord > 0.5 * credits and pending_ofo < 0.15 * credits:
            pending_total = pending_ofo
            ignored_stale = True

        available = max(0.0, credits - pending_total)
        if available <= 0 and credits > 0:
            available = credits
        if available <= 0 and not EtoroClient._pnl_debug_saved_session:
            self._save_pnl_debug(data)
            EtoroClient._pnl_debug_saved_session = True
        return EtoroBalanceBreakdown(
            credits=float(credits),
            pending_orders_for_open=float(pending_ofo),
            pending_orders_list=float(pending_ord),
            pending_total_applied=float(pending_total),
            available=float(available),
            ignored_stale_orders_list=bool(ignored_stale),
        )

    def get_usdt_balance_breakdown(self) -> EtoroBalanceBreakdown:
        """رصيد eToro مع تفصيل المحجوز (للعرض وللتشخيص)."""
        data = self._fetch_pnl_json()
        if not data:
            return EtoroBalanceBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, False)
        return self._compute_etoro_balance_from_pnl(data)

    def get_usdt_balance(self) -> float:
        """الرصيد المتاح (USD) من نقطة PnL — يستثني محجوزاً معقولاً فقط (لا طرح كل سجل orders)."""
        return self.get_usdt_balance_breakdown().available

    def _etoro_resolve_position_id_from_order(
        self,
        order_id: int,
        cancel_check: callable | None = None,
        max_attempts: int = 15,
    ) -> int | None:
        """
        وثائق eToro: orderForOpen.orderID ثم GET .../orders/{orderId} → positions[].positionID.
        cancel_check: إن رجعت True يتوقف (عند شراء جديد يلغي العامل السابق).
        يبحث في positions وأي كائن متداخل (orderDetails، openPosition، position، إلخ) ويحفظ الاستجابة للتصحيح إن فشل.
        """
        oid = int(order_id)
        if oid <= 0:
            return None
        # آخر سبب فشل تحويل orderID -> positionID (يُستخدم لرسالة أوضح في الواجهة)
        self._last_order_lookup_error_msg = None
        path = f"{BASE_URL}/api/v1/trading/info/{self._exec_prefix()}/orders/{oid}"
        debug_saved = False
        for attempt in range(max(1, int(max_attempts))):
            if cancel_check is not None and cancel_check():
                return None
            try:
                r = requests.get(path, headers=self._headers(), timeout=8)
                if r.status_code != 200:
                    time.sleep(0.4)
                    continue
                j = r.json() or {}
                ec, em = _etoro_business_error_from_body(j)
                if ec is not None and ec != 0:
                    em_s = str(em or "").strip()
                    # 619/764 غالباً قيود رافعة أو مبلغ — طبّق حدود الجلسة كما في place_order
                    if ec in (619, 764):
                        max_l, min_l = _etoro_parse_leverage_bounds_from_error(em_s)
                        if max_l is not None or min_l is not None:
                            self._persist_etoro_leverage_limits(max_l, min_l)
                    human = _etoro_human_limit_message(ec=ec, em=em_s)
                    self._last_order_lookup_error_msg = (
                        f"[eToro] الطلب لم يُنشئ مركزاً على المنصة (orderID={oid}, errorCode={ec}). {human}\n"
                        "لا يوجد مركز للإغلاق على eToro — احذف الصف من جدول المراكز إن كان محلياً فقط، "
                        "ثم من «الرافعة» في اللوحة اضبطها لتطابق حد حسابك (مثلاً 2x) وأعد فتح الصفقة."
                    )
                    log.warning("%s — %s", self._last_order_lookup_error_msg.replace("\n", " "), em_s[:400])
                    return None
                # استخراج موحّد من أي بنية قد ترجعها واجهة orders
                pid = _etoro_orders_response_to_position_id(j, oid)
                if pid is not None and int(pid) > 0:
                    return int(pid)
                # حفظ الاستجابة للتصحيح مرة واحدة عند الفشل + تسجيل المفاتيح لمعرفة البنية
                if not debug_saved and attempt >= 2:
                    try:
                        with open(_etoro_orders_debug_path(), "w", encoding="utf-8") as f:
                            json.dump(
                                {"orderId": oid, "attempt": attempt, "response": j},
                                f,
                                indent=2,
                                ensure_ascii=False,
                            )
                        debug_saved = True
                        top_keys = list(j.keys())[:20] if isinstance(j, dict) else []
                        log.info(
                            "[eToro] لم يُستخرج positionID من استجابة واجهة الطلبات (بنية غير متوقعة). "
                            "مفاتيح الاستجابة: %s — محفوظة في: %s",
                            top_keys,
                            _etoro_orders_debug_path(),
                        )
                    except OSError:
                        pass
                time.sleep(0.45)
            except Exception as e:
                log.debug("_etoro_resolve_position_id_from_order: %s", e)
                time.sleep(0.4)
            if cancel_check is not None and cancel_check():
                return None
        return None

    def place_order(
        self,
        symbol: str,
        side: str,
        amount_usd: float,
        leverage: int = 1,
        stop_loss_rate: float | None = None,
        take_profit_rate: float | None = None,
        _allow_leverage_retry: bool = True,
    ) -> tuple[bool, str]:
        """
        فتح مركز سوق بالمبلغ (USD).
        عند النجاح: (True, "", position_id) أو (True, "", None)؛ عند الفشل: (False, msg).
        """
        iid = self.get_instrument_id(symbol)
        if iid is None:
            return (
                False,
                f"لم يتم العثور على الأداة لـ {symbol} في eToro. "
                "قائمة أدوات eToro أضيق من Binance — غيّر الرمز في اللوحة إلى أداة متاحة في التطبيق.",
            )
        if amount_usd <= 0:
            return False, "المبلغ يجب أن يكون أكبر من صفر."
        path = f"{BASE_URL}/api/v1/trading/execution/{self._exec_prefix()}/market-open-orders/by-amount"
        # لا نفرض حد ثابت. نستخدم حدوداً مكتشفة من أخطاء API (سقف وأرضية منفصلان).
        lev = max(1, min(leverage or 1, 100))
        if self._user_min_leverage is not None:
            lev = max(lev, int(self._user_min_leverage))
        if self._user_max_leverage is not None:
            lev = min(lev, int(self._user_max_leverage))
        payload = {
            "InstrumentID": iid,
            "IsBuy": side.upper() == "BUY",
            "Leverage": lev,
            "Amount": round(amount_usd, 2),
        }
        if stop_loss_rate is not None and stop_loss_rate > 0:
            payload["StopLossRate"] = stop_loss_rate
            payload["IsNoStopLoss"] = False
        else:
            payload["IsNoStopLoss"] = True
        if take_profit_rate is not None and take_profit_rate > 0:
            payload["TakeProfitRate"] = take_profit_rate
            payload["IsNoTakeProfit"] = False
        else:
            payload["IsNoTakeProfit"] = True
        try:
            r = requests.post(path, headers=self._headers(), json=payload, timeout=30)
            if r.status_code != 200:
                msg = r.text
                try:
                    j = r.json()
                    err_msg = (j.get("errorMessage") or j.get("error_message") or j.get("message") or j.get("error") or msg).strip()
                    err_code = j.get("errorCode") or j.get("error_code") or ""
                    if r.status_code == 403 and ("permission" in (err_code + err_msg).lower() or "InsufficientPermissions" in err_code):
                        return False, (
                            f"[eToro] 403: {err_code} — {err_msg}. "
                            "أضف صلاحيات Read و Trade للمفتاح في eToro: الإعدادات > Trading > API Key Management."
                        )
                    msg = err_msg or msg
                    try:
                        ec_http = int(err_code) if str(err_code).strip() else None
                    except (TypeError, ValueError):
                        ec_http = None
                    msg = _etoro_human_limit_message(
                        ec=ec_http,
                        em=str(msg or ""),
                        requested_leverage=lev,
                        requested_amount=float(amount_usd),
                    )
                except Exception:
                    pass
                return False, f"[eToro] {r.status_code}: {msg}"
            try:
                j = r.json() or {}
            except Exception:
                j = {}
            ec, em = _etoro_business_error_from_body(j)
            if ec is not None and ec != 0:
                if _allow_leverage_retry and ec in (619, 764):
                    max_l, min_l = _etoro_parse_leverage_bounds_from_error(em)
                    adjusted = int(lev)
                    if max_l is not None or min_l is not None:
                        self._persist_etoro_leverage_limits(max_l, min_l)
                    if max_l is not None:
                        adjusted = min(adjusted, int(max_l))
                    if min_l is not None:
                        adjusted = max(adjusted, int(min_l))
                    adjusted = max(1, min(adjusted, 100))
                    if adjusted != int(lev):
                        log.info(
                            "[eToro] إعادة محاولة فتح المركز بعد خطأ رافعة ec=%s: leverage %s -> %s (max=%s min=%s)",
                            ec,
                            lev,
                            adjusted,
                            max_l,
                            min_l,
                        )
                        return self.place_order(
                            symbol,
                            side,
                            amount_usd,
                            leverage=adjusted,
                            stop_loss_rate=stop_loss_rate,
                            take_profit_rate=take_profit_rate,
                            _allow_leverage_retry=False,
                        )
                return False, _etoro_human_limit_message(
                    ec=ec,
                    em=str(em or ""),
                    requested_leverage=lev,
                    requested_amount=float(amount_usd),
                )
            # معرّف المركز: وثائق eToro تُرجع orderForOpen.orderID ثم GET .../orders/{id} → positions[].positionID
            position_id = None
            order_id = None
            try:
                ofp = j.get("orderForOpen") or j.get("OrderForOpen") or {}
                if isinstance(ofp, dict):
                    order_id = ofp.get("orderID") or ofp.get("OrderID")
                if order_id is None:
                    order_id = j.get("orderID") or j.get("OrderID")
                for key in ("positionId", "PositionId", "position_id", "PositionID"):
                    if key in j and j[key] is not None:
                        try:
                            position_id = int(j[key])
                            if position_id > 0:
                                break
                        except (TypeError, ValueError):
                            pass
                if position_id is None and isinstance(j, dict):
                    position_id = etoro_deep_find_position_id(j)
                # لا نستدعي _etoro_resolve_position_id_from_order هنا — يجمّد زر الشراء (خيط الأمر ينتظر ثوانٍ).
                # يُستدعى لاحقاً من خيط خلفي بعد إعادة تفعيل الأزرار.
            except Exception:
                pass
            try:
                oid_out = int(order_id) if order_id is not None else None
            except (TypeError, ValueError):
                oid_out = None
            # لا نتحقق هنا من orders/{id} حتى لا نكسر الشراء في حالات تأخر/تذبذب API.
            # حل positionID يتم لاحقاً في الخلفية من خيط مستقل عند الحاجة.
            return (True, "", position_id, oid_out)
        except Exception as e:
            log.exception("eToro place_order failed")
            return (False, str(e))

    def close_position(self, position_id: int | str, instrument_id: int | None = None) -> tuple[bool, str]:
        """إغلاق مركز بالكامل عبر positionId (int أو str). تراجع أسي عند 429."""
        try:
            s = str(position_id).strip()
            if not s:
                log.warning("[eToro بيع] close_position: معرّف مركز فارغ")
                return False, "معرّف المركز غير صالح."
            pid = int(float(s))
        except (TypeError, ValueError):
            log.warning("[eToro بيع] close_position: معرّف غير رقمي: %r", position_id)
            return False, "معرّف المركز غير صالح."
        if pid <= 0:
            log.warning("[eToro بيع] close_position: معرّف غير موجب: %s", pid)
            return False, "معرّف المركز غير صالح."
        path = f"{BASE_URL}/api/v1/trading/execution/{self._exec_prefix()}/market-close-orders/positions/{pid}"
        try:
            log.info("[eToro بيع] طلب إغلاق positionID=%s", pid)
            # eToro تختلف في صيغة جسم close-order بين الحسابات/الإصدارات.
            # نجرب أكثر من صيغة تلقائياً قبل الفشل.
            # عندما يكون instrument_id معروفاً: كثيراً ما ترفض المنصة الصيغ بدون InstrumentId
            # (400 «instrument id does not exist») ثم تقبل نفس المسار مع InstrumentId — نبدأ بالصيغ المؤهّلة.
            base_variants = [
                # نتجنب no-body/empty-json لأنها غالباً تعطي 415/429 وتؤخر الإغلاق بدون فائدة.
                ("units-null-camel", {"UnitsToDeduct": None}),
                ("units-zero-camel", {"UnitsToDeduct": 0}),
                ("units-null-lower", {"unitsToDeduct": None}),
                ("units-zero-lower", {"unitsToDeduct": 0}),
            ]
            iid = None
            if instrument_id is not None and int(instrument_id) > 0:
                iid = int(instrument_id)
            if iid is not None:
                instrument_first = [
                    ("units-null+instrument-camel", {"UnitsToDeduct": None, "InstrumentId": iid}),
                    ("units-zero+instrument-camel", {"UnitsToDeduct": 0, "InstrumentId": iid}),
                    ("units-null+instrument-upper", {"UnitsToDeduct": None, "InstrumentID": iid}),
                    ("units-zero+instrument-upper", {"UnitsToDeduct": 0, "InstrumentID": iid}),
                ]
                payload_variants = instrument_first + base_variants
            else:
                payload_variants = list(base_variants)
            last_err = ""
            for tag, body in payload_variants:
                r = requests.post(
                    path,
                    headers=self._headers(),
                    json=body,
                    timeout=30,
                )
                if r.status_code == 429:
                    log.warning(
                        "eToro 429 (%-12s): تجاوز مؤقت — الانتقال لصيغة payload التالية",
                        f"close-position:{tag}",
                    )
                    time.sleep(0.8)
                    continue
                resp, err = _etoro_check_response(r, f"close-position:{tag}")
                if not err and getattr(r, "status_code", None) == 200:
                    log.info("[eToro بيع] نجح إغلاق positionID=%s (payload=%s)", pid, tag)
                    return True, ""
                last_err = err or (r.text or f"HTTP {r.status_code}")
                log.warning(
                    "[eToro بيع] فشل إغلاق positionID=%s (payload=%s) — HTTP=%s — %s",
                    pid,
                    tag,
                    getattr(r, "status_code", "?"),
                    (last_err or "")[:400],
                )
                if r.status_code in (400, 415):
                    log.info(
                        "[eToro close-position] %s جسم الاستجابة (payload=%s): %s",
                        r.status_code,
                        tag,
                        (r.text or "")[:500],
                    )
                    continue
                # في غير 400 لا فائدة من تكرار نفس الطلب بصيغ أخرى غالباً
                return False, last_err
            # بعض حسابات eToro لا تقبل إغلاق positions/{id} وتطلب إغلاقاً بمسار/جسم يعتمد InstrumentId.
            if instrument_id is not None and int(instrument_id) > 0:
                iid = int(instrument_id)
                alt_path = f"{BASE_URL}/api/v1/trading/execution/{self._exec_prefix()}/market-close-orders/by-instrument"
                alt_variants = [
                    ("by-instrument-camel", {"InstrumentId": iid, "UnitsToDeduct": None}),
                    ("by-instrument-upper", {"InstrumentID": iid, "UnitsToDeduct": None}),
                    ("by-instrument-lower", {"instrumentId": iid, "unitsToDeduct": None}),
                    ("by-instrument-zero", {"InstrumentId": iid, "UnitsToDeduct": 0}),
                ]
                for tag, body in alt_variants:
                    r2 = requests.post(
                        alt_path,
                        headers=self._headers(),
                        json=body,
                        timeout=30,
                    )
                    if r2.status_code == 429:
                        log.warning(
                            "eToro 429 (%-12s): تجاوز مؤقت — الانتقال لصيغة fallback التالية",
                            f"close-position:{tag}",
                        )
                        time.sleep(0.8)
                        continue
                    resp2, err2 = _etoro_check_response(r2, f"close-position:{tag}")
                    if not err2 and getattr(r2, "status_code", None) == 200:
                        log.info(
                            "[eToro بيع] نجح الإغلاق fallback by-instrument positionID=%s instrumentID=%s (payload=%s)",
                            pid,
                            iid,
                            tag,
                        )
                        return True, ""
                    last_err = err2 or (r2.text or f"HTTP {r2.status_code}")
                    log.warning(
                        "[eToro بيع] فشل fallback by-instrument positionID=%s instrumentID=%s (payload=%s) — HTTP=%s — %s",
                        pid,
                        iid,
                        tag,
                        getattr(r2, "status_code", "?"),
                        (last_err or "")[:400],
                    )
                    if r2.status_code in (400, 415):
                        continue
                    return False, last_err

            return False, last_err or "فشل إغلاق المركز."
        except Exception as e:
            log.warning("[eToro بيع] استثناء عند إغلاق positionID=%s: %s", pid, e)
            return False, str(e)

    def _extract_positions_from_client_portfolio(self, data: dict) -> list[dict]:
        """clientPortfolio.positions + mirrors + ordersForOpen + orders + entryOrders + stockOrders (إن وُجد معرّف مركز)."""
        out: list[dict] = []
        cp = data.get("clientPortfolio") or data.get("ClientPortfolio")
        if not isinstance(cp, dict):
            return out
        main_pos = cp.get("positions") or cp.get("Positions")
        if isinstance(main_pos, list):
            out.extend(main_pos)
        # أسماء إضافية قد تُستخدم في بيئات مختلفة (Demo/API إصدارات)
        for alt_key in (
            "openPositions", "OpenPositions",
            "activePositions", "ActivePositions",
            "cfdPositions", "CfdPositions",
            "cryptoPositions", "CryptoPositions",
            "openCfdPositions", "OpenCfdPositions",
        ):
            arr = cp.get(alt_key)
            if isinstance(arr, list) and arr:
                out.extend(arr)
        for m in cp.get("mirrors") or cp.get("Mirrors") or []:
            if isinstance(m, dict):
                mp = m.get("positions") or m.get("Positions")
                if isinstance(mp, list):
                    out.extend(mp)
        # طلبات مفتوحة قد تحتوي معرّف مركز
        for list_key in (
            "ordersForOpen", "OrdersForOpen",
            "orders", "Orders", "entryOrders", "EntryOrders", "stockOrders", "StockOrders",
        ):
            arr = cp.get(list_key)
            if not isinstance(arr, list):
                continue
            for o in arr:
                if not isinstance(o, dict):
                    continue
                if etoro_extract_position_id(o) or etoro_deep_find_position_id(o):
                    out.append(etoro_flatten_position_dict(o))
        return out

    def _get_positions_from_portfolio_endpoint(self) -> list[dict]:
        """GET /trading/info/{demo|real}/portfolio. تراجع أسي عند 429."""
        path = f"{BASE_URL}/api/v1/trading/info/{self._exec_prefix()}/portfolio"
        try:
            r = self._request_with_429_backoff(
                lambda: requests.get(path, headers=self._headers(), timeout=15),
                "portfolio",
            )
            resp, err = _etoro_check_response(r, "portfolio")
            if err:
                if r.status_code in (401, 403):
                    return []
                raise requests.HTTPError(err)
            r.raise_for_status()
            data = r.json() if isinstance(r.json(), dict) else {}
            return self._extract_positions_from_client_portfolio(data)
        except Exception as e:
            log.debug("eToro portfolio endpoint: %s", e)
            return []

    def get_positions(self) -> list[dict]:
        """مراكز مفتوحة: أولاً /pnl ثم — إن فارغ — /portfolio. دعم التخزين المؤقت ETag/304."""
        path = f"{BASE_URL}/api/v1/trading/info/{self._exec_prefix()}/pnl"
        try:

            def _do_pnl_request():
                h = self._headers()
                # لا نرسل If-None-Match عندما آخر نتيجة كانت فارغة — وإلا قد يعيد الخادم 304
                # ونبقى عالقين على [] رغم فتح مركز جديد.
                cached_for_etag = getattr(self, "_pnl_cached_positions", None)
                if (
                    getattr(self, "_pnl_etag", None)
                    and isinstance(cached_for_etag, list)
                    and len(cached_for_etag) > 0
                ):
                    h["If-None-Match"] = self._pnl_etag
                return requests.get(path, headers=h, timeout=15)

            r = self._request_with_429_backoff(_do_pnl_request, "pnl")
            if r.status_code == 304:
                cached = getattr(self, "_pnl_cached_positions", None)
                if cached is not None and len(cached) > 0:
                    log.debug("eToro pnl: 304 Not Modified — استخدام التخزين المؤقت")
                    return cached
                self._pnl_etag = None
                log.debug("eToro pnl: 304 غير متوقع مع مخزن فارغ — إعادة جلب بدون ETag")
                r = self._request_with_429_backoff(
                    lambda: requests.get(path, headers=self._headers(), timeout=15),
                    "pnl-refresh",
                )
            resp, err = _etoro_check_response(r, "pnl")
            if err:
                if r.status_code in (401, 403):
                    return []
                raise requests.HTTPError(err)
            # 304 بعد إعادة المحاولة: جسم فارغ — لا نُحدّث ETag من جسم؛ نُبقي الطلب التالي بدون فرض
            if r.status_code == 304:
                cached = getattr(self, "_pnl_cached_positions", None)
                if cached is not None and len(cached) > 0:
                    return cached
                return []
            r.raise_for_status()
            etag = r.headers.get("ETag") or r.headers.get("etag")
            if etag:
                self._pnl_etag = etag.strip('"')
            try:
                raw = r.json()
            except Exception:
                raw = {}
            if isinstance(raw, list):
                if raw:
                    self._pnl_cached_positions = raw
                    return raw
                alt = self._get_positions_from_portfolio_endpoint()
                self._pnl_cached_positions = alt if alt else []
                return alt if alt else []
            data = raw if isinstance(raw, dict) else {}
            # المسار الرسمي من وثائق eToro API: PortfolioResponseWithPnl.clientPortfolio.positions
            cp = data.get("clientPortfolio") or data.get("ClientPortfolio")
            if isinstance(cp, dict):
                out = self._extract_positions_from_client_portfolio(data)
                if out:
                    self._pnl_cached_positions = out
                    return out
                alt = self._get_positions_from_portfolio_endpoint()
                if alt:
                    log.info(
                        "eToro get_positions: %s مركز(ات) من /portfolio (كان /pnl بدون مراكز)",
                        len(alt),
                    )
                    self._pnl_cached_positions = alt
                    return alt
                # لا نرجع [] فوراً — نجرب مفاتيح أخرى في الاستجابة (مثلاً demo قد يضع المراكز في مكان آخر)
            for key in (
                "positions",
                "Positions",
                "openPositions",
                "OpenPositions",
                "activePositions",
                "positionsList",
                "PositionsList",
                "items",
                "data",
                "result",
            ):
                lst = data.get(key)
                if isinstance(lst, list) and lst:
                    self._pnl_cached_positions = lst
                    return lst
            for outer in ("data", "result", "response", "body"):
                inner = data.get(outer)
                if not isinstance(inner, dict):
                    continue
                for k in ("positions", "openPositions", "items", "data"):
                    lst = inner.get(k)
                    if isinstance(lst, list) and lst:
                        self._pnl_cached_positions = lst
                        return lst

            def _deep_find_positions(o, depth: int = 0) -> list:
                if depth > 10 or o is None:
                    return []
                if isinstance(o, list) and len(o) > 0 and isinstance(o[0], dict):
                    keys = set()
                    for it in o[:5]:
                        if isinstance(it, dict):
                            keys.update(str(k).lower().replace("_", "") for k in it)
                    # مطابقة مفاتيح شائعة في استجابة eToro (بأي صيغة)
                    if keys & {"openrate", "openrate", "positionid", "position_id", "instrumentid", "openpositionid", "cocopositionid"}:
                        return o
                if isinstance(o, dict):
                    for v in o.values():
                        found = _deep_find_positions(v, depth + 1)
                        if found:
                            return found
                return []

            found = _deep_find_positions(data)
            if found:
                self._pnl_cached_positions = found
                return found
            # بحث نهائي: أي مصفوفة داخل الاستجابة تحتوي عناصراً فيها positionID/positionId
            anywhere = _etoro_find_positions_list_anywhere(data)
            if anywhere:
                self._pnl_cached_positions = anywhere
                return anywhere
            alt = self._get_positions_from_portfolio_endpoint()
            if alt:
                log.info(
                    "eToro get_positions: %s مركز(ات) من /portfolio (لم تُستخرج من /pnl)",
                    len(alt),
                )
                self._pnl_cached_positions = alt
                return alt
            # حفظ الاستجابة للتحقق عند استمرار المشكلة
            try:
                with open(_etoro_debug_response_path(), "w", encoding="utf-8") as f:
                    json.dump(raw if isinstance(raw, dict) else {"response": raw}, f, indent=2, ensure_ascii=False)
            except OSError:
                pass
            top_keys = list(data.keys())[:25] if isinstance(data, dict) else []
            cp = data.get("clientPortfolio") or data.get("ClientPortfolio") if isinstance(data, dict) else {}
            # أطوال القوائم = عدد العناصر (len) دائماً أعداد صحيحة، ليست أسعاراً
            list_lens: dict[str, int] = {}
            if isinstance(cp, dict):
                def _list_len(cp_d: dict, *names: str) -> int:
                    for name in names:
                        v = cp_d.get(name)
                        if isinstance(v, list):
                            return len(v)
                    return 0

                list_lens = {
                    "positions": _list_len(cp, "positions", "Positions"),
                    "orders": _list_len(cp, "orders", "Orders"),
                    "ordersForOpen": _list_len(cp, "ordersForOpen", "OrdersForOpen"),
                    "entryOrders": _list_len(cp, "entryOrders", "EntryOrders"),
                    "stockOrders": _list_len(cp, "stockOrders", "StockOrders"),
                    "mirrors": _list_len(cp, "mirrors", "Mirrors"),
                }
            now = time.time()
            interval = 180.0
            exec_env = self._exec_prefix()  # "demo" | "real" — يجب أن يطابق مكان فتح الصفقة في eToro
            credit_hint = ""
            if isinstance(cp, dict):
                try:
                    cr = cp.get("credit")
                    if cr is not None:
                        credit_hint = f" credit≈{float(cr):.2f} (المفاتيح صالحة لحساب على {exec_env})"
                except (TypeError, ValueError):
                    pass
            if now - float(getattr(EtoroClient, "_empty_positions_warn_ts", 0) or 0) >= interval:
                EtoroClient._empty_positions_warn_ts = now
                # بدون JSON مع علامات اقتباس بجانب نص عربي (RTL) قد يُنسخ/يُعرض المفتاح mirrors كـ mmirrors
                lens_order = (
                    "positions",
                    "orders",
                    "ordersForOpen",
                    "entryOrders",
                    "stockOrders",
                    "mirrors",
                )
                lens_txt = ",".join(f"{k}={list_lens.get(k, 0)}" for k in lens_order)
                # حالة شائعة (تجريبي↔حقيقي أو لا مراكز) — INFO وليس WARNING حتى لا يُعتبر خطأ في البرنامج
                log.info(
                    "eToro get_positions: لا مراكز في استجابة API (قوائم فارغة). "
                    "بيئة الطلب: /%s/pnl و/portfolio — إن فتحت الصفقة على الحساب الآخر (وهمي↔حقيقي) لن تظهر هنا. "
                    "أو لا يوجد مركز مفتوح فعلاً.%s مفاتيح الجذر: %s | lens_counts=%s | %s",
                    exec_env,
                    credit_hint,
                    top_keys,
                    lens_txt,
                    _etoro_debug_response_path(),
                )
            else:
                log.debug(
                    "eToro get_positions: فارغ (تكرار خلال %.0fs — تم خفض مستوى السجل)",
                    interval,
                )
            self._pnl_cached_positions = []
            return []
        except Exception as e:
            log.warning("eToro get positions failed: %s", e)
        return []


# توافق مع واجهة التطبيق: التطبيق يتوقع SpotClient/FuturesClient مع place_order(symbol, side, quantity)
# نعرض كـ SpotClient مع place_order يقتبل amount بدل quantity عند استدعاء من لوحة eToro
class SpotClient(EtoroClient):
    """واجهة Spot متوافقة: place_order(symbol, side, quantity, price) — نستخدم quantity*price كمبلغ USD."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False, debug: bool = False):
        # في eToro: api_key = user_key، api_secret = api_key (المفتاح العام)
        super().__init__(user_key=api_key or "", api_key=api_secret or "", demo=testnet, debug=debug)

    def place_order(self, symbol: str, side: str, quantity: float, price: float = None):
        if price is None or price <= 0:
            price = self.get_last_price(symbol)
        amount_usd = quantity * (price or 0)
        if amount_usd <= 0:
            return False, (
                "لا يمكن حساب المبلغ: السعر أو الكمية غير صالحة، أو تعذّر جلب سعر الأداة من eToro لهذا الرمز."
            )
        return super().place_order(symbol, side, amount_usd, leverage=1)


class FuturesClient(EtoroClient):
    """واجهة Futures متوافقة: رافعة من set_leverage، place_order بالمبلغ."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False, debug: bool = False):
        super().__init__(user_key=api_key or "", api_key=api_secret or "", demo=testnet, debug=debug)
        self._leverage = 1

    def set_leverage(self, symbol: str, leverage: int) -> tuple[bool, str]:
        req = max(1, min(int(leverage or 1), 100))
        lev = req
        if getattr(self, "_user_min_leverage", None) is not None:
            lev = max(lev, int(self._user_min_leverage))
        if getattr(self, "_user_max_leverage", None) is not None:
            lev = min(lev, int(self._user_max_leverage))
        if lev != req:
            log.warning(
                "[eToro] الرافعة في اللوحة %sx تُقيّد إلى %sx (حد الحساب المحفوظ أو المكتشف من API).",
                req,
                lev,
            )
        self._leverage = lev
        return True, ""

    def place_order(self, symbol: str, side: str, quantity: float, price: float = None):
        if price is None or price <= 0:
            price = self.get_last_price(symbol)
        amount_usd = quantity * (price or 0)
        if amount_usd <= 0:
            return False, (
                "لا يمكن حساب المبلغ: السعر أو الكمية غير صالحة، أو تعذّر جلب سعر الأداة من eToro لهذا الرمز."
            )
        return super().place_order(
            symbol, side, amount_usd, leverage=self._leverage
        )

    def _pos_instrument_id(self, pos: dict) -> int | None:
        """استخراج instrumentId من مركز (كل صيغ المفتاح المحتملة من eToro)."""
        v = pos.get("instrumentID") or pos.get("instrumentId") or pos.get("InstrumentID") or pos.get("InstrumentId")
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
        for key in ("internalSymbolFull", "InternalSymbolFull", "symbol", "Symbol"):
            s = str(pos.get(key) or "").strip().upper()
            m = re.search(r"ETORO_(\d+)", s)
            if m:
                try:
                    i = int(m.group(1))
                    if i > 0:
                        return i
                except ValueError:
                    pass
        return None

    def _pos_position_id(self, pos: dict) -> int | None:
        """استخراج positionId — صريح ثم بحث عميق."""
        x = etoro_extract_position_id(pos)
        if x is not None:
            try:
                return int(x)
            except (TypeError, ValueError):
                pass
        x = etoro_deep_find_position_id(pos)
        if x is not None:
            try:
                return int(x)
            except (TypeError, ValueError):
                pass
        return None

    def _iter_positions_with_pid(self, raw: list) -> list[tuple[int, dict]]:
        """
        نفس استخراج positionId في إغلاق الكل — يُرجع [(pid, pos_flat), ...].
        يستخدمه close_all_positions.
        """
        out: list[tuple[int, dict]] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            p = etoro_flatten_position_dict(item)
            pid = (
                self._pos_position_id(p)
                or etoro_deep_find_position_id(item)
                or etoro_deep_find_position_id(p)
                or _etoro_guess_position_id(p)
            )
            if pid is None:
                continue
            try:
                pid_i = int(pid)
            except (TypeError, ValueError):
                continue
            if pid_i <= 0:
                continue
            out.append((pid_i, p))
        return out

    def _find_position_id_for_symbol(self, symbol: str) -> int | None:
        """إرجاع positionId للمركز المطابق للرمز، أو None إن لم يُوجد."""
        orig = (symbol or "").strip()
        want_sym = orig.upper()
        if not want_sym:
            log.warning("[eToro] _find_position_id_for_symbol: الرمز فارغ")
            return None
        if want_sym.endswith("USD") and not want_sym.endswith("USDT"):
            want_sym = want_sym[:-3] + "USDT"
        elif not want_sym.endswith("USDT") and not want_sym.endswith("USD"):
            want_sym = want_sym + "USDT"
        log.debug("[eToro] بحث مركز للرمز: %s → موحّد: %s", orig, want_sym)
        iid = self.get_instrument_id(symbol)
        log.debug("[eToro] instrument_id لـ %s: %s", orig, iid)
        positions = self._iter_flat_positions()
        log.debug("[eToro] عدد مراكز مسطّحة للفحص: %s", len(positions))
        for idx, pos in enumerate(positions):
            pid = self._pos_position_id(pos)
            if pid is None:
                log.debug("[eToro] مركز[%s]: بدون position_id", idx)
                continue
            pos_iid = self._pos_instrument_id(pos)
            pos_sym = _normalize_position_symbol(pos)
            isf = str(pos.get("internalSymbolFull") or pos.get("symbol") or "").upper()
            log.debug(
                "[eToro] مركز[%s]: pid=%s iid=%s norm_sym=%s internalFull=%s",
                idx, pid, pos_iid, pos_sym, isf,
            )
            if pos_iid is not None and iid is not None and int(pos_iid) == int(iid):
                log.info("[eToro] مطابقة instrument_id: %s == %s → pid=%s", pos_iid, iid, pid)
                return int(pid)
            if pos_sym == want_sym:
                log.info("[eToro] مطابقة رمز: %s == %s → pid=%s", pos_sym, want_sym, pid)
                return int(pid)
            base = _symbol_to_etoro(want_sym)
            if base and (isf == base.upper() or isf.startswith(base.upper())):
                log.info("[eToro] مطابقة base %s في %s → pid=%s", base, isf, pid)
                return int(pid)
        log.warning("[eToro] لم يُعثر على مركز للرمز: %s", orig)
        return None

    def has_position_for_symbol(self, symbol: str) -> bool:
        return self._find_position_id_for_symbol(symbol) is not None

    def enumerate_positions_for_symbol(self, symbol: str) -> list[tuple[int, float, float]]:
        """
        كل المراكز المفتوحة على المنصة لهذا الرمز/الأداة — نفس معايير مطابقة الرمز
        المستخدمة في close_position(symbol) وإغلاق الكل، مع استخراج أوسع لـ positionId.
        يُستخدم لزر الإغلاق لكل صف (مبدأ Binance: مركز واحد = إغلاق بالرمز).
        """
        want_sym = (symbol or "").strip().upper()
        if want_sym.endswith("USD") and not want_sym.endswith("USDT"):
            want_sym = want_sym[:-3] + "USDT"
        elif want_sym and not want_sym.endswith("USDT") and not want_sym.endswith("USD"):
            want_sym = want_sym + "USDT"
        iid_target = self.get_instrument_id(symbol)
        base = _symbol_to_etoro(want_sym)
        raw = self.get_positions() or []
        if not raw:
            import time as _t

            _t.sleep(0.45)
            raw = self.get_positions() or []
        out: list[tuple[int, float, float]] = []
        seen: set[int] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            p = etoro_flatten_position_dict(item)
            pid = self._pos_position_id(p)
            if pid is None:
                pid = etoro_deep_find_position_id(item)
            if pid is None:
                pid = _etoro_guess_position_id(p)
            if pid is None:
                continue
            try:
                pid_i = int(pid)
            except (TypeError, ValueError):
                continue
            if pid_i <= 0 or pid_i in seen:
                continue
            pos_iid = self._pos_instrument_id(p)
            psym = _normalize_position_symbol(p)
            isf = str(
                p.get("internalSymbolFull")
                or p.get("InternalSymbolFull")
                or p.get("symbol")
                or p.get("Symbol")
                or ""
            ).upper()
            belongs = False
            if iid_target is not None and pos_iid is not None and int(pos_iid) == int(iid_target):
                belongs = True
            elif psym and psym == want_sym:
                belongs = True
            elif base:
                b = base.upper()
                if isf == b or isf.startswith(b) or (len(b) >= 2 and b in isf):
                    belongs = True
            if not belongs:
                continue
            seen.add(pid_i)
            r = etoro_row_from_pnl_item(item)
            if r and r.get("position_id"):
                pe, pq = float(r["entry_price"]), float(r["quantity"])
            else:
                pe, pq = _etoro_pos_entry_units(p)
                if pe <= 0 or pq <= 0:
                    inv = float(
                        p.get("initialInvestmentInDollars")
                        or p.get("amount")
                        or p.get("Amount")
                        or p.get("investedAmount")
                        or 0
                    )
                    if inv > 0 and pe > 0:
                        pq = inv / pe
                    elif inv > 0:
                        pe, pq = inv, 1.0
                    else:
                        pe, pq = 0.0, 0.0
            out.append((pid_i, pe, pq))
        return out

    def _instrument_id_for_open_pid(self, want_pid: int) -> int | None:
        """instrument_id لمركز مفتوح من قائمة المنصة — يُمرَّر لجسم إغلاق eToro عند الحاجة."""
        try:
            wp = int(want_pid)
        except (TypeError, ValueError):
            return None
        if wp <= 0:
            return None
        for item in self.get_positions() or []:
            if not isinstance(item, dict):
                continue
            p = etoro_flatten_position_dict(item)
            pid = self._pos_position_id(p)
            if pid is None:
                pid = etoro_deep_find_position_id(item)
            try:
                if int(pid) != wp:
                    continue
            except (TypeError, ValueError):
                continue
            return self._pos_instrument_id(p)
        return None

    def resolve_position_id_to_close(
        self,
        symbol: str,
        entry: float,
        quantity: float,
        sibling_entries: list[tuple[float, float]] | None = None,
    ) -> int | None:
        """
        اختيار positionId للصف — مركز واحد = ذلك الـ ID؛ عدة مراكز = أفضل مطابقة دخول/كمية.
        """
        try:
            entry = float(entry)
            quantity = float(quantity)
        except (TypeError, ValueError):
            return None
        rows = self.enumerate_positions_for_symbol(symbol)
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0][0]
        notional_ui = abs(entry * quantity) if entry > 0 and quantity > 0 else 0.0

        def _score(pe: float, pq: float) -> float:
            de = abs(entry - pe) / max(entry, pe, 1e-6)
            dq = abs(quantity - pq) / max(abs(quantity), pq, 1e-12)
            np_ = abs(pe * pq)
            dn = abs(notional_ui - np_) / max(notional_ui, np_, 1e-6) if notional_ui > 0 else 0.0
            return de * 2.0 + dq * 2.0 + dn * 0.35

        sibs = list(sibling_entries) if sibling_entries else [(entry, quantity)]
        sibs = [(float(a), float(b)) for a, b in sibs if float(a) > 0 and float(b) > 0]
        if not sibs:
            sibs = [(entry, quantity)]
        etol = max(1.0, abs(entry) * 0.01, 5.0)
        qtol = max(1e-8, abs(quantity) * 0.05, abs(quantity) * 1e-4)
        ui_sorted = sorted(sibs, key=lambda x: (x[0], x[1]))
        api_sorted = sorted(rows, key=lambda x: (x[1], x[2]))
        if len(ui_sorted) == len(api_sorted) and len(ui_sorted) >= 1:
            target_i = None
            for i, (ue, uq) in enumerate(ui_sorted):
                if abs(ue - entry) <= etol and abs(uq - quantity) <= qtol:
                    target_i = i
                    break
            if target_i is None:
                target_i = min(
                    range(len(ui_sorted)),
                    key=lambda ii: abs(ui_sorted[ii][0] - entry) / max(entry, 1e-6)
                    + abs(ui_sorted[ii][1] - quantity) / max(abs(quantity), ui_sorted[ii][1], 1e-12),
                )
            if 0 <= target_i < len(api_sorted):
                return api_sorted[target_i][0]
        best_pid, best_sc = rows[0][0], 1e18
        for pid, pe, pq in rows:
            sc = _score(pe, pq)
            if sc < best_sc:
                best_sc = sc
                best_pid = pid
        return best_pid

    def _iter_flat_positions(self) -> list[dict]:
        raw = self.get_positions() or []
        if not raw:
            import time as _t

            _t.sleep(0.45)
            raw = self.get_positions() or []
        out = []
        for item in raw:
            if isinstance(item, dict):
                out.append(etoro_flatten_position_dict(item))
        return out

    def find_position_id_for_open_row(
        self,
        symbol: str,
        entry: float,
        quantity: float,
        sibling_entries: list[tuple[float, float]] | None = None,
    ) -> int | None:
        """
        مطابقة صف الواجهة مع مركز المنصة (نفس استخراج الحقول مثل المزامنة).
        """
        import time as _time

        want = (symbol or "").strip().upper()
        if want.endswith("USD") and not want.endswith("USDT"):
            want = want[:-3] + "USDT"
        elif want and not want.endswith("USDT") and not want.endswith("USD"):
            want = want + "USDT"
        try:
            entry = float(entry)
            quantity = float(quantity)
        except (TypeError, ValueError):
            return None
        if entry <= 0 or quantity <= 0:
            # بيانات صف الواجهة ناقصة — جرّب مطابقة الرمز ثم مركزاً واحداً فقط على الحساب
            pid_sym = self._find_position_id_for_symbol(symbol)
            if pid_sym is not None:
                return int(pid_sym)
            raw0 = self.get_positions() or []
            one0 = self._iter_positions_with_pid(raw0)
            if len(one0) == 1:
                log.warning(
                    "[eToro] find_position_id_for_open_row: دخول/كمية صفر — استخدام مركز واحد فقط pid=%s",
                    int(one0[0][0]),
                )
                return int(one0[0][0])
            return None
        base = _symbol_to_etoro(want)
        iid_target = self.get_instrument_id(symbol)
        notional_ui = abs(entry * quantity)

        raw = self.get_positions() or []
        if not raw:
            _time.sleep(0.45)
            raw = self.get_positions() or []

        def _belongs_same_instrument(p: dict) -> bool:
            psym = _normalize_position_symbol(p)
            piid = self._pos_instrument_id(p)
            isf = str(
                p.get("internalSymbolFull") or p.get("InternalSymbolFull")
                or p.get("symbol") or p.get("Symbol") or ""
            ).upper()
            if psym and psym == want:
                return True
            if iid_target is not None and piid is not None and int(piid) == int(iid_target):
                return True
            if base:
                b = base.upper()
                if isf == b or isf.startswith(b) or (len(b) >= 2 and b in isf):
                    return True
            return False

        def _score(pe: float, pq: float) -> float:
            de = abs(entry - pe) / max(entry, pe, 1e-6)
            dq = abs(quantity - pq) / max(abs(quantity), pq, 1e-12)
            np_ = abs(pe * pq)
            dn = abs(notional_ui - np_) / max(notional_ui, np_, 1e-6)
            return de * 2.0 + dq * 2.0 + dn * 0.35

        def _gather_api_rows() -> list[tuple[int, float, float]]:
            rows: list[tuple[int, float, float]] = []
            seen: set[int] = set()
            for item in raw:
                if not isinstance(item, dict):
                    continue
                p = etoro_flatten_position_dict(item)
                if not _belongs_same_instrument(p):
                    continue
                r = etoro_row_from_pnl_item(item)
                if r and r.get("position_id"):
                    pid = int(r["position_id"])
                    if pid not in seen:
                        seen.add(pid)
                        rows.append((pid, float(r["entry_price"]), float(r["quantity"])))
                    continue
                pid = (
                    etoro_deep_find_position_id(item)
                    or etoro_extract_position_id(p)
                    or _etoro_guess_position_id(p)
                )
                if not pid or int(pid) in seen:
                    continue
                pid = int(pid)
                pe, pq = _etoro_pos_entry_units(p)
                if pe <= 0 or pq <= 0:
                    inv = float(
                        p.get("initialInvestmentInDollars") or p.get("amount") or p.get("Amount")
                        or p.get("investedAmount") or p.get("invested") or 0
                    )
                    if inv > 0 and entry > 0:
                        pq = inv / entry
                        pe = entry
                    elif inv > 0 and quantity > 0:
                        pe = inv / quantity
                        pq = quantity
                    else:
                        pe, pq = entry, quantity
                seen.add(pid)
                rows.append((pid, float(pe), float(pq)))
            return rows

        api_rows = _gather_api_rows()
        if not api_rows:
            one_fb = self._iter_positions_with_pid(raw)
            if len(one_fb) == 1:
                pid0 = int(one_fb[0][0])
                if _etoro_should_log_symbol_single_fb(want, pid0, "find_row"):
                    log.warning(
                        "[eToro] find_position_id_for_open_row: لا مطابقة لـ %s — مركز واحد pid=%s",
                        want,
                        pid0,
                    )
                else:
                    log.debug(
                        "[eToro] find_position_id_for_open_row: لا مطابقة لـ %s — مركز واحد pid=%s (متكرر)",
                        want,
                        pid0,
                    )
                return pid0
            return None
        if len(api_rows) > 1:
            filt = [
                x for x in api_rows
                if abs(x[1] * x[2] - notional_ui) / max(notional_ui, abs(x[1] * x[2]), 1.0) < 4.0
            ]
            if len(filt) >= 1:
                api_rows = filt
        if len(api_rows) == 1:
            return api_rows[0][0]

        sibs = list(sibling_entries) if sibling_entries else [(entry, quantity)]
        sibs = [(float(a), float(b)) for a, b in sibs if float(a) > 0 and float(b) > 0]
        if not sibs:
            sibs = [(entry, quantity)]

        etol = max(1.0, abs(entry) * 5e-4, abs(entry) * 0.002)
        qtol = max(1e-10, abs(quantity) * 5e-4, abs(quantity) * 0.02)

        ui_sorted = sorted(sibs, key=lambda x: (x[0], x[1]))
        api_sorted = sorted(api_rows, key=lambda x: (x[1], x[2]))

        if len(ui_sorted) == len(api_sorted) and len(ui_sorted) >= 1:
            target_i = None
            for i, (ue, uq) in enumerate(ui_sorted):
                if abs(ue - entry) <= etol and abs(uq - quantity) <= qtol:
                    target_i = i
                    break
            if target_i is None:
                target_i = min(
                    range(len(ui_sorted)),
                    key=lambda ii: abs(ui_sorted[ii][0] - entry) / max(entry, 1e-6)
                    + abs(ui_sorted[ii][1] - quantity) / max(abs(quantity), ui_sorted[ii][1], 1e-12),
                )
            if 0 <= target_i < len(api_sorted):
                return api_sorted[target_i][0]

        best_pid: int | None = None
        best_sc = 1e18
        for pid, pe, pq in api_rows:
            sc = _score(pe, pq)
            if sc < best_sc:
                best_sc = sc
                best_pid = pid
        return best_pid

    def close_position_by_position_id(
        self, position_id: int | str, instrument_id: int | None = None
    ) -> tuple[bool, str]:
        """إغلاق مركز محدد بمعرّف eToro — int أو str."""
        try:
            pid = int(float(str(position_id).strip()))
        except (TypeError, ValueError):
            log.error("[eToro] close_position_by_position_id: معرّف غير صالح: %r", position_id)
            return False, "معرّف المركز غير صالح."
        if pid <= 0:
            log.error("[eToro] close_position_by_position_id: معرّف <= 0: %s", pid)
            return False, "معرّف المركز غير صالح."
        log.info("[eToro] إغلاق مباشر بـ position_id=%s", pid)
        ok, msg = EtoroClient.close_position(self, pid, instrument_id=instrument_id)
        if ok:
            log.info("[eToro] تم إغلاق المركز %s بنجاح", pid)
        else:
            log.error("[eToro] فشل إغلاق المركز %s: %s", pid, (msg or "")[:500])
        return ok, msg

    def close_position(self, symbol_or_id: str | int) -> tuple[bool, str]:
        """
        إغلاق بالرمز (مثل BTCUSDT) أو بمعرّف المركز مباشرة (int أو نص رقمي).
        """
        if isinstance(symbol_or_id, int):
            pi = int(symbol_or_id)
            log.debug("[eToro close_position] position_id (int)=%s", pi)
            iid = self._instrument_id_for_open_pid(pi)
            return EtoroClient.close_position(self, pi, instrument_id=iid)
        if isinstance(symbol_or_id, float):
            pi = int(symbol_or_id)
            if pi > 0:
                iid = self._instrument_id_for_open_pid(pi)
                return EtoroClient.close_position(self, pi, instrument_id=iid)
        s = str(symbol_or_id).strip()
        if not s:
            return False, "رمز أو معرّف غير صالح."
        try:
            pid = int(float(s))
            if pid > 0:
                log.debug("[eToro close_position] position_id من نص رقمي=%s", pid)
                iid_one = self._instrument_id_for_open_pid(pid)
                return EtoroClient.close_position(self, pid, instrument_id=iid_one)
        except (ValueError, TypeError, OverflowError):
            pass
        # eToro: قد يوجد أكثر من مركز (positionId) لنفس الأداة — إغلاق بالرمز يجب أن يغلق الكل،
        # وإلا يُغلق الأول فقط وتظهر الواجهة «تم البيع» ثم تعيد المزامنة المراكز المتبقية.
        pairs = self.enumerate_positions_for_symbol(s)
        if not pairs:
            # أحياناً لا يطابق internalSymbolFull الرمز الظاهر (NIGHTUSDT) بينما يوجد مركز واحد فقط
            raw_fb = self.get_positions() or []
            one_fb = self._iter_positions_with_pid(raw_fb)
            if len(one_fb) == 1:
                pid_fb = int(one_fb[0][0])
                s_norm = (s or "").strip().upper()
                if s_norm.endswith("USD") and not s_norm.endswith("USDT"):
                    s_norm = s_norm[:-3] + "USDT"
                elif s_norm and not s_norm.endswith("USDT") and not s_norm.endswith("USD"):
                    s_norm = s_norm + "USDT"
                if _etoro_should_log_symbol_single_fb(s_norm, pid_fb, "close"):
                    log.warning(
                        "[eToro close_position] لا مطابقة للرمز %s — إغلاق مركز واحد مفتوح على الحساب (pid=%s)",
                        s,
                        pid_fb,
                    )
                else:
                    log.debug(
                        "[eToro close_position] لا مطابقة للرمز %s — إغلاق مركز واحد (pid=%s) (متكرر)",
                        s,
                        pid_fb,
                    )
                iid_fb = self._instrument_id_for_open_pid(pid_fb)
                return EtoroClient.close_position(self, pid_fb, instrument_id=iid_fb)
            log.warning(
                "[eToro close_position] لا مركز للرمز/المعرّف: %s",
                s,
            )
            return False, f"لا يوجد مركز مفتوح: {s}"
        if len(pairs) == 1:
            pid = int(pairs[0][0])
            iid = self._instrument_id_for_open_pid(pid)
            log.info("[eToro close_position] رمز %s → position_id=%s", s, pid)
            return EtoroClient.close_position(self, pid, instrument_id=iid)
        log.info(
            "[eToro close_position] رمز %s — إغلاق %d مراكز متتالية (عدة مراكز لنفس الأداة)",
            s,
            len(pairs),
        )
        errs: list[str] = []
        for idx, (pid, _, _) in enumerate(pairs):
            if idx > 0:
                time.sleep(0.4)
            iid = self._instrument_id_for_open_pid(int(pid))
            ok1, msg1 = EtoroClient.close_position(self, int(pid), instrument_id=iid)
            if not ok1:
                errs.append(f"{pid}: {msg1 or '?'}")
        return (
            len(errs) == 0,
            "; ".join(errs) if errs else f"[eToro] أُغلقت {len(pairs)} مراكز لـ {s}",
        )

    def close_all_positions(self) -> tuple[bool, str]:
        """إغلاق كل المراكز — نفس استخراج positionId المستخدم في إغلاق الصف الواحد (_iter_positions_with_pid)."""
        all_pos = self.get_positions()
        if not all_pos:
            return True, ""
        errs = []
        for pid, pos in self._iter_positions_with_pid(all_pos):
            iid = self._pos_instrument_id(pos)
            ok, msg = EtoroClient.close_position(self, pid, instrument_id=iid)
            if not ok:
                errs.append(msg)
        return len(errs) == 0, "; ".join(errs) if errs else ""
