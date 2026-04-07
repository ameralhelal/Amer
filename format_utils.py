# format_utils.py — تنسيق الأرقام للعرض (أسعار العملات الصغيرة بالكامل)


def _to_display_currency(value_usdt):
    """تحويل قيمة بالدولار (USDT) إلى عملة العرض المختارة. يُرجع (القيمة المحوّلة، رمز العملة)."""
    try:
        from config import load_config
        cfg = load_config()
        cur = (cfg.get("display_currency") or "USD").strip().upper()
        val = float(value_usdt)
        if cur == "EUR":
            rate = float(cfg.get("currency_rate_eur") or 0.92)
            return val * rate, "€"
        return val, "$"
    except Exception:
        return float(value_usdt), "$"


def format_currency(value_usdt, signed=False):
    """تنسيق مبلغ (قيمة، ربح/خسارة) حسب العملة المختارة (دولار، يورو، …)."""
    val, symbol = _to_display_currency(value_usdt)
    if signed:
        return f"{val:+.2f} {symbol}"
    return f"{val:.2f} {symbol}"


def format_price(price):
    """تنسيق السعر للعرض: عرض الرقم كاملاً للقيم الصغيرة (< 0.01) دون اقتطاع.
    للعملات مثل SHIB, PEPE التي قيمتها أقل من سنت يعرض مثلاً 0.00000005 بدلاً من 0.09"""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "0"
    if p >= 1000:
        s = f"{p:,.2f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    if p >= 1:
        s = f"{p:,.4f}"
        return s.rstrip("0").rstrip(".").rstrip(",")
    if p >= 0.01:
        s = f"{p:.6f}"
        return s.rstrip("0").rstrip(".")
    if p >= 0.0001:
        s = f"{p:.8f}"
        return s.rstrip("0").rstrip(".")
    if p >= 0.00000001:
        s = f"{p:.12f}"
        return s.rstrip("0").rstrip(".")
    if p != 0:
        s = f"{p:.16f}"
        return s.rstrip("0").rstrip(".") or "0"
    return "0"
