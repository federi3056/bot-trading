print("1. Avvio script...", flush=True)
import pandas as pd
print("2. Pandas importato", flush=True)
import time
from datetime import datetime
import pytz
print("3. Pytz importato", flush=True)
from flask import Flask
from threading import Thread
import os
import requests
print("4. Tutti gli import completati", flush=True)

# --- INIZIALIZZAZIONE SERVER WEB PER RENDER ---
app = Flask('')
print("5. Flask app creata", flush=True)

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
print("6. Variabili ambiente lette", flush=True)
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

# --- GESTIONE CREDITI TWELVE DATA (piano free: 800/giorno, 8/minuto) ---
# 30 ticker x 2 timeframe = 60 crediti a scansione -> max ~13 scansioni/giorno
SCAN_INTERVAL_SECONDS = 4500  # ~75 minuti tra scansioni per restare sotto gli 800 crediti/giorno

print("7. Configurazione completata, definizione funzioni...", flush=True)


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


def get_batch_data(tickers, interval):
    """
    Scarica i dati per TUTTI i ticker in un'unica chiamata HTTP
    (risparmia sul rate limit di 8 chiamate/minuto).
    I crediti consumati restano 1 per simbolo, ma con 1 sola richiesta.
    Per i titoli europei usa il formato SIMBOLO:BORSA (es. SAP:XETRA).
    """
    tf_query = "15min" if interval == "15m" else "30min"
    symbols_str = ",".join(tickers)
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbols_str}&interval={tf_query}&outputsize={OUTPUTSIZE}"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )

    result = {t: None for t in tickers}

    try:
        response = requests.get(url, timeout=30)
        if response.status_code != 200:
            print(f"[DEBUG API] Status Code Errato: {response.status_code}", flush=True)
            return result

        data = response.json()

        if len(tickers) == 1:
            if "values" in data:
                result[tickers[0]] = _parse_values(data["values"])
            else:
                print(f"[DEBUG API] Rifiuto per {tickers[0]}: {data}", flush=True)
        else:
            # Con piÃ¹ simboli la risposta Ã¨ un dizionario per ticker
            # (la chiave corrisponde esattamente al simbolo passato, es. "SAP:XETRA")
            for t in tickers:
                entry = data.get(t)
                if entry and "values" in entry:
                    result[t] = _parse_values(entry["values"])
                else:
                    print(f"[DEBUG API] Nessun dato per {t}: {entry}", flush=True)

    except Exception as e:
        print(f"[DEBUG ECCEZIONE] Errore batch: {e}", flush=True)

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

    # 2 chiamate totali invece di 60 (una per timeframe, con tutti i ticker in batch)
    data_15m = get_batch_data(TICKERS, '15m')
    time.sleep(3)  # piccola pausa di cortesia tra le due chiamate
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
    print("8. Inizializzazione bot...", flush=True)
    send_telegram_message("ðŸš€ **Bot Intraday Online!** 15 USA + 15 EU monitorati, EMA_200 attiva.")
    print("9. Bot in esecuzione...", flush=True)

    while True:
        scan_all_markets()
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    print("10. Entrato nel blocco main", flush=True)
    t_web = Thread(target=run_web_server)
    t_web.start()
    print("11. Thread web avviato, chiamo bot_loop", flush=True)
    bot_loop()
