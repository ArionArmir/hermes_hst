"""H3 a frequenza di segnale appaiata — esegue docs/PRE_REGISTRO_H3.md

Il primo tentativo ha lasciato H3 senza risposta: fissava la soglia di
probabilità a 0.50 per tutte le etichette, ma cambiare etichetta cambia le
probabilità a priori delle classi, quindi 0.50 era un filtro largo per il
triple barrier e stretto per l'orizzonte fisso. Confrontava filtri, non
etichette.

Qui si fissa la FREQUENZA DI SEGNALE (q = 1/2/4% delle barre) e si lascia
variare la soglia: per ogni fold è il quantile 1-q delle probabilità sul set
di CALIBRAZIONE (dentro il train, nessun lookahead), poi applicata al test.

Due domande separate, come da pre-registro:
  H3a - a parità di occasioni prese, quale etichetta sceglie i trade migliori?
        Confronto APPAIATO su 12 coppie. Rispondibile anche se nulla passa.
  H3b - esiste una configurazione promuovibile? (DSR > 90%, n_trials=115)

Uso:  venv/bin/python scripts/h3_matched_search.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import yaml
from loguru import logger
from scipy import stats
from sklearn.model_selection import train_test_split

from src.backtest import BacktestParams, backtest_joint
from src.data_collector import DataCollector
from src.research.evaluation import bootstrap_mensile, metriche, soglia_per_frequenza
from src.research.target_space import TargetSpec, make_target
from src.shared.circuit_breaker import CircuitBreakerParams
from src.shared.features import FEATURE_COLS, MIN_CANDLES, compute_features
from src.shared.holdout import assert_research_allowed, record_trial

FAMIGLIA = "target_h3_matched_v1"
BUDGET = 24
N_TRIALS_CUMULATIVI = 115      # 91 gia' spesi + 24: cio' che provo oggi l'ho
                               # scelto guardando i 91, quindi fanno parte
                               # della selezione (scelta conservativa)
N_FOLD = 4
THR_TARGET = ("fixed", 0.005)  # controllo piu' forte noto: rende H3 piu' difficile
ORIZZONTI = (2, 5, 10, 20)
FREQUENZE = (0.01, 0.02, 0.04)
ETICHETTE = ("fixed_horizon", "triple_barrier")
DSR_MIN = 0.90
MAX_QUOTA_SIMBOLO = 0.60

OUT = Path(__file__).parent.parent / "docs" / "h3_matched_results.csv"


def _prepare(df, spec):
    feats = compute_features(df)
    target, valid = make_target(df, spec)
    data = feats.copy()
    data["target"] = target
    data = data[valid].dropna()
    return data[FEATURE_COLS], data["target"]


def _run(spec: TargetSpec, q: float, raw, bounds, cfg):
    """Walk-forward con soglia ricalibrata per fold alla frequenza q."""
    from src.training.model_fit import fit_model
    pnls, trades_all, soglie = [], [], []

    for k in range(len(bounds) - 1):
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
        if y_train.nunique() < 3:
            return None
        X_calib = pd.concat(ca_X, ignore_index=True)
        model, _ = fit_model(pd.concat(tr_X, ignore_index=True), y_train,
                             X_calib, pd.concat(ca_y, ignore_index=True))

        # La correzione: soglia dal quantile sulla CALIBRAZIONE, mai sul test
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
        pnls.append(r.net_pnl)
        if len(r.trades):
            t = r.trades.copy()
            idx = tc[list(raw)[0]].index
            t["ts"] = [idx[b] if b < len(idx) else idx[-1] for b in t["bar"]]
            trades_all.append(t)

    T = pd.concat(trades_all, ignore_index=True) if trades_all else pd.DataFrame()
    return {"pnls": pnls, "trades": T, "soglia_media": round(sum(soglie) / len(soglie), 4)}


def main():
    cfg = yaml.safe_load(open("config/trading_params.yaml"))
    SYMBOLS = cfg["symbols"]
    assert_research_allowed(SYMBOLS)

    collector = DataCollector()
    raw = {s: collector.load_historical(s, timeframe="1h") for s in SYMBOLS}
    common = None
    for d in raw.values():
        common = d.index if common is None else common.intersection(d.index)
    common = common.sort_values()
    start = common.min() + (common.max() - common.min()) * 0.4
    sp = (common.max() - start) / N_FOLD
    bounds = [start + sp * i for i in range(N_FOLD + 1)]

    logger.info(f"Pre-registro H3: {BUDGET} configurazioni, frequenze {FREQUENZE}, "
                f"n_trials cumulativi {N_TRIALS_CUMULATIVI}")
    logger.info(f"Soglie dichiarate: H3a >= 9/12 coppie | H3b DSR > {DSR_MIN:.0%}\n")

    righe = []
    t0 = time.time()
    i = 0
    for h in ORIZZONTI:
        for q in FREQUENZE:
            for label in ETICHETTE:
                i += 1
                spec = TargetSpec(h, THR_TARGET[0], THR_TARGET[1], label)
                t1 = time.time()
                try:
                    res = _run(spec, q, raw, bounds, cfg)
                    m = metriche(res["pnls"], res["trades"], N_TRIALS_CUMULATIVI) if res else None
                except Exception as e:
                    logger.warning(f"[{i}/{BUDGET}] {spec.name} q={q:.0%}: errore {e}")
                    m = None

                base = {"horizon": h, "q": q, "label": label}
                if m is None:
                    record_trial(FAMIGLIA, base, {"esito": "scartata"})
                    righe.append({**base, "esito": "scartata"})
                    logger.info(f"[{i}/{BUDGET}] h{h:<2d} q={q:.0%} {label:14s} scartata")
                    continue

                m["soglia_media"] = res["soglia_media"]
                record_trial(FAMIGLIA, base, m)
                righe.append({**base, **m})
                pd.DataFrame(righe).to_csv(OUT, index=False)
                logger.info(
                    f"[{i}/{BUDGET}] h{h:<2d} q={q:.0%} {label:14s} "
                    f"PnL {m['pnl_totale']:+8.2f} | trade {m['n_trade']:5d} | "
                    f"fold+ {m['fold_positivi']}/{N_FOLD} | SR {m['sharpe_trade']:+.4f} | "
                    f"DSR {m['dsr']:5.1%} | soglia {m['soglia_media']:.3f} | "
                    f"{time.time()-t1:.0f}s")

    df = pd.DataFrame(righe)
    df.to_csv(OUT, index=False)
    logger.info(f"\nConcluso in {(time.time()-t0)/60:.1f} min -> {OUT}\n")

    # ---------- H3a: confronto APPAIATO, la domanda vera ----------
    logger.info("=" * 96)
    logger.info("H3a — a parità di occasioni prese, il triple barrier sceglie meglio?")
    logger.info("=" * 96)
    ok = df[df.get("esito").isna()] if "esito" in df else df
    coppie, vinte = 0, 0
    for h in ORIZZONTI:
        for q in FREQUENZE:
            fh = ok[(ok.horizon == h) & (ok.q == q) & (ok.label == "fixed_horizon")]
            tb = ok[(ok.horizon == h) & (ok.q == q) & (ok.label == "triple_barrier")]
            if fh.empty or tb.empty:
                continue
            coppie += 1
            f, t = fh.iloc[0], tb.iloc[0]
            win = t.sharpe_trade > f.sharpe_trade
            vinte += bool(win)
            logger.info(f"  h{h:<2d} q={q:.0%}: fisso SR {f.sharpe_trade:+.4f} "
                        f"({f.n_trade:5.0f} trade) | TB SR {t.sharpe_trade:+.4f} "
                        f"({t.n_trade:5.0f} trade) -> {'TB' if win else 'FISSO'}")

    if coppie:
        p = stats.binomtest(vinte, coppie, 0.5).pvalue
        logger.info(f"\n  Triple barrier vince {vinte}/{coppie} coppie (test dei segni p={p:.3f})")
        if vinte >= 10:
            logger.info("  -> H3a SIGNIFICATIVA (soglia dichiarata: 10/12)")
        elif vinte >= 9:
            logger.info("  -> H3a INDICATIVA (soglia dichiarata: 9/12)")
        else:
            logger.info("  -> H3a FALSIFICATA: il triple barrier non è migliore nemmeno")
            logger.info("     a frequenza appaiata. L'ipotesi è chiusa.")

    # ---------- H3b: promuovibilità ----------
    logger.info("\n" + "=" * 96)
    prom = ok[(ok.get("dsr", 0) > DSR_MIN) & (ok.get("fold_positivi", 0) == N_FOLD)
              & (ok.get("quota_simbolo_top", 1) <= MAX_QUOTA_SIMBOLO)]
    if prom.empty:
        logger.info("H3b: NESSUNA configurazione promuovibile. L'holdout resta sigillato.")
        logger.info(f"     DSR massimo: {ok['dsr'].max():.1%} (serve > {DSR_MIN:.0%})")
    else:
        logger.info(f"H3b: {len(prom)} promuovibili. Gate di robustezza:")
        for _, r in prom.iterrows():
            spec = TargetSpec(int(r.horizon), THR_TARGET[0], THR_TARGET[1], r.label)
            res = _run(spec, float(r.q), raw, bounds, cfg)
            b = bootstrap_mensile(res["trades"]) if res else None
            logger.info(f"  {spec.name} q={r.q:.0%}: bootstrap {b}")
            record_trial(FAMIGLIA + "_gate", {"horizon": int(r.horizon), "q": float(r.q),
                                              "label": r.label}, {"bootstrap": b})
        logger.info("\n  L'apertura del lotto A resta un atto separato e deliberato.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
