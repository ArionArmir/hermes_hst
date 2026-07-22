"""
Pubblicazione delle metriche attese del champion alla promozione
(docs/IMPROVEMENT_PLAN.md, V4/N4): il watchdog le confronta col
comportamento live per rilevare un degrado del modello.

Isolamento: chiavi su db 15 (mai quello dei servizi) e publish
INTERCETTATO — i canali pubsub sono globali al server Redis, e il
2026-07-20 questa suite ha fatto ricaricare il modello all'inference di
produzione pubblicando 'model_swap' sul canale vero. Un test non deve
poter parlare ai servizi vivi.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

import redis

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.training.trainer import Trainer

TEST_KEYS = ("champion_hit_rate", "champion_net_pnl", "champion_promoted_at",
            "active_model_path")


@dataclass
class _FakeBacktestResult:
    hit_rate: float
    net_pnl: float


class _RedisSandbox:
    """Chiavi vere ma su db 15; publish registrato invece che inviato."""

    def __init__(self):
        self._client = redis.Redis(host="localhost", port=6379, db=15,
                                   decode_responses=True)
        self.pubblicati = []

    def publish(self, channel, message):
        self.pubblicati.append((channel, message))
        return 0

    def __getattr__(self, name):
        return getattr(self._client, name)


def _make_trainer(tmp_path) -> Trainer:
    trainer = Trainer()
    trainer.redis = _RedisSandbox()
    trainer.model_path = str(tmp_path / "champion.pkl")
    trainer.challenger_path = str(tmp_path / "challenger.pkl")
    Path(trainer.challenger_path).write_bytes(b"stub-model-bytes")
    return trainer


def _cleanup(trainer: Trainer):
    trainer.redis.delete(*TEST_KEYS)


def test_swap_model_publishes_validation_metrics(tmp_path):
    trainer = _make_trainer(tmp_path)
    try:
        trainer._swap_model(_FakeBacktestResult(hit_rate=0.75, net_pnl=12.34))

        assert trainer.redis.get("champion_hit_rate") == "0.75"
        assert trainer.redis.get("champion_net_pnl") == "12.34"
        assert trainer.redis.get("champion_promoted_at") is not None
        assert Path(trainer.model_path).read_bytes() == b"stub-model-bytes"
        # la swap DEVE annunciare il nuovo modello — ma nella sandbox, non ai vivi
        assert ("model_swap", trainer.model_path) in trainer.redis.pubblicati
    finally:
        _cleanup(trainer)


def test_swap_model_without_validation_result_publishes_nothing(tmp_path):
    trainer = _make_trainer(tmp_path)
    trainer.redis.delete(*TEST_KEYS)  # stato pulito garantito prima dell'assert
    try:
        trainer._swap_model(None)

        assert trainer.redis.get("champion_hit_rate") is None
        assert trainer.redis.get("champion_net_pnl") is None
        assert Path(trainer.model_path).read_bytes() == b"stub-model-bytes"
    finally:
        _cleanup(trainer)


def test_swap_model_overwrites_previous_metrics(tmp_path):
    trainer = _make_trainer(tmp_path)
    try:
        trainer._swap_model(_FakeBacktestResult(hit_rate=0.40, net_pnl=-5.0))
        trainer._swap_model(_FakeBacktestResult(hit_rate=0.80, net_pnl=20.0))

        assert trainer.redis.get("champion_hit_rate") == "0.8"
        assert trainer.redis.get("champion_net_pnl") == "20.0"
    finally:
        _cleanup(trainer)
