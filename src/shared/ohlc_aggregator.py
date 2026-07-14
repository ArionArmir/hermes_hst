"""
Aggregazione di tick live in candele OHLC a 1 minuto, persistite su CSV per simbolo.
Nessuna dipendenza da Redis: la dashboard le legge direttamente da disco.
"""
import os
from datetime import datetime, timezone
from typing import Dict, Optional

from loguru import logger


class _Bar:
    __slots__ = ("bar_time", "open", "high", "low", "close", "volume")

    def __init__(self, bar_time: datetime, price: float, volume: float):
        self.bar_time = bar_time
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = volume

    def update(self, price: float, volume: float):
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume


class OHLCAggregator:
    def __init__(self, output_dir: str = "data/live_ohlc", bucket_seconds: int = 60):
        self.output_dir = output_dir
        self.bucket_seconds = bucket_seconds
        self._bars: Dict[str, _Bar] = {}
        os.makedirs(self.output_dir, exist_ok=True)

    def _bucket_start(self, ts: datetime) -> datetime:
        epoch_seconds = int(ts.timestamp())
        bucket_epoch = epoch_seconds - (epoch_seconds % self.bucket_seconds)
        return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)

    def add_tick(self, symbol: str, price: float, volume: float, timestamp: Optional[datetime] = None):
        ts = timestamp or datetime.now(timezone.utc)
        bucket_time = self._bucket_start(ts)

        current = self._bars.get(symbol)
        if current is None:
            self._bars[symbol] = _Bar(bucket_time, price, volume)
            return

        if bucket_time == current.bar_time:
            current.update(price, volume)
            return

        self._flush(symbol, current)
        self._bars[symbol] = _Bar(bucket_time, price, volume)

    def _flush(self, symbol: str, bar: "_Bar"):
        path = os.path.join(self.output_dir, f"{symbol}.csv")
        is_new = not os.path.exists(path)
        try:
            with open(path, "a") as f:
                if is_new:
                    f.write("bar_time,open,high,low,close,volume\n")
                f.write(f"{bar.bar_time.isoformat()},{bar.open},{bar.high},{bar.low},{bar.close},{bar.volume}\n")
        except Exception as e:
            logger.error(f"❌ Errore scrittura candela OHLC per {symbol}: {e}")
