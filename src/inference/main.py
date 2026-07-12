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
from src.core.models import Signal
from src.shared.redis_client import RedisClient
from src.inference.feature_engine import FeatureEngine

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
        self.latest_prices = {}

    async def initialize(self):
        logger.info("🧠 Avvio ML Inference con dati reali...")
        self.redis = RedisClient(host="localhost")
        await self.redis.connect()
        self._load_model()

        asyncio.create_task(self._websocket_loop())
        asyncio.create_task(self._inference_loop())

        logger.info("✅ ML Inference avviato (con WebSocket Binance)")

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
                logger.info("🔌 Connessione WebSocket Binance (BTC, ETH, SOL)...")
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
                                self.latest_prices[symbol] = price
                                if symbol in self.feature_engines:
                                    self.feature_engines[symbol].add_tick(price, volume, trade.get('T'))
                        except Exception as e:
                            logger.error(f"❌ Errore WebSocket: {e}")
                            await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"❌ Errore connessione WebSocket: {e}")
                await asyncio.sleep(5)

    async def _inference_loop(self):
        while self.running:
            try:
                if self.model is None:
                    await asyncio.sleep(1)
                    continue

                # Verifica le posizioni aperte una volta per ciclo
                positions_data = await self.redis.get_json('positions')
                open_positions = set(positions_data.keys()) if positions_data else set()

                for symbol in self.symbols:
                    # Salta se il simbolo ha già una posizione aperta
                    if symbol.upper() in open_positions:
                        logger.debug(f"⏭️ Posizione già aperta per {symbol.upper()}, segnale ignorato")
                        continue

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
