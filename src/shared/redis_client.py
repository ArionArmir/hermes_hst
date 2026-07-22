"""
Client Redis con connessione asincrona
"""
import asyncio
import json
import os
import redis.asyncio as aioredis
from loguru import logger
from typing import Optional, Any, Dict
from src.shared.json_encoder import to_json

class RedisClient:
    def __init__(self, host: str = None, port: int = None, db: int = 0):
        # In Docker l'host arriva da REDIS_HOST (es. il nome del servizio
        # compose); il default localhost preserva l'avvio manuale su WSL.
        self.host = host or os.getenv("REDIS_HOST", "localhost")
        self.port = int(port or os.getenv("REDIS_PORT", "6379"))
        self.db = db
        self.redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None

    async def connect(self, tentativi: int = 8):
        """Riprova la connessione con backoff, poi SOLLEVA (revisione
        2026-07-21, S6 + regressione della prima passata): il vecchio
        'ritorna None e avanti' produceva servizi zombie; un retry INFINITO
        (bug introdotto dal primo fix) bloccava avvio e test suite per sempre
        se Redis non tornava. Retry BOUNDED (~1+2+4+8+16+30+30+30 ≈ 2 min)
        copre il caso comune 'Redis parte pochi secondi dopo il servizio al
        boot'; se resta giù oltre, si solleva e systemd (Restart=always)
        riavvia — fail loud + auto-recover, mai zombie né hang."""
        attesa = 1
        for tentativo in range(1, tentativi + 1):
            try:
                self.redis = await aioredis.from_url(
                    f"redis://{self.host}:{self.port}/{self.db}",
                    decode_responses=True
                )
                await self.redis.ping()
                logger.info("✅ Redis connesso")
                return self.redis
            except Exception as e:
                ultimo = e
                if tentativo < tentativi:
                    logger.error(f"❌ Errore connessione Redis ({tentativo}/{tentativi}): "
                                 f"{e} — riprovo tra {attesa}s")
                    await asyncio.sleep(attesa)
                    attesa = min(attesa * 2, 30)
        logger.error(f"❌ Redis irraggiungibile dopo {tentativi} tentativi: sollevo")
        raise ultimo

    async def set(self, key: str, value: Any):
        if isinstance(value, (dict, list)):
            value = to_json(value)
        await self.redis.set(key, value)

    async def get(self, key: str) -> Optional[str]:
        return await self.redis.get(key)

    async def get_json(self, key: str) -> Optional[dict]:
        data = await self.redis.get(key)
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError as e:
                logger.error(f"❌ Errore parsing JSON per chiave '{key}': {e}")
        return None

    async def publish(self, channel: str, message: Any):
        if isinstance(message, (dict, list)):
            message = to_json(message)
        await self.redis.publish(channel, message)

    async def subscribe(self, channel: str):
        """DEPRECATO per i listener con recovery: riusa un unico pubsub
        condiviso, che dopo un errore di connessione può restare
        irrecuperabile. Usare subscribe_fresh."""
        if self._pubsub is None:
            self._pubsub = self.redis.pubsub()
        await self._pubsub.subscribe(channel)
        return self._pubsub

    async def subscribe_fresh(self, *channels: str):
        """Nuovo oggetto pubsub iscritto ai canali indicati. Per i listener
        che si ricreano dopo un errore: il pubsub precedente potrebbe essere
        morto insieme alla connessione, riusarlo fallirebbe per sempre."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(*channels)
        return pubsub

    async def close(self):
        if self.redis:
            await self.redis.close()
