"""Feature di posizionamento da futures metrics (docs/PRE_REGISTRO_POSITIONING.md).

Le 18 feature esistenti sono tutte trasformazioni della serie OHLCV+flow: il
posizionamento (open interest, ratio long/short) e' la prima informazione
ortogonale al prezzo disponibile sull'intera finestra storica (dump pubblici
dal 2020-09-01, snapshot ogni 5 minuti).

Le QUATTRO feature sono definite nel pre-registro PRIMA di guardare i dati.
Nessuna variante: queste o niente.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.shared.features import compute_features

_ROOT = Path(__file__).resolve().parents[2]
METRICS_DIR = _ROOT / "data" / "metrics"

METRIC_COLS = ("sum_open_interest", "count_long_short_ratio",
               "sum_taker_long_short_vol_ratio")
POSITIONING_COLS = ("oi_change_1", "oi_ratio_20", "lsr_ratio_20",
                    "taker_lsr_ratio_20")

# Oltre questo buco nei metrics la riga resta NaN e viene ESCLUSA a valle:
# riempire in avanti su buchi lunghi significherebbe dare al modello un
# posizionamento vecchio spacciandolo per corrente.
TOLLERANZA = pd.Timedelta(hours=2)


def load_metrics(symbol: str) -> pd.DataFrame:
    path = METRICS_DIR / f"{symbol}_metrics_5m.parquet"
    df = pd.read_parquet(path)
    return df.sort_values("create_time").reset_index(drop=True)


def attach_metrics(candles: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    """Aggiunge alle candele le 3 colonne metrics, allineate ALL'INDIETRO.

    merge_asof backward: per la barra chiusa a T si usa l'ultimo snapshot con
    create_time <= T. Mai in avanti - uno snapshot successivo alla chiusura
    sarebbe lookahead. Tolleranza 2h, oltre: NaN (dichiarato nel pre-registro).
    """
    out = pd.merge_asof(
        candles.reset_index().rename(columns={candles.index.name or "index": "timestamp"}),
        metrics[["create_time", *METRIC_COLS]],
        left_on="timestamp", right_on="create_time",
        direction="backward", tolerance=TOLLERANZA,
    ).drop(columns="create_time").set_index("timestamp")
    out.index.name = candles.index.name
    return out


def compute_positioning_features(df: pd.DataFrame) -> pd.DataFrame:
    """Le 4 feature del pre-registro. `df` deve contenere METRIC_COLS gia'
    allineate (attach_metrics). Rapporti su medie mobili 20 barre: stesso
    stile scale-free delle feature esistenti."""
    oi = df["sum_open_interest"]
    lsr = df["count_long_short_ratio"]
    tak = df["sum_taker_long_short_vol_ratio"]
    out = pd.DataFrame(index=df.index)
    out["oi_change_1"] = oi.pct_change()
    out["oi_ratio_20"] = oi / (oi.rolling(20).mean() + 1e-12)
    out["lsr_ratio_20"] = lsr / (lsr.rolling(20).mean() + 1e-12)
    out["taker_lsr_ratio_20"] = tak / (tak.rolling(20).mean() + 1e-12)
    return out


def compute_features_with_positioning(df: pd.DataFrame) -> pd.DataFrame:
    """18 feature base + 4 di posizionamento: il braccio 'positioning' del
    confronto appaiato. Il braccio baseline usa compute_features liscio."""
    base = compute_features(df)
    pos = compute_positioning_features(df)
    return base.join(pos)
