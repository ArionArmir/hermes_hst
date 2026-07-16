"""
src/training/model_fit.py e src/training/calibrated_model.py (punto 17,
docs/IMPROVEMENT_PLAN.md M5): con un set di calibrazione sufficiente si
ottiene un CalibratedModel (early stopping + Platt scaling); sotto la
soglia minima, un XGBClassifier semplice. Il wrapper deve restare
compatibile con tutto ciò che il sistema si aspetta da un modello:
classes_, get_booster(), feature_importances_, predict/predict_proba,
e deve sopravvivere a un roundtrip joblib (così com'è salvato/caricato
in produzione).
"""
import io
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.training.calibrated_model import CalibratedModel
from src.training.model_fit import fit_model, MIN_CALIBRATION_ROWS

FEATURE_COLS = [f"f{i}" for i in range(5)]


def _synthetic_xy(n: int, seed: int = 0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, 5)), columns=FEATURE_COLS)
    y = pd.Series(rng.integers(0, 3, size=n))
    return X, y


def test_fit_without_calib_returns_plain_xgb_and_no_calibration():
    X_train, y_train = _synthetic_xy(300)
    model, info = fit_model(X_train, y_train)

    assert info["calibrated"] is False
    assert not isinstance(model, CalibratedModel)
    assert list(model.classes_) == [0, 1, 2]
    assert list(model.get_booster().feature_names) == FEATURE_COLS


def test_fit_with_too_small_calib_falls_back_to_uncalibrated():
    X_train, y_train = _synthetic_xy(300)
    X_calib, y_calib = _synthetic_xy(MIN_CALIBRATION_ROWS - 1, seed=1)

    model, info = fit_model(X_train, y_train, X_calib, y_calib)

    assert info["calibrated"] is False


def test_fit_with_calib_returns_calibrated_model_with_full_interface():
    X_train, y_train = _synthetic_xy(600)
    X_calib, y_calib = _synthetic_xy(150, seed=1)

    model, info = fit_model(X_train, y_train, X_calib, y_calib, n_estimators=100)

    assert info["calibrated"] is True
    assert isinstance(model, CalibratedModel)
    assert list(model.classes_) == [0, 1, 2]
    # Interfaccia richiesta dal resto del sistema (guardia anti-skew
    # dell'inference, card del modello in dashboard)
    assert list(model.get_booster().feature_names) == FEATURE_COLS
    assert len(model.feature_importances_) == 5

    proba = model.predict_proba(X_calib.iloc[:5])
    assert proba.shape == (5, 3)
    np.testing.assert_allclose(proba.sum(axis=1), 1.0, atol=1e-6)
    preds = model.predict(X_calib.iloc[:5])
    assert set(preds).issubset({0, 1, 2})


def test_early_stopping_uses_fewer_trees_than_requested():
    # Con dati puramente casuali il validation loss smette presto di
    # migliorare: l'early stopping deve fermarsi ben prima del tetto.
    X_train, y_train = _synthetic_xy(600)
    X_calib, y_calib = _synthetic_xy(150, seed=1)

    model, info = fit_model(X_train, y_train, X_calib, y_calib, n_estimators=300)

    assert info["n_trees"] < 300


def test_calibrated_model_survives_joblib_roundtrip():
    X_train, y_train = _synthetic_xy(600)
    X_calib, y_calib = _synthetic_xy(150, seed=1)
    model, _ = fit_model(X_train, y_train, X_calib, y_calib, n_estimators=100)

    buf = io.BytesIO()
    joblib.dump(model, buf)
    buf.seek(0)
    reloaded = joblib.load(buf)

    np.testing.assert_allclose(
        reloaded.predict_proba(X_calib.iloc[:5]), model.predict_proba(X_calib.iloc[:5])
    )
    assert list(reloaded.get_booster().feature_names) == FEATURE_COLS


def test_calibrated_model_wrapper_delegates_correctly():
    class _FakeBase:
        def get_booster(self):
            return "booster"

        feature_importances_ = np.array([0.1, 0.9])

    class _FakeCalibrator:
        classes_ = np.array([0, 1])

        def predict(self, X):
            return "predicted"

        def predict_proba(self, X):
            return "proba"

    wrapper = CalibratedModel(_FakeBase(), _FakeCalibrator())

    assert wrapper.get_booster() == "booster"
    assert list(wrapper.feature_importances_) == [0.1, 0.9]
    assert wrapper.predict(None) == "predicted"
    assert wrapper.predict_proba(None) == "proba"
    assert list(wrapper.classes_) == [0, 1]
