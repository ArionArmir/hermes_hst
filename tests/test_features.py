"""
Test del modulo condiviso delle feature (src/shared/features.py).

Le due proprietà verificate qui sono i contratti che hanno causato il
train/serve skew storico del progetto (docs/IMPROVEMENT_PLAN.md, M1/M2):
1. scale-invariance: stessa serie a scala di prezzo/volume diversa → stesse
   feature (prerequisito del modello pooled multi-simbolo);
2. indipendenza dalla lunghezza dello storico: l'ultima riga calcolata su un
   anno di candele (training) deve coincidere con quella calcolata sulle
   ultime ~200 (inference).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.shared.features import compute_features, compute_latest_features, FEATURE_COLS, MIN_CANDLES
from src.training.feature_engine import prepare_train_data, TARGET_HORIZON_BARS


def _synthetic_ohlcv(n: int = 1000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    spread = np.abs(rng.normal(0, 0.005, n)) * close
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.002, n)),
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": rng.lognormal(3, 1, n),
    })


def test_feature_columns_and_no_nan_after_warmup():
    feats = compute_features(_synthetic_ohlcv())
    assert list(feats.columns) == FEATURE_COLS
    assert not feats.iloc[MIN_CANDLES:].isna().any().any()


def test_features_are_scale_invariant():
    df = _synthetic_ohlcv()
    scaled = df.copy()
    scaled[["open", "high", "low", "close"]] *= 1000.0  # BTC vs TRX, in pratica
    scaled["volume"] *= 500.0

    original = compute_features(df).iloc[MIN_CANDLES:]
    rescaled = compute_features(scaled).iloc[MIN_CANDLES:]

    pd.testing.assert_frame_equal(original, rescaled, rtol=1e-9, atol=1e-9)


def test_latest_features_independent_of_history_length():
    df = _synthetic_ohlcv()
    full = compute_latest_features(df)
    recent = compute_latest_features(df.iloc[-300:])

    assert full is not None and recent is not None
    # Tolleranza non nulla solo per la memoria esponenziale del MACD (EWM),
    # trascurabile oltre ~300 barre.
    np.testing.assert_allclose(full.values, recent.values, rtol=1e-6, atol=1e-8)


def test_compute_latest_features_requires_min_candles():
    df = _synthetic_ohlcv(n=MIN_CANDLES - 1)
    assert compute_latest_features(df) is None


def test_prepare_train_data_shape_and_target():
    df = _synthetic_ohlcv()
    X, y = prepare_train_data(df)

    assert list(X.columns) == FEATURE_COLS
    assert not X.isna().any().any()
    assert set(y.unique()) <= {0, 1}
    # Le ultime TARGET_HORIZON_BARS righe non hanno futuro osservabile e
    # devono essere escluse, non etichettate 0
    assert X.index.max() <= df.index.max() - TARGET_HORIZON_BARS
