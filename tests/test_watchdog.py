"""
Logica pura del watchdog (watchdog.py): valutazione staleness e transizioni
di alert/recovery con dedup. Le notifiche e Redis sono fuori scope (testati
a mano: il watchdog è pensato per girare da cron).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from watchdog import evaluate_checks, split_transitions, CHECKS

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
