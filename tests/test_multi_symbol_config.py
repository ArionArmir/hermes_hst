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


def test_inference_apply_config_adds_missing_feature_engine_without_discarding_existing():
    inference = MLInference()
    existing_feature_engine = inference.feature_engines["btcusdt"]

    inference._apply_config(Config(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]))

    assert set(inference.feature_engines.keys()) == {"btcusdt", "ethusdt", "solusdt", "xrpusdt"}
    assert inference.feature_engines["btcusdt"] is existing_feature_engine
