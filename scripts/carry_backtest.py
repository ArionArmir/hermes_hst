"""Backtest del carry delta-neutro — esegue docs/PRE_REGISTRO_CARRY.md

Nessun ML: regole meccaniche dichiarate. Ribilanciamento ogni lunedi' 00:00
UTC, selezione per funding medio trailing (solo passato), equal-notional.
Le posizioni riselezionate non si toccano (niente costi).

Rendimento quotato SUL NOTIONAL: ledger mensile in unita' di notional,
normalizzato per il numero medio di posizioni aperte nel mese.

Uso:  venv/bin/python scripts/carry_backtest.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import yaml
from loguru import logger

from src.research.carry import (COSTO_APERTURA, COSTO_CHIUSURA, Posizione,
                                basis, funding_incassato_tra,
                                funding_medio_trailing, seleziona)
from src.research.evaluation import metriche  # non usato per il carry: metriche proprie
from src.shared.holdout import assert_research_allowed, deflated_sharpe_ratio, record_trial

sys.path.insert(0, str(Path(__file__).parent))
from breadth_download import universo

FAMIGLIA = "carry_v1"
N_TRIALS_FAMIGLIA = 4              # VINCOLANTE (opzione C, decisa con l'utente)
N_TRIALS_CUMULATIVI = 143          # riportato, non vincolante
ANNI_MINIMI, SIMBOLI_MINIMI = 5.0, 40
CONFIGS = [(30, "all-positive"), (30, "top-10"), (7, "all-positive"), (7, "top-10")]
PRIMARIA = (30, "all-positive")
# gate H8: IC95>0, DSR(4)>90%, netto>=3%, mesi+>=55%, concentrazione<=40%
NETTO_MINIMO, MESI_POS_MINIMI, CONC_MAX = 0.03, 0.55, 0.40

ROOT = Path(__file__).parent.parent
OUT = ROOT / "docs" / "carry_results.csv"


def carica(univ):
    dati = {}
    for s in univ:
        try:
            fund = pd.read_parquet(ROOT / "data" / "funding" / f"{s}_funding.parquet")
            perp = pd.read_parquet(ROOT / "data" / "historical" / f"{s}_1h.parquet")["close"]
            spot = pd.read_parquet(ROOT / "data" / "spot" / f"{s}_1h.parquet")
        except FileNotFoundError:
            continue                     # senza spot: escluso per regola
        spot = spot.set_index("timestamp")["close"]
        perp.index = perp.index.astype("datetime64[ns]")
        spot.index = spot.index.astype("datetime64[ns]")
        fund["calc_time"] = fund["calc_time"].astype("datetime64[ns]")
        comuni = perp.index.intersection(spot.index)
        b = ((perp.loc[comuni] - spot.loc[comuni]) / spot.loc[comuni]).sort_index()
        inizio = max(b.index.min(), fund.calc_time.min())
        fine = min(b.index.max(), fund.calc_time.max())
        if (fine - inizio).days / 365.25 >= ANNI_MINIMI:
            dati[s] = {"basis": b, "funding": fund.sort_values("calc_time")}
    return dati


def run(dati, W, regola, lunedis):
    posizioni: dict[str, Posizione] = {}
    ledger: dict[pd.Period, float] = {}
    aperte_per_mese: dict[pd.Period, list] = {}
    funding_per_simbolo: dict[str, float] = {}
    prev = None

    for t in lunedis:
        mese = t.to_period("M")
        # 1) accredita il funding maturato dall'ultimo ribilanciamento
        if prev is not None:
            for s, pos in posizioni.items():
                f = funding_incassato_tra(dati[s]["funding"], prev, t)
                ledger[mese] = ledger.get(mese, 0.0) + f
                funding_per_simbolo[s] = funding_per_simbolo.get(s, 0.0) + f
                pos.funding_incassato += f
        # 2) selezione con la sola informazione passata
        medie = {s: funding_medio_trailing(d["funding"], t, W) for s, d in dati.items()}
        scelti = seleziona(medie, regola)
        # 3) chiusure (basis realizzato + costo uscita)
        for s in list(posizioni):
            if s not in scelti:
                pos = posizioni.pop(s)
                b_out = dati[s]["basis"].asof(t)
                if not np.isnan(b_out):
                    ledger[mese] = (ledger.get(mese, 0.0)
                                    + pos.basis_entrata - b_out - COSTO_CHIUSURA)
        # 4) aperture (costo entrata)
        for s in scelti - set(posizioni):
            b_in = dati[s]["basis"].asof(t)
            if np.isnan(b_in):
                continue
            posizioni[s] = Posizione(s, t, float(b_in))
            ledger[mese] = ledger.get(mese, 0.0) - COSTO_APERTURA
        aperte_per_mese.setdefault(mese, []).append(len(posizioni))
        prev = t

    # chiusura finale di tutto
    mese = lunedis[-1].to_period("M")
    for s, pos in posizioni.items():
        b_out = dati[s]["basis"].asof(lunedis[-1])
        if not np.isnan(b_out):
            ledger[mese] = (ledger.get(mese, 0.0)
                            + pos.basis_entrata - b_out - COSTO_CHIUSURA)

    mesi = sorted(ledger)
    ret = np.array([ledger[m] / max(np.mean(aperte_per_mese.get(m, [1])), 1)
                    for m in mesi])
    return ret, funding_per_simbolo


def statistiche(ret, funding_per_simbolo):
    n = len(ret)
    ann = float(ret.mean() * 12)
    sd = ret.std(ddof=1)
    sharpe_ann = float(ret.mean() / sd * np.sqrt(12)) if sd > 0 else 0.0
    rng = np.random.default_rng(42)
    boot = rng.choice(ret, (10_000, n), replace=True).mean(axis=1) * 12
    incassi = {s: v for s, v in funding_per_simbolo.items() if v > 0}
    conc = max(incassi.values()) / sum(incassi.values()) if incassi else 1.0
    return {
        "mesi": n, "annualizzato_netto": round(ann, 4),
        "sharpe_ann": round(sharpe_ann, 2),
        "mesi_positivi": round(float((ret > 0).mean()), 3),
        "ic95_basso": round(float(np.percentile(boot, 2.5)), 4),
        "ic95_alto": round(float(np.percentile(boot, 97.5)), 4),
        "dsr_famiglia": round(float(deflated_sharpe_ratio(ret, N_TRIALS_FAMIGLIA)), 4),
        "dsr_cumulativo": round(float(deflated_sharpe_ratio(ret, N_TRIALS_CUMULATIVI)), 4),
        "concentrazione": round(float(conc), 3),
        "simbolo_top": max(incassi, key=incassi.get) if incassi else "",
    }


def main():
    univ = universo()
    assert_research_allowed(univ)
    dati = carica(univ)
    inizio = max(d["basis"].index.min() for d in dati.values())
    inizio = max(inizio, max(d["funding"].calc_time.min() for d in dati.values()))
    fine = min(min(d["basis"].index.max() for d in dati.values()),
               min(d["funding"].calc_time.max() for d in dati.values()))
    anni = (fine - inizio).days / 365.25
    logger.info(f"Simboli con funding+perp+spot >= {ANNI_MINIMI} anni: {len(dati)}")
    logger.info(f"Finestra comune: {inizio.date()} -> {fine.date()} = {anni:.2f} anni")
    if anni < ANNI_MINIMI or len(dati) < SIMBOLI_MINIMI:
        logger.error(f"❌ STOP: gate non superato ({anni:.2f} anni, {len(dati)} simboli).")
        return 1
    logger.info("✅ Gate superato\n")

    lunedis = pd.date_range(inizio.ceil("D"), fine, freq="W-MON")
    # il trailing W=30 richiede storia: primo ribilanciamento dopo 30 giorni
    lunedis = lunedis[lunedis >= inizio + pd.Timedelta(days=30)]

    righe = []
    for W, regola in CONFIGS:
        t0 = time.time()
        ret, fps = run(dati, W, regola, lunedis)
        st = statistiche(ret, fps)
        record_trial(FAMIGLIA, {"W": W, "regola": regola}, st)
        righe.append({"W": W, "regola": regola, **st})
        pd.DataFrame(righe).to_csv(OUT, index=False)
        tag = "  <- PRIMARIA" if (W, regola) == PRIMARIA else ""
        logger.info(f"W{W:<2d} {regola:12s}: ann {st['annualizzato_netto']:+7.2%} | "
                    f"Sharpe {st['sharpe_ann']:5.2f} | mesi+ {st['mesi_positivi']:.0%} | "
                    f"IC95 [{st['ic95_basso']:+.2%}, {st['ic95_alto']:+.2%}] | "
                    f"DSR4 {st['dsr_famiglia']:.1%} | conc {st['concentrazione']:.0%} "
                    f"({st['simbolo_top'][:6]}) | {time.time()-t0:.0f}s{tag}")

    # benchmark descrittivo, fuori budget: apri tutto al primo lunedi',
    # chiudi all'ultimo, nessuna selezione — la misura fondante come strategia
    bench_led = 0.0
    for s, d in dati.items():
        b_in, b_out = d["basis"].asof(lunedis[0]), d["basis"].asof(lunedis[-1])
        if np.isnan(b_in) or np.isnan(b_out):
            continue
        bench_led += (funding_incassato_tra(d["funding"], lunedis[0], lunedis[-1])
                      + b_in - b_out - COSTO_APERTURA - COSTO_CHIUSURA)
    bench_ann = bench_led / len(dati) / ((lunedis[-1] - lunedis[0]).days / 365.25)
    logger.info(f"\nbenchmark incondizionato (tieni tutto, {len(dati)} simboli): "
                f"{bench_ann:+.2%} annuo netto sul notional")

    # ---- H8: decisione, SOLO sulla primaria ----
    p = next(r for r in righe if (r["W"], r["regola"]) == PRIMARIA)
    checks = [
        ("bootstrap IC95 basso > 0", p["ic95_basso"] > 0),
        (f"DSR famiglia (N=4) > 90%", p["dsr_famiglia"] > 0.90),
        (f"netto >= {NETTO_MINIMO:.0%}", p["annualizzato_netto"] >= NETTO_MINIMO),
        (f"mesi positivi >= {MESI_POS_MINIMI:.0%}", p["mesi_positivi"] >= MESI_POS_MINIMI),
        (f"concentrazione <= {CONC_MAX:.0%}", p["concentrazione"] <= CONC_MAX),
    ]
    logger.info("\n" + "=" * 96)
    for nome, ok in checks:
        logger.info(f"  {nome:32s}: {'OK' if ok else 'NO'}")
    logger.info(f"  (DSR cumulativo N=143, riportato: {p['dsr_cumulativo']:.1%})")
    logger.info("=" * 96)
    if all(ok for _, ok in checks):
        logger.info("H8: il carry SUPERA i criteri pre-registrati.")
        logger.info("Validazione holdout ed eventuale go-live restano atti separati.")
    else:
        logger.info("H8-nulla: il carry non supera i criteri. Si riporta e basta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
