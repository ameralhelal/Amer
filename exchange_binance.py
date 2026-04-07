import logging
import math
import time
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException

from binance_chart_aliases import binance_spot_pair_symbol

log = logging.getLogger("trading.exchange")


def _err_msg(e: Exception) -> str:
    """رسالة خطأ متوافقة مع Python 3 (لا يوجد e.message في بعض الاستثناءات)."""
    return getattr(e, "message", None) or str(e)


class BaseClient:
    """
    Base class for shared helpers between SpotClient and FuturesClient.
    يضيف طبقة حماية بسيطة على المدخلات والأخطاء.
    """

    def __init__(self, api_key, api_secret, testnet=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet

        if testnet:
            self.client = Client(api_key, api_secret, testnet=True)
            # Spot Testnet — المكتبة قد لا تضبط الرابط تلقائياً
            self.client.API_URL = "https://testnet.binance.vision/api"
        else:
            self.client = Client(api_key, api_secret)

    def _validate_symbol(self, symbol: str) -> bool:
        return isinstance(symbol, str) and len(symbol) >= 3

    def _validate_quantity(self, quantity: float) -> bool:
        try:
            q = float(quantity)
            return q > 0
        except Exception:
            return False

    def get_last_price(self, symbol: str) -> float:
        """
        Returns last price for symbol (Spot ticker).
        For Futures we override if needed, but this works for both in most cases.
        """
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception:
            return 0.0

    def get_usdt_balance(self) -> float:
        """رصيد USDT المتاح في المحفظة (Spot)."""
        try:
            bal = self.client.get_asset_balance(asset="USDT")
            if not bal:
                return 0.0
            # قد تُرجع الـ API "free" أو "available"
            raw = bal.get("free") or bal.get("available") or 0
            if isinstance(raw, str):
                return float(raw)
            if isinstance(raw, (int, float)):
                return float(raw)
            return 0.0
        except Exception as e:
            log.warning("Spot get_usdt_balance failed: %s", e)
            return 0.0

    def get_account_balances(self) -> tuple[dict[str, float], str | None]:
        """يرجع (قاموس أصل -> رصيد متاح، رسالة خطأ إن وُجدت)."""
        try:
            acc = self.client.get_account()
            balances = {}
            for b in (acc.get("balances") or []):
                asset = (b.get("asset") or "").strip()
                free = b.get("free") or b.get("available") or "0"
                try:
                    val = float(free)
                    if val > 0 and asset:
                        balances[asset] = val
                except (TypeError, ValueError):
                    pass
            return balances, None
        except Exception as e:
            return {}, str(e)


def _round_down_step(value: float, step: float) -> float:
    """تقريب الكمية لأسفل لمضاعفات stepSize (تفادي خطأ LOT_SIZE)."""
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step))))
    n = math.floor(value / step)
    return round(n * step, precision)


def _round_up_step(value: float, step: float) -> float:
    """تقريب الكمية لأعلى لمضاعفات stepSize."""
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step))))
    n = math.ceil(value / step)
    return round(n * step, precision)


class SpotClient(BaseClient):
    """
    Spot trading client wrapper.
    """

    def _get_symbol_filters(self, symbol: str):
        """جلب فلاتر الرمز (LOT_SIZE, MIN_NOTIONAL/NOTIONAL) من المنصة."""
        try:
            info = self.client.get_symbol_info(symbol)
        except Exception:
            return None
        filters = info.get("filters") or []
        out = {"min_qty": 0.0, "step_size": 0.00001, "min_notional": 5.0}
        for f in filters:
            if f.get("filterType") == "LOT_SIZE":
                out["min_qty"] = float(f.get("minQty", 0))
                out["step_size"] = float(f.get("stepSize", "0.00001"))
                out["max_qty"] = float(f.get("maxQty", 1e9))
                break
        for f in filters:
            if f.get("filterType") in ("MIN_NOTIONAL", "NOTIONAL"):
                out["min_notional"] = float(f.get("minNotional", 5.0))
                break
        return out

    def _adjust_quantity_to_filters(self, symbol: str, quantity: float, price: float) -> float:
        """تعديل الكمية لتحقيق LOT_SIZE و MIN_NOTIONAL."""
        filters = self._get_symbol_filters(symbol)
        if not filters:
            return round(quantity, 8)
        min_qty = filters["min_qty"]
        step = filters["step_size"]
        min_notional = filters["min_notional"]
        if price <= 0:
            price = self.get_last_price(symbol) or price
        if price <= 0:
            return quantity
        q_min_notional = min_notional / price
        q = max(quantity, min_qty, q_min_notional)
        q = _round_down_step(q, step)
        if q < min_qty:
            q = min_qty
        q = _round_down_step(q, step)
        # التأكد أن القيمة (كمية × سعر) لا تقل عن MIN_NOTIONAL
        if q * price < min_notional:
            q = _round_up_step(min_notional / price, step)
        q = max(q, min_qty)
        return q

    def place_order(self, symbol: str, side: str, quantity: float, price: float = None):
        """
        Place a simple market order on Spot.
        quantity يُعدَّل تلقائياً ليتوافق مع LOT_SIZE و MIN_NOTIONAL.
        price اختياري (لحساب MIN_NOTIONAL)؛ إن لم يُمرَّر يُجلب من التاكر.
        Returns (ok: bool, message: str)
        """
        if not self._validate_symbol(symbol):
            return False, "[SPOT][VALIDATION] Invalid symbol"
        if not self._validate_quantity(quantity):
            return False, "[SPOT][VALIDATION] Quantity must be > 0"
        if price is None:
            price = self.get_last_price(symbol)
        if price and price > 0:
            quantity = self._adjust_quantity_to_filters(symbol, quantity, price)
        if not self._validate_quantity(quantity):
            return False, "[SPOT][VALIDATION] Quantity after filters is too small (increase amount or check MIN_NOTIONAL)."

        try:
            order = self.client.create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
            )
            return True, f"[SPOT] {side} {symbol} qty={quantity} | OrderId={order.get('orderId')}"
        except BinanceAPIException as e:
            return False, f"[SPOT][API ERROR] {_err_msg(e)}"
        except BinanceOrderException as e:
            return False, f"[SPOT][ORDER ERROR] {_err_msg(e)}"
        except Exception as e:
            return False, f"[SPOT][ERROR] {e}"

    def _base_asset_for_spot_symbol(self, symbol: str):
        s = (symbol or "").strip().upper()
        for quote in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "BTC", "ETH", "BNB"):
            if s.endswith(quote) and len(s) > len(quote):
                return s[: -len(quote)]
        return None

    def _free_balance_asset(self, asset: str) -> float:
        try:
            bal = self.client.get_asset_balance(asset=(asset or "").upper())
            if not bal:
                return 0.0
            raw = bal.get("free") or bal.get("available") or 0
            return float(raw)
        except Exception:
            return 0.0

    def _qty_for_spot_market_sell(self, symbol: str, qty_desired: float):
        """
        كمية بيع آمنة: لا تتجاوز الرصيد الحر، وتُقرّب لأسفل حسب LOT_SIZE،
        وتتجاهل ما دون MIN_NOTIONAL (غبار) — بدون زيادة الكمية كما في place_order للشراء.
        """
        sym = binance_spot_pair_symbol(symbol)
        price = self.get_last_price(sym)
        if price <= 0:
            return 0.0, "no_price"
        base = self._base_asset_for_spot_symbol(sym)
        if not base:
            return 0.0, "base_asset"
        free = self._free_balance_asset(base)
        q = min(float(qty_desired), free)
        if q <= 0:
            return 0.0, "no_balance"
        filters = self._get_symbol_filters(sym)
        if not filters:
            return round(q, 8), None
        step = float(filters.get("step_size") or 0.00001)
        min_qty = float(filters.get("min_qty") or 0)
        min_notional = float(filters.get("min_notional") or 5.0)
        q = _round_down_step(q, step)
        if q < min_qty:
            return 0.0, "min_qty"
        if q * price < min_notional:
            return 0.0, "dust"
        return q, None

    def close_all_spot_positions(self, rows: list) -> tuple:
        """
        بيع سبوت (سوق) لكل صف في rows: (symbol, qty, ...) من جدول المراكز.
        يُقارن بكمية الرصيد الحر ولا يحاول بيع أكثر من المتوفر.
        """
        parts: list[str] = []
        any_ok = False
        if not rows:
            return False, "[SPOT] No positions in list."
        for row in rows:
            if not row:
                continue
            sym_u = str(row[0]).strip()
            try:
                qty_ui = float(row[1])
            except (TypeError, ValueError):
                continue
            if qty_ui <= 0 or not sym_u:
                continue
            sym = binance_spot_pair_symbol(sym_u)
            q, skip = self._qty_for_spot_market_sell(sym, qty_ui)
            if skip:
                parts.append(f"{sym}: skip ({skip})")
                continue
            try:
                order = self.client.create_order(
                    symbol=sym,
                    side="SELL",
                    type="MARKET",
                    quantity=q,
                )
                any_ok = True
                parts.append(f"{sym} SELL qty={q} id={order.get('orderId')}")
            except BinanceAPIException as e:
                parts.append(f"{sym}: API {_err_msg(e)}")
            except BinanceOrderException as e:
                parts.append(f"{sym}: ORDER {_err_msg(e)}")
            except Exception as e:
                parts.append(f"{sym}: {e}")
            time.sleep(0.12)
        msg = " | ".join(parts)
        if not any_ok:
            return False, msg or "[SPOT] No orders placed."
        return True, msg


class FuturesClient(BaseClient):
    """
    Futures trading client wrapper.
    """

    def __init__(self, api_key, api_secret, testnet=False):
        super().__init__(api_key, api_secret, testnet)
        if testnet:
            # Testnet Futures — رابط فيوتشر وهمي
            self.client.API_URL = "https://testnet.binancefuture.com"

    def get_usdt_balance(self) -> float:
        """رصيد USDT المتاح في محفظة الفيوتشر."""
        try:
            balances = self.client.futures_account_balance()
            for b in balances:
                if b.get("asset") == "USDT":
                    return float(b.get("availableBalance", 0) or b.get("balance", 0) or 0)
            return 0.0
        except Exception:
            return 0.0

    def set_leverage(self, symbol: str, leverage: int):
        """
        Set leverage for a futures symbol.
        Used by TradingPanel before placing Futures orders.
        Returns (ok: bool, response_or_error: str|dict)
        """
        if not self._validate_symbol(symbol):
            return False, "[LEV][VALIDATION] Invalid symbol"
        if not isinstance(leverage, int) or not (1 <= leverage <= 125):
            return False, "[LEV][VALIDATION] Leverage must be between 1 and 125"

        try:
            res = self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            return True, res
        except BinanceAPIException as e:
            return False, f"[LEV][API ERROR] {_err_msg(e)}"
        except Exception as e:
            return False, f"[LEV][ERROR] {e}"

    def place_order(self, symbol: str, side: str, quantity: float):
        """
        Place a simple market order on Futures.
        Returns (ok: bool, message: str)
        """
        if not self._validate_symbol(symbol):
            return False, "[FUTURES][VALIDATION] Invalid symbol"
        if not self._validate_quantity(quantity):
            return False, "[FUTURES][VALIDATION] Quantity must be > 0"

        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
            )
            return True, f"[FUTURES] {side} {symbol} qty={quantity} | OrderId={order.get('orderId')}"
        except BinanceAPIException as e:
            return False, f"[FUTURES][API ERROR] {_err_msg(e)}"
        except BinanceOrderException as e:
            return False, f"[FUTURES][ORDER ERROR] {_err_msg(e)}"
        except Exception as e:
            return False, f"[FUTURES][ERROR] {e}"

    def get_open_positions(self):
        """
        قائمة المراكز المفتوحة على الفيوتشر (positionAmt != 0).
        Returns list of dict: {"symbol": str, "entry_price": float, "quantity": float}
        """
        out = []
        try:
            positions = self.client.futures_position_information()
            for pos in positions or []:
                amt = float(pos.get("positionAmt", 0) or 0)
                if amt == 0:
                    continue
                entry = float(pos.get("entryPrice", 0) or 0)
                out.append({
                    "symbol": pos.get("symbol", ""),
                    "entry_price": entry,
                    "quantity": abs(amt),
                })
        except Exception as e:
            log.warning("get_open_positions failed: %s", e)
        return out

    def close_position(self, symbol: str):
        """
        Close position for a specific symbol (Futures).
        Uses POSITION_SIDE_SHORT/LONG via reduceOnly market order logic.
        Returns (ok: bool, message: str)
        """
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if not positions:
                return False, f"[FUTURES] No position for {symbol}."

            pos = positions[0]
            amt = float(pos["positionAmt"])

            if amt == 0:
                return False, f"[FUTURES] No open position for {symbol}."

            side = "SELL" if amt > 0 else "BUY"
            qty = abs(amt)

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            return True, f"[FUTURES] Closed {symbol} position qty={qty} | OrderId={order.get('orderId')}"
        except BinanceAPIException as e:
            return False, f"[FUTURES][API ERROR] {_err_msg(e)}"
        except Exception as e:
            return False, f"[FUTURES][ERROR] {e}"

    def close_all_positions(self):
        """
        Emergency: close all open futures positions.
        Returns (ok: bool, message: str)
        """
        try:
            positions = self.client.futures_position_information()
            if not positions:
                return False, "[FUTURES] No positions to close."

            closed_any = False
            messages = []

            for pos in positions:
                amt = float(pos["positionAmt"])
                symbol = pos["symbol"]

                if amt == 0:
                    continue

                side = "SELL" if amt > 0 else "BUY"
                qty = abs(amt)

                try:
                    order = self.client.futures_create_order(
                        symbol=symbol,
                        side=side,
                        type="MARKET",
                        quantity=qty,
                        reduceOnly=True
                    )
                    closed_any = True
                    messages.append(f"{symbol} qty={qty} closed (OrderId={order.get('orderId')})")
                except Exception as e:
                    messages.append(f"{symbol} close error: {e}")

            if not closed_any:
                return False, "[FUTURES] No open positions to close."

            return True, "[EMERGENCY] Closed positions: " + " | ".join(messages)
        except BinanceAPIException as e:
            return False, f"[FUTURES][API ERROR] {_err_msg(e)}"
        except Exception as e:
            return False, f"[FUTURES][ERROR] {e}"