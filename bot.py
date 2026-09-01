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

# --- 1. Pusher Setup (For Netlify Dashboard) ---
pusher_client = pusher.Pusher(
    app_id='2190746',
    key='f6d226d63552173e92b9',
    secret='0ab61f388d482d06c232',
    cluster='ap2',
    ssl=True
)

# --- 2. Telegram Setup (For Mobile Notifications) ---
TELEGRAM_BOT_TOKEN = 'YOUR_BOT_TOKEN_HERE'  # Enter your BotFather token here
TELEGRAM_CHAT_ID = 'YOUR_CHAT_ID_HERE'      # Enter your Telegram chat ID here

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

timeframe = '1m'     
last_signals = {}   # Track last signal per currency pair to avoid spamming

def send_signal(pair, direction, tf):
    print(f"✅ Quotex Signal Sent: {pair} -> {direction} ({tf})")
    
    # 1. Trigger Pusher for Netlify Dashboard
    try:
        pusher_client.trigger('trading-signals', 'new-signal', {
            'pair': pair,
            'direction': direction,
            'timeframe': tf
        })
    except Exception as e:
        print(f"Pusher error: {e}")
    
    # 2. Send direct Telegram notification to mobile
    try:
        message = f"🚨 Quotex Trading Signal!\n\nCurrency: {pair}\nDirection: {direction} (CALL/PUT)\nTimeframe: {tf}"
        url = f"https://api.telegram.org/bot{8893372314:AAEVyqxOmFmx-P0-tQlWq_nDNhsDWs9KK54}/sendMessage"
        payload = {
            'chat_id': -1004489990906,
            'text': message
        }
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

def analyze_market():
    global last_signals
    
    for yahoo_symbol, display_name in forex_pairs.items():
        try:
            df = yf.download(yahoo_symbol, period='1d', interval='1m', progress=False)
            
            if df.empty:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Technical Calculations (RSI 14 & Bollinger Bands 20, 2)
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI_14'] = 100 - (100 / (1 + rs))

            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['STD20'] = df['Close'].rolling(window=20).std()
            df['Lower_BB'] = df['MA20'] - (df['STD20'] * 2)
            df['Upper_BB'] = df['MA20'] + (df['STD20'] * 2)
            
            current_price = float(df['Close'].iloc[-1])
            current_rsi = float(df['RSI_14'].iloc[-1])
            lower_band = float(df['Lower_BB'].iloc[-1])
            upper_band = float(df['Upper_BB'].iloc[-1])
            
            print(f"Scanning {display_name} | Price: {current_price:.5f} | RSI: {current_rsi:.2f}")

            current_last_signal = last_signals.get(display_name, None)

            if current_rsi < 30 and current_price <= lower_band and current_last_signal != "CALL":
                send_signal(display_name, "CALL", "1 Min")
                last_signals[display_name] = "CALL"
                
            elif current_rsi > 70 and current_price >= upper_band and current_last_signal != "PUT":
                send_signal(display_name, "PUT", "1 Min")
                last_signals[display_name] = "PUT"
                
            elif 40 < current_rsi < 60:
                last_signals[display_name] = None 

            time.sleep(1)

        except Exception as e:
            print(f"Error on {display_name}: {e}")

def run_bot():
    print("🤖 Multi-Currency Quotex Bot Started with Telegram & Pusher!")
    while True:
        analyze_market()
        time.sleep(20)

@app.route('/')
def alive():
    return "Quotex Multi-Currency Bot with Telegram is alive and running 24/7!"

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
