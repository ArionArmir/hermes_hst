"""
Candele OHLCV live via REST Binance Futures (fapi/v1/klines).

Due consumatori:
- inference: feature del modello sulle stesse candele del training
  (ricostruirle dai tick richiederebbe ore di warmup a ogni riavvio e
  reintrodurrebbe il rischio di train/serve skew);
- engine: bootstrap di ATRExitModel e VolumePatternAnalyzer all'avvio,
  che poi restano aggiornati dallo stream WebSocket @kline.

Una chiamata al minuto per simbolo è trascurabile per i rate limit
(peso klines ≈ 5 su 2400/min disponibili).

Guardia anti-dati-stantii (docs/IMPROVEMENT_PLAN.md, V2): in caso di errore
di rete la cache veniva restituita SENZA limite di età — con un'interruzione
prolungata di Binance REST, l'inference avrebbe calcolato feature su un
mercato di ore/giorni prima e pubblicato segnali eseguiti a prezzi live,
senza che nulla lo segnalasse. Oltre max_age_seconds la cache non viene più
servita: meglio nessuna candela (l'inference resta muta su quel simbolo)
che dati vecchi spacciati per attuali.
"""
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional

import aiohttp
import pandas as pd
from loguru import logger

from src.shared.features import timeframe_minutes

KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"


class CandleFeed:
    def __init__(self, interval: str = "1h", limit: int = 200, refresh_seconds: int = 60,
                max_age_seconds: Optional[int] = None):
        self.interval = interval
        self.limit = limit
        self.refresh_seconds = refresh_seconds
        # Default: 2 candele di margine (assorbe un'interruzione REST breve
        # senza andare mute), con un minimo di 5 minuti per timeframe corti.
        if max_age_seconds is not None:
            self.max_age_seconds = max_age_seconds
        else:
            try:
                self.max_age_seconds = max(300, timeframe_minutes(interval) * 60 * 2)
            except ValueError:
                self.max_age_seconds = 300
        self._cache: Dict[str, pd.DataFrame] = {}
        self._last_fetch: Dict[str, float] = {}
        self._last_success: Dict[str, datetime] = {}

    def last_success(self, symbol: str) -> Optional[datetime]:
        """Istante (UTC) dell'ultimo fetch riuscito per il simbolo, None se
        mai riuscito."""
        return self._last_success.get(symbol)

    def oldest_last_success(self, symbols: Iterable[str]) -> Optional[datetime]:
        """Il più vecchio tra gli ultimi fetch riusciti dei simboli dati —
        None se ANCHE UNO SOLO non è mai riuscito (caso peggiore: un
        simbolo silenziosamente scoperto non deve nascondersi dietro la
        freschezza degli altri). Usato per pubblicare un singolo segnale di
        salute aggregato che il watchdog possa controllare."""
        timestamps = []
        for symbol in symbols:
            ts = self._last_success.get(symbol)
            if ts is None:
                return None
            timestamps.append(ts)
        return min(timestamps) if timestamps else None

    def _serve_cache_or_none(self, symbol: str, cached: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        last_success = self._last_success.get(symbol)
        if cached is None or last_success is None:
            return None
        age = (datetime.now(timezone.utc) - last_success).total_seconds()
        if age > self.max_age_seconds:
            logger.error(
                f"❌ Candele per {symbol} troppo vecchie ({age:.0f}s > {self.max_age_seconds}s): "
                f"nessuna candela restituita, l'inference resterà muta su questo simbolo"
            )
            return None
        return cached

    async def get_candles(self, symbol: str) -> Optional[pd.DataFrame]:
        """Ultime `limit` candele CHIUSE per il simbolo (maiuscolo, es.
        'BTCUSDT'). Usa la cache se più recente di refresh_seconds; in caso
        di errore di rete restituisce l'ultima versione valida in cache, ma
        MAI oltre max_age_seconds dall'ultimo fetch riuscito."""
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
            return self._serve_cache_or_none(symbol, cached)

        if not isinstance(raw, list) or len(raw) < 2:
            logger.warning(f"⚠️ Risposta klines inattesa per {symbol}: {raw!r:.200}")
            return self._serve_cache_or_none(symbol, cached)

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
        self._last_success[symbol] = datetime.now(timezone.utc)
        return df
