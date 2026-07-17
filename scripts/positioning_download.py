"""Scarica i futures metrics (OI, long/short) dai dump pubblici di Binance.

docs/PRE_REGISTRO_POSITIONING.md. Fonte: data.binance.vision, file giornalieri
zip con snapshot ogni 5 minuti dal 2020-09-01 (verificato su S3 il 2026-07-17;
l'API REST ne conserva solo 30 giorni, ed e' il motivo per cui questa
dimensione era stata chiusa per errore).

Esistono solo dump GIORNALIERI per i metrics (i monthly non li includono):
~2.000 file per simbolo. Il download e' concorrente e riprendibile: le date
gia' presenti nel parquet non vengono riscaricate.

Output: data/metrics/{SYMBOL}_metrics_5m.parquet

Uso:  venv/bin/python scripts/positioning_download.py
"""
import io
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import requests
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))
from breadth_download import universo

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BASE = "https://data.binance.vision/"
OUT_DIR = Path(__file__).parent.parent / "data" / "metrics"
WORKERS = 16
ANNI_MINIMI = 5.0

KEEP = ["create_time", "sum_open_interest", "count_long_short_ratio",
        "sum_taker_long_short_vol_ratio"]


def _list_keys(prefix: str) -> list[str]:
    """Listing S3 paginato: enumera i file reali invece di sondare date a
    tentativi (niente 404, e le date di inizio per-simbolo emergono da sole)."""
    keys, token = [], None
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        r = requests.get(S3, params=params, timeout=30)
        r.raise_for_status()
        root = ElementTree.fromstring(r.content)
        keys += [k.text for k in root.iter(f"{ns}Key")
                 if k.text.endswith(".zip")]
        tok = root.find(f"{ns}NextContinuationToken")
        if tok is None:
            return keys
        token = tok.text


def _fetch_day(key: str) -> pd.DataFrame | None:
    for attempt in range(3):
        try:
            r = requests.get(BASE + key, timeout=30)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]), usecols=KEEP)
            df["create_time"] = pd.to_datetime(df["create_time"])
            return df
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1 + attempt)


def scarica_simbolo(sym: str) -> Path | None:
    path = OUT_DIR / f"{sym}_metrics_5m.parquet"
    esistente = pd.read_parquet(path) if path.exists() else None
    gia = (set(esistente["create_time"].dt.date.astype(str))
           if esistente is not None else set())

    keys = _list_keys(f"data/futures/um/daily/metrics/{sym}/")
    mancanti = [k for k in keys if k.split("-metrics-")[1][:10] not in gia]
    if not mancanti:
        return path
    logger.info(f"  {sym}: {len(keys)} giorni su S3, {len(mancanti)} da scaricare")

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
        tot = (tot.drop_duplicates("create_time")
               .sort_values("create_time").reset_index(drop=True))
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tot.to_parquet(path)
    return path if path.exists() else None


def main():
    univ = universo()
    logger.info(f"Universo: {len(univ)} simboli | fonte: dump giornalieri "
                f"data.binance.vision (solo daily: i monthly non hanno metrics)")

    coperture = {}
    for i, sym in enumerate(sorted(univ), 1):
        t0 = time.time()
        path = scarica_simbolo(sym)
        if path is None:
            logger.warning(f"[{i}/{len(univ)}] {sym}: NESSUN dato metrics")
            continue
        df = pd.read_parquet(path)
        a, b = df["create_time"].min(), df["create_time"].max()
        coperture[sym] = (a, b)
        logger.info(f"[{i}/{len(univ)}] {sym}: {len(df):,} snapshot "
                    f"{a.date()} -> {b.date()} | {time.time()-t0:.0f}s")

    # ---- Gate del pre-registro: intersezione >= 5 anni o STOP ----
    if not coperture:
        logger.error("❌ STOP: nessun simbolo con metrics.")
        return 1
    inizio = max(a for a, _ in coperture.values())
    fine = min(b for _, b in coperture.values())
    anni = (fine - inizio).days / 365.25
    tardivi = sorted(coperture.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
    logger.info(f"\nIntersezione metrics: {inizio.date()} -> {fine.date()} = {anni:.2f} anni")
    logger.info("  inizi piu' tardivi: " +
                ", ".join(f"{s} {a.date()}" for s, (a, _) in tardivi))
    if len(coperture) < len(univ):
        logger.warning(f"  simboli senza metrics: {sorted(set(univ) - set(coperture))}")
    if anni < ANNI_MINIMI or len(coperture) < len(univ):
        logger.error(f"❌ STOP: gate del pre-registro non superato "
                     f"({anni:.2f} anni, {len(coperture)}/{len(univ)} simboli). "
                     "Diagnosi esplicita prima di procedere.")
        return 1
    logger.info(f"✅ Gate superato: {anni:.2f} anni su {len(coperture)} simboli.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
