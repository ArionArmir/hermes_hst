"""
Client Redis con connessione asincrona
"""
import json
import redis.asyncio as aioredis
from loguru import logger
from typing import Optional, Any, Dict
from src.shared.json_encoder import to_json

class RedisClient:
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        self.host = host
        self.port = port
        self.db = db
        self.redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None

    async def connect(self):
        try:
            self.redis = await aioredis.from_url(
                f"redis://{self.host}:{self.port}/{self.db}",
                decode_responses=True
            )
            logger.info("✅ Redis connesso")
            return self.redis
        except Exception as e:
            logger.error(f"❌ Errore connessione Redis: {e}")
            return None

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
        if self._pubsub is None:
            self._pubsub = self.redis.pubsub()
        await self._pubsub.subscribe(channel)
        return self._pubsub

    async def close(self):
        if self.redis:
            await self.redis.close()
