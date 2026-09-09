import os
import time
import threading
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify


# ============================================================
# CONFIGURAZIONE GENERALE
# ============================================================

PORT = int(os.getenv("PORT", "10000"))

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
TELEGRAM_URL = "https://api.telegram.org"

INTERVAL = os.getenv("INTERVAL", "15min")
OUTPUTSIZE = int(os.getenv("OUTPUTSIZE", "250"))

# Un gruppo viene analizzato ogni 15 minuti.
# Con 5 gruppi da 6 titoli:
# 30 titoli / 6 per gruppo = 5 gruppi
# 5 gruppi x 15 minuti = circa 75 minuti per completare il giro.
GROUP_INTERVAL_SECONDS = int(
    os.getenv("GROUP_INTERVAL_SECONDS", "900")
)

# Sei richieste con 10 secondi di distanza restano sotto il limite
# gratuito di 8 richieste al minuto.
REQUEST_DELAY_SECONDS = float(
    os.getenv("REQUEST_DELAY_SECONDS", "10")
)

# Il piano gratuito ha un limite giornaliero.
# Questa configurazione produce circa:
# 30 richieste ogni 75 minuti = circa 576 richieste al giorno.
MAX_SYMBOLS_PER_GROUP = int(
    os.getenv("MAX_SYMBOLS_PER_GROUP", "6")
)

# Se true, il segnale viene stampato ma non mandato a Telegram.
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# Se true, dopo un errore API viene mandato anche un messaggio Telegram.
# Lasciamo false per non riempire la chat di errori.
SEND_API_ERROR_MESSAGES = (
    os.getenv("SEND_API_ERROR_MESSAGES", "false").lower() == "true"
)

# Se true, viene esclusa l'ultima candela ricevuta.
# È più prudente perché l'ultima candela potrebbe essere ancora aperta.
USE_ONLY_CLOSED_CANDLES = (
    os.getenv("USE_ONLY_CLOSED_CANDLES", "true").lower() == "true"
)

# Bande VWAP.
VWAP_BAND_MULTIPLIER = float(
    os.getenv("VWAP_BAND_MULTIPLIER", "2.0")
)

# Strategia trend.
STOCH_OVERSOLD = float(
    os.getenv("STOCH_OVERSOLD", "20")
)

STOCH_OVERBOUGHT = float(
    os.getenv("STOCH_OVERBOUGHT", "80")
)


# ============================================================
# LETTURA VARIABILI D'AMBIENTE
# ============================================================

def get_first_env(*names):
    """
    Restituisce la prima variabile d'ambiente valorizzata.
    Permette di mantenere compatibilità con i nomi già usati.
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
# LISTA DEI TITOLI
# ============================================================

SYMBOLS = {
    # -------------------------
    # Stati Uniti - NASDAQ
    # -------------------------
    "AAPL": {"symbol": "AAPL", "exchange": "NASDAQ"},
    "TSLA": {"symbol": "TSLA", "exchange": "NASDAQ"},
    "NVDA": {"symbol": "NVDA", "exchange": "NASDAQ"},
    "MSFT": {"symbol": "MSFT", "exchange": "NASDAQ"},
    "AMZN": {"symbol": "AMZN", "exchange": "NASDAQ"},
    "GOOGL": {"symbol": "GOOGL", "exchange": "NASDAQ"},
    "META": {"symbol": "META", "exchange": "NASDAQ"},
    "AMD": {"symbol": "AMD", "exchange": "NASDAQ"},
    "NFLX": {"symbol": "NFLX", "exchange": "NASDAQ"},

    # -------------------------
    # Stati Uniti - NYSE
    # -------------------------
    "JPM": {"symbol": "JPM", "exchange": "NYSE"},
    "V": {"symbol": "V", "exchange": "NYSE"},
    "DIS": {"symbol": "DIS", "exchange": "NYSE"},
    "KO": {"symbol": "KO", "exchange": "NYSE"},
    "XOM": {"symbol": "XOM", "exchange": "NYSE"},
    "PFE": {"symbol": "PFE", "exchange": "NYSE"},

    # -------------------------
    # Germania - Xetra
    # -------------------------
    "SAP": {"symbol": "SAP", "exchange": "XETR"},
    "SIE": {"symbol": "SIE", "exchange": "XETR"},
    "ALV": {"symbol": "ALV", "exchange": "XETR"},
    "BMW": {"symbol": "BMW", "exchange": "XETR"},

    # -------------------------
    # Francia - Euronext Paris
    # -------------------------
    "MC": {"symbol": "MC", "exchange": "XPAR"},
    "OR": {"symbol": "OR", "exchange": "XPAR"},
    "AIR": {"symbol": "AIR", "exchange": "XPAR"},
    "BNP": {"symbol": "BNP", "exchange": "XPAR"},

    # -------------------------
    # Paesi Bassi - Euronext Amsterdam
    # -------------------------
    "ASML": {"symbol": "ASML", "exchange": "XAMS"},

    # -------------------------
    # Italia - Euronext Milan
    # -------------------------
    "ENI": {"symbol": "ENI", "exchange": "XMIL"},
    "ISP": {"symbol": "ISP", "exchange": "XMIL"},
    "ENEL": {"symbol": "ENEL", "exchange": "XMIL"},

    # -------------------------
    # Regno Unito - London Stock Exchange
    # -------------------------
    "HSBA": {"symbol": "HSBA", "exchange": "XLON"},
    "ULVR": {"symbol": "ULVR", "exchange": "XLON"},
    "AZN": {"symbol": "AZN", "exchange": "XLON"},
}


# ============================================================
# CREAZIONE GRUPPI
# ============================================================

SYMBOL_NAMES = list(SYMBOLS.keys())

SYMBOL_GROUPS = [
    SYMBOL_NAMES[index:index + MAX_SYMBOLS_PER_GROUP]
    for index in range(
        0,
        len(SYMBOL_NAMES),
        MAX_SYMBOLS_PER_GROUP,
    )
]


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
        "groups": len(SYMBOL_GROUPS),
        "symbols_per_group": MAX_SYMBOLS_PER_GROUP,
        "group_interval_seconds": GROUP_INTERVAL_SECONDS,
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
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def send_telegram(text):
    """
    Invia un messaggio Telegram.
    Il token non viene mai stampato nei log.
    """

    if not telegram_configured():
        log(
            "[TELEGRAM] Configurazione incompleta: "
            "token o chat_id mancanti"
        )
        return False

    url = (
        f"{TELEGRAM_URL}/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

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

        return response.ok

    except requests.RequestException as exc:
        log(
            "[TELEGRAM] Errore di rete durante l'invio: "
            f"{repr(exc)}"
        )
        return False

    except Exception as exc:
        log(
            "[TELEGRAM] Errore imprevisto durante l'invio: "
            f"{repr(exc)}"
        )
        return False


def send_startup_test():
    """
    Messaggio di conferma inviato prima dell'analisi dei titoli.
    """

    log(
        "[CHECK] TWELVE_DATA_API_KEY presente: "
        f"{bool(TWELVE_DATA_API_KEY)}"
    )

    log(
        "[CHECK] TELEGRAM_BOT_TOKEN presente: "
        f"{bool(TELEGRAM_BOT_TOKEN)}"
    )

    log(
        "[CHECK] TELEGRAM_CHAT_ID presente: "
        f"{bool(TELEGRAM_CHAT_ID)}"
    )

    if not telegram_configured():
        log(
            "[CHECK] Telegram non configurato: "
            "messaggio di avvio non inviato"
        )
        return False

    message = (
        "✅ Bot avviato correttamente su Render\n\n"
        f"Titoli configurati: {len(SYMBOLS)}\n"
        f"Gruppi: {len(SYMBOL_GROUPS)}\n"
        f"Titoli per gruppo: {MAX_SYMBOLS_PER_GROUP}\n"
        f"Timeframe: {INTERVAL}\n"
        f"VWAP bands: ±{VWAP_BAND_MULTIPLIER} deviazioni standard\n"
        f"Modalità test: {'ON' if DRY_RUN else 'OFF'}\n\n"
        "Strategie attive:\n"
        "• Trend EMA 200 + VWAP + Stoch RSI\n"
        "• Ritracciamento bande VWAP\n\n"
        "Inizio scansione."
    )

    log("[CHECK] Invio messaggio di avvio Telegram...")
    return send_telegram(message)


# ============================================================
# ECCEZIONE RATE LIMIT
# ============================================================

class TwelveDataRateLimitError(Exception):
    pass


# ============================================================
# TWELVE DATA
# ============================================================

def get_time_series(display_name, config):
    """
    Scarica le candele di un singolo titolo.

    La richiesta utilizza:
        symbol=AMD
        exchange=NASDAQ

    e non:
        symbol=AMD:NASDAQ
    """

    if not TWELVE_DATA_API_KEY:
        log("[API] TWELVE_DATA_API_KEY mancante")
        return None

    params = {
        "symbol": config["symbol"],
        "exchange": config["exchange"],
        "interval": INTERVAL,
        "outputsize": OUTPUTSIZE,
        "timezone": "Exchange",
        "prepost": "false",
        "format": "JSON",
        "apikey": TWELVE_DATA_API_KEY,
    }

    try:
        response = requests.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=30,
        )

        try:
            payload = response.json()

        except ValueError:
            log(
                f"[DEBUG API] Risposta non JSON per {display_name}: "
                f"HTTP {response.status_code} "
                f"{response.text[:500]}"
            )
            return None

        message = str(payload.get("message", ""))

        is_rate_limited = (
            response.status_code == 429
            or payload.get("code") == 429
            or "run out of API credits" in message.lower()
            or "current limit" in message.lower()
        )

        if is_rate_limited:
            log(
                f"[DEBUG API] Limite API raggiunto durante "
                f"l'analisi di {display_name}: {payload}"
            )
            raise TwelveDataRateLimitError(
                "Limite API Twelve Data raggiunto"
            )

        if (
            response.status_code != 200
            or payload.get("status") == "error"
        ):
            log(
                f"[DEBUG API] Errore per {display_name}: "
                f"HTTP {response.status_code} "
                f"{payload}"
            )

            if SEND_API_ERROR_MESSAGES:
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

        required_columns = [
            "datetime",
            "open",
            "high",
            "low",
            "close",
        ]

        missing_columns = [
            column
            for column in required_columns
            if column not in df.columns
        ]

        if missing_columns:
            log(
                f"[DEBUG API] Colonne mancanti per {display_name}: "
                f"{missing_columns}"
            )
            return None

        for column in [
            "open",
            "high",
            "low",
            "close",
        ]:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(
                df["volume"],
                errors="coerce",
            ).fillna(0.0)
        else:
            df["volume"] = 0.0

        df["datetime"] = pd.to_datetime(
            df["datetime"],
            errors="coerce",
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

        df = df.sort_values(
            "datetime",
            ascending=True,
        ).reset_index(drop=True)

        if USE_ONLY_CLOSED_CANDLES and len(df) > 1:
            df = df.iloc[:-1].copy()

        if len(df) < 220:
            log(
                f"[DEBUG API] Dati insufficienti per {display_name}: "
                f"{len(df)} candele disponibili"
            )
            return None

        log(
            f"[API OK] {display_name}: "
            f"{len(df)} candele ricevute "
            f"({config['symbol']} / {config['exchange']})"
        )

        return df

    except TwelveDataRateLimitError:
        raise

    except requests.RequestException as exc:
        log(
            f"[DEBUG API] Errore di rete per {display_name}: "
            f"{repr(exc)}"
        )
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

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    relative_strength = (
        average_gain
        / average_loss.replace(0, np.nan)
    )

    rsi = 100 - (
        100 / (1 + relative_strength)
    )

    return rsi


def calculate_stoch_rsi(
    close,
    rsi_period=14,
    stoch_period=14,
    smooth_period=3,
):
    rsi = calculate_rsi(
        close,
        period=rsi_period,
    )

    lowest_rsi = rsi.rolling(
        window=stoch_period
    ).min()

    highest_rsi = rsi.rolling(
        window=stoch_period
    ).max()

    denominator = (
        highest_rsi - lowest_rsi
    ).replace(0, np.nan)

    stoch_rsi = (
        (rsi - lowest_rsi)
        / denominator
        * 100
    )

    smoothed_stoch_rsi = stoch_rsi.rolling(
        window=smooth_period
    ).mean()

    return smoothed_stoch_rsi


def calculate_indicators(df):
    """
    Calcola:

    - EMA 200
    - VWAP giornaliera
    - banda VWAP superiore
    - banda VWAP inferiore
    - Stoch RSI

    La VWAP viene azzerata a ogni nuova data di sessione.
    """

    df = df.copy()

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        errors="coerce",
    )

    # --------------------------------------------------------
    # EMA 200
    # --------------------------------------------------------

    df["ema200"] = df["close"].ewm(
        span=200,
        adjust=False,
        min_periods=200,
    ).mean()

    # --------------------------------------------------------
    # Prezzo tipico
    # --------------------------------------------------------

    df["typical_price"] = (
        df["high"]
        + df["low"]
        + df["close"]
    ) / 3.0

    # --------------------------------------------------------
    # VWAP giornaliera
    # --------------------------------------------------------

    # Twelve Data restituisce l'orario in Exchange timezone.
    # Raggruppare per data quindi resetta la VWAP a ogni sessione.
    df["session_date"] = df["datetime"].dt.date

    df["price_volume"] = (
        df["typical_price"]
        * df["volume"]
    )

    df["price_squared_volume"] = (
        df["typical_price"] ** 2
        * df["volume"]
    )

    grouped_volume = df.groupby(
        "session_date",
        sort=False,
    )["volume"]

    grouped_price_volume = df.groupby(
        "session_date",
        sort=False,
    )["price_volume"]

    grouped_price_squared_volume = df.groupby(
        "session_date",
        sort=False,
    )["price_squared_volume"]

    df["cumulative_volume"] = (
        grouped_volume.cumsum()
    )

    df["cumulative_price_volume"] = (
        grouped_price_volume.cumsum()
    )

    df["cumulative_price_squared_volume"] = (
        grouped_price_squared_volume.cumsum()
    )

    safe_volume = df["cumulative_volume"].replace(
        0,
        np.nan,
    )

    df["vwap"] = (
        df["cumulative_price_volume"]
        / safe_volume
    )

    # Varianza ponderata dal volume:
    # E[x²] - E[x]²
    weighted_second_moment = (
        df["cumulative_price_squared_volume"]
        / safe_volume
    )

    variance = (
        weighted_second_moment
        - df["vwap"] ** 2
    )

    variance = variance.clip(lower=0)

    df["vwap_std"] = np.sqrt(variance)

    df["vwap_upper"] = (
        df["vwap"]
        + VWAP_BAND_MULTIPLIER
        * df["vwap_std"]
    )

    df["vwap_lower"] = (
        df["vwap"]
        - VWAP_BAND_MULTIPLIER
        * df["vwap_std"]
    )

    # --------------------------------------------------------
    # Stoch RSI
    # --------------------------------------------------------

    df["stoch_rsi"] = calculate_stoch_rsi(
        df["close"]
    )

    return df


# ============================================================
# LOGICA DEI SEGNALI
# ============================================================

def evaluate_signal(df):
    """
    Restituisce:

        signal, strategy, details

    Esempi:

        BUY, TREND, details
        SELL, RETRACEMENT, details
        None, None, None
    """

    if len(df) < 3:
        return None, None, None

    previous = df.iloc[-2]
    current = df.iloc[-1]

    required_columns = [
        "datetime",
        "close",
        "ema200",
        "vwap",
        "vwap_upper",
        "vwap_lower",
        "stoch_rsi",
    ]

    for column in required_columns:
        if pd.isna(previous[column]):
            return None, None, None

        if pd.isna(current[column]):
            return None, None, None

    previous_close = float(previous["close"])
    current_close = float(current["close"])

    previous_ema = float(previous["ema200"])
    current_ema = float(current["ema200"])

    previous_vwap = float(previous["vwap"])
    current_vwap = float(current["vwap"])

    previous_upper = float(previous["vwap_upper"])
    current_upper = float(current["vwap_upper"])

    previous_lower = float(previous["vwap_lower"])
    current_lower = float(current["vwap_lower"])

    previous_stoch = float(previous["stoch_rsi"])
    current_stoch = float(current["stoch_rsi"])

    details = {
        "candle_time": str(current["datetime"]),
        "close": current_close,
        "ema200": current_ema,
        "vwap": current_vwap,
        "vwap_upper": current_upper,
        "vwap_lower": current_lower,
        "vwap_std": float(current["vwap_std"]),
        "stoch_rsi": current_stoch,
        "previous_stoch_rsi": previous_stoch,
    }

    # ========================================================
    # STRATEGIA RITRACCIAMENTO
    # ========================================================
    #
    # SELL:
    # - candela precedente sopra banda superiore
    # - candela attuale rientra nella banda
    # - Stoch RSI precedente ipercomprato
    # - Stoch RSI in discesa
    #
    # BUY:
    # - candela precedente sotto banda inferiore
    # - candela attuale rientra nella banda
    # - Stoch RSI precedente ipervenduto
    # - Stoch RSI in salita
    #
    # È una conferma più prudente rispetto a vendere
    # semplicemente perché il prezzo supera una banda.

    retracement_sell = (
        previous_close > previous_upper
        and current_close <= current_upper
        and previous_stoch >= STOCH_OVERBOUGHT
        and current_stoch < previous_stoch
    )

    retracement_buy = (
        previous_close < previous_lower
        and current_close >= current_lower
        and previous_stoch <= STOCH_OVERSOLD
        and current_stoch > previous_stoch
    )

    if retracement_sell:
        return "SELL", "RETRACEMENT", details

    if retracement_buy:
        return "BUY", "RETRACEMENT", details

    # ========================================================
    # STRATEGIA TREND
    # ========================================================
    #
    # BUY:
    # - prezzo sopra EMA 200
    # - prezzo sopra VWAP
    # - Stoch RSI precedente in oversold
    # - Stoch RSI in risalita
    #
    # SELL:
    # - prezzo sotto EMA 200
    # - prezzo sotto VWAP
    # - Stoch RSI precedente in overbought
    # - Stoch RSI in discesa

    trend_buy = (
        current_close > current_ema
        and current_close > current_vwap
        and previous_stoch <= STOCH_OVERSOLD
        and current_stoch > previous_stoch
    )

    trend_sell = (
        current_close < current_ema
        and current_close < current_vwap
        and previous_stoch >= STOCH_OVERBOUGHT
        and current_stoch < previous_stoch
    )

    if trend_buy:
        return "BUY", "TREND", details

    if trend_sell:
        return "SELL", "TREND", details

    return None, None, details


# ============================================================
# MESSAGGI
# ============================================================

def format_signal_message(
    display_name,
    signal,
    strategy,
    details,
):
    if signal == "BUY":
        emoji = "🟢"
    else:
        emoji = "🔴"

    if strategy == "TREND":
        strategy_label = "TREND"
    else:
        strategy_label = "RITRACCIAMENTO"

    return (
        f"{emoji} SEGNALE {signal} - {strategy_label}\n\n"
        f"Titolo: {display_name}\n"
        f"Timeframe: {INTERVAL}\n"
        f"Candela: {details['candle_time']}\n\n"
        f"Prezzo: {details['close']:.4f}\n"
        f"EMA 200: {details['ema200']:.4f}\n"
        f"VWAP: {details['vwap']:.4f}\n"
        f"Banda superiore: "
        f"{details['vwap_upper']:.4f}\n"
        f"Banda inferiore: "
        f"{details['vwap_lower']:.4f}\n"
        f"Stoch RSI: "
        f"{details['stoch_rsi']:.2f}\n\n"
        "⚠️ Segnale informativo: verificare sempre "
        "grafico, liquidità e contesto prima di qualsiasi decisione."
    )


# ============================================================
# SCANSIONE DI UN GRUPPO
# ============================================================

last_signal_key = {}


def scan_group(group_number, group_symbols):
    log("=" * 70)
    log(
        f"[GROUP] Avvio gruppo {group_number + 1}/"
        f"{len(SYMBOL_GROUPS)}"
    )
    log(
        f"[GROUP] Titoli: {', '.join(group_symbols)}"
    )

    valid_data_count = 0
    signal_count = 0

    for position, display_name in enumerate(group_symbols):
        config = SYMBOLS[display_name]

        try:
            df = get_time_series(
                display_name,
                config,
            )

            if df is None:
                continue

            valid_data_count += 1

            df = calculate_indicators(df)

            signal, strategy, details = evaluate_signal(df)

            if details is None:
                log(
                    f"[CHECK] {display_name}: "
                    "indicatori non disponibili"
                )
                continue

            log(
                f"[CHECK] {display_name} | "
                f"close={details['close']:.4f} | "
                f"EMA200={details['ema200']:.4f} | "
                f"VWAP={details['vwap']:.4f} | "
                f"upper={details['vwap_upper']:.4f} | "
                f"lower={details['vwap_lower']:.4f} | "
                f"StochRSI={details['stoch_rsi']:.2f} | "
                f"signal={signal or 'NESSUNO'} | "
                f"strategy={strategy or '-'}"
            )

            if signal is None:
                continue

            signal_count += 1

            signal_key = (
                f"{display_name}|"
                f"{strategy}|"
                f"{signal}|"
                f"{details['candle_time']}"
            )

            if last_signal_key.get(display_name) == signal_key:
                log(
                    f"[SIGNAL] {display_name}: "
                    "segnale già inviato per questa candela"
                )
                continue

            message = format_signal_message(
                display_name,
                signal,
                strategy,
                details,
            )

            if DRY_RUN:
                log(
                    f"[DRY RUN] Segnale non inviato:\n"
                    f"{message}"
                )

                last_signal_key[display_name] = signal_key
                continue

            log(
                f"[TELEGRAM] Invio segnale {signal} "
                f"{strategy} per {display_name}"
            )

            sent = send_telegram(message)

            if sent:
                last_signal_key[display_name] = signal_key

                log(
                    f"[TELEGRAM] Segnale inviato per "
                    f"{display_name}"
                )
            else:
                log(
                    f"[TELEGRAM] Invio fallito per "
                    f"{display_name}"
                )

        except TwelveDataRateLimitError:
            log(
                "[RATE LIMIT] Limite Twelve Data raggiunto. "
                "Interrompo il gruppo attuale e riprenderò "
                "con il gruppo successivo."
            )
            break

        except Exception as exc:
            log(
                f"[GROUP] Errore non gestito su "
                f"{display_name}: {repr(exc)}"
            )

        finally:
            # Pausa tra le richieste API.
            # Non viene applicata dopo l'ultimo titolo del gruppo.
            if position < len(group_symbols) - 1:
                time.sleep(REQUEST_DELAY_SECONDS)

    log(
        f"[GROUP] Fine gruppo {group_number + 1} | "
        f"dati validi={valid_data_count} | "
        f"segnali trovati={signal_count}"
    )
    log("=" * 70)


# ============================================================
# CICLO ROTANTE
# ============================================================

def scanner_loop():
    log("[BOT] Thread scanner avviato")
    log(
        f"[BOT] {len(SYMBOLS)} titoli divisi in "
        f"{len(SYMBOL_GROUPS)} gruppi"
    )
    log(
        f"[BOT] Ogni gruppo contiene al massimo "
        f"{MAX_SYMBOLS_PER_GROUP} titoli"
    )
    log(
        f"[BOT] Intervallo tra gruppi: "
        f"{GROUP_INTERVAL_SECONDS} secondi"
    )
    log(
        f"[BOT] Pausa tra richieste: "
        f"{REQUEST_DELAY_SECONDS} secondi"
    )

    group_index = 0

    while True:
        cycle_start = time.monotonic()

        current_group = SYMBOL_GROUPS[group_index]

        try:
            scan_group(
                group_number=group_index,
                group_symbols=current_group,
            )

        except Exception as exc:
            log(
                f"[BOT] Errore grave nel gruppo "
                f"{group_index + 1}: {repr(exc)}"
            )

        group_index = (
            group_index + 1
        ) % len(SYMBOL_GROUPS)

        elapsed = time.monotonic() - cycle_start

        wait_seconds = max(
            1,
            GROUP_INTERVAL_SECONDS - elapsed,
        )

        log(
            f"[BOT] Prossimo gruppo tra "
            f"{wait_seconds:.0f} secondi"
        )

        time.sleep(wait_seconds)


# ============================================================
# AVVIO
# ============================================================

def start_background_bot():
    scanner_thread = threading.Thread(
        target=scanner_loop,
        name="scanner-thread",
        daemon=True,
    )

    scanner_thread.start()


if __name__ == "__main__":
    log("[BOT] Avvio applicazione")

    # Controllo Telegram indipendente dall'API di mercato.
    send_startup_test()

    # Avvio del ciclo rotante in background.
    start_background_bot()

    # Render mantiene attivo il Web Service tramite Flask.
    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
    )
