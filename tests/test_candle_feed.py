"""
Guardia anti-dati-stantii di CandleFeed (docs/IMPROVEMENT_PLAN.md, V2/N2):
in caso di errore di rete la cache viene servita SOLO entro max_age_seconds
dall'ultimo fetch riuscito. Oltre quella soglia: None, mai dati vecchi
spacciati per attuali.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.shared.candle_feed import CandleFeed


def _fake_klines_response(n=5):
    """n candele chiuse + 1 in formazione (l'ultima, con close_time nel
    FUTURO come manda Binance) — get_candles scarta solo quella parziale
    (revisione 2026-07-21, I5: lo scarto è condizionato a close_time)."""
    import time
    now_ms = int(time.time() * 1000)
    # l'ultima candela apre ora e chiude tra un'ora: è quella in formazione
    start_ms = now_ms - n * 3_600_000
    rows = []
    for i in range(n + 1):
        t = start_ms + i * 3_600_000
        rows.append([t, "100.0", "101.0", "99.0", "100.5", "10.0",
                    t + 3_599_999, "1000.0", 5, "5.0", "500.0", "0"])
    return rows


def _mock_session(json_payload):
    """Mock minimale di aiohttp.ClientSession per un GET riuscito."""
    resp = MagicMock()
    resp.json = AsyncMock(return_value=json_payload)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def test_max_age_defaults_from_interval():
    assert CandleFeed(interval="1h").max_age_seconds == 3600 * 2
    assert CandleFeed(interval="15m").max_age_seconds == 900 * 2
    assert CandleFeed(interval="1m").max_age_seconds == 300  # floor di 5 minuti


def test_explicit_max_age_overrides_default():
    assert CandleFeed(interval="1h", max_age_seconds=42).max_age_seconds == 42


def test_successful_fetch_populates_last_success():
    feed = CandleFeed(interval="1h")
    with patch("src.shared.candle_feed.aiohttp.ClientSession",
              return_value=_mock_session(_fake_klines_response())):
        df = asyncio.run(feed.get_candles("BTCUSDT"))

    assert df is not None and len(df) == 5  # scartata la candela parziale
    assert feed.last_success("BTCUSDT") is not None
    assert feed.oldest_last_success(["BTCUSDT"]) == feed.last_success("BTCUSDT")


def test_never_fetched_symbol_returns_none_on_failure():
    feed = CandleFeed(interval="1h")
    with patch("src.shared.candle_feed.aiohttp.ClientSession", side_effect=ConnectionError()):
        df = asyncio.run(feed.get_candles("BTCUSDT"))
    assert df is None


def test_stale_cache_within_max_age_is_still_served():
    feed = CandleFeed(interval="1h", max_age_seconds=600)
    feed._cache["BTCUSDT"] = pd.DataFrame({"close": [1.0]})
    feed._last_fetch["BTCUSDT"] = 0.0  # forza il refresh
    feed._last_success["BTCUSDT"] = datetime.now(timezone.utc) - timedelta(seconds=300)

    with patch("src.shared.candle_feed.aiohttp.ClientSession", side_effect=ConnectionError()):
        df = asyncio.run(feed.get_candles("BTCUSDT"))

    assert df is not None  # 300s < 600s di max_age: cache ancora valida


def test_cache_beyond_max_age_is_refused():
    feed = CandleFeed(interval="1h", max_age_seconds=600)
    feed._cache["BTCUSDT"] = pd.DataFrame({"close": [1.0]})
    feed._last_fetch["BTCUSDT"] = 0.0
    feed._last_success["BTCUSDT"] = datetime.now(timezone.utc) - timedelta(seconds=900)

    with patch("src.shared.candle_feed.aiohttp.ClientSession", side_effect=ConnectionError()):
        df = asyncio.run(feed.get_candles("BTCUSDT"))

    assert df is None  # 900s > 600s di max_age: meglio muto che stantio


def test_oldest_last_success_is_none_if_any_symbol_never_succeeded():
    feed = CandleFeed(interval="1h")
    feed._last_success["BTCUSDT"] = datetime.now(timezone.utc)
    # ETHUSDT non ha mai avuto un fetch riuscito

    assert feed.oldest_last_success(["BTCUSDT", "ETHUSDT"]) is None


def test_oldest_last_success_returns_the_minimum():
    feed = CandleFeed(interval="1h")
    now = datetime.now(timezone.utc)
    feed._last_success["BTCUSDT"] = now
    feed._last_success["ETHUSDT"] = now - timedelta(seconds=120)

    assert feed.oldest_last_success(["BTCUSDT", "ETHUSDT"]) == now - timedelta(seconds=120)


def test_malformed_response_falls_back_to_cache_policy():
    feed = CandleFeed(interval="1h", max_age_seconds=600)
    feed._cache["BTCUSDT"] = pd.DataFrame({"close": [1.0]})
    feed._last_fetch["BTCUSDT"] = 0.0
    feed._last_success["BTCUSDT"] = datetime.now(timezone.utc) - timedelta(seconds=100)

    with patch("src.shared.candle_feed.aiohttp.ClientSession",
              return_value=_mock_session({"error": "unexpected"})):
        df = asyncio.run(feed.get_candles("BTCUSDT"))

    assert df is not None  # risposta inattesa ma cache ancora fresca
