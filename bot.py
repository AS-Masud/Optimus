import os
import time
import json
import threading
import pandas as pd
import requests
import pusher
import websocket
from flask import Flask

# --- Flask Server Setup (Render Keep-Alive) ---
app = Flask(__name__)

# --- Pusher Configuration ---
PUSHER_APP_ID = os.environ.get('PUSHER_APP_ID', '2190746')
PUSHER_KEY = os.environ.get('PUSHER_KEY', 'f6d226d63552173e92b9')
PUSHER_SECRET = os.environ.get('PUSHER_SECRET', '0ab61f388d482d06c232')

pusher_client = pusher.Pusher(
    app_id=PUSHER_APP_ID,
    key=PUSHER_KEY,
    secret=PUSHER_SECRET,
    cluster='ap2',
    ssl=True
)

# --- Telegram Bot Setup ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8893372314:AAEIf8UbuT1_WMYfqPTBpXCtWJLEmrvJIR4')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '-1004489990906')

# --- All 21 Forex Pairs from Image (Deriv Symbols) ---
forex_pairs = {
    "EUR/JPY": "frxEURJPY",
    "EUR/USD": "frxEURUSD",
    "CAD/JPY": "frxCADJPY",
    "GBP/JPY": "frxGBPJPY",
    "GBP/AUD": "frxGBPAUD",
    "AUD/JPY": "frxAUDJPY",
    "AUD/USD": "frxAUDUSD",
    "CHF/JPY": "frxCHFJPY",
    "EUR/CHF": "frxEURCHF",
    "USD/JPY": "frxUSDJPY",
    "AUD/CAD": "frxAUDCAD",
    "EUR/CAD": "frxEURCAD",
    "EUR/AUD": "frxEURAUD",
    "GBP/CHF": "frxGBPCHF",
    "AUD/CHF": "frxAUDCHF",
    "EUR/GBP": "frxEURGBP",
    "GBP/CAD": "frxGBPCAD",
    "GBP/USD": "frxGBPUSD",
    "USD/CAD": "frxUSDCAD",
    "USD/CHF": "frxUSDCHF"
}

last_signals = {}
DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"


def send_signal(pair, direction, tf, strategy_name, price):
    """Sends trading signals to Telegram and Pusher."""
    clean_pair = pair.replace("/", "")
    print(f"\n[HIGH-PROBABILITY SIGNAL] {clean_pair} -> {direction} @ {price:.5f}", flush=True)

    # 1. Pusher Notification
    try:
        pusher_client.trigger('trading-signals', 'new-signal', {
            'pair': clean_pair,
            'direction': direction,
            'timeframe': tf,
            'strategy': strategy_name,
            'price': price
        })
    except Exception as e:
        print(f"Pusher error: {e}", flush=True)

    # 2. Telegram Alert
    try:
        message = (
            f"🎯 *Deriv Zero-Delay PA Signal!*\n\n"
            f"💱 *Pair:* {clean_pair}\n"
            f"📈 *Direction:* {direction} (CALL / PUT)\n"
            f"⏱ *Expiration:* {tf}\n"
            f"💵 *Price:* {price:.5f}\n"
            f"🧠 *Strategy:* {strategy_name}"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)


def calculate_rsi(series, period=14):
    """Calculates RSI value."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def analyze_candles(display_name, candles):
    """Applies 3-layer confluence strategy on 1m candles."""
    global last_signals

    if len(candles) < 30:
        return

    df = pd.DataFrame(candles)
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)

    # 1. Dynamic Support & Resistance
    df['Support'] = df['low'].shift(1).rolling(window=20).min()
    df['Resistance'] = df['high'].shift(1).rolling(window=20).max()

    # 2. Indicators
    df['RSI'] = calculate_rsi(df['close'], 14)
    df['EMA100'] = df['close'].ewm(span=100, adjust=False).mean()

    # Candlestick Anatomy
    body = (df['close'] - df['open']).abs()
    total_range = df['high'] - df['low']
    lower_wick = df[['open', 'close']].min(axis=1) - df['low']
    upper_wick = df['high'] - df[['open', 'close']].max(axis=1)

    bullish_rejection = lower_wick > (1.8 * body)
    bearish_rejection = upper_wick > (1.8 * body)
    is_valid_body = body.iloc[-1] > (total_range.iloc[-1] * 0.25)

    curr_close = float(df['close'].iloc[-1])
    curr_low = float(df['low'].iloc[-1])
    curr_high = float(df['high'].iloc[-1])
    support = float(df['Support'].iloc[-1])
    resistance = float(df['Resistance'].iloc[-1])
    rsi = float(df['RSI'].iloc[-1])
    ema = float(df['EMA100'].iloc[-1])

    buffer = curr_close * 0.00015
    at_support = curr_low <= (support + buffer)
    at_resistance = curr_high >= (resistance - buffer)

    print(f"[SCAN] {display_name:<7} | Price: {curr_close:<9.5f} | RSI: {rsi:<4.1f} | S: {support:<9.5f} | R: {resistance:<9.5f}", flush=True)

    curr_last = last_signals.get(display_name, None)

    # 3-Layer Confluence Verification
    if at_support and curr_close > ema and rsi < 35 and bullish_rejection.iloc[-1] and is_valid_body and curr_last != "CALL":
        strategy = "Support Bounce + Uptrend EMA + RSI Oversold + Hammer"
        send_signal(display_name, "CALL", "2-3 Min", strategy, curr_close)
        last_signals[display_name] = "CALL"

    elif at_resistance and curr_close < ema and rsi > 65 and bearish_rejection.iloc[-1] and is_valid_body and curr_last != "PUT":
        strategy = "Resistance Rejection + Downtrend EMA + RSI Overbought + Pinbar"
        send_signal(display_name, "PUT", "2-3 Min", strategy, curr_close)
        last_signals[display_name] = "PUT"

    elif not at_support and not at_resistance:
        last_signals[display_name] = None


def fetch_and_scan(display_name, symbol):
    """Fetches real-time candles via Deriv WebSocket."""
    try:
        ws = websocket.create_connection(DERIV_WS_URL, timeout=8)
        req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": 50,
            "end": "latest",
            "granularity": 60,
            "style": "candles"
        }
        ws.send(json.dumps(req))
        res = ws.recv()
        data = json.loads(res)
        ws.close()

        if "candles" in data:
            analyze_candles(display_name, data["candles"])
        elif "error" in data:
            print(f"[DERIV ERROR] {display_name}: {data['error'].get('message')}", flush=True)
    except Exception as e:
        print(f"[FETCH ERROR] {display_name}: {e}", flush=True)


def background_scanner():
    """Loops through all 20+ pairs continuously with optimized interval."""
    print("[ACTIVE] Deriv 20+ Forex Pairs Real-Time Scanner Running...", flush=True)
    while True:
        for display_name, symbol in forex_pairs.items():
            fetch_and_scan(display_name, symbol)
            time.sleep(0.5)  # Fast continuous loop across 20+ pairs
        time.sleep(1)


# Launch scanner thread
scanner_thread = threading.Thread(target=background_scanner)
scanner_thread.daemon = True
scanner_thread.start()


@app.route('/')
def health():
    return "Deriv 20+ Pairs Trading Engine is Active!", 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
