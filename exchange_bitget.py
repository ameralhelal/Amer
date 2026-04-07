import logging
from typing import Tuple

log = logging.getLogger("trading.exchange_bitget")


class BaseClient:
    """
    واجهة عامة بسيطة لعميل Bitget (Spot / Futures).

    ملاحظة هامة:
    - هذا الملف يوفّر حالياً هيكلاً متوافقاً مع exchange_binance من حيث أسماء الدوال فقط.
    - لم يتم بعد تنفيذ الاتصال الحقيقي بـ Bitget API (التوقيع، الطلبات، ...).
    - كل دوال التنفيذ ترجع حالياً رسالة واضحة بأن التنفيذ غير مفعّل بعد.
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.testnet = bool(testnet)

    def _not_implemented(self, where: str) -> Tuple[bool, str]:
        msg = (
            f"[BITGET][{where}] التنفيذ الحقيقي غير مفعّل بعد في هذا الإصدار. "
            f"ما زال العميل قيد الإعداد (يحتاج ضبط مفاتيح Bitget والتوقيع حسب وثائقهم)."
        )
        log.warning(msg)
        return False, msg

    def get_last_price(self, symbol: str) -> float:
        # السعر الفعلي يُجلب في التطبيق من WebSocket / بورصة أخرى؛ نعيد 0 هنا حتى لا نعتمد عليه.
        return 0.0

    def get_usdt_balance(self) -> float:
        # لم يُنفَّذ بعد — حتى لا نعطي رصيداً خاطئاً نرجع 0 فقط.
        return 0.0


class SpotClient(BaseClient):
    """
    عميل Spot لـ Bitget — مطابق لواجهة SpotClient في exchange_binance من حيث أسماء الدوال.
    """

    def place_order(self, symbol: str, side: str, quantity: float, price: float = None):
        """
        تنفيذ أمر سوق (Market) على Bitget Spot.

        لم يُنفَّذ بعد: ترجع فقط رسالة واضحة بأن التنفيذ غير مفعّل.
        """
        return self._not_implemented("SPOT_ORDER")


class FuturesClient(BaseClient):
    """
    عميل Futures لـ Bitget — مطابق لواجهة FuturesClient في exchange_binance من حيث أسماء الدوال.
    """

    def set_leverage(self, symbol: str, leverage: int):
        return self._not_implemented("FUTURES_LEVERAGE")

    def place_order(self, symbol: str, side: str, quantity: float):
        return self._not_implemented("FUTURES_ORDER")

    def close_position(self, symbol: str):
        return self._not_implemented("FUTURES_CLOSE_POSITION")

    def close_all_positions(self):
        return self._not_implemented("FUTURES_CLOSE_ALL")

