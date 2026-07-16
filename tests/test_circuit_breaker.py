"""
CircuitBreaker (src/shared/circuit_breaker.py, docs/IMPROVEMENT_PLAN.md V1):
sospende le nuove aperture su perdite consecutive (pausa temporanea con
cooldown), perdita giornaliera e drawdown dal picco (entrambe persistenti,
richiedono reset manuale). Nato dal fold catastrofico del walk-forward: 6
stop loss consecutivi in ~15 ore che il cap direzionale non copriva.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.shared.circuit_breaker import CircuitBreaker, CircuitBreakerParams

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def _params(**overrides):
    base = dict(max_consecutive_losses=3, consecutive_loss_cooldown_minutes=60,
               max_daily_loss_pct=0.10, max_drawdown_pct=0.30)
    base.update(overrides)
    return CircuitBreakerParams(**base)


# ---------- perdite consecutive ----------

def test_not_tripped_before_threshold():
    cb = CircuitBreaker(_params())
    cb.record_trade(-1.0, 999.0, now=T0)
    cb.record_trade(-1.0, 998.0, now=T0)
    assert not cb.is_tripped(now=T0)


def test_trips_at_consecutive_loss_threshold():
    cb = CircuitBreaker(_params())
    for i in range(3):
        cb.record_trade(-1.0, 1000.0 - i, now=T0)
    assert cb.is_tripped(now=T0)
    assert "3 perdite consecutive" in cb.status(now=T0)["reason"]


def test_a_win_resets_the_consecutive_counter():
    cb = CircuitBreaker(_params())
    cb.record_trade(-1.0, 999.0, now=T0)
    cb.record_trade(-1.0, 998.0, now=T0)
    cb.record_trade(+5.0, 1003.0, now=T0)  # vincita: azzera il contatore
    cb.record_trade(-1.0, 1002.0, now=T0)
    assert not cb.is_tripped(now=T0)


def test_consecutive_loss_trip_auto_resumes_after_cooldown():
    cb = CircuitBreaker(_params(consecutive_loss_cooldown_minutes=60))
    for i in range(3):
        cb.record_trade(-1.0, 1000.0 - i, now=T0)
    assert cb.is_tripped(now=T0 + timedelta(minutes=30))
    assert not cb.is_tripped(now=T0 + timedelta(minutes=61))  # cooldown scaduto


# ---------- perdita giornaliera ----------

def test_daily_loss_trip_is_persistent_within_the_day():
    cb = CircuitBreaker(_params(max_daily_loss_pct=0.05, max_consecutive_losses=None))
    cb.record_trade(-60.0, 940.0, now=T0)  # -6% del capitale (1000 prima del trade)
    assert cb.is_tripped(now=T0)
    assert cb.is_tripped(now=T0 + timedelta(hours=5))  # non si autoresetta come il cooldown


def test_daily_loss_trip_clears_on_new_utc_day():
    cb = CircuitBreaker(_params(max_daily_loss_pct=0.05, max_consecutive_losses=None))
    cb.record_trade(-60.0, 940.0, now=T0)
    assert cb.is_tripped(now=T0)
    next_day = T0 + timedelta(days=1)
    cb.record_trade(+1.0, 941.0, now=next_day)  # primo trade del nuovo giorno
    assert not cb.is_tripped(now=next_day)


# ---------- drawdown dal picco ----------

def test_drawdown_trip_requires_manual_reset():
    cb = CircuitBreaker(_params(max_drawdown_pct=0.20, max_consecutive_losses=None,
                               max_daily_loss_pct=None))
    cb.record_trade(+500.0, 1500.0, now=T0)   # nuovo picco: 1500
    cb.record_trade(-400.0, 1100.0, now=T0)   # drawdown -26.7% dal picco → trip
    assert cb.is_tripped(now=T0)
    assert cb.is_tripped(now=T0 + timedelta(days=10))  # non scade da solo

    cb.manual_reset()
    assert not cb.is_tripped(now=T0)


def test_peak_tracks_new_highs_not_just_initial_capital():
    cb = CircuitBreaker(_params(max_drawdown_pct=0.20, max_consecutive_losses=None,
                               max_daily_loss_pct=None))
    cb.record_trade(+200.0, 1200.0, now=T0)
    cb.record_trade(+300.0, 1500.0, now=T0)  # nuovo picco 1500
    cb.record_trade(-250.0, 1250.0, now=T0)  # -16.7% dal picco 1500: sotto soglia 20%, niente trip
    assert not cb.is_tripped(now=T0)


# ---------- nessun cap disattivato ----------

def test_none_thresholds_disable_that_specific_check():
    cb = CircuitBreaker(CircuitBreakerParams(
        max_consecutive_losses=None, max_daily_loss_pct=None, max_drawdown_pct=None))
    for i in range(20):
        cb.record_trade(-100.0, 1000.0 - i * 100, now=T0)
    assert not cb.is_tripped(now=T0)


# ---------- seeding da storico (sopravvivenza a un riavvio) ----------

def test_seed_from_history_reconstructs_consecutive_losses_and_trips():
    trades = pd.DataFrame([
        {"timestamp": "2026-03-01T10:00:00+00:00", "pnl": +5.0, "capital_after": 1005.0},
        {"timestamp": "2026-03-01T11:00:00+00:00", "pnl": -3.0, "capital_after": 1002.0},
        {"timestamp": "2026-03-01T12:00:00+00:00", "pnl": -3.0, "capital_after": 999.0},
        {"timestamp": "2026-03-01T13:00:00+00:00", "pnl": -3.0, "capital_after": 996.0},
    ])
    cb = CircuitBreaker(_params(max_consecutive_losses=3))

    cb.seed_from_history(trades, current_capital=996.0)

    assert cb.is_tripped(now=datetime(2026, 3, 1, 13, 30, tzinfo=timezone.utc))
    assert "ricostruito da storico" in cb.status()["reason"]


def test_seed_from_history_stops_counting_at_a_win():
    trades = pd.DataFrame([
        {"timestamp": "2026-03-01T10:00:00+00:00", "pnl": -3.0, "capital_after": 997.0},
        {"timestamp": "2026-03-01T11:00:00+00:00", "pnl": +5.0, "capital_after": 1002.0},
        {"timestamp": "2026-03-01T12:00:00+00:00", "pnl": -3.0, "capital_after": 999.0},
    ])
    cb = CircuitBreaker(_params(max_consecutive_losses=3))

    cb.seed_from_history(trades, current_capital=999.0)

    assert cb._consecutive_losses == 1
    assert not cb.is_tripped(now=datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc))


def test_seed_from_history_with_empty_dataframe_seeds_peak_only():
    cb = CircuitBreaker(_params())
    cb.seed_from_history(pd.DataFrame(), current_capital=1234.0)
    assert not cb.is_tripped()
    # Il picco riparte dal capitale attuale, non da zero/None
    cb.record_trade(-1000.0, 1234.0 - 1000.0)
    assert cb.is_tripped()  # drawdown enorme dal picco appena seminato


def test_seed_from_history_none_is_safe():
    cb = CircuitBreaker(_params())
    cb.seed_from_history(None, current_capital=1000.0)
    assert not cb.is_tripped()


# ---------- from_config ----------

def test_from_config_disabled_returns_none():
    assert CircuitBreakerParams.from_config({"circuit_breaker_enabled": False}) is None


def test_from_config_reads_all_fields():
    params = CircuitBreakerParams.from_config({
        "circuit_breaker_enabled": True,
        "circuit_breaker_max_consecutive_losses": 7,
        "circuit_breaker_cooldown_minutes": 90,
        "circuit_breaker_max_daily_loss_pct": 0.1,
        "circuit_breaker_max_drawdown_pct": 0.3,
    })
    assert params == CircuitBreakerParams(
        max_consecutive_losses=7, consecutive_loss_cooldown_minutes=90,
        max_daily_loss_pct=0.1, max_drawdown_pct=0.3,
    )


def test_from_config_defaults_when_keys_missing():
    params = CircuitBreakerParams.from_config({})
    assert params == CircuitBreakerParams()


def test_update_params_preserves_state():
    cb = CircuitBreaker(_params(max_consecutive_losses=3))
    cb.record_trade(-1.0, 999.0, now=T0)
    cb.record_trade(-1.0, 998.0, now=T0)
    cb.update_params(_params(max_consecutive_losses=10))  # reload di config
    assert cb._consecutive_losses == 2  # stato preservato, non azzerato
    cb.record_trade(-1.0, 997.0, now=T0)
    assert not cb.is_tripped(now=T0)  # ora servono 10 perdite, non più 3
