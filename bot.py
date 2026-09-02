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

def send_signal(pair, direction, tf):
  print(f'✅ Quotex Signal Sent: {pair} -> {direction} ({tf})')

  # 1. Trigger Pusher for Netlify Dashboard
  try:
    pusher_client.trigger(
        'trading-signals',
        'new-signal',
        {'pair': pair, 'direction': direction, 'timeframe': tf},
    )
  except Exception as e:
    print(f'⚠️ Pusher error: {e}')

  # 2. Send Telegram notification (Timeout enforced to prevent stalls)
  try:
    message = (
        f'🚨 Quotex Trading Signal!\n\nCurrency: {pair}\nDirection:'
        f' {direction} (CALL/PUT)\nTimeframe: {tf}'
    )
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message}
    requests.post(url, json=payload, timeout=8)
  except Exception as e:
    print(f'⚠️ Telegram error: {e}')


def analyze_market():
  global last_signals

  for yahoo_symbol, display_name in forex_pairs.items():
    try:
      # Explicit timeout prevents the request from hanging indefinitely on cloud hosts
      df = yf.download(
          yahoo_symbol, period='1d', interval='1m', progress=False, timeout=10
      )

      if df is None or df.empty or len(df) < 25:
        continue

      # Flatten MultiIndex columns if returned by newer yfinance versions
      if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

      # Ensure Close series is 1-dimensional
      close_series = df['Close']
      if isinstance(close_series, pd.DataFrame):
        close_series = close_series.iloc[:, 0]

      # Technical Calculations (RSI 14 & Bollinger Bands 20, 2)
      delta = close_series.diff()
      gain = (delta.where(delta > 0, 0.0)).rolling(window=14).mean()
      loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()

      # Prevent division by zero errors
      rs = gain / loss.replace(0, np.nan)
      rsi_series = 100 - (100 / (1 + rs))
      rsi_series = rsi_series.fillna(50.0)

      ma20 = close_series.rolling(window=20).mean()
      std20 = close_series.rolling(window=20).std()
      lower_bb = ma20 - (std20 * 2)
      upper_bb = ma20 + (std20 * 2)

      current_price = float(close_series.iloc[-1])
      current_rsi = float(rsi_series.iloc[-1])
      lower_band = float(lower_bb.iloc[-1])
      upper_band = float(upper_bb.iloc[-1])

      print(
          f'Scanning {display_name} | Price: {current_price:.5f} | RSI:'
          f' {current_rsi:.2f}'
      )

      current_last_signal = last_signals.get(display_name, None)

      if (
          current_rsi < 30
          and current_price <= lower_band
          and current_last_signal != 'CALL'
      ):
        send_signal(display_name, 'CALL', '1 Min')
        last_signals[display_name] = 'CALL'

      elif (
          current_rsi > 70
          and current_price >= upper_band
          and current_last_signal != 'PUT'
      ):
        send_signal(display_name, 'PUT', '1 Min')
        last_signals[display_name] = 'PUT'

      elif 40 < current_rsi < 60:
        last_signals[display_name] = None

    except Exception as e:
      print(f'⚠️ Error on {display_name}: {e}')

    # Short pause to prevent Yahoo Finance HTTP 429 rate-limiting
    time.sleep(1.5)


def run_bot():
  print('🤖 Multi-Currency Quotex Bot Started with Telegram & Pusher!')
  time.sleep(3)

  # Master loop: prevents unexpected errors from permanently killing the scanner thread
  while True:
    try:
      analyze_market()
    except Exception as fatal_err:
      print(f'🚨 Critical error caught in scanner loop: {fatal_err}')
      traceback.print_exc()
      time.sleep(10)

    time.sleep(20)


@app.route('/')
def alive():
  return 'Quotex Multi-Currency Bot with Telegram is alive and running 24/7!'


if __name__ == '__main__':
  bot_thread = threading.Thread(target=run_bot, daemon=True)
  bot_thread.start()

  port = int(os.environ.get('PORT', 5000))
  app.run(host='0.0.0.0', port=port)
