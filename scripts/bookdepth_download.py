"""Scarica i bookDepth giornalieri (dump pubblici) e li riduce a snapshot orari.

docs/PRE_REGISTRO_CHIUSURA_FEATURE.md. File giornalieri con snapshot ~ogni
25s del notional a +/-1..5% dal mid, dal 2023-01-01 (verificato su 4 simboli).

Riduzione all'ingestione, dichiarata nel pre-registro:
- si tengono SOLO i livelli +/-1% (le feature usano solo quelli)
- ultimo snapshot di ogni ora (l'orizzonte e' orario: 45M righe/simbolo non
  aggiungono informazione, solo costo)

Output: data/bookdepth/{SYMBOL}_1h.parquet  (timestamp, bid_1pct, ask_1pct)

Uso:  venv/bin/python scripts/bookdepth_download.py
"""
import io
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import requests
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))
from breadth_download import universo
from positioning_download import _list_keys

BASE = "https://data.binance.vision/"
OUT_DIR = Path(__file__).parent.parent / "data" / "bookdepth"
WORKERS = 16
ANNI_MINIMI = 3.0


def _fetch_day(key: str) -> pd.DataFrame | None:
    for attempt in range(3):
        try:
            r = requests.get(BASE + key, timeout=30)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]))
            df = df[df["percentage"].abs() == 1]          # solo +/-1%
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df["ora"] = df["timestamp"].dt.floor("1h")
            # ultimo snapshot dell'ora, per lato
            df = (df.sort_values("timestamp")
                    .groupby(["ora", "percentage"], as_index=False).last())
            piv = df.pivot(index="ora", columns="percentage", values="notional")
            piv.columns = ["bid_1pct" if c < 0 else "ask_1pct" for c in piv.columns]
            return piv.reset_index().rename(columns={"ora": "timestamp"})
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1 + attempt)


def scarica_simbolo(sym: str) -> Path | None:
    path = OUT_DIR / f"{sym}_1h.parquet"
    esistente = pd.read_parquet(path) if path.exists() else None
    gia = (set(esistente["timestamp"].dt.date.astype(str))
           if esistente is not None else set())
    keys = _list_keys(f"data/futures/um/daily/bookDepth/{sym}/")
    mancanti = [k for k in keys if k.split("-bookDepth-")[1][:10] not in gia]
    if not mancanti:
        return path

    pezzi, falliti = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(_fetch_day, k): k for k in mancanti}
        for f in as_completed(futs):
            df = f.result()
            if df is None:
                falliti += 1
            else:
                pezzi.append(df)
    if falliti:
        logger.warning(f"  {sym}: {falliti} giorni falliti dopo 3 tentativi")
    if pezzi:
        nuovo = pd.concat(pezzi, ignore_index=True)
        tot = (pd.concat([esistente, nuovo], ignore_index=True)
               if esistente is not None else nuovo)
        tot = (tot.drop_duplicates("timestamp")
                  .sort_values("timestamp").reset_index(drop=True))
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tot.to_parquet(path)
    return path if path.exists() else None


def main():
    univ = universo()
    logger.info(f"Universo: {len(univ)} simboli | bookDepth ridotto a orario, solo +/-1%")
    coperture = {}
    for i, sym in enumerate(sorted(univ), 1):
        t0 = time.time()
        path = scarica_simbolo(sym)
        if path is None:
            logger.warning(f"[{i}/{len(univ)}] {sym}: NESSUN dato")
            continue
        df = pd.read_parquet(path)
        coperture[sym] = (df["timestamp"].min(), df["timestamp"].max())
        logger.info(f"[{i}/{len(univ)}] {sym}: {len(df):,} ore "
                    f"{coperture[sym][0].date()} -> {coperture[sym][1].date()} "
                    f"| {time.time()-t0:.0f}s")

    if not coperture:
        logger.error("❌ STOP: nessun simbolo con bookDepth.")
        return 1
    inizio = max(a for a, _ in coperture.values())
    fine = min(b for _, b in coperture.values())
    anni = (fine - inizio).days / 365.25
    logger.info(f"\nIntersezione bookDepth: {inizio.date()} -> {fine.date()} = {anni:.2f} anni")
    if anni < ANNI_MINIMI or len(coperture) < len(univ):
        logger.error(f"❌ STOP: gate non superato ({anni:.2f} anni, "
                     f"{len(coperture)}/{len(univ)} simboli).")
        return 1
    logger.info(f"✅ Gate superato: {anni:.2f} anni su {len(coperture)} simboli.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
