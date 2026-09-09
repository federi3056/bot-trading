import os
import time
import threading
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np
from flask import Flask, jsonify


# ============================================================
# CONFIGURAZIONE
# ============================================================

PORT = int(os.getenv("PORT", "10000"))

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
TELEGRAM_URL = "https://api.telegram.org"

INTERVAL = os.getenv("INTERVAL", "15min")
OUTPUTSIZE = int(os.getenv("OUTPUTSIZE", "250"))

# Intervallo tra una scansione e la successiva.
# 900 secondi = 15 minuti.
SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "900"))

# Se true, il bot stampa i segnali ma non invia i segnali operativi.
# Il messaggio di avvio Telegram viene comunque inviato.
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# Invia un messaggio anche se l'API restituisce errori sui simboli.
SEND_ERROR_MESSAGES = (
    os.getenv("SEND_ERROR_MESSAGES", "false").lower() == "true"
)


# ============================================================
# LETTURA VARIABILI D'AMBIENTE
# ============================================================

def get_first_env(*names):
    """
    Legge la prima variabile disponibile tra quelle indicate.
    Permette di mantenere compatibilità con eventuali nomi già
    presenti su Render.
    """
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return ""


TWELVE_DATA_API_KEY = get_first_env(
    "TWELVE_DATA_API_KEY",
    "TWELVE_DATA_KEY",
    "TWELVE_API_KEY",
)

TELEGRAM_BOT_TOKEN = get_first_env(
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TOKEN",
    "BOT_TOKEN",
)

TELEGRAM_CHAT_ID = get_first_env(
    "TELEGRAM_CHAT_ID",
    "CHAT_ID",
    "TELEGRAM_ID",
)


# ============================================================
# LISTA TITOLI
# ============================================================
#
# IMPORTANTE:
# - symbol contiene solo il ticker
# - exchange contiene il codice della borsa Twelve Data
#
# Non usare valori come:
# SAP:XETRA
# SIE:XETRA
# MIL:ENI
#
# ============================================================

SYMBOLS = {
    # Stati Uniti
    "AAPL": {"symbol": "AAPL", "exchange": "NASDAQ"},
    "TSLA": {"symbol": "TSLA", "exchange": "NASDAQ"},
    "NVDA": {"symbol": "NVDA", "exchange": "NASDAQ"},
    "MSFT": {"symbol": "MSFT", "exchange": "NASDAQ"},
    "AMZN": {"symbol": "AMZN", "exchange": "NASDAQ"},
    "GOOGL": {"symbol": "GOOGL", "exchange": "NASDAQ"},
    "META": {"symbol": "META", "exchange": "NASDAQ"},
    "AMD": {"symbol": "AMD", "exchange": "NASDAQ"},
    "NFLX": {"symbol": "NFLX", "exchange": "NASDAQ"},

    "JPM": {"symbol": "JPM", "exchange": "NYSE"},
    "V": {"symbol": "V", "exchange": "NYSE"},
    "DIS": {"symbol": "DIS", "exchange": "NYSE"},
    "KO": {"symbol": "KO", "exchange": "NYSE"},
    "XOM": {"symbol": "XOM", "exchange": "NYSE"},
    "PFE": {"symbol": "PFE", "exchange": "NYSE"},

    # Germania - Xetra
    "SAP": {"symbol": "SAP", "exchange": "XETR"},
    "SIE": {"symbol": "SIE", "exchange": "XETR"},
    "ALV": {"symbol": "ALV", "exchange": "XETR"},
    "BMW": {"symbol": "BMW", "exchange": "XETR"},

    # Francia - Euronext Paris
    "MC": {"symbol": "MC", "exchange": "XPAR"},
    "OR": {"symbol": "OR", "exchange": "XPAR"},
    "AIR": {"symbol": "AIR", "exchange": "XPAR"},
    "BNP": {"symbol": "BNP", "exchange": "XPAR"},

    # Paesi Bassi - Euronext Amsterdam
    "ASML": {"symbol": "ASML", "exchange": "XAMS"},

    # Italia - Euronext Milan
    "ENI": {"symbol": "ENI", "exchange": "XMIL"},
    "ISP": {"symbol": "ISP", "exchange": "XMIL"},
    "ENEL": {"symbol": "ENEL", "exchange": "XMIL"},

    # Regno Unito - London Stock Exchange
    "HSBA": {"symbol": "HSBA", "exchange": "XLON"},
    "ULVR": {"symbol": "ULVR", "exchange": "XLON"},
    "AZN": {"symbol": "AZN", "exchange": "XLON"},
}


# ============================================================
# SERVER WEB PER RENDER
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "bot-trading",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "symbols": len(SYMBOLS),
        "interval": INTERVAL,
        "dry_run": DRY_RUN,
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "time_utc": datetime.now(timezone.utc).isoformat(),
    })


# ============================================================
# LOG
# ============================================================

def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_configured():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(text):
    """
    Invia un messaggio Telegram e registra sempre la risposta.
    Non stampa mai il token.
    """

    if not telegram_configured():
        log("[TELEGRAM] Configurazione incompleta: token o chat_id mancanti")
        return False

    url = f"{TELEGRAM_URL}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
            },
            timeout=20,
        )

        log(
            f"[TELEGRAM] status={response.status_code} "
            f"response={response.text[:500]}"
        )

        if response.ok:
            return True

        return False

    except Exception as exc:
        log(f"[TELEGRAM] eccezione durante l'invio: {repr(exc)}")
        return False


def send_startup_test():
    """
    Questo messaggio deve arrivare a prescindere dai dati di mercato.
    Serve a verificare subito che Render legga le variabili Telegram.
    """

    log(
        "[CHECK] "
        f"TWELVE_DATA_API_KEY presente: {bool(TWELVE_DATA_API_KEY)}"
    )
    log(
        "[CHECK] "
        f"TELEGRAM_BOT_TOKEN presente: {bool(TELEGRAM_BOT_TOKEN)}"
    )
    log(
        "[CHECK] "
        f"TELEGRAM_CHAT_ID presente: {bool(TELEGRAM_CHAT_ID)}"
    )

    if not telegram_configured():
        log("[CHECK] Telegram NON configurato: messaggio non inviato")
        return False

    text = (
        "✅ Bot avviato correttamente su Render\n\n"
        f"Intervallo: {INTERVAL}\n"
        f"Titoli configurati: {len(SYMBOLS)}\n"
        f"Modalità test: {'ON' if DRY_RUN else 'OFF'}\n"
        "Ora inizio la scansione."
    )

    log("[CHECK] Invio messaggio di conferma Telegram...")
    return send_telegram(text)


# ============================================================
# TWELVE DATA
# ============================================================

def get_time_series(display_name, config):
    """
    Richiede le candele di un singolo titolo.

    La richiesta usa:
      symbol=SAP
      exchange=XETR

    e non:
      symbol=SAP:XETRA
    """

    if not TWELVE_DATA_API_KEY:
        log("[API] TWELVE_DATA_API_KEY mancante")
        return None

    params = {
        "symbol": config["symbol"],
        "exchange": config["exchange"],
        "interval": INTERVAL,
        "outputsize": OUTPUTSIZE,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
    }

    try:
        response = requests.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=30,
        )

        try:
            payload = response.json()
        except Exception:
            log(
                f"[DEBUG API] Risposta non JSON per {display_name}: "
                f"HTTP {response.status_code} "
                f"{response.text[:300]}"
            )
            return None

        # Twelve Data può restituire errori nel JSON anche con una risposta HTTP.
        if response.status_code != 200 or payload.get("status") == "error":
            log(
                f"[DEBUG API] Errore per {display_name}: "
                f"HTTP {response.status_code} "
                f"{payload}"
            )

            if SEND_ERROR_MESSAGES:
                send_telegram(
                    f"⚠️ Errore dati per {display_name}\n"
                    f"{str(payload)[:800]}"
                )

            return None

        values = payload.get("values")

        if not values:
            log(
                f"[DEBUG API] Nessun dato per {display_name}: "
                f"{payload}"
            )
            return None

        df = pd.DataFrame(values)

        required_columns = ["datetime", "open", "high", "low", "close"]

        for column in required_columns:
            if column not in df.columns:
                log(
                    f"[DEBUG API] Colonna {column} mancante per "
                    f"{display_name}: {df.columns.tolist()}"
                )
                return None

        for column in ["open", "high", "low", "close"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(
                df["volume"],
                errors="coerce"
            ).fillna(0)
        else:
            df["volume"] = 0.0

        df["datetime"] = pd.to_datetime(
            df["datetime"],
            errors="coerce"
        )

        df = df.dropna(
            subset=[
                "datetime",
                "open",
                "high",
                "low",
                "close",
            ]
        )

        # Twelve Data spesso restituisce i dati dal più recente
        # al più vecchio: li ordiniamo dal più vecchio al più recente.
        df = df.sort_values("datetime").reset_index(drop=True)

        if len(df) < 50:
            log(
                f"[DEBUG API] Dati insufficienti per {display_name}: "
                f"{len(df)} candele"
            )
            return None

        log(
            f"[API OK] {display_name} "
            f"{len(df)} candele ricevute "
            f"({config['symbol']} / {config['exchange']})"
        )

        return df

    except requests.RequestException as exc:
        log(f"[DEBUG API] Errore di rete per {display_name}: {repr(exc)}")
        return None

    except Exception as exc:
        log(
            f"[DEBUG API] Errore imprevisto per {display_name}: "
            f"{repr(exc)}"
        )
        return None


# ============================================================
# INDICATORI
# ============================================================

def calculate_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    average_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    rs = average_gain / average_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_stoch_rsi(
    close,
    rsi_period=14,
    stoch_period=14,
    smooth_k=3,
):
    rsi = calculate_rsi(close, rsi_period)

    lowest_rsi = rsi.rolling(stoch_period).min()
    highest_rsi = rsi.rolling(stoch_period).max()

    denominator = (highest_rsi - lowest_rsi).replace(0, np.nan)

    stoch_rsi = (rsi - lowest_rsi) / denominator * 100

    k = stoch_rsi.rolling(smooth_k).mean()

    return k


def calculate_indicators(df):
    df = df.copy()

    # EMA 200
    df["ema200"] = df["close"].ewm(
        span=200,
        adjust=False,
        min_periods=200,
    ).mean()

    # VWAP cumulativa della sessione.
    # Per questa prova usiamo una VWAP cumulativa sulle candele
    # restituite dall'API.
    typical_price = (
        df["high"] + df["low"] + df["close"]
    ) / 3

    cumulative_volume = df["volume"].cumsum()

    cumulative_value = (
        typical_price * df["volume"]
    ).cumsum()

    df["vwap"] = cumulative_value / cumulative_volume.replace(
        0,
        np.nan,
    )

    # Stoch RSI in percentuale: 0-100
    df["stoch_rsi"] = calculate_stoch_rsi(df["close"])

    return df


# ============================================================
# LOGICA SEGNALI
# ============================================================

def evaluate_signal(df):
    """
    Strategia di prova:

    BUY:
      - prezzo sopra EMA 200
      - prezzo sopra VWAP
      - Stoch RSI <= 20
      - Stoch RSI della candela precedente <= 20
      - Stoch RSI attuale maggiore di quello precedente

    SELL:
      - prezzo sotto EMA 200
      - prezzo sotto VWAP
      - Stoch RSI >= 80
      - Stoch RSI della candela precedente >= 80
      - Stoch RSI attuale minore di quello precedente

    Questa è una versione di test. Prima verifichiamo che dati,
    indicatori e Telegram funzionino.
    """

    if len(df) < 3:
        return None, None

    last = df.iloc[-1]
    previous = df.iloc[-2]

    required = [
        "close",
        "ema200",
        "vwap",
        "stoch_rsi",
    ]

    for column in required:
        if pd.isna(last[column]) or pd.isna(previous[column]):
            return None, None

    close = float(last["close"])
    ema200 = float(last["ema200"])
    vwap = float(last["vwap"])
    stoch = float(last["stoch_rsi"])
    previous_stoch = float(previous["stoch_rsi"])

    details = {
        "time": str(last["datetime"]),
        "close": close,
        "ema200": ema200,
        "vwap": vwap,
        "stoch_rsi": stoch,
        "previous_stoch_rsi": previous_stoch,
    }

    buy_condition = (
        close > ema200
        and close > vwap
        and stoch <= 20
        and previous_stoch <= 20
        and stoch > previous_stoch
    )

    sell_condition = (
        close < ema200
        and close < vwap
        and stoch >= 80
        and previous_stoch >= 80
        and stoch < previous_stoch
    )

    if buy_condition:
        return "BUY", details

    if sell_condition:
        return "SELL", details

    return None, details


# ============================================================
# CICLO DI SCANSIONE
# ============================================================

last_sent_signal = {}
last_scan_time = None


def format_signal_message(display_name, signal, details):
    emoji = "🟢" if signal == "BUY" else "🔴"

    return (
        f"{emoji} SEGNALE {signal}\n\n"
        f"Titolo: {display_name}\n"
        f"Timeframe: {INTERVAL}\n"
        f"Data candela: {details['time']}\n\n"
        f"Prezzo: {details['close']:.4f}\n"
        f"EMA 200: {details['ema200']:.4f}\n"
        f"VWAP: {details['vwap']:.4f}\n"
        f"Stoch RSI: {details['stoch_rsi']:.2f}\n"
    )


def scan_once():
    global last_scan_time

    last_scan_time = datetime.now(timezone.utc)

    log("=" * 70)
    log(f"[SCAN] Inizio scansione di {len(SYMBOLS)} titoli")
    log(f"[SCAN] Timeframe: {INTERVAL}")

    valid_data_count = 0
    signal_count = 0

    for display_name, config in SYMBOLS.items():
        try:
            df = get_time_series(display_name, config)

            if df is None:
                continue

            valid_data_count += 1

            df = calculate_indicators(df)

            signal, details = evaluate_signal(df)

            if details is None:
                log(
                    f"[INDICATORI] {display_name}: "
                    "dati insufficienti per gli indicatori"
                )
                continue

            log(
                f"[CHECK] {display_name} | "
                f"close={details['close']:.4f} | "
                f"EMA200={details['ema200']:.4f} | "
                f"VWAP={details['vwap']:.4f} | "
                f"StochRSI={details['stoch_rsi']:.2f} | "
                f"signal={signal or 'NESSUNO'}"
            )

            if signal is None:
                continue

            signal_count += 1

            signal_key = f"{display_name}:{signal}"
            previous_signal = last_sent_signal.get(display_name)

            # Evita di inviare lo stesso segnale a ogni ciclo.
            if previous_signal == signal:
                log(
                    f"[SIGNAL] {display_name}: {signal} già inviato; "
                    "nessun duplicato"
                )
                continue

            message = format_signal_message(
                display_name,
                signal,
                details,
            )

            if DRY_RUN:
                log(
                    f"[DRY RUN] Segnale non inviato a Telegram:\n"
                    f"{message}"
                )
            else:
                log(
                    f"[TELEGRAM] Invio segnale {signal} "
                    f"per {display_name}"
                )

                sent = send_telegram(message)

                if sent:
                    last_sent_signal[display_name] = signal
                    log(
                        f"[TELEGRAM] Segnale inviato per {display_name}"
                    )
                else:
                    log(
                        f"[TELEGRAM] Invio fallito per {display_name}"
                    )

        except Exception as exc:
            log(
                f"[SCAN] Errore non gestito su {display_name}: "
                f"{repr(exc)}"
            )

    log(
        f"[SCAN] Fine scansione | "
        f"dati validi={valid_data_count} | "
        f"segnali={signal_count}"
    )
    log("=" * 70)


def scanner_loop():
    log("[BOT] Thread scanner avviato")

    # Prima prova: deve partire subito, senza aspettare 15 minuti.
    try:
        scan_once()
    except Exception as exc:
        log(f"[BOT] Errore nella prima scansione: {repr(exc)}")

    while True:
        try:
            log(
                f"[BOT] Attendo {SCAN_SECONDS} secondi "
                "prima della prossima scansione"
            )
            time.sleep(SCAN_SECONDS)

            scan_once()

        except Exception as exc:
            log(
                f"[BOT] Errore nel ciclo principale: "
                f"{repr(exc)}"
            )
            time.sleep(60)


# ============================================================
# AVVIO
# ============================================================

def start_background_bot():
    thread = threading.Thread(
        target=scanner_loop,
        name="scanner-thread",
        daemon=True,
    )
    thread.start()


if __name__ == "__main__":
    log("[BOT] Avvio applicazione")

    # Questo deve inviare Telegram prima della scansione.
    send_startup_test()

    # Avvio scanner in background.
    start_background_bot()

    # Flask mantiene vivo il Web Service di Render.
    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
    )


