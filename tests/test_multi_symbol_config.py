import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.models import Config
from src.engine.main import TradingEngine
from src.inference.main import MLInference


def test_engine_apply_config_adds_missing_symbol_models_without_discarding_existing():
    engine = TradingEngine()
    existing_exit_model = engine.exit_models["BTCUSDT"]
    existing_pattern_model = engine.pattern_models["BTCUSDT"]

    engine._apply_config(Config(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]))

    assert set(engine.exit_models.keys()) == {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}
    assert set(engine.pattern_models.keys()) == {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}
    # I modelli dei simboli invariati non vengono ricreati (stato ATR/pattern preservato)
    assert engine.exit_models["BTCUSDT"] is existing_exit_model
    assert engine.pattern_models["BTCUSDT"] is existing_pattern_model


def test_inference_apply_config_updates_symbols_and_keeps_candle_feed():
    inference = MLInference()
    feed_before = inference.candle_feed

    inference._apply_config(Config(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]))

    assert inference.symbols == ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
    # Stesso timeframe → il feed (e la sua cache di candele) viene preservato
    assert inference.candle_feed is feed_before


def test_inference_apply_config_recreates_candle_feed_on_timeframe_change():
    inference = MLInference()
    feed_before = inference.candle_feed

    inference._apply_config(Config(symbols=["BTCUSDT"], timeframe="15m"))

    assert inference.candle_feed is not feed_before
    assert inference.candle_feed.interval == "15m"
