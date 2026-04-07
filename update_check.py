"""التحقق من وجود إصدار أحدث عبر ملف manifest (JSON) على HTTPS."""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request


def _version_tuple(s: str) -> tuple[int, ...]:
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?", str(s).strip())
    if not m:
        return (0,)
    return tuple(int(g or 0) for g in m.groups())


def _cmp_version(a: str, b: str) -> int:
    """-1 إذا a أقدم من b، 0 متساويان، 1 إذا a أحدث من b."""
    ta, tb = _version_tuple(a), _version_tuple(b)
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def run_check_sync(manifest_url: str, current_version: str) -> dict:
    """
    يُرجع قاموس:
      no_url — لا يوجد رابط في الإعدادات
      up_to_date — الإصدار الحالي محدث
      update_available — يوجد إصدار أحدث (+ remote_version, download_url, notes)
      error — فشل الشبكة أو JSON (+ message)
    """
    url = (manifest_url or "").strip()
    if not url:
        return {"status": "no_url"}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CryptoTrading/UpdateCheck"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return {"status": "error", "message": str(e) or type(e).__name__}
    except Exception as e:
        return {"status": "error", "message": str(e) or type(e).__name__}

    remote = str(data.get("version") or "").strip()
    if not remote:
        return {"status": "error", "message": "manifest missing version"}

    cur = str(current_version or "").strip() or "0"
    if _cmp_version(cur, remote) >= 0:
        return {"status": "up_to_date", "remote_version": remote}

    dl = str(data.get("download_url") or "").strip()
    notes = data.get("notes_ar") or data.get("notes") or data.get("notes_en") or ""
    return {
        "status": "update_available",
        "remote_version": remote,
        "download_url": dl,
        "notes": str(notes).strip(),
    }
