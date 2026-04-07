# websocket_client.py
import json
import threading
import websocket


class WebSocketClient:
    def __init__(self, callback):
        self.callback = callback
        self.ws = None
        self.thread = None
        self.running = False

    def start(self, symbol="btcusdt", interval="1m"):
        url = f"wss://stream.binance.com:9443/ws/{symbol}@kline_{interval}"

        self.running = True

        def run():
            self.ws = websocket.WebSocketApp(
                url,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            self.ws.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.ws:
            self.ws.close()

    def on_message(self, ws, message):
        if not self.running:
            return

        data = json.loads(message)
        k = data["k"]

        candle = {
            "time": int(k["t"] / 1000),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"])
        }

        self.callback(candle)

    def on_error(self, ws, error):
        print("WebSocket Error:", error)

    def on_close(self, ws, close_status_code, close_msg):
        print("WebSocket Closed")

