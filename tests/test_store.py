"""
Persistenza SQLite (src/shared/store.py) e strumentazione delle decisioni
dell'engine sulla tabella signals. DB temporaneo per ogni test via
monkeypatch di store.DB_PATH; il percorso di trading non deve MAI rompersi
per un errore di persistenza.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.shared import store
from src.engine.main import TradingEngine
from src.core.models import Position, Signal


class FakeRedis:
    async def set(self, key, value):
        pass


def _make_engine(monkeypatch, tmp_path) -> TradingEngine:
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    engine = TradingEngine()
    engine.redis = FakeRedis()
    engine.pattern_confirmation_enabled = False
    engine.dynamic_exit_enabled = False
    engine.latest_prices["BTCUSDT"] = 100.0
    return engine


def _send(engine, action="buy", confidence=0.9):
    signal = Signal(symbol="BTCUSDT", action=action, confidence=confidence, source="ml")
    with patch("src.engine.main.notifier"):
        asyncio.run(engine._on_signal(signal))


def test_trade_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.insert_trade(symbol="BTCUSDT", side="long", entry=100.0, exit_price=110.0,
                       pnl=9.9, reason="TEST", pnl_gross=10.0, fees=0.1, capital_after=1009.9)

    trades = store.read_trades()
    assert len(trades) == 1
    row = trades.iloc[0]
    assert row["symbol"] == "BTCUSDT" and row["pnl"] == 9.9 and row["capital_after"] == 1009.9


def test_sentiment_rows_per_asset(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.insert_sentiment({"BTC": 0.2, "ETH": -0.1, "aggregate": 0.05, "spazzatura": "x"})

    rows = store.read_sentiment()
    assert set(rows["asset"]) == {"BTC", "ETH", "aggregate"}  # non-numerici scartati


def test_signal_outcomes_are_recorded(monkeypatch, tmp_path):
    engine = _make_engine(monkeypatch, tmp_path)

    # 1. confidenza bassa: 0.5 < 0.55
    _send(engine, confidence=0.5)
    # 2. veto sentiment
    engine.sentiment_by_asset["BTCUSDT"] = -0.9
    _send(engine, confidence=0.95)
    engine.sentiment_by_asset["BTCUSDT"] = 0.0
    # 3. apertura riuscita
    _send(engine, confidence=0.9)
    # 4. stessa direzione a posizione aperta
    _send(engine, confidence=0.9)

    signals = store.read_signals().sort_values("id")
    assert list(signals["outcome"]) == ["LOW_CONFIDENCE", "SENTIMENT_VETO", "OPENED", "ALREADY_OPEN"]
    opened = signals[signals["outcome"] == "OPENED"].iloc[0]
    # bonus-only con sentiment neutro: pesata = confidenza del modello (0.9)
    assert opened["weighted_confidence"] is not None and abs(opened["weighted_confidence"] - 0.9) < 1e-9


def test_reverse_is_recorded_as_reversed(monkeypatch, tmp_path):
    engine = _make_engine(monkeypatch, tmp_path)
    engine.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="long", entry_price=100.0, quantity=1.0,
        leverage=3, stop_loss=90.0, take_profit=120.0,
        entry_time=datetime.now(timezone.utc) - timedelta(minutes=30),
    )

    with patch.object(engine, "_save_trade_to_file"):
        _send(engine, action="sell", confidence=0.9)

    signals = store.read_signals()
    assert list(signals["outcome"]) == ["REVERSED"]
    assert engine.positions["BTCUSDT"].side == "short"


def test_store_failure_never_blocks_trading(monkeypatch, tmp_path):
    engine = _make_engine(monkeypatch, tmp_path)

    with patch.object(store, "insert_signal", side_effect=OSError("disk full")):
        _send(engine, confidence=0.9)

    # La posizione si apre comunque, l'errore è solo loggato
    assert engine.positions["BTCUSDT"].is_open


def test_trade_close_writes_sqlite_and_appends_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.chdir(tmp_path)
    engine = TradingEngine()
    engine.redis = FakeRedis()
    engine.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", side="long", entry_price=100.0, quantity=1.0,
        leverage=3, stop_loss=90.0, take_profit=120.0,
    )
    engine.latest_prices["BTCUSDT"] = 110.0

    with patch("src.engine.main.notifier"):
        asyncio.run(engine._close_position("BTCUSDT", reason="TEST"))

    trades = store.read_trades()
    assert len(trades) == 1 and trades.iloc[0]["reason"] == "TEST"

    csv_path = tmp_path / "data" / "trades_history.csv"
    lines = csv_path.read_text().strip().splitlines()
    assert lines[0].startswith("timestamp,symbol,side")
    assert len(lines) == 2 and ",TEST," in lines[1]


def test_wal_allows_concurrent_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    store.insert_trade(symbol="A", side="long", entry=1, exit_price=2, pnl=1, reason="X")
    # Lettore aperto mentre si scrive di nuovo (WAL: nessun lock error)
    first = store.read_trades()
    store.insert_trade(symbol="B", side="short", entry=2, exit_price=1, pnl=1, reason="Y")
    second = store.read_trades()
    assert len(first) == 1 and len(second) == 2
