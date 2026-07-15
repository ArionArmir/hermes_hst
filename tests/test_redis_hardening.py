"""
Punto 14 (docs/IMPROVEMENT_PLAN.md, A4 + §4): listener Redis con recovery su
pubsub NUOVO (il vecchio schema riusava un pubsub potenzialmente
irrecuperabile: il listener moriva in silenzio) e throttle delle scritture
tick su Redis (lo stato in memoria resta per-tick).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.engine.main import TradingEngine
from src.inference.main import MLInference
from src.shared.redis_client import RedisClient
from src.shared.throttle import WriteThrottle


# ---------- WriteThrottle ----------

def test_throttle_first_write_passes_then_blocks(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("src.shared.throttle.time.monotonic", lambda: clock[0])
    throttle = WriteThrottle(interval_seconds=0.5)

    assert throttle.ready("k") is True
    assert throttle.ready("k") is False      # subito dopo: bloccato
    clock[0] += 0.3
    assert throttle.ready("k") is False      # non è ancora passato l'intervallo
    clock[0] += 0.3
    assert throttle.ready("k") is True       # 0.6s totali → passa


def test_throttle_keys_are_independent(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("src.shared.throttle.time.monotonic", lambda: clock[0])
    throttle = WriteThrottle(interval_seconds=0.5)

    assert throttle.ready("a") is True
    assert throttle.ready("b") is True       # chiave diversa, non bloccata da "a"
    assert throttle.ready("a") is False


# ---------- subscribe_fresh (Redis reale) ----------

def test_subscribe_fresh_returns_distinct_pubsubs():
    async def scenario():
        client = RedisClient(host="localhost")
        await client.connect()
        first = await client.subscribe_fresh("canale_test_a")
        second = await client.subscribe_fresh("canale_test_a", "canale_test_b")
        assert first is not second
        await first.aclose()
        await second.aclose()
        await client.close()

    asyncio.run(scenario())


# ---------- recovery dei listener ----------

class _FailingPubSub:
    async def listen(self):
        raise ConnectionError("connessione persa")
        yield  # pragma: no cover — rende listen un async generator


class _HealthyPubSub:
    """Alla prima iterazione ferma il servizio: il loop esce pulito."""
    def __init__(self, owner):
        self.owner = owner

    async def listen(self):
        self.owner.running = False
        return
        yield  # pragma: no cover


def _resubscribe_scenario(service) -> int:
    calls = []

    async def subscribe_fresh(*channels):
        calls.append(channels)
        return _FailingPubSub() if len(calls) == 1 else _HealthyPubSub(service)

    service.redis = type("FakeRedis", (), {"subscribe_fresh": staticmethod(subscribe_fresh)})()
    service._listener_backoff_seconds = 0
    asyncio.run(service._redis_listener())
    return len(calls)


def test_engine_listener_resubscribes_with_fresh_pubsub_after_failure():
    engine = TradingEngine()
    assert _resubscribe_scenario(engine) == 2  # fallito 1 → ri-sottoscritto


def test_inference_listener_resubscribes_with_fresh_pubsub_after_failure():
    inference = MLInference()
    assert _resubscribe_scenario(inference) == 2
