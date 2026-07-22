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
from src.shared.circuit_breaker import CircuitBreaker, CircuitBreakerParams
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
        # Età dell'ultimo tick per simbolo: oltre prezzo_max_eta_sec il prezzo è
        # "morto" (stream silente ma TCP vivo, nessun reconnect) e il backstop
        # SL/TP deve ripiegare sul REST invece di usare un prezzo congelato.
        self.latest_price_ts: Dict[str, datetime] = {}
        self.prezzo_max_eta_sec = 60
        self._richiedi_riconnessione_ws = False   # E10: cambio timeframe → riconnetti
        # Ultima chiusura per simbolo (solo in memoria: dopo un riavvio il
        # cooldown di ri-ingresso riparte pulito, scelta accettabile)
        self.last_close_time: Dict[str, datetime] = {}
        self.sentiment_score: float = 0.0
        self.sentiment_by_asset: Dict[str, float] = {}
        self.capital: float = 1000.0

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
        self.max_positions_same_direction = 3
        self.taker_fee_pct = 0.0005
        self.ml_confidence_threshold = 0.55
        self.sentiment_weight = 0.3

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

        self.circuit_breaker_enabled = True
        self.circuit_breaker = CircuitBreaker()

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
        await self._seed_circuit_breaker()

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
                    if self.config is None:
                        self._apply_config(Config())
        except Exception as e:
            # revisione 2026-07-22: MAI regredire ai default hardcoded (leva 3,
            # soglia 0.55, 3 simboli) sovrascrivendo una config buona già in
            # memoria. Un hiccup di Redis durante un reload (config_updated /
            # _resync_dopo_subscribe) replicherebbe la deriva soglia sul MOTORE
            # live — stesso fix I2 già in src/inference/main.py, qui mancante.
            logger.error(f"❌ Errore caricamento config: {e}"
                         + (" — tengo la config corrente" if self.config else
                            " — nessuna config precedente, uso i default"))
            if self.config is None:
                self._apply_config(Config())

    def _apply_config(self, config: Config):
        self.leverage = config.leverage
        self.stop_loss_pct = config.stop_loss_pct
        self.take_profit_pct = config.take_profit_pct
        self.max_position_usdt = config.max_position_size_usdt
        self.trailing_stop_pct = config.trailing_stop_pct
        self.max_exposure = config.max_exposure
        self.max_positions_same_direction = config.max_positions_same_direction
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
            # revisione 2026-07-21 (E10): svuota i modelli ATR/pattern — la loro
            # finestra conterrebbe candele del vecchio intervallo, dando SL/TP
            # su una volatilità mista senza senso finché non si svuota da sola.
            # E richiedi la riconnessione del WS (che manda ancora kline del
            # vecchio intervallo fino alla prossima disconnessione).
            self.exit_models.clear()
            self.pattern_models.clear()
            self._richiedi_riconnessione_ws = True
            for symbol in self.symbols:
                self.exit_models[symbol.upper()] = _build_exit_model(symbol.upper())
                self.pattern_models[symbol.upper()] = VolumePatternAnalyzer(window=10)
        self.ml_confidence_threshold = config.ml_confidence_threshold
        self.sentiment_weight = config.sentiment_weight
        self.reverse_trading_enabled = config.reverse_trading_enabled
        self.pattern_confirmation_enabled = config.pattern_confirmation_enabled
        self.dynamic_exit_enabled = config.dynamic_exit_enabled
        self.max_holding_minutes = config.max_holding_minutes
        self.reverse_cooldown_minutes = config.reverse_cooldown_minutes
        self.reverse_confidence_margin = config.reverse_confidence_margin
        self.entry_cooldown_minutes = config.entry_cooldown_minutes
        self.circuit_breaker_enabled = config.circuit_breaker_enabled
        self.circuit_breaker.update_params(CircuitBreakerParams(
            max_consecutive_losses=config.circuit_breaker_max_consecutive_losses,
            consecutive_loss_cooldown_minutes=config.circuit_breaker_cooldown_minutes,
            max_daily_loss_pct=config.circuit_breaker_max_daily_loss_pct,
            max_drawdown_pct=config.circuit_breaker_max_drawdown_pct,
        ))
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
        """Il capitale paper sopravvive ai riavvii. Fonte di verità: l'ultimo
        capital_after del log SQLite (E5), che è durevole e coerente col
        breaker; Redis è solo cache. Se divergono, vince SQLite e lo si
        segnala — la divergenza è la firma di un crash tra insert e update
        Redis."""
        da_db = None
        try:
            trades = store.read_trades(limit=1)
            if len(trades):
                valore = trades.iloc[0].get("capital_after")
                if valore is not None and valore == valore:      # non-NaN senza pandas
                    da_db = float(valore)
        except Exception as e:
            logger.warning(f"⚠️ capital_after da SQLite non leggibile: {e}")

        value = await self.redis.get('capital')
        da_redis = None
        if value:
            try:
                da_redis = float(value)
            except ValueError:
                logger.warning(f"⚠️ Valore capitale non valido su Redis: {value!r}")

        if da_db is not None:
            self.capital = da_db
            if da_redis is not None and abs(da_redis - da_db) > 0.01:
                logger.warning(f"⚠️ Capitale Redis {da_redis:.2f} ≠ SQLite {da_db:.2f}: "
                               "uso SQLite (crash tra insert e update?), riallineo Redis")
                await self.redis.set('capital', str(self.capital))
            else:
                logger.info(f"💰 Capitale da SQLite: {self.capital:.2f} USDT")
        elif da_redis is not None:
            self.capital = da_redis
            logger.info(f"💰 Capitale da Redis (nessun trade a DB): {self.capital:.2f} USDT")

    async def _seed_circuit_breaker(self):
        """Ricostruisce lo stato del circuit breaker dallo storico dei trade
        (data/hermes.db): senza, un crash durante una serie di perdite (con
        systemd Restart=always) azzererebbe il contatore a ogni riavvio,
        vanificando la protezione proprio quando serve di più."""
        try:
            trades_df = store.read_trades(limit=200)
        except Exception as e:
            logger.warning(f"⚠️ Impossibile leggere lo storico trade per il circuit breaker: {e}")
            trades_df = None
        reset_after = reset_capital = None
        raw = await self.redis.get('circuit_breaker_reset')
        if raw:
            try:
                r = json.loads(raw)
                reset_after, reset_capital = r.get("ts"), r.get("capital")
            except (json.JSONDecodeError, AttributeError):
                pass
        try:
            self.circuit_breaker.seed_from_history(trades_df, self.capital,
                                                   reset_after=reset_after, reset_capital=reset_capital)
        except Exception as e:
            logger.warning(f"⚠️ Seed del circuit breaker fallito, parto senza stato ricostruito: {e}")
        status = self.circuit_breaker.status()
        if status["tripped"]:
            logger.warning(f"⛔ Circuit breaker già attivo dopo il riavvio: {status['reason']}")

    def _count_open_positions(self, side: str) -> int:
        return sum(1 for pos in self.positions.values() if pos.is_open and pos.side == side)

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

                self._richiedi_riconnessione_ws = False
                async with websockets.connect(stream_url, ping_interval=30, ping_timeout=10) as self.ws:
                    logger.info("✅ WebSocket connesso")

                    while self.running:
                        # E10: un cambio di timeframe/simboli richiede di
                        # riconnettere con i nuovi stream — usciamo dall'inner
                        # loop e l'outer riconnette con self.symbols aggiornati
                        if self._richiedi_riconnessione_ws:
                            logger.info("🔄 Riconnessione WS richiesta (cambio config)")
                            break
                        try:
                            try:
                                msg = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
                            except asyncio.TimeoutError:
                                continue          # nessun tick: torna a controllare i flag
                                # (connessione viva, il ping_interval la tiene su)
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
                                self.latest_price_ts[symbol] = datetime.now(timezone.utc)
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

        # revisione 2026-07-21 (E4): il gate del breaker è verificato una volta
        # a inizio segnale, ma la chiusura del reverse (o un altro simbolo che
        # chiude durante gli await) può averlo fatto scattare nel frattempo.
        # Ricontrolliamo qui, l'ultimo istante prima di impegnare capitale.
        if self.circuit_breaker_enabled and self.circuit_breaker.is_tripped():
            logger.warning(f"⛔ Circuit breaker scattato durante l'apertura: {symbol} non aperto")
            self._record_signal(signal, "CIRCUIT_BREAKER", weighted_confidence,
                                detail=self.circuit_breaker.status().get("reason"))
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

        # Cap sul NUMERO di posizioni simultanee nella stessa direzione: con
        # crypto correlate, max_exposure da solo non impedisce che 6-7
        # simboli aprano tutti long insieme (docs/IMPROVEMENT_PLAN.md,
        # scoperto con walk_forward.py). Controllato PRIMA del pattern e del
        # cap di margine: è il vincolo più economico da valutare.
        same_direction_count = self._count_open_positions(side)
        if same_direction_count >= self.max_positions_same_direction:
            logger.warning(
                f"⛔ Cap direzionale: {same_direction_count} posizioni {side} già aperte "
                f"(max {self.max_positions_same_direction}) → {symbol} non aperto"
            )
            self._record_signal(signal, "DIRECTION_CAP", weighted_confidence,
                                detail=f"{same_direction_count} posizioni {side} già aperte")
            return

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
        # revisione 2026-07-21 (E9): con asset ad alto prezzo e capitale ridotto
        # l'arrotondamento a 3 decimali può dare 0.0 → posizione zombie che
        # occupa il simbolo con PnL identicamente 0 fino a MAX_HOLDING.
        if quantity <= 0:
            logger.warning(f"⚠️ Quantità arrotondata a 0 per {symbol} "
                           f"(size {position_size:.2f} / prezzo {price:.2f}): non aperta")
            self._record_signal(signal, "QTY_ZERO", weighted_confidence,
                                detail=f"size {position_size:.2f} insufficiente a prezzo {price:.2f}")
            return

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
        # revisione 2026-07-21 (E8): notifica in un thread — requests/smtplib
        # sincroni nel percorso caldo congelavano l'intero loop asyncio (niente
        # tick, niente SL su TUTTI i simboli) a ogni hiccup di Telegram/SMTP.
        asyncio.create_task(asyncio.to_thread(
            notifier.notify_position_opened, symbol, side, price, quantity, stop_loss, take_profit))

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

        # revisione 2026-07-21 (E5): la scrittura durevole (SQLite) viene PRIMA
        # delle mutazioni di stato e della cache Redis. Redis e SQLite non
        # possono essere atomici tra loro; scegliamo SQLite come fonte di
        # verità e al riavvio riconciliamo il capitale dal log dei trade
        # (_load_capital: last capital_after) — così storia, capitale e seed
        # del breaker non divergono mai. Residuo noto e documentato
        # (docs/REVISIONE_2026-07-21.md): un crash nella finestra micrometrica
        # tra insert e update Redis può lasciare la posizione "aperta" in
        # cache; in paper trading si richiude al più con un trade duplicato.
        nuovo_capitale = self.capital + pnl
        # se la scrittura durevole fallisce (disco pieno, lock SQLite), la
        # chiusura si ABORTISCE prima di mutare stato/Redis (regressione della
        # prima passata: proseguire lasciava Redis avanti e il DB indietro, e
        # al riavvio la riconciliazione da DB faceva REGREDIRE il capitale).
        # La posizione resta aperta e si richiude al prossimo giro: stato
        # sempre coerente col log dei trade.
        if not self._save_trade_to_file(symbol, position.side, position.entry_price, price, pnl,
                                        reason, pnl_gross=pnl_gross, fees=fees,
                                        capital_after=nuovo_capitale):
            logger.critical(f"⛔ Chiusura {symbol} abortita: scrittura durevole fallita, "
                            "posizione lasciata aperta per il prossimo tentativo")
            return

        position.is_open = False
        self.capital = nuovo_capitale
        self.last_close_time[symbol] = datetime.now(timezone.utc)
        self.circuit_breaker.record_trade(pnl, self.capital)
        await self._save_positions_to_redis()
        await self.redis.set('capital', str(self.capital))

        logger.info(
            f"📉 Posizione CHIUSA: {symbol} | PnL: {pnl:.2f} USDT "
            f"(lordo {pnl_gross:.2f}, fee {fees:.2f}) | Capitale: {self.capital:.2f} | Motivo: {reason}"
        )
        asyncio.create_task(asyncio.to_thread(          # E8: fuori dal loop caldo
            notifier.notify_position_closed, symbol, position.side, position.entry_price, price, pnl, reason))

    def _save_trade_to_file(self, symbol: str, side: str, entry: float, exit_price: float, pnl: float, reason: str,
                            pnl_gross: float = None, fees: float = 0.0, capital_after: float = None) -> bool:
        """Ritorna True se la scrittura DUREVOLE (SQLite) è riuscita. Il
        chiamante aborta la chiusura se torna False (coerenza capitale/log)."""
        import os
        timestamp = datetime.now(timezone.utc).isoformat()
        # capital_after esplicito (E5): la scrittura durevole precede
        # l'aggiornamento di self.capital, quindi non possiamo leggerlo da lì
        capital_after = capital_after if capital_after is not None else self.capital

        # SQLite: fonte di verità interrogabile (dashboard, analisi)
        try:
            store.insert_trade(
                symbol=symbol, side=side, entry=entry, exit_price=exit_price,
                pnl=pnl, reason=reason, pnl_gross=pnl_gross, fees=fees,
                capital_after=capital_after, timestamp=timestamp,
            )
        except Exception as e:
            logger.error(f"❌ Persistenza trade su SQLite fallita: {e}")
            return False

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
                        f"{pnl_gross if pnl_gross is not None else pnl},{fees},{capital_after}\n")
        except Exception as e:
            logger.error(f"❌ Scrittura CSV trade fallita: {e}")
        return True                       # la durabilità è lo SQLite; il CSV è best-effort

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

                # revisione 2026-07-21 (E3): SL/TP di BACKSTOP anche qui, non
                # solo sui tick WebSocket. Un simbolo aggiunto a caldo o con lo
                # stream morto restava senza stop e veniva chiuso da MAX_HOLDING
                # al prezzo di entrata, nascondendo la perdita. Se il prezzo di
                # tick è stantio, lo prendiamo via REST.
                prezzo_uscita = self.latest_prices.get(symbol)
                eta_prezzo = self.latest_price_ts.get(symbol)
                prezzo_stantio = (
                    eta_prezzo is None
                    or (now - eta_prezzo).total_seconds() > self.prezzo_max_eta_sec
                )
                if prezzo_uscita is None or prezzo_stantio:
                    prezzo_uscita = await self._get_price_rest(symbol)
                if prezzo_uscita and prezzo_uscita > 0:
                    # revisione 2026-07-21 (E6): trailing statico REALE. Il
                    # campo trailing_stop era scritto e mai letto — chi
                    # disattivava dynamic_exit confidando nel trailing 1.5%
                    # non aveva alcun trailing. Qui il stop_loss avanza (mai
                    # indietro) col prezzo quando il dinamico è spento.
                    if self._ratchet_trailing_statico(symbol, prezzo_uscita):
                        await self._save_positions_to_redis()   # persiste lo stop ratchettato
                    await self._check_exit_conditions(symbol, prezzo_uscita)
                    if symbol not in self.positions or not self.positions[symbol].is_open:
                        continue          # chiusa dal backstop, non valutare MAX_HOLDING

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

            await self.redis.set('circuit_breaker_status', self.circuit_breaker.status())
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
        pubsub = None
        while self.running:
            try:
                pubsub = await self.redis.subscribe_fresh(*self.LISTENER_CHANNELS)
                logger.info(f"👂 Listener Redis attivo su: {', '.join(self.LISTENER_CHANNELS)}")
                # revisione 2026-07-21 (E7): il pubsub è fire-and-forget — i
                # messaggi pubblicati nella finestra errore→re-subscribe sono
                # persi. Dopo ogni (ri)sottoscrizione riconciliamo lo stato
                # che DEVE restare coerente: config (un config_updated perso
                # lascerebbe l'engine su parametri vecchi, come la deriva soglia).
                await self._resync_dopo_subscribe()
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
            finally:
                # E11: chiudi il vecchio pubsub prima di ricrearne uno, o un
                # flapping prolungato di Redis lascia connessioni/FD pendenti
                if pubsub is not None:
                    try:
                        await pubsub.aclose()
                    except Exception:
                        pass
                    pubsub = None

    def _ratchet_trailing_statico(self, symbol: str, price: float) -> bool:
        """Trailing statico (E6): avanza lo stop_loss nella direzione del
        profitto, mai indietro. Attivo solo col dinamico spento e pct>0 —
        col dinamico acceso ci pensa exit_model.update_trailing_stop.
        Ritorna True se lo stop è cambiato (il chiamante lo persiste su Redis,
        così un riavvio non perde la protezione guadagnata)."""
        if self.dynamic_exit_enabled or self.trailing_stop_pct <= 0:
            return False
        pos = self.positions.get(symbol)
        if pos is None or not pos.is_open:
            return False
        if pos.side == 'long':
            nuovo = price * (1 - self.trailing_stop_pct)
            if nuovo > pos.stop_loss:
                pos.stop_loss = pos.trailing_stop = nuovo
                return True
        else:
            nuovo = price * (1 + self.trailing_stop_pct)
            if nuovo < pos.stop_loss:
                pos.stop_loss = pos.trailing_stop = nuovo
                return True
        return False

    async def _resync_dopo_subscribe(self):
        """Riallinea lo stato che un messaggio perso avrebbe aggiornato.
        La config è la cosa critica; i comandi transitori (close_all) non
        sono replicabili — restano un limite noto del pubsub fire-and-forget."""
        try:
            await self._load_config_from_redis()
        except Exception as e:
            logger.warning(f"⚠️ Resync config dopo subscribe fallito: {e}")

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
                elif command.get('action') == 'reset_circuit_breaker':
                    logger.warning("🔄 Comando ricevuto: reset manuale del circuit breaker")
                    self.circuit_breaker.manual_reset()
                    # persiste il reset (E2): senza, il prossimo riavvio
                    # ripesca il vecchio picco e ri-arma il trip appena azzerato
                    await self.redis.set('circuit_breaker_reset', json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "capital": self.capital,
                    }))
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

            # Circuit breaker: gate GLOBALE prima di qualunque altra
            # valutazione, mai sulle chiusure. Blocca sia aperture fresche
            # sia reverse (che aprono una nuova posizione).
            if self.circuit_breaker_enabled and self.circuit_breaker.is_tripped():
                status = self.circuit_breaker.status()
                logger.warning(
                    f"⛔ Circuit breaker attivo ({status['reason']}) → segnale "
                    f"{signal.action} per {signal.symbol} ignorato"
                )
                self._record_signal(signal, "CIRCUIT_BREAKER", detail=status["reason"] or "")
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

            # Sentiment come BONUS-ONLY: con sentiment neutro la confidenza
            # resta quella del modello. La vecchia media pesata (0.7×conf)
            # penalizzava del 30% ogni segnale con sentiment neutro, creando
            # un secondo gate nascosto: soglia policy 0.60 + gate engine 0.55
            # → soglia effettiva P ≥ 0.786 senza che nessun parametro lo
            # dicesse. Ora la soglia in config è l'unico regolatore (la usa
            # anche l'inference) e questo gate scatta solo se le config dei
            # due processi divergono.
            weighted_confidence = min(
                1.0,
                signal.confidence + self.sentiment_weight * max(0.0, directional_sentiment),
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
