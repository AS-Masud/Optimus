import pandas as pd
import pusher
import time
import os
import threading
from flask import Flask
import yfinance as yf
import requests

# Flask Web Server Setup (Required to keep Render server awake 24/7)
app = Flask(__name__)

# --- 1. API Keys & Setup ---
PUSHER_APP_ID = '2190746'
PUSHER_KEY = 'f6d226d63552173e92b9'
PUSHER_SECRET = '0ab61f388d482d06c232'

# 🚨 IMPORTANT: Place your newly generated Telegram token here
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
        message = f"🚨 *Quotex Pure Price Action!*\n\n💱 Currency: {pair}\n📈 Direction: *{direction}* (CALL/PUT)\n⏱ Timeframe: {tf}\n🧠 Trigger: {strategy_name}"
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
            
            if df.empty or len(df) < 25:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # --- 1. Dynamic Support & Resistance (Last 20 Candles) ---
            # Shifting by 1 so current candle doesn't affect established S/R
            df['Support'] = df['Low'].shift(1).rolling(window=20).min()
            df['Resistance'] = df['High'].shift(1).rolling(window=20).max()

            # --- 2. Candlestick Properties ---
            df['Prev_Open'] = df['Open'].shift(1)
            df['Prev_Close'] = df['Close'].shift(1)
            
            body = abs(df['Close'] - df['Open'])
            total_range = df['High'] - df['Low']
            
            # --- 3. Pattern: Engulfing ---
            bullish_engulfing = (df['Prev_Close'] < df['Prev_Open']) & (df['Close'] > df['Open']) & (df['Close'] > df['Prev_Open']) & (df['Open'] < df['Prev_Close'])
            bearish_engulfing = (df['Prev_Close'] > df['Prev_Open']) & (df['Close'] < df['Open']) & (df['Close'] < df['Prev_Open']) & (df['Open'] > df['Prev_Close'])
            
            # --- 4. Pattern: Pin Bar (Rejection) ---
            lower_wick = df[['Open', 'Close']].min(axis=1) - df['Low']
            upper_wick = df['High'] - df[['Open', 'Close']].max(axis=1)
            bullish_pinbar = (lower_wick > (2 * body)) & (upper_wick < (body * 0.5))
            bearish_pinbar = (upper_wick > (2 * body)) & (lower_wick < (body * 0.5))

            # --- 5. NEW: Candle Pressure (Momentum) ---
            # Bullish Pressure: Green candle, body is > 50% of range, very small upper wick (buyers in full control)
            bullish_pressure = (df['Close'] > df['Open']) & (body > (total_range * 0.5)) & (upper_wick < (body * 0.2))
            
            # Bearish Pressure: Red candle, body is > 50% of range, very small lower wick (sellers in full control)
            bearish_pressure = (df['Open'] > df['Close']) & (body > (total_range * 0.5)) & (lower_wick < (body * 0.2))

            # --- Current Market Values ---
            curr_low = float(df['Low'].iloc[-1])
            curr_high = float(df['High'].iloc[-1])
            curr_close = float(df['Close'].iloc[-1])
            
            support = float(df['Support'].iloc[-1])
            resistance = float(df['Resistance'].iloc[-1])
            
            # Create a tiny buffer zone (0.02%) around Support/Resistance to catch touches
            buffer = curr_close * 0.0002 
            
            at_support = curr_low <= (support + buffer)
            at_resistance = curr_high >= (resistance - buffer)

            print(f"Scanning {display_name} | Price: {curr_close:.5f} | PA Engine Active")

            current_last_signal = last_signals.get(display_name, None)

            # ==========================================
            # 🚀 PURE PRICE ACTION SIGNAL LOGIC
            # ==========================================
            
            # CALL SIGNAL (Price is at Support zone)
            if at_support and current_last_signal != "CALL":
                if bullish_engulfing.iloc[-1]:
                    send_signal(display_name, "CALL", "1 Min", "Support + Bullish Engulfing")
                    last_signals[display_name] = "CALL"
                elif bullish_pinbar.iloc[-1]:
                    send_signal(display_name, "CALL", "1 Min", "Support + Bullish Pin Bar")
                    last_signals[display_name] = "CALL"
                elif bullish_pressure.iloc[-1]:
                    send_signal(display_name, "CALL", "1 Min", "Support + Bullish Pressure")
                    last_signals[display_name] = "CALL"
                    
            # PUT SIGNAL (Price is at Resistance zone)
            elif at_resistance and current_last_signal != "PUT":
                if bearish_engulfing.iloc[-1]:
                    send_signal(display_name, "PUT", "1 Min", "Resistance + Bearish Engulfing")
                    last_signals[display_name] = "PUT"
                elif bearish_pinbar.iloc[-1]:
                    send_signal(display_name, "PUT", "1 Min", "Resistance + Bearish Pin Bar")
                    last_signals[display_name] = "PUT"
                elif bearish_pressure.iloc[-1]:
                    send_signal(display_name, "PUT", "1 Min", "Resistance + Bearish Pressure")
                    last_signals[display_name] = "PUT"
            
            # Reset Logic (Ready for next signal when price moves away from S/R)
            elif not at_support and not at_resistance:
                last_signals[display_name] = None 

            time.sleep(2) # Avoid Yahoo Finance IP Ban

        except Exception as e:
            print(f"Error on {display_name}: {e}")

def run_bot():
    print("🤖 Pure Price Action Bot Started (S/R + Patterns + Candle Pressure)!")
    while True:
        analyze_market()
        print("--- Waiting 20 seconds to complete the 1-minute cycle ---")
        time.sleep(20)

# =====================================================================
# 🚨 GUNICORN FIX: Start the background thread OUTSIDE if __name__ == "__main__"
# =====================================================================
bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()

@app.route('/')
def alive():
    return "Quotex Pure Price Action Bot is alive and running 24/7!"

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
