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
        asyncio.create_task(self._heartbeat_loop())
        logger.info("✅ Agente Sentiment avviato con fonti RSS")

    async def _heartbeat_loop(self):
        while self.running:
            await self.redis.set('heartbeat_sentiment', datetime.now(timezone.utc).isoformat())
            await asyncio.sleep(15)

    async def _get_assets(self) -> list:
        """Asset base dai simboli configurati (BTCUSDT → BTC): Redis prima,
        YAML come fallback. Riletto a ogni ciclo (5 min): i cambi di config
        arrivano senza bisogno di un listener dedicato."""
        symbols = None
        try:
            config = await self.redis.get_json('trading_config')
            if config:
                symbols = config.get('symbols')
        except Exception as e:
            logger.warning(f"⚠️ Config non leggibile da Redis: {e}")
        if not symbols:
            try:
                import yaml
                with open('config/trading_params.yaml') as f:
                    symbols = yaml.safe_load(f).get('symbols')
            except Exception:
                pass
        if not symbols:
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        return [s.upper().replace("USDT", "") for s in symbols]

    @staticmethod
    def _normalize_scores(raw: dict, assets: list) -> dict:
        """Punteggi float in [-1, 1] per ogni asset richiesto (0 se mancante
        o non numerico); aggregate ricalcolato come media se assente o fuori
        scala. Mai valori inventati: il fallback è sempre 0 (neutro)."""
        scores = {}
        for asset in assets:
            try:
                scores[asset] = max(-1.0, min(1.0, float(raw.get(asset, 0.0))))
            except (TypeError, ValueError):
                scores[asset] = 0.0
        try:
            aggregate = float(raw.get('aggregate'))
            if not -1.0 <= aggregate <= 1.0:
                raise ValueError
        except (TypeError, ValueError):
            aggregate = sum(scores.values()) / len(scores) if scores else 0.0
        scores['aggregate'] = aggregate
        return scores

    async def _analyze_sentiment(self, news_by_asset: dict, assets: list) -> dict:
        sections = ["Notizie Generali:\n" + ("\n".join(news_by_asset.get("general", [])) or "Nessuna notizia generale.")]
        for asset in assets:
            titles = "\n".join(news_by_asset.get(asset, [])) or f"Nessuna notizia su {asset}."
            sections.append(f"Notizie su {asset}:\n{titles}")
        json_fields = ",\n".join(f'    "{asset}": (punteggio da -1 a 1)' for asset in assets)

        prompt = f"""
Sei un analista finanziario esperto in criptovalute.
Analizza le seguenti notizie e assegna un punteggio di sentiment da -1 (molto negativo) a +1 (molto positivo) per ciascun asset.
Se per un asset non ci sono notizie rilevanti, assegna 0.

{chr(10).join(sections)}

Restituisci SOLO un JSON valido con la seguente struttura:
{{
{json_fields},
    "aggregate": (media dei punteggi)
}}
Non aggiungere altro testo, solo il JSON.
"""
        neutral = self._normalize_scores({}, assets)
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
                        return self._normalize_scores(json.loads(response), assets)
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ Risposta Ollama non-JSON, sentiment neutro: {response[:200]!r}")
                        return neutral
            except Exception as e:
                logger.error(f"❌ Errore chiamata Ollama: {e}")
                return neutral

    async def _sentiment_loop(self):
        while self.running:
            try:
                assets = await self._get_assets()
                logger.info(f"📰 Recupero news da fonti RSS per {', '.join(assets)}...")
                news_by_asset = await self.news_fetcher.fetch_news_for_all(assets, limit_per_symbol=3)
                for asset in assets:
                    logger.debug(f"📰 News {asset}: {news_by_asset.get(asset, [])}")

                scores = await self._analyze_sentiment(news_by_asset, assets)
                summary = ", ".join(f"{a}={scores.get(a, 0):+.2f}" for a in assets)
                logger.info(f"🧠 Sentiment calcolato: {summary} | aggregate={scores.get('aggregate', 0):+.2f}")

                aggregate_score = scores.get('aggregate', 0.0)
                self._save_sentiment(aggregate_score)
                await self.redis.set('sentiment_score', str(aggregate_score))
                await self.redis.publish('sentiment_update', str(aggregate_score))

                # Pubblica il sentiment per asset (l'engine mappa asset→simbolo)
                # e persiste le chiavi per-asset lette dalla dashboard
                await self.redis.publish("sentiment_asset", json.dumps(scores))
                for asset in assets:
                    await self.redis.set(f'sentiment_{asset.lower()}', str(scores.get(asset, 0.0)))
                logger.info("✅ Sentiment pubblicato su Redis (aggregato + per asset)")

            except Exception as e:
                logger.error(f"❌ Errore sentiment loop: {e}")

            logger.info(f"⏳ Prossimo ciclo tra 5 minuti...")
            await asyncio.sleep(300)

    def _save_sentiment(self, score: float):
        filename = "data/sentiment_history.csv"
        new_row = pd.DataFrame([{"timestamp": datetime.now(timezone.utc).isoformat(), "score": score}])
        os.makedirs("data", exist_ok=True)
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
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
