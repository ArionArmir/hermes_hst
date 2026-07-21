"""Regressioni della revisione 2026-07-21: ogni fix ha il suo test che
riproduce lo scenario di fallimento originale."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_guardia_inf_scarta_feature_infinite():
    """I4: ±inf passa isna() ma non deve entrare nel modello."""
    from src.shared import features
    n = features.MIN_CANDLES + 30
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="utc")
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close, "volume": rng.uniform(5, 15, n),
                       "n_trades": rng.uniform(3, 8, n),
                       "taker_buy_base": rng.uniform(1, 4, n)}, index=idx)
    assert features.compute_latest_features(df) is not None      # dati sani: ok
    df.iloc[-1, df.columns.get_loc("close")] = 0.0               # close=0 → pct_change inf
    out = features.compute_latest_features(df)
    assert out is None or np.isfinite(out.to_numpy(dtype="float64")).all()


def test_candela_completa_non_scartata(monkeypatch):
    """I5: se l'ultima candela è già chiusa, non va buttata."""
    import asyncio
    from src.shared import candle_feed as cf
    now_ms = pd.Timestamp.now(tz="utc").value // 1_000_000
    ora = 3_600_000
    # 3 candele: le prime due chiuse nel passato, la terza chiusa da poco
    righe = []
    for i, apertura in enumerate([now_ms - 3 * ora, now_ms - 2 * ora, now_ms - ora]):
        chiusura = apertura + ora - 1
        righe.append([apertura, "1", "2", "0.5", "1.5", "10",
                      chiusura, "0", "5", "3", "0", "0"])

    feed = cf.CandleFeed(interval="1h")

    class _Resp:
        status = 200
        async def json(self): return righe
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Sess:
        def get(self, *a, **k): return _Resp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(cf.aiohttp, "ClientSession", lambda *a, **k: _Sess())
    df = asyncio.run(feed.get_candles("BTCUSDT"))
    assert len(df) == 3                          # l'ultima chiusa NON scartata


def test_config_non_regredisce_ai_default():
    """I2: un reload fallito non deve sovrascrivere una config buona."""
    import asyncio
    from unittest.mock import AsyncMock
    from src.core.models import Config
    from src.inference.main import MLInference

    inf = MLInference.__new__(MLInference)
    inf.candle_feed = type("F", (), {"interval": "1h"})()
    buona = Config(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "TRXUSDT",
                            "DOGEUSDT", "BNBUSDT", "XRPUSDT"], ml_confidence_threshold=0.50)
    inf.config = buona
    inf.symbols = [s.lower() for s in buona.symbols]
    inf.redis = type("R", (), {"get_json": AsyncMock(side_effect=RuntimeError("Redis giù"))})()
    asyncio.run(inf._load_config_from_redis())
    assert inf.config.ml_confidence_threshold == 0.50 and len(inf.config.symbols) == 7


def test_breaker_giornaliero_rientra_al_cambio_giorno():
    """E1: il trip giornaliero deve rientrare al giorno UTC successivo anche
    senza nessun trade che si chiuda nel frattempo."""
    from datetime import datetime, timezone
    from src.shared.circuit_breaker import CircuitBreaker, CircuitBreakerParams
    cb = CircuitBreaker(CircuitBreakerParams(max_daily_loss_pct=0.05))
    giorno1 = datetime(2026, 7, 21, 20, 0, tzinfo=timezone.utc)
    cb.record_trade(pnl=-6.0, capital_after=94.0, now=giorno1)   # -6% → trip
    assert cb.is_tripped(now=giorno1)
    # nessun altro trade si chiude; arriva il giorno dopo
    giorno2 = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
    assert not cb.is_tripped(now=giorno2)


def test_breaker_giornaliero_ricostruito_dopo_riavvio():
    """E1b: seed_from_history deve ricostruire il trip giornaliero, non solo
    il contatore di perdite consecutive."""
    import pandas as pd
    from src.shared.circuit_breaker import CircuitBreaker, CircuitBreakerParams
    cb = CircuitBreaker(CircuitBreakerParams(max_daily_loss_pct=0.05))
    oggi = pd.Timestamp.now(tz="utc").strftime("%Y-%m-%dT10:00:00+00:00")
    trades = pd.DataFrame([{"timestamp": oggi, "pnl": -7.0, "capital_after": 93.0}])
    cb.seed_from_history(trades, current_capital=93.0)           # -7% oggi
    assert cb.is_tripped()                                       # protezione ricostruita


def test_reset_manuale_persistito_non_ri_arma(tmp_path):
    """E2: dopo un reset, un riavvio non deve ripescare il vecchio picco e
    ri-armare il trip drawdown."""
    import pandas as pd
    from src.shared.circuit_breaker import CircuitBreaker, CircuitBreakerParams
    cb = CircuitBreaker(CircuitBreakerParams(max_drawdown_pct=0.20))
    # storia: picco 1200, poi -21% a 950 (trip). Reset avvenuto a 950.
    trades = pd.DataFrame([
        {"timestamp": "2026-07-21T10:00:00+00:00", "pnl": 200.0, "capital_after": 1200.0},
        {"timestamp": "2026-07-21T11:00:00+00:00", "pnl": -250.0, "capital_after": 950.0},
    ])
    # senza reset: seed ripesca picco 1200 → 950 è -21% → trip
    cb.seed_from_history(trades, current_capital=950.0)
    assert cb.is_tripped()
    # con reset a 950 dopo l'ultimo trade: picco riparte da 950 → niente trip
    cb2 = CircuitBreaker(CircuitBreakerParams(max_drawdown_pct=0.20))
    cb2.seed_from_history(trades, current_capital=950.0,
                          reset_after="2026-07-21T11:00:01+00:00", reset_capital=950.0)
    assert not cb2.is_tripped()


def test_trailing_statico_avanza_e_non_indietreggia():
    """E6: lo stop_loss statico ratchet segue il prezzo solo in avanti."""
    from types import SimpleNamespace
    from src.engine.main import TradingEngine
    eng = TradingEngine.__new__(TradingEngine)
    eng.dynamic_exit_enabled = False
    eng.trailing_stop_pct = 0.02
    pos = SimpleNamespace(side="long", is_open=True, stop_loss=98.0, trailing_stop=98.0)
    eng.positions = {"BTCUSDT": pos}
    eng._ratchet_trailing_statico("BTCUSDT", 105.0)      # sale → stop a 102.9
    assert abs(pos.stop_loss - 105.0 * 0.98) < 1e-9
    salito = pos.stop_loss
    eng._ratchet_trailing_statico("BTCUSDT", 100.0)      # scende → stop NON indietreggia
    assert pos.stop_loss == salito


def test_trailing_statico_spento_col_dinamico():
    from types import SimpleNamespace
    from src.engine.main import TradingEngine
    eng = TradingEngine.__new__(TradingEngine)
    eng.dynamic_exit_enabled = True                      # dinamico acceso: statico inattivo
    eng.trailing_stop_pct = 0.02
    pos = SimpleNamespace(side="long", is_open=True, stop_loss=98.0, trailing_stop=98.0)
    eng.positions = {"BTCUSDT": pos}
    eng._ratchet_trailing_statico("BTCUSDT", 105.0)
    assert pos.stop_loss == 98.0                         # invariato
