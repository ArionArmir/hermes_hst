import asyncio
import aiohttp
from loguru import logger
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.shared.redis_client import RedisClient
from data_engine.news_fetcher import NewsFetcher

class OllamaSentiment:
    def __init__(self):
        self.redis: RedisClient = None
        self.ollama_host = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
        self.model = "qwen2.5-coder:1.5b"
        self.running = True
        self.news_fetcher = NewsFetcher()

    async def initialize(self):
        logger.info("🧠 Avvio Agente Sentiment avanzato...")
        self.redis = RedisClient(host="localhost")
        await self.redis.connect()
        asyncio.create_task(self._sentiment_loop())
        logger.info("✅ Agente Sentiment avviato con fonti RSS")

    async def _analyze_sentiment(self, news_by_asset: dict) -> dict:
        prompt_template = """
Sei un analista finanziario esperto in criptovalute. 
Analizza le seguenti notizie e assegna un punteggio di sentiment da -1 (molto negativo) a +1 (molto positivo) per ciascun asset.

Notizie Generali:
{general}

Notizie su Bitcoin (BTC):
{btc}

Notizie su Ethereum (ETH):
{eth}

Notizie su Solana (SOL):
{sol}

Restituisci SOLO un JSON valido con la seguente struttura:
{{
    "BTC": (punteggio da -1 a 1),
    "ETH": (punteggio da -1 a 1),
    "SOL": (punteggio da -1 a 1),
    "aggregate": (media ponderata dei tre punteggi)
}}
Non aggiungere altro testo, solo il JSON.
"""
        general = "\n".join(news_by_asset.get("general", [])) or "Nessuna notizia generale."
        btc = "\n".join(news_by_asset.get("BTC", [])) or "Nessuna notizia su Bitcoin."
        eth = "\n".join(news_by_asset.get("ETH", [])) or "Nessuna notizia su Ethereum."
        sol = "\n".join(news_by_asset.get("SOL", [])) or "Nessuna notizia su Solana."

        prompt = prompt_template.format(general=general, btc=btc, eth=eth, sol=sol)

        async with aiohttp.ClientSession() as session:
            try:
                payload = {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"
                }
                async with session.post(f"{self.ollama_host}/api/generate", json=payload, timeout=45) as resp:
                    data = await resp.json()
                    response = data.get('response', '{}').strip()
                    try:
                        result = json.loads(response)
                    except:
                        import re
                        numbers = re.findall(r'-?\d+\.?\d*', response)
                        if len(numbers) >= 3:
                            result = {
                                "BTC": float(numbers[0]),
                                "ETH": float(numbers[1]),
                                "SOL": float(numbers[2]),
                                "aggregate": float(numbers[3]) if len(numbers) > 3 else sum(map(float, numbers[:3]))/3
                            }
                        else:
                            result = {"BTC": 0.0, "ETH": 0.0, "SOL": 0.0, "aggregate": 0.0}
                    return result
            except Exception as e:
                logger.error(f"❌ Errore chiamata Ollama: {e}")
                return {"BTC": 0.0, "ETH": 0.0, "SOL": 0.0, "aggregate": 0.0}

    async def _sentiment_loop(self):
        while self.running:
            try:
                logger.info("📰 Recupero news da fonti RSS...")
                news_by_asset = await self.news_fetcher.fetch_news_for_all(limit_per_symbol=3)
                logger.debug(f"📰 News BTC: {news_by_asset.get('BTC', [])}")
                logger.debug(f"📰 News ETH: {news_by_asset.get('ETH', [])}")
                logger.debug(f"📰 News SOL: {news_by_asset.get('SOL', [])}")

                scores = await self._analyze_sentiment(news_by_asset)
                logger.info(f"🧠 Sentiment calcolato: BTC={scores.get('BTC', 0):.2f}, ETH={scores.get('ETH', 0):.2f}, SOL={scores.get('SOL', 0):.2f}, aggregate={scores.get('aggregate', 0):.2f}")
                logger.info(f"   📊 Sentiment singoli: BTC={scores.get('BTC', 0):.2f}, ETH={scores.get('ETH', 0):.2f}, SOL={scores.get('SOL', 0):.2f}")

                aggregate_score = scores.get('aggregate', 0.0)
                self._save_sentiment(aggregate_score)
                await self.redis.set('sentiment_score', str(aggregate_score))
                await self.redis.publish('sentiment_update', str(aggregate_score))
                logger.info(f"✅ Sentiment aggregato pubblicato su Redis: {aggregate_score:.2f}")

                # Pubblica sentiment per asset
                await self.redis.publish("sentiment_asset", json.dumps(scores))
                logger.info(f"✅ Sentiment per asset pubblicato su Redis")

                await self.redis.set('sentiment_btc', str(scores.get('BTC', 0)))
                await self.redis.set('sentiment_eth', str(scores.get('ETH', 0)))
                await self.redis.set('sentiment_sol', str(scores.get('SOL', 0)))

            except Exception as e:
                logger.error(f"❌ Errore sentiment loop: {e}")

            logger.info(f"⏳ Prossimo ciclo tra 5 minuti...")
            await asyncio.sleep(300)

    def _save_sentiment(self, score: float):
        filename = "data/sentiment_history.csv"
        new_row = pd.DataFrame([{"timestamp": datetime.now(timezone.utc).isoformat(), "score": score}])
        os.makedirs("data", exist_ok=True)
        if os.path.exists(filename):
            df = pd.read_csv(filename)
            df = pd.concat([df, new_row], ignore_index=True)
        else:
            df = new_row
        df.to_csv(filename, index=False)
        logger.debug(f"💾 Sentiment salvato: {score:.2f}")

    def stop(self):
        self.running = False

if __name__ == "__main__":
    logger.add(
        "logs/sentiment_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        level="DEBUG"
    )

    sentiment = OllamaSentiment()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(sentiment.initialize())
        loop.run_forever()
    except KeyboardInterrupt:
        sentiment.stop()
        logger.info("🛑 Agente Sentiment fermato manualmente")
    except Exception as e:
        logger.error(f"❌ Errore critico: {e}")
        sys.exit(1)
