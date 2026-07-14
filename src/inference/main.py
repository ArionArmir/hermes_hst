import asyncio
import uvloop
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

import json
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import joblib
import numpy as np
import websockets
from loguru import logger
from datetime import datetime, timezone
from src.core.models import Signal, Config
from src.shared.redis_client import RedisClient
from src.inference.feature_engine import FeatureEngine
from src.shared.ohlc_aggregator import OHLCAggregator

class MLInference:
    def __init__(self):
        self.redis: RedisClient = None
        self.model = None
        self.model_path = "config/models/champion.pkl"
        self.running = True
        self.ws = None
        self.ws_url = "wss://fstream.binance.com/stream"
        self.symbols = ["btcusdt", "ethusdt", "solusdt"]
        self.feature_engines = {symbol: FeatureEngine(window=100) for symbol in self.symbols}
        self.ohlc_aggregator = OHLCAggregator()
        self.latest_prices = {}
        self.config = None

    async def initialize(self):
        logger.info("🧠 Avvio ML Inference con dati reali...")
        self.redis = RedisClient(host="localhost")
        await self.redis.connect()
        await self._load_config_from_redis()
        self._load_model()

        asyncio.create_task(self._websocket_loop())
        asyncio.create_task(self._inference_loop())
        asyncio.create_task(self._redis_listener())

        logger.info("✅ ML Inference avviato (con WebSocket Binance)")

    async def _load_config_from_redis(self):
        try:
            config_data = await self.redis.get_json('trading_config')
            if config_data:
                self.config = Config(**config_data)
                self._apply_config(self.config)
                logger.info("✅ Configurazione caricata da Redis")
            else:
                import yaml
                try:
                    with open('config/trading_params.yaml', 'r') as f:
                        yaml_data = yaml.safe_load(f)
                        self.config = Config(**yaml_data)
                        self._apply_config(self.config)
                        logger.info("✅ Configurazione caricata da YAML")
                except Exception as e:
                    logger.warning(f"⚠️ Config YAML non trovato: {e}")
                    self._apply_config(Config())
        except Exception as e:
            logger.error(f"❌ Errore caricamento config: {e}")
            self._apply_config(Config())

    def _apply_config(self, config: Config):
        self.symbols = [s.lower() for s in config.symbols]
        for symbol in self.symbols:
            if symbol not in self.feature_engines:
                self.feature_engines[symbol] = FeatureEngine(window=100)
        self.config = config

    async def _redis_listener(self):
        pubsub = await self.redis.subscribe('config_updated')
        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    logger.info("🔄 Configurazione aggiornata, ricarico...")
                    await self._load_config_from_redis()
        except Exception as e:
            logger.error(f"❌ Errore Redis listener: {e}")
            if self.running:
                await asyncio.sleep(5)
                asyncio.create_task(self._redis_listener())

    def _load_model(self):
        try:
            if os.path.exists(self.model_path):
                self.model = joblib.load(self.model_path)
                logger.info(f"✅ Modello caricato da {self.model_path}")
            else:
                logger.warning(f"⚠️ Modello non trovato: {self.model_path}")
                self.model = None
        except Exception as e:
            logger.error(f"❌ Errore caricamento modello: {e}")
            self.model = None

    async def _websocket_loop(self):
        while self.running:
            try:
                logger.info(f"🔌 Connessione WebSocket Binance ({', '.join(s.upper() for s in self.symbols)})...")
                stream_names = [f"{symbol}@trade" for symbol in self.symbols]
                stream_url = f"{self.ws_url}?streams={'/'.join(stream_names)}"

                async with websockets.connect(stream_url) as self.ws:
                    logger.info("✅ WebSocket Binance connesso")
                    while self.running:
                        try:
                            message = await self.ws.recv()
                            data = json.loads(message)
                            if 'data' in data:
                                trade = data['data']
                                stream = data.get('stream', '')
                                symbol = stream.replace('@trade', '').lower()
                                price = float(trade['p'])
                                volume = float(trade['q'])
                                if price <= 0 or volume <= 0:
                                    continue
                                self.latest_prices[symbol] = price
                                if symbol in self.feature_engines:
                                    self.feature_engines[symbol].add_tick(price, volume, trade.get('T'))
                                self.ohlc_aggregator.add_tick(symbol.upper(), price, volume)
                        except Exception as e:
                            logger.error(f"❌ Errore WebSocket: {e}")
                            await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"❌ Errore connessione WebSocket: {e}")
                await asyncio.sleep(5)

    async def _inference_loop(self):
        while self.running:
            await self.redis.set('heartbeat_inference', datetime.now(timezone.utc).isoformat())
            try:
                if self.model is None:
                    await asyncio.sleep(1)
                    continue

                # Verifica le posizioni aperte una volta per ciclo
                positions_data = await self.redis.get_json('positions')

                for symbol in self.symbols:
                    fe = self.feature_engines.get(symbol)
                    if fe is None or not fe.is_ready():
                        continue

                    features = fe.calculate_features()
                    if features is None:
                        continue

                    if not isinstance(features, np.ndarray):
                        features = np.array(features)
                    if features.size == 0:
                        continue
                    if features.ndim == 0:
                        features = features.reshape(1, -1)
                    elif features.ndim == 1:
                        features = features.reshape(1, -1)
                    elif features.ndim > 2:
                        continue

                    if features.shape[1] != 14:
                        logger.warning(f"⚠️ Feature mismatch per {symbol}: {features.shape[1]} != 14")
                        continue

                    pred = self.model.predict(features)[0]
                    prob = self.model.predict_proba(features)[0][1]

                    if pred == 1 and prob > 0.6:
                        action = "buy"
                    elif pred == 0 and prob < 0.4:
                        action = "sell"
                    else:
                        action = "hold"

                    # Non inviare segnali "hold" per non inquinare i log
                    if action == "hold":
                        continue

                    current_position = positions_data.get(symbol.upper()) if positions_data else None
                    if current_position:
                        implied_side = "long" if action == "buy" else "short"
                        if current_position.get("side") == implied_side:
                            logger.debug(
                                f"⏭️ Posizione già aperta in {implied_side} per {symbol.upper()}, segnale ignorato"
                            )
                            continue
                        logger.info(
                            f"🔄 Segnale {action} per {symbol.upper()} in direzione opposta alla posizione "
                            f"aperta ({current_position.get('side')}): pubblicato per valutazione reverse trading"
                        )

                    signal = Signal(
                        symbol=symbol.upper(),
                        action=action,
                        confidence=float(prob),
                        source="ml"
                    )

                    await self.redis.publish('ml_signals', signal.model_dump())
                    logger.debug(f"📊 {symbol.upper()} Segnale ML: {action} (conf={prob:.2f})")

            except Exception as e:
                logger.error(f"❌ Errore inference loop: {e}")

            await asyncio.sleep(5)

    def stop(self):
        self.running = False

async def main():
    inference = MLInference()
    await inference.initialize()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    logger.add(
        "logs/inference_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        level="DEBUG"
    )

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 ML Inference fermato manualmente")
    except Exception as e:
        logger.error(f"❌ Errore critico: {e}")
        sys.exit(1)
