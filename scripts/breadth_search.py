"""Ampiezza dell'universo — esegue docs/PRE_REGISTRO_BREADTH.md

Non ci manca un segnale migliore: ci mancano occasioni per usarlo. La migliore
configurazione mai osservata (h10/q=1%/orizzonte fisso) ha Sharpe/trade 0.1190
e non e' promuovibile solo perche' 510 trade non bastano. Servono 1.344 trade:
~18 simboli invece di 7.

La configurazione di trading e' CONGELATA: qui varia solo l'ampiezza
dell'universo. Piu' simboli aggiungono dati, non tentativi.

Decisione di promozione solo su U47, dichiarata nel pre-registro. U7/U17/U27/
U37 leggono la curva di scala e NON sono candidati.

Il gate primario e' il BOOTSTRAP mensile, non il DSR: con 47 cripto correlate
i trade non sono indipendenti e il DSR - che li assume iid - sovrastima. Se i
due dissentono, vince il bootstrap.

Uso:  venv/bin/python scripts/breadth_search.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import yaml
from loguru import logger

from src.data_collector import DataCollector
from src.research.evaluation import bootstrap_mensile, metriche
from src.research.target_space import TargetSpec
from src.research.walkforward import run_walkforward
from src.shared.holdout import assert_research_allowed, record_trial

sys.path.insert(0, str(Path(__file__).parent))
from breadth_download import universo

FAMIGLIA = "breadth_v1"
N_TRIALS_CUMULATIVI = 120          # 115 spesi + 5 nuovi
SEME = 42                          # dichiarato nel pre-registro
TAGLIE = (7, 17, 27, 37, 47)
TAGLIA_PRIMARIA = 47               # l'UNICA su cui si decide
N_FOLD = 4
TRADE_RICHIESTI = 1344             # per DSR>90% a Sharpe/trade 0.1190
DSR_MIN = 0.90
MAX_QUOTA_SIMBOLO = 0.60

# Configurazione CONGELATA: la migliore mai osservata. Nulla da tarare.
SPEC = TargetSpec(horizon=10, thr_kind="fixed", thr_val=0.005, label="fixed_horizon")
Q = 0.01

OUT = Path(__file__).parent.parent / "docs" / "breadth_results.csv"


def main():
    cfg = yaml.safe_load(open("config/trading_params.yaml"))

    univ = universo()
    assert_research_allowed(univ)          # nessun sigillato puo' entrare qui

    collector = DataCollector()
    raw_all = {}
    for s in univ:
        try:
            d = collector.load_historical(s, timeframe="1h")
            if d is not None and not d.empty:
                raw_all[s] = d
        except Exception as e:
            logger.warning(f"  {s}: illeggibile ({e}), escluso")

    ordine = sorted(raw_all)
    rng = np.random.default_rng(SEME)
    rng.shuffle(ordine)                    # sottoinsiemi annidati, seme dichiarato

    logger.info(f"Universo: {len(ordine)} simboli | config congelata {SPEC.name} q={Q:.0%}")
    logger.info(f"Decisione solo su U{TAGLIA_PRIMARIA} | n_trials cumulativi "
                f"{N_TRIALS_CUMULATIVI} | gate primario: bootstrap mensile\n")

    righe = []
    for n in TAGLIE:
        if n > len(ordine):
            logger.warning(f"U{n}: universo insufficiente ({len(ordine)}), saltata")
            continue
        simboli = sorted(ordine[:n])
        raw = {s: raw_all[s] for s in simboli}

        comune = None
        for d in raw.values():
            comune = d.index if comune is None else comune.intersection(d.index)
        comune = comune.sort_values()
        start = comune.min() + (comune.max() - comune.min()) * 0.4
        sp = (comune.max() - start) / N_FOLD
        bounds = [start + sp * i for i in range(N_FOLD + 1)]

        t0 = time.time()
        res = run_walkforward(SPEC, Q, raw, bounds, cfg)
        m = metriche(res["pnls"], res["trades"], N_TRIALS_CUMULATIVI) if res else None
        if m is None:
            record_trial(FAMIGLIA, {"universo": n}, {"esito": "scartata"})
            logger.info(f"U{n:<2d}: scartata")
            continue

        boot = bootstrap_mensile(res["trades"])
        riga = {"universo": n, "anni": round((comune.max() - comune.min()).days / 365.25, 2),
                **m, **(boot or {})}
        record_trial(FAMIGLIA, {"universo": n, "simboli": simboli}, riga)
        righe.append(riga)
        pd.DataFrame(righe).to_csv(OUT, index=False)

        tag = "  <- PRIMARIA" if n == TAGLIA_PRIMARIA else ""
        logger.info(
            f"U{n:<2d}: PnL {m['pnl_totale']:+8.2f} | trade {m['n_trade']:5d} | "
            f"fold+ {m['fold_positivi']}/{N_FOLD} | SR {m['sharpe_trade']:+.4f} | "
            f"DSR {m['dsr']:5.1%} | top {m['simbolo_top'][:5]} {m['quota_simbolo_top']:.0%}"
            + (f" | IC95 [{boot['ic95_basso']:+.0f}, {boot['ic95_alto']:+.0f}]" if boot else "")
            + f" | {time.time()-t0:.0f}s{tag}")

    df = pd.DataFrame(righe)
    df.to_csv(OUT, index=False)

    # ---- H5b: la curva di scala (descrittiva, NON promozione) ----
    logger.info("\n" + "=" * 96)
    logger.info("H5b — lo Sharpe totale cresce come √breadth? (descrittivo)")
    logger.info("=" * 96)
    base = df[df.universo == TAGLIE[0]]
    if not base.empty:
        b = base.iloc[0]
        for _, r in df.iterrows():
            atteso = b.sharpe_trade * np.sqrt(r.n_trade / b.n_trade) if b.n_trade else 0
            logger.info(f"  U{r.universo:<2.0f}: {r.n_trade:5.0f} trade | "
                        f"SR/trade {r.sharpe_trade:+.4f} | "
                        f"sr·√n = {r.sharpe_trade*np.sqrt(r.n_trade):5.2f} "
                        f"(serve > 4.36)")

    # ---- H5a / H5c: decisione, SOLO sulla taglia primaria ----
    logger.info("\n" + "=" * 96)
    prim = df[df.universo == TAGLIA_PRIMARIA]
    if prim.empty:
        logger.error(f"U{TAGLIA_PRIMARIA} non disponibile: nessuna decisione.")
        return 1
    p = prim.iloc[0]

    h5a = p.sharpe_trade > 0 and p.n_trade >= TRADE_RICHIESTI
    logger.info(f"H5a — l'edge generalizza? Sharpe/trade {p.sharpe_trade:+.4f} "
                f"(serve > 0) | {p.n_trade:.0f} trade (servono {TRADE_RICHIESTI})")
    logger.info(f"     -> {'CONFERMATA' if h5a else 'FALSIFICATA'}")

    ic_esclude_zero = p.get("ic95_basso", 0) > 0
    h5c = (p.dsr > DSR_MIN and p.fold_positivi == N_FOLD
           and p.quota_simbolo_top <= MAX_QUOTA_SIMBOLO and ic_esclude_zero)
    logger.info(f"\nH5c — promuovibile?")
    logger.info(f"     DSR {p.dsr:.1%} (serve > {DSR_MIN:.0%}): "
                f"{'OK' if p.dsr > DSR_MIN else 'NO'}")
    logger.info(f"     fold positivi {p.fold_positivi}/{N_FOLD}: "
                f"{'OK' if p.fold_positivi == N_FOLD else 'NO'}")
    logger.info(f"     concentrazione {p.quota_simbolo_top:.0%} (max {MAX_QUOTA_SIMBOLO:.0%}): "
                f"{'OK' if p.quota_simbolo_top <= MAX_QUOTA_SIMBOLO else 'NO'}")
    logger.info(f"     bootstrap IC95 basso {p.get('ic95_basso', float('nan')):+.1f} "
                f"(serve > 0): {'OK' if ic_esclude_zero else 'NO'}  <- GATE PRIMARIO")

    logger.info("\n" + "=" * 96)
    if h5c:
        logger.info(f"H5c: U{TAGLIA_PRIMARIA} SUPERA TUTTI I CRITERI PRE-REGISTRATI.")
        logger.info("L'apertura del lotto A resta un atto separato e deliberato:")
        logger.info("open_seal('A', ipotesi, n_trials=120, motivazione=...), a mente fresca.")
    else:
        logger.info("H5-nulla: nessuna promozione. L'holdout resta sigillato.")
        if not h5a:
            logger.info("L'edge NON generalizza oltre i 7 simboli. Con target, feature e")
            logger.info("timeframe gia' chiusi, le ipotesi accessibili sono esaurite:")
            logger.info("questi dati non contengono un edge dimostrabile a 1h con queste")
            logger.info("feature. E' un risultato valido, e va riportato come tale.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
