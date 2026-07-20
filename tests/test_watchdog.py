"""
Logica pura del watchdog (watchdog.py): valutazione staleness e transizioni
di alert/recovery con dedup. Le notifiche e Redis sono fuori scope (testati
a mano: il watchdog è pensato per girare da cron).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from watchdog import (
    check_model_health,
    check_ollama,
    evaluate_checks,
    split_transitions,
    CHECKS,
    MODEL_HEALTH_MIN_TRADES,
)
from src.shared import store

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


def _iso(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def _all_fresh() -> dict:
    return {spec["key"]: _iso(5) for spec in CHECKS.values()}


def test_fresh_heartbeats_are_healthy():
    problems = evaluate_checks(_all_fresh(), NOW)
    assert all(desc is None for desc in problems.values())


def test_stale_heartbeat_is_detected_with_age():
    values = _all_fresh()
    values["heartbeat_engine"] = _iso(300)
    problems = evaluate_checks(values, NOW)

    assert problems["engine"] is not None
    assert "300s" in problems["engine"]
    assert problems["inference"] is None


def test_missing_and_garbage_keys_are_problems():
    values = _all_fresh()
    values["heartbeat_sentiment"] = None
    values["last_tick_engine"] = "not-a-timestamp"
    problems = evaluate_checks(values, NOW)

    assert "nessun heartbeat" in problems["sentiment"]
    assert "illeggibile" in problems["tick engine"]


def test_stale_candle_feed_is_detected():
    # Preavviso indipendente dagli heartbeat: il processo può essere vivo
    # (heartbeat fresco) e tradare comunque su candele vecchie se Binance
    # REST è irraggiungibile (docs/IMPROVEMENT_PLAN.md, V2).
    values = _all_fresh()
    values["candle_feed_last_success"] = _iso(1000)
    problems = evaluate_checks(values, NOW)

    assert problems["candele"] is not None
    assert "1000s" in problems["candele"]
    assert problems["inference"] is None  # heartbeat separato, resta sano


def test_naive_timestamps_are_treated_as_utc():
    values = _all_fresh()
    values["heartbeat_engine"] = (NOW - timedelta(seconds=10)).replace(tzinfo=None).isoformat()
    problems = evaluate_checks(values, NOW)
    assert problems["engine"] is None


def test_new_problem_alerts_once():
    problems = {"engine": "fermo da 300s", "inference": None}

    new_alerts, recovered = split_transitions(set(), problems)
    assert new_alerts == {"engine": "fermo da 300s"}
    assert recovered == []

    # Secondo giro: già allertato → niente doppio alert
    new_alerts, recovered = split_transitions({"engine"}, problems)
    assert new_alerts == {}
    assert recovered == []


def test_recovery_is_reported_and_clears_state():
    problems = {"engine": None, "inference": None}
    new_alerts, recovered = split_transitions({"engine"}, problems)

    assert new_alerts == {}
    assert recovered == ["engine"]


def test_ollama_responding_is_healthy():
    with patch("watchdog.requests.get", return_value=MagicMock(status_code=200)):
        assert check_ollama() is None


def test_ollama_http_error_is_a_problem():
    with patch("watchdog.requests.get", return_value=MagicMock(status_code=503)):
        assert "503" in check_ollama()


def test_ollama_unreachable_is_a_problem():
    with patch("watchdog.requests.get", side_effect=requests.exceptions.ConnectionError()):
        problem = check_ollama()
    assert problem is not None and "non raggiungibile" in problem


def test_ollama_honours_env_host(monkeypatch):
    # In Docker OLLAMA_HOST punta al container (http://ollama:11434)
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")
    with patch("watchdog.requests.get", return_value=MagicMock(status_code=200)) as get:
        check_ollama()

    assert get.call_args[0][0] == "http://ollama:11434/api/version"


# ---------- model-health monitor ----------

class _FakeRedisGet:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key):
        return self.values.get(key)


def _insert_trades(pnls):
    for i, pnl in enumerate(pnls):
        store.insert_trade(symbol="BTCUSDT", side="long", entry=100.0, exit_price=100.0 + pnl,
                           pnl=pnl, reason="TEST", timestamp=f"2026-07-16T{10 + i:02d}:00:00+00:00")


def test_too_few_trades_is_not_judged():
    _insert_trades([-1.0] * (MODEL_HEALTH_MIN_TRADES - 1))
    assert check_model_health(_FakeRedisGet()) is None


def test_healthy_hit_rate_is_not_a_problem():
    # 15/20 vincenti: hit rate sano, nessun problema anche senza guardare il PnL
    _insert_trades([+1.0] * 15 + [-1.0] * 5)
    assert check_model_health(_FakeRedisGet()) is None


def test_low_hit_rate_with_negative_pnl_is_a_problem():
    _insert_trades([-1.0] * 15 + [+0.5] * 5)  # hit rate 25%, PnL netto -12.5
    problem = check_model_health(_FakeRedisGet())
    assert problem is not None
    assert "25%" in problem


def test_low_hit_rate_with_positive_pnl_is_not_flagged():
    # Poche vincite grandi, molte piccole perdite: hit rate basso ma sano
    # (riduce i falsi positivi rispetto a guardare solo l'hit rate)
    _insert_trades([-1.0] * 15 + [+10.0] * 5)
    assert check_model_health(_FakeRedisGet()) is None


def test_problem_message_includes_champion_expectation_when_available():
    _insert_trades([-1.0] * 15 + [+0.5] * 5)
    problem = check_model_health(_FakeRedisGet({"champion_hit_rate": "0.75"}))
    assert "75%" in problem


def test_missing_champion_expectation_is_handled_gracefully():
    _insert_trades([-1.0] * 15 + [+0.5] * 5)
    problem = check_model_health(_FakeRedisGet({"champion_hit_rate": "not-a-number"}))
    assert problem is not None  # non deve sollevare, solo omettere il confronto


def test_config_drift_rilevata():
    """Il caso del 2026-07-20: Redis resuscita una soglia pre-esperimento
    diversa dal YAML dichiarato — deve urlare, non tacere."""
    import json
    from watchdog import check_config_drift
    client = MagicMock()
    client.get.return_value = json.dumps(
        {"ml_confidence_threshold": 0.55, "timeframe": "1h"})
    finto_yaml = "ml_confidence_threshold: 0.50\ntimeframe: 1h\n"
    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read = lambda: finto_yaml
        with patch("yaml.safe_load", return_value={"ml_confidence_threshold": 0.50,
                                                   "timeframe": "1h"}):
            problema = check_config_drift(client)
    assert problema and "ml_confidence_threshold" in problema


def test_config_allineata_e_redis_vuoto_sono_sani():
    import json
    from watchdog import check_config_drift
    client = MagicMock()
    with patch("yaml.safe_load", return_value={"ml_confidence_threshold": 0.50}):
        with patch("builtins.open", create=True):
            client.get.return_value = json.dumps({"ml_confidence_threshold": 0.50})
            assert check_config_drift(client) is None
            client.get.return_value = None     # Redis vuoto: al boot vince il YAML
            assert check_config_drift(client) is None
