import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score
import joblib
import os
import shutil
from datetime import datetime
from loguru import logger
import redis
from src.training.feature_engine import prepare_train_data

class Trainer:
    def __init__(self):
        self.redis = redis.Redis(host='localhost', port=6379, decode_responses=True)
        self.model_path = "config/models/champion.pkl"
        self.challenger_path = "config/models/challenger.pkl"

    def train(self, X_train: pd.DataFrame, X_val: pd.DataFrame, y_train: pd.Series, y_val: pd.Series):
        """Addestra un nuovo modello XGBoost su train/validation già pronti e già
        divisi (Approccio A: possono provenire dalla concatenazione di più
        simboli). Lo split va fatto PRIMA per singolo simbolo (rispettando
        l'ordine temporale di ciascuno) e poi concatenato separatamente per
        train e per validation — uno split unico sul dataset già concatenato
        con shuffle=False finirebbe per validare quasi solo sull'ultimo
        simbolo appeso, non su un campione rappresentativo di tutti."""
        logger.info(f"🧠 Avvio training su {len(X_train)} righe (validation: {len(X_val)})...")

        if len(X_train) < 100:
            logger.error("❌ Dati insufficienti per training")
            return False

        distribution = y_train.value_counts(normalize=True).sort_index()
        logger.info(f"📊 Distribuzione classi train (down/flat/up): {distribution.round(3).to_dict()}")

        model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            objective='multi:softprob',
            eval_metric='mlogloss',
            random_state=42
        )
        model.fit(X_train, y_train)

        acc = accuracy_score(y_val, model.predict(X_val))
        logger.info(f"✅ Accuratezza: {acc:.2%}")

        joblib.dump(model, self.challenger_path)
        logger.info(f"💾 Challenger salvato: {self.challenger_path}")

        if os.path.exists(self.model_path):
            champion = joblib.load(self.model_path)
            try:
                # Un champion con classi diverse (es. il vecchio binario) non
                # solleverebbe eccezioni su predict, ma il confronto di
                # accuratezza sarebbe privo di senso: trattalo come incompatibile.
                if list(champion.classes_) != list(model.classes_):
                    raise ValueError(
                        f"classi champion {list(champion.classes_)} != challenger {list(model.classes_)}"
                    )
                champion_acc = accuracy_score(y_val, champion.predict(X_val))
            except Exception as e:
                # Il champion è stato addestrato su un set di feature diverso
                # (nomi/ordine non compatibili con FEATURE_COLS attuale): non è
                # confrontabile né utilizzabile in inference, promuovo il
                # challenger direttamente.
                logger.warning(f"⚠️ Champion incompatibile con le feature attuali ({e}), promuovo il challenger")
                self._swap_model()
                logger.info("🏆 Challenger promosso per incompatibilità del champion")
                return True
            if acc > champion_acc:
                self._swap_model()
                logger.info(f"🏆 Nuovo champion! {acc:.2%} > {champion_acc:.2%}")
            else:
                logger.info(f"ℹ️ Challenger non supera champion ({acc:.2%} < {champion_acc:.2%})")
        else:
            self._swap_model()
            logger.info("🏆 Primo modello champion")

        return True

    def _swap_model(self):
        """Swap atomico su Redis"""
        shutil.copy(self.challenger_path, self.model_path)
        self.redis.set('active_model_path', self.model_path)
        self.redis.publish('model_swap', self.model_path)
        logger.info("🔄 Modello swapped via Redis")

# Il punto d'ingresso per addestrare su tutti i simboli configurati è
# train_all_models.py (repo root): concatena le feature di ogni simbolo in
# config/trading_params.yaml e addestra un unico champion (Approccio A).
