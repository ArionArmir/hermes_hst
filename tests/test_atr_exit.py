import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.exit_model.atr_exit import ATRExitModel


def test_long_fallback_brackets_entry_price():
    model = ATRExitModel()
    price = 60000.0
    stop_loss, take_profit = model.calculate_exit_levels(price, "long")
    assert stop_loss < price < take_profit


def test_short_fallback_brackets_entry_price():
    model = ATRExitModel()
    price = 60000.0
    stop_loss, take_profit = model.calculate_exit_levels(price, "short")
    assert take_profit < price < stop_loss


def test_long_atr_based_brackets_entry_price():
    model = ATRExitModel()
    price = 60000.0
    for i in range(20):
        p = price + i * 5
        model.add_price(p, p * 1.002, p * 0.998)
    last_price = model.prices[-1]
    stop_loss, take_profit = model.calculate_exit_levels(last_price, "long")
    assert stop_loss < last_price < take_profit


def test_short_atr_based_brackets_entry_price():
    model = ATRExitModel()
    price = 60000.0
    for i in range(20):
        p = price - i * 5
        model.add_price(p, p * 1.002, p * 0.998)
    last_price = model.prices[-1]
    stop_loss, take_profit = model.calculate_exit_levels(last_price, "short")
    assert take_profit < last_price < stop_loss
