import os
import time
import threading
import pandas as pd
import requests
import pusher
from flask import Flask

# --- Flask Web Server ---
app = Flask(__name__)

# --- Pusher Setup ---
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

# --- TwelveData API Key (Sign up for free at twelvedata.com) ---
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY', '31c5e6f950c44a41a06b90dc6a57f8a2')

# --- Highly Liquid Forex Pairs (Slash format for TwelveData) ---
forex_pairs = [
    "EUR/USD", "GBP/JPY", "USD/JPY", "AUD/JPY", "EUR/JPY",
    "GBP/USD", "USD/CAD", "AUD/USD", "EUR/GBP"
]

last_signals = {}


def send_signal(pair, direction, tf, strategy_name, price):
    """Sends trading signal to Pusher and Telegram."""
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
            f"🎯 *High Probability PA Signal!*\n\n"
            f"💱 *Pair:* {clean_pair}\n"
            f"📈 *Direction:* {direction} (CALL / PUT)\n"
            f"⏱ *Expiration:* {tf}\n"
            f"💵 *Entry Price:* {price:.5f}\n"
            f"🧠 *Conditions:* {strategy_name}"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        print(f"Telegram alert error: {e}", flush=True)


def calculate_rsi(series, period=14):
    """Calculates relative strength index."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def scan_pair(pair):
    """Fetches real-time candles and validates 3-layer confluence."""
    global last_signals

    url = (
        f"https://api.twelvedata.com/time_series?"
        f"symbol={pair}&interval=1min&outputsize=45&apikey={TWELVE_DATA_API_KEY}"
    )

    try:
        response = requests.get(url, timeout=6)
        res_json = response.json()

        if "values" not in res_json:
            return

        # Prepare DataFrame
        df = pd.DataFrame(res_json["values"])
        df = df.iloc[::-1].reset_index(drop=True)  # Chronological order

        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)

        # 1. Dynamic Support & Resistance (Last 20 Candles)
        df['Support'] = df['low'].shift(1).rolling(window=20).min()
        df['Resistance'] = df['high'].shift(1).rolling(window=20).max()

        # 2. RSI Indicator
        df['RSI'] = calculate_rsi(df['close'], 14)

        # 3. EMA 50 Trend Direction
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()

        # Candlestick Shapes
        body = (df['close'] - df['open']).abs()
        lower_wick = df[['open', 'close']].min(axis=1) - df['low']
        upper_wick = df['high'] - df[['open', 'close']].max(axis=1)

        bullish_rejection = lower_wick > (1.8 * body)
        bearish_rejection = upper_wick > (1.8 * body)

        curr_close = df['close'].iloc[-1]
        curr_low = df['low'].iloc[-1]
        curr_high = df['high'].iloc[-1]
        support = df['Support'].iloc[-1]
        resistance = df['Resistance'].iloc[-1]
        rsi = df['RSI'].iloc[-1]
        ema = df['EMA50'].iloc[-1]

        buffer = curr_close * 0.00015
        at_support = curr_low <= (support + buffer)
        at_resistance = curr_high >= (resistance - buffer)

        curr_last = last_signals.get(pair, None)

        # --- High Win-Rate Setup Logic ---

        # 1. CALL Criteria: At Support + RSI Oversold (<38) + Strong Lower Wick Rejection
        if at_support and rsi < 38 and bullish_rejection.iloc[-1] and curr_last != "CALL":
            strategy = "Support Bounce + RSI Oversold + Hammer Rejection"
            send_signal(pair, "CALL", "1 Min", strategy, curr_close)
            last_signals[pair] = "CALL"

        # 2. PUT Criteria: At Resistance + RSI Overbought (>62) + Strong Upper Wick Rejection
        elif at_resistance and rsi > 62 and bearish_rejection.iloc[-1] and curr_last != "PUT":
            strategy = "Resistance Rejection + RSI Overbought + Shooting Star"
            send_signal(pair, "PUT", "1 Min", strategy, curr_close)
            last_signals[pair] = "PUT"

        elif not at_support and not at_resistance:
            last_signals[pair] = None

    except Exception as err:
        print(f"Error parsing {pair}: {err}", flush=True)


def background_scanner():
    """Main continuous scanner loop."""
    print("[ACTIVE] Real-time High Probability PA Bot Running...", flush=True)
    while True:
        for pair in forex_pairs:
            scan_pair(pair)
            time.sleep(1.2)  # Avoid rate limit on free API key
        time.sleep(3)


# Run background scanner thread
scanner_thread = threading.Thread(target=background_scanner)
scanner_thread.daemon = True
scanner_thread.start()


@app.route('/')
def health():
    return "Real-Time Trading Bot is Healthy and Active!", 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
