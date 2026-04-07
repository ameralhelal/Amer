# symbol_fetcher.py — جلب كل أزواج USDT النشطة (سبوت) من Binance (منطق موحّد مع binance_symbols)
from binance_symbols import load_all_usdt_spot_trading_symbols


class SymbolFetcher:
    def __init__(self):
        self.all_symbols = load_all_usdt_spot_trading_symbols()
        if not self.all_symbols:
            self.all_symbols = ["BTCUSDT", "ETHUSDT"]

    def get_all_symbols(self):
        return self.all_symbols
