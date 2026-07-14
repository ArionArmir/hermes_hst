"""
Candele OHLCV live per l'inference, via REST Binance Futures (fapi/v1/klines).

Perché REST e non i tick del WebSocket: il modello è addestrato su candele
(stesso timeframe di config), quindi anche l'inference deve calcolare le
feature su candele identiche — ricostruirle dai tick richiederebbe ore di
warmup a ogni riavvio e reintrodurrebbe il rischio di skew. Una chiamata al
minuto per simbolo è trascurabile per i rate limit (peso klines ≈ 5 su
2400/min disponibili).
"""
import time
from typing import Dict, Optional

import aiohttp
import pandas as pd
from loguru import logger

KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"


class CandleFeed:
    def __init__(self, interval: str = "1h", limit: int = 200, refresh_seconds: int = 60):
        self.interval = interval
        self.limit = limit
        self.refresh_seconds = refresh_seconds
        self._cache: Dict[str, pd.DataFrame] = {}
        self._last_fetch: Dict[str, float] = {}

    async def get_candles(self, symbol: str) -> Optional[pd.DataFrame]:
        """Ultime `limit` candele CHIUSE per il simbolo (maiuscolo, es.
        'BTCUSDT'). Usa la cache se più recente di refresh_seconds; in caso
        di errore di rete restituisce l'ultima versione valida in cache."""
        now = time.monotonic()
        cached = self._cache.get(symbol)
        if cached is not None and now - self._last_fetch.get(symbol, 0.0) < self.refresh_seconds:
            return cached

        try:
            params = {"symbol": symbol, "interval": self.interval, "limit": self.limit}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    KLINES_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    raw = await resp.json()
        except Exception as e:
            logger.error(f"❌ Errore fetch candele {symbol}: {e}")
            return cached

        if not isinstance(raw, list) or len(raw) < 2:
            logger.warning(f"⚠️ Risposta klines inattesa per {symbol}: {raw!r:.200}")
            return cached

        df = pd.DataFrame(
            raw,
            columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore",
            ],
        )
        df = df[["open_time", "open", "high", "low", "close", "volume"]].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("open_time")

        # L'ultima riga è la candela in formazione (parziale): la scartiamo.
        # In training tutte le candele sono complete; usare una candela
        # parziale (volume_ratio artificialmente basso, ecc.) reintrodurrebbe
        # skew. Effetto collaterale voluto: le feature cambiano solo alla
        # chiusura di ogni candela.
        df = df.iloc[:-1]

        self._cache[symbol] = df
        self._last_fetch[symbol] = now
        return df
