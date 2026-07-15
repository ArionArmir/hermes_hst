import asyncio
import uvloop
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

import json
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import joblib
import websockets
from loguru import logger
from datetime import datetime, timezone
from src.core.models import Signal, Config
from src.shared.redis_client import RedisClient
from src.shared.features import compute_latest_features, FEATURE_COLS
from src.shared.candle_feed import CandleFeed
from src.shared.ohlc_aggregator import OHLCAggregator
from src.training.feature_engine import TARGET_DOWN, TARGET_FLAT, TARGET_UP
from src.shared.signal_policy import signal_from_proba, SIGNAL_PROB_THRESHOLD

class MLInference:
    def __init__(self):
        self.redis: RedisClient = None
        self.model = None
        self.model_path = "config/models/champion.pkl"
        self.running = True
        self.ws = None
        self.ws_url = "wss://fstream.binance.com/stream"
        self.symbols = ["btcusdt", "ethusdt", "solusdt"]
        # Le feature sono calcolate sulle stesse candele del training
        # (config.timeframe), scaricate via REST: mai sui tick del WebSocket,
        # che qui serve solo per prezzi live e candele 1m della dashboard.
        self.candle_feed = CandleFeed(interval="1h")
        self.ohlc_aggregator = OHLCAggregator()
        self.latest_prices = {}
        self.config = None

    async def initialize(self):
        logger.info("🧠 Avvio ML Inference con dati reali...")
        self.redis = RedisClient()
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
        if config.timeframe != self.candle_feed.interval:
            logger.info(f"🕐 Timeframe candele: {self.candle_feed.interval} → {config.timeframe}")
            self.candle_feed = CandleFeed(interval=config.timeframe)
        self.config = config

    async def _redis_listener(self):
        pubsub = await self.redis.subscribe('config_updated')
        await self.redis.subscribe('model_swap')
        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    await self._on_pubsub_message(message['channel'])
        except Exception as e:
            logger.error(f"❌ Errore Redis listener: {e}")
            if self.running:
                await asyncio.sleep(5)
                asyncio.create_task(self._redis_listener())

    async def _on_pubsub_message(self, channel: str):
        if isinstance(channel, bytes):
            channel = channel.decode()
        if channel == 'config_updated':
            logger.info("🔄 Configurazione aggiornata, ricarico...")
            await self._load_config_from_redis()
        elif channel == 'model_swap':
            # Il trainer pubblica qui dopo ogni promozione del challenger:
            # il nuovo champion entra in servizio senza riavviare il processo.
            # _load_model rivalida feature e classi: un modello incompatibile
            # viene rifiutato (model=None, nessun segnale) invece di produrre
            # predizioni senza senso.
            logger.info("🔄 Nuovo champion pubblicato dal trainer, ricarico il modello...")
            self._load_model()

    def _load_model(self):
        try:
            if os.path.exists(self.model_path):
                model = joblib.load(self.model_path)
                # Guardia anti-skew: il modello deve essere stato addestrato
                # esattamente sulle FEATURE_COLS condivise. Un modello vecchio
                # o incompatibile qui produrrebbe predizioni silenziosamente
                # senza senso: meglio nessun segnale che segnali casuali.
                trained_names = list(model.get_booster().feature_names or [])
                if trained_names != FEATURE_COLS:
                    logger.error(
                        f"❌ Modello incompatibile: addestrato su {trained_names}, "
                        f"attese {FEATURE_COLS}. Rilanciare train_all_models.py. Nessun segnale verrà emesso."
                    )
                    self.model = None
                    return
                expected_classes = [TARGET_DOWN, TARGET_FLAT, TARGET_UP]
                if list(model.classes_) != expected_classes:
                    logger.error(
                        f"❌ Modello incompatibile: classi {list(model.classes_)}, attese {expected_classes} "
                        f"(down/flat/up). Rilanciare train_all_models.py. Nessun segnale verrà emesso."
                    )
                    self.model = None
                    return
                self.model = model
                logger.info(
                    f"✅ Modello caricato da {self.model_path} "
                    f"({len(trained_names)} feature e {len(expected_classes)} classi validate)"
                )
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

                async with websockets.connect(stream_url, ping_interval=30, ping_timeout=10) as self.ws:
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
                                await self.redis.set('last_tick_inference', datetime.now(timezone.utc).isoformat())
                                self.ohlc_aggregator.add_tick(symbol.upper(), price, volume)
                        except Exception as e:
                            logger.error(f"❌ Errore WebSocket: {e}")
                            await asyncio.sleep(1)
                            # Stessa ragione dell'engine: la connessione può essere
                            # morta senza sollevare più eccezioni distinguibili;
                            # usciamo per farla riaprire dal ciclo esterno. Il
                            # ping_interval/ping_timeout sopra serve a rilevare
                            # anche connessioni "zombie" che non erroravano affatto.
                            break
            except Exception as e:
                logger.error(f"❌ Errore connessione WebSocket: {e}")
                await asyncio.sleep(5)

    def _signal_from_proba(self, proba) -> tuple:
        """Delega alla policy condivisa con il backtester
        (src/shared/signal_policy.py): live e simulazione devono decidere
        con la stessa identica regola."""
        return signal_from_proba(proba[TARGET_DOWN], proba[TARGET_UP])

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
                    candles = await self.candle_feed.get_candles(symbol.upper())
                    # DataFrame 1×N con i nomi di colonna: XGBoost valida che
                    # corrispondano a quelli visti in training (con un ndarray
                    # la validazione salterebbe silenziosamente).
                    features = compute_latest_features(candles)
                    if features is None:
                        continue

                    proba = self.model.predict_proba(features)[0]
                    action, prob = self._signal_from_proba(proba)

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
