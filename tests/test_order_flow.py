"""
Feature di order flow (src/shared/features.py) e loro ingestione.

Contratto: le colonne taker_buy_base/n_trades sono OBBLIGATORIE — un parquet
salvato prima dell'order flow deve far fallire il calcolo a voce alta, non
produrre feature NaN e righe silenziosamente scartate. DataCollector si
autoripara riscaricando; CandleFeed (live) conserva i campi.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data_collector import DataCollector
from src.shared.features import compute_features, FEATURE_COLS, FLOW_INPUT_COLS


def _candles(n=200, seed=1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    volume = rng.lognormal(3, 1, n)
    return pd.DataFrame({
        "open": close, "high": close * 1.005, "low": close * 0.995, "close": close,
        "volume": volume,
        "taker_buy_base": volume * rng.uniform(0.2, 0.8, n),
        "n_trades": rng.integers(50, 500, n).astype(float),
    }, index=pd.date_range("2026-01-01", periods=n, freq="1h"))


def test_flow_features_are_in_feature_cols():
    for c in ("taker_buy_ratio", "taker_ratio_20", "trade_intensity", "avg_trade_size_ratio"):
        assert c in FEATURE_COLS


def test_missing_flow_columns_raise_loudly():
    df = _candles().drop(columns=["taker_buy_base"])
    with pytest.raises(ValueError, match="order flow"):
        compute_features(df)


def test_taker_buy_ratio_is_a_fraction():
    feats = compute_features(_candles())
    tbr = feats["taker_buy_ratio"].dropna()
    assert ((tbr >= 0) & (tbr <= 1)).all()


def test_taker_ratio_reflects_actual_aggressive_buying():
    df = _candles(n=150)
    df["taker_buy_base"] = df["volume"] * 0.9   # 90% comprato aggressivamente
    feats = compute_features(df)
    assert np.allclose(feats["taker_buy_ratio"].dropna(), 0.9, atol=1e-6)


def test_trade_intensity_is_relative_to_own_norm():
    # Attività costante → intensità ~1 (nessuna anomalia)
    df = _candles(n=150)
    df["n_trades"] = 100.0
    feats = compute_features(df)
    assert np.allclose(feats["trade_intensity"].dropna(), 1.0, atol=1e-6)


def test_flow_features_scale_invariant_across_symbols():
    # Un simbolo "grande" (volumi e trade 1000x) deve dare le stesse feature
    df = _candles()
    big = df.copy()
    big[["volume", "taker_buy_base"]] *= 1000.0
    big["n_trades"] *= 1000.0
    flow = ["taker_buy_ratio", "taker_ratio_20", "trade_intensity", "avg_trade_size_ratio"]
    a = compute_features(df)[flow].iloc[30:]
    b = compute_features(big)[flow].iloc[30:]
    pd.testing.assert_frame_equal(a, b, rtol=1e-9, atol=1e-9)


# ---------- ingestione ----------

def _fake_klines_response(n=3, start_ms=1_700_000_000_000):
    rows = []
    for i in range(n):
        t = start_ms + i * 3_600_000
        rows.append([t, "100.0", "101.0", "99.0", "100.5", "10.0", t + 3_599_999,
                     "1000.0", "250", "6.0", "600.0", "0"])
    return rows


def test_collector_keeps_flow_columns():
    collector = DataCollector()
    resp = MagicMock()
    resp.json = MagicMock(return_value=_fake_klines_response())
    with patch("src.data_collector.requests.get", return_value=resp):
        df = collector.fetch_ohlcv("BTC/USDT", "1h")

    for c in FLOW_INPUT_COLS:
        assert c in df.columns
    assert df["taker_buy_base"].iloc[0] == 6.0
    assert df["n_trades"].iloc[0] == 250.0


def test_collector_accepts_both_symbol_formats():
    collector = DataCollector()
    resp = MagicMock()
    resp.json = MagicMock(return_value=_fake_klines_response())
    with patch("src.data_collector.requests.get", return_value=resp) as get:
        collector.fetch_ohlcv("BTC/USDT", "1h")
        assert get.call_args.kwargs["params"]["symbol"] == "BTCUSDT"
        collector.fetch_ohlcv("ETHUSDT", "1h")
        assert get.call_args.kwargs["params"]["symbol"] == "ETHUSDT"


def test_legacy_parquet_without_flow_triggers_full_redownload(monkeypatch, tmp_path):
    """Il caso critico: un parquet vecchio (senza order flow) non va esteso —
    il concat lascerebbe NaN su tutto lo storico e le righe sparirebbero al
    dropna del training, silenziosamente."""
    monkeypatch.chdir(tmp_path)
    collector = DataCollector()
    legacy = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0},
        index=pd.date_range("2026-07-01", periods=24, freq="1h"),
    ).rename_axis("timestamp")
    collector.save_to_parquet(legacy, "BTCUSDT")   # niente colonne di flow

    fresh = _candles(n=48)
    with patch.object(collector, "download_historical", return_value=fresh) as dl:
        out = collector.update_historical("BTC/USDT", "BTCUSDT")

    dl.assert_called_once()            # ridownload completo, non estensione
    assert all(c in out.columns for c in FLOW_INPUT_COLS)
