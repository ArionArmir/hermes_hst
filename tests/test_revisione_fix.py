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


def test_connect_bounded_poi_solleva_non_appende(monkeypatch):
    """Regressione passata 2: retry_forever infinito appendeva avvio e test.
    Ora il retry è bounded e solleva (systemd riavvia) invece di bloccare."""
    import asyncio
    from src.shared.redis_client import RedisClient

    async def scenario():
        c = RedisClient()
        chiamate = {"n": 0}

        async def from_url_rotto(*a, **k):
            chiamate["n"] += 1
            raise ConnectionError("Redis giù")

        async def no_sleep(*a, **k):
            return
        monkeypatch.setattr("src.shared.redis_client.aioredis.from_url", from_url_rotto)
        monkeypatch.setattr("src.shared.redis_client.asyncio.sleep", no_sleep)
        import pytest
        with pytest.raises(ConnectionError):
            await c.connect(tentativi=4)
        assert chiamate["n"] == 4                              # bounded, non infinito

    asyncio.run(scenario())


def test_carica_stato_oserror_transitorio_rilancia_non_butta(tmp_path, monkeypatch):
    """Regressione passata: OSError di lettura buttava uno stato valido.
    Ora rilancia (systemd riavvia, file intatto); solo la corruzione va in quarantena."""
    import src.sentiment.v2 as v2mod
    monkeypatch.setattr(v2mod, "DIR_STATO", tmp_path)
    (tmp_path / "stato.json").write_text('{"scores": {}, "viste": {}}')
    s = v2mod.SentimentV2.__new__(v2mod.SentimentV2)

    import pathlib
    orig = pathlib.Path.read_text
    def read_rotto(self, *a, **k):
        if self.name == "stato.json":
            raise OSError("EMFILE: troppi file aperti")
        return orig(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", read_rotto)

    import pytest
    with pytest.raises(OSError):
        v2mod.SentimentV2._carica_stato(s)                    # rilancia, non butta
    assert (tmp_path / "stato.json").exists()                 # file valido INTATTO
    assert not list(tmp_path.glob("stato.corrotto.*"))        # niente quarantena


def test_chiusura_abortita_se_scrittura_durevole_fallisce():
    """Regressione: insert SQLite fallito + Redis avanti → capitale regrediva
    al riavvio. Ora la chiusura si aborta e la posizione resta aperta."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.engine.main import TradingEngine

    eng = TradingEngine.__new__(TradingEngine)
    eng.capital = 1000.0
    eng.taker_fee_pct = 0.0
    pos = SimpleNamespace(side="long", is_open=True, entry_price=100.0, quantity=1.0)
    eng.positions = {"BTCUSDT": pos}
    eng.latest_prices = {"BTCUSDT": 90.0}
    eng.circuit_breaker = SimpleNamespace(record_trade=lambda *a, **k: None)
    eng.redis = SimpleNamespace(set=AsyncMock())
    eng._save_positions_to_redis = AsyncMock()
    eng.last_close_time = {}
    eng._save_trade_to_file = lambda *a, **k: False           # scrittura durevole fallita

    asyncio.run(eng._close_position("BTCUSDT", reason="STOP_LOSS", price=90.0))
    assert pos.is_open is True                                # chiusura abortita
    assert eng.capital == 1000.0                              # capitale NON mutato
    eng.redis.set.assert_not_called()                         # Redis NON toccato
