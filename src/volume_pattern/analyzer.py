"""
Modello di conferma basato su Volumi, Pattern e Supporto/Resistenza
"""
import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional
from loguru import logger

class VolumePatternAnalyzer:
    def __init__(self, window: int = 20):
        self.window = window
        self.prices = []
        self.volumes = []
        self.highs = []
        self.lows = []
        self.timestamps = []
        self.is_ready = False

    def add_data(self, price: float, volume: float, high: float, low: float, timestamp: str = None):
        self.prices.append(price)
        self.volumes.append(volume)
        self.highs.append(high)
        self.lows.append(low)
        if timestamp:
            self.timestamps.append(timestamp)

        if len(self.prices) > self.window * 2:
            self.prices.pop(0)
            self.volumes.pop(0)
            self.highs.pop(0)
            self.lows.pop(0)
            if self.timestamps:
                self.timestamps.pop(0)

        self.is_ready = len(self.prices) >= self.window

    def analyze(self) -> Dict:
        if not self.is_ready:
            return {
                "score": 0.0,
                "signal": "NEUTRAL",
                "reason": "Dati insufficienti",
                "details": {}
            }

        prices = np.array(self.prices[-self.window:])
        volumes = np.array(self.volumes[-self.window:])
        highs = np.array(self.highs[-self.window:])
        lows = np.array(self.lows[-self.window:])

        current_vol = volumes[-1]
        avg_vol = np.mean(volumes[:-1]) if len(volumes) > 1 else current_vol
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        sma20 = np.mean(prices)
        price_sma_ratio = prices[-1] / sma20 - 1 if sma20 > 0 else 0

        pattern = self._detect_pattern(highs, lows, prices)
        pattern_score = self._pattern_score(pattern)

        support, resistance = self._find_support_resistance(prices)

        volatility = np.std(np.diff(prices) / prices[:-1]) if len(prices) > 1 else 0.01

        score = 0.0
        reasons = []

        if vol_ratio > 1.5:
            score += 0.4
            reasons.append(f"Volume alto ({vol_ratio:.2f}x)")
        elif vol_ratio < 0.5:
            score -= 0.4
            reasons.append(f"Volume basso ({vol_ratio:.2f}x)")

        if price_sma_ratio > 0.02:
            score += 0.3
            reasons.append(f"Sopra SMA20 ({price_sma_ratio:.2%})")
        elif price_sma_ratio < -0.02:
            score -= 0.3
            reasons.append(f"Sotto SMA20 ({price_sma_ratio:.2%})")

        score += pattern_score * 0.4
        if pattern_score > 0:
            reasons.append(f"Pattern rialzista: {pattern}")
        elif pattern_score < 0:
            reasons.append(f"Pattern ribassista: {pattern}")

        if support is not None and prices[-1] < support * 1.02:
            score += 0.2
            reasons.append("Vicino a supporto")
        elif resistance is not None and prices[-1] > resistance * 0.98:
            score -= 0.2
            reasons.append("Vicino a resistenza")

        score = np.clip(score, -1, 1)

        if score > 0.3:
            signal = "CONFIRM"
        elif score < -0.3:
            signal = "REJECT"
        else:
            signal = "NEUTRAL"

        return {
            "score": float(score),
            "signal": signal,
            "reason": " | ".join(reasons) if reasons else "Nessuna evidenza",
            "details": {
                "vol_ratio": float(vol_ratio),
                "price_sma_ratio": float(price_sma_ratio),
                "pattern": pattern,
                "pattern_score": pattern_score,
                "support": float(support) if support else None,
                "resistance": float(resistance) if resistance else None,
                "volatility": float(volatility)
            }
        }

    def _detect_pattern(self, highs, lows, closes) -> str:
        if len(closes) < 2:
            return "neutral"

        current_close = closes[-1]
        current_open = closes[-2] if len(closes) > 1 else current_close
        current_high = highs[-1]
        current_low = lows[-1]

        body = abs(current_close - current_open)
        range_ = current_high - current_low
        body_ratio = body / range_ if range_ > 0 else 0

        if body_ratio < 0.1:
            return "doji"

        if len(closes) >= 3:
            prev_close = closes[-2]
            prev_open = closes[-3] if len(closes) > 2 else prev_close
            if current_close > prev_close and current_open < prev_open:
                return "bullish_engulfing"
            if current_close < prev_close and current_open > prev_open:
                return "bearish_engulfing"

        lower_shadow = min(current_close, current_open) - current_low
        if lower_shadow > body * 2 and body_ratio < 0.3:
            return "hammer"

        upper_shadow = current_high - max(current_close, current_open)
        if upper_shadow > body * 2 and body_ratio < 0.3:
            return "shooting_star"

        return "neutral"

    def _pattern_score(self, pattern: str) -> float:
        scores = {
            "bullish_engulfing": 1.0,
            "hammer": 0.8,
            "doji": 0.2,
            "neutral": 0.0,
            "shooting_star": -0.8,
            "bearish_engulfing": -1.0
        }
        return scores.get(pattern, 0.0)

    def _find_support_resistance(self, prices) -> Tuple[Optional[float], Optional[float]]:
        if len(prices) < 10:
            return None, None

        support = np.min(prices[-10:])
        resistance = np.max(prices[-10:])

        return support, resistance
