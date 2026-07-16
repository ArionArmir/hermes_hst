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

from watchdog import check_ollama, evaluate_checks, split_transitions, CHECKS

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
