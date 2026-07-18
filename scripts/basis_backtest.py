"""Basis trimestrale — esegue docs/PRE_REGISTRO_BASIS.md

Scarica le klines dei contratti delivery BTCUSDT_*/ETHUSDT_* (piccoli: ogni
contratto vive ~6 mesi), poi backtest a regole dichiarate. Rendimento del
trimestre = basis bloccato all'entrata − costi: la convergenza a scadenza e'
contrattuale, non predetta.

Uso:  venv/bin/python scripts/basis_backtest.py
"""
import io
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import requests
from loguru import logger

from src.shared.holdout import deflated_sharpe_ratio, record_trial

sys.path.insert(0, str(Path(__file__).parent))
from positioning_download import _list_keys

FAMIGLIA = "basis_v1"
N_TRIALS_FAMIGLIA = 2
N_TRIALS_CUMULATIVI = 145
SOTTOSTANTI = ("BTCUSDT", "ETHUSDT")
TRIMESTRI_MINIMI = 20
COSTO_ENTRATA_FUT = 0.0007          # taker 0.05% + slippage 0.02%
COSTO_REGOLAMENTO = 0.0005          # prudenziale
NETTO_MINIMO, TRIM_POS_MINIMI, CONC_MAX = 0.03, 0.55, 0.70

ROOT = Path(__file__).parent.parent
BASE = "https://data.binance.vision/"
OUT_DIR = ROOT / "data" / "delivery"
OUT = ROOT / "docs" / "basis_results.csv"


def _fetch(key: str) -> pd.DataFrame | None:
    for _ in range(3):
        try:
            r = requests.get(BASE + key, timeout=30)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]), header=None,
                                 usecols=[0, 4], names=["open_time", "close"])
            df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
            df["open_time"] = df["open_time"].astype("int64")
            unit = "us" if df["open_time"].iloc[0] > 10**14 else "ms"
            df["timestamp"] = pd.to_datetime(df["open_time"], unit=unit)
            df["close"] = df["close"].astype(float)
            return df[["timestamp", "close"]]
        except Exception:
            pass
    return None


def scarica_contratti(sottostante: str) -> dict[pd.Timestamp, pd.Series]:
    """{scadenza: serie dei close 1h} per ogni contratto trimestrale."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prefissi = _list_keys(f"data/futures/um/monthly/klines/{sottostante}_")
    per_contratto: dict[str, list[str]] = {}
    for k in prefissi:
        m = re.search(rf"{sottostante}_(\d{{6}})/1h/", k)
        if m:
            per_contratto.setdefault(m.group(1), []).append(k)

    out = {}
    for suffisso, keys in sorted(per_contratto.items()):
        path = OUT_DIR / f"{sottostante}_{suffisso}_1h.parquet"
        if path.exists():
            df = pd.read_parquet(path)
        else:
            pezzi = []
            with ThreadPoolExecutor(max_workers=8) as pool:
                for f in as_completed({pool.submit(_fetch, k) for k in keys}):
                    if f.result() is not None:
                        pezzi.append(f.result())
            if not pezzi:
                continue
            df = (pd.concat(pezzi, ignore_index=True)
                    .drop_duplicates("timestamp").sort_values("timestamp")
                    .reset_index(drop=True))
            df.to_parquet(path)
        scadenza = pd.to_datetime(f"20{suffisso} 08:00", format="%Y%m%d %H:%M")
        out[scadenza] = df.set_index("timestamp")["close"]
    return out


def rendimenti_trimestrali(contratti, spot: pd.Series, regola: str):
    """Serie (entrata, rendimento_netto, basis_ann_entrata) per trimestre."""
    scadenze = sorted(contratti)
    righe = []
    for prec, succ in zip(scadenze, scadenze[1:]):
        serie = contratti[succ]
        f_in = serie.asof(prec)
        s_in = spot.asof(prec)
        if np.isnan(f_in) or np.isnan(s_in):
            continue
        giorni = (succ - prec).days
        basis = (f_in - s_in) / s_in
        basis_ann = basis * 365 / giorni
        if regola == "positive-only" and basis_ann <= 0:
            righe.append((prec, 0.0, basis_ann, giorni))     # cash quel trimestre
            continue
        netto = basis - COSTO_ENTRATA_FUT - COSTO_REGOLAMENTO
        righe.append((prec, netto, basis_ann, giorni))
    return righe


def tbill_medio(inizio, fine) -> float | None:
    """T-bill 3 mesi medio del periodo, da FRED (csv pubblico). Riga zero."""
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3",
                         timeout=30)
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["data", "tasso"]
        df["data"] = pd.to_datetime(df["data"])
        df["tasso"] = pd.to_numeric(df["tasso"], errors="coerce")
        m = (df["data"] >= inizio) & (df["data"] <= fine)
        return float(df.loc[m, "tasso"].mean()) / 100
    except Exception as e:
        logger.warning(f"T-bill non recuperato ({e}): riga zero non disponibile")
        return None


def main():
    spot = {s: pd.read_parquet(ROOT / "data" / "spot" / f"{s}_1h.parquet")
              .set_index("timestamp")["close"] for s in SOTTOSTANTI}
    contratti = {s: scarica_contratti(s) for s in SOTTOSTANTI}
    for s in SOTTOSTANTI:
        chiusi = sum(1 for sc in contratti[s] if sc < pd.Timestamp.now())
        logger.info(f"{s}: {len(contratti[s])} contratti, {chiusi} scaduti")
        if chiusi - 1 < TRIMESTRI_MINIMI:      # -1: il primo fa solo da ancora
            logger.error(f"❌ STOP: {s} ha {chiusi-1} trimestri utilizzabili "
                         f"(minimo {TRIMESTRI_MINIMI}).")
            return 1
    logger.info("✅ Gate superato\n")

    righe_csv = []
    for regola in ("always-roll", "positive-only"):
        per_sott = {s: rendimenti_trimestrali(contratti[s], spot[s], regola)
                    for s in SOTTOSTANTI}
        # portafoglio 50/50 sui trimestri comuni (stesso calendario di scadenze)
        date = sorted(set(t for s in SOTTOSTANTI for t, *_ in per_sott[s])
                      & set(t for t, *_ in per_sott[SOTTOSTANTI[1]]))
        rows = []
        for t in date:
            r = [next(x for x in per_sott[s] if x[0] == t) for s in SOTTOSTANTI]
            if any(x[0] != t for x in r):
                continue
            rows.append((t, np.mean([x[1] for x in r]),
                         {s: x[1] for s, x in zip(SOTTOSTANTI, r)},
                         np.mean([x[3] for x in r])))
        ret = np.array([x[1] for x in rows])
        giorni_medi = np.mean([x[3] for x in rows])
        ann = float(ret.mean() * 365 / giorni_medi)
        sd = ret.std(ddof=1)
        rng = np.random.default_rng(42)
        boot = rng.choice(ret, (10_000, len(ret)), replace=True).mean(axis=1) * 365 / giorni_medi
        contrib = {s: sum(x[2][s] for x in rows) for s in SOTTOSTANTI}
        tot_pos = sum(v for v in contrib.values() if v > 0)
        conc = max(contrib.values()) / tot_pos if tot_pos > 0 else 1.0
        st = {"regola": regola, "trimestri": len(ret),
              "annualizzato_netto": round(ann, 4),
              "trimestri_positivi": round(float((ret > 0).mean()), 3),
              "ic95_basso": round(float(np.percentile(boot, 2.5)), 4),
              "ic95_alto": round(float(np.percentile(boot, 97.5)), 4),
              "dsr_famiglia": round(float(deflated_sharpe_ratio(ret, N_TRIALS_FAMIGLIA)), 4),
              "dsr_cumulativo": round(float(deflated_sharpe_ratio(ret, N_TRIALS_CUMULATIVI)), 4),
              "concentrazione": round(float(conc), 3)}
        record_trial(FAMIGLIA, {"regola": regola}, st)
        righe_csv.append(st)
        pd.DataFrame(righe_csv).to_csv(OUT, index=False)
        logger.info(f"{regola:14s}: ann {ann:+7.2%} | trimestri {len(ret)} | "
                    f"trim+ {st['trimestri_positivi']:.0%} | "
                    f"IC95 [{st['ic95_basso']:+.2%}, {st['ic95_alto']:+.2%}] | "
                    f"DSR2 {st['dsr_famiglia']:.1%} | conc {conc:.0%}")
        if regola == "always-roll":
            primaria, rows_primaria = st, rows

    # spaccato annuale della primaria (obbligatorio: lezione del carry)
    s_ann = pd.Series([x[1] for x in rows_primaria],
                      index=[x[0] for x in rows_primaria])
    logger.info("\nSpaccato annuale (primaria, somma dei trimestri):")
    for anno, r in s_ann.groupby(s_ann.index.year):
        logger.info(f"  {anno}: {r.sum():+7.2%} ({len(r)} trimestri)")

    tb = tbill_medio(rows_primaria[0][0], rows_primaria[-1][0])
    if tb is not None:
        logger.info(f"\nRiga zero — T-bill 3M medio del periodo: {tb:+.2%}")
        logger.info(f"  eccesso della primaria sul T-bill: "
                    f"{primaria['annualizzato_netto'] - tb:+.2%}")

    checks = [
        ("bootstrap IC95 basso > 0", primaria["ic95_basso"] > 0),
        ("DSR famiglia (N=2) > 90%", primaria["dsr_famiglia"] > 0.90),
        (f"netto >= {NETTO_MINIMO:.0%}", primaria["annualizzato_netto"] >= NETTO_MINIMO),
        (f"trimestri+ >= {TRIM_POS_MINIMI:.0%}", primaria["trimestri_positivi"] >= TRIM_POS_MINIMI),
        (f"concentrazione <= {CONC_MAX:.0%}", primaria["concentrazione"] <= CONC_MAX),
    ]
    logger.info("\n" + "=" * 88)
    for nome, ok in checks:
        logger.info(f"  {nome:30s}: {'OK' if ok else 'NO'}")
    logger.info(f"  (DSR cumulativo N=145: {primaria['dsr_cumulativo']:.1%})")
    logger.info("=" * 88)
    logger.info("H9: il basis SUPERA i criteri." if all(ok for _, ok in checks)
                else "H9-nulla: il basis non supera i criteri. Si riporta e basta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
