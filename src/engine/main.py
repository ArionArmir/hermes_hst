"""
Trading Engine - Nucleo del sistema HFT
Gestisce WebSocket, posizioni, reverse trading e integrazione modelli
"""
import asyncio
import json
import signal
import sys
import os
from pathlib import Path
from typing import Dict, Optional, Tuple
from datetime import datetime, timezone
import time
import aiohttp

import websockets
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.models import Position, Signal, Config
from src.shared.redis_client import RedisClient
from src.shared.notifier import notifier
from src.shared.json_encoder import to_json
from src.exit_model import ATRExitModel
from src.volume_pattern import VolumePatternAnalyzer

# Moltiplicatori ATR per simbolo: stima iniziale basata sulla volatilità
# realizzata calcolata in questa sessione (vedi commit sull'aggiunta di
# TRX/DOGE/BNB/XRP) — da affinare con i risultati del test notturno.
# Simbolo non presente nel dizionario -> fallback 5.0/5.5.
DEFAULT_SL_MULTIPLIERS = {
    "BTCUSDT": 5.0,
    "ETHUSDT": 5.5,
    "SOLUSDT": 6.0,
    "DOGEUSDT": 8.0,
    "XRPUSDT": 6.0,
    "BNBUSDT": 4.5,
    "TRXUSDT": 4.0,
}
DEFAULT_TP_MULTIPLIERS = {
    "BTCUSDT": 5.5,
    "ETHUSDT": 6.0,
    "SOLUSDT": 6.5,
    "DOGEUSDT": 8.5,
    "XRPUSDT": 6.5,
    "BNBUSDT": 5.0,
    "TRXUSDT": 4.5,
}


def _build_exit_model(symbol_upper: str) -> ATRExitModel:
    return ATRExitModel(
        atr_multiplier_sl=DEFAULT_SL_MULTIPLIERS.get(symbol_upper, 5.0),
        atr_multiplier_tp=DEFAULT_TP_MULTIPLIERS.get(symbol_upper, 5.5),
    )


class TradingEngine:
    def __init__(self):
        self.redis: Optional[RedisClient] = None
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.ws_task: Optional[asyncio.Task] = None
        self.running: bool = True

        self.positions: Dict[str, Position] = {}
        self.latest_prices: Dict[str, float] = {}
        self.sentiment_score: float = 0.0
        self.sentiment_by_asset: Dict[str, float] = {}
        self.capital: float = 1000.0
        self.open_orders: Dict[str, dict] = {}

        self.config: Optional[Config] = None
        self.config_version: int = 0
        self._config_reload_lock = asyncio.Lock()

        self.ws_url = "wss://fstream.binance.com/stream"
        self.symbols = ["btcusdt", "ethusdt", "solusdt"]

        self.leverage = 3
        self.stop_loss_pct = 0.025
        self.take_profit_pct = 0.04
        self.max_position_usdt = 200.0
        self.trailing_stop_pct = 0.015
        self.max_exposure = 0.5
        self.ml_confidence_threshold = 0.55
        self.sentiment_weight = 0.3

        self._executed_trades = []

        # --- MODELLI (uno per simbolo) ---
        self.exit_models: Dict[str, ATRExitModel] = {
            symbol.upper(): _build_exit_model(symbol.upper())
            for symbol in self.symbols
        }
        self.pattern_models: Dict[str, VolumePatternAnalyzer] = {
            symbol.upper(): VolumePatternAnalyzer(window=10)
            for symbol in self.symbols
        }

        self.reverse_trading_enabled = True
        self.pattern_confirmation_enabled = True
        self.dynamic_exit_enabled = True
        self.max_holding_minutes = 300

    async def initialize(self):
        logger.info("🚀 Avvio Trading Engine...")
        self.redis = RedisClient(host="localhost")
        await self.redis.connect()
        await self._load_config_from_redis()
        await self._load_positions_from_redis()

        self.ws_task = asyncio.create_task(self._websocket_loop())
        asyncio.create_task(self._redis_listener())
        asyncio.create_task(self._position_monitor())

        logger.info("✅ Trading Engine inizializzato")
        logger.info(f"📊 Simboli: {self.symbols}")
        logger.info(f"⚡ Leva: {self.leverage}x")
        logger.info(f"🔁 Reverse Trading: {'ON' if self.reverse_trading_enabled else 'OFF'}")
        logger.info(f"🔍 Pattern Confirmation: {'ON' if self.pattern_confirmation_enabled else 'OFF'}")
        logger.info(f"📊 Dynamic Exit: {'ON' if self.dynamic_exit_enabled else 'OFF'}")

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
                        await self.redis.set('trading_config', self.config.model_dump())
                        logger.info("✅ Configurazione caricata da YAML")
                except Exception as e:
                    logger.warning(f"⚠️ Config YAML non trovato: {e}")
                    self._apply_config(Config())
        except Exception as e:
            logger.error(f"❌ Errore caricamento config: {e}")
            self._apply_config(Config())

    def _apply_config(self, config: Config):
        self.leverage = config.leverage
        self.stop_loss_pct = config.stop_loss_pct
        self.take_profit_pct = config.take_profit_pct
        self.max_position_usdt = config.max_position_size_usdt
        self.trailing_stop_pct = config.trailing_stop_pct
        self.max_exposure = config.max_exposure
        self.symbols = [s.lower() for s in config.symbols]
        for symbol in self.symbols:
            symbol_upper = symbol.upper()
            if symbol_upper not in self.exit_models:
                self.exit_models[symbol_upper] = _build_exit_model(symbol_upper)
            if symbol_upper not in self.pattern_models:
                self.pattern_models[symbol_upper] = VolumePatternAnalyzer(window=10)
        self.ml_confidence_threshold = config.ml_confidence_threshold
        self.sentiment_weight = config.sentiment_weight
        self.reverse_trading_enabled = config.reverse_trading_enabled
        self.pattern_confirmation_enabled = config.pattern_confirmation_enabled
        self.dynamic_exit_enabled = config.dynamic_exit_enabled
        self.max_holding_minutes = config.max_holding_minutes
        self._warn_if_holding_below_model_horizon(config)
        self.config = config
        self.config_version += 1

    def _warn_if_holding_below_model_horizon(self, config: Config):
        """Il modello predice il rendimento su TARGET_HORIZON_BARS candele:
        un max_holding più corto chiude le posizioni prima che la predizione
        possa realizzarsi (è il bug che rendeva la strategia un "ruota ogni
        ora" — vedi docs/IMPROVEMENT_PLAN.md, S1)."""
        try:
            from src.training.feature_engine import TARGET_HORIZON_BARS
            from src.shared.features import timeframe_minutes
            horizon_minutes = TARGET_HORIZON_BARS * timeframe_minutes(config.timeframe)
        except ValueError as e:
            logger.warning(f"⚠️ Impossibile verificare l'orizzonte del modello: {e}")
            return
        if config.max_holding_minutes < horizon_minutes:
            logger.warning(
                f"⚠️ max_holding_minutes={config.max_holding_minutes} < orizzonte del modello "
                f"({TARGET_HORIZON_BARS} barre × {config.timeframe} = {horizon_minutes} min): "
                f"le posizioni verranno chiuse prima che la predizione possa realizzarsi"
            )

    async def _load_positions_from_redis(self):
        positions_data = await self.redis.get_json('positions')
        if positions_data:
            for symbol, pos_data in positions_data.items():
                try:
                    self.positions[symbol] = Position(**pos_data)
                    logger.info(f"📊 Posizione caricata: {symbol}")
                except Exception as e:
                    logger.error(f"❌ Errore caricamento posizione {symbol}: {e}")

    async def _save_positions_to_redis(self):
        positions_dict = {}
        for symbol, pos in self.positions.items():
            if pos.is_open:
                positions_dict[symbol] = pos.model_dump()
        await self.redis.set('positions', positions_dict)

    async def _get_price_rest(self, symbol: str) -> float:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol.upper()}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    return float(data['price'])
        except Exception as e:
            logger.error(f"❌ Fallback REST fallito per {symbol}: {e}")
            return 0.0

    async def _websocket_loop(self):
        while self.running:
            try:
                logger.info("🔌 Connessione WebSocket Binance...")
                stream_names = [f"{symbol}@trade" for symbol in self.symbols]
                stream_url = f"{self.ws_url}?streams={'/'.join(stream_names)}"

                async with websockets.connect(stream_url, ping_interval=30, ping_timeout=10) as self.ws:
                    logger.info("✅ WebSocket connesso")

                    while self.running:
                        try:
                            msg = await self.ws.recv()
                            data = json.loads(msg)
                            if 'data' in data:
                                trade = data['data']
                                stream = data.get('stream', '')
                                symbol = stream.replace('@trade', '').upper()
                                price = float(trade['p'])
                                volume = float(trade['q'])
                                if price <= 0 or volume <= 0:
                                    continue
                                high = price * 1.002
                                low = price * 0.998

                                exit_model = self.exit_models.get(symbol)
                                pattern_model = self.pattern_models.get(symbol)
                                if exit_model:
                                    exit_model.add_price(price, high, low)
                                if pattern_model:
                                    pattern_model.add_data(price, volume, high, low)
                                self.latest_prices[symbol] = price
                                await self.redis.set(f"latest_price_{symbol}", str(price))
                                await self.redis.set('last_tick_engine', datetime.now(timezone.utc).isoformat())

                                await self._check_exit_conditions(symbol, price)

                                # Trailing stop dinamico (con logging ridotto)
                                if symbol in self.positions and self.positions[symbol].is_open and self.dynamic_exit_enabled and exit_model:
                                    pos = self.positions[symbol]
                                    new_sl = exit_model.update_trailing_stop(price, pos)
                                    if new_sl != pos.stop_loss:
                                        old_sl = pos.stop_loss
                                        pos.stop_loss = new_sl
                                        await self._save_positions_to_redis()
                                        if abs(new_sl - old_sl) / old_sl > 0.001:
                                            logger.info(f"🔝 Trailing stop aggiornato per {symbol}: {new_sl:.2f}")
                        except Exception as e:
                            logger.error(f"❌ Errore WebSocket: {e}")
                            await asyncio.sleep(1)
                            # La connessione può essere morta (es. "no close frame
                            # received or sent"): ritentare .recv() sullo stesso
                            # oggetto fallirebbe identicamente per sempre. Usciamo
                            # dal ciclo interno così quello esterno ne apre una nuova.
                            break
            except Exception as e:
                logger.error(f"❌ Errore connessione WebSocket: {e}")
                await asyncio.sleep(5)

    async def _check_exit_conditions(self, symbol: str, price: float):
        if symbol not in self.positions:
            return
        position = self.positions[symbol]
        if not position.is_open:
            return

        if price <= 0:
            price = await self._get_price_rest(symbol)
            if price <= 0:
                logger.warning(f"⚠️ Prezzo non disponibile per {symbol}")
                return

        if position.side == 'long':
            if price <= position.stop_loss:
                await self._close_position(symbol, reason="STOP_LOSS", price=price)
                return
            if price >= position.take_profit:
                await self._close_position(symbol, reason="TAKE_PROFIT", price=price)
                return
        elif position.side == 'short':
            if price >= position.stop_loss:
                await self._close_position(symbol, reason="STOP_LOSS", price=price)
                return
            if price <= position.take_profit:
                await self._close_position(symbol, reason="TAKE_PROFIT", price=price)
                return

    async def _open_position(self, signal: Signal):
        symbol = signal.symbol
        if symbol in self.positions and self.positions[symbol].is_open:
            logger.warning(f"⚠️ Posizione già aperta su {symbol}")
            return

        price = self.latest_prices.get(symbol, 0)
        if price <= 0:
            price = await self._get_price_rest(symbol)
            if price <= 0:
                logger.warning(f"⚠️ Prezzo non disponibile per {symbol}")
                return

        exit_model = self.exit_models.get(symbol)
        side = 'long' if signal.action == 'buy' else 'short'

        if self.pattern_confirmation_enabled:
            pattern_model = self.pattern_models.get(symbol)
            if pattern_model:
                pattern_result = pattern_model.analyze()
                if pattern_result["signal"] == "REJECT":
                    logger.warning(f"⛔ Segnale rifiutato da Pattern Analyzer: {pattern_result['reason']}")
                    return
                elif pattern_result["signal"] == "NEUTRAL":
                    logger.info(f"ℹ️ Pattern neutro: {pattern_result['reason']}")

        leverage = self.leverage
        position_size = min(self.max_position_usdt * leverage, self.capital * self.max_exposure)
        if position_size <= 0:
            logger.warning("⚠️ Capitale insufficiente per aprire posizione")
            return

        quantity = position_size / price
        quantity = round(quantity, 3)

        if self.dynamic_exit_enabled and exit_model:
            stop_loss, take_profit = exit_model.calculate_exit_levels(price, side)
            logger.info(f"📊 SL/TP dinamici: SL={stop_loss:.2f}, TP={take_profit:.2f} (ATR={exit_model._calculate_atr():.2f})")
        else:
            if signal.action == 'buy':
                stop_loss = price * (1 - self.stop_loss_pct)
                take_profit = price * (1 + self.take_profit_pct)
            else:
                stop_loss = price * (1 + self.stop_loss_pct)
                take_profit = price * (1 - self.take_profit_pct)

        trailing_stop = price * (1 - self.trailing_stop_pct) if side == 'long' else price * (1 + self.trailing_stop_pct)

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

        self.positions[symbol] = position
        await self._save_positions_to_redis()

        logger.info(f"🚀 Posizione APERTA: {symbol} {side} {quantity:.4f} @ {price:.2f} | SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
        notifier.notify_position_opened(symbol, side, price, quantity, stop_loss, take_profit)

    async def _close_position(self, symbol: str, reason: str = "MANUAL", price: float = None):
        if symbol not in self.positions:
            return
        position = self.positions[symbol]
        if not position.is_open:
            return

        if price is None or price <= 0:
            price = self.latest_prices.get(symbol, position.entry_price)
            if price <= 0:
                price = await self._get_price_rest(symbol)
                if price <= 0:
                    logger.warning(f"⚠️ Prezzo non disponibile per chiusura {symbol}, uso entry")
                    price = position.entry_price

        pnl = (price - position.entry_price) * position.quantity
        if position.side == 'short':
            pnl = -pnl

        position.is_open = False
        await self._save_positions_to_redis()

        logger.info(f"📉 Posizione CHIUSA: {symbol} | PnL: {pnl:.2f} USDT | Motivo: {reason}")
        notifier.notify_position_closed(symbol, position.side, position.entry_price, price, pnl, reason)
        self._save_trade_to_file(symbol, position.side, position.entry_price, price, pnl, reason)

    def _save_trade_to_file(self, symbol: str, side: str, entry: float, exit_price: float, pnl: float, reason: str):
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
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            df = pd.read_csv(filename)
            df = pd.concat([df, new_row], ignore_index=True)
        else:
            df = new_row
        df.to_csv(filename, index=False)

    async def _place_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> bool:
        logger.info(f"📊 ORDINE LIMITE: {side.upper()} {quantity:.4f} {symbol} @ {price:.2f}")
        return True

    async def _place_close_order(self, symbol: str, side: str, quantity: float) -> bool:
        close_side = 'SELL' if side == 'long' else 'BUY'
        logger.info(f"📊 ORDINE CHIUSURA: {close_side} {quantity:.4f} {symbol}")
        return True

    async def _position_monitor(self):
        while self.running:
            await asyncio.sleep(5)
            now = datetime.now(timezone.utc)
            for symbol, position in list(self.positions.items()):
                if not position.is_open:
                    continue
                if symbol in self.latest_prices:
                    price = self.latest_prices[symbol]
                    if position.side == 'long':
                        pnl = (price - position.entry_price) * position.quantity
                    else:
                        pnl = (position.entry_price - price) * position.quantity
                    position.pnl = pnl

                entry_time = position.entry_time
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)
                holding_minutes = (now - entry_time).total_seconds() / 60
                if holding_minutes >= self.max_holding_minutes:
                    logger.info(
                        f"⏰ {symbol}: durata massima di holding superata "
                        f"({holding_minutes:.1f} min >= {self.max_holding_minutes} min), chiusura"
                    )
                    await self._close_position(symbol, reason="MAX_HOLDING")

            await self.redis.set('heartbeat_engine', datetime.now(timezone.utc).isoformat())

    async def _close_all_positions(self, reason: str):
        for symbol in list(self.positions.keys()):
            if self.positions[symbol].is_open:
                await self._close_position(symbol, reason=reason)

    async def _redis_listener(self):
        pubsub = await self.redis.subscribe('ml_signals')
        await self.redis.subscribe('sentiment_update')
        await self.redis.subscribe('config_updated')
        await self.redis.subscribe('sentiment_asset')
        await self.redis.subscribe('engine_commands')

        try:
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    channel = message['channel']
                    data = message['data']
                    if isinstance(data, bytes):
                        data = data.decode('utf-8')

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
                    elif channel == 'sentiment_asset':
                        try:
                            asset_data = json.loads(data)
                            self.sentiment_by_asset = {
                                'BTCUSDT': asset_data.get('BTC', 0.0),
                                'ETHUSDT': asset_data.get('ETH', 0.0),
                                'SOLUSDT': asset_data.get('SOL', 0.0)
                            }
                            logger.info(f"🧠 Sentiment per asset: BTC={self.sentiment_by_asset.get('BTCUSDT', 0):.2f}, ETH={self.sentiment_by_asset.get('ETHUSDT', 0):.2f}, SOL={self.sentiment_by_asset.get('SOLUSDT', 0):.2f}")
                        except Exception as e:
                            logger.error(f"❌ Errore parsing sentiment asset: {e}")
                    elif channel == 'config_updated':
                        logger.info("🔄 Configurazione aggiornata, ricarico...")
                        await self._load_config_from_redis()
                    elif channel == 'engine_commands':
                        try:
                            command = json.loads(data)
                            if command.get('action') == 'close_all':
                                reason = command.get('reason', 'MANUAL_RESET')
                                logger.warning(f"⛔ Comando ricevuto: chiusura di tutte le posizioni ({reason})")
                                await self._close_all_positions(reason)
                        except Exception as e:
                            logger.error(f"❌ Errore comando engine: {e}")
        except Exception as e:
            logger.error(f"❌ Errore Redis listener: {e}")
            if self.running:
                await asyncio.sleep(5)
                asyncio.create_task(self._redis_listener())

    async def _on_signal(self, signal: Signal):
        logger.info(f"📊 Segnale ricevuto: {signal.action} per {signal.symbol} (conf: {signal.confidence:.2f})")

        if signal.source == 'ml':
            asset_sentiment = self.sentiment_by_asset.get(signal.symbol, self.sentiment_score)
            weighted_confidence = (1 - self.sentiment_weight) * signal.confidence + self.sentiment_weight * abs(asset_sentiment)

            if asset_sentiment < -0.5 and signal.action == 'buy':
                logger.warning(f"⚠️ Sentiment negativo forte per {signal.symbol} → segnale BUY filtrato")
                return

            if weighted_confidence < self.ml_confidence_threshold:
                logger.info(f"ℹ️ Confidenza bassa ({weighted_confidence:.2f}) → segnale ignorato")
                return

            # --- REVERSE TRADING ---
            if self.reverse_trading_enabled:
                if signal.symbol in self.positions and self.positions[signal.symbol].is_open:
                    current_pos = self.positions[signal.symbol]
                    if (signal.action == 'buy' and current_pos.side == 'short') or (signal.action == 'sell' and current_pos.side == 'long'):
                        logger.info(f"🔄 Reverse trading: chiusura posizione {signal.symbol} {current_pos.side} per segnale opposto")
                        await self._close_position(signal.symbol, reason="REVERSE_SIGNAL")
                        await asyncio.sleep(0.5)

            if signal.action == 'buy':
                await self._open_position(signal)
            elif signal.action == 'sell':
                await self._open_position(signal)
            elif signal.action == 'close':
                await self._close_position(signal.symbol, reason="ML_SIGNAL")

    def stop(self):
        self.running = False
        if self.ws_task:
            self.ws_task.cancel()

    async def run(self):
        await self.initialize()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop)
        while self.running:
            await asyncio.sleep(0.1)
        logger.info("🛑 Trading Engine fermato")


if __name__ == "__main__":
    logger.add("logs/trading_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days", level="INFO")

    engine = TradingEngine()
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        engine.stop()
        logger.info("🛑 Interruzione manuale")
    except Exception as e:
        logger.error(f"❌ Errore critico: {e}")
        sys.exit(1)
