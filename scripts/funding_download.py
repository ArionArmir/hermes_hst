"""Scarica lo storico funding rate dai dump mensili pubblici.

docs/PRE_REGISTRO_CHIUSURA_FEATURE.md. I dump mensili partono dal mese di
quotazione di ogni simbolo (verificato su 4 simboli): coprono l'intera
finestra candele. Colonne: calc_time (ms), funding_interval_hours,
last_funding_rate.

Output: data/funding/{SYMBOL}_funding.parquet

Uso:  venv/bin/python scripts/funding_download.py
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
OUT_DIR = Path(__file__).parent.parent / "data" / "funding"
WORKERS = 16
ANNI_MINIMI = 5.0


def _fetch(key: str) -> pd.DataFrame | None:
    for attempt in range(3):
        try:
            r = requests.get(BASE + key, timeout=30)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]))
            df["calc_time"] = pd.to_datetime(df["calc_time"], unit="ms")
            return df[["calc_time", "last_funding_rate"]]
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1 + attempt)


def main():
    univ = universo()
    logger.info(f"Universo: {len(univ)} simboli | funding dai dump mensili")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    coperture = {}
    for i, sym in enumerate(sorted(univ), 1):
        keys = _list_keys(f"data/futures/um/monthly/fundingRate/{sym}/")
        pezzi = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for f in as_completed({pool.submit(_fetch, k) for k in keys}):
                if f.result() is not None:
                    pezzi.append(f.result())
        if not pezzi:
            logger.warning(f"[{i}/{len(univ)}] {sym}: NESSUN funding")
            continue
        df = (pd.concat(pezzi, ignore_index=True)
                .drop_duplicates("calc_time").sort_values("calc_time")
                .reset_index(drop=True))
        df.to_parquet(OUT_DIR / f"{sym}_funding.parquet")
        coperture[sym] = (df["calc_time"].min(), df["calc_time"].max())
        logger.info(f"[{i}/{len(univ)}] {sym}: {len(df):,} eventi "
                    f"{coperture[sym][0].date()} -> {coperture[sym][1].date()}")

    inizio = max(a for a, _ in coperture.values())
    fine = min(b for _, b in coperture.values())
    anni = (fine - inizio).days / 365.25
    logger.info(f"\nIntersezione funding: {inizio.date()} -> {fine.date()} = {anni:.2f} anni")
    if anni < ANNI_MINIMI or len(coperture) < len(univ):
        logger.error(f"❌ STOP: gate non superato ({anni:.2f} anni, "
                     f"{len(coperture)}/{len(univ)}).")
        return 1
    logger.info(f"✅ Gate superato: {anni:.2f} anni su {len(coperture)} simboli.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
