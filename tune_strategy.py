#!/usr/bin/env python3
"""
Taratura di soglia segnali e moltiplicatori ATR sul backtester: griglia
soglia × SL × TP, metrica = PnL netto (fee e slippage inclusi) sul periodo
di validation del champion (ultimo 20% delle candele, out-of-sample per il
modello).

Usa backtest_joint (capitale e cap di margine CONDIVISI tra simboli, come
l'engine live) e non backtest_portfolio (simboli indipendenti): con crypto
correlate, simboli che aprono long in blocco competono per lo stesso
margine — backtest_portfolio non vede questo rischio e sovrastimerebbe il
PnL di soglie/moltiplicatori troppo permissivi (scoperto con
walk_forward.py, vedi docs/IMPROVEMENT_PLAN.md).

Regole di lettura dei risultati (anti-overfitting su ~70 giorni):
- scartare combinazioni con pochi trade (il PnL di 3 trade è rumore);
- preferire un "altopiano" (vicini di griglia anch'essi buoni) a un picco
  isolato — lo script stampa la media del vicinato per le migliori;
- la scelta finale va confermata su una finestra diversa (--days) e sul
  walk-forward (walk_forward.py).

Uso:
  python tune_strategy.py               # ultimo 20% delle candele
  python tune_strategy.py --days 45     # finestra alternativa di conferma
"""
import argparse
import itertools
import sys
from pathlib import Path

import joblib
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.backtest import BacktestParams, backtest_joint
from src.data_collector import DataCollector
from src.shared.features import MIN_CANDLES

CONFIG_PATH = "config/trading_params.yaml"
RESULTS_PATH = "data/tuning_results.csv"

THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75]
SL_MULTIPLIERS = [2.0, 3.0, 4.0, 5.0]
TP_MULTIPLIERS = [3.0, 4.0, 6.0, 8.0]
MIN_TRADES = 8  # sotto questa soglia il PnL è rumore statistico


def load_candles(days: int = None) -> dict:
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    collector = DataCollector()
    candles = {}
    for symbol in config["symbols"]:
        df = collector.load_historical(symbol, timeframe=config.get("timeframe", "1h"))
        if df.empty:
            print(f"⚠️ {symbol}: nessun dato, saltato")
            continue
        n_test = days * 24 if days else int(len(df) * 0.2)
        candles[symbol] = df.iloc[-(n_test + MIN_CANDLES):]
    return candles


def run_grid(model, candles: dict, direction_cap: int = None) -> pd.DataFrame:
    rows = []
    combos = list(itertools.product(THRESHOLDS, SL_MULTIPLIERS, TP_MULTIPLIERS))
    for i, (threshold, sl, tp) in enumerate(combos, 1):
        params = BacktestParams(prob_threshold=threshold,
                                atr_multiplier_sl=sl, atr_multiplier_tp=tp,
                                max_positions_same_direction=direction_cap)
        total = backtest_joint(model, candles, params)
        if total is None:
            continue
        rows.append({
            "threshold": threshold, "sl_mult": sl, "tp_mult": tp,
            "net_pnl": round(total.net_pnl, 2), "trades": total.n_trades,
            "hit_rate": round(total.hit_rate, 3),
            "max_dd_pct": round(total.max_drawdown_pct * 100, 2),
            "sharpe": round(total.sharpe, 2), "fees": round(total.fees, 2),
        })
        if i % 20 == 0:
            print(f"  … {i}/{len(combos)} combinazioni")
    return pd.DataFrame(rows)


def neighborhood_mean(results: pd.DataFrame, row) -> float:
    """PnL medio dei vicini di griglia (±1 passo per dimensione): un buon
    punto di lavoro deve stare su un altopiano, non su un picco isolato."""
    t_idx = THRESHOLDS.index(row["threshold"])
    s_idx = SL_MULTIPLIERS.index(row["sl_mult"])
    p_idx = TP_MULTIPLIERS.index(row["tp_mult"])
    near_t = THRESHOLDS[max(0, t_idx - 1):t_idx + 2]
    near_s = SL_MULTIPLIERS[max(0, s_idx - 1):s_idx + 2]
    near_p = TP_MULTIPLIERS[max(0, p_idx - 1):p_idx + 2]
    mask = (results["threshold"].isin(near_t)
            & results["sl_mult"].isin(near_s)
            & results["tp_mult"].isin(near_p))
    return round(float(results[mask]["net_pnl"].mean()), 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="config/models/champion.pkl")
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args()

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    direction_cap = config.get("max_positions_same_direction")

    model = joblib.load(args.model)
    candles = load_candles(args.days)
    n_bars = max(len(df) for df in candles.values())
    print(f"griglia {len(THRESHOLDS)}×{len(SL_MULTIPLIERS)}×{len(TP_MULTIPLIERS)} "
          f"su {len(candles)} simboli × ~{n_bars} candele "
          f"(cap direzionale: {direction_cap or 'nessuno'})\n")

    results = run_grid(model, candles, direction_cap=direction_cap)
    results.to_csv(RESULTS_PATH, index=False)

    valid = results[results["trades"] >= MIN_TRADES].copy()
    if valid.empty:
        print(f"\n⚠️ Nessuna combinazione con ≥{MIN_TRADES} trade: griglia troppo selettiva per la finestra.")
        valid = results.copy()
    valid["vicinato"] = valid.apply(lambda r: neighborhood_mean(results, r), axis=1)

    print(f"\n=== TOP 12 per PnL netto (≥{MIN_TRADES} trade) — griglia completa in {RESULTS_PATH} ===")
    top = valid.sort_values("net_pnl", ascending=False).head(12)
    print(top.to_string(index=False))

    print("\n=== TOP 5 per robustezza (media del vicinato) ===")
    robust = valid.sort_values("vicinato", ascending=False).head(5)
    print(robust.to_string(index=False))

    best = robust.iloc[0]
    print(f"\n=== dettaglio per simbolo (backtest_joint, portafoglio condiviso) della scelta robusta "
          f"(t={best['threshold']}, SL={best['sl_mult']}, TP={best['tp_mult']}) ===")
    params = BacktestParams(prob_threshold=best["threshold"],
                            atr_multiplier_sl=best["sl_mult"],
                            atr_multiplier_tp=best["tp_mult"],
                            max_positions_same_direction=direction_cap)
    result = backtest_joint(model, candles, params)
    for symbol, group in result.trades.groupby("symbol"):
        print(f"  {symbol:10s} trade={len(group):3d}  PnL={group['pnl'].sum():+8.2f}  "
              f"hit={(group['pnl'] > 0).mean():5.1%}")
    print(f"  {'PORTFOLIO':10s} trade={result.n_trades:3d}  PnL={result.net_pnl:+8.2f}  "
          f"hit={result.hit_rate:5.1%}  maxDD={result.max_drawdown_pct:6.2%}")


if __name__ == "__main__":
    main()
