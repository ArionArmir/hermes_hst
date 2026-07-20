"""Fase 0 dello studio firma liquidazioni: l'aggancio etichette ↔ nastro.

docs/PRE_REGISTRO_FIRMA_LIQUIDAZIONI.md. Per ogni etichetta del registratore
(data/liquidations/) cerca nel nastro pubblico (aggTrades USDT-M, Binance
Vision) i trade corrispondenti: stesso simbolo, finestra ±1 s, lato
aggressore = lato forzato, prezzo entro lo 0.1% del prezzo medio, quantità
cumulata ≥ 80% di quella dell'ordine.

Gate dichiarato: ≥90% delle etichette agganciate, o STOP.

Uso:  venv/bin/python scripts/firma_fase0.py [--data 2026-07-20] [--auto]
      --auto (per il timer): esce zitto se la fase è già registrata o se il
      dump del giorno non è ancora pubblicato; notifica su Telegram a fine run.
"""
import argparse
import io
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import requests
from loguru import logger

from src.shared.holdout import REGISTRY_PATH, record_trial, sealed_symbols

BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
LIQ_DIR = Path(__file__).parent.parent / "data" / "liquidations"
FINESTRA_MS = 1000
BANDA_PREZZO = 0.001
QUOTA_QTY = 0.8
GATE = 0.90
WORKERS = 8


def filtra_sigillati(etichette: pd.DataFrame) -> pd.DataFrame:
    """L'holdout non si tocca nemmeno per uno studio non direzionale."""
    return etichette[~etichette["symbol"].isin(sealed_symbols())]


def aggancia(etichette: pd.DataFrame, tape: pd.DataFrame) -> pd.Series:
    """True per ogni etichetta ritrovata nel nastro secondo i criteri del
    pre-registro. tape: colonne price, quantity, transact_time (ms),
    is_buyer_maker. Un ordine forzato SELL è un venditore aggressore, che
    nel nastro appare come is_buyer_maker=True."""
    tape = tape.sort_values("transact_time")
    tempi = tape["transact_time"].to_numpy()
    esiti = []
    for e in etichette.itertuples():
        ts_ms = int(e.ts.timestamp() * 1000)
        lo, hi = tempi.searchsorted(ts_ms - FINESTRA_MS), tempi.searchsorted(ts_ms + FINESTRA_MS, side="right")
        finestra = tape.iloc[lo:hi]
        lato_ok = finestra["is_buyer_maker"] == (e.side == "SELL")
        prezzo_ok = (finestra["price"] - e.prezzo_medio).abs() <= e.prezzo_medio * BANDA_PREZZO
        cum = finestra.loc[lato_ok & prezzo_ok, "quantity"].sum()
        esiti.append(cum >= QUOTA_QTY * e.qty)
    return pd.Series(esiti, index=etichette.index)


def _scarica_tape(symbol: str, giorno: str) -> pd.DataFrame | None:
    url = f"{BASE}/{symbol}/{symbol}-aggTrades-{giorno}.zip"
    for tentativo in range(3):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 404:
                return None                      # simbolo senza dump (es. delisting)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]))
            df.columns = [c.strip() for c in df.columns]
            return df[["price", "quantity", "transact_time", "is_buyer_maker"]]
        except Exception:
            if tentativo == 2:
                raise
    return None


def fase0_gia_registrata() -> bool:
    if not REGISTRY_PATH.exists():
        return False
    for line in REGISTRY_PATH.read_text().splitlines():
        r = json.loads(line)
        if r.get("ipotesi") == "firma_liquidazioni" and r.get("config", {}).get("fase") == 0:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None, help="giorno etichette (default: ieri UTC)")
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()
    giorno = args.data or f"{datetime.now(timezone.utc) - timedelta(days=1):%Y-%m-%d}"

    if args.auto and fase0_gia_registrata():
        return                                   # già fatta: il timer può girare a vuoto

    percorso = LIQ_DIR / f"{giorno}.parquet"
    if not percorso.exists():
        sys.exit(f"nessuna etichetta per {giorno} in {LIQ_DIR}")
    etichette = filtra_sigillati(pd.read_parquet(percorso))
    simboli = sorted(etichette["symbol"].unique())
    logger.info(f"{len(etichette)} etichette su {len(simboli)} simboli ({giorno})")

    # il dump del giorno è pubblicato? sonda sul simbolo più etichettato
    sonda = etichette["symbol"].value_counts().index[0]
    if requests.head(f"{BASE}/{sonda}/{sonda}-aggTrades-{giorno}.zip", timeout=30).status_code == 404:
        msg = f"dump aggTrades {giorno} non ancora pubblicato, riprovo domani"
        logger.info(msg)
        if args.auto:
            return
        sys.exit(msg)

    agganciate = 0
    senza_tape = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futuri = {pool.submit(_scarica_tape, s, giorno): s for s in simboli}
        for fut in as_completed(futuri):
            symbol = futuri[fut]
            tape = fut.result()
            gruppo = etichette[etichette["symbol"] == symbol]
            if tape is None:
                senza_tape.append((symbol, len(gruppo)))
                continue
            ok = aggancia(gruppo, tape)
            agganciate += int(ok.sum())
            logger.info(f"{symbol}: {ok.sum()}/{len(gruppo)} agganciate")

    valutabili = len(etichette) - sum(n for _, n in senza_tape)
    tasso = agganciate / valutabili if valutabili else 0.0
    esito = "PASS" if tasso >= GATE else "STOP"
    riassunto = (f"Fase 0 firma liquidazioni [{giorno}]: {agganciate}/{valutabili} "
                 f"agganciate ({tasso:.1%}) — gate {GATE:.0%} → {esito}"
                 + (f" | senza tape: {len(senza_tape)} simboli" if senza_tape else ""))
    logger.info(riassunto)

    record_trial("firma_liquidazioni",
                 {"fase": 0, "giorno": giorno, "criteri": {"finestra_ms": FINESTRA_MS,
                  "banda_prezzo": BANDA_PREZZO, "quota_qty": QUOTA_QTY, "gate": GATE},
                  "simboli": len(simboli)},
                 {"etichette_valutabili": valutabili, "agganciate": agganciate,
                  "tasso": round(tasso, 4), "esito": esito,
                  "senza_tape": [s for s, _ in senza_tape]})

    if args.auto:
        from src.shared.notifier import Notifier
        Notifier().send_telegram(f"🔬 {riassunto}")


if __name__ == "__main__":
    main()
