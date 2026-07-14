"""
Preparazione dei dati di training: feature condivise + etichetta.

Le feature vivono in src/shared/features.py (stesso modulo usato
dall'inference); qui c'è solo ciò che è specifico del training: il target.
"""
import pandas as pd

from src.shared.features import compute_features, FEATURE_COLS

# Orizzonte del target: ±0,5% entro 5 candele di config.timeframe. È
# l'orizzonte dell'INTERA strategia: max_holding_minutes dell'engine deve
# coprire TARGET_HORIZON_BARS × timeframe (5 × 1h = 300 min), altrimenti le
# posizioni vengono chiuse prima che la predizione abbia il tempo di
# realizzarsi (l'engine lo verifica in _apply_config e logga un warning).
TARGET_HORIZON_BARS = 5
TARGET_THRESHOLD = 0.005

# Target a 3 classi: gli short richiedono una predizione ATTIVA di ribasso
# (P(down) alta), non l'assenza di rialzo — con un target binario "sale/non
# sale" il mercato laterale soddisfa entrambe e uno short non è giustificato
# (docs/IMPROVEMENT_PLAN.md, S2). L'ordinamento down < flat < up è quello
# che XGBoost espone in classes_ / predict_proba.
TARGET_DOWN = 0
TARGET_FLAT = 1
TARGET_UP = 2


def prepare_train_data(df: pd.DataFrame) -> tuple:
    """Da candele OHLCV di un singolo simbolo a (X, y) pronti per il fit.
    X mantiene i nomi di colonna FEATURE_COLS: il modello viene salvato con
    quei nomi e l'inference li rivalida al caricamento."""
    features = compute_features(df)

    future_return = df['close'].shift(-TARGET_HORIZON_BARS) / df['close'] - 1
    target = pd.Series(TARGET_FLAT, index=df.index)
    target[future_return > TARGET_THRESHOLD] = TARGET_UP
    target[future_return < -TARGET_THRESHOLD] = TARGET_DOWN

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
