#!/usr/bin/env python3
"""
Validazione walk-forward: riaddestra il modello (stessa procedura di fit di
produzione, src/training/model_fit.py) su finestre temporali consecutive a
orizzonte crescente e valuta ciascuna sul backtest a PORTAFOGLIO CONDIVISO
(backtest_joint: stesso capitale e stesso cap di margine tra simboli, come
l'engine live — mai backtest_portfolio, che tratta i simboli come se
avessero capitale indipendente e nasconde il rischio di correlazione), con
la soglia e i profili ATR correnti da config. Risponde alla domanda che
tune_strategy.py da solo non può: i parametri trovati sono buoni su TUTTO lo
storico o solo sulle finestre già viste durante la taratura?

Nessun file scritto (né champion.pkl né challenger.pkl né Redis): puro
strumento diagnostico, i modelli dei fold restano in memoria e vengono
scartati a fine esecuzione.

Uso:
  python walk_forward.py                # 4 fold, expanding window
  python walk_forward.py --folds 6
"""
import argparse
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd
import yaml
from loguru import logger
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.backtest import BacktestParams, backtest_joint
from src.data_collector import DataCollector
from src.shared.features import MIN_CANDLES
from src.training.feature_engine import prepare_train_data
from src.training.model_fit import fit_model

CONFIG_PATH = "config/trading_params.yaml"
MIN_ROWS_PER_SYMBOL = MIN_CANDLES + 100


def build_fold_boundaries(symbols_df: Dict[str, pd.DataFrame], n_folds: int) -> List[pd.Timestamp]:
    """n_folds+1 timestamp che delimitano n_folds finestre di test
    consecutive sull'ultimo 60% dello storico comune a tutti i simboli
    (il primo 40% è il minimo storico di addestramento per il primo fold)."""
    common_start = max(df.index.min() for df in symbols_df.values())
    common_end = min(df.index.max() for df in symbols_df.values())
    total = common_end - common_start
    test_region_start = common_start + total * 0.4
    fold_span = (common_end - test_region_start) / n_folds
    return [test_region_start + fold_span * i for i in range(n_folds + 1)]


def run_fold(raw: Dict[str, pd.DataFrame], train_end: pd.Timestamp, test_end: pd.Timestamp,
            params: BacktestParams):
    train_parts_X, calib_parts_X, train_parts_y, calib_parts_y = [], [], [], []
    test_candles = {}

    for symbol, df in raw.items():
        train_df = df[df.index <= train_end]
        if len(train_df) < MIN_ROWS_PER_SYMBOL:
            continue
        X, y = prepare_train_data(train_df)
        X_train, X_calib, y_train, y_calib = train_test_split(X, y, test_size=0.15, shuffle=False)
        train_parts_X.append(X_train)
        calib_parts_X.append(X_calib)
        train_parts_y.append(y_train)
        calib_parts_y.append(y_calib)

        test_df = df[(df.index > train_end) & (df.index <= test_end)]
        if test_df.empty:
            continue
        # Warmup: le ultime MIN_CANDLES righe di train, cosicché la prima
        # candela di test abbia già feature valide (niente NaN in testa).
        warmup = train_df.iloc[-MIN_CANDLES:]
        test_candles[symbol] = pd.concat([warmup, test_df])

    if not train_parts_X or not test_candles:
        return None

    X_train = pd.concat(train_parts_X, ignore_index=True)
    y_train = pd.concat(train_parts_y, ignore_index=True)
    X_calib = pd.concat(calib_parts_X, ignore_index=True)
    y_calib = pd.concat(calib_parts_y, ignore_index=True)

    model, info = fit_model(X_train, y_train, X_calib, y_calib)
    logger.info(f"  Modello: {info['n_trees']} alberi, calibrato={info['calibrated']}")

    return backtest_joint(model, test_candles, params)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--direction-cap", type=int, default=None,
                        help="override di max_positions_same_direction (default: valore in config, "
                             "0 o negativo per disattivarlo esplicitamente durante la taratura)")
    args = parser.parse_args()

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    collector = DataCollector()
    raw = {}
    for symbol in config["symbols"]:
        df = collector.load_historical(symbol, timeframe=config.get("timeframe", "1h"))
        if not df.empty:
            raw[symbol] = df
        else:
            logger.warning(f"⚠️ {symbol}: nessun dato storico, escluso dal walk-forward")
    if not raw:
        logger.error("❌ Nessun dato storico disponibile (lanciare prima train_all_models.py)")
        return

    direction_cap = (args.direction_cap if args.direction_cap is not None
                     else config.get("max_positions_same_direction"))

    boundaries = build_fold_boundaries(raw, args.folds)
    params = BacktestParams(
        max_position_usdt=config.get("max_position_size_usdt", 50.0),
        leverage=config.get("leverage", 3),
        max_exposure=config.get("max_exposure", 0.5),
        taker_fee_pct=config.get("taker_fee_pct", 0.0005),
        prob_threshold=config.get("ml_confidence_threshold", 0.55),
        max_positions_same_direction=direction_cap,
    )

    rows = []
    for k in range(args.folds):
        train_end, test_end = boundaries[k], boundaries[k + 1]
        logger.info(f"=== Fold {k + 1}/{args.folds}: train ≤ {train_end.date()}, test ≤ {test_end.date()} ===")
        result = run_fold(raw, train_end, test_end, params)
        if result is None:
            logger.warning(f"Fold {k + 1}: dati insufficienti, saltato")
            continue
        rows.append({
            "fold": k + 1, "test_fino_a": test_end.date(), "trade": result.n_trades,
            "pnl_netto": round(result.net_pnl, 2), "hit_rate": round(result.hit_rate, 3),
            "max_dd_pct": round(result.max_drawdown_pct * 100, 2), "sharpe": round(result.sharpe, 2),
        })

    if not rows:
        logger.error("❌ Nessun fold valutabile")
        return

    df_results = pd.DataFrame(rows)
    print(f"\n=== Walk-forward: {len(df_results)} fold, soglia={params.prob_threshold}, "
          f"SL/TP={params.atr_multiplier_sl or 'profili'}/{params.atr_multiplier_tp or 'profili'} ===")
    print(df_results.to_string(index=False))
    profitable = (df_results["pnl_netto"] > 0).sum()
    print(f"\nPnL netto totale: {df_results['pnl_netto'].sum():+.2f} USDT su {df_results['trade'].sum()} trade")
    print(f"Fold in profitto: {profitable}/{len(df_results)}")


if __name__ == "__main__":
    main()
