"""Motore di ricerca sulla definizione del target — esegue docs/PRE_REGISTRO_TARGET.md

Non è "un bot che prova strategie": è una ricerca a budget dichiarato. Lo
spazio (48 configurazioni), i criteri di successo e l'uso dell'holdout sono
fissati nel pre-registro PRIMA di questo codice e prima dei risultati.

Fase 1 — tutte le 48 configurazioni su walk-forward a 4 fold. Ogni tentativo
         viene registrato, vincente o perdente: senza il totale il Deflated
         Sharpe non è calcolabile.
Fase 2 — solo sui candidati che superano i criteri primari, i gate di
         robustezza (6 fold, bootstrap mensile). Sono conferme, non ricerca:
         girarli su tutti sprecherebbe ore senza aggiungere informazione.

L'holdout NON viene toccato: se un candidato passa tutto, l'apertura del lotto
A è un atto separato e deliberato (open_seal), da fare a mente fresca.

Uso:  venv/bin/python scripts/target_search.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import yaml
from loguru import logger
from scipy import stats
from sklearn.model_selection import train_test_split

from src.backtest import BacktestParams, backtest_joint
from src.data_collector import DataCollector
from src.research.target_space import make_target, search_space
from src.shared.circuit_breaker import CircuitBreakerParams
from src.shared.features import FEATURE_COLS, MIN_CANDLES, compute_features
from src.shared.holdout import (assert_research_allowed, deflated_sharpe_ratio,
                                record_trial)

FAMIGLIA = "target_definition_v1"
BUDGET = 48                 # dichiarato nel pre-registro: fissa la soglia-fortuna
PROB_THRESHOLD = 0.50       # costante, giustificata strutturalmente (vedi pre-registro)
N_FOLD = 4
N_FOLD_GATE = 6             # perturbazione delle finestre
MAX_QUOTA_SIMBOLO = 0.60    # nessun simbolo oltre il 60% del PnL
DSR_MIN = 0.90

OUT = Path(__file__).parent.parent / "docs" / "target_search_results.csv"


def _prepare(df: pd.DataFrame, spec):
    """(X, y) per una definizione di target. Le uscite del backtester restano
    3xATR per tutte le configurazioni (vedi 'cosa resta costante' nel
    pre-registro): qui varia SOLO la domanda posta al modello."""
    feats = compute_features(df)
    target, valid = make_target(df, spec)
    data = feats.copy()
    data["target"] = target
    data = data[valid].dropna()
    return data[FEATURE_COLS], data["target"]


def _folds(raw, bounds, k):
    tr_end, te_end = bounds[k], bounds[k + 1]
    return tr_end, te_end


def _run_config(spec, raw, bounds, n_fold, cfg_trading):
    """Walk-forward completo per una definizione di target."""
    pnls, trades_all = [], []
    for k in range(n_fold):
        tr_end, te_end = bounds[k], bounds[k + 1]
        tr_X, ca_X, tr_y, ca_y, tc = [], [], [], [], {}
        for sym, df in raw.items():
            td = df[df.index <= tr_end]
            X, y = _prepare(td, spec)
            if len(X) < 200:
                return None
            X_tr, X_ca, y_tr, y_ca = train_test_split(X, y, test_size=0.15, shuffle=False)
            tr_X.append(X_tr); ca_X.append(X_ca); tr_y.append(y_tr); ca_y.append(y_ca)
            test_df = df[(df.index > tr_end) & (df.index <= te_end)]
            tc[sym] = pd.concat([td.iloc[-MIN_CANDLES:], test_df])

        y_train = pd.concat(tr_y, ignore_index=True)
        # Con soglie alte/orizzonti corti una classe può sparire: il modello
        # non sarebbe confrontabile con gli altri
        if y_train.nunique() < 3:
            return None
        from src.training.model_fit import fit_model
        model, _ = fit_model(pd.concat(tr_X, ignore_index=True), y_train,
                             pd.concat(ca_X, ignore_index=True),
                             pd.concat(ca_y, ignore_index=True))

        params = BacktestParams(
            max_position_usdt=cfg_trading["max_position_size_usdt"],
            leverage=cfg_trading["leverage"], max_exposure=cfg_trading["max_exposure"],
            taker_fee_pct=cfg_trading["taker_fee_pct"],
            prob_threshold=PROB_THRESHOLD,
            max_positions_same_direction=cfg_trading["max_positions_same_direction"],
            circuit_breaker=CircuitBreakerParams.from_config(cfg_trading),
            atr_multiplier_sl=3.0, atr_multiplier_tp=3.0,
            # accoppiamento deliberato: oggi max_holding=5 e horizon=5 coincidono
            # per caso, e l'81% dei trade chiude a MAX_HOLDING
            max_holding_bars=spec.horizon,
        )
        r = backtest_joint(model, tc, params)
        pnls.append(r.net_pnl)
        if len(r.trades):
            t = r.trades.copy()
            idx = tc[list(raw)[0]].index
            t["ts"] = [idx[b] if b < len(idx) else idx[-1] for b in t["bar"]]
            trades_all.append(t)

    T = pd.concat(trades_all, ignore_index=True) if trades_all else pd.DataFrame()
    return {"pnls": pnls, "trades": T}


def _metriche(res):
    T, pnls = res["trades"], res["pnls"]
    n = len(T)
    if n < 30:
        return None
    pnl = T["pnl"].to_numpy()
    sr = pnl.mean() / pnl.std(ddof=1) if pnl.std(ddof=1) > 0 else 0.0
    attr = T.groupby("symbol")["pnl"].sum()
    tot = attr.sum()
    quota = (attr.max() / tot) if tot > 0 else 1.0
    return {
        "pnl_totale": round(float(sum(pnls)), 2),
        "worst_fold": round(float(min(pnls)), 2),
        "fold_positivi": int(sum(1 for p in pnls if p > 0)),
        "n_trade": n,
        "sharpe_trade": round(float(sr), 4),
        "dsr": round(float(deflated_sharpe_ratio(pnl, BUDGET)), 4),
        "quota_simbolo_top": round(float(quota), 3),
        "simbolo_top": str(attr.idxmax()) if len(attr) else "",
    }


def _bootstrap_mensile(T, n_boot=10_000):
    per_mese = T.groupby(T["ts"].dt.to_period("M"))["pnl"].sum().to_numpy()
    if len(per_mese) < 6:
        return None
    rng = np.random.default_rng(42)
    boot = rng.choice(per_mese, (n_boot, len(per_mese)), replace=True).sum(axis=1)
    return {"ic95_basso": round(float(np.percentile(boot, 2.5)), 1),
            "ic95_alto": round(float(np.percentile(boot, 97.5)), 1),
            "p_perdita": round(float(np.mean(boot <= 0)), 4)}


def main():
    cfg = yaml.safe_load(open("config/trading_params.yaml"))
    SYMBOLS = cfg["symbols"]

    # L'holdout non deve nemmeno essere sfiorato dalla ricerca
    assert_research_allowed(SYMBOLS)

    collector = DataCollector()
    raw = {s: collector.load_historical(s, timeframe="1h") for s in SYMBOLS}
    common = None
    for d in raw.values():
        common = d.index if common is None else common.intersection(d.index)
    common = common.sort_values()
    start = common.min() + (common.max() - common.min()) * 0.4

    def bounds_for(n):
        sp = (common.max() - start) / n
        return [start + sp * i for i in range(n + 1)]

    spazio = search_space()
    assert len(spazio) == BUDGET, f"spazio {len(spazio)} != budget dichiarato {BUDGET}"
    logger.info(f"Pre-registro: {BUDGET} configurazioni, soglia prob {PROB_THRESHOLD}, "
                f"{N_FOLD} fold su {start.date()} -> {common.max().date()}")
    logger.info(f"Soglia di successo dichiarata: DSR > {DSR_MIN:.0%} con n_trials={BUDGET}\n")

    b4 = bounds_for(N_FOLD)
    righe = []
    t0 = time.time()

    for i, spec in enumerate(spazio, 1):
        t1 = time.time()
        try:
            res = _run_config(spec, raw, b4, N_FOLD, cfg)
            m = _metriche(res) if res else None
        except Exception as e:
            logger.warning(f"[{i}/{BUDGET}] {spec.name}: errore {e}")
            m = None

        if m is None:
            record_trial(FAMIGLIA, spec.as_dict(), {"esito": "scartata: dati insufficienti"})
            logger.info(f"[{i}/{BUDGET}] {spec.name:24s} scartata (troppo pochi trade/classi)")
            righe.append({**spec.as_dict(), "esito": "scartata"})
            continue

        # Ogni tentativo va registrato, anche perdente: senza il totale il DSR
        # non è calcolabile e la correzione non corregge nulla
        record_trial(FAMIGLIA, spec.as_dict(), m)
        righe.append({**spec.as_dict(), **m})
        pd.DataFrame(righe).to_csv(OUT, index=False)

        logger.info(
            f"[{i}/{BUDGET}] {spec.name:24s} PnL {m['pnl_totale']:+8.2f} | "
            f"trade {m['n_trade']:5d} | fold+ {m['fold_positivi']}/{N_FOLD} | "
            f"SR {m['sharpe_trade']:+.4f} | DSR {m['dsr']:5.1%} | "
            f"top {m['simbolo_top'][:4]} {m['quota_simbolo_top']:.0%} | "
            f"{time.time()-t1:.0f}s"
        )

    df = pd.DataFrame(righe)
    df.to_csv(OUT, index=False)
    logger.info(f"\nFase 1 conclusa in {(time.time()-t0)/60:.0f} min -> {OUT}")

    # ---- Criteri di successo, come dichiarati PRIMA di guardare ----
    ok = df[(df.get("dsr", 0) > DSR_MIN)
            & (df.get("fold_positivi", 0) == N_FOLD)
            & (df.get("quota_simbolo_top", 1) <= MAX_QUOTA_SIMBOLO)]

    logger.info("=" * 96)
    if ok.empty:
        logger.info("ESITO: NESSUN candidato supera i criteri pre-registrati -> H4 confermata.")
        logger.info("L'holdout NON va aperto: una cartuccia spesa su un candidato bocciato")
        logger.info("è buttata, e non ne abbiamo altre.")
        best = df.sort_values("dsr", ascending=False).head(5) if "dsr" in df else df.head()
        logger.info(f"\nMigliori 5 comunque sotto soglia (per riferimento, NON promuovibili):\n"
                    f"{best.to_string(index=False)}")
        return 0

    logger.info(f"ESITO: {len(ok)} candidati superano i criteri primari. Gate di robustezza:")
    b6 = bounds_for(N_FOLD_GATE)
    for _, riga in ok.iterrows():
        spec = next(s for s in spazio if s.as_dict() == {k: riga[k] for k in
                    ("horizon", "thr_kind", "thr_val", "label")})
        res6 = _run_config(spec, raw, b6, N_FOLD_GATE, cfg)
        m6 = _metriche(res6) if res6 else None
        res4 = _run_config(spec, raw, b4, N_FOLD, cfg)
        boot = _bootstrap_mensile(res4["trades"]) if res4 else None
        logger.info(f"\n  {spec.name}:")
        logger.info(f"    4 fold : PnL {riga['pnl_totale']:+.2f} | DSR {riga['dsr']:.1%} | "
                    f"top {riga['simbolo_top']} {riga['quota_simbolo_top']:.0%}")
        if m6:
            logger.info(f"    6 fold : PnL {m6['pnl_totale']:+.2f} | "
                        f"fold+ {m6['fold_positivi']}/{N_FOLD_GATE} | "
                        f"worst {m6['worst_fold']:+.2f}")
        if boot:
            logger.info(f"    bootstrap mensile: IC95 [{boot['ic95_basso']:+.1f}, "
                        f"{boot['ic95_alto']:+.1f}] | P(perdita) {boot['p_perdita']:.1%}")
            if boot["ic95_basso"] <= 0:
                logger.info("    ⚠️  l'IC 95% include lo zero")
        record_trial(FAMIGLIA + "_gate", spec.as_dict(),
                     {"sei_fold": m6, "bootstrap": boot})

    logger.info("\n" + "=" * 96)
    logger.info("I candidati NON sono promossi da questo script. L'apertura del lotto A")
    logger.info("è un atto separato e deliberato (open_seal), da fare a mente fresca e")
    logger.info("su UN solo candidato: sceglierne il migliore fra più passati sul lotto A")
    logger.info("sarebbe una nuova selezione, e riporterebbe il problema da capo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
