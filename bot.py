import os
import time
import asyncio
import threading
import pandas as pd
import requests
import pusher
from flask import Flask
from pyquotex.stable_api import Quotex
import pyquotex.api as quotex_api_module

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

# --- Quotex Account Credentials & Session ---
QUOTEX_EMAIL = os.environ.get('QUOTEX_EMAIL', 'user@example.com')
QUOTEX_PASSWORD = os.environ.get('QUOTEX_PASSWORD', 'dummy_pass')
QUOTEX_COOKIE = os.environ.get('QUOTEX_COOKIE', '')
USER_AGENT = os.environ.get(
    'USER_AGENT',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'
)

# --- Currency Pairs to Scan ---
forex_pairs = [
    "EURUSD", "GBPJPY", "USDJPY", "AUDJPY", "EURJPY",
    "AUDCAD", "CADJPY", "CHFJPY", "EURAUD", "GBPCAD",
    "AUDCHF", "EURCAD", "EURCHF", "GBPUSD", "USDCAD"
]

last_signals = {}


def parse_cookies(cookie_str):
    """Converts a raw cookie string into a dictionary."""
    cookie_dict = {}
    if cookie_str:
        for item in cookie_str.split(';'):
            item = item.strip()
            if '=' in item:
                key, val = item.split('=', 1)
                cookie_dict[key] = val
    return cookie_dict


def send_signal(pair, direction, tf, strategy_name):
    """Dispatches trading signals to Pusher Dashboard and Telegram Channel."""
    print(f"\n[SIGNAL] {pair} -> {direction} ({strategy_name})", flush=True)

    # 1. Pusher Dashboard Trigger
    try:
        pusher_client.trigger('trading-signals', 'new-signal', {
            'pair': pair,
            'direction': direction,
            'timeframe': tf,
            'strategy': strategy_name
        })
    except Exception as e:
        print(f"   -> Pusher Error: {e}", flush=True)

    # 2. Telegram Message Alert
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
            print(f"   -> Telegram API Failed: {response.status_code} - {response.text}", flush=True)
        else:
            print("   -> Telegram alert sent successfully.", flush=True)
    except Exception as e:
        print(f"   -> Telegram Network Exception: {e}", flush=True)


async def run_price_action(client):
    """Asynchronously fetches Quotex candles and executes Price Action scans."""
    global last_signals

    current_time = int(time.time())
    offset = 3600  # 1 hour lookback
    period = 60    # 1-minute candles

    for base_pair in forex_pairs:
        try:
            pair_name = base_pair
            candles = await client.get_candles(pair_name, current_time, offset, period)

            # Fall back to OTC if standard pair returns no candle data
            if not candles:
                pair_name = f"{base_pair}_otc"
                candles = await client.get_candles(pair_name, current_time, offset, period)

            if not candles:
                print(f"No candles returned for {base_pair} / {pair_name}", flush=True)
                continue

            if len(candles) < 25:
                print(f"Insufficient candles for {pair_name}: count={len(candles)}", flush=True)
                continue

            df = pd.DataFrame(candles)
            df.rename(
                columns={'max': 'High', 'min': 'Low', 'open': 'Open', 'close': 'Close'},
                inplace=True
            )

            # --- 1. Dynamic Support & Resistance ---
            df['Support'] = df['Low'].shift(1).rolling(window=20).min()
            df['Resistance'] = df['High'].shift(1).rolling(window=20).max()

            # --- 2. Candlestick Properties ---
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

            curr_low = float(df['Low'].iloc[-1])
            curr_high = float(df['High'].iloc[-1])
            curr_close = float(df['Close'].iloc[-1])
            support = float(df['Support'].iloc[-1])
            resistance = float(df['Resistance'].iloc[-1])

            buffer = curr_close * 0.0002
            at_support = curr_low <= (support + buffer)
            at_resistance = curr_high >= (resistance - buffer)

            print(f"Scanning {pair_name} | Price: {curr_close:.5f} | Engine Active", flush=True)

            curr_last = last_signals.get(pair_name, None)

            # --- 4. Signal Trigger Logic ---
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

            elif not at_support and not at_resistance:
                last_signals[pair_name] = None

            await asyncio.sleep(0.3)

        except Exception as e:
            print(f"Error scanning {base_pair}: {e}", flush=True)


async def async_run_bot():
    """Bypasses Cloudflare login scrapers using module-level method overrides."""
    print("[INIT] Connecting to Quotex WebSocket Gateway via Session...", flush=True)

    parsed_cookies = parse_cookies(QUOTEX_COOKIE)

    async def bypassed_authenticate(self):
        if hasattr(self, 'session') and self.session:
            self.session.headers.update({
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://market-qx.info/"
            })
            for k, v in parsed_cookies.items():
                self.session.cookies.set(k, v)

        if 'laravel_session' in parsed_cookies:
            self.token = parsed_cookies['laravel_session']

        return True, "Authenticated via Session"

    if hasattr(quotex_api_module, 'QuotexAPI'):
        quotex_api_module.QuotexAPI.authenticate = bypassed_authenticate

    client = Quotex(
        email=QUOTEX_EMAIL or "user@example.com",
        password=QUOTEX_PASSWORD or "dummy_pass"
    )

    connected, reason = await client.connect()

    if not connected and not getattr(client, 'check_connect', False):
        print(f"[ERROR] Connection to Quotex failed: {reason}", flush=True)
        return

    print("[STATUS] Connected successfully to Quotex! Starting Real-time Engine...", flush=True)
    while True:
        try:
            print("[SCAN CYCLE] Starting asset evaluation pass...", flush=True)
            await run_price_action(client)
            await asyncio.sleep(10)
        except Exception as err:
            print(f"[LOOP EXCEPTION] Engine error: {err}", flush=True)
            await asyncio.sleep(5)


def start_bot_thread():
    """Runs the asyncio loop inside a background worker thread."""
    asyncio.run(async_run_bot())


# Launch background thread
bot_thread = threading.Thread(target=start_bot_thread)
bot_thread.daemon = True
bot_thread.start()


@app.route('/')
def health_check():
    return "Quotex Real-Time PA Engine is alive and running 24/7!", 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
