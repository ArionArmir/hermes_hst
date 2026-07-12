import uvloop
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
"""
Trading Engine - Nucleo del sistema HFT
Gestisce WebSocket, posizioni, ordini limite e integrazione segnali
"""
import asyncio
import json
from src.shared.json_encoder import to_json
import signal
import sys
import os
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime
from datetime import timezone
from decimal import Decimal
import time

import websockets
from loguru import logger

# Aggiungi il percorso src al PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.models import Position, Signal, Config
from src.shared.redis_client import RedisClient
from src.shared.notifier import notifier

class TradingEngine:
    def __init__(self):
        self.redis: Optional[RedisClient] = None
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.ws_task: Optional[asyncio.Task] = None
        self.running: bool = True

        # Stato del sistema
        self.positions: Dict[str, Position] = {}
        self.latest_prices: Dict[str, float] = {}
        self.price_buffer: Dict[str, list] = {}
        self.sentiment_score: float = 0.0
        self.capital: float = 1000.0  # Capitale iniziale
        self.open_orders: Dict[str, dict] = {}  # Ordini limite aperti

        # Configurazione
        self.config: Optional[Config] = None
        self.config_version: int = 0
        self._config_reload_lock = asyncio.Lock()

        # URL WebSocket Binance Futures
        self.ws_url = "wss://fstream.binance.com/stream"

        # Simboli di trading
        self.symbols = ["btcusdt", "ethusdt", "solusdt"]

        # Parametri di trading (default, verranno sovrascritti dal config)
        self.leverage = 3
        self.stop_loss_pct = 0.01
        self.take_profit_pct = 0.02
        self.max_position_usdt = 200.0
        self.trailing_stop_pct = 0.005
        self.max_exposure = 0.5
        self.ml_confidence_threshold = 0.55
        self.sentiment_weight = 0.3

        self._executed_trades = []

    # =================================================================
    # METODI DI INIZIALIZZAZIONE
    # =================================================================
    async def initialize(self):
        """Inizializza il Trading Engine"""
        logger.info("🚀 Avvio Trading Engine...")

        # Connessione Redis
        self.redis = RedisClient(host="localhost")
        await self.redis.connect()

        # Carica configurazione
        await self._load_config_from_redis()

        # Carica posizioni da Redis (se presenti)
        await self._load_positions_from_redis()

        # Connessione WebSocket
        self.ws_task = asyncio.create_task(self._websocket_loop())

        # Task asincroni di background
        asyncio.create_task(self._redis_listener())
        asyncio.create_task(self._volatility_adjuster())
        asyncio.create_task(self._position_monitor())

        logger.info("✅ Trading Engine inizializzato")
        logger.info(f"📊 Simboli: {self.symbols}")
        logger.info(f"⚡ Leva: {self.leverage}x")
        logger.info(f"📉 Stop Loss: {self.stop_loss_pct*100:.1f}%")
        logger.info(f"📈 Take Profit: {self.take_profit_pct*100:.1f}%")

    # =================================================================
    # CONFIGURAZIONE
    # =================================================================
    async def _load_config_from_redis(self):
        """Carica la configurazione da Redis o usa i default"""
        try:
            config_data = await self.redis.get_json('trading_config')
            if config_data:
                self.config = Config(**config_data)
                self._apply_config(self.config)
                logger.info("✅ Configurazione caricata da Redis")
            else:
                # Carica da file YAML se disponibile
                import yaml
                try:
                    with open('config/trading_params.yaml', 'r') as f:
                        yaml_data = yaml.safe_load(f)
                        self.config = Config(**yaml_data)
                        self._apply_config(self.config)
                        # Salva su Redis per gli altri servizi
                        await self.redis.set('trading_config', self.config.model_dump())
                        logger.info("✅ Configurazione caricata da YAML")
                except Exception as e:
                    logger.warning(f"⚠️ Config YAML non trovato: {e}")
                    self._apply_config(Config())
        except Exception as e:
            logger.error(f"❌ Errore caricamento config: {e}")
            self._apply_config(Config())

    def _apply_config(self, config: Config):
        """Applica la configurazione ai parametri di trading"""
        self.leverage = config.leverage
        self.stop_loss_pct = config.stop_loss_pct
        self.take_profit_pct = config.take_profit_pct
        self.max_position_usdt = config.max_position_size_usdt
        self.trailing_stop_pct = config.trailing_stop_pct
        self.max_exposure = config.max_exposure
        self.symbols = [s.lower() for s in config.symbols]
        self.ml_confidence_threshold = config.ml_confidence_threshold
        self.sentiment_weight = config.sentiment_weight
        self.config = config
        self.config_version += 1

    # =================================================================
    # POSIZIONI SU REDIS
    # =================================================================
    async def _load_positions_from_redis(self):
        """Carica le posizioni da Redis all'avvio"""
        positions_data = await self.redis.get_json('positions')
        if positions_data:
            for symbol, pos_data in positions_data.items():
                try:
                    self.positions[symbol] = Position(**pos_data)
                    logger.info(f"📊 Posizione caricata: {symbol}")
                except Exception as e:
                    logger.error(f"❌ Errore caricamento posizione {symbol}: {e}")

    async def _save_positions_to_redis(self):
        """Salva le posizioni su Redis"""
        positions_dict = {}
        for symbol, pos in self.positions.items():
            if pos.is_open:
                positions_dict[symbol] = pos.model_dump()
        await self.redis.set('positions', positions_dict)

    # =================================================================
    # WEBSOCKET
    # =================================================================
    async def _websocket_loop(self):
        """Loop principale del WebSocket con riconnessione automatica"""
        while self.running:
            try:
                logger.info("🔌 Connessione WebSocket a Binance Futures...")
                stream_names = [f"{symbol}@trade" for symbol in self.symbols]
                stream_url = f"{self.ws_url}?streams={'/'.join(stream_names)}"

                async with websockets.connect( 
                    stream_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=2**20
                ) as self.ws:
                    logger.info("✅ WebSocket connesso")

                    # Invia heartbeat periodico
                    heartbeat_task = asyncio.create_task(self._heartbeat())

                    while self.running:
                        try:
                            message = await self.ws.recv()
                            await self._handle_ws_message(message)
                        except websockets.ConnectionClosed:
                            logger.warning("⚠️ WebSocket disconnesso. Riconnessione...")
                            break
                        except Exception as e:
                            logger.error(f"❌ Errore WebSocket: {e}")

                    heartbeat_task.cancel()

                # Attesa prima di riconnettere
                if self.running:
                    await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"❌ Errore connessione WebSocket: {e}")
                await asyncio.sleep(10)

    async def _heartbeat(self):
        """Invia heartbeat per mantenere la connessione"""
        while self.running and self.ws:
            try:
                await asyncio.sleep(30)
                await self.ws.ping()
            except:
                pass

    async def _handle_ws_message(self, message: str):
        """Gestisce un messaggio ricevuto dal WebSocket"""
        try:
            data = json.loads(message)
            if 'data' in data:
                trade = data['data']
                stream = data.get('stream', '')
                symbol = stream.replace('@trade', '').upper()
                price = float(trade['p'])
                quantity = float(trade['q'])

                # Aggiorna il prezzo ultimo
                self.latest_prices[symbol] = price

                # Controlla le condizioni di uscita per le posizioni aperte
                await self._check_exit_conditions(symbol, price)

        except json.JSONDecodeError as e:
            logger.error(f"❌ Errore parsing JSON: {e}")
        except Exception as e:
            logger.error(f"❌ Errore gestione messaggio: {e}")

    # =================================================================
    # LOGICA DI ENTRATA E USCITA
    # =================================================================
    async def _check_exit_conditions(self, symbol: str, price: float):
        """Controlla le condizioni di uscita per una posizione"""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]
        if not position.is_open:
            return

        # Stop Loss
        if position.side == 'long':
            if price <= position.stop_loss:
                await self._close_position(symbol, reason="STOP_LOSS")
                return
            if price >= position.take_profit:
                await self._close_position(symbol, reason="TAKE_PROFIT")
                return
            # Trailing Stop
            if position.trailing_stop and price <= position.trailing_stop:
                await self._close_position(symbol, reason="TRAILING_STOP")
                return
            # Aggiorna trailing stop (solo se il prezzo sale)
            new_trailing = price * (1 - self.trailing_stop_pct)
            if position.trailing_stop is None or new_trailing > position.trailing_stop:
                position.trailing_stop = new_trailing
                await self._save_positions_to_redis()

        elif position.side == 'short':
            # Logica speculare per short
            if price >= position.stop_loss:
                await self._close_position(symbol, reason="STOP_LOSS")
                return
            if price <= position.take_profit:
                await self._close_position(symbol, reason="TAKE_PROFIT")
                return
            if position.trailing_stop and price >= position.trailing_stop:
                await self._close_position(symbol, reason="TRAILING_STOP")
                return
            new_trailing = price * (1 + self.trailing_stop_pct)
            if position.trailing_stop is None or new_trailing < position.trailing_stop:
                position.trailing_stop = new_trailing
                await self._save_positions_to_redis()

    async def _open_position(self, signal: Signal):
        """
        Apre una nuova posizione con ordine limite (anti-slippage)
        """
        symbol = signal.symbol
        if symbol in self.positions and self.positions[symbol].is_open:
            logger.warning(f"⚠️ Posizione già aperta su {symbol}")
            return

        price = self.latest_prices.get(symbol, 0)
        if price <= 0:
            logger.warning(f"⚠️ Prezzo non disponibile per {symbol}")
            return

        # Calcola leva dinamica in base alla volatilità
        leverage = self.leverage
        if self.config and self.config.volatility_adjustment:
            vol = await self._get_current_volatility(symbol)
            if vol > self.config.max_volatility_threshold:
                leverage = max(1, leverage - 2)
                logger.info(f"⚡ Volatilità alta → leva ridotta a {leverage}x")
            elif vol < self.config.min_volatility_threshold:
                leverage = min(5, leverage + 1)
                logger.info(f"⚡ Volatilità bassa → leva aumentata a {leverage}x")

        # Calcola quantità massima investibile (esposizione)
        position_size = min(
            self.max_position_usdt * leverage,
            self.capital * self.max_exposure
        )
        if position_size <= 0:
            logger.warning("⚠️ Capitale insufficiente per aprire posizione")
            return

        quantity = position_size / price

        # Arrotonda la quantità secondo il lotto minimo di Binance
        quantity = self._round_quantity(symbol, quantity)

        # Calcola stop loss e take profit
        if signal.action == 'buy':
            side = 'long'
            stop_loss = price * (1 - self.stop_loss_pct)
            take_profit = price * (1 + self.take_profit_pct)
            trailing_stop = price * (1 - self.trailing_stop_pct)
        else:  # sell (short)
            side = 'short'
            stop_loss = price * (1 + self.stop_loss_pct)
            take_profit = price * (1 - self.take_profit_pct)
            trailing_stop = price * (1 + self.trailing_stop_pct)

        # Crea la posizione
        position = Position(
            symbol=symbol,
            side=side,
            entry_price=price,
            quantity=quantity,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=trailing_stop
        )

        # Esegui l'ordine limite
        order_placed = await self._place_limit_order(symbol, side, quantity, price)
        if not order_placed:
            logger.error(f"❌ Ordine fallito per {symbol}")
            return

        # Aggiungi la posizione
        self.positions[symbol] = position
        await self._save_positions_to_redis()

        logger.info(f"🚀 Posizione APERTA: {symbol} {side} {quantity:.4f} @ {price:.2f} | SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
        notifier.notify_position_opened(symbol, side, price, quantity, stop_loss, take_profit)

    async def _close_position(self, symbol: str, reason: str = "MANUAL"):
        """
        Chiude una posizione aperta
        """
        if symbol not in self.positions:
            return

        position = self.positions[symbol]
        if not position.is_open:
            return

        current_price = self.latest_prices.get(symbol, position.entry_price)
        pnl = (current_price - position.entry_price) * position.quantity
        if position.side == 'short':
            pnl = -pnl

        # Chiudi l'ordine
        await self._place_close_order(symbol, position.side, position.quantity)

        # Registra il trade
        self._executed_trades.append({
            'symbol': symbol,
            'side': position.side,
            'entry': position.entry_price,
            'exit': current_price,
            'quantity': position.quantity,
            'pnl': pnl,
            'reason': reason,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

        # Rimuovi la posizione
        position.is_open = False
        await self._save_positions_to_redis()

        logger.info(f"📉 Posizione CHIUSA: {symbol} | PnL: {pnl:.2f} USDT | Motivo: {reason}")
        self._save_trade_to_file(symbol, position.side, position.entry_price, current_price, pnl, reason)
        notifier.notify_position_closed(symbol, position.side, position.entry_price, current_price, pnl, reason)

    # =================================================================
    # ORDINI LIMITE (ANTI-SLIPPAGE)
    # =================================================================
    async def _place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> bool:
        """
        Piazza un ordine limite su Binance Futures (simulato per paper trading)
        """
        # In produzione usare ccxt o binance connector
        logger.info(f"📊 ORDINE LIMITE: {side.upper()} {quantity:.4f} {symbol} @ {price:.2f}")
        return True  # Simulazione successo

    async def _place_close_order(self, symbol: str, side: str, quantity: float) -> bool:
        """
        Chiude una posizione con ordine di mercato (simulato)
        """
        close_side = 'SELL' if side == 'long' else 'BUY'
        logger.info(f"📊 ORDINE CHIUSURA: {close_side} {quantity:.4f} {symbol}")
        return True

    def _round_quantity(self, symbol: str, quantity: float) -> float:
        """
        Arrotonda la quantità secondo i lotti minimi di Binance
        """
        # Per semplicità arrotondiamo a 3 decimali
        # In produzione usare i filtri dal exchange info
        return round(quantity, 3)

    # =================================================================
    # VOLATILITÀ
    # =================================================================
    async def _get_current_volatility(self, symbol: str) -> float:
        """Calcola la volatilità sugli ultimi 50 tick (simulato)"""
        # In produzione, calcolare da un buffer di prezzi
        return 0.012

    async def _volatility_adjuster(self):
        """Task periodico per adattare la leva in base alla volatilità"""
        while self.running:
            await asyncio.sleep(60)
            try:
                for symbol in self.symbols:
                    vol = await self._get_current_volatility(symbol.upper())
                    if vol > 0.02 and self.leverage > 1:
                        self.leverage = max(1, self.leverage - 1)
                        logger.info(f"⚡ Volatilità alta su {symbol}: leva ridotta a {self.leverage}x")
            except Exception as e:
                logger.error(f"❌ Errore volatility adjuster: {e}")

    # =================================================================
    # POSITION MONITOR
    # =================================================================
    async def _position_monitor(self):
        """Monitora periodicamente le posizioni per aggiornamenti"""
        while self.running:
            await asyncio.sleep(5)
            for symbol, position in self.positions.items():
                if position.is_open and symbol in self.latest_prices:
                    price = self.latest_prices[symbol]
                    if position.side == 'long':
                        pnl = (price - position.entry_price) * position.quantity
                    else:
                        pnl = (position.entry_price - price) * position.quantity
                    position.pnl = pnl

    # =================================================================
    # REDIS LISTENER
    # =================================================================
    async def _redis_listener(self):
        """Ascolta i segnali da Redis"""
        pubsub = await self.redis.subscribe('ml_signals')
        await self.redis.subscribe('sentiment_update')
        await self.redis.subscribe('config_updated')

        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    channel = message['channel']
                    data = message['data']

                    if channel == 'ml_signals':
                        try:
                            signal_data = json.loads(data)
                            signal = Signal(**signal_data)
                            await self._on_signal(signal)
                        except Exception as e:
                            logger.error(f"❌ Errore parsing segnale ML: {e}")

                    elif channel == 'sentiment_update':
                        try:
                            self.sentiment_score = float(data)
                            logger.info(f"🧠 Sentiment aggiornato: {self.sentiment_score:.2f}")
                        except Exception as e:
                            logger.error(f"❌ Errore parsing sentiment: {e}")

                    elif channel == 'config_updated':
                        logger.info("🔄 Configurazione aggiornata, ricarico...")
                        await self._load_config_from_redis()

        except Exception as e:
            logger.error(f"❌ Errore Redis listener: {e}")
            if self.running:
                await asyncio.sleep(5)
                asyncio.create_task(self._redis_listener())

    async def _on_signal(self, signal: Signal):
        """Gestisce un segnale dal modello ML"""
        logger.info(f"📊 Segnale ricevuto: {signal.action} per {signal.symbol} (conf: {signal.confidence:.2f})")

        # Applica il filtro del sentiment
        if signal.source == 'ml':
            # 70% ML + 30% Sentiment
            weighted_confidence = (1 - self.sentiment_weight) * signal.confidence + self.sentiment_weight * abs(self.sentiment_score)

            # Se il sentiment è fortemente negativo e il segnale è buy, annulla
            if self.sentiment_score < -0.5 and signal.action == 'buy':
                logger.warning("⚠️ Sentiment negativo forte → segnale BUY filtrato")
                return

            if weighted_confidence < self.ml_confidence_threshold:
                logger.info(f"ℹ️ Confidenza bassa ({weighted_confidence:.2f}) → segnale ignorato")
                return

            # Applica il segnale
            if signal.action == 'buy':
                await self._open_position(signal)
            elif signal.action == 'sell':
                await self._open_position(signal)  # Per short
            elif signal.action == 'close':
                await self._close_position(signal.symbol, reason="ML_SIGNAL")

    # =================================================================
    # SEGNALI DI SISTEMA
    # =================================================================
    def stop(self):
        """Arresta il Trading Engine"""
        self.running = False
        if self.ws_task:
            self.ws_task.cancel()

    # =================================================================
    # RUN
    # =================================================================
    async def run(self):
        """Avvia il Trading Engine"""
        await self.initialize()

        # Gestione segnali di sistema
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop)

        # Loop principale
        while self.running:
            await asyncio.sleep(0.1)

        logger.info("🛑 Trading Engine fermato")


# =================================================================
# MAIN
# =================================================================
if __name__ == "__main__":
    # Configura il logging
        "logs/trading_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
    )

    engine = TradingEngine()
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        engine.stop()
        logger.info("🛑 Interruzione manuale")
    except Exception as e:
        logger.error(f"❌ Errore critico: {e}")
        sys.exit(1)


    def _save_trade_to_file(self, symbol: str, side: str, entry: float, exit_price: float, pnl: float, reason: str):
        """Salva le operazioni in un file CSV per la dashboard"""
        import pandas as pd
        import os
        filename = "data/trades_history.csv"
        new_row = pd.DataFrame([{
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'symbol': symbol,
            'side': side,
            'entry': entry,
            'exit': exit_price,
            'pnl': pnl,
            'reason': reason
        }])
        os.makedirs("data", exist_ok=True)
        if os.path.exists(filename):
            df = pd.read_csv(filename)
            df = pd.concat([df, new_row], ignore_index=True)
        else:
            df = new_row
        df.to_csv(filename, index=False)
