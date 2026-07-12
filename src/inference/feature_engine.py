import numpy as np
from typing import List, Optional

class FeatureEngine:
    def __init__(self, window: int = 100):
        self.window = window
        self.prices: List[float] = []
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.volumes: List[float] = []
        self.timestamps: List[str] = []

    def add_tick(self, price: float, volume: float = 0, high: float = None, low: float = None, timestamp: str = None):
        self.prices.append(price)
        self.highs.append(high if high is not None else price)
        self.lows.append(low if low is not None else price)
        self.volumes.append(volume)
        if timestamp:
            self.timestamps.append(timestamp)
        if len(self.prices) > self.window:
            self.prices.pop(0)
            self.highs.pop(0)
            self.lows.pop(0)
            self.volumes.pop(0)
            if self.timestamps:
                self.timestamps.pop(0)

    def is_ready(self) -> bool:
        return len(self.prices) >= 50

    def _ema_array(self, arr: np.ndarray, span: int) -> np.ndarray:
        """Calcola EMA su un array, restituisce l'array completo"""
        alpha = 2 / (span + 1)
        ema = np.zeros_like(arr, dtype=np.float64)
        ema[0] = arr[0]
        for i in range(1, len(arr)):
            ema[i] = alpha * arr[i] + (1 - alpha) * ema[i-1]
        return ema

    def calculate_features(self) -> Optional[np.ndarray]:
        if not self.is_ready():
            return None

        prices = np.array(self.prices, dtype=np.float64)
        highs = np.array(self.highs, dtype=np.float64)
        lows = np.array(self.lows, dtype=np.float64)
        volumes = np.array(self.volumes, dtype=np.float64)

        # Rendimenti
        returns = np.diff(prices) / (prices[:-1] + 1e-9)

        # RSI
        rsi = self._rsi(prices, period=14)
        # SMA
        sma_20 = np.mean(prices[-20:]) if len(prices) >= 20 else prices[-1]
        sma_50 = np.mean(prices[-50:]) if len(prices) >= 50 else prices[-1]
        # Vol
        vol = np.std(returns[-20:]) * np.sqrt(252) if len(returns) >= 20 else 0.01
        # Momentum
        mom = (prices[-1] - prices[-10]) / (prices[-10] + 1e-9) if len(prices) >= 10 else 0.0
        # Volume ratio
        vol_ratio = volumes[-1] / (np.mean(volumes[-20:]) + 1e-6) if len(volumes) >= 20 else 1.0
        # Price SMA20 ratio
        price_sma20 = prices[-1] / (sma_20 + 1e-9) - 1
        # ATR
        high_low = highs - lows
        high_close = np.abs(highs - np.roll(prices, 1))
        low_close = np.abs(lows - np.roll(prices, 1))
        ranges = np.maximum(np.maximum(high_low, high_close), low_close)
        atr = np.mean(ranges[-14:]) if len(ranges) >= 14 else prices[-1] * 0.01
        atr_pct = atr / (prices[-1] + 1e-9)
        # Bollinger
        if len(prices) >= 20:
            bb_middle = np.mean(prices[-20:])
            bb_std = np.std(prices[-20:])
            bb_lower = bb_middle - 2 * bb_std
            bb_upper = bb_middle + 2 * bb_std
            bb_position = (prices[-1] - bb_lower) / (bb_upper - bb_lower + 1e-6)
        else:
            bb_position = 0.5
        # MACD (calcolato su array)
        if len(prices) >= 26:
            ema12 = self._ema_array(prices, 12)
            ema26 = self._ema_array(prices, 26)
            macd_array = ema12 - ema26
            macd_signal_array = self._ema_array(macd_array, 9)
            macd = macd_array[-1]
            macd_signal = macd_signal_array[-1]
            macd_hist_norm = (macd - macd_signal) / (prices[-1] + 1e-9)
        else:
            macd_hist_norm = 0.0
        # OBV
        if len(volumes) > 1:
            signs = np.sign(np.diff(prices))
            obv = np.sum(signs * volumes[1:])
            obv_norm = obv / (np.mean(volumes[-20:]) + 1e-6) - 1
        else:
            obv_norm = 0.0
        # Fibonacci
        if len(prices) >= 20:
            high_20 = np.max(prices[-20:])
            low_20 = np.min(prices[-20:])
            diff_20 = high_20 - low_20
            fib_position = (prices[-1] - low_20) / (diff_20 + 1e-9)
            fib_618_distance = (prices[-1] - (low_20 + 0.618 * diff_20)) / (prices[-1] + 1e-9)
        else:
            fib_position = 0.5
            fib_618_distance = 0.0

        # Array delle 14 feature
        features = np.array([
            rsi,
            sma_20 / (prices[-1] + 1e-9) - 1,
            sma_50 / (prices[-1] + 1e-9) - 1,
            vol,
            mom,
            vol_ratio,
            price_sma20,
            returns[-1] if len(returns) > 0 else 0.0,
            atr_pct,
            bb_position,
            macd_hist_norm,
            obv_norm,
            fib_position,
            fib_618_distance
        ], dtype=np.float64)

        features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)

        return features.reshape(1, -1)

    def _rsi(self, prices: np.ndarray, period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        deltas = np.diff(prices)
        gains = np.maximum(deltas, 0)
        losses = np.maximum(-deltas, 0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return np.clip(rsi, 0, 100)
