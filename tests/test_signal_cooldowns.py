"""
Anti flip-flop dell'engine (docs/IMPROVEMENT_PLAN.md, S3):
- cooldown di ri-ingresso: dopo una chiusura, il simbolo non si riapre
  finché non passa entry_cooldown_minutes (le feature cambiano solo a
  candela chiusa: lo stesso segnale riaprirebbe la posizione in pochi secondi);
- reverse cooldown: una posizione troppo giovane non si inverte;
- isteresi sul reverse: per invertire serve confidenza extra rispetto
  alla soglia di ingresso.

Nota sui numeri: con sentiment neutro la confidenza pesata è
(1 - sentiment_weight) × confidenza = 0.7 × conf. Soglia base 0.55,
con isteresi 0.60 → conf 0.9 → 0.63 (passa tutto), conf 0.8 → 0.56
(passa la base, non l'isteresi).
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
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


def _open_long(engine, symbol="BTCUSDT", minutes_ago=0.0) -> Position:
    position = Position(
        symbol=symbol, side="long", entry_price=100.0, quantity=1.0,
        leverage=3, stop_loss=90.0, take_profit=120.0,
        entry_time=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    engine.positions[symbol] = position
    engine.latest_prices[symbol] = 100.0
    return position


def _send(engine, action="sell", confidence=0.9, symbol="BTCUSDT"):
    signal = Signal(symbol=symbol, action=action, confidence=confidence, source="ml")
    with patch("src.engine.main.notifier"), patch.object(engine, "_save_trade_to_file"):
        asyncio.run(engine._on_signal(signal))


def test_reverse_blocked_when_position_too_young():
    engine = _make_engine()
    _open_long(engine, minutes_ago=5)  # < reverse_cooldown_minutes (15)

    _send(engine, action="sell", confidence=0.9)

    assert engine.positions["BTCUSDT"].is_open
    assert engine.positions["BTCUSDT"].side == "long"


def test_reverse_blocked_by_hysteresis_margin():
    engine = _make_engine()
    _open_long(engine, minutes_ago=30)  # cooldown superato

    # 0.7 × 0.8 = 0.56: sopra la soglia base (0.55), sotto quella con
    # isteresi (0.60) → nessuna inversione
    _send(engine, action="sell", confidence=0.8)

    assert engine.positions["BTCUSDT"].is_open
    assert engine.positions["BTCUSDT"].side == "long"


def test_reverse_allowed_when_old_enough_and_confident():
    engine = _make_engine()
    _open_long(engine, minutes_ago=30)

    # 0.7 × 0.9 = 0.63 ≥ 0.60 → il long viene chiuso e si apre lo short
    _send(engine, action="sell", confidence=0.9)

    assert engine.positions["BTCUSDT"].is_open
    assert engine.positions["BTCUSDT"].side == "short"


def test_entry_blocked_during_cooldown_after_close():
    engine = _make_engine()
    engine.latest_prices["BTCUSDT"] = 100.0
    engine.last_close_time["BTCUSDT"] = datetime.now(timezone.utc) - timedelta(minutes=10)

    _send(engine, action="buy", confidence=0.9)

    assert "BTCUSDT" not in engine.positions


def test_entry_allowed_after_cooldown_expired():
    engine = _make_engine()
    engine.latest_prices["BTCUSDT"] = 100.0
    engine.last_close_time["BTCUSDT"] = datetime.now(timezone.utc) - timedelta(minutes=70)

    _send(engine, action="buy", confidence=0.9)

    assert engine.positions["BTCUSDT"].is_open


def test_close_records_last_close_time():
    engine = _make_engine()
    _open_long(engine)

    with patch("src.engine.main.notifier"), patch.object(engine, "_save_trade_to_file"):
        asyncio.run(engine._close_position("BTCUSDT", reason="TEST"))

    assert "BTCUSDT" in engine.last_close_time
    age_seconds = (datetime.now(timezone.utc) - engine.last_close_time["BTCUSDT"]).total_seconds()
    assert age_seconds < 5
