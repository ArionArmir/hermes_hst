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
from src.shared.candle_feed import CandleFeed
from src.shared.throttle import WriteThrottle
from src.shared import store
from src.exit_model import ATRExitModel
from src.exit_model.profiles import build_exit_model as _build_exit_model
from src.volume_pattern import VolumePatternAnalyzer


class TradingEngine:
    def __init__(self):
        self.redis: Optional[RedisClient] = None
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.ws_task: Optional[asyncio.Task] = None
        self.running: bool = True

        self.positions: Dict[str, Position] = {}
        self.latest_prices: Dict[str, float] = {}
        # Ultima chiusura per simbolo (solo in memoria: dopo un riavvio il
        # cooldown di ri-ingresso riparte pulito, scelta accettabile)
        self.last_close_time: Dict[str, datetime] = {}
        self.sentiment_score: float = 0.0
        self.sentiment_by_asset: Dict[str, float] = {}
        self.capital: float = 1000.0
        self.open_orders: Dict[str, dict] = {}

        self.config: Optional[Config] = None
        self.config_version: int = 0
        self._config_reload_lock = asyncio.Lock()

        self.ws_url = "wss://fstream.binance.com/stream"
        # Persistenza dei tick limitata (0.5s/chiave): lo stato in memoria
        # resta per-tick, Redis no — vedi src/shared/throttle.py
        self._tick_throttle = WriteThrottle(interval_seconds=0.5)
        self._listener_backoff_seconds = 5
        self.symbols = ["btcusdt", "ethusdt", "solusdt"]

        self.leverage = 3
        self.stop_loss_pct = 0.025
        self.take_profit_pct = 0.04
        self.max_position_usdt = 200.0
        self.trailing_stop_pct = 0.015
        self.max_exposure = 0.5
        self.taker_fee_pct = 0.0005
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
        self.reverse_cooldown_minutes = 15
        self.reverse_confidence_margin = 0.05
        self.entry_cooldown_minutes = 60

        # Bootstrap REST dei modelli a candele: bastano ~30 candele per
        # scaldare ATR (finestra 14) e pattern (finestra 10) all'avvio,
        # poi restano aggiornati dallo stream WebSocket @kline.
        self.candle_feed = CandleFeed(interval="1h", limit=30)

    async def initialize(self):
        logger.info("🚀 Avvio Trading Engine...")
        self.redis = RedisClient()
        await self.redis.connect()
        await self._load_config_from_redis()
        await self._bootstrap_candle_models()
        await self._load_positions_from_redis()
        await self._load_capital_from_redis()

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
        self.taker_fee_pct = config.taker_fee_pct
        self.symbols = [s.lower() for s in config.symbols]
        for symbol in self.symbols:
            symbol_upper = symbol.upper()
            if symbol_upper not in self.exit_models:
                self.exit_models[symbol_upper] = _build_exit_model(symbol_upper)
            if symbol_upper not in self.pattern_models:
                self.pattern_models[symbol_upper] = VolumePatternAnalyzer(window=10)
        if config.timeframe != self.candle_feed.interval:
            logger.info(f"🕐 Timeframe candele: {self.candle_feed.interval} → {config.timeframe}")
            self.candle_feed = CandleFeed(interval=config.timeframe, limit=30)
        self.ml_confidence_threshold = config.ml_confidence_threshold
        self.sentiment_weight = config.sentiment_weight
        self.reverse_trading_enabled = config.reverse_trading_enabled
        self.pattern_confirmation_enabled = config.pattern_confirmation_enabled
        self.dynamic_exit_enabled = config.dynamic_exit_enabled
        self.max_holding_minutes = config.max_holding_minutes
        self.reverse_cooldown_minutes = config.reverse_cooldown_minutes
        self.reverse_confidence_margin = config.reverse_confidence_margin
        self.entry_cooldown_minutes = config.entry_cooldown_minutes
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

    async def _load_capital_from_redis(self):
        """Il capitale paper sopravvive ai riavvii: senza persistenza ogni
        restart lo riporterebbe a 1000 falsando l'equity e il cap di
        esposizione."""
        value = await self.redis.get('capital')
        if value:
            try:
                self.capital = float(value)
                logger.info(f"💰 Capitale caricato da Redis: {self.capital:.2f} USDT")
            except ValueError:
                logger.warning(f"⚠️ Valore capitale non valido su Redis, resto a {self.capital:.2f}: {value!r}")

    def _margin_in_use(self) -> float:
        """Margine impegnato dalle posizioni aperte (nozionale/leva), al
        prezzo corrente quando disponibile."""
        total = 0.0
        for sym, pos in self.positions.items():
            if not pos.is_open:
                continue
            price = self.latest_prices.get(sym, pos.entry_price)
            total += (pos.quantity * price) / max(pos.leverage, 1)
        return total

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

    def _ingest_candle(self, symbol_upper: str, close: float, high: float, low: float, volume: float):
        """Unico punto di alimentazione di ATRExitModel e VolumePatternAnalyzer:
        SOLO candele reali (REST bootstrap o kline WebSocket chiuse), mai tick
        con high/low sintetici — l'ATR su range inventati era il difetto S6
        di docs/IMPROVEMENT_PLAN.md."""
        exit_model = self.exit_models.get(symbol_upper)
        pattern_model = self.pattern_models.get(symbol_upper)
        if exit_model:
            exit_model.add_price(close, high, low)
        if pattern_model:
            pattern_model.add_data(close, volume, high, low)

    def _candle_models_warm(self, symbol_upper: str) -> bool:
        exit_model = self.exit_models.get(symbol_upper)
        return exit_model is not None and len(exit_model.prices) > exit_model.window

    async def _bootstrap_candle_models(self):
        """Scalda ATR e pattern con le ultime candele chiuse via REST: senza
        bootstrap, dopo un riavvio servirebbero ~15 candele (15 ore a 1h)
        prima di avere un ATR reale. Idempotente: salta i simboli già caldi,
        quindi è richiamabile anche dopo un reload di config che aggiunge
        simboli."""
        for symbol in self.symbols:
            symbol_upper = symbol.upper()
            if self._candle_models_warm(symbol_upper):
                continue
            candles = await self.candle_feed.get_candles(symbol_upper)
            if candles is None or candles.empty:
                logger.warning(f"⚠️ Bootstrap candele fallito per {symbol_upper}: ATR in warmup dallo stream")
                continue
            for row in candles.itertuples():
                self._ingest_candle(symbol_upper, row.close, row.high, row.low, row.volume)
            logger.info(f"🕯️ {symbol_upper}: modelli ATR/pattern inizializzati con {len(candles)} candele reali")

    def _on_kline(self, kline: dict):
        """Gestisce un evento kline del WebSocket. Solo le candele chiuse
        (x=true) alimentano i modelli: quella in formazione cambia a ogni
        tick e produrrebbe range parziali."""
        if not kline.get('x'):
            return
        try:
            self._ingest_candle(
                kline['s'],
                float(kline['c']),
                float(kline['h']),
                float(kline['l']),
                float(kline['v']),
            )
        except (KeyError, ValueError) as e:
            logger.error(f"❌ Kline malformata: {e}")

    async def _websocket_loop(self):
        while self.running:
            try:
                logger.info("🔌 Connessione WebSocket Binance...")
                kline_interval = self.config.timeframe if self.config else "1h"
                stream_names = [f"{symbol}@trade" for symbol in self.symbols]
                stream_names += [f"{symbol}@kline_{kline_interval}" for symbol in self.symbols]
                stream_url = f"{self.ws_url}?streams={'/'.join(stream_names)}"

                async with websockets.connect(stream_url, ping_interval=30, ping_timeout=10) as self.ws:
                    logger.info("✅ WebSocket connesso")

                    while self.running:
                        try:
                            msg = await self.ws.recv()
                            data = json.loads(msg)
                            if 'data' in data:
                                stream = data.get('stream', '')
                                if '@kline' in stream:
                                    self._on_kline(data['data'].get('k') or {})
                                    continue

                                trade = data['data']
                                symbol = stream.replace('@trade', '').upper()
                                price = float(trade['p'])
                                volume = float(trade['q'])
                                if price <= 0 or volume <= 0:
                                    continue

                                exit_model = self.exit_models.get(symbol)
                                self.latest_prices[symbol] = price
                                if self._tick_throttle.ready(f"latest_price_{symbol}"):
                                    await self.redis.set(f"latest_price_{symbol}", str(price))
                                if self._tick_throttle.ready("last_tick_engine"):
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

    async def _open_position(self, signal: Signal, weighted_confidence: float = None,
                             opened_outcome: str = "OPENED"):
        symbol = signal.symbol
        if symbol in self.positions and self.positions[symbol].is_open:
            logger.warning(f"⚠️ Posizione già aperta su {symbol}")
            self._record_signal(signal, "ALREADY_OPEN", weighted_confidence)
            return

        price = self.latest_prices.get(symbol, 0)
        if price <= 0:
            price = await self._get_price_rest(symbol)
            if price <= 0:
                logger.warning(f"⚠️ Prezzo non disponibile per {symbol}")
                self._record_signal(signal, "NO_PRICE", weighted_confidence)
                return

        exit_model = self.exit_models.get(symbol)
        side = 'long' if signal.action == 'buy' else 'short'

        if self.pattern_confirmation_enabled:
            pattern_model = self.pattern_models.get(symbol)
            if pattern_model:
                pattern_result = pattern_model.analyze()
                if pattern_result["signal"] == "REJECT":
                    logger.warning(f"⛔ Segnale rifiutato da Pattern Analyzer: {pattern_result['reason']}")
                    self._record_signal(signal, "PATTERN_REJECT", weighted_confidence,
                                        detail=pattern_result["reason"])
                    return
                elif pattern_result["signal"] == "NEUTRAL":
                    logger.info(f"ℹ️ Pattern neutro: {pattern_result['reason']}")

        leverage = self.leverage
        position_size = min(self.max_position_usdt * leverage, self.capital * self.max_exposure)
        if position_size <= 0:
            logger.warning("⚠️ Capitale insufficiente per aprire posizione")
            self._record_signal(signal, "NO_CAPITAL", weighted_confidence)
            return

        # Cap a livello PORTAFOGLIO: il sizing per-simbolo da solo permetteva
        # di impegnare più del capitale sommando simboli correlati
        # (docs/IMPROVEMENT_PLAN.md, S5).
        margin_required = position_size / max(leverage, 1)
        margin_in_use = self._margin_in_use()
        margin_cap = self.capital * self.max_exposure
        if margin_in_use + margin_required > margin_cap:
            logger.warning(
                f"⛔ Cap esposizione portafoglio: margine in uso {margin_in_use:.2f} + richiesto "
                f"{margin_required:.2f} > {margin_cap:.2f} USDT ({self.max_exposure:.0%} di {self.capital:.2f}) "
                f"→ {symbol} non aperto"
            )
            self._record_signal(signal, "EXPOSURE_CAP", weighted_confidence,
                                detail=f"margine {margin_in_use:.0f}+{margin_required:.0f} > cap {margin_cap:.0f}")
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
        self._record_signal(signal, opened_outcome, weighted_confidence,
                            detail=f"{side} {quantity:.4f} @ {price:.4f}")
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

        pnl_gross = (price - position.entry_price) * position.quantity
        if position.side == 'short':
            pnl_gross = -pnl_gross

        # Fee taker su entrambi i lati: senza, il paper trading sovrastima il
        # PnL proprio dove la strategia rischia di più (turnover del reverse).
        fees = (position.entry_price + price) * position.quantity * self.taker_fee_pct
        pnl = pnl_gross - fees

        position.is_open = False
        self.capital += pnl
        self.last_close_time[symbol] = datetime.now(timezone.utc)
        await self._save_positions_to_redis()
        await self.redis.set('capital', str(self.capital))

        logger.info(
            f"📉 Posizione CHIUSA: {symbol} | PnL: {pnl:.2f} USDT "
            f"(lordo {pnl_gross:.2f}, fee {fees:.2f}) | Capitale: {self.capital:.2f} | Motivo: {reason}"
        )
        notifier.notify_position_closed(symbol, position.side, position.entry_price, price, pnl, reason)
        self._save_trade_to_file(symbol, position.side, position.entry_price, price, pnl, reason,
                                 pnl_gross=pnl_gross, fees=fees)

    def _save_trade_to_file(self, symbol: str, side: str, entry: float, exit_price: float, pnl: float, reason: str,
                            pnl_gross: float = None, fees: float = 0.0):
        import os
        timestamp = datetime.now(timezone.utc).isoformat()

        # SQLite: fonte di verità interrogabile (dashboard, analisi)
        try:
            store.insert_trade(
                symbol=symbol, side=side, entry=entry, exit_price=exit_price,
                pnl=pnl, reason=reason, pnl_gross=pnl_gross, fees=fees,
                capital_after=self.capital, timestamp=timestamp,
            )
        except Exception as e:
            logger.error(f"❌ Persistenza trade su SQLite fallita: {e}")

        # Export CSV in append puro (il vecchio leggi-tutto+riscrivi era
        # O(n²) e corruttibile se il processo moriva a metà scrittura),
        # mantenuto per verify_overnight e compatibilità
        filename = "data/trades_history.csv"
        try:
            os.makedirs("data", exist_ok=True)
            is_new = not os.path.exists(filename) or os.path.getsize(filename) == 0
            with open(filename, "a") as f:
                if is_new:
                    f.write("timestamp,symbol,side,entry,exit,pnl,reason,pnl_gross,fees,capital_after\n")
                f.write(f"{timestamp},{symbol},{side},{entry},{exit_price},{pnl},{reason},"
                        f"{pnl_gross if pnl_gross is not None else pnl},{fees},{self.capital}\n")
        except Exception as e:
            logger.error(f"❌ Scrittura CSV trade fallita: {e}")

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

    LISTENER_CHANNELS = ('ml_signals', 'sentiment_update', 'config_updated',
                         'sentiment_asset', 'engine_commands')

    async def _redis_listener(self):
        """Loop con recovery: a ogni errore si ri-sottoscrive con un pubsub
        NUOVO (il vecchio schema ricreava il task riusando lo stesso pubsub,
        che dopo un errore di connessione può essere irrecuperabile → il
        listener moriva in silenzio e l'engine smetteva di ricevere segnali)."""
        while self.running:
            try:
                pubsub = await self.redis.subscribe_fresh(*self.LISTENER_CHANNELS)
                logger.info(f"👂 Listener Redis attivo su: {', '.join(self.LISTENER_CHANNELS)}")
                async for message in pubsub.listen():
                    if message['type'] != 'message':
                        continue
                    data = message['data']
                    if isinstance(data, bytes):
                        data = data.decode('utf-8')
                    channel = message['channel']
                    if isinstance(channel, bytes):
                        channel = channel.decode('utf-8')
                    await self._handle_channel_message(channel, data)
                    if not self.running:
                        break
            except Exception as e:
                logger.error(f"❌ Errore Redis listener: {e}")
                if self.running:
                    await asyncio.sleep(self._listener_backoff_seconds)

    async def _handle_channel_message(self, channel: str, data: str):
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
                self._on_sentiment_asset(data)
            except Exception as e:
                logger.error(f"❌ Errore parsing sentiment asset: {e}")
        elif channel == 'config_updated':
            logger.info("🔄 Configurazione aggiornata, ricarico...")
            await self._load_config_from_redis()
            # Simboli eventualmente aggiunti partono con ATR/pattern
            # già caldi (idempotente: i simboli già caldi vengono saltati)
            await self._bootstrap_candle_models()
        elif channel == 'engine_commands':
            try:
                command = json.loads(data)
                if command.get('action') == 'close_all':
                    reason = command.get('reason', 'MANUAL_RESET')
                    logger.warning(f"⛔ Comando ricevuto: chiusura di tutte le posizioni ({reason})")
                    await self._close_all_positions(reason)
            except Exception as e:
                logger.error(f"❌ Errore comando engine: {e}")

    def _record_signal(self, signal: Signal, outcome: str,
                       weighted_confidence: float = None, detail: str = ""):
        """Registra ogni decisione su un segnale nella tabella SQLite
        `signals` (anche gli scarti e il perché): è la risposta a "perché il
        bot non sta tradando?" senza grep nei log. MAI bloccante: un errore
        di persistenza non deve fermare il trading."""
        try:
            store.insert_signal(
                symbol=signal.symbol, action=signal.action, outcome=outcome,
                confidence=signal.confidence, weighted_confidence=weighted_confidence,
                detail=detail,
            )
        except Exception as e:
            logger.error(f"❌ Persistenza segnale fallita: {e}")

    def _on_sentiment_asset(self, payload: str):
        """Mappa dinamica asset→simbolo ({'BTC': 0.2, …} → {'BTCUSDT': 0.2}):
        qualunque asset pubblicato dal processo sentiment viene usato, niente
        più lista hardcoded BTC/ETH/SOL (tutte le coppie del progetto quotano
        in USDT)."""
        asset_data = json.loads(payload)
        self.sentiment_by_asset = {
            f"{asset.upper()}USDT": float(score)
            for asset, score in asset_data.items()
            if asset.lower() != 'aggregate' and isinstance(score, (int, float))
        }
        if self.sentiment_by_asset:
            summary = ", ".join(f"{sym[:-4]}={score:+.2f}" for sym, score in self.sentiment_by_asset.items())
            logger.info(f"🧠 Sentiment per asset: {summary}")

    async def _on_signal(self, signal: Signal):
        logger.info(f"📊 Segnale ricevuto: {signal.action} per {signal.symbol} (conf: {signal.confidence:.2f})")

        if signal.source == 'ml':
            if signal.action == 'close':
                self._record_signal(signal, "CLOSE")
                await self._close_position(signal.symbol, reason="ML_SIGNAL")
                return

            asset_sentiment = self.sentiment_by_asset.get(signal.symbol, self.sentiment_score)
            direction = 1.0 if signal.action == 'buy' else -1.0
            directional_sentiment = direction * asset_sentiment  # > 0 = favorevole al trade

            # Veto SIMMETRICO: un sentiment fortemente contrario blocca sia i
            # buy (sentiment molto negativo) sia i sell (molto positivo) — il
            # vecchio filtro copriva solo il lato buy.
            if directional_sentiment < -0.5:
                logger.warning(
                    f"⚠️ Sentiment fortemente contrario ({asset_sentiment:+.2f}) per {signal.symbol} "
                    f"→ segnale {signal.action.upper()} filtrato"
                )
                self._record_signal(signal, "SENTIMENT_VETO",
                                    detail=f"sentiment {asset_sentiment:+.2f}")
                return

            # Il sentiment contribuisce alla confidenza solo se FAVOREVOLE
            # alla direzione: il vecchio abs() aumentava la confidenza anche
            # con sentiment opposto al trade (docs/IMPROVEMENT_PLAN.md, S4).
            weighted_confidence = (
                (1 - self.sentiment_weight) * signal.confidence
                + self.sentiment_weight * max(0.0, directional_sentiment)
            )

            if weighted_confidence < self.ml_confidence_threshold:
                logger.info(f"ℹ️ Confidenza bassa ({weighted_confidence:.2f}) → segnale ignorato")
                self._record_signal(signal, "LOW_CONFIDENCE", weighted_confidence,
                                    detail=f"soglia {self.ml_confidence_threshold}")
                return

            now = datetime.now(timezone.utc)
            current_pos = self.positions.get(signal.symbol)
            has_open = current_pos is not None and current_pos.is_open

            # --- COOLDOWN DI RI-INGRESSO ---
            # Le feature cambiano solo a candela chiusa: senza questo blocco,
            # lo stesso segnale riaprirebbe una posizione appena chiusa (es.
            # in stop loss) entro pochi secondi, ripagando fee e slippage.
            if not has_open and signal.action in ('buy', 'sell'):
                last_close = self.last_close_time.get(signal.symbol)
                if last_close is not None:
                    minutes_since_close = (now - last_close).total_seconds() / 60
                    if minutes_since_close < self.entry_cooldown_minutes:
                        logger.info(
                            f"⏳ Cooldown ri-ingresso {signal.symbol}: chiuso {minutes_since_close:.1f} min fa "
                            f"(< {self.entry_cooldown_minutes} min) → segnale ignorato"
                        )
                        self._record_signal(signal, "ENTRY_COOLDOWN", weighted_confidence,
                                            detail=f"chiuso {minutes_since_close:.1f} min fa")
                        return

            # --- REVERSE TRADING (con cooldown e isteresi) ---
            if self.reverse_trading_enabled and has_open:
                if (signal.action == 'buy' and current_pos.side == 'short') or (signal.action == 'sell' and current_pos.side == 'long'):
                    entry_time = current_pos.entry_time
                    if entry_time.tzinfo is None:
                        entry_time = entry_time.replace(tzinfo=timezone.utc)
                    held_minutes = (now - entry_time).total_seconds() / 60
                    if held_minutes < self.reverse_cooldown_minutes:
                        logger.info(
                            f"⏳ Reverse ignorato per {signal.symbol}: posizione aperta da "
                            f"{held_minutes:.1f} min (< {self.reverse_cooldown_minutes} min)"
                        )
                        self._record_signal(signal, "REVERSE_COOLDOWN", weighted_confidence,
                                            detail=f"posizione aperta da {held_minutes:.1f} min")
                        return
                    # Isteresi: per INVERTIRE serve più convinzione che per
                    # entrare, altrimenti prob oscillanti intorno alla soglia
                    # producono flip-flop che paga fee a ogni giro.
                    reverse_threshold = self.ml_confidence_threshold + self.reverse_confidence_margin
                    if weighted_confidence < reverse_threshold:
                        logger.info(
                            f"⏳ Reverse ignorato per {signal.symbol}: confidenza {weighted_confidence:.2f} "
                            f"sotto la soglia con isteresi ({reverse_threshold:.2f})"
                        )
                        self._record_signal(signal, "REVERSE_HYSTERESIS", weighted_confidence,
                                            detail=f"soglia con isteresi {reverse_threshold:.2f}")
                        return
                    logger.info(f"🔄 Reverse trading: chiusura posizione {signal.symbol} {current_pos.side} per segnale opposto")
                    await self._close_position(signal.symbol, reason="REVERSE_SIGNAL")
                    await asyncio.sleep(0.5)
                    did_reverse = True
                else:
                    did_reverse = False
            else:
                did_reverse = False

            if signal.action in ('buy', 'sell'):
                await self._open_position(
                    signal,
                    weighted_confidence=weighted_confidence,
                    opened_outcome="REVERSED" if did_reverse else "OPENED",
                )

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
