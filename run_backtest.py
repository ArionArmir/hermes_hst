#!/usr/bin/env python3
"""
Backtest del champion sui simboli configurati, con fee e slippage.

Due tabelle: per-simbolo (backtest_portfolio, capitale indipendente per
simbolo — utile per capire il contributo di ciascuno) e PORTFOLIO
(backtest_joint, capitale e cap di margine condivisi come l'engine live —
il numero che conta davvero, perché tiene conto del rischio di correlazione
tra simboli: docs/IMPROVEMENT_PLAN.md, scoperto con walk_forward.py).

Uso:
  python run_backtest.py                # ultimo 20% delle candele storiche
  python run_backtest.py --days 60      # ultimi 60 giorni
  python run_backtest.py --model config/models/challenger.pkl
"""
import argparse
import sys
from pathlib import Path

import joblib
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.backtest import BacktestParams, backtest_portfolio, backtest_joint
from src.data_collector import DataCollector
from src.shared.features import MIN_CANDLES

CONFIG_PATH = "config/trading_params.yaml"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="config/models/champion.pkl")
    parser.add_argument("--days", type=int, default=None,
                        help="giorni finali da simulare (default: ultimo 20%% delle candele)")
    args = parser.parse_args()

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    model = joblib.load(args.model)
    collector = DataCollector()

    candles_by_symbol = {}
    for symbol in config["symbols"]:
        df = collector.load_historical(symbol, timeframe=config.get("timeframe", "1h"))
        if df.empty:
            print(f"⚠️ {symbol}: nessun dato storico, saltato")
            continue
        n_test = args.days * 24 if args.days else int(len(df) * 0.2)
        # warmup extra così le feature esistono dalla prima barra utile
        candles_by_symbol[symbol] = df.iloc[-(n_test + MIN_CANDLES):]

    params = BacktestParams(
        max_position_usdt=config.get("max_position_size_usdt", 50.0),
        leverage=config.get("leverage", 3),
        max_exposure=config.get("max_exposure", 0.5),
        taker_fee_pct=config.get("taker_fee_pct", 0.0005),
        # Stessa soglia del sistema live: ml_confidence_threshold è l'unico
        # regolatore di selettività (usato da inference e policy)
        prob_threshold=config.get("ml_confidence_threshold", 0.55),
    )
    results = backtest_portfolio(model, candles_by_symbol, params)

    print(f"\nModello: {args.model}")
    print("--- Per simbolo (capitale indipendente) ---")
    print(f"{'simbolo':10s} {'trade':>6s} {'PnL netto':>10s} {'fee':>8s} {'ritorno':>8s} "
          f"{'hit rate':>8s} {'max DD':>7s} {'Sharpe':>7s}")
    for symbol, r in results.items():
        print(f"{symbol:10s} {r.n_trades:6d} {r.net_pnl:10.2f} {r.fees:8.2f} "
              f"{r.return_pct:8.2%} {r.hit_rate:8.1%} {r.max_drawdown_pct:7.2%} {r.sharpe:7.2f}")

    joint = backtest_joint(model, candles_by_symbol, params)
    print("\n--- PORTFOLIO (capitale e cap di margine condivisi, come l'engine live) ---")
    if joint is None:
        print("dati insufficienti per la simulazione a portafoglio condiviso")
    else:
        print(f"{'PORTFOLIO':10s} {joint.n_trades:6d} {joint.net_pnl:10.2f} {joint.fees:8.2f} "
              f"{joint.return_pct:8.2%} {joint.hit_rate:8.1%} {joint.max_drawdown_pct:7.2%} {joint.sharpe:7.2f}")


if __name__ == "__main__":
    main()
