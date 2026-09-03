import os
import time
import threading
import pandas as pd
import requests
import pusher
from flask import Flask
from pyquotex import Quotex

# --- Flask Server Setup (Keeps Render Web Service alive 24/7) ---
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

# --- Quotex Account Credentials ---
QUOTEX_EMAIL = os.environ.get('QUOTEX_EMAIL', 'your_email@example.com')
QUOTEX_PASSWORD = os.environ.get('QUOTEX_PASSWORD', 'your_password')

# --- Assets List (Scans Live during regular hours, switches to OTC automatically) ---
forex_pairs = [
    "EURUSD", "GBPJPY", "USDJPY", "AUDJPY", "EURJPY",
    "AUDCAD", "CADJPY", "CHFJPY", "EURAUD", "GBPCAD",
    "AUDCHF", "EURCAD", "EURCHF", "GBPUSD", "USDCAD"
]

last_signals = {}


def send_signal(pair, direction, tf, strategy_name):
    """Dispatches trading signals to Pusher Dashboard and Telegram Channel."""
    print(f"\n[SIGNAL] {pair} -> {direction} ({strategy_name})")

    # 1. Send signal to Web Dashboard (Pusher)
    try:
        pusher_client.trigger('trading-signals', 'new-signal', {
            'pair': pair,
            'direction': direction,
            'timeframe': tf,
            'strategy': strategy_name
        })
    except Exception as e:
        print(f"   -> Pusher Error: {e}")

    # 2. Send signal to Telegram Channel
    try:
        message = (
            f"🚨 *Quotex Pure Price Action Signal!*\n\n"
            f"💱 *Pair:* {pair}\n"
            f"📈 *Direction:* {direction} (CALL/PUT)\n"
            f"⏱ *Timeframe:* {tf}\n"
            f"🧠 *Trigger:* {strategy_name}"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"   -> Telegram API Failed: {response.status_code} - {response.text}")
        else:
            print("   -> Telegram alert delivered successfully.")
    except Exception as e:
        print(f"   -> Telegram Request Exception: {e}")


def run_price_action(client):
    """Fetches real-time Quotex candle data and executes the Price Action engine."""
    global last_signals

    for base_pair in forex_pairs:
        try:
            # First attempt: standard regular market asset
            pair_name = base_pair
            candles = client.get_candles(pair_name, 60, 30)  # 30 candles on 60s timeframe

            # Fallback: if regular asset has no feed (e.g. weekend/after-hours), query the OTC asset
            if not candles:
                pair_name = f"{base_pair}_otc"
                candles = client.get_candles(pair_name, 60, 30)

            if not candles or len(candles) < 25:
                continue

            # Convert fetched candles to a Pandas DataFrame
            df = pd.DataFrame(candles)

            # Standardize OHLC column headers across pyquotex versions
            df.rename(
                columns={'max': 'High', 'min': 'Low', 'open': 'Open', 'close': 'Close'},
                inplace=True
            )

            # --- 1. Dynamic Support & Resistance (Last 20 Candles) ---
            df['Support'] = df['Low'].shift(1).rolling(window=20).min()
            df['Resistance'] = df['High'].shift(1).rolling(window=20).max()

            # --- 2. Candlestick Dimensional Properties ---
            df['Prev_Open'] = df['Open'].shift(1)
            df['Prev_Close'] = df['Close'].shift(1)

            body = (df['Close'] - df['Open']).abs()
            total_range = df['High'] - df['Low']

            # --- 3. Patterns Recognition ---
            bullish_engulfing = (
                (df['Prev_Close'] < df['Prev_Open']) &
                (df['Close'] > df['Open']) &
                (df['Close'] > df['Prev_Open']) &
                (df['Open'] < df['Prev_Close'])
            )

            bearish_engulfing = (
                (df['Prev_Close'] > df['Prev_Open']) &
                (df['Close'] < df['Open']) &
                (df['Close'] < df['Prev_Open']) &
                (df['Open'] > df['Prev_Close'])
            )

            lower_wick = df[['Open', 'Close']].min(axis=1) - df['Low']
            upper_wick = df['High'] - df[['Open', 'Close']].max(axis=1)

            bullish_pinbar = (lower_wick > (2 * body)) & (upper_wick < (body * 0.5))
            bearish_pinbar = (upper_wick > (2 * body)) & (lower_wick < (body * 0.5))

            bullish_pressure = (df['Close'] > df['Open']) & (body > (total_range * 0.5)) & (upper_wick < (body * 0.2))
            bearish_pressure = (df['Open'] > df['Close']) & (body > (total_range * 0.5)) & (lower_wick < (body * 0.2))

            # Current values
            curr_low = float(df['Low'].iloc[-1])
            curr_high = float(df['High'].iloc[-1])
            curr_close = float(df['Close'].iloc[-1])
            support = float(df['Support'].iloc[-1])
            resistance = float(df['Resistance'].iloc[-1])

            # Buffer threshold to identify zone interaction (0.02%)
            buffer = curr_close * 0.0002
            at_support = curr_low <= (support + buffer)
            at_resistance = curr_high >= (resistance - buffer)

            print(f"Scanning {pair_name} | Price: {curr_close:.5f} | PA Engine Active")

            curr_last = last_signals.get(pair_name, None)

            # --- 4. Signal Trigger Logic ---
            # CALL Signals (At Support)
            if at_support and curr_last != "CALL":
                if bullish_engulfing.iloc[-1]:
                    send_signal(pair_name, "CALL", "1 Min", "Support + Bullish Engulfing")
                    last_signals[pair_name] = "CALL"
                elif bullish_pinbar.iloc[-1]:
                    send_signal(pair_name, "CALL", "1 Min", "Support + Bullish Pin Bar")
                    last_signals[pair_name] = "CALL"
                elif bullish_pressure.iloc[-1]:
                    send_signal(pair_name, "CALL", "1 Min", "Support + Bullish Pressure")
                    last_signals[pair_name] = "CALL"

            # PUT Signals (At Resistance)
            elif at_resistance and curr_last != "PUT":
                if bearish_engulfing.iloc[-1]:
                    send_signal(pair_name, "PUT", "1 Min", "Resistance + Bearish Engulfing")
                    last_signals[pair_name] = "PUT"
                elif bearish_pinbar.iloc[-1]:
                    send_signal(pair_name, "PUT", "1 Min", "Resistance + Bearish Pin Bar")
                    last_signals[pair_name] = "PUT"
                elif bearish_pressure.iloc[-1]:
                    send_signal(pair_name, "PUT", "1 Min", "Resistance + Bearish Pressure")
                    last_signals[pair_name] = "PUT"

            # Reset signal status once price leaves S/R territory
            elif not at_support and not at_resistance:
                last_signals[pair_name] = None

            time.sleep(0.3)

        except Exception as e:
            print(f"Error scanning {base_pair}: {e}")


def run_bot():
    """Initializes Quotex WebSocket gateway and runs the background loop."""
    print("[INIT] Connecting to Quotex WebSocket Gateway...")
    client = Quotex(email=QUOTEX_EMAIL, password=QUOTEX_PASSWORD)
    connected, reason = client.connect()

    if not connected:
        print(f"[ERROR] Connection to Quotex failed: {reason}")
        return

    print("[STATUS] Connected successfully to Quotex! Starting Real-time Engine...")
    while True:
        try:
            run_price_action(client)
            time.sleep(10)  # Interval between scanner sweeps
        except Exception as err:
            print(f"[LOOP EXCEPTION] Engine error: {err}")
            time.sleep(5)


# --- Background Worker Thread for Gunicorn / Render compatibility ---
bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()


@app.route('/')
def health_check():
    return "Quotex Pure Price Action Engine is alive and running 24/7!", 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
