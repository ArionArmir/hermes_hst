"""
Vincoli di range su Config (docs/IMPROVEMENT_PLAN.md, V6/N5): un valore fuori
scala deve far fallire Config(**dati) con ValidationError, ovunque venga
costruito (dashboard, YAML, Redis) — non solo "sembrare strano" in un log.
"""
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.models import Config

CONFIG_PATH = Path(__file__).parent.parent / "config" / "trading_params.yaml"


def test_default_config_is_valid():
    Config()  # non deve sollevare


def test_production_yaml_is_valid():
    with open(CONFIG_PATH) as f:
        Config(**yaml.safe_load(f))  # non deve sollevare


@pytest.mark.parametrize("field, bad_value", [
    ("leverage", 0),
    ("leverage", 100),
    ("stop_loss_pct", 0.0),
    ("stop_loss_pct", -0.01),
    ("stop_loss_pct", 0.9),
    ("take_profit_pct", 1.5),
    ("max_position_size_usdt", 0.0),
    ("max_position_size_usdt", -50.0),
    ("max_exposure", 0.0),
    ("max_exposure", 5.0),
    ("max_positions_same_direction", 0),
    ("taker_fee_pct", -0.001),
    ("taker_fee_pct", 0.1),
    ("ml_confidence_threshold", 0.0),
    ("ml_confidence_threshold", 1.0),
    ("sentiment_weight", 1.5),
    ("max_holding_minutes", 0),
    ("circuit_breaker_max_consecutive_losses", 0),
    ("circuit_breaker_max_daily_loss_pct", 0.0),
    ("circuit_breaker_max_drawdown_pct", 1.5),
])
def test_out_of_range_values_are_rejected(field, bad_value):
    with pytest.raises(ValidationError):
        Config(**{field: bad_value})


def test_empty_symbols_list_is_rejected():
    with pytest.raises(ValidationError, match="non può essere vuota"):
        Config(symbols=[])


def test_non_usdt_symbol_is_rejected():
    with pytest.raises(ValidationError, match="simbolo non supportato"):
        Config(symbols=["BTCUSDT", "ETHBTC"])


def test_non_usdt_symbol_check_is_case_insensitive():
    Config(symbols=["btcusdt"])  # non deve sollevare


@pytest.mark.parametrize("bad_timeframe", ["1w", "", "abc", "h1", "1x"])
def test_invalid_timeframe_is_rejected(bad_timeframe):
    with pytest.raises(ValidationError, match="timeframe non valido"):
        Config(timeframe=bad_timeframe)


@pytest.mark.parametrize("good_timeframe", ["1m", "15m", "1h", "4h", "1d", "1H", "60m"])
def test_valid_timeframes_are_accepted(good_timeframe):
    # timeframe_minutes è case-insensitive e non impone i soli intervalli
    # Binance ("60m" == "1h" numericamente): il validator riusa la stessa
    # funzione, quindi eredita la stessa flessibilità già in uso altrove.
    Config(timeframe=good_timeframe)  # non deve sollevare


def test_boundary_values_are_accepted():
    # I limiti stessi (ge/le) devono passare, solo l'oltre-limite fallisce
    Config(leverage=1)
    Config(leverage=20)
    Config(max_exposure=1.0)
    Config(ml_confidence_threshold=0.999)
