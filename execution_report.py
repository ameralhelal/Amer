from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timezone

_REPORT_LOCK = threading.RLock()


def sanitize_for_execution_json(
    obj,
    *,
    max_str: int = 6000,
    max_list: int = 500,
    _depth: int = 0,
):
    """تحويل إلى هيكل قابل لـ JSON (لتقارير التنفيذ) مع حدود حجم."""
    if _depth > 24:
        return "<max_depth>"
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 10) if abs(obj) < 1e18 else float(obj)
    if isinstance(obj, int):
        return obj
    if isinstance(obj, str):
        return obj[:max_str] + ("…" if len(obj) > max_str else "")
    try:
        import numpy as np  # type: ignore

        if isinstance(obj, np.floating):
            return sanitize_for_execution_json(float(obj), max_str=max_str, max_list=max_list, _depth=_depth)
        if isinstance(obj, np.integer):
            return int(obj)
    except Exception:
        pass
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in sorted(obj.items(), key=lambda x: str(x[0])):
            sk = str(k)[:220]
            out[sk] = sanitize_for_execution_json(v, max_str=max_str, max_list=max_list, _depth=_depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [
            sanitize_for_execution_json(v, max_str=max_str, max_list=max_list, _depth=_depth + 1)
            for v in obj[:max_list]
        ]
    return str(obj)[:max_str]


def _report_path() -> str:
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "execution_reports.jsonl")


def append_execution_report(payload: dict) -> tuple[bool, str]:
    """
    Append one execution report row as JSONL.
    Returns (ok, path_or_error).
    """
    path = _report_path()
    row = dict(payload or {})
    row.setdefault("time_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        with _REPORT_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
        return True, path
    except Exception as e:
        return False, str(e)
