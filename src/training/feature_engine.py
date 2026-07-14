"""
Preparazione dei dati di training: feature condivise + etichetta.

Le feature vivono in src/shared/features.py (stesso modulo usato
dall'inference); qui c'è solo ciò che è specifico del training: il target.
"""
import pandas as pd

from src.shared.features import compute_features, FEATURE_COLS

# Orizzonte del target: +0,5% entro 5 candele. NB: disallineato rispetto a
# max_holding_minutes dell'engine — da correggere nel punto 2 del piano
# (docs/IMPROVEMENT_PLAN.md, S1).
TARGET_HORIZON_BARS = 5
TARGET_THRESHOLD = 0.005


def prepare_train_data(df: pd.DataFrame) -> tuple:
    """Da candele OHLCV di un singolo simbolo a (X, y) pronti per il fit.
    X mantiene i nomi di colonna FEATURE_COLS: il modello viene salvato con
    quei nomi e l'inference li rivalida al caricamento."""
    features = compute_features(df)

    future_return = df['close'].shift(-TARGET_HORIZON_BARS) / df['close'] - 1
    target = (future_return > TARGET_THRESHOLD).astype(int)

    data = features.copy()
    data['target'] = target
    # Le ultime TARGET_HORIZON_BARS righe non hanno futuro osservabile: vanno
    # escluse, non etichettate 0 (il vecchio `NaN > soglia → False` le
    # trasformava silenziosamente in esempi negativi fittizi).
    data = data[future_return.notna()]
    data = data.dropna()

    X = data[FEATURE_COLS]
    y = data['target']
    return X, y
