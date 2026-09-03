import pandas as pd
import pusher
import time
import os
import threading
from flask import Flask
import yfinance as yf
import requests

# Flask Web Server Setup (Required for Render uptime)
app = Flask(__name__)

# --- 1. API Keys & Setup ---
PUSHER_APP_ID = '2190746'
PUSHER_KEY = 'f6d226d63552173e92b9'
PUSHER_SECRET = '0ab61f388d482d06c232'

# 🚨 Place your newly generated Telegram token here
TELEGRAM_BOT_TOKEN = '8893372314:AAEIf8UbuT1_WMYfqPTBpXCtWJLEmrvJIR4' 
TELEGRAM_CHAT_ID = '-1004489990906'

# --- 2. Pusher Client Setup ---
pusher_client = pusher.Pusher(
    app_id=PUSHER_APP_ID,
    key=PUSHER_KEY,
    secret=PUSHER_SECRET,
    cluster='ap2',
    ssl=True
)

# --- 3. Multi-Currency Quotex Forex List (20 Pairs) ---
forex_pairs = {
    'EURUSD=X': 'EUR/USD',
    'GBPJPY=X': 'GBP/JPY',
    'USDJPY=X': 'USD/JPY',
    'AUDJPY=X': 'AUD/JPY',
    'EURJPY=X': 'EUR/JPY',
    'AUDCAD=X': 'AUD/CAD',
    'CADJPY=X': 'CAD/JPY',
    'CHFJPY=X': 'CHF/JPY',
    'EURAUD=X': 'EUR/AUD',
    'GBPCAD=X': 'GBP/CAD',
    'AUDCHF=X': 'AUD/CHF',
    'EURCAD=X': 'EUR/CAD',
    'EURCHF=X': 'EUR/CHF',
    'GBPUSD=X': 'GBP/USD',
    'USDCAD=X': 'USD/CAD',
    'AUDUSD=X': 'AUD/USD',
    'GBPAUD=X': 'GBP/AUD',
    'EURGBP=X': 'EUR/GBP',
    'GBPCHF=X': 'GBP/CHF',
    'USDCHF=X': 'USD/CHF'
}

last_signals = {}

def send_signal(pair, direction, tf, strategy_name):
    print(f"\n✅ SIGNAL DETECTED: {pair} -> {direction} ({strategy_name})")
    
    # 1. Send signal to Web Dashboard (Pusher)
    try:
        pusher_client.trigger('trading-signals', 'new-signal', {
            'pair': pair,
            'direction': direction,
            'timeframe': tf,
            'strategy': strategy_name
        })
    except Exception as e:
        print(f"   -> ❌ Pusher error: {e}")
    
    # 2. Send signal to Telegram Channel
    try:
        message = f"🚨 *Quotex Trading Signal!*\n\n💱 Currency: {pair}\n📈 Direction: *{direction}* (CALL/PUT)\n⏱ Timeframe: {tf}\n🧠 Strategy: {strategy_name}"
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code != 200:
            print(f"   -> ❌ Telegram Failed! Code: {response.status_code}, Reason: {response.text}")
        else:
            print("   -> ✅ Telegram message sent successfully!")
            
    except Exception as e:
        print(f"   -> ❌ Telegram Network Error: {e}")

def analyze_market():
    global last_signals
    
    for yahoo_symbol, display_name in forex_pairs.items():
        try:
            # Download 1-minute data
            df = yf.download(yahoo_symbol, period='1d', interval='1m', progress=False)
            
            if df.empty or len(df) < 30:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # --- 1. Bollinger Bands (20, 2) ---
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['STD20'] = df['Close'].rolling(window=20).std()
            df['Lower_BB'] = df['MA20'] - (df['STD20'] * 2)
            df['Upper_BB'] = df['MA20'] + (df['STD20'] * 2)

            # --- 2. MACD (12, 26, 9) ---
            exp1 = df['Close'].ewm(span=12, adjust=False).mean()
            exp2 = df['Close'].ewm(span=26, adjust=False).mean()
            df['MACD'] = exp1 - exp2
            df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()

            # --- 3. Price Action (Engulfing & Pin Bar) ---
            df['Prev_Open'] = df['Open'].shift(1)
            df['Prev_Close'] = df['Close'].shift(1)
            
            # Bullish & Bearish Engulfing Logic
            bullish_engulfing = (df['Prev_Close'] < df['Prev_Open']) & (df['Close'] > df['Open']) & (df['Close'] > df['Prev_Open']) & (df['Open'] < df['Prev_Close'])
            bearish_engulfing = (df['Prev_Close'] > df['Prev_Open']) & (df['Close'] < df['Open']) & (df['Close'] < df['Prev_Open']) & (df['Open'] > df['Prev_Close'])
            
            # Pin Bar Logic (Hammer / Shooting Star)
            body = abs(df['Close'] - df['Open'])
            lower_wick = df[['Open', 'Close']].min(axis=1) - df['Low']
            upper_wick = df['High'] - df[['Open', 'Close']].max(axis=1)
            
            bullish_pinbar = (lower_wick > (2 * body)) & (upper_wick < body)
            bearish_pinbar = (upper_wick > (2 * body)) & (lower_wick < body)

            # --- Latest Market Values ---
            curr_low = float(df['Low'].iloc[-1])
            curr_high = float(df['High'].iloc[-1])
            lower_bb = float(df['Lower_BB'].iloc[-1])
            upper_bb = float(df['Upper_BB'].iloc[-1])
            
            # MACD Crossover check
            macd_cross_up = (df['MACD'].iloc[-1] > df['Signal_Line'].iloc[-1]) and (df['MACD'].iloc[-2] <= df['Signal_Line'].iloc[-2])
            macd_cross_down = (df['MACD'].iloc[-1] < df['Signal_Line'].iloc[-1]) and (df['MACD'].iloc[-2] >= df['Signal_Line'].iloc[-2])

            # Price Action Check
            pa_bullish = bullish_engulfing.iloc[-1] or bullish_pinbar.iloc[-1]
            pa_bearish = bearish_engulfing.iloc[-1] or bearish_pinbar.iloc[-1]

            print(f"Scanning {display_name} | Price: {float(df['Close'].iloc[-1]):.5f} | Hybrid Engine Active")

            current_last_signal = last_signals.get(display_name, None)

            # ==========================================
            # 🚀 THE HYBRID SIGNAL LOGIC (OR CONDITION)
            # ==========================================
            
            # CALL SIGNAL (If price drops to lower band)
            if curr_low <= lower_bb and current_last_signal != "CALL":
                if macd_cross_up:
                    send_signal(display_name, "CALL", "1 Min", "MACD Reversal")
                    last_signals[display_name] = "CALL"
                elif pa_bullish:
                    send_signal(display_name, "CALL", "1 Min", "Price Action (Bullish)")
                    last_signals[display_name] = "CALL"
                    
            # PUT SIGNAL (If price rises to upper band)
            elif curr_high >= upper_bb and current_last_signal != "PUT":
                if macd_cross_down:
                    send_signal(display_name, "PUT", "1 Min", "MACD Reversal")
                    last_signals[display_name] = "PUT"
                elif pa_bearish:
                    send_signal(display_name, "PUT", "1 Min", "Price Action (Bearish)")
                    last_signals[display_name] = "PUT"
            
            # Reset Logic (Ready for a new signal once price enters safely inside the bands)
            elif lower_bb < float(df['Close'].iloc[-1]) < upper_bb:
                last_signals[display_name] = None 

            # Delay to prevent Yahoo Finance IP Ban (Total ~40 seconds for 20 pairs)
            time.sleep(2) 

        except Exception as e:
            print(f"Error on {display_name}: {e}")

def run_bot():
    print("🤖 Ultimate Hybrid Quotex Bot Started (BB + MACD + Price Action, 20 Pairs)!")
    while True:
        analyze_market()
        print("--- Waiting 20 seconds to complete the 1-minute cycle ---")
        time.sleep(20) # Optimized from 60 to 20 for perfect 1-minute loops

# =====================================================================
# 🚨 GUNICORN FIX: Start the background thread OUTSIDE if __name__ == "__main__"
# =====================================================================
bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()

@app.route('/')
def alive():
    return "Quotex Ultimate Hybrid Bot is alive and running 24/7!"

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
