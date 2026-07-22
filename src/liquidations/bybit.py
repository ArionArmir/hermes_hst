"""Registratore di liquidazioni Bybit — servizio (hermes-liquidations-bybit).

Ascolta il canale allLiquidation dei perpetual lineari: a differenza dello
stream Binance (censurato a max 1 evento/simbolo/secondo dal 2021), Bybit
dichiara di pubblicare TUTTI gli eventi. È la verità completa che a Binance
manca: serve da ground truth per l'eventuale Fase 2 dello studio firma
(docs/PRE_REGISTRO_FIRMA_LIQUIDAZIONI.md). Solo raccolta, nessuna chiave.

Il canale richiede la sottoscrizione simbolo per simbolo: l'elenco dei
perpetual si scarica dalla REST a ogni (ri)connessione, così i nuovi listing
entrano da soli al primo drop dello stream.

Nota sul campo side: si salva ESATTAMENTE come consegnato da Bybit, senza
tradurlo nella convenzione Binance (dove SELL = long liquidato). La semantica
andrà fissata empiricamente nel pre-registro che userà questi dati.

Heartbeat su Redis ogni minuto; flush ogni 200 eventi o 120 secondi.
Riconnessione con backoff: lo stream cade, il servizio no.
"""
import asyncio
import json
from datetime import datetime, timezone

import requests
import websockets
from loguru import logger

from src.liquidations.recorder import BufferGiornaliero, OUT_DIR
from src.shared.redis_client import RedisClient

WS_URL = "wss://stream.bybit.com/v5/public/linear"
REST_INSTRUMENTS = "https://api.bybit.com/v5/market/instruments-info"
OUT_BYBIT = OUT_DIR.parent / "liquidations_bybit"
FLUSH_SECONDI = 120
PING_SECONDI = 20           # Bybit vuole un ping applicativo {"op":"ping"} ogni 20s
BATCH_SUB = 10              # argomenti per messaggio di sottoscrizione


def normalizza(evento: dict) -> dict | None:
    """Da un elemento data di allLiquidation {T, s, S, v, p} allo schema del
    registratore Binance (stesse colonne: le analisi girano su entrambi)."""
    try:
        qty, prezzo = float(evento["v"]), float(evento["p"])
        return {
            "ts": datetime.fromtimestamp(int(evento["T"]) / 1000, tz=timezone.utc),
            "symbol": str(evento["s"]),
            "side": str(evento["S"]),
            "qty": qty,
            "prezzo_medio": prezzo,
            "notional_usdt": qty * prezzo,
        }
    except (KeyError, TypeError, ValueError):
        return None


def lotti_sottoscrizione(simboli: list[str], batch: int = BATCH_SUB) -> list[list[str]]:
    """Topic allLiquidation.<SYMBOL> spezzati in messaggi da <=batch argomenti."""
    topics = [f"allLiquidation.{s}" for s in simboli]
    return [topics[i:i + batch] for i in range(0, len(topics), batch)]


def simboli_lineari() -> list[str]:
    """Tutti i perpetual lineari in stato Trading, via REST paginata."""
    simboli, cursor = [], ""
    while True:
        r = requests.get(REST_INSTRUMENTS,
                         params={"category": "linear", "limit": 1000, "cursor": cursor},
                         timeout=30)
        r.raise_for_status()
        risultato = r.json()["result"]
        simboli += [i["symbol"] for i in risultato["list"] if i.get("status") == "Trading"]
        cursor = risultato.get("nextPageCursor") or ""
        if not cursor:
            return sorted(set(simboli))


async def main():
    logger.info("Registratore liquidazioni Bybit: solo raccolta, nessuna decisione")
    redis = RedisClient()
    await redis.connect()
    buffer = BufferGiornaliero(out_dir=OUT_BYBIT, dedup=False)   # verità completa: mai deduplicare
    ultimo_evento = {"ts": datetime.now(timezone.utc).isoformat()}
    backoff = 1

    async def flush_e_batti():
        while True:
            await asyncio.sleep(FLUSH_SECONDI)
            try:      # per-giro: un errore non deve uccidere il task (vedi main.py)
                n = buffer.flush()
                if n:
                    logger.info(f"flush: {n} liquidazioni scritte")
                await redis.set("heartbeat_liquidations_bybit",
                                datetime.now(timezone.utc).isoformat())
                await redis.set("last_liquidation_event_bybit", ultimo_evento["ts"])
            except Exception as e:
                logger.error(f"flush/heartbeat bybit fallito (riprovo): {e}")

    asyncio.ensure_future(flush_e_batti())

    while True:
        try:
            simboli = simboli_lineari()
            async with websockets.connect(WS_URL, ping_interval=None) as ws:
                for lotto in lotti_sottoscrizione(simboli):
                    await ws.send(json.dumps({"op": "subscribe", "args": lotto}))
                logger.info(f"stream allLiquidation connesso ({len(simboli)} perpetual)")
                backoff = 1

                async def ping():
                    while True:
                        await asyncio.sleep(PING_SECONDI)
                        await ws.send(json.dumps({"op": "ping"}))

                compito_ping = asyncio.ensure_future(ping())
                try:
                    async for msg in ws:
                        d = json.loads(msg)
                        if not d.get("topic", "").startswith("allLiquidation"):
                            continue        # ack di sottoscrizione, pong, ecc.
                        for evento in d.get("data", []):
                            riga = normalizza(evento)
                            if riga:
                                ultimo_evento["ts"] = riga["ts"].isoformat()
                                if buffer.aggiungi(riga):
                                    try:
                                        buffer.flush()
                                    except Exception as e:
                                        logger.error(f"flush inline fallito (le righe restano nel buffer, ritenta il task periodico): {e}")
                finally:
                    compito_ping.cancel()
        except Exception as e:
            logger.warning(f"stream caduto ({e}), riconnessione in {backoff}s")
            try:
                buffer.flush()
            except Exception as e2:
                logger.error(f"flush in riconnessione fallito (ritenta il task periodico): {e2}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
