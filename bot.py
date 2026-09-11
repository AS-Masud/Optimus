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

DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"

# --- State Management & Duplicate Protection (PAIR + TRADE_CANDLE_TIMESTAMP) ---
pair_states = {}
sent_signals_tracker = set()

for pair in forex_pairs.keys():
    pair_states[pair] = {
        "active_trade_candle": None,
        "signal_direction": "NO TRADE",
        "strategy": None,
        "entry_time": None,
        "expiry_time": None,
        "trade_candle_str": None,
        "is_locked": False,
        "notification_sent": False
    }


def format_time(epoch):
    """Converts epoch timestamp to HH:MM:SS format."""
    return time.strftime('%H:%M:%S', time.gmtime(epoch))


def send_signal(pair, direction, entry_epoch, expiry_epoch, strategy_name, price):
    """Sends strict locked trading signals to Telegram and Pusher matching exact formatting rules."""
    clean_pair = pair.replace("/", "")
    entry_str = format_time(entry_epoch)
    expiry_str = format_time(expiry_epoch)
    trade_candle_str = f"{entry_str} -> {format_time(expiry_epoch - 1)}"
    direction_emoji = "📈" if direction == "CALL" else "📉"

    print(f"\n[SIGNAL LOCKED] {clean_pair} | Direction: {direction} | Entry: {entry_str} | Expiry: {expiry_str}", flush=True)

    # 1. Pusher Notification
    try:
        pusher_client.trigger('trading-signals', 'new-signal', {
            'pair': clean_pair,
            'direction': direction,
            'entry_time': entry_str,
            'expiry_time': expiry_str,
            'trade_candle': trade_candle_str,
            'strategy': strategy_name,
            'price': price
        })
    except Exception as e:
        print(f"Pusher error: {e}", flush=True)

    # 2. Telegram Alert (Strict Mandated Format)
    try:
        message = (
            f"🎯 *NEXT 1-MINUTE SIGNAL*\n\n"
            f"💱 *Pair:* {clean_pair}\n"
            f"{direction_emoji} *Direction:* {direction}\n"
            f"🕐 *ENTRY:* `{entry_str}`\n"
            f"⏳ *EXPIRY:* `{expiry_str}`\n"
            f"📊 *TRADE CANDLE:*\n"
            f"`{trade_candle_str}`\n\n"
            f"🧠 *Strategy:*\n"
            f"{strategy_name}\n\n"
            f"🔒 *STATUS:* LOCKED"
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


def analyze_and_lock(display_name, candles, current_trade_candle_epoch):
    """Applies strategy on completed analysis candle and locks state for the next trade candle without mid-candle flipping."""
    global pair_states, sent_signals_tracker

    state = pair_states[display_name]
    clean_pair = display_name.replace("/", "")
    signal_key = f"{clean_pair}_{current_trade_candle_epoch}"

    # If already locked or evaluated for this exact trade candle, bypass to prevent duplicate/opposite triggers
    if state["active_trade_candle"] == current_trade_candle_epoch and state["is_locked"]:
        return

    # New Trade Candle started -> Reset or Initialize State
    if state["active_trade_candle"] != current_trade_candle_epoch:
        print(f"\n[NEW CANDLE] {display_name} | Trade Candle: {format_time(current_trade_candle_epoch)}", flush=True)
        state["active_trade_candle"] = current_trade_candle_epoch
        state["is_locked"] = False
        state["notification_sent"] = False
        state["signal_direction"] = "NO TRADE"

    if len(candles) < 30:
        return

    df = pd.DataFrame(candles)
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)

    # 1. Dynamic Support & Resistance (Look-ahead bias prevented by shifting)
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

    analysis_candle_time = format_time(int(df['epoch'].iloc[-1]))
    print(f"[CANDLE] {display_name:<7} | Analysis Candle: {analysis_candle_time} | Close: {curr_close:<9.5f} | RSI: {rsi:<4.1f}", flush=True)

    # Confluence Checks for Next Candle Entry
    direction = "NO TRADE"
    strategy = None

    if at_support and curr_close > ema and rsi < 35 and bullish_rejection.iloc[-1] and is_valid_body:
        direction = "CALL"
        strategy = "Support Bounce + EMA100 + RSI + Bullish Rejection"
    elif at_resistance and curr_close < ema and rsi > 65 and bearish_rejection.iloc[-1] and is_valid_body:
        direction = "PUT"
        strategy = "Resistance Rejection + EMA100 + RSI + Bearish Rejection"

    # Lock state for this trade candle
    state["signal_direction"] = direction
    state["strategy"] = strategy
    state["entry_time"] = current_trade_candle_epoch
    state["expiry_time"] = current_trade_candle_epoch + 60
    state["is_locked"] = True

    if direction != "NO TRADE":
        # Strict Duplicate Protection Check via PAIR + TRADE_CANDLE_TIMESTAMP key
        if signal_key not in sent_signals_tracker:
            sent_signals_tracker.add(signal_key)
            print(f"[SIGNAL READY] {display_name} {direction} | Trade Candle: {format_time(current_trade_candle_epoch)}", flush=True)
            send_signal(display_name, direction, state["entry_time"], state["expiry_time"], strategy, curr_close)
            state["notification_sent"] = True
        else:
            print(f"[DUPLICATE BLOCKED] Signal already sent for {signal_key}. Ignored.", flush=True)
    else:
        print(f"[NO TRADE] {display_name} - Criteria not met. No Telegram signal sent.", flush=True)


def fetch_pair_data(ws, display_name, symbol, current_trade_epoch):
    """Requests and processes historical candles for a single pair using persistent WS connection."""
    try:
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

        if "candles" in data:
            analyze_and_lock(display_name, data["candles"], current_trade_epoch)
        elif "error" in data:
            print(f"[DERIV ERROR] {display_name}: {data['error'].get('message')}", flush=True)
    except Exception as e:
        print(f"[FETCH ERROR] {display_name}: {e}", flush=True)


def background_scanner():
    """Main background loop utilizing a persistent WebSocket connection with precise candle boundary synchronization."""
    print("[ACTIVE] Deriv 20+ Forex Pairs Next-Candle Precision Scanner Running...", flush=True)
    
    while True:
        try:
            ws = websocket.create_connection(DERIV_WS_URL, timeout=10)
            print("[WS CONNECTED] Established persistent connection to Deriv API.", flush=True)
            
            last_scanned_candle_epoch = 0

            while ws.connected:
                now_epoch = int(time.time())
                # Exact 1-minute candle boundary calculation (timestamp // 60 * 60)
                current_trade_candle_epoch = (now_epoch // 60) * 60

                # Scan only once per new candle boundary to avoid redundant requests
                if current_trade_candle_epoch != last_scanned_candle_epoch:
                    last_scanned_candle_epoch = current_trade_candle_epoch
                    
                    for display_name, symbol in forex_pairs.items():
                        fetch_pair_data(ws, display_name, symbol, current_trade_candle_epoch)
                        time.sleep(0.2)  # Short throttle between pairs to respect API limits

                # Sleep briefly until close to the next second
                time.sleep(0.5)

        except Exception as e:
            print(f"[WS CONNECTION LOST]: {e}. Reconnecting in 3 seconds...", flush=True)
            time.sleep(3)


# --- Production Safe Thread Initialization ---
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not os.environ.get("FLASK_RUN_FROM_CLI"):
    scanner_thread = threading.Thread(target=background_scanner, daemon=True)
    scanner_thread.start()


@app.route('/')
def health():
    return "Deriv Next-Candle Precision Trading Engine is Active!", 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
