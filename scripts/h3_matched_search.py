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

from src.data_collector import DataCollector
from src.research.evaluation import bootstrap_mensile, metriche
from src.research.target_space import TargetSpec
from src.research.walkforward import run_walkforward
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


def _run(spec: TargetSpec, q: float, raw, bounds, cfg):
    """Walk-forward con soglia ricalibrata per fold: ora nel runner condiviso,
    che i motori target/H3/breadth usano identico (una terza copia sarebbe
    divergenza garantita).

    Il runner corregge anche la mappatura dei timestamp: `bar` e' la posizione
    nell'INTERSEZIONE degli indici calcolata da backtest_joint, non nell'indice
    di un simbolo qualsiasi. Con i 7 parquet allineati la differenza e' nulla e
    i risultati H3 restano quelli committati; con universi eterogenei no, e il
    bootstrap mensile raggrupperebbe i trade nei mesi sbagliati.
    """
    return run_walkforward(spec, q, raw, bounds, cfg)


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
