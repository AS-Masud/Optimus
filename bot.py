import ccxt
import pandas as pd
import pandas_ta as ta
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
        
        df.ta.rsi(length=14, append=True)
        bbands = df.ta.bbands(length=20, std=2) 
        
        current_price = df['close'].iloc[-1]
        current_rsi = df['RSI_14'].iloc[-1]
        lower_band = bbands.iloc[-1, 0]
        upper_band = bbands.iloc[-1, 2]
        
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

# Main bot loop running in the background
def run_bot():
    print(f"🤖 Background Bot Started! Monitoring {symbol}...")
    while True:
        analyze_market()
        time.sleep(10)

# Dummy web page to tell Render the app is healthy
@app.route('/')
def alive():
    return "Bot is alive and running 24/7!"

if __name__ == "__main__":
    # Start the trading bot in a separate background thread
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Start the Flask web server
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
