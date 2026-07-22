"""Feature di funding e bookDepth (docs/PRE_REGISTRO_CHIUSURA_FEATURE.md).

Le ultime due famiglie di dati gratuiti con storia utile. Stesse regole del
positioning: allineamento asof SOLO all'indietro, tolleranza dichiarata oltre
cui la riga e' NaN ed esclusa (mai riempita), guardia anti-inf.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.shared.features import compute_features

_ROOT = Path(__file__).resolve().parents[2]
FUNDING_DIR = _ROOT / "data" / "funding"
BOOKDEPTH_DIR = _ROOT / "data" / "bookdepth"

FUNDING_COLS = ("funding_last", "funding_sum_9")
DEPTH_COLS = ("depth_imbalance_1", "depth_total_ratio_20")

# Un funding ogni 8 ore: oltre 9 il dato e' perso, non vecchio-ma-valido
TOLLERANZA_FUNDING = pd.Timedelta(hours=9)
TOLLERANZA_DEPTH = pd.Timedelta(hours=2)


def _asof(candles: pd.DataFrame, right: pd.DataFrame, on: str,
          tolerance: pd.Timedelta) -> pd.DataFrame:
    sinistra = candles.reset_index().rename(
        columns={candles.index.name or "index": "timestamp"})
    destra = right.copy()
    sinistra["timestamp"] = sinistra["timestamp"].astype("datetime64[ns]")
    destra[on] = destra[on].astype("datetime64[ns]")
    out = pd.merge_asof(sinistra, destra, left_on="timestamp", right_on=on,
                        direction="backward", tolerance=tolerance)
    if on != "timestamp":
        out = out.drop(columns=on)
    return out.set_index("timestamp")


# ---------------------------------------------------------------- funding --

def load_funding(symbol: str) -> pd.DataFrame:
    df = pd.read_parquet(FUNDING_DIR / f"{symbol}_funding.parquet")
    return df.sort_values("calc_time").reset_index(drop=True)


def attach_funding(candles: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """Le 2 colonne funding, calcolate SUGLI EVENTI e poi allineate: la somma
    dei 9 eventi precedenti (~3 giorni) va fatta sulla serie a 8h, non sulle
    barre orarie dove sarebbe una media mobile di valori ripetuti."""
    f = funding.copy()
    f["funding_last"] = f["last_funding_rate"]
    f["funding_sum_9"] = f["last_funding_rate"].rolling(9).sum()
    return _asof(candles, f[["calc_time", *FUNDING_COLS]], "calc_time",
                 TOLLERANZA_FUNDING)


def compute_features_with_funding(df: pd.DataFrame) -> pd.DataFrame:
    base = compute_features(df)
    extra = df[list(FUNDING_COLS)]
    return base.join(extra.where(extra.abs() < 1e6))


# -------------------------------------------------------------- bookDepth --

def load_bookdepth(symbol: str) -> pd.DataFrame:
    df = pd.read_parquet(BOOKDEPTH_DIR / f"{symbol}_1h.parquet")
    return df.sort_values("timestamp").reset_index(drop=True)


def attach_bookdepth(candles: pd.DataFrame, depth: pd.DataFrame) -> pd.DataFrame:
    return _asof(candles, depth[["timestamp", "bid_1pct", "ask_1pct"]],
                 "timestamp", TOLLERANZA_DEPTH)


def compute_features_with_bookdepth(df: pd.DataFrame) -> pd.DataFrame:
    base = compute_features(df)
    bid, ask = df["bid_1pct"], df["ask_1pct"]
    tot = bid + ask
    out = pd.DataFrame(index=df.index)
    # sbilancio in [-1, +1]: chi difende il prezzo vicino al mid
    out["depth_imbalance_1"] = (bid - ask) / (tot + 1e-12)
    # liquidita' che si assottiglia o ispessisce rispetto alla propria storia
    out["depth_total_ratio_20"] = tot / (tot.rolling(20).mean() + 1e-12)
    return base.join(out.where(out.abs() < 1e6))
