"""Fasi 2-3 dello studio medio termine — esegue PRE_REGISTRO_TREND.md e
PRE_REGISTRO_MOMENTUM.md.

Fase 2: regola di Faber (SMA mensile) su S&P 500 1985-2026 con contabilita'
italiana (26% a ogni uscita in utile, minusvalenze compensabili entro 4 anni
- generoso verso il timing, dichiarato; liquidita' al T-bill netto 12.5%;
0.1%/lato per switch; dividendi esclusi, anche questo a favore del timing).

Fase 3: momentum cross-sectional crypto - top-10 mensile per rendimento
trailing contro equal-weight di tutti i disponibili, long-only, appaiato.

Uso:  venv/bin/python scripts/trend_momentum_tests.py
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import requests
from loguru import logger

from src.invest.drawdown import load_asset_monthly
from src.shared.holdout import record_trial

ROOT = Path(__file__).parent.parent
TASSA_CG, TASSA_TBILL, COSTO_SWITCH = 0.26, 0.125, 0.001
COSTO_LATO_SPOT = 0.0015
rng = np.random.default_rng(42)


def tbill_mensile() -> pd.Series:
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3",
                     timeout=30)
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["data", "tasso"]
    df["data"] = pd.to_datetime(df["data"])
    df["tasso"] = pd.to_numeric(df["tasso"], errors="coerce") / 100
    ann = df.dropna().set_index("data")["tasso"].groupby(lambda t: t.to_period("M")).mean()
    return (1 + ann) ** (1 / 12) - 1


def ic_bootstrap(diff: np.ndarray) -> tuple[float, float]:
    boot = rng.choice(diff, (10_000, len(diff)), replace=True).mean(axis=1) * 12
    return float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))


# ------------------------------------------------------------- fase 2 -----

def faber(px: pd.Series, tb: pd.Series, sma_mesi: int) -> dict:
    ret = px.pct_change()
    sma = px.rolling(sma_mesi).mean()
    dentro = (px > sma).shift(1).fillna(False)      # decisione a fine mese t-1
    tb = tb.reindex(px.index).ffill().fillna(0.0)

    w, basis, minus_bank = 1.0, None, 0.0
    tasse = switch = 0.0
    serie_w, in_mkt = [], []
    stato = False
    for t in px.index[1:]:
        voglio = bool(dentro.loc[t])
        if voglio != stato:                          # switch a inizio mese
            w *= (1 - COSTO_SWITCH)
            switch += 1
            if stato:                                # uscita dall'azionario
                gain = w - basis
                if gain > 0:
                    imponibile = max(0.0, gain - minus_bank)
                    minus_bank = max(0.0, minus_bank - gain)
                    w -= TASSA_CG * imponibile
                    tasse += TASSA_CG * imponibile
                else:
                    minus_bank += -gain
            else:
                basis = w
            stato = voglio
        if stato:
            w *= 1 + ret.loc[t]
        else:
            w *= 1 + tb.loc[t] * (1 - TASSA_TBILL)
        serie_w.append(w)
        in_mkt.append(stato)
    # liquidazione finale (parita' di trattamento col buy&hold)
    if stato and w > basis:
        imponibile = max(0.0, (w - basis) - minus_bank)
        w -= TASSA_CG * imponibile
        tasse += TASSA_CG * imponibile
    sw = pd.Series(serie_w, index=px.index[1:])
    anni = len(sw) / 12
    return {"finale_netto": w, "cagr": w ** (1 / anni) - 1,
            "max_dd": float((sw / sw.cummax() - 1).min()),
            "tempo_investito": float(np.mean(in_mkt)), "switch": int(switch),
            "tasse_pagate": round(tasse, 4),
            "serie": sw}


def fase2():
    logger.info("=" * 78)
    logger.info("FASE 2 — Faber su S&P 500, contabilita' italiana")
    logger.info("=" * 78)
    px = load_asset_monthly("SPX", in_eur=False)
    tb = tbill_mensile()
    anni = len(px) / 12

    ret = px.pct_change().dropna()
    bh_lordo = float((1 + ret).prod())
    bh = bh_lordo - TASSA_CG * (bh_lordo - 1)
    bh_serie = (1 + ret).cumprod()
    bh_dd = float((bh_serie / bh_serie.cummax() - 1).min())
    logger.info(f"dati: {px.index[0]} -> {px.index[-1]} ({anni:.1f} anni)")
    logger.info(f"BUY&HOLD netto finale: x{bh:.1f} | CAGR {bh**(1/anni)-1:.2%} | "
                f"maxDD {bh_dd:.0%}\n")

    for sma in (10, 6, 12):
        r = faber(px, tb, sma)
        diff = (r["serie"].pct_change().dropna()
                - bh_serie.pct_change().reindex(r["serie"].index).dropna()).dropna().to_numpy()
        lo, hi = ic_bootstrap(diff)
        st = {k: (round(v, 4) if isinstance(v, float) else v)
              for k, v in r.items() if k != "serie"}
        st |= {"ic95_diff": [round(lo, 4), round(hi, 4)],
               "batte_bh": bool(r["finale_netto"] > bh and lo > 0)}
        record_trial("trend_v1", {"sma_mesi": sma}, st)
        tag = "  <- PRIMARIA" if sma == 10 else ""
        logger.info(f"SMA {sma:>2d}: finale x{r['finale_netto']:.1f} | "
                    f"CAGR {r['cagr']:.2%} | maxDD {r['max_dd']:.0%} | "
                    f"investito {r['tempo_investito']:.0%} | switch {r['switch']} | "
                    f"tasse x{r['tasse_pagate']:.2f} | "
                    f"IC95 diff [{lo:+.2%}, {hi:+.2%}]{tag}")
    logger.info("")
    return bh, bh_dd


# ------------------------------------------------------------- fase 3 -----

def fase3():
    logger.info("=" * 78)
    logger.info("FASE 3 — Momentum cross-sectional crypto (top-10 vs tutti)")
    logger.info("=" * 78)
    chiusure = {}
    for p in sorted((ROOT / "data" / "spot").glob("*_1h.parquet")):
        s = p.name.split("_1h")[0]
        px = pd.read_parquet(p).set_index("timestamp")["close"]
        chiusure[s] = px.groupby(px.index.to_period("M")).last()
    prezzi = pd.DataFrame(chiusure)
    logger.info(f"universo: {prezzi.shape[1]} simboli, "
                f"{prezzi.index[0]} -> {prezzi.index[-1]}\n")
    ret = prezzi.pct_change()

    for lb in (3, 1, 6):
        mesi, top_prec = [], set()
        for i in range(lb + 1, len(prezzi.index) - 1):
            t, t1 = prezzi.index[i], prezzi.index[i + 1]
            disponibili = [s for s in prezzi.columns
                           if prezzi[s].iloc[i - lb:i + 1].notna().all()
                           and pd.notna(ret[s].loc[t1])]
            if len(disponibili) < 15:
                continue
            mom = {s: prezzi[s].loc[t] / prezzi[s].iloc[i - lb] - 1 for s in disponibili}
            top = set(sorted(mom, key=mom.get, reverse=True)[:10])
            ricambio = len(top - top_prec) / 10 if top_prec else 1.0
            costo = ricambio * 2 * COSTO_LATO_SPOT
            r_top = float(np.mean([ret[s].loc[t1] for s in top])) - costo
            r_all = float(np.mean([ret[s].loc[t1] for s in disponibili]))
            mesi.append((t1, r_top, r_all, ricambio))
            top_prec = top
        m = pd.DataFrame(mesi, columns=["mese", "top10", "tutti", "ricambio"])
        diff = (m.top10 - m.tutti).to_numpy()
        lo, hi = ic_bootstrap(diff)
        st = {"mesi": len(m),
              "ann_top10": round(float(m.top10.mean() * 12), 4),
              "ann_tutti": round(float(m.tutti.mean() * 12), 4),
              "diff_ann": round(float(diff.mean() * 12), 4),
              "ic95_diff": [round(lo, 4), round(hi, 4)],
              "ricambio_medio": round(float(m.ricambio.mean()), 3),
              "batte_benchmark": bool(diff.mean() > 0 and lo > 0)}
        record_trial("momentum_crypto_v1", {"lookback_mesi": lb}, st)
        tag = "  <- PRIMARIA" if lb == 3 else ""
        logger.info(f"lookback {lb}m: top10 {st['ann_top10']:+.1%}/anno | "
                    f"tutti {st['ann_tutti']:+.1%} | diff {st['diff_ann']:+.1%} | "
                    f"IC95 [{lo:+.1%}, {hi:+.1%}] | ricambio {st['ricambio_medio']:.0%}"
                    f"{tag}")
    logger.info("")


if __name__ == "__main__":
    fase2()
    fase3()
