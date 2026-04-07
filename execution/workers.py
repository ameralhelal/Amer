"""
عمال تنفيذ الأوامر على المنصة (خيوط Qt) — منفصلون عن لوحة التداول لتقليل التداخل
ومركز واحد لاحقاً لبوابة التنفيذ.
"""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger("execution.workers")


def _run_close_all_spot(client, rows: list) -> tuple[bool, str]:
    """بيع سبوت متتالي: Binance عبر close_all_spot_positions؛ غير ذلك عبر place_order لكل صف."""
    if hasattr(client, "close_all_spot_positions"):
        return client.close_all_spot_positions(rows)
    parts: list[str] = []
    ok_any = False
    for row in rows:
        if not row:
            continue
        sym = str(row[0]).strip()
        try:
            qty = float(row[1] or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 0 or not sym:
            continue
        price = 0.0
        if hasattr(client, "get_last_price"):
            try:
                price = float(client.get_last_price(sym) or 0)
            except Exception:
                price = 0.0
        try:
            res = client.place_order(sym, "SELL", qty, price if price > 0 else None)
        except TypeError:
            res = client.place_order(sym, "SELL", qty)
        if isinstance(res, tuple) and len(res) >= 2:
            ok, m = bool(res[0]), str(res[1] or "")
        else:
            ok, m = bool(res), ""
        if ok:
            ok_any = True
        parts.append(f"{sym}: {m or ok}")
        time.sleep(0.4)
    msg = " | ".join(parts) if parts else "[SPOT] empty"
    return ok_any, msg


class EtoroResolvePositionWorker(QObject):
    """جلب positionID من orderID في خيط منفصل — لا يجمّد واجهة الشراء."""
    finished = pyqtSignal(int)  # position_id أو 0

    def __init__(self, api_key: str, api_secret: str, testnet: bool, order_id: int):
        super().__init__()
        self._api_key = api_key or ""
        self._api_secret = api_secret or ""
        self._testnet = bool(testnet)
        self._order_id = int(order_id)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from exchange_etoro import EtoroFuturesClient

            c = EtoroFuturesClient(self._api_key, self._api_secret, testnet=self._testnet)
            pid = c._etoro_resolve_position_id_from_order(
                self._order_id, cancel_check=lambda: self._cancelled
            )
            if self._cancelled:
                self.finished.emit(0)
                return
            self.finished.emit(int(pid or 0))
        except Exception:
            self.finished.emit(0)


class OrderWorker(QObject):
    finished = pyqtSignal(bool, str, str, str, float, float, bool, str, object, object)

    def __init__(
        self,
        client,
        symbol: str,
        side: str,
        quantity: float,
        max_retries: int = 2,
        price: float = 0.0,
        testnet: bool = False,
        reason: str = "",
        etoro_position_id: int | None = None,
        etoro_open_order_id: int | None = None,
        etoro_instrument_id: int | None = None,
    ):
        super().__init__()
        self.client = client
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.max_retries = max_retries
        self.price = float(price or 0)
        self.testnet = bool(testnet)
        self.reason = str(reason or "")
        self.etoro_position_id = int(etoro_position_id) if etoro_position_id is not None else None
        try:
            self.etoro_open_order_id = (
                int(etoro_open_order_id) if etoro_open_order_id is not None else None
            )
        except (TypeError, ValueError):
            self.etoro_open_order_id = None
        try:
            self.etoro_instrument_id = (
                int(etoro_instrument_id) if etoro_instrument_id is not None else None
            )
        except (TypeError, ValueError):
            self.etoro_instrument_id = None

    def run(self):
        try:
            last_err = ""
            position_id = None
            open_order_id = None
            for attempt in range(self.max_retries + 1):
                try:
                    if hasattr(self.client, "close_position") and self.side == "SELL":
                        ep = getattr(self, "etoro_position_id", None)
                        try:
                            ep_i = int(ep) if ep is not None else 0
                        except (TypeError, ValueError):
                            ep_i = 0
                        if (
                            ep_i > 0
                            and hasattr(self.client, "close_position_by_position_id")
                        ):
                            iid_i = getattr(self, "etoro_instrument_id", None)
                            if iid_i is None and hasattr(self.client, "get_instrument_id"):
                                try:
                                    gi = self.client.get_instrument_id(self.symbol)
                                    if gi is not None and int(gi) > 0:
                                        iid_i = int(gi)
                                except Exception:
                                    iid_i = None
                            ok, msg = self.client.close_position_by_position_id(
                                ep_i, instrument_id=iid_i
                            )
                        else:
                            oid_i = 0
                            try:
                                oa = getattr(self, "etoro_open_order_id", None)
                                oid_i = int(oa) if oa is not None else 0
                            except (TypeError, ValueError):
                                oid_i = 0
                            if (
                                oid_i > 0
                                and hasattr(self.client, "_etoro_resolve_position_id_from_order")
                                and hasattr(self.client, "close_position_by_position_id")
                            ):
                                log.info(
                                    "[eToro بيع] لا position_id في الأمر — حل orderID=%s إلى positionID",
                                    oid_i,
                                )
                                pid_r = self.client._etoro_resolve_position_id_from_order(
                                    oid_i, max_attempts=18
                                )
                                if pid_r and int(pid_r) > 0:
                                    ok, msg = self.client.close_position_by_position_id(
                                        int(pid_r)
                                    )
                                else:
                                    em = getattr(
                                        self.client, "_last_order_lookup_error_msg", None
                                    )
                                    if em:
                                        ok, msg = False, str(em)
                                    else:
                                        ok, msg = self.client.close_position(self.symbol)
                            else:
                                ok, msg = self.client.close_position(self.symbol)
                    else:
                        px = float(self.price or 0)
                        if px > 0:
                            try:
                                result = self.client.place_order(
                                    self.symbol, self.side, self.quantity, px
                                )
                            except TypeError:
                                result = self.client.place_order(
                                    self.symbol, self.side, self.quantity
                                )
                        else:
                            result = self.client.place_order(
                                self.symbol, self.side, self.quantity
                            )
                        ok = result[0]
                        msg = result[1] if len(result) > 1 else ""
                        position_id = result[2] if len(result) > 2 else None
                        open_order_id = result[3] if len(result) > 3 else None
                    self.finished.emit(
                        ok, msg or "", self.side, self.symbol, self.price, self.quantity,
                        self.testnet, self.reason, position_id, open_order_id,
                    )
                    return
                except Exception as e:
                    last_err = str(e)
                    log.info("Order attempt %d failed: %s", attempt + 1, last_err)
                    log.warning("Order attempt %d failed: %s", attempt + 1, last_err)
                    if attempt < self.max_retries:
                        time.sleep(1.0)
            self.finished.emit(
                False, last_err or "Order failed", self.side, self.symbol, self.price, self.quantity,
                self.testnet, self.reason, None, None,
            )
        except Exception as e:
            log.exception("OrderWorker: خطأ غير متوقع")
            self.finished.emit(
                False, str(e), self.side, self.symbol, self.price, self.quantity,
                self.testnet, self.reason, None, None,
            )


class ClosePositionWorker(QObject):
    """إغلاق مركز واحد، إغلاق الكل (فيوتشر)، أو بيع كل صفوف السبوت الظاهرة في الجدول."""
    finished = pyqtSignal(bool, str, str, bool)

    def __init__(
        self,
        client,
        symbol: str = None,
        close_all: bool = False,
        position_id: int | None = None,
        etoro_close_spec: dict | None = None,
        spot_close_rows: list | None = None,
    ):
        super().__init__()
        self.client = client
        self.symbol = symbol or ""
        self.close_all = close_all
        self.position_id = position_id
        self.etoro_close_spec = etoro_close_spec
        self.spot_close_rows = spot_close_rows

    def run(self):
        try:
            if self.spot_close_rows is not None:
                ok, msg = _run_close_all_spot(self.client, self.spot_close_rows)
                self.finished.emit(ok, msg or "", "", True)
            elif self.close_all:
                ok, msg = self.client.close_all_positions()
                self.finished.emit(ok, msg or "", "", True)
            elif self.etoro_close_spec:
                s = self.etoro_close_spec
                sym = str(s.get("symbol") or "").strip()
                entry_i = None
                qty_i = None
                try:
                    ev = s.get("entry_price")
                    entry_i = float(ev) if ev is not None else None
                except (TypeError, ValueError):
                    entry_i = None
                try:
                    qv = s.get("quantity")
                    qty_i = float(qv) if qv is not None else None
                except (TypeError, ValueError):
                    qty_i = None
                iid_i = None
                try:
                    if sym.startswith("ETORO_"):
                        iid_i = int(sym.split("_", 1)[1])
                except (TypeError, ValueError, IndexError):
                    iid_i = None
                if iid_i is None and hasattr(self.client, "get_instrument_id"):
                    try:
                        gi = self.client.get_instrument_id(sym)
                        if gi is not None and int(gi) > 0:
                            iid_i = int(gi)
                    except Exception:
                        iid_i = None
                pid = s.get("position_id")
                try:
                    pid_i = int(pid) if pid is not None else None
                except (TypeError, ValueError):
                    pid_i = None
                oid_i = None
                try:
                    ox = s.get("etoro_open_order_id")
                    if ox is not None:
                        oid_i = int(ox)
                        if oid_i <= 0:
                            oid_i = None
                except (TypeError, ValueError):
                    oid_i = None
                # fallback قوي: إن لم يصل position_id/order_id من الصف، حاول مطابقته من open rows.
                if (pid_i is None or pid_i <= 0) and hasattr(self.client, "find_position_id_for_open_row"):
                    try:
                        pid_guess = self.client.find_position_id_for_open_row(
                            sym,
                            entry_i if entry_i is not None else 0.0,
                            qty_i if qty_i is not None else 0.0,
                        )
                        if pid_guess is not None and int(pid_guess) > 0:
                            pid_i = int(pid_guess)
                            log.info("[eToro إغلاق صف] تم إيجاد position_id=%s من بيانات الصف", pid_i)
                    except Exception as e:
                        log.debug("[eToro إغلاق صف] فشل find_position_id_for_open_row: %s", e)
                if pid_i is not None and pid_i > 0 and hasattr(self.client, "close_position_by_position_id"):
                    ok, msg = self.client.close_position_by_position_id(
                        pid_i, instrument_id=iid_i
                    )
                elif (
                    oid_i
                    and hasattr(self.client, "_etoro_resolve_position_id_from_order")
                    and hasattr(self.client, "close_position_by_position_id")
                ):
                    log.info("[eToro إغلاق صف] حل orderID=%s ثم إغلاق", oid_i)
                    pid_r = self.client._etoro_resolve_position_id_from_order(
                        int(oid_i), max_attempts=18
                    )
                    if pid_r and int(pid_r) > 0:
                        ok, msg = self.client.close_position_by_position_id(
                            int(pid_r), instrument_id=iid_i
                        )
                    else:
                        em = getattr(self.client, "_last_order_lookup_error_msg", None)
                        if em:
                            ok, msg = False, str(em)
                        else:
                            ok, msg = self.client.close_position(sym)
                else:
                    ok, msg = self.client.close_position(sym)
                self.finished.emit(ok, msg or "", sym, False)
            elif self.position_id is not None and hasattr(self.client, "close_position_by_position_id"):
                ok, msg = self.client.close_position_by_position_id(int(self.position_id))
                self.finished.emit(ok, msg or "", self.symbol, False)
            else:
                ok, msg = self.client.close_position(self.symbol)
                self.finished.emit(ok, msg or "", self.symbol, False)
        except Exception as e:
            ca = bool(self.close_all) or (self.spot_close_rows is not None)
            self.finished.emit(False, str(e), self.symbol, ca)
