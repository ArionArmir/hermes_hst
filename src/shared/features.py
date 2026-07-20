"""
Fonte unica di verità per le feature del modello ML.

Usata SIA dal training (src/training/feature_engine.py) SIA dall'inference
(src/inference/main.py): qualunque modifica qui vale automaticamente per
entrambi. Non duplicare mai questi calcoli altrove — la duplicazione
training/inference è stata la causa del train/serve skew corretto in questo
modulo (vedi docs/IMPROVEMENT_PLAN.md, punti A1/M1).

Regole per aggiungere una feature:
- input: candele OHLCV di UN SOLO simbolo (mai concatenare simboli prima);
- deve essere scale-invariant: mai valori in scala prezzo o volume assoluto
  (il modello è unico per tutti i simboli — una feature che porta la scala
  del simbolo permette al modello di "riconoscere" il simbolo dal prezzo);
- deve dipendere solo da finestre rolling, mai dalla lunghezza totale del
  DataFrame (in training arriva ~1 anno di candele, in inference ~200:
  una cumsum non finestrata, ad es., cambierebbe valore tra i due contesti).

Lezione appresa (2026-07-16): aggiungere TRASFORMAZIONI degli stessi prezzi
(feature di regime/calendario a orizzonte lungo) peggiora il modello — sono
nuovi modi di overfittare, non nuova informazione. Le feature di order flow
qui sotto funzionano perché sono informazione GENUINAMENTE NUOVA: chi ha
comprato aggressivamente e quanto si è scambiato, dati non ricostruibili
dall'OHLC. Validato con walk-forward su due strutture di fold indipendenti
(fold peggiore da -24.9 a -6.1, trade invariati).
"""
import numpy as np
import pandas as pd

# Ordine = ordine delle colonne viste dal modello. Il modello viene salvato
# con questi nomi e l'inference li rivalida al caricamento.
FEATURE_COLS = [
    'rsi',
    'sma20_ratio',
    'sma50_ratio',
    'sma20_sma50_ratio',
    'volatility',
    'momentum',
    'volume_ratio',
    'returns',
    'atr_pct',
    'bb_position',
    'macd_hist_norm',
    'obv_ratio',
    'fib_position',
    'fib_618_distance',
    # --- ORDER FLOW (microstruttura, non derivabile dall'OHLC) ---
    'taker_buy_ratio',       # quota di volume comprata aggressivamente
    'taker_ratio_20',        # pressione di flusso persistente
    'trade_intensity',       # attività vs norma recente (la più informativa)
    'avg_trade_size_ratio',  # taglia media dei trade: flusso "whale" vs retail
]

# Colonne grezze richieste in input per l'order flow. Binance le restituisce
# in ogni kline (campi 8 e 9) ma ccxt.fetch_ohlcv le scarta: per questo
# src/data_collector.py usa il REST diretto.
FLOW_INPUT_COLS = ('taker_buy_base', 'n_trades')

# Finestra più lunga usata sotto è 50 (SMA50) + 1 per il diff dei returns;
# 64 lascia margine. Sotto questa soglia l'ultima riga contiene NaN.
MIN_CANDLES = 64

_TIMEFRAME_UNIT_MINUTES = {'m': 1, 'h': 60, 'd': 1440}


def timeframe_minutes(timeframe: str) -> int:
    """'1m' → 1, '15m' → 15, '1h' → 60, '4h' → 240, '1d' → 1440.
    Solleva ValueError su formati non riconosciuti: meglio fallire subito che
    lasciare che training e inference lavorino su barre diverse."""
    tf = timeframe.strip().lower()
    unit = _TIMEFRAME_UNIT_MINUTES.get(tf[-1]) if tf else None
    if unit is None or not tf[:-1].isdigit():
        raise ValueError(f"Timeframe non riconosciuto: {timeframe!r}")
    return int(tf[:-1]) * unit


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calcola le feature su candele di un singolo simbolo. Colonne richieste:
    open, high, low, close, volume + taker_buy_base, n_trades (order flow).
    Restituisce un DataFrame con le sole colonne FEATURE_COLS, stesso indice
    dell'input, NaN nelle righe di warmup: è il chiamante a decidere come
    gestirle (dropna in training, scarto della riga in inference)."""
    missing = [c for c in FLOW_INPUT_COLS if c not in df.columns]
    if missing:
        # Fallire subito e a voce alta: un parquet salvato prima
        # dell'order flow produrrebbe altrimenti feature NaN e righe
        # silenziosamente scartate (o peggio, un modello addestrato su meno
        # dati senza che nessuno se ne accorga).
        raise ValueError(
            f"Colonne di order flow mancanti: {missing}. Le candele devono venire da "
            f"DataCollector/CandleFeed aggiornati (REST completo). Se il parquet è "
            f"vecchio, rilanciare train_all_models.py per il ridownload automatico."
        )

    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']

    out = pd.DataFrame(index=df.index)

    returns = close.pct_change()
    out['returns'] = returns

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    out['rsi'] = 100 - (100 / (1 + gain / loss))

    # Medie mobili — solo come rapporti, mai in scala prezzo
    sma_20 = close.rolling(20).mean()
    sma_50 = close.rolling(50).mean()
    out['sma20_ratio'] = close / sma_20 - 1
    out['sma50_ratio'] = close / sma_50 - 1
    out['sma20_sma50_ratio'] = sma_20 / sma_50 - 1

    out['volatility'] = returns.rolling(20).std() * np.sqrt(252)
    out['momentum'] = close.pct_change(10)
    out['volume_ratio'] = volume / volume.rolling(20).mean()

    # ATR (percentuale del prezzo)
    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    out['atr_pct'] = true_range.rolling(14).mean() / close

    # Bollinger Bands
    bb_middle = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_middle + 2 * bb_std
    bb_lower = bb_middle - 2 * bb_std
    # Epsilon anti-divisione-per-zero sempre RELATIVI alla scala della serie
    # (1e-9 * close, mai costanti assolute): un epsilon fisso renderebbe la
    # feature leggermente dipendente dalla scala del prezzo.
    out['bb_position'] = (close - bb_lower) / (bb_upper - bb_lower + 1e-9 * close)

    # MACD normalizzato sul prezzo
    exp1 = close.ewm(span=12, adjust=False).mean()
    exp2 = close.ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    out['macd_hist_norm'] = (macd - macd_signal) / close

    # OBV finestrato: somma con segno del volume sulle ultime 20 candele,
    # normalizzata sul volume totale della stessa finestra → in [-1, 1].
    # (Sostituisce la vecchia cumsum non finestrata, che dipendeva dalla
    # lunghezza del DataFrame e quindi divergeva tra training e inference.)
    signed_volume = np.sign(close.diff()) * volume
    out['obv_ratio'] = signed_volume.rolling(20).sum() / (volume.rolling(20).sum() + 1e-12)

    # Fibonacci sul range delle ultime 20 candele
    high_20 = high.rolling(20).max()
    low_20 = low.rolling(20).min()
    diff_20 = high_20 - low_20
    out['fib_position'] = (close - low_20) / (diff_20 + 1e-9 * close)
    fib_618 = low_20 + 0.618 * diff_20
    out['fib_618_distance'] = (close - fib_618) / close

    # --- ORDER FLOW ---
    # Quota del volume comprata AGGRESSIVAMENTE (chi colpisce l'ask): 0-1,
    # già adimensionale. ~0.5 = equilibrio tra compratori e venditori attivi.
    taker_buy = df['taker_buy_base']
    n_trades = df['n_trades']
    taker_ratio = taker_buy / (volume + 1e-12)
    out['taker_buy_ratio'] = taker_ratio
    out['taker_ratio_20'] = taker_ratio.rolling(20).mean()

    # Attività vs norma recente: rapporto → scale-invariant tra simboli
    # (BTC fa ~300k trade/ora, TRX molti meno: conta lo scostamento, non il livello)
    out['trade_intensity'] = n_trades / (n_trades.rolling(20).mean() + 1e-12)

    # Taglia media dei trade rispetto alla propria norma: proxy di flusso
    # istituzionale ("whale") vs frammentato (retail)
    avg_trade_size = volume / (n_trades + 1e-12)
    out['avg_trade_size_ratio'] = avg_trade_size / (avg_trade_size.rolling(20).mean() + 1e-12)

    return out[FEATURE_COLS]


def compute_latest_features(df: pd.DataFrame) -> pd.DataFrame | None:
    """Feature dell'ultima candela, come DataFrame 1×N con i nomi di colonna
    (XGBoost valida i nomi solo se riceve un DataFrame: mai convertirlo in
    ndarray). None se le candele non bastano o la riga contiene NaN."""
    if df is None or len(df) < MIN_CANDLES:
        return None
    latest = compute_features(df).iloc[[-1]]
    if latest.isna().any().any():
        return None
    return latest
