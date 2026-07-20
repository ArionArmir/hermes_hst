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
from src.training.feature_engine import (
    prepare_train_data,
    TARGET_HORIZON_BARS,
    TARGET_THRESHOLD,
    TARGET_DOWN,
    TARGET_FLAT,
    TARGET_UP,
)


def _synthetic_ohlcv(n: int = 1000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    spread = np.abs(rng.normal(0, 0.005, n)) * close
    volume = rng.lognormal(3, 1, n)
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.002, n)),
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": volume,
        # Order flow: taker buy ~metà del volume, n_trades proporzionale
        "taker_buy_base": volume * rng.uniform(0.3, 0.7, n),
        "n_trades": rng.integers(50, 500, n).astype(float),
    })


def test_feature_columns_and_no_nan_after_warmup():
    feats = compute_features(_synthetic_ohlcv())
    assert list(feats.columns) == FEATURE_COLS
    assert not feats.iloc[MIN_CANDLES:].isna().any().any()


def test_features_are_scale_invariant():
    df = _synthetic_ohlcv()
    scaled = df.copy()
    scaled[["open", "high", "low", "close"]] *= 1000.0  # BTC vs TRX, in pratica
    # volume e taker_buy_base sono la STESSA unità (quantità di base asset):
    # vanno scalati insieme, altrimenti il rapporto tra i due perde senso fisico
    scaled[["volume", "taker_buy_base"]] *= 500.0
    # n_trades è un conteggio: BTC ne ha ~300k/ora, TRX molti meno. Anche
    # scalandolo, trade_intensity resta invariante (è rapporto sulla sua media)
    scaled["n_trades"] *= 7.0

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
    assert set(y.unique()) <= {TARGET_DOWN, TARGET_FLAT, TARGET_UP}
    # Le ultime TARGET_HORIZON_BARS righe non hanno futuro osservabile e
    # devono essere escluse, non etichettate flat
    assert X.index.max() <= df.index.max() - TARGET_HORIZON_BARS


def test_target_labels_match_future_returns():
    df = _synthetic_ohlcv()
    X, y = prepare_train_data(df)

    future_return = (df['close'].shift(-TARGET_HORIZON_BARS) / df['close'] - 1).loc[y.index]
    assert (y[future_return > TARGET_THRESHOLD] == TARGET_UP).all()
    assert (y[future_return < -TARGET_THRESHOLD] == TARGET_DOWN).all()
    flat_mask = (future_return >= -TARGET_THRESHOLD) & (future_return <= TARGET_THRESHOLD)
    assert (y[flat_mask] == TARGET_FLAT).all()
    # Con un random walk devono esistere tutte e tre le classi
    assert set(y.unique()) == {TARGET_DOWN, TARGET_FLAT, TARGET_UP}
