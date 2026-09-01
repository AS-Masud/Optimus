import ccxt
import pandas as pd
import pusher
import time
import os
import threading
from flask import Flask

# Flask Web Server Setup (Required for Render)
app = Flask(__name__)

# --- 1. Pusher Setup ---
pusher_client = pusher.Pusher(
  app_id='2190746',
  key='f6d226d63552173e92b9',
  secret='0ab61f388d482d06c232',
  cluster='ap2',
  ssl=True
)

# --- 2. Binance Setup ---
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot'
    }
})
symbol = 'BTC/USDT'
timeframe = '1m'     
last_signal = None   

def send_signal(pair, direction, tf):
    print(f"✅ Advanced Signal Sent: {pair} -> {direction} ({tf})")
    pusher_client.trigger('trading-signals', 'new-signal', {
        'pair': pair,
        'direction': direction,
        'timeframe': tf
    })

def analyze_market():
    global last_signal
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # --- Pure Pandas Technical Calculations (No external pandas-ta needed) ---
        # 1. Calculate RSI (14)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))

        # 2. Calculate Bollinger Bands (20, 2)
        df['MA20'] = df['close'].rolling(window=20).mean()
        df['STD20'] = df['close'].rolling(window=20).std()
        df['Lower_BB'] = df['MA20'] - (df['STD20'] * 2)
        df['Upper_BB'] = df['MA20'] + (df['STD20'] * 2)
        
        current_price = df['close'].iloc[-1]
        current_rsi = df['RSI_14'].iloc[-1]
        lower_band = df['Lower_BB'].iloc[-1]
        upper_band = df['Upper_BB'].iloc[-1]
        
        print(f"Live... Price: {current_price} | RSI: {current_rsi:.2f} | Lower BB: {lower_band:.2f} | Upper BB: {upper_band:.2f}")

        if current_rsi < 30 and current_price <= lower_band and last_signal != "CALL":
            send_signal(symbol, "CALL", "1 Minute")
            last_signal = "CALL"
            time.sleep(60) 
            
        elif current_rsi > 70 and current_price >= upper_band and last_signal != "PUT":
            send_signal(symbol, "PUT", "1 Minute")
            last_signal = "PUT"
            time.sleep(60)
            
        elif 40 < current_rsi < 60:
            last_signal = None 

    except Exception as e:
        print(f"Error fetching data: {e}")

def run_bot():
    print(f"🤖 Background Bot Started! Monitoring {symbol}...")
    while True:
        analyze_market()
        time.sleep(10)

@app.route('/')
def alive():
    return "Bot is alive and running 24/7!"

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
