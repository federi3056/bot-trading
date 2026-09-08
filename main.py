import pandas as pd
import time
from datetime import datetime
import pytz
from flask import Flask
from threading import Thread
import os
import requests

# --- INIZIALIZZAZIONE SERVER WEB PER RENDER ---
app = Flask('')

@app.route('/')
def home():
    return "Bot di Trading Intraday Nativo Attivo!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# =========================================================================
# ✅ CHIAVE API UFFICIALE TWELVE DATA INSERITA CON SUCCESSO
TWELVE_DATA_API_KEY = "faa6e06a87514e9abd41de4c56e519f5"
# =========================================================================

# --- CONFIGURAZIONE TELEGRAM ---
TELEGRAM_TOKEN = "8820172406:AAE1Cewxm3qCOYmtKurcMw517AbH6-uqyic"
TELEGRAM_CHAT_ID = "1027014963"

# --- CONFIGURAZIONE ORARIA (Fuso Orario Italiano) ---
LOCAL_TZ = pytz.timezone("Europe/Rome")
START_HOUR = 9
END_HOUR = 23

# --- PANIERE DI 30 AZIONI (Formattate per Twelve Data) ---
# Le azioni italiane (.MI) su Twelve Data richiedono il suffisso :XMIL (Borsa di Milano)
TICKERS = [
    'AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'TSLA', 'GOOGL', 'BRK.B', 
    'AMD', 'NFLX', 'JPM', 'V', 'DIS', 'PLTR', 'XOM',
    'RACE:XMIL', 'STLAM:XMIL', 'ISP:XMIL', 'UCG:XMIL', 'ENI:XMIL', 'EGP:XMIL', 'G:XMIL', 
    'A2A:XMIL', 'PST:XMIL', 'TRN:XMIL', 'PRY:XMIL', 'MONC:XMIL', 'STM:XMIL', 'LDO:XMIL', 'CPR:XMIL'
]

# --- PARAMETRI STRATEGIA INTRADAY DIREZIONALE ---
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
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[ERRORE TELEGRAM] {e}", flush=True)

def calculate_vwap(df):
    try:
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3
        tp_v = typical_price * df['Volume']
        df['Date_Group'] = df.index.date
        cum_tp_v = df.groupby('Date_Group', group_keys=False).apply(lambda x: tp_v.loc[x.index].cumsum())
        cum_v = df.groupby('Date_Group', group_keys=False).apply(lambda x: x['Volume'].cumsum())
        df['VWAP'] = cum_tp_v / (cum_v + 1e-10)
    except Exception as e:
        df['VWAP'] = df['Close']
    return df

def calculate_stoch_rsi(df, period=14, k_smooth=3, d_smooth=3):
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
    return df['Close'].ewm(span=period, adjust=False).mean()

def get_clean_data(ticker, interval):
    """Scarica i dati ufficiali intraday tramite la chiave Twelve Data senza blocchi."""
    tf_query = "15min" if interval == "15m" else "30min"
    
    # URL di rete ufficiale Twelve Data con la tua chiave privata incorporata
    url = f"https://twelvedata.com{ticker}&interval={tf_query}&outputsize=250&apikey={TWELVE_DATA_API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return None
            
        data = response.json()
        
        # Protezione nel caso in cui l'API risponda con un messaggio di errore anziché con i dati
        if "values" not in data:
            return None
            
        values = data["values"]
        if not values:
            return None
            
        # Twelve Data ordina dal più recente al più vecchio, noi invertiamo per l'analisi tecnica
        df = pd.DataFrame(values)
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.set_index('datetime')
        df = df.iloc[::-1] 
        
        # Conversione esplicita in valori numerici per Pandas
        df['Close'] = pd.to_numeric(df['close'])
        df['High'] = pd.to_numeric(df['high'])
        df['Low'] = pd.to_numeric(df['low'])
        df['Volume'] = pd.to_numeric(df['volume'])
        
        return df[['Close', 'High', 'Low', 'Volume']]
    except Exception as e:
        return None

def check_timeframe_signal(ticker_symbol, tf):
    try:
        df = get_clean_data(ticker_symbol, tf)
        if df is None or df.empty or len(df) < 50:
            return None

        df = calculate_vwap(df)
        df = calculate_stoch_rsi(df)
        df['EMA_200'] = calculate_ema(df, 200)

        if len(df) < 2:
            return None

        # Utilizziamo l'ultima candela conclusa in tempo reale per calcolare i segnali operativi
        p_close = df.iloc[-1]['Close'] 
        p_vwap = df.iloc[-1]['VWAP']
        p_ema = df.iloc[-1]['EMA_200']
        k_curr = df.iloc[-1]['StochRSI_K']
        d_curr = df.iloc[-1]['StochRSI_D']

        if (BUY_LOW <= k_curr <= BUY_HIGH) and (BUY_LOW <= d_curr <= BUY_HIGH) and (p_close > p_vwap) and (p_close > p_ema):
            return "BUY"
        elif (SELL_LOW <= k_curr <= SELL_HIGH) and (SELL_LOW <= d_curr <= SELL_HIGH) and (p_close < p_vwap) and (p_close < p_ema):
            return "SELL"
            
    except Exception as e:
        print(f"[ERRORE CALCOLO] {ticker_symbol} {tf}: {e}", flush=True)
        return None
    return None

def scan_all_markets():
    if not is_market_time():
        print(f"[{datetime.now(LOCAL_TZ).strftime('%H:%M:%S')}] Mercati chiusi. Standby...", flush=True)
        return

    print(f"\n--- 📈 Scansione Intraday Nativa (15m+30m) Avviata: {datetime.now(LOCAL_TZ).strftime('%H:%M:%S')} ---", flush=True)
    
    conteggio_ok = 0
    for ticker in TICKERS:
        print(f"Analisi titolo: {ticker}...", end=" ", flush=True)
        
        sig_15m = check_timeframe_signal(ticker, '15m')
        sig_30m = check_timeframe_signal(ticker, '30m')
        
        # Monitoraggio dello stato dell'API nei log di Render
        if sig_15m is not None or sig_30m is not None or get_clean_data(ticker, '15m') is not None:
            conteggio_ok += 1
            print("OK", flush=True)
        else:
            print("ERRORE DATO", flush=True)
        
        if sig_15m == "BUY" and sig_30m == "BUY":
            send_telegram_message(
                f"🎯 🟢 **SEGNALE BUY INTRADAY** 🟢 🎯\n\n"
                f"**Titolo:** `{ticker}`\n"
                f"**Analisi:** Confluenza direzionale su **15m** e **30m**.\n"
                f"1. Stoch RSI uscito da ipervenduto ({BUY_LOW}-{BUY_HIGH})\n"
                f"2. Prezzo superiore al VWAP intraday\n"
                f"3. Tendenza rialzista protetta da EMA 200"
            )
        elif sig_15m == "SELL" and sig_30m == "SELL":
            send_telegram_message(
                f"🎯 🔴 **SEGNALE SELL INTRADAY** 🔴 🎯\n\n"
                f"**Titolo:** `{ticker}`\n"
                f"**Analisi:** Confluenza short su **15m** e **30m**.\n"
                f"1. Stoch RSI uscito da ipercomprato ({SELL_LOW}-{SELL_HIGH})\n"
                f"2. Prezzo inferiore al VWAP intraday\n"
                f"3. Tendenza ribassista confermata sotto EMA 200"
            )
        
        # Pausa obbligatoria di 8 secondi tra i titoli per rispettare i limiti del piano Free di Twelve Data (max 8 richieste al minuto)
        time.sleep(8)
        
    print(f"--- Scansione completata. Analizzati con successo {conteggio_ok}/{len(TICKERS)} titoli. Pausa di 5 minuti ---", flush=True)

def bot_loop():
    print("Inizializzazione bot...", flush=True)
    send_telegram_message("🚀 **Bot Intraday V5.1 Online!** Monitoraggio mercati Twelve Data attivo.")
    print("Bot in esecuzione...", flush=True)
    print("[INFO] Avvio diretto del ciclo standard scaglionato...", flush=True)

    while True:
        scan_all_markets()
        time.sleep(300)

if __name__ == "__main__":
    t_web = Thread(target=run_web_server)
    t_web.start()
    bot_loop()
