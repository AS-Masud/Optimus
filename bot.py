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

# Your Telegram Bot Token & Chat ID
TELEGRAM_BOT_TOKEN = '8893372314:AAEIf8UbuT1_WMYfqPTBpXCtWJLEmrvJIR4'
TELEGRAM_CHAT_ID = '-1004489990906'

# --- 2. Pusher Client ---
pusher_client = pusher.Pusher(
    app_id=PUSHER_APP_ID,
    key=PUSHER_KEY,
    secret=PUSHER_SECRET,
    cluster='ap2',
    ssl=True
)

# --- 3. Multi-Currency Quotex Forex List (15 Pairs) ---
forex_pairs = {
    'EURUSD=X': 'EUR/USD',
    'GBPUSD=X': 'GBP/USD',
    'USDJPY=X': 'USD/JPY',
    'AUDUSD=X': 'AUD/USD',
    'USDCAD=X': 'USD/CAD',
    'NZDUSD=X': 'NZD/USD',
    'USDCHF=X': 'USD/CHF',
    'EURJPY=X': 'EUR/JPY',
    'GBPJPY=X': 'GBP/JPY',
    'EURGBP=X': 'EUR/GBP',
    'AUDJPY=X': 'AUD/JPY',
    'CADJPY=X': 'CAD/JPY',
    'CHFJPY=X': 'CHF/JPY',
    'EURAUD=X': 'EUR/AUD',
    'GBPAUD=X': 'GBP/AUD'
}

last_signals = {}   # Track last signal per currency pair to avoid spamming

def send_signal(pair, direction, tf):
    print(f"\n✅ SIGNAL DETECTED: {pair} -> {direction} ({tf})")
    
    # 1. Trigger Pusher for Netlify Dashboard
    try:
        pusher_client.trigger('trading-signals', 'new-signal', {
            'pair': pair,
            'direction': direction,
            'timeframe': tf
        })
        print("   -> Pusher notification sent.")
    except Exception as e:
        print(f"   -> ❌ Pusher error: {e}")
    
    # 2. Send direct Telegram notification
    try:
        message = f"🚨 *Quotex Trading Signal!*\n\n💱 Currency: {pair}\n📈 Direction: {direction} (CALL/PUT)\n⏱ Timeframe: {tf}"
        
        # Ensure the URL is formatted perfectly (no spaces, 'bot' touches the token)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        
        # Adding a timeout prevents the bot from hanging if Telegram servers are slow
        response = requests.post(url, json=payload, timeout=10)
        
        # Print EXACT error if Telegram rejects it
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
            # Download 1-minute data silently
            df = yf.download(yahoo_symbol, period='1d', interval='1m', progress=False)
            
            # Ensure we have enough data points (minimum 20 for Bollinger Bands)
            if df.empty or len(df) < 20:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # --- Technical Calculations ---
            
            # 1. Accurate RSI 14 (Using Wilder's Exponential Smoothing)
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, 0.0001) # prevent division by zero
            df['RSI_14'] = 100 - (100 / (1 + rs))

            # 2. Bollinger Bands (20, 2)
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['STD20'] = df['Close'].rolling(window=20).std()
            df['Lower_BB'] = df['MA20'] - (df['STD20'] * 2)
            df['Upper_BB'] = df['MA20'] + (df['STD20'] * 2)
            
            # Get latest values
            current_price = float(df['Close'].iloc[-1])
            current_rsi = float(df['RSI_14'].iloc[-1])
            lower_band = float(df['Lower_BB'].iloc[-1])
            upper_band = float(df['Upper_BB'].iloc[-1])
            
            print(f"Scanning {display_name} | Price: {current_price:.5f} | RSI: {current_rsi:.2f}")

            current_last_signal = last_signals.get(display_name, None)

            # --- Signal Logic ---
            if current_rsi < 30 and current_price <= lower_band and current_last_signal != "CALL":
                send_signal(display_name, "CALL", "1 Min")
                last_signals[display_name] = "CALL"
                
            elif current_rsi > 70 and current_price >= upper_band and current_last_signal != "PUT":
                send_signal(display_name, "PUT", "1 Min")
                last_signals[display_name] = "PUT"
                
            elif 40 < current_rsi < 60:
                last_signals[display_name] = None 

            # Sleep briefly to avoid Yahoo Finance IP ban
            time.sleep(2)

        except Exception as e:
            print(f"Error on {display_name}: {e}")

def run_bot():
    print("🤖 Multi-Currency Quotex Bot Started with Telegram & Pusher!")
    while True:
        analyze_market()
        print("--- Waiting 60 seconds for next 1-minute candle ---")
        time.sleep(60) # 1-minute candles only update every 60 seconds

@app.route('/')
def alive():
    return "Quotex Multi-Currency Bot with Telegram is alive and running 24/7!"

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
