"""
Coerenza tra l'orizzonte del target del modello e i parametri di trading:
il max holding dell'engine deve coprire le TARGET_HORIZON_BARS candele su cui
il modello è addestrato a predire (docs/IMPROVEMENT_PLAN.md, S1).
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.models import Config
from src.shared.features import timeframe_minutes
from src.training.feature_engine import TARGET_HORIZON_BARS

CONFIG_PATH = Path(__file__).parent.parent / "config" / "trading_params.yaml"


def test_timeframe_minutes_parses_known_formats():
    assert timeframe_minutes("1m") == 1
    assert timeframe_minutes("15m") == 15
    assert timeframe_minutes("1h") == 60
    assert timeframe_minutes("4h") == 240
    assert timeframe_minutes("1d") == 1440


def test_timeframe_minutes_rejects_unknown_formats():
    for bad in ("", "h", "60", "1w", "abc"):
        with pytest.raises(ValueError):
            timeframe_minutes(bad)


def test_default_config_holding_covers_model_horizon():
    config = Config()
    horizon = TARGET_HORIZON_BARS * timeframe_minutes(config.timeframe)
    assert config.max_holding_minutes >= horizon


def test_yaml_config_holding_covers_model_horizon():
    with open(CONFIG_PATH) as f:
        config = Config(**yaml.safe_load(f))
    horizon = TARGET_HORIZON_BARS * timeframe_minutes(config.timeframe)
    assert config.max_holding_minutes >= horizon
