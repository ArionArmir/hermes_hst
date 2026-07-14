"""
Calcoli e formattazione condivisi tra le pagine della dashboard (capitale, drawdown,
righe della tabella posizioni). Deduplicato dall'app.py originale a singolo file.
"""
import os
from typing import List, Tuple

import pandas as pd

CAPITALE_INIZIALE = 1000.0
TRADES_FILE = "data/trades_history.csv"


def load_trades_history() -> pd.DataFrame:
    if not os.path.exists(TRADES_FILE) or os.path.getsize(TRADES_FILE) == 0:
        return pd.DataFrame(columns=["timestamp", "symbol", "side", "entry", "exit", "pnl", "reason"])
    df = pd.read_csv(TRADES_FILE)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp")


def compute_capital_and_drawdown(
    trades_df: pd.DataFrame, capitale_iniziale: float = CAPITALE_INIZIALE
) -> Tuple[float, float]:
    if trades_df.empty:
        return capitale_iniziale, 0.0
    pnl_cumulato = trades_df["pnl"].sum()
    capitale_attuale = capitale_iniziale + pnl_cumulato
    cum_pnl = trades_df["pnl"].cumsum()
    peak = cum_pnl.expanding().max()
    max_drawdown = (cum_pnl - peak).min() / capitale_iniziale * 100
    return capitale_attuale, max_drawdown


def equity_curve(trades_df: pd.DataFrame, capitale_iniziale: float = CAPITALE_INIZIALE) -> List[float]:
    if trades_df.empty:
        return [capitale_iniziale]
    return (capitale_iniziale + trades_df["pnl"].cumsum()).tolist()


def compute_position_row(symbol: str, pos: dict, current_price: float) -> dict:
    entry = pos.get("entry_price", 0.0)
    quantity = pos.get("quantity", 0.0)
    side = pos.get("side", "long")
    sl = pos.get("stop_loss", 0.0)
    tp = pos.get("take_profit", 0.0)

    if side == "long":
        pnl_current = (current_price - entry) * quantity
        pnl_sl = (sl - entry) * quantity
        pnl_tp = (tp - entry) * quantity
    else:
        pnl_current = (entry - current_price) * quantity
        pnl_sl = (entry - sl) * quantity
        pnl_tp = (entry - tp) * quantity

    return {
        "Symbol": symbol,
        "Side": side.upper(),
        "Qty": quantity,
        "Entry": entry,
        "Current": current_price,
        "Stop Loss": sl,
        "Take Profit": tp,
        "PnL (USDT)": pnl_current,
        "PnL a SL": pnl_sl,
        "PnL a TP": pnl_tp,
    }


def age_seconds(iso_timestamp: str) -> float:
    return (pd.Timestamp.now(tz="UTC") - pd.Timestamp(iso_timestamp)).total_seconds()


def heartbeat_status(heartbeat_iso: str, is_running: bool, stale_after_seconds: float) -> str:
    """Ritorna 'ok' / 'stale' / 'down' incrociando processo OS e freschezza dell'heartbeat."""
    if not is_running:
        return "down"
    if not heartbeat_iso:
        return "stale"
    return "ok" if age_seconds(heartbeat_iso) <= stale_after_seconds else "stale"
