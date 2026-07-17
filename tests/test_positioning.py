"""
Feature di posizionamento (docs/PRE_REGISTRO_POSITIONING.md).

Il punto critico e' l'allineamento temporale: un merge che guardasse in avanti
darebbe al modello il posizionamento FUTURO, producendo un falso edge - il tipo
di bug che non fa fallire nulla e sembra una scoperta.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research.positioning import (METRIC_COLS, POSITIONING_COLS,
                                      attach_metrics,
                                      compute_features_with_positioning,
                                      compute_positioning_features)


def _candles(n=30, start="2024-01-01"):
    idx = pd.date_range(start, periods=n, freq="1h")
    close = pd.Series(np.linspace(100, 110, n), index=idx)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 10.0, "n_trades": 5.0, "taker_buy_base": 5.0},
        index=idx,
    ).rename_axis("timestamp")


def _metrics(times, oi):
    return pd.DataFrame({
        "create_time": pd.to_datetime(times),
        "sum_open_interest": oi,
        "count_long_short_ratio": 1.0,
        "sum_taker_long_short_vol_ratio": 1.0,
    })


def test_allineamento_solo_all_indietro():
    """Barra chiusa alle 01:00: puo' vedere lo snapshot delle 00:55, MAI
    quello delle 01:05. E' l'intero motivo di questo test file."""
    candles = _candles(3)
    m = _metrics(["2024-01-01 00:55", "2024-01-01 01:05"], [100.0, 999.0])
    out = attach_metrics(candles, m)
    assert out.loc["2024-01-01 01:00", "sum_open_interest"] == 100.0  # non 999


def test_snapshot_alla_chiusura_esatta_e_ammesso():
    # create_time == chiusura barra: noto in quell'istante, non e' lookahead
    candles = _candles(3)
    m = _metrics(["2024-01-01 01:00"], [42.0])
    out = attach_metrics(candles, m)
    assert out.loc["2024-01-01 01:00", "sum_open_interest"] == 42.0


def test_buco_oltre_tolleranza_da_nan_non_valori_vecchi():
    """Dopo 2h senza snapshot, un OI vecchio spacciato per corrente e'
    disinformazione: meglio NaN (la riga verra' esclusa a valle)."""
    candles = _candles(6)
    m = _metrics(["2024-01-01 00:00"], [100.0])
    out = attach_metrics(candles, m)
    assert out.loc["2024-01-01 01:00", "sum_open_interest"] == 100.0   # entro 2h
    assert pd.isna(out.loc["2024-01-01 04:00", "sum_open_interest"])   # oltre


def test_feature_calcolate_e_scale_free():
    candles = _candles(30)
    times = pd.date_range("2024-01-01", periods=30, freq="1h")
    m = _metrics(times, np.linspace(1000, 1300, 30))
    out = attach_metrics(candles, m)
    feats = compute_positioning_features(out)
    assert list(feats.columns) == list(POSITIONING_COLS)
    # oi_change_1: passo costante (300/29) rapportato al valore precedente
    passo = (1300 - 1000) / 29
    assert feats["oi_change_1"].iloc[-1] == pytest.approx(
        passo / m["sum_open_interest"].iloc[-2], rel=1e-6)
    # scale-free: moltiplicare l'OI per 1000 non cambia i rapporti
    m2 = _metrics(times, np.linspace(1000, 1300, 30) * 1000)
    feats2 = compute_positioning_features(attach_metrics(candles, m2))
    pd.testing.assert_frame_equal(feats, feats2)


def test_braccio_positioning_ha_22_colonne_e_include_le_18_base():
    from src.shared.features import FEATURE_COLS
    candles = _candles(100)
    times = pd.date_range("2024-01-01", periods=100, freq="1h")
    out = attach_metrics(candles, _metrics(times, np.linspace(1000, 1100, 100)))
    tutte = compute_features_with_positioning(out)
    assert len(tutte.columns) == len(FEATURE_COLS) + 4
    assert set(FEATURE_COLS) <= set(tutte.columns)
    assert set(POSITIONING_COLS) <= set(tutte.columns)


def test_risoluzioni_timestamp_diverse_non_rompono_il_merge():
    """I parquet reali arrivano con risoluzioni diverse (candele ms, metrics
    us) e merge_asof rifiuta chiavi di dtype diverso: il primo run e' crashato
    esattamente qui. Il test in memoria non lo vedeva (entrambi ns)."""
    candles = _candles(3)
    candles.index = candles.index.astype("datetime64[ms]")
    m = _metrics(["2024-01-01 00:55"], [100.0])
    m["create_time"] = m["create_time"].astype("datetime64[us]")
    out = attach_metrics(candles, m)
    assert out.loc["2024-01-01 01:00", "sum_open_interest"] == 100.0


def test_oi_a_zero_non_produce_infiniti():
    """Alcuni simboli hanno OI = 0 nei tratti iniziali: pct_change su uno zero
    da' inf e il rapporto con epsilon esplode a ~1e12. XGBoost rifiuta
    entrambi (il run reale e' crashato qui). Devono diventare NaN."""
    candles = _candles(30)
    times = pd.date_range("2024-01-01", periods=30, freq="1h")
    oi = np.concatenate([np.zeros(10), np.linspace(100, 130, 20)])
    out = attach_metrics(candles, _metrics(times, oi))
    feats = compute_positioning_features(out)
    assert np.isfinite(feats.to_numpy()[~np.isnan(feats.to_numpy())]).all()
    assert feats["oi_change_1"].iloc[-1] > 0        # la parte sana sopravvive


def test_metrics_mancanti_producono_righe_scartabili_non_errori():
    """Simboli o periodi senza metrics: NaN che il dropna a valle esclude,
    senza far saltare il run degli altri."""
    candles = _candles(10)
    m = _metrics(["2023-06-01 00:00"], [1.0])       # tutto fuori tolleranza
    out = attach_metrics(candles, m)
    feats = compute_positioning_features(out)
    assert feats.isna().all().all()
