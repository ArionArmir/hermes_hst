"""Backfill dello storico liquidazioni AGGREGATE da Coinalyze (API gratuita).

Contesto per il dataset del registratore live (hermes-liquidations): somme
long/short per simbolo e intervallo, NON i singoli eventi. Serve a rispondere
fra mesi a "il periodo che abbiamo registrato era tipico o anomalo?" — non
sostituisce la granularità evento-per-evento del registratore.

Limiti dichiarati del dato (misurati il 2026-07-20, non da doc):
- il piano gratuito serve SOLO gli ultimi ~2000-3200 punti per intervallo e
  simbolo: daily arriva al 2020, 1hour copre ~3-8 mesi (meno per i simboli
  più attivi); chiedere finestre più vecchie restituisce vuoto, quindi la
  profondità oraria si costruisce nel tempo rilanciando lo script (mensile
  basta: la finestra scorre più lentamente);
- unità: QUANTITÀ dell'asset base (0.024 BTC, non dollari) — verificato
  incrociando l'ora 10:00 del 2026-07-20 col nostro registratore;
- a monte c'è comunque lo stream Binance campionato (max 1 evento/simbolo/s
  dal 2021): il "totale" è un campione sistematicamente troncato nelle
  cascate, per chiunque lo raccolga.

Richiede COINALYZE_API_KEY in .env (registrazione gratuita).
Rilanciabile: unione con dedup su (symbol, t), append-only di fatto.

Output: data/liquidations_aggregate/coinalyze_{1h,daily}.parquet
Uso:    venv/bin/python scripts/coinalyze_backfill.py
"""
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import requests
from loguru import logger

from src.shared.holdout import record_trial

BASE = "https://api.coinalyze.net/v1"
OUT_DIR = Path(__file__).parent.parent / "data" / "liquidations_aggregate"
INTERVALLI = {"1hour": "coinalyze_1h.parquet", "daily": "coinalyze_daily.parquet"}
ASSET_NOSTRI = ["BTC", "ETH", "SOL", "TRX", "DOGE", "BNB", "XRP"]
INIZIO = int(datetime(2019, 9, 1, tzinfo=timezone.utc).timestamp())  # lancio Binance Futures
PAUSA = 1.6                 # 40 richieste/minuto sul piano gratuito


def normalizza_storia(symbol: str, history: list[dict]) -> pd.DataFrame:
    """Da una history Coinalyze [{t, l, s}, ...] a righe (t, symbol, liq_long,
    liq_short). t arriva in secondi UTC; l/s restano nelle unità dell'API."""
    if not history:
        return pd.DataFrame(columns=["t", "symbol", "liq_long", "liq_short"])
    df = pd.DataFrame(history)
    out = pd.DataFrame({
        "t": pd.to_datetime(df["t"], unit="s", utc=True),
        "symbol": symbol,
        "liq_long": pd.to_numeric(df["l"], errors="coerce"),
        "liq_short": pd.to_numeric(df["s"], errors="coerce"),
    })
    return out.dropna(subset=["liq_long", "liq_short"])


def unisci(vecchio: pd.DataFrame | None, nuovo: pd.DataFrame) -> pd.DataFrame:
    """Merge rilanciabile: dedup su (symbol, t), l'ultimo arrivato vince
    (una ri-scarica corregge eventuali ore parziali)."""
    frames = [f for f in (vecchio, nuovo) if f is not None and len(f)]
    if not frames:
        return nuovo
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["symbol", "t"], keep="last")
    return df.sort_values(["symbol", "t"]).reset_index(drop=True)


def _api(chiave: str, percorso: str, **params) -> list | dict:
    for tentativo in range(3):
        r = requests.get(f"{BASE}{percorso}", params=params,
                         headers={"api_key": chiave}, timeout=30)
        if r.status_code == 429:           # rate limit: aspetta e riprova
            time.sleep(30)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate limit persistente su {percorso}")


def simboli_binance_perp(chiave: str) -> list[str]:
    """I perpetual USDT Binance dei nostri 7 asset, scoperti dall'API invece
    che dedotti dal suffisso: se Coinalyze cambia codifica, fallisce rumorosamente."""
    exchanges = _api(chiave, "/exchanges")
    codici = {e["code"] for e in exchanges if e["name"] == "Binance"}
    if not codici:
        raise RuntimeError(f"exchange Binance non trovato: {[e['name'] for e in exchanges]}")
    mercati = _api(chiave, "/future-markets")
    simboli = sorted(m["symbol"] for m in mercati
                     if m.get("exchange") in codici and m.get("is_perpetual")
                     and m.get("quote_asset") == "USDT"
                     and m.get("base_asset") in ASSET_NOSTRI)
    if len(simboli) != len(ASSET_NOSTRI):
        trovati = {m.get("base_asset") for m in mercati if m["symbol"] in simboli}
        raise RuntimeError(f"attesi {len(ASSET_NOSTRI)} perp, trovati {len(simboli)} "
                           f"(mancano: {set(ASSET_NOSTRI) - trovati})")
    return simboli


def scarica(chiave: str, simboli: list[str], interval: str) -> pd.DataFrame:
    """Una richiesta per simbolo: l'API serve comunque solo la coda recente
    (~2000 punti), quindi spezzare la finestra non recupera nulla in più."""
    adesso = int(time.time())
    pezzi = []
    for symbol in simboli:
        risposta = _api(chiave, "/liquidation-history", symbols=symbol,
                        interval=interval, **{"from": INIZIO, "to": adesso})
        time.sleep(PAUSA)
        history = risposta[0]["history"] if risposta else []
        df = normalizza_storia(symbol, history)
        pezzi.append(df)
        estremi = f"{df['t'].min():%Y-%m-%d} → {df['t'].max():%Y-%m-%d}" if len(df) else "vuoto"
        logger.info(f"{symbol} {interval}: {len(df)} punti ({estremi})")
    return pd.concat(pezzi, ignore_index=True)


def main():
    env = Path(__file__).parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    chiave = os.environ.get("COINALYZE_API_KEY")
    if not chiave:
        sys.exit("COINALYZE_API_KEY mancante in .env (registrazione gratuita su coinalyze.net)")

    simboli = simboli_binance_perp(chiave)
    logger.info(f"simboli: {simboli}")
    for interval, nome_file in INTERVALLI.items():
        out = OUT_DIR / nome_file
        prima_volta = not out.exists()
        nuovo = scarica(chiave, simboli, interval)
        vecchio = pd.read_parquet(out) if out.exists() else None
        df = unisci(vecchio, nuovo)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        logger.info(f"{interval}: {len(df)} righe ({df['t'].min()} → {df['t'].max()}) in {out}")

        if prima_volta:
            record_trial("dati_liquidazioni_aggregate",
                         {"fonte": "Coinalyze /liquidation-history", "interval": interval,
                          "simboli": simboli},
                         {"evento": "nascita dataset aggregato",
                          "righe": len(df),
                          "nota": "contesto storico per il dataset live; aggregato in "
                                  "quantità di asset base, non sostituisce gli eventi "
                                  "del registratore"})


if __name__ == "__main__":
    main()
