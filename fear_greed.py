"""Crypto Fear & Greed Index (alternative.me), cached to limit HTTP traffic."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

import requests

log = logging.getLogger("trading.fear_greed")

_TTL_SEC = 1800.0
_URL = "https://api.alternative.me/fng/?limit=1"
_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_ts: float = 0.0


def get_crypto_fear_greed_index() -> Optional[Dict[str, Any]]:
    """Return ``{"value": int 0–100, "classification": str}`` or last good cache on failure."""
    global _cache, _cache_ts
    with _lock:
        now = time.time()
        if _cache is not None and (now - _cache_ts) < _TTL_SEC:
            return dict(_cache)
        try:
            r = requests.get(_URL, timeout=10)
            r.raise_for_status()
            payload = r.json()
            row = (payload.get("data") or [{}])[0]
            val = int(str(row.get("value", "0")))
            cls = str(row.get("value_classification", "") or "")
            out = {"value": val, "classification": cls}
            _cache = out
            _cache_ts = time.time()
            return dict(out)
        except Exception as e:
            log.debug("fear_greed fetch failed: %s", e)
            if _cache is not None:
                return dict(_cache)
            return None
