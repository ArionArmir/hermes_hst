"""
Sentiment direzionale nell'engine (docs/IMPROVEMENT_PLAN.md, S4):
- veto simmetrico: sentiment fortemente contrario blocca sia buy sia sell;
- il sentiment aumenta la confidenza solo se FAVOREVOLE alla direzione
  (il vecchio abs() premiava anche un sentiment opposto al trade);
- mappa asset→simbolo dinamica, non più hardcoded BTC/ETH/SOL.

Numeri di riferimento (sentiment_weight=0.3, soglia=0.55):
confidenza pesata = min(1, conf + 0.3 × max(0, sentiment direzionale)) —
bonus-only: il sentiment neutro NON penalizza (la vecchia media pesata
0.7×conf creava un secondo gate nascosto sopra la soglia della policy).
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.main import TradingEngine
from src.core.models import Signal
from src.sentiment.ollama_client import OllamaSentiment


class FakeRedis:
    async def set(self, key, value):
        pass


def _make_engine() -> TradingEngine:
    engine = TradingEngine()
    engine.redis = FakeRedis()
    engine.pattern_confirmation_enabled = False
    engine.dynamic_exit_enabled = False
    engine.latest_prices["BTCUSDT"] = 100.0
    return engine


def _send(engine, action, confidence, symbol="BTCUSDT"):
    signal = Signal(symbol=symbol, action=action, confidence=confidence, source="ml")
    with patch("src.engine.main.notifier"), patch.object(engine, "_save_trade_to_file"):
        asyncio.run(engine._on_signal(signal))


def test_buy_blocked_by_strong_negative_sentiment():
    engine = _make_engine()
    engine.sentiment_by_asset["BTCUSDT"] = -0.6

    _send(engine, "buy", confidence=0.95)

    assert "BTCUSDT" not in engine.positions


def test_sell_blocked_by_strong_positive_sentiment():
    # Il lato che il vecchio filtro asimmetrico NON copriva
    engine = _make_engine()
    engine.sentiment_by_asset["BTCUSDT"] = 0.6

    _send(engine, "sell", confidence=0.95)

    assert "BTCUSDT" not in engine.positions


def test_sell_allowed_with_negative_sentiment():
    # Sentiment negativo è FAVOREVOLE a uno short: 0.8 + 0.3×0.6 = 0.98
    engine = _make_engine()
    engine.sentiment_by_asset["BTCUSDT"] = -0.6

    _send(engine, "sell", confidence=0.8)

    assert engine.positions["BTCUSDT"].side == "short"


def test_neutral_sentiment_does_not_penalize_confidence():
    # conf 0.7 con sentiment neutro: pesata = 0.7 ≥ 0.55 → apre.
    # (Con la vecchia media pesata sarebbe stata 0.49 e il segnale, già
    # sopra la soglia della policy, sarebbe morto nel gate nascosto.)
    engine = _make_engine()
    _send(engine, "buy", confidence=0.7)
    assert engine.positions["BTCUSDT"].side == "long"


def test_favorable_sentiment_boosts_confidence_over_threshold():
    engine = _make_engine()

    # Sotto soglia senza aiuto: 0.5 < 0.55 → ignorato
    _send(engine, "buy", confidence=0.5)
    assert "BTCUSDT" not in engine.positions

    # Con sentiment favorevole: 0.5 + 0.3 × 0.8 = 0.74 ≥ 0.55 → apre
    engine.sentiment_by_asset["BTCUSDT"] = 0.8
    _send(engine, "buy", confidence=0.5)
    assert engine.positions["BTCUSDT"].side == "long"


def test_mildly_contrary_sentiment_gives_no_boost_but_no_veto():
    # -0.3: sopra il veto (-0.5), ma contributo azzerato dal max(0, ·):
    # pesata = 0.9 ≥ 0.55 → apre comunque (il vecchio abs() avrebbe
    # aggiunto +0.09 di confidenza immeritata)
    engine = _make_engine()
    engine.sentiment_by_asset["BTCUSDT"] = -0.3

    _send(engine, "buy", confidence=0.9)

    assert engine.positions["BTCUSDT"].side == "long"


def test_sentiment_asset_mapping_is_dynamic():
    engine = _make_engine()
    payload = {"BTC": 0.2, "DOGE": -0.4, "TRX": 0.1, "aggregate": 0.0}

    engine._on_sentiment_asset(json.dumps(payload))

    assert engine.sentiment_by_asset == {
        "BTCUSDT": 0.2, "DOGEUSDT": -0.4, "TRXUSDT": 0.1,
    }


def test_normalize_scores_clamps_and_recomputes_aggregate():
    raw = {"BTC": 3.0, "ETH": "n/a", "DOGE": -0.4, "aggregate": 9.9}
    scores = OllamaSentiment._normalize_scores(raw, ["BTC", "ETH", "DOGE"])

    assert scores["BTC"] == 1.0        # clampato da 3.0
    assert scores["ETH"] == 0.0        # non numerico → neutro
    assert scores["DOGE"] == -0.4
    # aggregate fuori scala → ricalcolato come media
    assert abs(scores["aggregate"] - (1.0 + 0.0 - 0.4) / 3) < 1e-9
