import logging
import threading
from typing import Optional, Tuple

import requests

from config import load_config
from format_utils import format_price, format_currency
from translations import tr, get_language

log = logging.getLogger("trading.telegram")


def _check_internet(timeout: float = 4.0) -> bool:
    """التحقق من الاتصال بالإنترنت أولاً (طلب خفيف إلى نفس خادم تيليجرام)."""
    try:
        r = requests.get("https://api.telegram.org", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def _verify_bot_token(token: str, timeout: float = 8.0) -> bool:
    """التحقق من صحة رمز البوت عبر getMe — إن نجح فالرمز صحيح (نسخته من BotFather)."""
    if not (token or "").strip():
        return False
    try:
        url = f"https://api.telegram.org/bot{token.strip()}/getMe"
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return False
        data = r.json()
        return data.get("ok") is True
    except Exception:
        return False


def _build_trade_message(
    side: str,
    symbol: str,
    price: float,
    qty: float,
    mode: str,
    pnl: Optional[float] = None,
    is_bot: bool = False,
    confidence: Optional[float] = None,
) -> str:
    """بناء نص تنبيه تيليجرام لصفقة واحدة."""
    lang = get_language()
    side = side.upper()
    is_buy = side == "BUY"
    side_ar = tr("trading_side_buy") if is_buy else tr("trading_side_sell")

    price_str = format_price(price)
    value_str = format_currency(price * qty)
    qty_str = f"{qty:.6f}".rstrip("0").rstrip(".") or "0"
    mode_str = "حقيقي (LIVE)" if mode == "live" else "وهمي (Testnet)"
    mode_en = "LIVE" if mode == "live" else "TESTNET"

    header = "🚀 صفقة جديدة" if is_buy else "✅ إغلاق صفقة"
    if lang == "en":
        header = "🚀 New trade opened" if is_buy else "✅ Trade closed"

    lines = [
        f"{header}",
        f"{'النوع' if lang == 'ar' else 'Side'}: {side_ar} ({side})",
        f"{'الرمز' if lang == 'ar' else 'Symbol'}: {symbol}",
        f"{'السعر' if lang == 'ar' else 'Price'}: {price_str}",
        f"{'الكمية' if lang == 'ar' else 'Quantity'}: {qty_str}",
        f"{'قيمة الصفقة' if lang == 'ar' else 'Value'}: {value_str}",
        f"{'الوضع' if lang == 'ar' else 'Mode'}: {mode_str} ({mode_en})",
    ]

    if is_bot:
        if lang == "ar":
            lines.append("منفَّذ بواسطة: 🤖 البوت")
        else:
            lines.append("Executed by: 🤖 Bot")
        if confidence is not None:
            c = f"{confidence:.1f}%"
            if lang == "ar":
                lines.append(f"ثقة التوصية: {c}")
            else:
                lines.append(f"Recommendation confidence: {c}")
    else:
        if lang == "ar":
            lines.append("منفَّذ بواسطة: 👤 يدوي")
        else:
            lines.append("Executed by: 👤 Manual")

    if pnl is not None and not is_buy:
        pnl_val = float(pnl)
        pnl_str = format_currency(pnl_val, signed=True)
        if lang == "ar":
            status = "ربح ✅" if pnl_val > 0 else ("خسارة ❌" if pnl_val < 0 else "تعادل")
            lines.append(f"الربح/الخسارة: {pnl_str} — {status}")
        else:
            status = "Profit ✅" if pnl_val > 0 else ("Loss ❌" if pnl_val < 0 else "Break-even")
            lines.append(f"P/L: {pnl_str} — {status}")

    return "\n".join(lines)


def _send_message(text: str, use_html: bool = True) -> bool:
    """إرسال الطلب فعلياً. تُرجع True إذا تم الإرسال بنجاح."""
    try:
        cfg = load_config()
        if not cfg.get("telegram_enabled"):
            return False
        token = (cfg.get("telegram_bot_token") or "").strip()
        chat_id = (cfg.get("telegram_chat_id") or "").strip()
        if not token or not chat_id:
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if use_html:
            payload["parse_mode"] = "HTML"
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            log.warning("Telegram send failed: %s - %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        log.warning("Telegram send error: %s", e)
        return False


def send_telegram_test_message(token: str = "", chat_id: str = "") -> Tuple[bool, Optional[str], Optional[str]]:
    """
    إرسال رسالة تجريبية إلى تيليجرام.
    الإرجاع: (نجاح، سبب_الفشل، نص_الخطأ_من_تيليجرام).
    """
    from config import load_config
    cfg = load_config()
    token = (token or cfg.get("telegram_bot_token") or "").strip()
    chat_id = (chat_id or cfg.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        return (False, None, None)
    if not _check_internet():
        return (False, "no_internet", None)
    if not _verify_bot_token(token):
        return (False, "bad_token", None)
    lang = get_language()
    if lang == "ar":
        text = "✅ تجربة تيليجرام\n\nإذا وصلتك هذه الرسالة فإعداد البوت ومعرّف المحادثة صحيح. ستصل تنبيهات الصفقات وكلمة السر (إن اخترت ذلك) إلى هنا."
    else:
        text = "✅ Telegram test\n\nIf you received this message, your bot token and chat ID are correct. Trade alerts and password backup (if you choose) will arrive here."
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.ok:
            return (True, None, None)
        body_raw = resp.text or ""
        body_lower = body_raw.lower()
        # استخراج رسالة الخطأ من تيليجرام إن وُجدت
        err_detail = body_raw
        try:
            data = resp.json()
            if isinstance(data, dict) and "description" in data:
                err_detail = data.get("description", body_raw)
        except Exception:
            pass
        if "chat not found" in body_lower or "chat_id" in body_lower or resp.status_code == 400:
            return (False, "bad_chat_id", err_detail.strip() or None)
        log.warning("Telegram test send failed: %s - %s", resp.status_code, body_raw[:200])
        return (False, None, err_detail.strip() or None)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        log.warning("Telegram test send error (network): %s", e)
        return (False, "no_internet", None)
    except Exception as e:
        log.warning("Telegram test send error: %s", e)
        return (False, None, str(e))


def send_password_recovery_reminder() -> bool:
    """إرسال تذكير استرجاع كلمة السر إلى تيليجرام (الراجع للرسائل السابقة إن أرسل نسخة احتياطية)."""
    lang = get_language()
    if lang == "ar":
        text = (
            "🔐 طلب استرجاع كلمة السر\n\n"
            "تم طلب استرجاع كلمة سر تطبيق التداول. إن كنت قد أرسلت سابقاً نسخة احتياطية عند الحفظ، "
            "ستجدها في الرسائل السابقة في هذه المحادثة. ابحث عن رسالة تحتوي على «نسخة احتياطية — كلمة سر»."
        )
    else:
        text = (
            "🔐 Password recovery request\n\n"
            "A password recovery was requested for the trading app. If you previously sent a backup when saving, "
            "you will find it in the earlier messages in this chat. Look for a message containing \"Backup — Trading app password\"."
        )
    return _send_message(text, use_html=False)


def send_password_backup_to_telegram(password: str) -> bool:
    """
    إرسال نسخة احتياطية من كلمة سر التطبيق إلى تيليجرام لاسترجاعها عند النسيان.
    تُستدعى بعد حفظ الإعدادات بتشفير. تُرجع True إذا تم الإرسال بنجاح.
    """
    if not password or not password.strip():
        return False
    lang = get_language()
    if lang == "ar":
        text = (
            "🔐 نسخة احتياطية — كلمة سر تطبيق التداول\n\n"
            f"كلمة السر: {password}\n\n"
            "احتفظ بهذه الرسالة في مكان آمن. في حال نسيان كلمة السر يمكنك الرجوع إليها.\n"
            "⚠️ لا تشاركها مع أحد."
        )
    else:
        text = (
            "🔐 Backup — Trading app password\n\n"
            f"Password: {password}\n\n"
            "Keep this message private. Use it to recover your password if you forget it.\n"
            "⚠️ Do not share with anyone."
        )
    return _send_message(text, use_html=False)


def send_trade_notification(
    side: str,
    symbol: str,
    price: float,
    qty: float,
    mode: str,
    pnl: Optional[float] = None,
    is_bot: bool = False,
    confidence: Optional[float] = None,
) -> None:
    """واجهة خارجية: إنشاء رسالة وإرسالها في خيط منفصل حتى لا تتجمّد الواجهة."""
    try:
        text = _build_trade_message(side, symbol, price, qty, mode, pnl=pnl, is_bot=is_bot, confidence=confidence)
    except Exception as e:
        log.warning("Failed to build Telegram message: %s", e)
        return

    def _send():
        _send_message(text)

    t = threading.Thread(target=_send, daemon=True)
    t.start()

