"""
Alimentazione dei modelli a candele dell'engine (ATRExitModel e
VolumePatternAnalyzer): solo candele reali chiuse — dallo stream kline o dal
bootstrap REST — mai tick con high/low sintetici (docs/IMPROVEMENT_PLAN.md, S6).
"""
import asyncio
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.main import TradingEngine


def _kline(symbol="BTCUSDT", closed=True, close=60000.0, high=60400.0, low=59700.0, volume=123.0):
    return {"s": symbol, "x": closed, "c": str(close), "h": str(high), "l": str(low), "v": str(volume)}


def test_closed_kline_feeds_exit_and_pattern_models():
    engine = TradingEngine()
    engine._on_kline(_kline())

    exit_model = engine.exit_models["BTCUSDT"]
    pattern_model = engine.pattern_models["BTCUSDT"]
    assert exit_model.prices == [60000.0]
    assert exit_model.highs == [60400.0]   # high reale, non price * 1.002
    assert exit_model.lows == [59700.0]    # low reale, non price * 0.998
    assert pattern_model.volumes == [123.0]


def test_forming_kline_is_ignored():
    engine = TradingEngine()
    engine._on_kline(_kline(closed=False))

    assert engine.exit_models["BTCUSDT"].prices == []


def test_malformed_kline_does_not_raise():
    engine = TradingEngine()
    engine._on_kline({"s": "BTCUSDT", "x": True, "c": "not-a-number"})
    engine._on_kline({})

    assert engine.exit_models["BTCUSDT"].prices == []


def test_atr_reflects_real_ranges_after_warmup():
    engine = TradingEngine()
    # 20 candele con range reale dell'1% del prezzo
    for i in range(20):
        price = 60000.0 + i * 10
        engine._on_kline(_kline(close=price, high=price * 1.005, low=price * 0.995))

    atr = engine.exit_models["BTCUSDT"]._calculate_atr()
    # ATR ≈ range medio (1% del prezzo), non il fallback 0.8% né lo 0.4%
    # costante dei vecchi high/low sintetici
    assert 0.009 < atr / 60000.0 < 0.011


class _StubFeed:
    """Feed finto: registra i simboli richiesti e restituisce 30 candele."""
    def __init__(self):
        self.calls = []
        self.interval = "1h"

    async def get_candles(self, symbol):
        self.calls.append(symbol)
        n = 30
        return pd.DataFrame({
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": [10.0] * n,
        })


def test_bootstrap_warms_models_and_skips_already_warm_symbols():
    engine = TradingEngine()
    engine.candle_feed = _StubFeed()

    asyncio.run(engine._bootstrap_candle_models())
    # Tutti i simboli di default richiesti e scaldati
    assert engine.candle_feed.calls == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert engine._candle_models_warm("BTCUSDT")

    # Secondo giro: tutti già caldi → nessuna nuova chiamata REST
    engine.candle_feed.calls.clear()
    asyncio.run(engine._bootstrap_candle_models())
    assert engine.candle_feed.calls == []
