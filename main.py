import yfinance as yf
import requests
import pandas as pd
import pandas_ta as ta
import time
from datetime import datetime
import pytz
from flask import Flask
from threading import Thread

# --- INIZIALIZZAZIONE SERVER WEB PER RENDER ---
# Render richiede che l'applicazione risponda a una porta web per rimanere attiva.
app = Flask('')

@app.route('/')
def home():
    return "Bot di Trading Attivo e in Esecuzione!"

def run_web_server():
    # Render assegna automaticamente una porta tramite la variabile d'ambiente PORT
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- CONFIGURAZIONE TELEGRAM ---
TELEGRAM_TOKEN = "8820172406:AAE1Cewxm3qcOYmtKurMw517ABH6-uqyic"
TELEGRAM_CHAT_ID = "-1027014963"

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

# --- PARAMETRI STRATEGIA ---
TIMEFRAMES = ['5m', '15m', '30m', '1h']
STOCH_LENGTH = 14
RSI_LENGTH = 14
K_SMOOTH = 3
D_SMOOTH = 3

BUY_LOW, BUY_HIGH = 0, 2
SELL_LOW, SELL_HIGH = 98, 100

def is_market_time():
    now = datetime.now(LOCAL_TZ)
    if now.weekday() > 4:  # Sabato e Domenica fermo
        return False
    if START_HOUR <= now.hour < END_HOUR:
        return True
    return False

def send_telegram_message(message):
    """Invia le notifiche direttamente senza proxy (su Render la rete è libera)."""
    url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code != 200:
            print(f"[ERRORE TELEGRAM] Risposta del server: {response.text}")
        else:
            print("[OK] Notifica inviata con successo su Telegram!")
    except Exception as e:
        print(f"[ERRORE RETE TELEGRAM] Impossibile connettersi: {e}")

def check_timeframe_signal(ticker_symbol, tf):
    try:
        ticker = yf.Ticker(ticker_symbol)
        period = "2d" if tf in ['5m', '15m'] else "7d"
        # Rimosso il vecchio proxy di PythonAnywhere che causava l'errore
        df = ticker.history(period=period, interval=tf)
        
        if df.empty or len(df) < 30:
            return None

        df.ta.vwap(append=True)
        stoch_rsi = df.ta.stochrsi(length=STOCH_LENGTH, rsi_length=RSI_LENGTH, k=K_SMOOTH, d=D_SMOOTH)
        df = pd.concat([df, stoch_rsi], axis=1)

        k_col = [col for col in df.columns if 'STOCHRSIk' in col]
        d_col = [col for col in df.columns if 'STOCHRSId' in col]
        vwap_col = [col for col in df.columns if 'VWAP' in col]

        p_close = df.iloc[-2]['Close']
        p_vwap = df.iloc[-2][vwap_col]
        k_curr = df.iloc[-2][k_col]
        d_curr = df.iloc[-2][d_col]

        if (BUY_LOW <= k_curr <= BUY_HIGH) and (BUY_LOW <= d_curr <= BUY_HIGH) and p_close > p_vwap:
            return "BUY"
        elif (SELL_LOW <= k_curr <= SELL_HIGH) and (SELL_LOW <= d_curr <= SELL_HIGH) and p_close < p_vwap:
            return "SELL"
    except:
        return None
    return None

def scan_all_markets():
    if not is_market_time():
        print(f"[{datetime.now(LOCAL_TZ).strftime('%H:%M:%S')}] Mercati chiusi o fuori orario. Standby...")
        return

    print(f"\n--- Scansione avviata: {datetime.now(LOCAL_TZ).strftime('%H:%M:%S')} ---")
    for ticker in TICKERS:
        signals = {tf: check_timeframe_signal(ticker, tf) for tf in TIMEFRAMES}
        
        if all(sig == "BUY" for sig in signals.values()):
            send_telegram_message(f"🔥 🟢 **BUY CONVERGENTE** 🟢 🔥\n\n**Titolo:** `{ticker}`\nStoch RSI in ipervenduto estremo su 5m, 15m, 30m, 1h.\nPrezzo sopra VWAP.")
        elif all(sig == "SELL" for sig in signals.values()):
            send_telegram_message(f"🔥 🔴 **SELL CONVERGENTE** 🔴 🔥\n\n**Titolo:** `{ticker}`\nStoch RSI in ipercomprato estremo su 5m, 15m, 30m, 1h.\nPrezzo sotto VWAP.")

def bot_loop():
    """Ciclo infinito di scansione ogni 5 minuti."""
    print("Inizializzazione bot...")
    send_telegram_message("🚀 **Bot di Trading attivato con successo su Render!** Monitoraggio h24 attivo.")
    print("Bot in esecuzione...")
    while True:
        scan_all_markets()
        time.sleep(300)

# --- AVVIO MULTI-THREAD ---
if __name__ == "__main__":
    # 1. Avvia il server web in background per soddisfare i requisiti di Render
    t_web = Thread(target=run_web_server)
    t_web.start()
    
    # 2. Avvia il ciclo principale del bot di trading nello stesso momento
    bot_loop()
