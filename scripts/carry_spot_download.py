"""Scarica le klines SPOT 1h (solo close) per la gamba delta-neutra del carry.

docs/PRE_REGISTRO_CARRY.md. Fonte: dump mensili data.binance.vision (storia
2017-2020 a seconda del simbolo, verificata su 6/6 sondati) + giornalieri per
il mese corrente. Serve solo il close: il basis e' (perp-spot)/spot sui close.

Attenzione al formato: i dump spot storici NON hanno la riga di intestazione
(quelli recenti si'): si legge senza header e si scarta l'eventuale riga di
testa non numerica.

Output: data/spot/{SYMBOL}_1h.parquet  (timestamp, close)

Uso:  venv/bin/python scripts/carry_spot_download.py
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
OUT_DIR = Path(__file__).parent.parent / "data" / "spot"
WORKERS = 16


def _fetch(key: str) -> pd.DataFrame | None:
    for attempt in range(3):
        try:
            r = requests.get(BASE + key, timeout=30)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]), header=None,
                                 usecols=[0, 4], names=["open_time", "close"])
            df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]
            df["open_time"] = df["open_time"].astype("int64")
            # klines recenti usano microsecondi, storiche millisecondi
            unit = "us" if df["open_time"].iloc[0] > 10**14 else "ms"
            df["timestamp"] = pd.to_datetime(df["open_time"], unit=unit)
            df["close"] = df["close"].astype(float)
            return df[["timestamp", "close"]]
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1 + attempt)


def main():
    univ = universo()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Universo: {len(univ)} simboli | spot 1h (solo close)")
    senza_spot = []
    for i, sym in enumerate(sorted(univ), 1):
        keys = (_list_keys(f"data/spot/monthly/klines/{sym}/1h/")
                + _list_keys(f"data/spot/daily/klines/{sym}/1h/"))
        if not keys:
            senza_spot.append(sym)
            logger.warning(f"[{i}/{len(univ)}] {sym}: nessun mercato spot "
                           "(escluso per regola)")
            continue
        pezzi = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for f in as_completed({pool.submit(_fetch, k) for k in keys}):
                if f.result() is not None:
                    pezzi.append(f.result())
        df = (pd.concat(pezzi, ignore_index=True)
                .drop_duplicates("timestamp").sort_values("timestamp")
                .reset_index(drop=True))
        df.to_parquet(OUT_DIR / f"{sym}_1h.parquet")
        logger.info(f"[{i}/{len(univ)}] {sym}: {len(df):,} ore "
                    f"{df.timestamp.min().date()} -> {df.timestamp.max().date()}")
    logger.info(f"\nCompletato. Senza spot: {senza_spot or 'nessuno'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
