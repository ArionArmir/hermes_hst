"""Walk-forward parametrico condiviso dai motori di ricerca.

Estratto da scripts/h3_matched_search.py: il motore breadth ne ha bisogno
identico, e una terza copia sarebbe divergenza garantita.
"""
from __future__ import annotations

import pandas as pd
from sklearn.model_selection import train_test_split

from src.backtest import BacktestParams, backtest_joint
from src.research.evaluation import soglia_per_frequenza
from src.research.target_space import TargetSpec, make_target
from src.shared.circuit_breaker import CircuitBreakerParams
from src.shared.features import FEATURE_COLS, MIN_CANDLES, compute_features

MIN_RIGHE_TRAIN = 200


def prepara(df: pd.DataFrame, spec: TargetSpec):
    """(X, y) per una definizione di target."""
    feats = compute_features(df)
    target, valid = make_target(df, spec)
    data = feats.copy()
    data["target"] = target
    data = data[valid].dropna()
    return data[FEATURE_COLS], data["target"]


def _indice_comune(tc: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """L'indice su cui backtest_joint numera le barre: l'INTERSEZIONE.

    Va ricalcolato qui e non approssimato con l'indice del primo simbolo: i
    trade riportano `bar` come posizione in questo indice, e prendere quello
    di un simbolo qualsiasi assegnerebbe timestamp sbagliati non appena le
    storie non coincidono (con 7 parquet allineati la differenza e' nulla, con
    38 no). Il bootstrap mensile raggrupperebbe i trade nei mesi sbagliati.
    """
    idx = None
    for df in tc.values():
        idx = df.index if idx is None else idx.intersection(df.index)
    return idx.sort_values()


def run_walkforward(spec: TargetSpec, q: float, raw: dict[str, pd.DataFrame],
                    bounds: list, cfg: dict) -> dict | None:
    """Walk-forward completo con soglia ricalibrata per fold alla frequenza q.

    La soglia e' il quantile 1-q delle probabilita' sul set di CALIBRAZIONE
    (dentro il train): mai sul test, sarebbe lookahead.
    """
    from src.training.model_fit import fit_model

    pnls, trades_all, soglie = [], [], []
    for k in range(len(bounds) - 1):
        tr_end, te_end = bounds[k], bounds[k + 1]
        tr_X, ca_X, tr_y, ca_y, tc = [], [], [], [], {}
        for sym, df in raw.items():
            td = df[df.index <= tr_end]
            X, y = prepara(td, spec)
            if len(X) < MIN_RIGHE_TRAIN:
                return None
            X_tr, X_ca, y_tr, y_ca = train_test_split(X, y, test_size=0.15, shuffle=False)
            tr_X.append(X_tr); ca_X.append(X_ca); tr_y.append(y_tr); ca_y.append(y_ca)
            test_df = df[(df.index > tr_end) & (df.index <= te_end)]
            tc[sym] = pd.concat([td.iloc[-MIN_CANDLES:], test_df])

        y_train = pd.concat(tr_y, ignore_index=True)
        if y_train.nunique() < 3:
            return None
        X_calib = pd.concat(ca_X, ignore_index=True)
        model, _ = fit_model(pd.concat(tr_X, ignore_index=True), y_train,
                             X_calib, pd.concat(ca_y, ignore_index=True))

        soglia = soglia_per_frequenza(model.predict_proba(X_calib), q)
        soglie.append(soglia)

        params = BacktestParams(
            max_position_usdt=cfg["max_position_size_usdt"], leverage=cfg["leverage"],
            max_exposure=cfg["max_exposure"], taker_fee_pct=cfg["taker_fee_pct"],
            prob_threshold=soglia,
            max_positions_same_direction=cfg["max_positions_same_direction"],
            circuit_breaker=CircuitBreakerParams.from_config(cfg),
            atr_multiplier_sl=3.0, atr_multiplier_tp=3.0,
            max_holding_bars=spec.horizon,
        )
        r = backtest_joint(model, tc, params)
        if r is None:
            return None
        pnls.append(r.net_pnl)
        if len(r.trades):
            t = r.trades.copy()
            idx = _indice_comune(tc)
            t["ts"] = [idx[b] if b < len(idx) else idx[-1] for b in t["bar"]]
            trades_all.append(t)

    T = pd.concat(trades_all, ignore_index=True) if trades_all else pd.DataFrame()
    return {"pnls": pnls, "trades": T,
            "soglia_media": round(sum(soglie) / len(soglie), 4)}
