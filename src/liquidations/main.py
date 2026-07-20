"""Registratore di liquidazioni — servizio (hermes-liquidations).

Ascolta lo stream pubblico !forceOrder@arr di Binance Futures (tutte le
liquidazioni, tutti i simboli) e le scrive nei parquet giornalieri. Solo
raccolta: nessuna chiave, nessuna decisione, nessun segnale.

Heartbeat su Redis ogni minuto; flush ogni 200 eventi o 120 secondi.
Riconnessione con backoff: lo stream cade, il servizio no.
"""
import asyncio
import json
from datetime import datetime, timezone

import websockets
from loguru import logger

from src.liquidations.recorder import BufferGiornaliero, normalizza
from src.shared.redis_client import RedisClient

# Endpoint instradato /market: dai 2026-04-23 gli URL legacy senza percorso
# (/ws, /stream) accettano la connessione e l'ACK di sottoscrizione ma non
# spingono più i canali /market — forceOrder tace senza alcun errore.
WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
FLUSH_SECONDI = 120


async def main():
    logger.info("Registratore di liquidazioni: solo raccolta, nessuna decisione")
    redis = RedisClient()
    await redis.connect()
    buffer = BufferGiornaliero()
    backoff = 1
    # Il heartbeat dice "il loop gira", non "i dati arrivano": 11 ore di
    # stream muto col watchdog verde. Questo timestamp misura i dati;
    # all'avvio vale l'ora di partenza, così un avvio a stream rotto
    # genera comunque un alert appena scade la soglia del watchdog.
    ultimo_evento = {"ts": datetime.now(timezone.utc).isoformat()}

    async def flush_e_batti():
        while True:
            await asyncio.sleep(FLUSH_SECONDI)
            n = buffer.flush()
            if n:
                logger.info(f"flush: {n} liquidazioni scritte")
            await redis.set("heartbeat_liquidations",
                            datetime.now(timezone.utc).isoformat())
            await redis.set("last_liquidation_event", ultimo_evento["ts"])

    asyncio.ensure_future(flush_e_batti())

    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                logger.info("stream forceOrder connesso")
                backoff = 1
                async for msg in ws:
                    riga = normalizza(json.loads(msg))
                    if riga:
                        ultimo_evento["ts"] = riga["ts"].isoformat()
                        if buffer.aggiungi(riga):
                            buffer.flush()
        except Exception as e:
            logger.warning(f"stream caduto ({e}), riconnessione in {backoff}s")
            buffer.flush()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
