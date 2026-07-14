import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.main import TradingEngine
from src.core.models import Signal


class FakeRedis:
    async def set(self, key, value):
        pass


def _make_engine() -> TradingEngine:
    engine = TradingEngine()
    engine.redis = FakeRedis()
    engine.pattern_confirmation_enabled = False
    return engine


def test_buy_signal_opens_long_with_sl_below_and_tp_above_entry():
    engine = _make_engine()
    symbol = "BTCUSDT"
    engine.latest_prices[symbol] = 60000.0
    signal = Signal(symbol=symbol, action="buy", confidence=0.9, source="ml")

    with patch("src.engine.main.notifier"):
        asyncio.run(engine._open_position(signal))

    position = engine.positions[symbol]
    assert position.side == "long"
    assert position.stop_loss < position.entry_price < position.take_profit


def test_sell_signal_opens_short_with_tp_below_and_sl_above_entry():
    engine = _make_engine()
    symbol = "ETHUSDT"
    engine.latest_prices[symbol] = 1800.0
    signal = Signal(symbol=symbol, action="sell", confidence=0.9, source="ml")

    with patch("src.engine.main.notifier"):
        asyncio.run(engine._open_position(signal))

    position = engine.positions[symbol]
    assert position.side == "short"
    assert position.take_profit < position.entry_price < position.stop_loss
