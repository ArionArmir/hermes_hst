"""Positioning — esegue docs/PRE_REGISTRO_POSITIONING.md

Confronto appaiato: 2 config (h10 e h5, 0.5% fisso, q=1%, U47) x 2 bracci
(18 feature / 18 + 4 di posizionamento) = 4 run. L'unica differenza dentro
una coppia e' l'informazione data al modello: open interest e ratio long/short
dai dump pubblici di Binance (5 min dal 2020-09), la prima informazione
ortogonale al prezzo disponibile sull'intera finestra.

Prior dichiarato nel pre-registro: H7-nulla attesa. Decisione (H7b) solo sulla
config h10+positioning; gate primario il bootstrap mensile.

Uso:  venv/bin/python scripts/positioning_search.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import yaml
from loguru import logger

from src.data_collector import DataCollector
from src.research.evaluation import bootstrap_mensile, metriche
from src.research.positioning import (attach_metrics,
                                      compute_features_with_positioning,
                                      load_metrics)
from src.research.target_space import TargetSpec
from src.research.walkforward import run_walkforward
from src.shared.holdout import assert_research_allowed, record_trial

sys.path.insert(0, str(Path(__file__).parent))
from breadth_download import universo

FAMIGLIA = "positioning_v1"
N_TRIALS_CUMULATIVI = 129          # 125 spesi + 4 nuovi
N_FOLD = 4
Q = 0.01
ORIZZONTI = (10, 5)                # h10 primaria, h5 coppia di controllo
DSR_MIN = 0.90
MAX_QUOTA_SIMBOLO = 0.60
ANNI_MINIMI = 5.0

OUT = Path(__file__).parent.parent / "docs" / "positioning_results.csv"


def main():
    cfg = yaml.safe_load(open("config/trading_params.yaml"))
    univ = universo()
    assert_research_allowed(univ)

    collector = DataCollector()
    raw = {}
    for s in univ:
        try:
            candele = collector.load_historical(s, timeframe="1h")
            m = load_metrics(s)
        except Exception as e:
            logger.error(f"  {s}: {e} — il pre-registro richiede TUTTI i 47")
            return 1
        # Le colonne metrics viaggiano DENTRO le candele: cosi' arrivano sia a
        # prepara() sia a backtest_joint senza toccare le firme intermedie
        raw[s] = attach_metrics(candele, m)

    # ---- Gate del pre-registro: intersezione con metrics validi >= 5 anni ----
    comune = None
    for d in raw.values():
        ok = d[d["sum_open_interest"].notna()].index
        comune = ok if comune is None else comune.intersection(ok)
    comune = comune.sort_values()
    anni = (comune.max() - comune.min()).days / 365.25
    logger.info(f"Intersezione candele+metrics: {len(comune):,} barre "
                f"{comune.min().date()} -> {comune.max().date()} = {anni:.2f} anni")
    if anni < ANNI_MINIMI:
        logger.error(f"❌ STOP: {anni:.2f} anni < {ANNI_MINIMI}. Gate del "
                     "pre-registro non superato.")
        return 1
    logger.info(f"✅ Gate superato\n")

    start = comune.min() + (comune.max() - comune.min()) * 0.4
    sp = (comune.max() - start) / N_FOLD
    bounds = [start + sp * i for i in range(N_FOLD + 1)]

    righe = []
    for h in ORIZZONTI:
        spec = TargetSpec(horizon=h, thr_kind="fixed", thr_val=0.005,
                          label="fixed_horizon")
        for braccio, ffn in (("baseline_18", None),
                             ("positioning_22", compute_features_with_positioning)):
            t0 = time.time()
            res = run_walkforward(spec, Q, raw, bounds, cfg, feature_fn=ffn)
            m = metriche(res["pnls"], res["trades"], N_TRIALS_CUMULATIVI) if res else None
            base = {"horizon": h, "braccio": braccio}
            if m is None:
                record_trial(FAMIGLIA, base, {"esito": "scartata"})
                logger.info(f"h{h} {braccio}: scartata")
                continue
            boot = bootstrap_mensile(res["trades"])
            record_trial(FAMIGLIA, base, {**m, **(boot or {})})
            righe.append({**base, **m, **(boot or {})})
            pd.DataFrame(righe).to_csv(OUT, index=False)
            logger.info(
                f"h{h:<2d} {braccio:15s}: PnL {m['pnl_totale']:+8.2f} | "
                f"trade {m['n_trade']:5d} | fold+ {m['fold_positivi']}/{N_FOLD} | "
                f"SR {m['sharpe_trade']:+.4f} | DSR {m['dsr']:5.1%}"
                + (f" | IC95 [{boot['ic95_basso']:+.0f}, {boot['ic95_alto']:+.0f}]"
                   if boot else "")
                + f" | {time.time()-t0:.0f}s")

    df = pd.DataFrame(righe)

    # ---- H7a: confronto appaiato ----
    logger.info("\n" + "=" * 96)
    logger.info("H7a — il posizionamento migliora il segnale? (appaiato, 2 coppie)")
    logger.info("=" * 96)
    vinte = 0
    for h in ORIZZONTI:
        b = df[(df.horizon == h) & (df.braccio == "baseline_18")]
        p = df[(df.horizon == h) & (df.braccio == "positioning_22")]
        if b.empty or p.empty:
            continue
        win = p.iloc[0].sharpe_trade > b.iloc[0].sharpe_trade
        vinte += bool(win)
        logger.info(f"  h{h:<2d}: baseline SR {b.iloc[0].sharpe_trade:+.4f} | "
                    f"positioning SR {p.iloc[0].sharpe_trade:+.4f} -> "
                    f"{'POSITIONING' if win else 'BASELINE'}")
    logger.info(f"  positioning vince {vinte}/2 "
                f"(pre-registro: 2/2 = indicativa, non conclusiva)")
    logger.info(f"  -> H7a {'INDICATIVA' if vinte == 2 else 'FALSIFICATA'}")

    # ---- H7b: promozione, SOLO su h10+positioning ----
    prim = df[(df.horizon == 10) & (df.braccio == "positioning_22")]
    logger.info("\n" + "=" * 96)
    if prim.empty:
        logger.error("Primario mancante: nessuna decisione.")
        return 1
    p = prim.iloc[0]
    ic_ok = p.get("ic95_basso", 0) > 0
    h7b = (p.dsr > DSR_MIN and p.fold_positivi == N_FOLD
           and p.quota_simbolo_top <= MAX_QUOTA_SIMBOLO and ic_ok)
    logger.info(f"H7b — promuovibile? (h10 + positioning)")
    logger.info(f"     DSR {p.dsr:.1%} (> {DSR_MIN:.0%}): {'OK' if p.dsr > DSR_MIN else 'NO'}")
    logger.info(f"     fold positivi {p.fold_positivi}/{N_FOLD}: "
                f"{'OK' if p.fold_positivi == N_FOLD else 'NO'}")
    logger.info(f"     concentrazione {p.quota_simbolo_top:.0%}: "
                f"{'OK' if p.quota_simbolo_top <= MAX_QUOTA_SIMBOLO else 'NO'}")
    logger.info(f"     bootstrap IC95 basso {p.get('ic95_basso', float('nan')):+.1f} "
                f"(> 0): {'OK' if ic_ok else 'NO'}  <- GATE PRIMARIO")
    logger.info("\n" + "=" * 96)
    if h7b:
        logger.info("H7b: SUPERA I CRITERI PRE-REGISTRATI (contro il prior dichiarato).")
        logger.info("L'apertura del lotto A resta un atto separato e deliberato.")
    else:
        logger.info("H7-nulla: nessuna promozione. L'holdout resta sigillato.")
        logger.info("Sulla dimensione feature restano: funding (asterisco aperto) e")
        logger.info("bookDepth 2023+ (3.5 anni). Ciascuno con pre-registro proprio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
