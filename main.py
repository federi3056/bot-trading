import yfinance as yf
import yfinance_cache as yfc  # Nuova libreria di override anti-blocco
import requests
import pandas as pd
import time
from datetime import datetime
import pytz
from flask import Flask
from threading import Thread
import os

# --- INIZIALIZZAZIONE SERVER WEB PER RENDER ---
app = Flask('')

@app.route('/')
def home():
    return "Bot di Trading Intraday Alta Precisione Attivo!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- CONFIGURAZIONE TELEGRAM ---
TELEGRAM_TOKEN = "8820172406:AAE1Cewxm3qCOYmtKurcMw517AbH6-uqyic"
TELEGRAM_CHAT_ID = "1027014963"

# --- CONFIGURAZIONE ORARIA (Fuso Orario Italiano) ---
LOCAL_TZ = pytz.timezone("Europe/Rome")
START_HOUR = 9
END_HOUR = 23

# --- PANIERE DI 30 AZIONI (15 USA + 15 ITALIA) ---
TICKERS = [
    'AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'TSLA', 'GOOGL', 'BRK-B', 
    'AMD', 'NFLX', 'JPM', 'V', 'DIS', 'PLTR', 'XOM',
    'RACE.MI', 'STLAM.MI', 'ISP.MI', 'UCG.MI', 'ENI.MI', 'EGP.MI', 'G.MI', 
    'A2A.MI', 'PST.MI', 'TRN.MI', 'PRY.MI', 'MONC.MI', 'STM.MI', 'LDO.MI', 'CPR.MI'
]

# --- PARAMETRI STRATEGIA INTRADAY DIREZIONALE ---
TIMEFRAMES = ['15m', '30m']
BUY_LOW, BUY_HIGH = 0, 15
SELL_LOW, SELL_HIGH = 85, 100

def is_market_time():
    now = datetime.now(LOCAL_TZ)
    if now.weekday() > 4:  # Sabato e Domenica chiusi
        return False
    if START_HOUR <= now.hour < END_HOUR:
        return True
    return False

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            print("[OK] Notifica inviata con successo su Telegram!")
    except Exception as e:
        print(f"[ERRORE RETE TELEGRAM] {e}")

def calculate_vwap(df):
    """Calcola il VWAP su base intraday resettandolo ogni giorno."""
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    tp_v = typical_price * df['Volume']
    
    df['Date_Group'] = df.index.date
    cum_tp_v = df.groupby('Date_Group', group_keys=False).apply(lambda x: tp_v.loc[x.index].cumsum())
    cum_v = df.groupby('Date_Group', group_keys=False).apply(lambda x: x['Volume'].cumsum())
    
    if isinstance(cum_tp_v, pd.Series):
        df['VWAP'] = cum_tp_v / cum_v
    else:
        df['VWAP'] = tp_v.cumsum() / df['Volume'].cumsum()
    return df

def calculate_stoch_rsi(df, period=14, k_smooth=3, d_smooth=3):
    """Calcola lo Stochastic RSI manualmente in Python puro."""
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / (loss + 1e-10)
    rsi = 100 - (100 / (1 + rs + 1e-10))
    
    rsi_min = rsi.rolling(window=period).min()
    rsi_max = rsi.rolling(window=period).max()
    
    stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-10) * 100
    df['StochRSI_K'] = stoch_rsi.rolling(window=k_smooth).mean()
    df['StochRSI_D'] = df['StochRSI_K'].rolling(window=d_smooth).mean()
    return df

def calculate_ema(df, period=200):
    """Calcola la Media Mobile Esponenziale a 200 periodi."""
    return df['Close'].ewm(span=period, adjust=False).mean()

def check_timeframe_signal(ticker_symbol, tf):
    try:
        # Usiamo yfinance_cache per richiedere il Ticker aggirando il blocco Crumb
        ticker = yfc.Ticker(ticker_symbol)
        period = "5d" if tf == '15m' else "10d"
        df = ticker.history(period=period, interval=tf)
        
        if df.empty or len(df) < 200:
            return None

        df = calculate_vwap(df)
        df = calculate_stoch_rsi(df)
        df['EMA_200'] = calculate_ema(df, 200)

        p_close = df.iloc[-2]['Close']
        p_vwap = df.iloc[-2]['VWAP']
        p_ema = df.iloc[-2]['EMA_200']
        k_curr = df.iloc[-2]['StochRSI_K']
        d_curr = df.iloc[-2]['StochRSI_D']

        # 🟢 CONDIZIONE BUY
        if (BUY_LOW <= k_curr <= BUY_HIGH) and (BUY_LOW <= d_curr <= BUY_HIGH) and (p_close > p_vwap) and (p_close > p_ema):
            return "BUY"
            
        # 🔴 CONDIZIONE SELL
        elif (SELL_LOW <= k_curr <= SELL_HIGH) and (SELL_LOW <= d_curr <= SELL_HIGH) and (p_close < p_vwap) and (p_close < p_ema):
            return "SELL"
            
    except Exception as e:
        print(f"[ERRORE DATA] {ticker_symbol} su {tf}: {e}")
        return None
    return None

def scan_all_markets():
    if not is_market_time():
        print(f"[{datetime.now(LOCAL_TZ).strftime('%H:%M:%S')}] Mercati chiusi o fuori orario. Standby...")
        return

    print(f"\n--- 📈 Scansione Intraday Alta Precisione (15m+30m) Avviata: {datetime.now(LOCAL_TZ).strftime('%H:%M:%S')} ---")
    for ticker in TICKERS:
        sig_15m = check_timeframe_signal(ticker, '15m')
        sig_30m = check_timeframe_signal(ticker, '30m')
        
        if sig_15m == "BUY" and sig_30m == "BUY":
            send_telegram_message(
                f"🎯 🟢 **SEGNALE BUY INTRADAY CELESTE** 🟢 🎯\n\n"
                f"**Titolo:** `{ticker}`\n"
                f"**Analisi:** Confluenza direzionale su **15m** e **30m**.\n"
                f"1. Stoch RSI uscito da ipervenduto ({BUY_LOW}-{BUY_HIGH})\n"
                f"2. Prezzo superiore al VWAP intraday\n"
                f"3. Tendenza rialzista protetta da EMA 200"
            )
        elif sig_15m == "SELL" and sig_30m == "SELL":
            send_telegram_message(
                f"🎯 🔴 **SEGNALE SELL INTRADAY CELESTE** 🔴 🎯\n\n"
                f"**Titolo:** `{ticker}`\n"
                f"**Analisi:** Confluenza short su **15m** e **30m**.\n"
                f"1. Stoch RSI uscito da ipercomprato ({SELL_LOW}-{SELL_HIGH})\n"
                f"2. Prezzo inferiore al VWAP intraday\n"
                f"3. Tendenza ribassista confermata sotto EMA 200"
            )

def bot_loop():
    print("Inizializzazione bot...")
    send_telegram_message("🚀 **Bot Intraday V2 Online!** Corretto il blocco di Yahoo Finance via cache persistente.")
    print("Bot in esecuzione...")
    while True:
        scan_all_markets()
        time.sleep(300)

if __name__ == "__main__":
    t_web = Thread(target=run_web_server)
    t_web.start()
    bot_loop()

