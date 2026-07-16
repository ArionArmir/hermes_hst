"""
Wrapper che rende un XGBClassifier calibrato compatibile con l'interfaccia
che il resto del sistema si aspetta da un modello (inference, backtester,
dashboard): tutti validano/leggono model.classes_, model.get_booster() e
model.feature_importances_ — attributi che CalibratedClassifierCV di
sklearn non espone direttamente. Senza questo wrapper, calibrare le
probabilità romperebbe la guardia anti-skew di src/inference/main.py e la
card del modello nella dashboard.
"""
from typing import Any


class CalibratedModel:
    def __init__(self, base_model: Any, calibrator: Any):
        self.base_model = base_model
        self.calibrator = calibrator
        self.classes_ = calibrator.classes_

    def get_booster(self):
        return self.base_model.get_booster()

    @property
    def feature_importances_(self):
        return self.base_model.feature_importances_

    def predict(self, X):
        return self.calibrator.predict(X)

    def predict_proba(self, X):
        return self.calibrator.predict_proba(X)
