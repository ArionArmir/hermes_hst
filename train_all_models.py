#!/usr/bin/env python3
"""
Addestra il modello XGBoost champion su tutti i simboli configurati in
config/trading_params.yaml (Approccio A: un unico modello su dati concatenati).

Uso: python train_all_models.py
Rilanciare ogni volta che la lista `symbols` in trading_params.yaml cambia:
aggiungere un simbolo richiede solo l'edit YAML + questo comando, nessuna
altra modifica manuale.
"""
import sys
from pathlib import Path

import pandas as pd
import yaml
from loguru import logger
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data_collector import DataCollector
from src.training.feature_engine import prepare_train_data
from src.training.trainer import Trainer

CONFIG_PATH = "config/trading_params.yaml"


def load_symbols() -> list:
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
    return config["symbols"]


def to_ccxt_symbol(symbol_clean: str) -> str:
    """'BTCUSDT' -> 'BTC/USDT' (tutte le coppie di questo progetto quotano in USDT)."""
    return f"{symbol_clean[:-4]}/{symbol_clean[-4:]}"


def check_scale_invariance(per_symbol_iqr: dict, max_ratio: float = 50.0):
    """Guardia per il modello pooled: ogni feature deve avere scala
    confrontabile su tutti i simboli. Una feature in scala prezzo (es. una SMA
    grezza: ~60.000 su BTC, ~0,3 su TRX) avrebbe IQR che divergono di ordini
    di grandezza e permetterebbe al modello di riconoscere il simbolo invece
    di imparare pattern comuni. Confrontiamo gli IQR (robusti agli outlier,
    e sensati anche per feature a mediana ~0 come i returns)."""
    iqr_df = pd.DataFrame(per_symbol_iqr)  # righe = feature, colonne = simboli
    offending = []
    for feature, row in iqr_df.iterrows():
        floor = max(row.min(), 1e-12)
        ratio = row.max() / floor
        if ratio > max_ratio:
            offending.append(f"{feature} (IQR min={row.min():.3g}, max={row.max():.3g})")
    if offending:
        logger.warning(
            "⚠️ Feature NON scale-invariant tra simboli — il modello pooled può "
            f"riconoscere il simbolo dalla scala: {'; '.join(offending)}"
        )
    else:
        logger.info("✅ Check scale-invariance superato: scale delle feature omogenee tra simboli")


def main():
    symbols = load_symbols()
    logger.info(f"📋 Simboli da addestrare: {symbols}")

    collector = DataCollector()
    train_parts_X, val_parts_X, train_parts_y, val_parts_y = [], [], [], []
    per_symbol_iqr = {}

    for symbol in symbols:
        symbol_clean = symbol.replace("/", "").upper()
        df = collector.load_historical(symbol_clean, timeframe="1h")

        if df.empty:
            logger.info(f"📥 Scarico {symbol_clean}...")
            df = collector.download_historical(to_ccxt_symbol(symbol_clean), timeframe="1h", days=365)
            if not df.empty:
                collector.save_to_parquet(df, symbol_clean, timeframe="1h")

        if df.empty:
            logger.error(f"❌ Impossibile ottenere dati per {symbol_clean}, saltato")
            continue

        # Feature calcolate qui, per singolo simbolo, PRIMA di concatenare:
        # sono operazioni su finestre temporali (RSI/SMA/ATR/MACD) e mescolare
        # simboli prima di calcolarle genererebbe valori falsi ai confini.
        X, y = prepare_train_data(df)
        logger.info(f"✅ {symbol_clean}: {len(X)} righe di feature pronte")
        per_symbol_iqr[symbol_clean] = X.quantile(0.75) - X.quantile(0.25)

        # Split temporale (shuffle=False) per singolo simbolo, PRIMA di
        # concatenare: uno split unico sul dataset già unito finirebbe per
        # validare quasi solo sull'ultimo simbolo appeso, non su un campione
        # rappresentativo di tutti.
        X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, shuffle=False)
        train_parts_X.append(X_train)
        val_parts_X.append(X_val)
        train_parts_y.append(y_train)
        val_parts_y.append(y_val)

    if not train_parts_X:
        logger.error("❌ Nessun dato disponibile per nessun simbolo, training annullato")
        return

    X_train = pd.concat(train_parts_X, ignore_index=True)
    X_val = pd.concat(val_parts_X, ignore_index=True)
    y_train = pd.concat(train_parts_y, ignore_index=True)
    y_val = pd.concat(val_parts_y, ignore_index=True)
    logger.info(
        f"🧠 Dataset combinato: {len(X_train)} righe di train + {len(X_val)} di validation "
        f"da {len(train_parts_X)} simboli"
    )
    check_scale_invariance(per_symbol_iqr)

    trainer = Trainer()
    trainer.train(X_train, X_val, y_train, y_val)


if __name__ == "__main__":
    main()
