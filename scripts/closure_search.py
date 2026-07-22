"""Chiusura feature: funding e bookDepth — esegue docs/PRE_REGISTRO_CHIUSURA_FEATURE.md

Due chiusure appaiate e autonome, ciascuna coi propri baseline sulla propria
finestra (funding ~5.5 anni, bookDepth ~3.55). Disegno identico al
positioning; prior dichiarato H-nulla per entrambe.

Uso:  venv/bin/python scripts/closure_search.py funding
      venv/bin/python scripts/closure_search.py bookdepth
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import yaml
from loguru import logger

from src.data_collector import DataCollector
from src.research.closure import (attach_bookdepth, attach_funding,
                                  compute_features_with_bookdepth,
                                  compute_features_with_funding,
                                  load_bookdepth, load_funding)
from src.research.evaluation import bootstrap_mensile, metriche
from src.research.target_space import TargetSpec
from src.research.walkforward import run_walkforward
from src.shared.holdout import assert_research_allowed, record_trial

sys.path.insert(0, str(Path(__file__).parent))
from breadth_download import universo

N_TRIALS_CUMULATIVI = 139          # 131 a registro + 8 di questa serie
N_FOLD = 4
Q = 0.01
ORIZZONTI = (10, 5)
DSR_MIN = 0.90
MAX_QUOTA_SIMBOLO = 0.60

FAMIGLIE = {
    "funding": dict(famiglia="funding_v1", load=load_funding,
                    attach=attach_funding, ffn=compute_features_with_funding,
                    col_gate="funding_last", anni_minimi=5.0,
                    braccio="funding_20"),
    "bookdepth": dict(famiglia="bookdepth_v1", load=load_bookdepth,
                      attach=attach_bookdepth, ffn=compute_features_with_bookdepth,
                      col_gate="bid_1pct", anni_minimi=3.0,
                      braccio="bookdepth_20"),
}


def main(quale: str):
    F = FAMIGLIE[quale]
    cfg = yaml.safe_load(open("config/trading_params.yaml"))
    univ = universo()
    assert_research_allowed(univ)

    collector = DataCollector()
    raw = {}
    for s in univ:
        try:
            candele = collector.load_historical(s, timeframe="1h")
            extra = F["load"](s)
        except Exception as e:
            logger.error(f"  {s}: {e} — il pre-registro richiede TUTTI i 47")
            return 1
        raw[s] = F["attach"](candele, extra)

    comune = None
    for d in raw.values():
        ok = d[d[F["col_gate"]].notna()].index
        comune = ok if comune is None else comune.intersection(ok)
    comune = comune.sort_values()
    anni = (comune.max() - comune.min()).days / 365.25
    logger.info(f"[{quale}] intersezione: {len(comune):,} barre "
                f"{comune.min().date()} -> {comune.max().date()} = {anni:.2f} anni")
    if anni < F["anni_minimi"]:
        logger.error(f"❌ STOP: {anni:.2f} < {F['anni_minimi']} anni.")
        return 1
    logger.info("✅ Gate superato\n")

    # I baseline girano sulla finestra RISTRETTA ai dati extra validi,
    # altrimenti i due bracci vedrebbero periodi diversi e il confronto non
    # sarebbe appaiato
    raw = {s: d.loc[d.index.isin(comune)] for s, d in raw.items()}

    start = comune.min() + (comune.max() - comune.min()) * 0.4
    sp = (comune.max() - start) / N_FOLD
    bounds = [start + sp * i for i in range(N_FOLD + 1)]

    righe = []
    for h in ORIZZONTI:
        spec = TargetSpec(horizon=h, thr_kind="fixed", thr_val=0.005,
                          label="fixed_horizon")
        for braccio, ffn in (("baseline_18", None), (F["braccio"], F["ffn"])):
            t0 = time.time()
            res = run_walkforward(spec, Q, raw, bounds, cfg, feature_fn=ffn)
            m = metriche(res["pnls"], res["trades"], N_TRIALS_CUMULATIVI) if res else None
            base = {"horizon": h, "braccio": braccio}
            if m is None:
                record_trial(F["famiglia"], base, {"esito": "scartata"})
                logger.info(f"h{h} {braccio}: scartata")
                continue
            boot = bootstrap_mensile(res["trades"])
            record_trial(F["famiglia"], base, {**m, **(boot or {})})
            righe.append({**base, **m, **(boot or {})})
            pd.DataFrame(righe).to_csv(
                Path("docs") / f"{F['famiglia']}_results.csv", index=False)
            logger.info(
                f"h{h:<2d} {braccio:14s}: PnL {m['pnl_totale']:+8.2f} | "
                f"trade {m['n_trade']:5d} | fold+ {m['fold_positivi']}/{N_FOLD} | "
                f"SR {m['sharpe_trade']:+.4f} | DSR {m['dsr']:5.1%}"
                + (f" | IC95 [{boot['ic95_basso']:+.0f}, {boot['ic95_alto']:+.0f}]"
                   if boot else "") + f" | {time.time()-t0:.0f}s")

    df = pd.DataFrame(righe)
    logger.info("\n" + "=" * 96)
    vinte = 0
    for h in ORIZZONTI:
        b = df[(df.horizon == h) & (df.braccio == "baseline_18")]
        p = df[(df.horizon == h) & (df.braccio == F["braccio"])]
        if b.empty or p.empty:
            continue
        win = p.iloc[0].sharpe_trade > b.iloc[0].sharpe_trade
        vinte += bool(win)
        logger.info(f"  h{h:<2d}: baseline SR {b.iloc[0].sharpe_trade:+.4f} | "
                    f"{quale} SR {p.iloc[0].sharpe_trade:+.4f} -> "
                    f"{quale.upper() if win else 'BASELINE'}")
    logger.info(f"  {quale} vince {vinte}/2 -> appaiato "
                f"{'INDICATIVO' if vinte == 2 else 'FALSIFICATO'}")

    prim = df[(df.horizon == 10) & (df.braccio == F["braccio"])]
    if prim.empty:
        logger.error("Primario mancante.")
        return 1
    p = prim.iloc[0]
    ic_ok = p.get("ic95_basso", 0) > 0
    promosso = (p.dsr > DSR_MIN and p.fold_positivi == N_FOLD
                and p.quota_simbolo_top <= MAX_QUOTA_SIMBOLO and ic_ok)
    logger.info(f"\n  primario h10+{quale}: DSR {p.dsr:.1%} | fold+ "
                f"{p.fold_positivi}/{N_FOLD} | conc. {p.quota_simbolo_top:.0%} | "
                f"IC95 basso {p.get('ic95_basso', float('nan')):+.1f} <- GATE PRIMARIO")
    logger.info("=" * 96)
    if promosso:
        logger.info(f"[{quale}] SUPERA I CRITERI (contro il prior). Apertura lotto A "
                    "resta un atto separato e deliberato.")
    else:
        logger.info(f"[{quale}] H-nulla: nessuna promozione. Holdout sigillato.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in FAMIGLIE:
        print("uso: closure_search.py funding|bookdepth")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
