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
# ðŸ”’ Le chiavi vengono lette dalle Environment Variables di Render
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# =========================================================================

# --- CONFIGURAZIONE ORARIA (Fuso Orario Italiano) ---
LOCAL_TZ = pytz.timezone("Europe/Rome")
START_HOUR = 9
END_HOUR = 23

# --- PANIERE: 15 USA + 15 EUROPA (formato SIMBOLO:BORSA) ---
TICKERS = [
    # --- 15 USA ---
    'TSLA', 'NVDA', 'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'AMD',
    'NFLX', 'JPM', 'V', 'DIS', 'KO', 'XOM', 'PFE',

    # --- 15 EUROPA ---
    'SAP:XETRA', 'SIE:XETRA', 'ALV:XETRA', 'BMW:XETRA',
    'MC:EURONEXT', 'OR:EURONEXT', 'AIR:EURONEXT', 'BNP:EURONEXT', 'ASML:EURONEXT',
    'ENI:MTA', 'ISP:MTA', 'ENEL:MTA',
    'HSBA:LSE', 'ULVR:LSE', 'AZN:LSE',
]

# --- PARAMETRI STRATEGIA INTRADAY ---
BUY_LOW, BUY_HIGH = 0, 15
SELL_LOW, SELL_HIGH = 85, 100
EMA_PERIOD = 200
OUTPUTSIZE = 300  # servono almeno ~250-300 barre per una EMA_200 affidabile

# --- GESTIONE RATE LIMIT TWELVE DATA (piano free: 8 crediti/min, 800/giorno) ---
# Ogni simbolo in una richiesta batch consuma 1 credito, quindi dividiamo
# i 30 ticker in gruppi da massimo 8 per rispettare il limite al minuto.
CHUNK_SIZE = 8
SECONDS_BETWEEN_CHUNKS = 65  # margine di sicurezza sopra il reset di 60s

# 30 ticker x 2 timeframe = 60 crediti a scansione -> max ~13 scansioni/giorno
SCAN_INTERVAL_SECONDS = 4500  # ~75 minuti tra scansioni per restare sotto gli 800 crediti/giorno


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
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[ERRORE TELEGRAM] {e}", flush=True)


def calculate_vwap(df):
    try:
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3
        tp_v = typical_price * df['Volume']
        date_group = df.index.date
        cum_tp_v = tp_v.groupby(date_group).cumsum()
        cum_v = df['Volume'].groupby(date_group).cumsum()
        df['VWAP'] = cum_tp_v / (cum_v + 1e-10)
    except Exception:
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


def calculate_ema(df, period):
    return df['Close'].ewm(span=period, adjust=False).mean()


def _parse_values(values):
    """Trasforma la lista 'values' di Twelve Data in DataFrame pulito."""
    if not values:
        return None
    df = pd.DataFrame(values)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime')
    df = df.iloc[::-1]

    df['Close'] = pd.to_numeric(df['close'])
    df['High'] = pd.to_numeric(df['high'])
    df['Low'] = pd.to_numeric(df['low'])
    df['Volume'] = pd.to_numeric(df['volume'])

    return df[['Close', 'High', 'Low', 'Volume']]


def _chunk_list(lst, size):
    """Divide una lista in sottoliste di massimo 'size' elementi."""
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def get_batch_data(tickers, interval):
    """
    Scarica i dati per i ticker richiesti, dividendo in gruppi da max
    CHUNK_SIZE simboli per rispettare il limite di 8 crediti/minuto
    del piano free di Twelve Data. Ogni simbolo consuma 1 credito,
    anche dentro una richiesta batch.
    """
    tf_query = "15min" if interval == "15m" else "30min"
    result = {t: None for t in tickers}

    chunks = list(_chunk_list(tickers, CHUNK_SIZE))

    for idx, chunk in enumerate(chunks):
        symbols_str = ",".join(chunk)
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbols_str}&interval={tf_query}&outputsize={OUTPUTSIZE}"
            f"&apikey={TWELVE_DATA_API_KEY}"
        )

        try:
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                print(f"[DEBUG API] Status Code Errato ({tf_query}, gruppo {idx+1}/{len(chunks)}): {response.status_code}", flush=True)
            else:
                data = response.json()

                if len(chunk) == 1:
                    if "values" in data:
                        result[chunk[0]] = _parse_values(data["values"])
                    else:
                        print(f"[DEBUG API] Rifiuto per {chunk[0]}: {data}", flush=True)
                else:
                    for t in chunk:
                        entry = data.get(t)
                        if entry and "values" in entry:
                            result[t] = _parse_values(entry["values"])
                        else:
                            print(f"[DEBUG API] Nessun dato per {t}: {entry}", flush=True)

        except Exception as e:
            print(f"[DEBUG ECCEZIONE] Errore batch gruppo {idx+1}: {e}", flush=True)

        # Aspetta il reset del credito al minuto prima del prossimo gruppo
        if idx < len(chunks) - 1:
            time.sleep(SECONDS_BETWEEN_CHUNKS)

    return result


def analyze_dataframe(df):
    """Calcola indicatori e restituisce il segnale per un singolo DataFrame."""
    try:
        if df is None or df.empty or len(df) < EMA_PERIOD + 20:
            return None

        df = calculate_vwap(df)
        df = calculate_stoch_rsi(df)
        df['EMA_200'] = calculate_ema(df, EMA_PERIOD)

        last = df.iloc[-1]
        p_close = last['Close']
        p_vwap = last['VWAP']
        p_ema = last['EMA_200']
        k_curr = last['StochRSI_K']
        d_curr = last['StochRSI_D']

        if (BUY_LOW <= k_curr <= BUY_HIGH) and (BUY_LOW <= d_curr <= BUY_HIGH) and (p_close > p_vwap) and (p_close > p_ema):
            return "BUY"
        elif (SELL_LOW <= k_curr <= SELL_HIGH) and (SELL_LOW <= d_curr <= SELL_HIGH) and (p_close < p_vwap) and (p_close < p_ema):
            return "SELL"

    except Exception as e:
        print(f"[ERRORE CALCOLO] {e}", flush=True)
        return None
    return None


def clean_label(ticker):
    """Restituisce solo il simbolo, senza suffisso borsa, per i messaggi Telegram."""
    return ticker.split(':')[0]


def scan_all_markets():
    if not is_market_time():
        print(f"[{datetime.now(LOCAL_TZ).strftime('%H:%M:%S')}] Mercati chiusi. Standby...", flush=True)
        return

    print(f"\n--- ðŸ“ˆ Scansione Intraday Avviata: {datetime.now(LOCAL_TZ).strftime('%H:%M:%S')} ---", flush=True)

    data_15m = get_batch_data(TICKERS, '15m')
    time.sleep(SECONDS_BETWEEN_CHUNKS)  # margine anche tra i due timeframe
    data_30m = get_batch_data(TICKERS, '30m')

    conteggio_ok = 0
    for ticker in TICKERS:
        sig_15m = analyze_dataframe(data_15m.get(ticker))
        sig_30m = analyze_dataframe(data_30m.get(ticker))

        if sig_15m is not None or sig_30m is not None:
            conteggio_ok += 1

        label = clean_label(ticker)

        if sig_15m == "BUY" and sig_30m == "BUY":
            send_telegram_message(
                f"ðŸŽ¯ ðŸŸ¢ **SEGNALE BUY INTRADAY** ðŸŸ¢ ðŸŽ¯\n\n"
                f"**Titolo:** `{label}`\n"
                f"**Analisi:** Confluenza direzionale su **15m** e **30m**.\n"
                f"1. Stoch RSI uscito da ipervenduto ({BUY_LOW}-{BUY_HIGH})\n"
                f"2. Prezzo superiore al VWAP intraday\n"
                f"3. Tendenza rialzista protetta da EMA {EMA_PERIOD}"
            )
        elif sig_15m == "SELL" and sig_30m == "SELL":
            send_telegram_message(
                f"ðŸŽ¯ ðŸ”´ **SEGNALE SELL INTRADAY** ðŸ”´ ðŸŽ¯\n\n"
                f"**Titolo:** `{label}`\n"
                f"**Analisi:** Confluenza short su **15m** e **30m**.\n"
                f"1. Stoch RSI uscito da ipercomprato ({SELL_LOW}-{SELL_HIGH})\n"
                f"2. Prezzo inferiore al VWAP intraday\n"
                f"3. Tendenza ribassista confermata sotto EMA {EMA_PERIOD}"
            )

    print(f"--- Scansione completata. Analizzati con successo {conteggio_ok}/{len(TICKERS)} titoli. ---", flush=True)


def bot_loop():
    print("Inizializzazione bot...", flush=True)
    send_telegram_message("ðŸš€ **Bot Intraday Online!** 15 USA + 15 EU monitorati, EMA_200 attiva.")
    print("Bot in esecuzione...", flush=True)

    while True:
        scan_all_markets()
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    t_web = Thread(target=run_web_server)
    t_web.start()
    bot_loop()
