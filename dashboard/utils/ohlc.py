"""
Lettura e merge delle candele per il grafico: storico 1h (parquet) + intraday live
(CSV scritto da src/shared/ohlc_aggregator.py). Nessuna logica di scrittura qui:
solo lettura lato dashboard.
"""
from pathlib import Path

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HISTORICAL_DIR = REPO_ROOT / "data" / "historical"
LIVE_DIR = REPO_ROOT / "data" / "live_ohlc"

CANDLE_COLUMNS = ["bar_time", "open", "high", "low", "close", "volume"]


def _empty_candles() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDLE_COLUMNS)


@st.cache_data(ttl="1h")
def _load_historical(symbol: str) -> pd.DataFrame:
    path = HISTORICAL_DIR / f"{symbol}_1h.parquet"
    if not path.exists():
        return _empty_candles()
    df = pd.read_parquet(path).reset_index()
    df = df.rename(columns={df.columns[0]: "bar_time"})
    df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True)
    return df[CANDLE_COLUMNS].sort_values("bar_time")


@st.cache_data(ttl="5s")
def _load_live(symbol: str) -> pd.DataFrame:
    path = LIVE_DIR / f"{symbol}.csv"
    if not path.exists():
        return _empty_candles()
    df = pd.read_csv(path)
    if df.empty:
        return _empty_candles()
    df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True)
    return df[CANDLE_COLUMNS].sort_values("bar_time")


def get_candles(symbol: str) -> pd.DataFrame:
    """Storico 1h + candele intraday live unite in un'unica serie ordinata per bar_time."""
    historical = _load_historical(symbol)
    live = _load_live(symbol)
    if historical.empty:
        return live
    if live.empty:
        return historical
    cutoff = live["bar_time"].min()
    historical = historical[historical["bar_time"] < cutoff]
    return pd.concat([historical, live], ignore_index=True)
