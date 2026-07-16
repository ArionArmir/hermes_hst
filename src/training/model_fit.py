"""
Fit di un candidato modello XGBoost, con early stopping e calibrazione delle
probabilità quando è disponibile un set di calibrazione. Unica funzione,
usata SIA dal training di produzione (src/training/trainer.py, che poi
gestisce file/Redis/promozione) SIA dal walk-forward diagnostico
(walk_forward.py, che non scrive nulla su disco): stessa procedura di fit
in entrambi i posti, altrimenti il walk-forward validerebbe un modello
diverso da quello che finisce in produzione.

Perché calibrare: XGBoost non è calibrato out-of-the-box (le probabilità
softmax non corrispondono alle frequenze reali), ma tutta la policy dei
segnali (src/shared/signal_policy.py, soglia da config) decide in base al
valore assoluto di predict_proba. Senza calibrazione la soglia tarata con
tune_strategy.py rincorre le probabilità distorte di QUESTO modello e può
smettere di valere al prossimo retraining.
"""
from typing import Optional, Tuple

import pandas as pd
import xgboost as xgb
from loguru import logger
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator

from src.training.calibrated_model import CalibratedModel

# Sotto questa soglia la calibrazione (in particolare per la classe meno
# frequente) diventa rumore: si fitta un albero senza calibrarlo.
MIN_CALIBRATION_ROWS = 50


def fit_model(X_train: pd.DataFrame, y_train: pd.Series,
             X_calib: Optional[pd.DataFrame] = None, y_calib: Optional[pd.Series] = None,
             n_estimators: int = 300) -> Tuple[object, dict]:
    """Ritorna (model, info). Se X_calib/y_calib sono presenti e abbastanza
    numerosi: early stopping (eval_set=calib) + calibrazione Platt/sigmoid
    sullo stesso set, wrappati in CalibratedModel. Altrimenti: XGBClassifier
    semplice, nessuna calibrazione (fallback per dataset piccoli)."""
    can_calibrate = (
        X_calib is not None and y_calib is not None and len(X_calib) >= MIN_CALIBRATION_ROWS
    )

    if can_calibrate:
        base_model = xgb.XGBClassifier(
            n_estimators=n_estimators,
            max_depth=5,
            learning_rate=0.1,
            objective='multi:softprob',
            eval_metric='mlogloss',
            random_state=42,
            early_stopping_rounds=20,
        )
        base_model.fit(X_train, y_train, eval_set=[(X_calib, y_calib)], verbose=False)
        n_trees = base_model.best_iteration + 1
        if n_trees >= n_estimators:
            logger.warning(
                f"⚠️ Early stopping non attivato ({n_trees}/{n_estimators} alberi): "
                f"valutare se aumentare n_estimators"
            )

        calibrator = CalibratedClassifierCV(FrozenEstimator(base_model), method="sigmoid")
        calibrator.fit(X_calib, y_calib)
        model = CalibratedModel(base_model, calibrator)
        return model, {"n_trees": n_trees, "calibrated": True}

    base_model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        objective='multi:softprob',
        eval_metric='mlogloss',
        random_state=42,
    )
    base_model.fit(X_train, y_train)
    return base_model, {"n_trees": base_model.n_estimators, "calibrated": False}
