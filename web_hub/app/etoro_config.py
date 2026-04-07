"""
قراءة مفاتيح eToro للخادم فقط — لا تُرسل أبداً للمتصفح.
الأولوية: متغيرات البيئة ETORO_USER_KEY / ETORO_API_KEY ثم ملف api_settings.json (نفس مسار تطبيق سطح المكتب).
إذا كان الملف مشفّراً لا يمكن قراءة eToro منه بدون كلمة المرور — استخدم متغيرات البيئة أو احفظ مفاتيح eToro بعد فك التشفير من التطبيق.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _api_settings_path() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    return Path(base) / "CryptoTrading" / "api_settings.json"


def etoro_demo_flag() -> bool:
    v = (os.environ.get("ETORO_DEMO") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def get_etoro_credentials() -> tuple[str, str, bool]:
    """
    يُرجع (user_key, api_key, demo).
    """
    demo = etoro_demo_flag()
    u = (os.environ.get("ETORO_USER_KEY") or "").strip()
    k = (os.environ.get("ETORO_API_KEY") or "").strip()
    if u and k:
        return u, k, demo
    p = _api_settings_path()
    if not p.is_file():
        return "", "", demo
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return "", "", demo
    if raw.get("salt_b64") and raw.get("data_b64"):
        # ملف مشفّر — مفاتيح eToro غير متاحة للخادم بدون كلمة مرور الجلسة
        return "", "", demo
    u = (raw.get("etoro_user_key") or "").strip()
    k = (raw.get("etoro_api_key") or "").strip()
    return u, k, demo


def etoro_configured() -> bool:
    u, k, _ = get_etoro_credentials()
    return bool(u and k)
