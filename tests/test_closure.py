"""
Feature funding e bookDepth (docs/PRE_REGISTRO_CHIUSURA_FEATURE.md).

Stessi rischi del positioning: lookahead nell'allineamento (falso edge
silenzioso) e valori patologici che fanno esplodere XGBoost.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research.closure import (DEPTH_COLS, FUNDING_COLS, attach_bookdepth,
                                  attach_funding,
                                  compute_features_with_bookdepth,
                                  compute_features_with_funding)


def _candles(n=30, start="2024-01-01"):
    idx = pd.date_range(start, periods=n, freq="1h")
    close = pd.Series(np.linspace(100, 110, n), index=idx)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 10.0, "n_trades": 5.0, "taker_buy_base": 5.0},
        index=idx,
    ).rename_axis("timestamp")


def _funding(times, rates):
    return pd.DataFrame({"calc_time": pd.to_datetime(times),
                         "last_funding_rate": rates})


def test_funding_solo_all_indietro():
    candles = _candles(3)
    f = _funding(["2024-01-01 00:00", "2024-01-01 08:00"], [0.0001, 0.0099])
    out = attach_funding(candles, f)
    # barra 01:00: l'evento delle 08:00 e' futuro, deve vedere quello delle 00:00
    assert out.loc["2024-01-01 01:00", "funding_last"] == 0.0001


def test_funding_sum_9_calcolata_sugli_eventi_non_sulle_barre():
    """La somma dei 9 eventi precedenti va fatta sulla serie a 8h: farla sulle
    barre orarie sommerebbe 9 copie dello stesso valore ripetuto."""
    times = pd.date_range("2023-12-28", periods=12, freq="8h")
    f = _funding(times, [0.001] * 12)
    candles = _candles(5, start=str(times[-1]))
    out = attach_funding(candles, f)
    assert out["funding_sum_9"].iloc[0] == pytest.approx(0.009)   # 9 x 0.001


def test_funding_buco_oltre_9h_da_nan():
    candles = _candles(24)
    f = _funding(["2024-01-01 00:00"], [0.0003])
    out = attach_funding(candles, f)
    assert out.loc["2024-01-01 08:00", "funding_last"] == 0.0003   # entro 9h
    assert pd.isna(out.loc["2024-01-01 12:00", "funding_last"])    # oltre


def test_depth_imbalance_segno_e_limiti():
    candles = _candles(3)
    d = pd.DataFrame({"timestamp": pd.to_datetime(["2024-01-01 01:00"]),
                      "bid_1pct": [300.0], "ask_1pct": [100.0]})
    out = attach_bookdepth(candles, d)
    feats = compute_features_with_bookdepth(out)
    v = feats.loc["2024-01-01 01:00", "depth_imbalance_1"]
    assert v == pytest.approx(0.5)          # (300-100)/400: piu' difesa sotto
    assert -1 <= v <= 1


def test_depth_ratio_scale_free_e_niente_inf():
    candles = _candles(50)
    times = pd.date_range("2024-01-01", periods=50, freq="1h")
    d = pd.DataFrame({"timestamp": times,
                      "bid_1pct": np.linspace(100, 200, 50),
                      "ask_1pct": np.linspace(100, 150, 50)})
    out = attach_bookdepth(candles, d)
    f1 = compute_features_with_bookdepth(out)
    d2 = d.copy(); d2[["bid_1pct", "ask_1pct"]] *= 1e6
    f2 = compute_features_with_bookdepth(attach_bookdepth(candles, d2))
    pd.testing.assert_series_equal(f1["depth_total_ratio_20"],
                                   f2["depth_total_ratio_20"])
    arr = f1[list(DEPTH_COLS)].to_numpy()
    assert np.isfinite(arr[~np.isnan(arr)]).all()


def test_bracci_hanno_le_colonne_giuste():
    from src.shared.features import FEATURE_COLS
    candles = _candles(100)
    times = pd.date_range("2024-01-01", periods=100, freq="1h")
    ff = compute_features_with_funding(
        attach_funding(candles, _funding(pd.date_range("2023-12-25", periods=40, freq="8h"),
                                         np.full(40, 1e-4))))
    assert set(FEATURE_COLS) <= set(ff.columns) and set(FUNDING_COLS) <= set(ff.columns)
    fd = compute_features_with_bookdepth(
        attach_bookdepth(candles, pd.DataFrame({"timestamp": times,
                                                "bid_1pct": 100.0, "ask_1pct": 90.0})))
    assert set(FEATURE_COLS) <= set(fd.columns) and set(DEPTH_COLS) <= set(fd.columns)
