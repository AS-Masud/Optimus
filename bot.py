import os
import time
import threading
import pandas as pd
import requests
import pusher
from flask import Flask

# --- Flask Server Setup (Keep Render Alive) ---
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

# --- Telegram Bot Configuration ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8893372314:AAEIf8UbuT1_WMYfqPTBpXCtWJLEmrvJIR4')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '-1004489990906')

# --- Finnhub API Key ---
FINNHUB_API_KEY = os.environ.get('FINNHUB_API_KEY', 'daaqf5hr01qn50rj81a0daaqf5hr01qn50rj81ag')

# --- 10 Major Forex Pairs (Finnhub OANDA Symbol Format) ---
forex_pairs = {
    "EUR/USD": "OANDA:EUR_USD",
    "GBP/USD": "OANDA:GBP_USD",
    "USD/JPY": "OANDA:USD_JPY",
    "AUD/USD": "OANDA:AUD_USD",
    "USD/CAD": "OANDA:USD_CAD",
    "EUR/JPY": "OANDA:EUR_JPY",
    "GBP/JPY": "OANDA:GBP_JPY",
    "AUD/JPY": "OANDA:AUD_JPY",
    "EUR/GBP": "OANDA:EUR_GBP",
    "USD/CHF": "OANDA:USD_CHF"
}

last_signals = {}


def send_signal(pair, direction, tf, strategy_name, price):
    """Dispatches high-probability trading signals to Pusher and Telegram."""
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
            f"🎯 *Finnhub Live PA Signal!*\n\n"
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
        print(f"Telegram alert error: {e}", flush=True)


def calculate_rsi(series, period=14):
    """Calculates Relative Strength Index."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def scan_pair(display_name, symbol):
    """Fetches real-time 1m candles from Finnhub and executes 3-layer confluence checks."""
    global last_signals

    now = int(time.time())
    start_time = now - (60 * 60)  # Lookback past 60 minutes

    url = (
        f"https://finnhub.io/api/v1/forex/candle?"
        f"symbol={symbol}&resolution=1&from={start_time}&to={now}&token={FINNHUB_API_KEY}"
    )

    try:
        res = requests.get(url, timeout=10)
        data = res.json()

        if data.get('s') != 'ok':
            print(f"[DEBUG] {display_name} API Status: {data}", flush=True)
            return

        df = pd.DataFrame({
            'open': data['o'],
            'high': data['h'],
            'low': data['l'],
            'close': data['c'],
            'timestamp': data['t']
        })

        if len(df) < 25:
            print(f"[WAIT] {display_name}: Insufficient historical bars ({len(df)})", flush=True)
            return

        # 1. Dynamic Support & Resistance (Last 20 Candles)
        df['Support'] = df['low'].shift(1).rolling(window=20).min()
        df['Resistance'] = df['high'].shift(1).rolling(window=20).max()

        # 2. RSI Indicator (14 Period)
        df['RSI'] = calculate_rsi(df['close'], 14)

        # 3. EMA 100 Trend Direction
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

        print(f"[SCAN] {display_name} | Close: {curr_close:.5f} | RSI: {rsi:.1f} | Supp: {support:.5f} | Res: {resistance:.5f}", flush=True)

        curr_last = last_signals.get(display_name, None)

        # --- 3-Layer Confluence Signal Logic ---
        # 1. CALL Setup
        if at_support and curr_close > ema and rsi < 35 and bullish_rejection.iloc[-1] and is_valid_body and curr_last != "CALL":
            strategy = "Support Reversal + Uptrend EMA + RSI Oversold + Hammer"
            send_signal(display_name, "CALL", "2-3 Min", strategy, curr_close)
            last_signals[display_name] = "CALL"

        # 2. PUT Setup
        elif at_resistance and curr_close < ema and rsi > 65 and bearish_rejection.iloc[-1] and is_valid_body and curr_last != "PUT":
            strategy = "Resistance Reversal + Downtrend EMA + RSI Overbought + Shooting Star"
            send_signal(display_name, "PUT", "2-3 Min", strategy, curr_close)
            last_signals[display_name] = "PUT"

        elif not at_support and not at_resistance:
            last_signals[display_name] = None

    except requests.exceptions.Timeout:
        print(f"[TIMEOUT] {display_name}: Finnhub request timed out", flush=True)
    except Exception as err:
        print(f"[ERROR] {display_name}: {err}", flush=True)


def background_scanner():
    """Continuous scanner loop respecting Finnhub rate limits (60 requests/minute)."""
    print("[ACTIVE] Finnhub Real-time 24/7 Scanner Running...", flush=True)
    while True:
        for display_name, symbol in forex_pairs.items():
            scan_pair(display_name, symbol)
            time.sleep(1.2)
        time.sleep(2)


# Start worker thread
scanner_thread = threading.Thread(target=background_scanner)
scanner_thread.daemon = True
scanner_thread.start()


@app.route('/')
def health():
    return "Finnhub Live Trading Bot is Healthy and Active 24/7!", 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
