"""Timeframe — esegue docs/PRE_REGISTRO_TIMEFRAME.md

L'ultima dimensione mai misurata. La conclusione dei 120 tentativi e' scoped a
1h, e il lato lungo di quello scope era stato chiuso per INFERENZA: "meno barre
= meno potenza", vero a universo fisso, falso con 47 simboli. 4h x 47 da'
624.087 barre (1.6x tutto il dataset usato finora) con il doppio del margine
economico (25.3x contro 12.3x).

Il meccanismo: il costo e' FISSO a 0.14% per trade, il movimento cresce con
√tempo. Se il modello cattura una frazione costante del movimento, il timeframe
lungo converte meglio per pura aritmetica. E' la sola leva che cambia il COSTO
RELATIVO invece del segnale.

Prior di chi scrive, dichiarato nel pre-registro PRIMA del run: NEGATIVO.
Ricampionare non aggiunge informazione.

Decisione solo su 4h x 47. La config #5 (4h sui nostri 7) e' DESCRITTIVA e non
promuovibile.

Uso:  venv/bin/python scripts/timeframe_search.py
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
from src.research.resample import ORE_PER_BARRA, resample, soglia_scalata
from src.research.target_space import TargetSpec
from src.research.walkforward import run_walkforward
from src.shared.holdout import assert_research_allowed, record_trial

sys.path.insert(0, str(Path(__file__).parent))
from breadth_download import universo

FAMIGLIA = "timeframe_v1"
N_TRIALS_CUMULATIVI = 125          # 120 spesi + 5 nuovi
TF_PRIMARIO = "4h"                 # l'UNICO su cui si decide (regola nel pre-registro)
TIMEFRAMES = ("2h", "4h", "8h", "1d")
N_FOLD = 4
ORIZZONTE = 5                      # barre, per ogni timeframe
Q = 0.01
DSR_MIN = 0.90
MAX_QUOTA_SIMBOLO = 0.60

OUT = Path(__file__).parent.parent / "docs" / "timeframe_results.csv"


def _carica(univ, tf, collector):
    raw = {}
    for s in univ:
        try:
            d = collector.load_historical(s, timeframe="1h")
        except Exception:
            continue
        if d is None or d.empty:
            continue
        r = resample(d, tf)
        if len(r) > 500:
            raw[s] = r
    return raw


def _run(raw, tf, cfg, etichetta):
    comune = None
    for d in raw.values():
        comune = d.index if comune is None else comune.intersection(d.index)
    comune = comune.sort_values()
    start = comune.min() + (comune.max() - comune.min()) * 0.4
    sp = (comune.max() - start) / N_FOLD
    bounds = [start + sp * i for i in range(N_FOLD + 1)]

    # Soglia scalata con √tempo: tiene COSTANTE il tasso di eventi fra
    # timeframe. Regola derivata, non manopola.
    spec = TargetSpec(horizon=ORIZZONTE, thr_kind="fixed",
                      thr_val=soglia_scalata(tf), label="fixed_horizon")
    t0 = time.time()
    res = run_walkforward(spec, Q, raw, bounds, cfg)
    m = metriche(res["pnls"], res["trades"], N_TRIALS_CUMULATIVI) if res else None
    if m is None:
        record_trial(FAMIGLIA, {"tf": tf, "universo": etichetta}, {"esito": "scartata"})
        logger.info(f"{tf:>3s} x {etichetta:<9s}: scartata")
        return None

    boot = bootstrap_mensile(res["trades"])
    riga = {"tf": tf, "universo": etichetta, "n_simboli": len(raw),
            "soglia_target": round(spec.thr_val, 4),
            "anni": round((comune.max() - comune.min()).days / 365.25, 2),
            **m, **(boot or {})}
    record_trial(FAMIGLIA, {"tf": tf, "universo": etichetta,
                            "soglia": spec.thr_val}, riga)
    logger.info(
        f"{tf:>3s} x {etichetta:<9s}: PnL {m['pnl_totale']:+8.2f} | "
        f"trade {m['n_trade']:5d} | fold+ {m['fold_positivi']}/{N_FOLD} | "
        f"SR {m['sharpe_trade']:+.4f} | DSR {m['dsr']:5.1%} | "
        f"soglia {spec.thr_val:.2%} | top {m['simbolo_top'][:5]} "
        f"{m['quota_simbolo_top']:.0%}"
        + (f" | IC95 [{boot['ic95_basso']:+.0f}, {boot['ic95_alto']:+.0f}]" if boot else "")
        + f" | {time.time()-t0:.0f}s")
    return riga


def main():
    cfg = yaml.safe_load(open("config/trading_params.yaml"))
    univ = universo()
    assert_research_allowed(univ)
    nostri = cfg["symbols"]

    collector = DataCollector()
    logger.info(f"Universo {len(univ)} simboli | orizzonte {ORIZZONTE} barre | q={Q:.0%}")
    logger.info(f"Decisione solo su {TF_PRIMARIO} x 47 | n_trials cumulativi "
                f"{N_TRIALS_CUMULATIVI} | gate primario: bootstrap mensile")
    logger.info("Prior dichiarato nel pre-registro: NEGATIVO\n")

    righe = []
    for tf in TIMEFRAMES:
        raw = _carica(univ, tf, collector)
        r = _run(raw, tf, cfg, f"{len(raw)} simb")
        if r:
            righe.append(r)
            pd.DataFrame(righe).to_csv(OUT, index=False)

    # #5 — DESCRITTIVA: il passaggio a 4h aiuta il gruppo sovradattato?
    # Un suo successo NON e' un candidato.
    raw7 = _carica(nostri, TF_PRIMARIO, collector)
    r7 = _run(raw7, TF_PRIMARIO, cfg, "nostri 7")
    if r7:
        r7["nota"] = "DESCRITTIVA, non promuovibile"
        righe.append(r7)

    df = pd.DataFrame(righe)
    df.to_csv(OUT, index=False)

    # ---- H6b: la curva (descrittiva) ----
    logger.info("\n" + "=" * 100)
    logger.info("H6b — lo Sharpe/trade cresce col timeframe, seguendo mossa/costo?")
    logger.info("=" * 100)
    for _, r in df[df.universo != "nostri 7"].iterrows():
        logger.info(f"  {r.tf:>3s}: SR/trade {r.sharpe_trade:+.4f} | "
                    f"{r.n_trade:5.0f} trade | soglia {r.soglia_target:.2%}")

    # ---- H6a / H6c: decisione, SOLO sul primario ----
    prim = df[(df.tf == TF_PRIMARIO) & (df.universo != "nostri 7")]
    logger.info("\n" + "=" * 100)
    if prim.empty:
        logger.error(f"{TF_PRIMARIO} x 47 non disponibile: nessuna decisione.")
        return 1
    p = prim.iloc[0]

    logger.info(f"H6a — il costo relativo era il vincolo? Sharpe/trade "
                f"{p.sharpe_trade:+.4f} (serve > 0, a 1h era -0.0170)")
    logger.info(f"     -> {'CONFERMATA' if p.sharpe_trade > 0 else 'FALSIFICATA'}")
    if p.sharpe_trade <= 0:
        logger.info("     Il problema non era il costo: il segnale non c'e', e")
        logger.info("     nessuna aritmetica lo crea.")

    ic_ok = p.get("ic95_basso", 0) > 0
    h6c = (p.dsr > DSR_MIN and p.fold_positivi == N_FOLD
           and p.quota_simbolo_top <= MAX_QUOTA_SIMBOLO and ic_ok)
    logger.info(f"\nH6c — promuovibile?")
    logger.info(f"     DSR {p.dsr:.1%} (> {DSR_MIN:.0%}): {'OK' if p.dsr > DSR_MIN else 'NO'}")
    logger.info(f"     fold positivi {p.fold_positivi}/{N_FOLD}: "
                f"{'OK' if p.fold_positivi == N_FOLD else 'NO'}")
    logger.info(f"     concentrazione {p.quota_simbolo_top:.0%}: "
                f"{'OK' if p.quota_simbolo_top <= MAX_QUOTA_SIMBOLO else 'NO'}")
    logger.info(f"     bootstrap IC95 basso {p.get('ic95_basso', float('nan')):+.1f} "
                f"(> 0): {'OK' if ic_ok else 'NO'}  <- GATE PRIMARIO")

    logger.info("\n" + "=" * 100)
    if h6c:
        logger.info(f"H6c: {TF_PRIMARIO} x 47 SUPERA TUTTI I CRITERI PRE-REGISTRATI.")
        logger.info("Contro il prior dichiarato: vale di piu', non di meno.")
        logger.info("L'apertura del lotto A resta un atto separato e deliberato.")
    else:
        logger.info("H6-nulla: nessuna promozione. L'holdout resta sigillato.")
        logger.info("")
        logger.info("Target, feature, breadth e timeframe: tutti chiusi PER MISURA.")
        logger.info("La ricerca sui dati storici e' conclusa, e la frase perde ogni")
        logger.info("aggettivo: questi dati non contengono un edge dimostrabile con")
        logger.info("queste feature. Non 'a 1h', non 'con questo target'. Punto.")
        logger.info("")
        logger.info("E' un risultato, non una resa: e' cio' che sappiamo dopo 125")
        logger.info("tentativi contati, con un metodo costruito per impedirci di")
        logger.info("ingannarci. Il 2026-07-16 'avevamo trovato' +244, e non era vero.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
