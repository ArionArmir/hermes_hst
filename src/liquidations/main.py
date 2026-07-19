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

WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
FLUSH_SECONDI = 120


async def main():
    logger.info("Registratore di liquidazioni: solo raccolta, nessuna decisione")
    redis = RedisClient()
    await redis.connect()
    buffer = BufferGiornaliero()
    backoff = 1

    async def flush_e_batti():
        while True:
            await asyncio.sleep(FLUSH_SECONDI)
            n = buffer.flush()
            if n:
                logger.info(f"flush: {n} liquidazioni scritte")
            await redis.set("heartbeat_liquidations",
                            datetime.now(timezone.utc).isoformat())

    asyncio.ensure_future(flush_e_batti())

    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                logger.info("stream forceOrder connesso")
                backoff = 1
                async for msg in ws:
                    riga = normalizza(json.loads(msg))
                    if riga and buffer.aggiungi(riga):
                        buffer.flush()
        except Exception as e:
            logger.warning(f"stream caduto ({e}), riconnessione in {backoff}s")
            buffer.flush()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
