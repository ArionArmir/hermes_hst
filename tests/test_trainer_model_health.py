"""
Pubblicazione delle metriche attese del champion alla promozione
(docs/IMPROVEMENT_PLAN.md, V4/N4): il watchdog le confronta col
comportamento live per rilevare un degrado del modello. Usa Redis reale
(come test_redis_hardening.py) ma su model_path/challenger_path sandboxati
in tmp_path e con pulizia esplicita delle chiavi al termine.
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


def _make_trainer(tmp_path) -> Trainer:
    trainer = Trainer()
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
