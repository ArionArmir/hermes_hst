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

    async def connect(self, retry_forever: bool = True):
        """Riprova finché Redis non risponde (revisione 2026-07-21, S6):
        il vecchio 'ritorna None e avanti' produceva servizi zombie — vivi
        per systemd, ma con ogni set/publish che falliva per sempre.
        retry_forever=False mantiene il vecchio contratto per i chiamanti
        che gestiscono da soli il fallimento."""
        attesa = 1
        while True:
            try:
                self.redis = await aioredis.from_url(
                    f"redis://{self.host}:{self.port}/{self.db}",
                    decode_responses=True
                )
                await self.redis.ping()
                logger.info("✅ Redis connesso")
                return self.redis
            except Exception as e:
                logger.error(f"❌ Errore connessione Redis: {e}"
                             + (f" — riprovo tra {attesa}s" if retry_forever else ""))
                if not retry_forever:
                    return None
                await asyncio.sleep(attesa)
                attesa = min(attesa * 2, 30)

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
