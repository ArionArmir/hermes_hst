"""
Integrazione del circuit breaker nell'engine (docs/IMPROVEMENT_PLAN.md, N1):
il gate blocca SOLO le nuove aperture/reverse, mai le chiusure; ogni
chiusura alimenta il breaker; il seeding dallo storico sopravvive a un
riavvio; il reset manuale arriva via engine_commands (stesso canale di
"Reset posizioni" già in dashboard).
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.main import TradingEngine
from src.core.models import Position, Signal
from src.shared import store


class FakeRedis:
    def __init__(self):
        self._store = {}

    async def set(self, key, value):
        self._store[key] = value

    async def get(self, key):
        return self._store.get(key)


def _make_engine() -> TradingEngine:
    engine = TradingEngine()
    engine.redis = FakeRedis()
    engine.pattern_confirmation_enabled = False
    engine.dynamic_exit_enabled = False
    return engine


def _send(engine, action="buy", confidence=0.9, symbol="BTCUSDT"):
    signal = Signal(symbol=symbol, action=action, confidence=confidence, source="ml")
    with patch("src.engine.main.notifier"), patch.object(engine, "_save_trade_to_file"):
        asyncio.run(engine._on_signal(signal))


def test_tripped_breaker_blocks_new_buy():
    engine = _make_engine()
    engine.latest_prices["BTCUSDT"] = 100.0
    for i in range(engine.circuit_breaker.params.max_consecutive_losses):
        engine.circuit_breaker.record_trade(-1.0, 999.0 - i)

    _send(engine, action="buy", confidence=0.95)

    assert "BTCUSDT" not in engine.positions


def test_tripped_breaker_does_not_block_close():
    engine = _make_engine()
    engine.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="long", entry_price=100.0, quantity=1.0,
        leverage=3, stop_loss=90.0, take_profit=120.0,
    )
    engine.latest_prices["BTCUSDT"] = 105.0
    for i in range(engine.circuit_breaker.params.max_consecutive_losses):
        engine.circuit_breaker.record_trade(-1.0, 999.0 - i)

    signal = Signal(symbol="BTCUSDT", action="close", confidence=0.9, source="ml")
    with patch("src.engine.main.notifier"), patch.object(engine, "_save_trade_to_file"):
        asyncio.run(engine._on_signal(signal))

    assert not engine.positions["BTCUSDT"].is_open


def test_disabled_breaker_does_not_gate_even_when_tripped():
    engine = _make_engine()
    engine.circuit_breaker_enabled = False
    engine.latest_prices["BTCUSDT"] = 100.0
    for i in range(engine.circuit_breaker.params.max_consecutive_losses):
        engine.circuit_breaker.record_trade(-1.0, 999.0 - i)

    _send(engine, action="buy", confidence=0.95)

    assert engine.positions["BTCUSDT"].is_open


def test_blocked_signal_is_recorded_with_circuit_breaker_outcome(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "cb_test.db")
    engine = _make_engine()
    engine.latest_prices["BTCUSDT"] = 100.0
    for i in range(engine.circuit_breaker.params.max_consecutive_losses):
        engine.circuit_breaker.record_trade(-1.0, 999.0 - i)

    _send(engine, action="buy", confidence=0.95)

    signals = store.read_signals()
    assert list(signals["outcome"]) == ["CIRCUIT_BREAKER"]


def test_close_position_feeds_the_breaker():
    engine = _make_engine()
    engine.circuit_breaker.params.max_consecutive_losses = 2
    engine.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="long", entry_price=100.0, quantity=1.0,
        leverage=3, stop_loss=90.0, take_profit=120.0,
    )
    engine.latest_prices["BTCUSDT"] = 90.0  # in perdita

    with patch("src.engine.main.notifier"), patch.object(engine, "_save_trade_to_file"):
        asyncio.run(engine._close_position("BTCUSDT", reason="STOP_LOSS"))

    assert engine.circuit_breaker._consecutive_losses == 1


def test_reset_circuit_breaker_command_clears_persistent_trip():
    engine = _make_engine()
    engine.circuit_breaker.manual_reset()
    # Simula un drawdown che scatta un trip persistente
    engine.circuit_breaker.record_trade(+1000.0, 2000.0)
    engine.circuit_breaker.record_trade(-800.0, 1200.0)
    assert engine.circuit_breaker.is_tripped()

    asyncio.run(engine._handle_channel_message(
        "engine_commands", '{"action": "reset_circuit_breaker"}'))

    assert not engine.circuit_breaker.is_tripped()


def test_apply_config_updates_breaker_params_without_resetting_state():
    from src.core.models import Config
    engine = _make_engine()
    engine.circuit_breaker.record_trade(-1.0, 999.0)
    engine.circuit_breaker.record_trade(-1.0, 998.0)

    engine._apply_config(Config(circuit_breaker_max_consecutive_losses=10))

    assert engine.circuit_breaker.params.max_consecutive_losses == 10
    assert engine.circuit_breaker._consecutive_losses == 2  # stato preservato


def test_seed_circuit_breaker_from_store_on_init(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "seed_test.db")
    ts = datetime.now(timezone.utc)
    for i, pnl in enumerate([-1.0, -1.0, -1.0]):
        store.insert_trade(symbol="BTCUSDT", side="long", entry=100.0, exit_price=99.0,
                           pnl=pnl, reason="STOP_LOSS", capital_after=1000.0 - i,
                           timestamp=(ts - timedelta(minutes=3 - i)).isoformat())

    engine = _make_engine()
    engine.circuit_breaker.params.max_consecutive_losses = 3
    asyncio.run(engine._seed_circuit_breaker())

    assert engine.circuit_breaker.is_tripped()
