import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta
from loguru import logger
import os

class DataCollector:
    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })

    def fetch_ohlcv(self, symbol: str, timeframe: str = '1h', limit: int = 1000, since: int = None):
        """Scarica dati OHLCV da Binance Futures"""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            logger.error(f"❌ Errore fetch {symbol}: {e}")
            return pd.DataFrame()

    def download_historical(self, symbol: str, timeframe: str = '1h', days: int = 30):
        """Scarica dati storici per un periodo specifico"""
        logger.info(f"📥 Scaricando {days} giorni di {symbol}...")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        since = int(start_date.timestamp() * 1000)

        all_data = []
        while since < int(end_date.timestamp() * 1000):
            df = self.fetch_ohlcv(symbol, timeframe, limit=1000, since=since)
            if df.empty:
                break
            all_data.append(df)
            since = int(df.index[-1].timestamp() * 1000) + 1
            time.sleep(0.5)  # rate limit

        if all_data:
            df_final = pd.concat(all_data).drop_duplicates().sort_index()
            df_final = df_final[~df_final.index.duplicated(keep='first')]
            return df_final
        return pd.DataFrame()

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
