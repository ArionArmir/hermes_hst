import numpy as np
from typing import Tuple
from loguru import logger

class ATRExitModel:
    def __init__(self, atr_multiplier_sl: float = 2.0, atr_multiplier_tp: float = 3.0):
        self.atr_multiplier_sl = atr_multiplier_sl
        self.atr_multiplier_tp = atr_multiplier_tp
        self.window = 14
        self.prices = []
        self.highs = []
        self.lows = []

    def add_price(self, price: float, high: float, low: float):
        self.prices.append(price)
        self.highs.append(high)
        self.lows.append(low)
        if len(self.prices) > self.window * 2:
            self.prices.pop(0)
            self.highs.pop(0)
            self.lows.pop(0)

    def _calculate_atr(self) -> float:
        if len(self.prices) < self.window + 1:
            return self.prices[-1] * 0.008 if self.prices else 0.0

        true_ranges = []
        for i in range(1, len(self.prices)):
            high = self.highs[i]
            low = self.lows[i]
            prev_close = self.prices[i-1]
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            true_range = max(tr1, tr2, tr3)
            true_ranges.append(true_range)

        if not true_ranges:
            return self.prices[-1] * 0.008

        atr = np.mean(true_ranges[-self.window:])
        current_price = self.prices[-1]
        if atr > current_price * 0.10:
            atr = current_price * 0.008
        return atr

    def calculate_exit_levels(self, price: float, side: str) -> Tuple[float, float]:
        atr = self._calculate_atr()
        logger.debug(f"🔍 [ATR] ATR calcolato: {atr:.4f} per prezzo {price:.2f}")

        if atr <= 0:
            if side == 'long':
                sl = price * 0.985
                tp = price * 1.03
            else:
                sl = price * 1.015
                tp = price * 0.97
            logger.debug(f"🔍 [ATR] Fallback: SL={sl:.2f}, TP={tp:.2f}")
            return sl, tp

        if side == 'long':
            stop_loss = price - atr * self.atr_multiplier_sl
            take_profit = price + atr * self.atr_multiplier_tp
        else:
            stop_loss = price + atr * self.atr_multiplier_sl
            take_profit = price - atr * self.atr_multiplier_tp

        # Limiti di sicurezza
        min_sl_pct = 0.01
        max_sl_pct = 0.05
        min_tp_pct = 0.015
        max_tp_pct = 0.08

        sl_pct = abs(stop_loss - price) / price
        tp_pct = abs(take_profit - price) / price

        if sl_pct < min_sl_pct:
            stop_loss = price * (1 - min_sl_pct) if side == 'long' else price * (1 + min_sl_pct)
        elif sl_pct > max_sl_pct:
            stop_loss = price * (1 - max_sl_pct) if side == 'long' else price * (1 + max_sl_pct)

        if tp_pct < min_tp_pct:
            take_profit = price * (1 + min_tp_pct) if side == 'long' else price * (1 - min_tp_pct)
        elif tp_pct > max_tp_pct:
            take_profit = price * (1 + max_tp_pct) if side == 'long' else price * (1 - max_tp_pct)

        return stop_loss, take_profit

    def update_trailing_stop(self, price: float, position) -> float:
        if not position.is_open:
            return position.stop_loss

        atr = self._calculate_atr()
        if atr <= 0:
            return position.stop_loss

        if position.side == 'long':
            new_sl = price - atr * self.atr_multiplier_sl
            if new_sl > position.stop_loss:
                return new_sl
        else:
            new_sl = price + atr * self.atr_multiplier_sl
            if new_sl < position.stop_loss:
                return new_sl

        return position.stop_loss
