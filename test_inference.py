#!/usr/bin/env python3
"""
Smoke test manuale della pipeline di inference: per ogni simbolo configurato
carica le candele storiche, calcola le feature con il modulo condiviso e
interroga il champion — lo stesso identico percorso del processo di inference
(a parte la sorgente delle candele: parquet invece di REST).

Uso: python test_inference.py
"""
import sys
from pathlib import Path

import joblib
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.data_collector import DataCollector
from src.shared.features import compute_latest_features, FEATURE_COLS
from src.training.feature_engine import TARGET_DOWN, TARGET_FLAT, TARGET_UP

MODEL_PATH = "config/models/champion.pkl"

model = joblib.load(MODEL_PATH)
trained_names = list(model.get_booster().feature_names or [])
assert trained_names == FEATURE_COLS, (
    f"Champion incompatibile:\n  addestrato su: {trained_names}\n  attese:        {FEATURE_COLS}"
)
assert list(model.classes_) == [TARGET_DOWN, TARGET_FLAT, TARGET_UP], (
    f"Champion incompatibile: classi {list(model.classes_)}, attese [down, flat, up]"
)
print(f"✅ Champion compatibile ({len(FEATURE_COLS)} feature e 3 classi validate)")

with open("config/trading_params.yaml") as f:
    symbols = yaml.safe_load(f)["symbols"]

collector = DataCollector()
for symbol in symbols:
    df = collector.load_historical(symbol, timeframe="1h")
    if df.empty:
        print(f"⚠️ {symbol}: nessun parquet storico, saltato")
        continue
    features = compute_latest_features(df.iloc[-200:])
    if features is None:
        print(f"⚠️ {symbol}: candele insufficienti")
        continue
    proba = model.predict_proba(features)[0]
    print(
        f"📊 {symbol}: P(down)={proba[TARGET_DOWN]:.3f} P(flat)={proba[TARGET_FLAT]:.3f} "
        f"P(up)={proba[TARGET_UP]:.3f}"
    )

print("✅ Smoke test completato")
