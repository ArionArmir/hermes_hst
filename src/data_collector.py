import pandas as pd
import requests
import time
from datetime import datetime, timedelta
from loguru import logger
import os

from src.shared.features import FLOW_INPUT_COLS

KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"

# Profondità storica richiesta al primo download. 2600 giorni (~7,1 anni)
# copre TUTTA la storia disponibile su Binance Futures (BTCUSDT parte dal
# 2019-09; i simboli più giovani restituiscono semplicemente meno candele).
#
# Perché non 365 come prima: con 1 anno si hanno ~15-40 trade per finestra di
# valutazione, ben sotto la soglia di rilevabilità di un edge — servono
# N=(2σ/μ)² trade, cioè ~250 per un edge di 0.5 USDT/trade. A 1 anno si
# misura rumore, non alpha: due vintage di dati davano conclusioni opposte
# sullo stesso codice. ~6 anni danno ~500 trade e 4-5 regimi di mercato
# distinti (crollo 2020, bull 2021, bear 2022, ripresa 2023...).
HISTORY_DAYS = 2600

# Tetto dell'endpoint klines: 1500 candele per richiesta. Usarlo tutto riduce
# di un terzo le chiamate (rilevante ora che si scaricano ~60k barre/simbolo).
KLINES_MAX_LIMIT = 1500

# Binance restituisce 12 campi per candela; ccxt.fetch_ohlcv ne normalizza solo
# 6 e SCARTA taker_buy_base e n_trades — cioè l'order flow, informazione non
# ricostruibile dall'OHLC e la sola aggiunta che abbia superato il gate
# walk-forward (vedi src/shared/features.py). Per questo qui si usa il REST
# diretto invece di ccxt.
_KLINE_FIELDS = [
    'timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
    'quote_volume', 'n_trades', 'taker_buy_base', 'taker_buy_quote', 'ignore',
]
_KEEP = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'n_trades', 'taker_buy_base']


class DataCollector:
    def fetch_ohlcv(self, symbol: str, timeframe: str = '1h', limit: int = 1000, since: int = None):
        """Scarica klines COMPLETE da Binance Futures (OHLCV + order flow).
        `symbol` accetta sia 'BTC/USDT' (formato ccxt, storico) sia 'BTCUSDT'."""
        try:
            params = {
                'symbol': symbol.replace('/', '').upper(),
                'interval': timeframe,
                'limit': min(limit, 1500),   # tetto dell'endpoint
            }
            if since is not None:
                params['startTime'] = int(since)
            resp = requests.get(KLINES_URL, params=params, timeout=20)
            raw = resp.json()
            if not isinstance(raw, list):
                logger.error(f"❌ Risposta klines inattesa per {symbol}: {raw!r:.200}")
                return pd.DataFrame()
            if not raw:
                return pd.DataFrame()
            df = pd.DataFrame(raw, columns=_KLINE_FIELDS)[_KEEP].astype(float)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            logger.error(f"❌ Errore fetch {symbol}: {e}")
            return pd.DataFrame()

    def download_historical(self, symbol: str, timeframe: str = '1h', days: int = HISTORY_DAYS):
        """Scarica dati storici per un periodo specifico. Se `days` risale a
        prima della quotazione del simbolo, Binance restituisce semplicemente
        dalla prima candela esistente (nessun errore)."""
        logger.info(f"📥 Scaricando {days} giorni di {symbol}...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        since = int(start_date.timestamp() * 1000)

        all_data = []
        while since < int(end_date.timestamp() * 1000):
            df = self.fetch_ohlcv(symbol, timeframe, limit=KLINES_MAX_LIMIT, since=since)
            if df.empty:
                break
            all_data.append(df)
            since = int(df.index[-1].timestamp() * 1000) + 1
            time.sleep(0.3)  # rate limit (peso klines ~5 su 2400/min)

        if all_data:
            df_final = pd.concat(all_data).drop_duplicates().sort_index()
            df_final = df_final[~df_final.index.duplicated(keep='first')]
            return df_final
        return pd.DataFrame()

    def update_historical(self, symbol_ccxt: str, symbol_clean: str, timeframe: str = '1h',
                         days: int = HISTORY_DAYS) -> pd.DataFrame:
        """Carica il parquet e lo estende con le candele mancanti fino a ora
        (parquet assente → scarica `days` giorni da zero). Senza questo refresh
        il retraining schedulato riaddestrerebbe per sempre sugli stessi
        dati congelati alla data del primo download.

        L'ultima candela salvata viene riscaricata e sostituita (keep='last'):
        al download precedente poteva essere ancora in formazione."""
        df = self.load_historical(symbol_clean, timeframe)

        # Un parquet salvato prima dell'order flow non ha le colonne taker:
        # estenderlo lascerebbe NaN su tutto lo storico vecchio (e le righe
        # sparirebbero al dropna del training, silenziosamente). Si riscarica
        # da zero — self-healing, nessun intervento manuale richiesto.
        if not df.empty and any(c not in df.columns for c in FLOW_INPUT_COLS):
            logger.info(f"🔄 {symbol_clean}: parquet senza order flow, ridownload completo")
            df = pd.DataFrame()

        # NB: l'estensione incrementale aggiunge solo candele NUOVE — non
        # recupera storia più VECCHIA di quella già nel parquet. Per
        # approfondire lo storico (es. il passaggio da 365 a HISTORY_DAYS)
        # basta cancellare i parquet: data/ è cache rigenerabile.
        if df.empty:
            df = self.download_historical(symbol_ccxt, timeframe, days=days)
            if not df.empty:
                self.save_to_parquet(df, symbol_clean, timeframe)
            return df

        since = int(df.index.max().timestamp() * 1000)
        new_batches = []
        while True:
            batch = self.fetch_ohlcv(symbol_ccxt, timeframe, limit=KLINES_MAX_LIMIT, since=since)
            if batch.empty:
                break
            new_batches.append(batch)
            if len(batch) < KLINES_MAX_LIMIT:
                break
            since = int(batch.index[-1].timestamp() * 1000) + 1
            time.sleep(0.3)  # rate limit

        if new_batches:
            before = len(df)
            df = pd.concat([df] + new_batches)
            df = df[~df.index.duplicated(keep='last')].sort_index()
            self.save_to_parquet(df, symbol_clean, timeframe)
            logger.info(
                f"🔄 {symbol_clean}: parquet aggiornato con {len(df) - before} candele nuove "
                f"(ultima: {df.index.max()})"
            )
        return df

    def save_to_parquet(self, df: pd.DataFrame, symbol: str, timeframe: str = '1h'):
        """Salva i dati in formato Parquet"""
        os.makedirs('data/historical', exist_ok=True)
        filename = f"data/historical/{symbol}_{timeframe}.parquet"
        df.to_parquet(filename)
        logger.info(f"💾 Dati salvati in {filename} ({len(df)} righe)")
        return filename

    def load_historical(self, symbol: str, timeframe: str = '1h') -> pd.DataFrame:
        """Carica dati da parquet"""
        filename = f"data/historical/{symbol}_{timeframe}.parquet"
        if os.path.exists(filename):
            return pd.read_parquet(filename)
        return pd.DataFrame()


if __name__ == "__main__":
    collector = DataCollector()

    # Scarica 60 giorni di BTCUSDT e ETHUSDT
    for symbol in ['BTC/USDT', 'ETH/USDT']:
        df = collector.download_historical(symbol, timeframe='1h', days=60)
        if not df.empty:
            collector.save_to_parquet(df, symbol.replace('/', ''))

    logger.info("✅ Download completato")
