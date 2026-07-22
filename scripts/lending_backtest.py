"""Lending stablecoin — esegue docs/PRE_REGISTRO_LENDING.md

Depositare su Aave v3 e incassare il supply rate: zero parametri, zero
timing. Il criterio primario e' l'ECCESSO mensile sul T-bill 3M: un lending
che pareggia il tasso privo di rischio portandosi dietro exploit e depeg e'
un gioco perso per definizione.

Uso:  venv/bin/python scripts/lending_backtest.py
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import requests
from loguru import logger

from src.shared.holdout import deflated_sharpe_ratio, record_trial

FAMIGLIA = "lending_v1"
N_TRIALS_FAMIGLIA = 2
N_TRIALS_CUMULATIVI = 147
ANNI_MINIMI = 3.0
ECCESSO_MINIMO, MESI_POS_MINIMI = 0.02, 0.55

POOLS = {  # verificati su DefiLlama il 2026-07-18
    "USDT": "f981a304-bb6c-45b8-b0c5-fd2f515ad23a",   # PRIMARIA (TVL maggiore)
    "USDC": "aa70268e-4b52-42bf-a116-608b370f9501",
}
PRIMARIA = "USDT"
OUT = Path(__file__).parent.parent / "docs" / "lending_results.csv"


def rendimenti_mensili(pool_id: str) -> pd.Series:
    r = requests.get(f"https://yields.llama.fi/chart/{pool_id}", timeout=60)
    r.raise_for_status()
    d = pd.DataFrame(r.json()["data"])
    d["data"] = pd.to_datetime(d["timestamp"]).dt.tz_localize(None)
    d["apy"] = pd.to_numeric(d["apy"], errors="coerce") / 100
    d = d.dropna(subset=["apy"]).set_index("data").sort_index()
    # APY giornaliero -> tasso giornaliero -> capitalizzazione mensile
    tasso_g = (1 + d["apy"]) ** (1 / 365) - 1
    return (1 + tasso_g).groupby(tasso_g.index.to_period("M")).prod() - 1


def tbill_mensile() -> pd.Series:
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3",
                     timeout=30)
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["data", "tasso"]
    df["data"] = pd.to_datetime(df["data"])
    df["tasso"] = pd.to_numeric(df["tasso"], errors="coerce") / 100
    df = df.dropna().set_index("data")
    ann = df["tasso"].groupby(df.index.to_period("M")).mean()
    return (1 + ann) ** (1 / 12) - 1          # mensilizzato


def main():
    tb = tbill_mensile()
    righe = []
    for asset, pool in POOLS.items():
        ret = rendimenti_mensili(pool)
        # mesi parziali ai bordi esclusi: capitalizzano meno di un mese
        ret = ret.iloc[1:-1]
        anni = len(ret) / 12
        eccesso = (ret - tb.reindex(ret.index)).dropna()
        if anni < ANNI_MINIMI:
            logger.error(f"❌ STOP: {asset} ha {anni:.2f} anni (< {ANNI_MINIMI}).")
            return 1
        e = eccesso.to_numpy()
        ann_ecc = float(e.mean() * 12)
        ann_lordo = float(ret.mean() * 12)
        rng = np.random.default_rng(42)
        boot = rng.choice(e, (10_000, len(e)), replace=True).mean(axis=1) * 12
        st = {"asset": asset, "mesi": len(e), "anni": round(anni, 2),
              "rendimento_ann": round(ann_lordo, 4),
              "eccesso_ann": round(ann_ecc, 4),
              "mesi_eccesso_pos": round(float((e > 0).mean()), 3),
              "ic95_basso": round(float(np.percentile(boot, 2.5)), 4),
              "ic95_alto": round(float(np.percentile(boot, 97.5)), 4),
              "dsr_famiglia": round(float(deflated_sharpe_ratio(e, N_TRIALS_FAMIGLIA)), 4),
              "dsr_cumulativo": round(float(deflated_sharpe_ratio(e, N_TRIALS_CUMULATIVI)), 4)}
        record_trial(FAMIGLIA, {"asset": asset}, st)
        righe.append(st)
        logger.info(f"{asset}: {anni:.1f} anni | lordo {ann_lordo:+.2%} | "
                    f"ECCESSO {ann_ecc:+.2%} | mesi+ {st['mesi_eccesso_pos']:.0%} | "
                    f"IC95 [{st['ic95_basso']:+.2%}, {st['ic95_alto']:+.2%}] | "
                    f"DSR2 {st['dsr_famiglia']:.1%}")
        if asset == PRIMARIA:
            primaria, ecc_primaria = st, eccesso

    pd.DataFrame(righe).to_csv(OUT, index=False)

    logger.info("\nSpaccato annuale dell'eccesso (primaria):")
    s = ecc_primaria
    for anno, r in s.groupby(s.index.year):
        logger.info(f"  {anno}: {r.sum():+7.2%} ({len(r)} mesi)")

    checks = [
        ("bootstrap IC95 eccesso > 0", primaria["ic95_basso"] > 0),
        ("DSR famiglia (N=2) > 90%", primaria["dsr_famiglia"] > 0.90),
        (f"eccesso >= {ECCESSO_MINIMO:.0%}", primaria["eccesso_ann"] >= ECCESSO_MINIMO),
        (f"mesi+ >= {MESI_POS_MINIMI:.0%}", primaria["mesi_eccesso_pos"] >= MESI_POS_MINIMI),
    ]
    logger.info("\n" + "=" * 88)
    for nome, ok in checks:
        logger.info(f"  {nome:30s}: {'OK' if ok else 'NO'}")
    logger.info(f"  (DSR cumulativo N=147: {primaria['dsr_cumulativo']:.1%})")
    logger.info("=" * 88)
    logger.info("H10: il lending SUPERA i criteri." if all(ok for _, ok in checks)
                else "H10-nulla: il lending non supera i criteri.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
