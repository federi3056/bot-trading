import os
import time
import threading
from datetime import datetime, timezone, timedelta, time as dt_time
from zoneinfo import ZoneInfo

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

# Timeframe operativo.
INTERVAL = os.getenv("INTERVAL", "5min")

# Numero massimo di candele richieste per ogni titolo.
OUTPUTSIZE = int(os.getenv("OUTPUTSIZE", "300"))

# Fuso orario utilizzato per gli orari operativi.
ROME_TIMEZONE = ZoneInfo("Europe/Rome")


# ============================================================
# ORARI OPERATIVI
# ============================================================

# Il bot opera solo durante la sessione americana.
#
# Nota:
# questi orari sono mantenuti come configurati nel progetto:
# 15:30 - 23:00 ora italiana.
#
# Durante il cambio tra ora solare e ora legale americana,
# l'apertura USA può risultare alle 14:30 ora italiana.
# Per ora manteniamo comunque la fascia richiesta.

USA_SESSION_START = dt_time(15, 30)
USA_SESSION_END = dt_time(23, 0)


# ============================================================
# SCANSIONE DEI GRUPPI
# ============================================================

# Numero massimo di titoli analizzati per gruppo.
MAX_SYMBOLS_PER_GROUP = int(
    os.getenv("MAX_SYMBOLS_PER_GROUP", "6")
)

# Tempo minimo tra l'inizio di un gruppo e quello successivo.
GROUP_INTERVAL_SECONDS = int(
    os.getenv("GROUP_INTERVAL_SECONDS", "300")
)

# Pausa tra le singole richieste API.
REQUEST_DELAY_SECONDS = float(
    os.getenv("REQUEST_DELAY_SECONDS", "10")
)

# Se true, stampa i segnali ma non li invia a Telegram.
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# Se true, vengono inviati messaggi Telegram anche per errori API.
SEND_API_ERROR_MESSAGES = (
    os.getenv("SEND_API_ERROR_MESSAGES", "false").lower() == "true"
)

# Se true, viene esclusa l'ultima candela ricevuta.
USE_ONLY_CLOSED_CANDLES = (
    os.getenv("USE_ONLY_CLOSED_CANDLES", "true").lower() == "true"
)


# ============================================================
# PARAMETRI STRATEGIA VWAP RETRACEMENT
# ============================================================

# Moltiplicatore della deviazione standard per le bande VWAP.
VWAP_BAND_MULTIPLIER = float(
    os.getenv("VWAP_BAND_MULTIPLIER", "2.0")
)

# Soglia Stoch RSI per la zona ipervenduto.
STOCH_OVERSOLD = float(
    os.getenv("STOCH_OVERSOLD", "5")
)

# Soglia Stoch RSI per la zona ipercomprato.
STOCH_OVERBOUGHT = float(
    os.getenv("STOCH_OVERBOUGHT", "95")
)

# Periodo ATR.
ATR_PERIOD = int(
    os.getenv("ATR_PERIOD", "14")
)

# Distanza minima dalla banda VWAP.
MIN_BAND_DISTANCE_ATR = float(
    os.getenv("MIN_BAND_DISTANCE_ATR", "0.25")
)

# Periodi indicatori.
RSI_PERIOD = int(
    os.getenv("RSI_PERIOD", "14")
)

STOCH_RSI_PERIOD = int(
    os.getenv("STOCH_RSI_PERIOD", "14")
)

STOCH_K_PERIOD = int(
    os.getenv("STOCH_K_PERIOD", "3")
)

STOCH_D_PERIOD = int(
    os.getenv("STOCH_D_PERIOD", "3")
)


# ============================================================
# VARIABILI D'AMBIENTE
# ============================================================

def get_first_env(*names):
    """
    Restituisce la prima variabile d'ambiente valorizzata.
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
# TITOLI AMERICANI
# ============================================================

# 30 titoli USA:
# - 15 NASDAQ
# - 15 NYSE
#
# La lista è stata scelta privilegiando titoli generalmente
# molto trattati e con buona liquidità.
#
# La liquidità effettiva può comunque variare in base alla
# giornata e all'orario.

SYMBOLS = {
    # --------------------------------------------------------
    # NASDAQ
    # --------------------------------------------------------
    "AAPL": {
        "symbol": "AAPL",
        "exchange": "NASDAQ",
    },
    "TSLA": {
        "symbol": "TSLA",
        "exchange": "NASDAQ",
    },
    "NVDA": {
        "symbol": "NVDA",
        "exchange": "NASDAQ",
    },
    "MSFT": {
        "symbol": "MSFT",
        "exchange": "NASDAQ",
    },
    "AMZN": {
        "symbol": "AMZN",
        "exchange": "NASDAQ",
    },
    "GOOGL": {
        "symbol": "GOOGL",
        "exchange": "NASDAQ",
    },
    "META": {
        "symbol": "META",
        "exchange": "NASDAQ",
    },
    "AMD": {
        "symbol": "AMD",
        "exchange": "NASDAQ",
    },
    "NFLX": {
        "symbol": "NFLX",
        "exchange": "NASDAQ",
    },
    "AVGO": {
        "symbol": "AVGO",
        "exchange": "NASDAQ",
    },
    "INTC": {
        "symbol": "INTC",
        "exchange": "NASDAQ",
    },
    "MU": {
        "symbol": "MU",
        "exchange": "NASDAQ",
    },
    "QCOM": {
        "symbol": "QCOM",
        "exchange": "NASDAQ",
    },
    "AMAT": {
        "symbol": "AMAT",
        "exchange": "NASDAQ",
    },
    "PLTR": {
        "symbol": "PLTR",
        "exchange": "NASDAQ",
    },

    # --------------------------------------------------------
    # NYSE
    # --------------------------------------------------------
    "JPM": {
        "symbol": "JPM",
        "exchange": "NYSE",
    },
    "V": {
        "symbol": "V",
        "exchange": "NYSE",
    },
    "DIS": {
        "symbol": "DIS",
        "exchange": "NYSE",
    },
    "KO": {
        "symbol": "KO",
        "exchange": "NYSE",
    },
    "XOM": {
        "symbol": "XOM",
        "exchange": "NYSE",
    },
    "PFE": {
        "symbol": "PFE",
        "exchange": "NYSE",
    },
    "ORCL": {
        "symbol": "ORCL",
        "exchange": "NYSE",
    },
    "CRM": {
        "symbol": "CRM",
        "exchange": "NYSE",
    },
    "UBER": {
        "symbol": "UBER",
        "exchange": "NYSE",
    },
    "BAC": {
        "symbol": "BAC",
        "exchange": "NYSE",
    },
    "WFC": {
        "symbol": "WFC",
        "exchange": "NYSE",
    },
    "GS": {
        "symbol": "GS",
        "exchange": "NYSE",
    },
    "C": {
        "symbol": "C",
        "exchange": "NYSE",
    },
    "CAT": {
        "symbol": "CAT",
        "exchange": "NYSE",
    },
    "MRK": {
        "symbol": "MRK",
        "exchange": "NYSE",
    },
}


US_EXCHANGES = {
    "NASDAQ",
    "NYSE",
}


# ============================================================
# STATO GLOBALE
# ============================================================

last_signal_by_symbol = {}
last_api_rate_limit_log_time = None

state_lock = threading.Lock()


# ============================================================
# SERVER WEB
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    now = get_local_now()

    session_name, active_symbols, session_end = (
        get_current_session(now)
    )

    active_groups = build_symbol_groups(active_symbols)

    return jsonify({
        "status": "online",
        "service": "vwap-retracement-bot",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "time_rome": now.isoformat(),
        "current_session": session_name or "OFF",
        "symbols_configured": len(SYMBOLS),
        "active_symbols": len(active_symbols),
        "active_groups": len(active_groups),
        "symbols_per_group": MAX_SYMBOLS_PER_GROUP,
        "group_interval_seconds": GROUP_INTERVAL_SECONDS,
        "request_delay_seconds": REQUEST_DELAY_SECONDS,
        "interval": INTERVAL,
        "strategy": "VWAP_RETRACEMENT",
        "vwap_band_multiplier": VWAP_BAND_MULTIPLIER,
        "stoch_oversold": STOCH_OVERSOLD,
        "stoch_overbought": STOCH_OVERBOUGHT,
        "atr_period": ATR_PERIOD,
        "min_band_distance_atr": MIN_BAND_DISTANCE_ATR,
        "use_only_closed_candles": USE_ONLY_CLOSED_CANDLES,
        "dry_run": DRY_RUN,
        "session_start": USA_SESSION_START.strftime("%H:%M"),
        "session_end": USA_SESSION_END.strftime("%H:%M"),
        "session_end_datetime": (
            session_end.isoformat()
            if session_end is not None
            else None
        ),
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "time_rome": get_local_now().isoformat(),
    })


# ============================================================
# FUNZIONI ORARIO
# ============================================================

def get_local_now():
    """
    Restituisce l'ora corrente nel fuso Europe/Rome.
    """

    return datetime.now(ROME_TIMEZONE)


def is_weekday(current_datetime):
    """
    Restituisce false durante sabato e domenica.
    """

    return current_datetime.weekday() < 5


def get_current_session(current_datetime=None):
    """
    Restituisce:

        nome_sessione
        lista_titoli_attivi
        orario_fine_sessione

    L'unica sessione prevista è:

        USA: 15:30 - 23:00 ora italiana
    """

    if current_datetime is None:
        current_datetime = get_local_now()

    if not is_weekday(current_datetime):
        return None, [], None

    current_time = current_datetime.time().replace(
        tzinfo=None
    )

    if (
        USA_SESSION_START
        <= current_time
        < USA_SESSION_END
    ):
        session_end = datetime.combine(
            current_datetime.date(),
            USA_SESSION_END,
            tzinfo=ROME_TIMEZONE,
        )

        return (
            "USA",
            list(SYMBOLS.keys()),
            session_end,
        )

    return None, [], None


def get_next_session_start(current_datetime=None):
    """
    Restituisce il prossimo inizio della sessione USA.
    """

    if current_datetime is None:
        current_datetime = get_local_now()

    current_date = current_datetime.date()

    if is_weekday(current_datetime):
        today_start = datetime.combine(
            current_date,
            USA_SESSION_START,
            tzinfo=ROME_TIMEZONE,
        )

        if current_datetime < today_start:
            return today_start

    next_date = current_date + timedelta(days=1)

    while next_date.weekday() >= 5:
        next_date += timedelta(days=1)

    return datetime.combine(
        next_date,
        USA_SESSION_START,
        tzinfo=ROME_TIMEZONE,
    )


def sleep_until_next_session():
    """
    Mette in pausa il bot fino alla sessione USA successiva.
    """

    now = get_local_now()
    next_start = get_next_session_start(now)

    wait_seconds = max(
        1,
        (next_start - now).total_seconds(),
    )

    log(
        "[SCHEDULE] Nessuna sessione attiva. "
        f"Prossima sessione: "
        f"{next_start.strftime('%Y-%m-%d %H:%M:%S %Z')}. "
        f"Attesa prevista: {wait_seconds / 60:.1f} minuti"
    )

    time.sleep(wait_seconds)


# ============================================================
# LOG
# ============================================================

def log(message):
    """
    Stampa un messaggio con timestamp.
    """

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(
        f"[{now}] {message}",
        flush=True,
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_configured():
    """
    Verifica che Telegram sia configurato.
    """

    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def send_telegram(text):
    """
    Invia un messaggio Telegram.
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
            "[TELEGRAM] "
            f"status={response.status_code} "
            f"response={response.text[:500]}"
        )

        return response.ok

    except requests.RequestException as exc:
        log(
            "[TELEGRAM] Errore di rete: "
            f"{repr(exc)}"
        )
        return False

    except Exception as exc:
        log(
            "[TELEGRAM] Errore imprevisto: "
            f"{repr(exc)}"
        )
        return False


def send_startup_test():
    """
    Invia il messaggio di avvio.
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
            "[CHECK] Telegram non configurato. "
            "Messaggio di avvio non inviato."
        )
        return False

    nasdaq_symbols = [
        name
        for name, config in SYMBOLS.items()
        if config["exchange"] == "NASDAQ"
    ]

    nyse_symbols = [
        name
        for name, config in SYMBOLS.items()
        if config["exchange"] == "NYSE"
    ]

    message = (
        "✅ Bot avviato correttamente\n\n"
        f"Titoli configurati: {len(SYMBOLS)}\n"
        f"Titoli NASDAQ: {len(nasdaq_symbols)}\n"
        f"Titoli NYSE: {len(nyse_symbols)}\n"
        f"Titoli per gruppo: {MAX_SYMBOLS_PER_GROUP}\n"
        f"Timeframe: {INTERVAL}\n"
        f"Bande VWAP: ±{VWAP_BAND_MULTIPLIER} deviazioni standard\n"
        f"Stoch RSI BUY: <= {STOCH_OVERSOLD}\n"
        f"Stoch RSI SELL: >= {STOCH_OVERBOUGHT}\n"
        f"ATR periodo: {ATR_PERIOD}\n"
        f"Distanza minima banda: "
        f"{MIN_BAND_DISTANCE_ATR} × ATR\n"
        f"Modalità demo: {'ON' if DRY_RUN else 'OFF'}\n\n"
        "Orario operativo, ora italiana:\n"
        "• 15:30–23:00: titoli americani\n\n"
        "Strategia attiva:\n"
        "• Ritracciamento VWAP\n"
        "• Stoch RSI estremo con conferma di inversione\n"
        "• Candela completamente oltre la banda VWAP\n"
        "• Hammer o shooting star\n\n"
        "Inizio scansione."
    )

    log("[CHECK] Invio messaggio di avvio Telegram...")

    return send_telegram(message)


# ============================================================
# ERRORI TWELVE DATA
# ============================================================

class TwelveDataRateLimitError(Exception):
    """
    Errore specifico per limite API Twelve Data.
    """


# ============================================================
# DOWNLOAD DATI
# ============================================================

def get_time_series(display_name, config):
    """
    Scarica le candele di un singolo titolo.
    """

    global last_api_rate_limit_log_time

    if not TWELVE_DATA_API_KEY:
        log(
            "[API] TWELVE_DATA_API_KEY mancante"
        )
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

        message = str(
            payload.get("message", "")
        )

        rate_limit_detected = (
            response.status_code == 429
            or payload.get("code") == 429
            or "run out of api credits" in message.lower()
            or "current limit" in message.lower()
            or "rate limit" in message.lower()
        )

        if rate_limit_detected:
            now = datetime.now()

            should_log = (
                last_api_rate_limit_log_time is None
                or (
                    now - last_api_rate_limit_log_time
                ).total_seconds() > 300
            )

            if should_log:
                last_api_rate_limit_log_time = now

                log(
                    "[DEBUG API] Limite API Twelve Data raggiunto: "
                    f"{payload}"
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
                f"[DEBUG API] Colonne mancanti per "
                f"{display_name}: {missing_columns}"
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

        # Evita di analizzare la candela ancora in formazione.
        if USE_ONLY_CLOSED_CANDLES and len(df) > 1:
            df = df.iloc[:-1].copy()

        minimum_required_candles = max(
            100,
            ATR_PERIOD + RSI_PERIOD + STOCH_RSI_PERIOD + 20,
        )

        if len(df) < minimum_required_candles:
            log(
                f"[DATA] Dati insufficienti per {display_name}: "
                f"{len(df)} candele, minimo richiesto "
                f"{minimum_required_candles}"
            )
            return None

        return df.reset_index(drop=True)

    except TwelveDataRateLimitError:
        raise

    except requests.RequestException as exc:
        log(
            f"[API] Errore di rete per {display_name}: "
            f"{repr(exc)}"
        )
        return None

    except Exception as exc:
        log(
            f"[API] Errore imprevisto per {display_name}: "
            f"{repr(exc)}"
        )
        return None


# ============================================================
# INDICATORI
# ============================================================

def calculate_rsi(close, period=14):
    """
    Calcola RSI con il metodo Wilder.
    """

    delta = close.diff()

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

    rs = average_gain / average_loss.replace(
        0,
        np.nan,
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    rsi = rsi.where(
        average_loss != 0,
        100,
    )

    rsi = rsi.where(
        ~(
            (average_gain == 0)
            & (average_loss == 0)
        ),
        50,
    )

    return rsi


def calculate_stoch_rsi(
    close,
    rsi_period=14,
    stoch_period=14,
    k_period=3,
    d_period=3,
):
    """
    Calcola Stoch RSI espresso su scala 0-100.
    """

    rsi = calculate_rsi(
        close,
        period=rsi_period,
    )

    lowest_rsi = rsi.rolling(
        window=stoch_period,
        min_periods=stoch_period,
    ).min()

    highest_rsi = rsi.rolling(
        window=stoch_period,
        min_periods=stoch_period,
    ).max()

    rsi_range = (
        highest_rsi - lowest_rsi
    ).replace(
        0,
        np.nan,
    )

    stoch_rsi = (
        (rsi - lowest_rsi)
        / rsi_range
        * 100
    )

    stoch_k = stoch_rsi.rolling(
        window=k_period,
        min_periods=k_period,
    ).mean()

    stoch_d = stoch_k.rolling(
        window=d_period,
        min_periods=d_period,
    ).mean()

    return (
        rsi,
        stoch_rsi,
        stoch_k,
        stoch_d,
    )


def calculate_atr(df, period=14):
    """
    Calcola Average True Range.
    """

    previous_close = df["close"].shift(1)

    true_range_components = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    )

    true_range = true_range_components.max(
        axis=1
    )

    atr = true_range.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    return atr


def calculate_session_vwap(df):
    """
    Calcola VWAP ancorato all'inizio di ogni giornata.
    """

    typical_price = (
        df["high"]
        + df["low"]
        + df["close"]
    ) / 3

    volume = df["volume"].fillna(0.0)

    working = pd.DataFrame(index=df.index)

    working["typical_price"] = typical_price
    working["volume"] = volume
    working["price_volume"] = (
        working["typical_price"]
        * working["volume"]
    )

    date_key = df["datetime"].dt.date

    cumulative_price_volume = (
        working
        .groupby(date_key)["price_volume"]
        .cumsum()
    )

    cumulative_volume = (
        working
        .groupby(date_key)["volume"]
        .cumsum()
    )

    # Se il volume non è disponibile o vale zero,
    # viene usata una VWAP basata sul prezzo tipico.
    vwap_from_volume = (
        cumulative_price_volume
        / cumulative_volume.replace(0, np.nan)
    )

    vwap_from_price = (
        working
        .groupby(date_key)["typical_price"]
        .expanding()
        .mean()
        .reset_index(level=0, drop=True)
    )

    vwap_from_price = vwap_from_price.reindex(
        df.index
    )

    vwap = vwap_from_volume.fillna(
        vwap_from_price
    )

    return vwap


def add_indicators(df):
    """
    Aggiunge tutti gli indicatori necessari.
    """

    result = df.copy()

    result["vwap"] = calculate_session_vwap(
        result
    )

    result["vwap_std"] = (
        result
        .groupby(result["datetime"].dt.date)["close"]
        .transform(
            lambda values: values.expanding(
                min_periods=2
            ).std()
        )
    )

    result["vwap_std"] = result["vwap_std"].fillna(
        result["close"].rolling(
            window=20,
            min_periods=2,
        ).std()
    )

    result["vwap_upper"] = (
        result["vwap"]
        + (
            result["vwap_std"]
            * VWAP_BAND_MULTIPLIER
        )
    )

    result["vwap_lower"] = (
        result["vwap"]
        - (
            result["vwap_std"]
            * VWAP_BAND_MULTIPLIER
        )
    )

    result["atr"] = calculate_atr(
        result,
        period=ATR_PERIOD,
    )

    (
        result["rsi"],
        result["stoch_rsi"],
        result["stoch_k"],
        result["stoch_d"],
    ) = calculate_stoch_rsi(
        result["close"],
        rsi_period=RSI_PERIOD,
        stoch_period=STOCH_RSI_PERIOD,
        k_period=STOCH_K_PERIOD,
        d_period=STOCH_D_PERIOD,
    )

    return result


# ============================================================
# PATTERN CANDELE
# ============================================================

def candle_is_hammer(row):
    """
    Riconosce una candela hammer rialzista.

    Non richiede un colore specifico, ma privilegia:
    - corpo relativamente piccolo;
    - lunga ombra inferiore;
    - ombra superiore ridotta.
    """

    candle_range = row["high"] - row["low"]

    if candle_range <= 0:
        return False

    body = abs(
        row["close"] - row["open"]
    )

    upper_wick = (
        row["high"]
        - max(row["open"], row["close"])
    )

    lower_wick = (
        min(row["open"], row["close"])
        - row["low"]
    )

    return (
        body <= candle_range * 0.40
        and lower_wick >= body * 2.0
        and upper_wick <= candle_range * 0.35
    )


def candle_is_shooting_star(row):
    """
    Riconosce una shooting star ribassista.

    Privilegia:
    - corpo relativamente piccolo;
    - lunga ombra superiore;
    - ombra inferiore ridotta.
    """

    candle_range = row["high"] - row["low"]

    if candle_range <= 0:
        return False

    body = abs(
        row["close"] - row["open"]
    )

    upper_wick = (
        row["high"]
        - max(row["open"], row["close"])
    )

    lower_wick = (
        min(row["open"], row["close"])
        - row["low"]
    )

    return (
        body <= candle_range * 0.40
        and upper_wick >= body * 2.0
        and lower_wick <= candle_range * 0.35
    )


# ============================================================
# GENERAZIONE SEGNALE
# ============================================================

def safe_float(value):
    """
    Converte un valore in float oppure restituisce None.
    """

    try:
        if pd.isna(value):
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def evaluate_signal(display_name, df):
    """
    Valuta l'ultima candela chiusa.

    BUY:
        - candela completamente sotto la banda inferiore;
        - distanza minima dalla banda >= 0.25 ATR;
        - Stoch RSI precedente <= 5;
        - Stoch RSI attuale in risalita;
        - candela hammer.

    SELL:
        - candela completamente sopra la banda superiore;
        - distanza minima dalla banda >= 0.25 ATR;
        - Stoch RSI precedente >= 95;
        - Stoch RSI attuale in discesa;
        - candela shooting star.
    """

    if df is None or len(df) < 3:
        return None

    working = add_indicators(df)

    if len(working) < 3:
        return None

    previous = working.iloc[-2]
    current = working.iloc[-1]

    required_values = [
        current["vwap"],
        current["vwap_upper"],
        current["vwap_lower"],
        current["atr"],
        current["stoch_rsi"],
        previous["stoch_rsi"],
    ]

    if any(
        pd.isna(value)
        for value in required_values
    ):
        return None

    atr = float(current["atr"])

    if atr <= 0:
        return None

    current_stoch = float(
        current["stoch_rsi"]
    )

    previous_stoch = float(
        previous["stoch_rsi"]
    )

    lower_band = float(
        current["vwap_lower"]
    )

    upper_band = float(
        current["vwap_upper"]
    )

    candle_high = float(
        current["high"]
    )

    candle_low = float(
        current["low"]
    )

    distance_below_lower_band = (
        lower_band - candle_high
    )

    distance_above_upper_band = (
        candle_low - upper_band
    )

    minimum_distance = (
        MIN_BAND_DISTANCE_ATR * atr
    )

    is_completely_below_lower_band = (
        candle_high < lower_band
    )

    is_completely_above_upper_band = (
        candle_low > upper_band
    )

    is_buy_stoch_reversal = (
        previous_stoch <= STOCH_OVERSOLD
        and current_stoch > previous_stoch
    )

    is_sell_stoch_reversal = (
        previous_stoch >= STOCH_OVERBOUGHT
        and current_stoch < previous_stoch
    )

    is_hammer = candle_is_hammer(
        current
    )

    is_shooting_star = candle_is_shooting_star(
        current
    )

    candle_time = current["datetime"]

    if (
        is_completely_below_lower_band
        and distance_below_lower_band >= minimum_distance
        and is_buy_stoch_reversal
        and is_hammer
    ):
        return {
            "side": "BUY",
            "symbol": display_name,
            "datetime": candle_time,
            "price": float(current["close"]),
            "vwap": float(current["vwap"]),
            "band": lower_band,
            "atr": atr,
            "stoch_previous": previous_stoch,
            "stoch_current": current_stoch,
            "distance": distance_below_lower_band,
            "minimum_distance": minimum_distance,
            "pattern": "HAMMER",
            "reason": (
                "Candela completamente sotto la banda VWAP "
                "con distanza minima ATR e inversione "
                "rialzista dello Stoch RSI"
            ),
        }

    if (
        is_completely_above_upper_band
        and distance_above_upper_band >= minimum_distance
        and is_sell_stoch_reversal
        and is_shooting_star
    ):
        return {
            "side": "SELL",
            "symbol": display_name,
            "datetime": candle_time,
            "price": float(current["close"]),
            "vwap": float(current["vwap"]),
            "band": upper_band,
            "atr": atr,
            "stoch_previous": previous_stoch,
            "stoch_current": current_stoch,
            "distance": distance_above_upper_band,
            "minimum_distance": minimum_distance,
            "pattern": "SHOOTING STAR",
            "reason": (
                "Candela completamente sopra la banda VWAP "
                "con distanza minima ATR e inversione "
                "ribassista dello Stoch RSI"
            ),
        }

    return None


# ============================================================
# FORMATTAZIONE SEGNALI
# ============================================================

def format_signal_message(signal):
    """
    Crea il messaggio Telegram per un segnale.
    """

    candle_datetime = signal["datetime"]

    if hasattr(candle_datetime, "strftime"):
        candle_datetime_text = candle_datetime.strftime(
            "%Y-%m-%d %H:%M"
        )

    else:
        candle_datetime_text = str(
            candle_datetime
        )

    return (
        f"🚨 SEGNALE {signal['side']}\n\n"
        f"Titolo: {signal['symbol']}\n"
        f"Timeframe: {INTERVAL}\n"
        f"Candela: {candle_datetime_text}\n"
        f"Prezzo chiusura: {signal['price']:.4f}\n"
        f"VWAP: {signal['vwap']:.4f}\n"
        f"Banda VWAP: {signal['band']:.4f}\n"
        f"ATR: {signal['atr']:.4f}\n"
        f"Distanza banda: {signal['distance']:.4f}\n"
        f"Distanza minima: {signal['minimum_distance']:.4f}\n"
        f"Stoch RSI precedente: "
        f"{signal['stoch_previous']:.2f}\n"
        f"Stoch RSI attuale: "
        f"{signal['stoch_current']:.2f}\n"
        f"Pattern: {signal['pattern']}\n\n"
        f"Motivazione:\n{signal['reason']}"
    )


def signal_already_sent(signal):
    """
    Evita di inviare più volte lo stesso segnale
    sulla stessa candela.
    """

    symbol = signal["symbol"]
    side = signal["side"]
    candle_datetime = str(
        signal["datetime"]
    )

    signal_key = (
        symbol,
        side,
        candle_datetime,
    )

    with state_lock:
        if signal_key in last_signal_by_symbol:
            return True

        last_signal_by_symbol[signal_key] = True

    return False


def process_signal(signal):
    """
    Gestisce un segnale valido.
    """

    if signal is None:
        return

    if signal_already_sent(signal):
        log(
            f"[SIGNAL] Segnale già inviato: "
            f"{signal['symbol']} {signal['side']}"
        )
        return

    message = format_signal_message(
        signal
    )

    log(
        f"[SIGNAL] {signal['side']} "
        f"{signal['symbol']} "
        f"price={signal['price']:.4f} "
        f"pattern={signal['pattern']}"
    )

    if DRY_RUN:
        log(
            "[DRY_RUN] Messaggio Telegram non inviato."
        )
        return

    send_telegram(message)


# ============================================================
# GRUPPI
# ============================================================

def build_symbol_groups(symbol_names):
    """
    Divide i titoli in gruppi.
    """

    return [
        symbol_names[index:index + MAX_SYMBOLS_PER_GROUP]
        for index in range(
            0,
            len(symbol_names),
            MAX_SYMBOLS_PER_GROUP,
        )
    ]


def scan_symbol(display_name):
    """
    Scarica, analizza e processa un titolo.
    """

    config = SYMBOLS.get(display_name)

    if config is None:
        log(
            f"[SCAN] Configurazione mancante per "
            f"{display_name}"
        )
        return

    log(
        f"[SCAN] Analisi {display_name} "
        f"({config['exchange']})"
    )

    try:
        df = get_time_series(
            display_name,
            config,
        )

        if df is None:
            return

        signal = evaluate_signal(
            display_name,
            df,
        )

        if signal is None:
            log(
                f"[SCAN] Nessun segnale per "
                f"{display_name}"
            )
            return

        process_signal(signal)

    except TwelveDataRateLimitError:
        log(
            "[SCAN] Limite API raggiunto. "
            "Interruzione della scansione corrente."
        )
        raise

    except Exception as exc:
        log(
            f"[SCAN] Errore analizzando "
            f"{display_name}: {repr(exc)}"
        )


def scan_group(group_number, groups):
    """
    Analizza tutti i titoli appartenenti a un gruppo.
    """

    if group_number >= len(groups):
        return

    group = groups[group_number]

    log(
        f"[GROUP] Avvio gruppo "
        f"{group_number + 1}/{len(groups)}: "
        f"{', '.join(group)}"
    )

    for index, display_name in enumerate(group):
        scan_symbol(display_name)

        if index < len(group) - 1:
            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    log(
        f"[GROUP] Fine gruppo "
        f"{group_number + 1}/{len(groups)}"
    )


# ============================================================
# LOOP PRINCIPALE
# ============================================================

def scanner_loop():
    """
    Loop principale del bot.

    Analizza i 30 titoli americani divisi in gruppi.
    """

    log("[BOT] Scanner avviato.")
    log(
        f"[BOT] Titoli totali: {len(SYMBOLS)}"
    )
    log(
        f"[BOT] Titoli per gruppo: "
        f"{MAX_SYMBOLS_PER_GROUP}"
    )
    log(
        f"[BOT] Numero gruppi: "
        f"{len(build_symbol_groups(list(SYMBOLS.keys())))}"
    )
    log(
        f"[BOT] Timeframe: {INTERVAL}"
    )
    log(
        f"[BOT] Modalità DRY_RUN: {DRY_RUN}"
    )

    send_startup_test()

    while True:
        try:
            now = get_local_now()

            session_name, active_symbols, session_end = (
                get_current_session(now)
            )

            if session_name is None:
                sleep_until_next_session()
                continue

            groups = build_symbol_groups(
                active_symbols
            )

            log(
                f"[SESSION] Sessione attiva: "
                f"{session_name}"
            )

            log(
                f"[SESSION] Titoli attivi: "
                f"{len(active_symbols)}"
            )

            log(
                f"[SESSION] Gruppi attivi: "
                f"{len(groups)}"
            )

            for group_number in range(
                len(groups)
            ):
                now = get_local_now()

                current_session, _, _ = (
                    get_current_session(now)
                )

                if current_session is None:
                    log(
                        "[SESSION] Sessione terminata "
                        "durante la scansione."
                    )
                    break

                scan_started_at = time.time()

                try:
                    scan_group(
                        group_number,
                        groups,
                    )

                except TwelveDataRateLimitError:
                    log(
                        "[BOT] Limite API rilevato. "
                        "Pausa di 5 minuti."
                    )
                    time.sleep(300)
                    break

                elapsed_seconds = (
                    time.time()
                    - scan_started_at
                )

                remaining_seconds = max(
                    0,
                    GROUP_INTERVAL_SECONDS
                    - elapsed_seconds,
                )

                if group_number < len(groups) - 1:
                    log(
                        f"[GROUP] Pausa di "
                        f"{remaining_seconds:.0f} secondi "
                        "prima del gruppo successivo."
                    )

                    time.sleep(
                        remaining_seconds
                    )

            else:
                log(
                    "[SESSION] Ciclo completo terminato. "
                    "Ripartenza dal primo gruppo."
                )

                time.sleep(5)

        except Exception as exc:
            log(
                f"[BOT] Errore nel loop principale: "
                f"{repr(exc)}"
            )

            time.sleep(60)


# ============================================================
# AVVIO FLASK
# ============================================================

def start_flask_server():
    """
    Avvia il server Flask in un thread separato.
    """

    log(
        f"[WEB] Avvio server Flask sulla porta {PORT}"
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False,
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    flask_thread = threading.Thread(
        target=start_flask_server,
        daemon=True,
    )

    flask_thread.start()

    scanner_loop()
