import pandas as pd
import time
from datetime import datetime
import pytz
from flask import Flask
from threading import Thread
import os
import requests
from tvdatafeed import TvDatafeed, Interval

# --- INIZIALIZZAZIONE SERVER WEB PER RENDER ---
app = Flask('')

@app.route('/')
def home():
    return "Bot di Trading Intraday TradingView Attivo!"

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

# --- PANIERE DI 30 AZIONI ADATTATO PER TRADINGVIEW ---
# Formato: (Nome_Visualizzazione, Simbolo_TradingView, Scambio)
TICKERS_CONFIG = [
    ('AAPL', 'AAPL', 'NASDAQ'), ('MSFT', 'MSFT', 'NASDAQ'), ('NVDA', 'NVDA', 'NASDAQ'),
    ('AMZN', 'AMZN', 'NASDAQ'), ('META', 'META', 'NASDAQ'), ('TSLA', 'TSLA', 'NASDAQ'),
    ('GOOGL', 'GOOGL', 'NASDAQ'), ('BRK-B', 'BRK.B', 'NYSE'), ('AMD', 'AMD', 'NASDAQ'),
    ('NFLX', 'NFLX', 'NASDAQ'), ('JPM', 'JPM', 'NYSE'), ('V', 'V', 'NYSE'),
    ('DIS', 'DIS', 'NYSE'), ('PLTR', 'PLTR', 'NYSE'), ('XOM', 'XOM', 'NYSE'),
    ('RACE.MI', 'RACE', 'MIL'), ('STLAM.MI', 'STLAM', 'MIL'), ('ISP.MI', 'ISP', 'MIL'),
    ('UCG.MI', 'UCG', 'MIL'), ('ENI.MI', 'ENI', 'MIL'), ('EGP.MI', 'EGP', 'MIL'),
    ('G.MI', 'G', 'MIL'), ('A2A.MI', 'A2A', 'MIL'), ('PST.MI', 'PST', 'MIL'),
    ('TRN.MI', 'TRN', 'MIL'), ('PRY.MI', 'PRY', 'MIL'), ('MONC.MI', 'MONC', 'MIL'),
    ('STM.MI', 'STMMI', 'MIL'), ('LDO.MI', 'LDO', 'MIL'), ('CPR.MI', 'CPR', 'MIL')
]

# --- PARAMETRI STRATEGIA INTRADAY DIREZIONALE ---
BUY_LOW, BUY_HIGH = 0, 15
SELL_LOW, SELL_HIGH = 85, 100

# Inizializziamo TradingView Datafeed in modalità anonima
tv = TvDatafeed()

def is_market_time():
    now = datetime.now(LOCAL_TZ)
    if now.weekday() > 4:
        return False
    if START_HOUR <= now.hour < END_HOUR:
        return True
    return False

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=15)
    except:
        pass

def calculate_vwap(df):
    """Calcola il VWAP basandosi sulla sessione intraday (reset giornaliero)."""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    tp_v = typical_price * df['volume']
    
    df['Date_Group'] = df.index.date
    cum_tp_v = df.groupby('Date_Group', group_keys=False).apply(lambda x: tp_v.loc[x.index].cumsum())
    cum_v = df.groupby('Date_Group', group_keys=False).apply(lambda x: x['volume'].cumsum())
    
    if isinstance(cum_tp_v, pd.Series):
        df['VWAP'] = cum_tp_v / cum_v
    else:
        df['VWAP'] = tp_v.cumsum() / df['volume'].cumsum()
    return df

def calculate_stoch_rsi(df, period=14, k_smooth=3, d_smooth=3):
    delta = df['close'].diff()
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
    return df['close'].ewm(span=period, adjust=False).mean()

def check_timeframe_signal(symbol, exchange, tf_tv):
    try:
        # Scarichiamo 250 candele stabili direttamente da TradingView
        df = tv.get_hist(symbol=symbol, exchange=exchange, interval=tf_tv, n_bars=250)
        
        if df is优化 or df.empty or len(df) < 200:
            return None

        df = calculate_vwap(df)
        df = calculate_stoch_rsi(df)
        df['EMA_200'] = calculate_ema(df, 200)

        p_close = df.iloc[-2]['close']
        p_vwap = df.iloc[-2]['VWAP']
        p_ema = df.iloc[-2]['EMA_200']
        k_curr = df.iloc[-2]['StochRSI_K']
        d_curr = df.iloc[-2]['StochRSI_D']

        if (BUY_LOW <= k_curr <= BUY_HIGH) and (BUY_LOW <= d_curr <= BUY_HIGH) and (p_close > p_vwap) and (p_close > p_ema):
            return "BUY"
        elif (SELL_LOW <= k_curr <= SELL_HIGH) and (SELL_LOW <= d_curr <= SELL_HIGH) and (p_close < p_vwap) and (p_close < p_ema):
            return "SELL"
            
    except:
        return None
    return None

def scan_all_markets():
    if not is_market_time():
        print(f"[{datetime.now(LOCAL_TZ).strftime('%H:%M:%S')}] Mercati chiusi. Standby...")
        return

    print(f"\n--- 📈 Scansione TradingView (15m+30m) Avviata: {datetime.now(LOCAL_TZ).strftime('%H:%M:%S')} ---")
    for name, symbol, exchange in TICKERS_CONFIG:
        sig_15m = check_timeframe_signal(symbol, exchange, Interval.in_15_minute)
        sig_30m = check_timeframe_signal(symbol, exchange, Interval.in_30_minute)
        
        if sig_15m == "BUY" and sig_30m == "BUY":
            send_telegram_message(
                f"🎯 🟢 **SEGNALE BUY TRADINGVIEW** 🟢 🎯\n\n"
                f"**Titolo:** `{name}`\n"
                f"**Analisi:** Confluenza direzionale su **15m** e **30m**.\n"
                f"1. Stoch RSI uscito da ipervenduto ({BUY_LOW}-{BUY_HIGH})\n"
                f"2. Prezzo superiore al VWAP intraday\n"
                f"3. Tendenza rialzista protetta da EMA 200"
            )
        elif sig_15m == "SELL" and sig_30m == "SELL":
            send_telegram_message(
                f"🎯 🔴 **SEGNALE SELL TRADINGVIEW** 🔴 🎯\n\n"
                f"**Titolo:** `{name}`\n"
                f"**Analisi:** Confluenza short su **15m** e **30m**.\n"
                f"1. Stoch RSI uscito da ipercomprato ({SELL_LOW}-{SELL_HIGH})\n"
                f"2. Prezzo inferiore al VWAP intraday\n"
                f"3. Tendenza ribassista confermata sotto EMA 200"
            )

def bot_loop():
    print("Inizializzazione bot...")
    send_telegram_message("🚀 **Bot Intraday V3 Online!** Rimosso Yahoo Finance. Motore dati commutato su **TradingView** con successo.")
    print("Bot in esecuzione...")
    while True:
        scan_all_markets()
        time.sleep(300)

if __name__ == "__main__":
    t_web = Thread(target=run_web_server)
    t_web.start()
    bot_loop()
