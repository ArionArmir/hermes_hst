"""
Rischio a livello portafoglio (docs/IMPROVEMENT_PLAN.md, S5/A5):
- cap sul margine complessivo: la somma dei margini delle posizioni aperte
  non può superare capital × max_exposure;
- capitale paper aggiornato a ogni chiusura con PnL al netto delle fee taker.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.main import TradingEngine
from src.core.models import Position, Signal


class FakeRedis:
    async def set(self, key, value):
        pass


def _make_engine() -> TradingEngine:
    engine = TradingEngine()
    engine.redis = FakeRedis()
    engine.pattern_confirmation_enabled = False
    engine.dynamic_exit_enabled = False
    return engine


def test_portfolio_margin_cap_blocks_new_positions():
    engine = _make_engine()
    engine.capital = 1000.0
    engine.max_exposure = 0.1        # cap margine: 100 USDT
    engine.max_position_usdt = 20.0
    engine.leverage = 1              # nozionale 20 → margine richiesto 20
    engine.latest_prices["ETHUSDT"] = 100.0
    # Posizione esistente che impegna 90 di margine (0.9 × 100 a leva 1)
    engine.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="long", entry_price=100.0, quantity=0.9,
        leverage=1, stop_loss=90.0, take_profit=120.0,
    )
    engine.latest_prices["BTCUSDT"] = 100.0
    signal = Signal(symbol="ETHUSDT", action="buy", confidence=0.9, source="ml")

    with patch("src.engine.main.notifier"):
        asyncio.run(engine._open_position(signal))

    # 90 in uso + 20 richiesti > 100 → rifiutata
    assert "ETHUSDT" not in engine.positions

    # Sotto il cap la stessa apertura passa: margine in uso ridotto a 50
    engine.positions["BTCUSDT"].quantity = 0.5
    with patch("src.engine.main.notifier"):
        asyncio.run(engine._open_position(signal))

    assert engine.positions["ETHUSDT"].is_open  # 50 + 20 ≤ 100


def test_margin_in_use_counts_only_open_positions():
    engine = _make_engine()
    engine.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="long", entry_price=100.0, quantity=3.0,
        leverage=3, stop_loss=90.0, take_profit=120.0,
    )
    engine.positions["ETHUSDT"] = Position(
        symbol="ETHUSDT", side="long", entry_price=100.0, quantity=3.0,
        leverage=3, stop_loss=90.0, take_profit=120.0, is_open=False,
    )
    # 3 × 100 di nozionale a leva 3 → 100 di margine, solo per la posizione aperta
    assert engine._margin_in_use() == 100.0


def test_close_long_updates_capital_with_pnl_net_of_fees():
    engine = _make_engine()
    engine.capital = 1000.0
    engine.taker_fee_pct = 0.0005
    engine.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="long", entry_price=100.0, quantity=1.0,
        leverage=3, stop_loss=90.0, take_profit=120.0,
    )
    engine.latest_prices["BTCUSDT"] = 110.0

    with patch("src.engine.main.notifier"), patch.object(engine, "_save_trade_to_file"):
        asyncio.run(engine._close_position("BTCUSDT", reason="TEST"))

    # PnL lordo +10, fee (100 + 110) × 0.0005 = 0.105
    assert engine.capital == 1000.0 + 10.0 - 0.105
    assert not engine.positions["BTCUSDT"].is_open


def test_close_short_updates_capital_with_pnl_net_of_fees():
    engine = _make_engine()
    engine.capital = 1000.0
    engine.taker_fee_pct = 0.0005
    engine.positions["ETHUSDT"] = Position(
        symbol="ETHUSDT", side="short", entry_price=100.0, quantity=1.0,
        leverage=3, stop_loss=110.0, take_profit=80.0,
    )
    engine.latest_prices["ETHUSDT"] = 90.0

    with patch("src.engine.main.notifier"), patch.object(engine, "_save_trade_to_file"):
        asyncio.run(engine._close_position("ETHUSDT", reason="TEST"))

    # PnL lordo +10, fee (100 + 90) × 0.0005 = 0.095
    assert engine.capital == 1000.0 + 10.0 - 0.095


def test_losing_trade_reduces_capital():
    engine = _make_engine()
    engine.capital = 1000.0
    engine.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="long", entry_price=100.0, quantity=1.0,
        leverage=3, stop_loss=90.0, take_profit=120.0,
    )
    engine.latest_prices["BTCUSDT"] = 95.0

    with patch("src.engine.main.notifier"), patch.object(engine, "_save_trade_to_file"):
        asyncio.run(engine._close_position("BTCUSDT", reason="TEST"))

    assert engine.capital < 995.0  # -5 di PnL lordo, più le fee
