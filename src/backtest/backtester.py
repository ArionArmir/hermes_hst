"""
Backtester event-driven: simula la logica dell'engine su candele storiche,
con fee taker e slippage. È il banco di prova per soglie, moltiplicatori ATR
e selezione champion/challenger (docs/IMPROVEMENT_PLAN.md, S7/M3): la metrica
che conta è il PnL netto, non l'accuratezza di classificazione.

Fedeltà al live (stessi moduli condivisi, mai logica duplicata):
- segnali: src/shared/signal_policy.signal_from_proba sulle probabilità del
  modello, feature da src/shared/features (identiche al training/inference);
- esecuzione: il segnale nasce sulla candela chiusa t → esegue all'open di
  t+1, con slippage avverso;
- uscite: SL/TP da ATRExitModel con i profili per-simbolo condivisi
  (src/exit_model/profiles), controllati intrabar su high/low (se una candela
  tocca sia SL sia TP si assume il caso peggiore: SL); trailing stop ATR
  aggiornato alla chiusura di ogni candela; max holding in barre; reverse su
  segnale opposto; conferma pattern con VolumePatternAnalyzer.

Due modalità:
- backtest_symbol/backtest_portfolio: ogni simbolo simulato INDIPENDENTEMENTE
  con il proprio capitale pieno — comodo per un primo sguardo per-simbolo, ma
  ottimistico: ignora che in live tutti i simboli condividono capitale e cap
  di margine (src/engine/main.py, _margin_in_use). Con simboli correlati
  (tutte crypto) che aprono posizioni simultanee nella stessa direzione,
  questa modalità non vede il rischio di un ribasso sincrono che le chiude
  tutte in stop loss (scoperto con walk_forward.py: docs/IMPROVEMENT_PLAN.md).
- backtest_joint: capitale e cap di margine CONDIVISI tra tutti i simboli,
  simulati bar-per-bar in sincrono sullo stesso orologio — la simulazione
  onesta da usare per tarare soglia/ATR (tune_strategy.py) e per il
  confronto champion/challenger (trainer.py). Supporta anche il cap sul
  numero di posizioni simultanee nella stessa direzione
  (max_positions_same_direction): il cap di margine da solo si è rivelato
  insufficiente a bloccare aperture correlate simultanee al sizing attuale
  (walk_forward.py lo dimostra empiricamente).

Semplificazioni note (documentate, non nascoste):
- niente sentiment (non esiste uno storico di sentiment);
- i cooldown anti flip-flop dell'engine (reverse_cooldown_minutes,
  entry_cooldown_minutes) sono sotto la granularità della barra 1h e non
  vengono simulati: qui i segnali cambiano comunque solo a candela chiusa
  e l'esecuzione avviene alla barra successiva.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.shared.features import compute_features, MIN_CANDLES
from src.shared.signal_policy import signal_from_proba, SIGNAL_PROB_THRESHOLD
from src.training.feature_engine import TARGET_DOWN, TARGET_UP
from src.exit_model.atr_exit import ATRExitModel
from src.exit_model.profiles import build_exit_model
from src.volume_pattern import VolumePatternAnalyzer
from src.shared.circuit_breaker import CircuitBreaker, CircuitBreakerParams

# Barre per anno con candele 1h, per annualizzare lo Sharpe
BARS_PER_YEAR_1H = 24 * 365


@dataclass
class BacktestParams:
    initial_capital: float = 1000.0
    max_position_usdt: float = 50.0
    leverage: int = 3
    max_exposure: float = 0.5
    taker_fee_pct: float = 0.0005
    slippage_pct: float = 0.0002
    max_holding_bars: int = 5
    prob_threshold: float = SIGNAL_PROB_THRESHOLD
    pattern_confirmation: bool = True
    reverse_trading: bool = True
    # None → profili per-simbolo (src/exit_model/profiles.py); un valore →
    # moltiplicatore uniforme per tutti i simboli (usato dalla taratura)
    atr_multiplier_sl: Optional[float] = None
    atr_multiplier_tp: Optional[float] = None
    # Max posizioni simultanee nella stessa direzione, solo per backtest_joint
    # (unica modalità con stato condiviso tra simboli). None = nessun cap.
    max_positions_same_direction: Optional[int] = None
    # Circuit breaker (solo backtest_joint, stato condiviso). None = nessuno:
    # è così che tune_strategy.py isola l'effetto di soglia/ATR da quello del
    # breaker, e come si misura il "senza breaker" per confronto.
    circuit_breaker: Optional[CircuitBreakerParams] = None


@dataclass
class BacktestResult:
    symbol: str
    n_trades: int
    net_pnl: float
    gross_pnl: float
    fees: float
    final_capital: float
    return_pct: float
    hit_rate: float
    max_drawdown_pct: float
    sharpe: float
    trades: pd.DataFrame = field(repr=False)
    equity: pd.Series = field(repr=False)


class _SimPosition:
    # is_open incluso per compatibilità con ATRExitModel.update_trailing_stop,
    # che legge position.is_open/side/stop_loss come sulla Position live
    __slots__ = ("side", "entry_price", "quantity", "stop_loss", "take_profit",
                 "bars_held", "is_open")

    def __init__(self, side: str, entry_price: float, quantity: float,
                 stop_loss: float, take_profit: float):
        self.side = side
        self.entry_price = entry_price
        self.quantity = quantity
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.bars_held = 0
        self.is_open = True


def _apply_slippage(price: float, side: str, entering: bool, slippage_pct: float) -> float:
    """Slippage sempre avverso: compri più caro, vendi più basso."""
    buying = (side == "long") == entering
    return price * (1 + slippage_pct) if buying else price * (1 - slippage_pct)


def backtest_symbol(model, candles: pd.DataFrame, symbol: str,
                    params: BacktestParams = None) -> Optional[BacktestResult]:
    """Simula la strategia su un simbolo. `candles`: OHLCV con almeno
    MIN_CANDLES + qualche barra; le prime MIN_CANDLES fanno solo da warmup."""
    params = params or BacktestParams()
    if candles is None or len(candles) <= MIN_CANDLES + 1:
        return None

    candles = candles.reset_index(drop=True)
    features = compute_features(candles)

    # Probabilità vettorializzate una volta sola (bar senza feature → hold)
    proba = np.full((len(candles), 3), np.nan)
    valid_mask = features.notna().all(axis=1)
    if valid_mask.any():
        proba[valid_mask.values] = model.predict_proba(features[valid_mask])

    if params.atr_multiplier_sl is not None and params.atr_multiplier_tp is not None:
        exit_model = ATRExitModel(atr_multiplier_sl=params.atr_multiplier_sl,
                                  atr_multiplier_tp=params.atr_multiplier_tp)
    else:
        exit_model = build_exit_model(symbol)
    pattern_model = VolumePatternAnalyzer(window=10)

    capital = params.initial_capital
    position: Optional[_SimPosition] = None
    pending_action: Optional[str] = None
    trades: List[dict] = []
    equity = np.empty(len(candles))

    def close_position(price: float, bar_i: int, reason: str):
        nonlocal capital, position
        exit_price = _apply_slippage(price, position.side, entering=False,
                                     slippage_pct=params.slippage_pct)
        gross = (exit_price - position.entry_price) * position.quantity
        if position.side == "short":
            gross = -gross
        fee = (position.entry_price + exit_price) * position.quantity * params.taker_fee_pct
        capital += gross - fee
        trades.append({
            "bar": bar_i, "symbol": symbol, "side": position.side,
            "entry": position.entry_price, "exit": exit_price,
            "pnl": gross - fee, "pnl_gross": gross, "fees": fee,
            "bars_held": position.bars_held, "reason": reason,
        })
        position = None

    def open_position(action: str, price: float):
        nonlocal position
        side = "long" if action == "buy" else "short"
        if params.pattern_confirmation and pattern_model.analyze()["signal"] == "REJECT":
            return
        entry_price = _apply_slippage(price, side, entering=True,
                                      slippage_pct=params.slippage_pct)
        position_size = min(params.max_position_usdt * params.leverage,
                            capital * params.max_exposure)
        if position_size <= 0:
            return
        quantity = position_size / entry_price
        stop_loss, take_profit = exit_model.calculate_exit_levels(entry_price, side)
        position = _SimPosition(side, entry_price, quantity, stop_loss, take_profit)

    for i in range(len(candles)):
        bar = candles.iloc[i]

        # 1. Esecuzione del segnale nato sulla candela precedente, all'open
        #    di questa (ATR/pattern aggiornati fino a i-1: nessun lookahead).
        if pending_action is not None:
            if position is not None:
                if params.reverse_trading and (
                    (pending_action == "buy" and position.side == "short")
                    or (pending_action == "sell" and position.side == "long")
                ):
                    close_position(bar["open"], i, reason="REVERSE_SIGNAL")
                    open_position(pending_action, bar["open"])
            else:
                open_position(pending_action, bar["open"])
            pending_action = None

        # 2. Gestione posizione aperta durante la candela
        if position is not None:
            if position.side == "long":
                sl_hit = bar["low"] <= position.stop_loss
                tp_hit = bar["high"] >= position.take_profit
            else:
                sl_hit = bar["high"] >= position.stop_loss
                tp_hit = bar["low"] <= position.take_profit
            if sl_hit:  # caso peggiore se toccati entrambi nella stessa barra
                close_position(position.stop_loss, i, reason="STOP_LOSS")
            elif tp_hit:
                close_position(position.take_profit, i, reason="TAKE_PROFIT")
            else:
                position.bars_held += 1
                if position.bars_held >= params.max_holding_bars:
                    close_position(bar["close"], i, reason="MAX_HOLDING")

        # 3. La candela chiusa aggiorna i modelli (come _ingest_candle live)
        exit_model.add_price(bar["close"], bar["high"], bar["low"])
        pattern_model.add_data(bar["close"], bar["volume"], bar["high"], bar["low"])

        # 4. Trailing stop sul close, con l'ATR aggiornato
        if position is not None:
            position.stop_loss = exit_model.update_trailing_stop(bar["close"], position)

        # 5. Segnale dalla candela appena chiusa → eseguirà all'open della prossima
        if not np.isnan(proba[i][0]):
            action, _ = signal_from_proba(proba[i][TARGET_DOWN], proba[i][TARGET_UP],
                                          threshold=params.prob_threshold)
            if action != "hold":
                pending_action = action

        # Equity mark-to-market a fine barra
        unrealized = 0.0
        if position is not None:
            unrealized = (bar["close"] - position.entry_price) * position.quantity
            if position.side == "short":
                unrealized = -unrealized
        equity[i] = capital + unrealized

    # Chiusura forzata a fine periodo per un PnL confrontabile
    if position is not None:
        close_position(candles.iloc[-1]["close"], len(candles) - 1, reason="END_OF_DATA")
        equity[-1] = capital

    trades_df = pd.DataFrame(trades)
    equity_s = pd.Series(equity)

    running_max = equity_s.cummax()
    drawdown = (equity_s - running_max) / running_max
    bar_returns = equity_s.pct_change().dropna()
    sharpe = 0.0
    if len(bar_returns) > 1 and bar_returns.std() > 0:
        sharpe = float(bar_returns.mean() / bar_returns.std() * np.sqrt(BARS_PER_YEAR_1H))

    net_pnl = capital - params.initial_capital
    return BacktestResult(
        symbol=symbol,
        n_trades=len(trades_df),
        net_pnl=net_pnl,
        gross_pnl=float(trades_df["pnl_gross"].sum()) if len(trades_df) else 0.0,
        fees=float(trades_df["fees"].sum()) if len(trades_df) else 0.0,
        final_capital=capital,
        return_pct=net_pnl / params.initial_capital,
        hit_rate=float((trades_df["pnl"] > 0).mean()) if len(trades_df) else 0.0,
        max_drawdown_pct=float(-drawdown.min()),
        sharpe=sharpe,
        trades=trades_df,
        equity=equity_s,
    )


def backtest_portfolio(model, candles_by_symbol: Dict[str, pd.DataFrame],
                       params: BacktestParams = None) -> Dict[str, BacktestResult]:
    """Backtest per ogni simbolo (indipendenti, capitale separato per
    simbolo). Chiave 'TOTAL' con l'aggregato."""
    params = params or BacktestParams()
    results: Dict[str, BacktestResult] = {}
    for symbol, candles in candles_by_symbol.items():
        result = backtest_symbol(model, candles, symbol, params)
        if result is not None:
            results[symbol] = result
    if results:
        all_trades = pd.concat([r.trades for r in results.values() if len(r.trades)],
                               ignore_index=True) if any(len(r.trades) for r in results.values()) else pd.DataFrame()
        total_net = sum(r.net_pnl for r in results.values())
        n_capitals = len(results) * params.initial_capital
        results["TOTAL"] = BacktestResult(
            symbol="TOTAL",
            n_trades=sum(r.n_trades for r in results.values()),
            net_pnl=total_net,
            gross_pnl=sum(r.gross_pnl for r in results.values()),
            fees=sum(r.fees for r in results.values()),
            final_capital=n_capitals + total_net,
            return_pct=total_net / n_capitals,
            hit_rate=float((all_trades["pnl"] > 0).mean()) if len(all_trades) else 0.0,
            max_drawdown_pct=max(r.max_drawdown_pct for r in results.values()),
            sharpe=float(np.mean([r.sharpe for r in results.values()])),
            trades=all_trades,
            equity=pd.Series(dtype=float),
        )
    return results


def _align_common_index(candles_by_symbol: Dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """Intersezione degli indici: solo le barre presenti per TUTTI i
    simboli. Niente forward-fill/dati sintetici — verificato che i nostri
    parquet storici sono già perfettamente allineati (nessun buco), quindi
    l'intersezione perde al più qualche barra ai bordi (inizi storici
    diversi tra simboli scaricati in momenti diversi)."""
    indexes = [df.index for df in candles_by_symbol.values()]
    common = indexes[0]
    for idx in indexes[1:]:
        common = common.intersection(idx)
    return common.sort_values()


def backtest_joint(model, candles_by_symbol: Dict[str, pd.DataFrame],
                   params: BacktestParams = None) -> Optional[BacktestResult]:
    """Simulazione a PORTAFOGLIO CONDIVISO: un solo capitale e un solo cap di
    margine per tutti i simboli, aggiornati bar-per-bar in sincrono sullo
    stesso orologio (a differenza di backtest_portfolio, che tratta ogni
    simbolo come se avesse capitale proprio e infinito rispetto agli altri).
    Replica _margin_in_use()/_open_position() dell'engine live
    (src/engine/main.py): un'apertura viene rifiutata se il margine in uso
    più quello richiesto supera capital × max_exposure.

    Ogni simbolo mantiene il proprio ATRExitModel/VolumePatternAnalyzer (le
    uscite restano per-simbolo), ma tutti condividono lo stesso `capital` e
    competono per lo stesso `margin_cap` — è questo che rende visibile il
    rischio di correlazione: simboli che aprono long in blocco esauriscono
    il margine disponibile invece di aprire tutti a piena taglia. Supporta
    anche il circuit breaker (src/shared/circuit_breaker.py): il fold peggiore
    del walk-forward era una sequenza di stop loss consecutivi NEL TEMPO, non
    un eccesso di posizioni contemporanee — il cap direzionale non lo copre."""
    params = params or BacktestParams()
    if not candles_by_symbol:
        return None

    common_index = _align_common_index(candles_by_symbol)
    if len(common_index) <= MIN_CANDLES + 1:
        return None

    aligned = {sym: df.loc[common_index] for sym, df in candles_by_symbol.items()}
    n_bars = len(common_index)

    proba_by_symbol: Dict[str, np.ndarray] = {}
    for sym, df in aligned.items():
        features = compute_features(df)
        proba = np.full((n_bars, 3), np.nan)
        valid_mask = features.notna().all(axis=1)
        if valid_mask.any():
            proba[valid_mask.values] = model.predict_proba(features[valid_mask])
        proba_by_symbol[sym] = proba

    exit_models = {}
    for sym in aligned:
        if params.atr_multiplier_sl is not None and params.atr_multiplier_tp is not None:
            exit_models[sym] = ATRExitModel(atr_multiplier_sl=params.atr_multiplier_sl,
                                            atr_multiplier_tp=params.atr_multiplier_tp)
        else:
            exit_models[sym] = build_exit_model(sym)
    pattern_models = {sym: VolumePatternAnalyzer(window=10) for sym in aligned}

    capital = params.initial_capital
    positions: Dict[str, _SimPosition] = {}
    pending_actions: Dict[str, str] = {}
    trades: List[dict] = []
    equity = np.empty(n_bars)
    breaker = CircuitBreaker(params.circuit_breaker) if params.circuit_breaker else None

    def margin_in_use(bar_by_symbol: dict) -> float:
        total = 0.0
        for sym, pos in positions.items():
            price = bar_by_symbol[sym]["open"]
            total += (pos.quantity * price) / max(params.leverage, 1)
        return total

    def close_position(sym: str, price: float, bar_i: int, reason: str):
        nonlocal capital
        position = positions.pop(sym)
        exit_price = _apply_slippage(price, position.side, entering=False,
                                     slippage_pct=params.slippage_pct)
        gross = (exit_price - position.entry_price) * position.quantity
        if position.side == "short":
            gross = -gross
        fee = (position.entry_price + exit_price) * position.quantity * params.taker_fee_pct
        capital += gross - fee
        trades.append({
            "bar": bar_i, "symbol": sym, "side": position.side,
            "entry": position.entry_price, "exit": exit_price,
            "pnl": gross - fee, "pnl_gross": gross, "fees": fee,
            "bars_held": position.bars_held, "reason": reason,
        })
        if breaker is not None:
            breaker.record_trade(gross - fee, capital, now=common_index[bar_i])

    def try_open(sym: str, action: str, price: float, bar_by_symbol: dict):
        side = "long" if action == "buy" else "short"
        if breaker is not None and breaker.is_tripped(now=common_index[i]):
            return  # circuit breaker attivo, come l'engine live
        if params.max_positions_same_direction is not None:
            same_direction = sum(1 for p in positions.values() if p.side == side)
            if same_direction >= params.max_positions_same_direction:
                return  # cap direzionale raggiunto, come l'engine live
        if params.pattern_confirmation and pattern_models[sym].analyze()["signal"] == "REJECT":
            return
        position_size = min(params.max_position_usdt * params.leverage,
                            capital * params.max_exposure)
        if position_size <= 0:
            return
        margin_required = position_size / max(params.leverage, 1)
        margin_cap = capital * params.max_exposure
        if margin_in_use(bar_by_symbol) + margin_required > margin_cap:
            return  # cap di esposizione portafoglio raggiunto, come l'engine live
        entry_price = _apply_slippage(price, side, entering=True,
                                      slippage_pct=params.slippage_pct)
        quantity = position_size / entry_price
        stop_loss, take_profit = exit_models[sym].calculate_exit_levels(entry_price, side)
        positions[sym] = _SimPosition(side, entry_price, quantity, stop_loss, take_profit)

    for i in range(n_bars):
        bar_by_symbol = {sym: df.iloc[i] for sym, df in aligned.items()}

        # 1. Esecuzione dei segnali nati alla barra precedente, all'open di
        #    questa — stesso ordine di iterazione per tutti i simboli
        #    (deterministico: l'ordine del dict candles_by_symbol in input).
        for sym, action in list(pending_actions.items()):
            bar = bar_by_symbol[sym]
            position = positions.get(sym)
            if position is not None:
                if params.reverse_trading and (
                    (action == "buy" and position.side == "short")
                    or (action == "sell" and position.side == "long")
                ):
                    close_position(sym, bar["open"], i, reason="REVERSE_SIGNAL")
                    try_open(sym, action, bar["open"], bar_by_symbol)
            else:
                try_open(sym, action, bar["open"], bar_by_symbol)
        pending_actions.clear()

        # 2. Gestione posizioni aperte (SL/TP intrabar, caso peggiore se
        #    toccati entrambi; max holding)
        for sym, position in list(positions.items()):
            bar = bar_by_symbol[sym]
            if position.side == "long":
                sl_hit = bar["low"] <= position.stop_loss
                tp_hit = bar["high"] >= position.take_profit
            else:
                sl_hit = bar["high"] >= position.stop_loss
                tp_hit = bar["low"] <= position.take_profit
            if sl_hit:
                close_position(sym, position.stop_loss, i, reason="STOP_LOSS")
            elif tp_hit:
                close_position(sym, position.take_profit, i, reason="TAKE_PROFIT")
            else:
                position.bars_held += 1
                if position.bars_held >= params.max_holding_bars:
                    close_position(sym, bar["close"], i, reason="MAX_HOLDING")

        # 3. La candela chiusa aggiorna i modelli (come _ingest_candle live)
        for sym, bar in bar_by_symbol.items():
            exit_models[sym].add_price(bar["close"], bar["high"], bar["low"])
            pattern_models[sym].add_data(bar["close"], bar["volume"], bar["high"], bar["low"])

        # 4. Trailing stop sul close, con l'ATR aggiornato
        for sym, position in positions.items():
            position.stop_loss = exit_models[sym].update_trailing_stop(
                bar_by_symbol[sym]["close"], position)

        # 5. Nuovi segnali dalla candela appena chiusa → eseguiranno alla prossima
        for sym, proba in proba_by_symbol.items():
            if not np.isnan(proba[i][0]):
                action, _ = signal_from_proba(proba[i][TARGET_DOWN], proba[i][TARGET_UP],
                                              threshold=params.prob_threshold)
                if action != "hold":
                    pending_actions[sym] = action

        # Equity mark-to-market su TUTTE le posizioni aperte
        unrealized = 0.0
        for sym, position in positions.items():
            bar = bar_by_symbol[sym]
            u = (bar["close"] - position.entry_price) * position.quantity
            if position.side == "short":
                u = -u
            unrealized += u
        equity[i] = capital + unrealized

    # Chiusura forzata a fine periodo per un PnL confrontabile
    if positions:
        last_bar_by_symbol = {sym: df.iloc[-1] for sym, df in aligned.items()}
        for sym in list(positions.keys()):
            close_position(sym, last_bar_by_symbol[sym]["close"], n_bars - 1, reason="END_OF_DATA")
        equity[-1] = capital

    trades_df = pd.DataFrame(trades)
    equity_s = pd.Series(equity)

    running_max = equity_s.cummax()
    drawdown = (equity_s - running_max) / running_max
    bar_returns = equity_s.pct_change().dropna()
    sharpe = 0.0
    if len(bar_returns) > 1 and bar_returns.std() > 0:
        sharpe = float(bar_returns.mean() / bar_returns.std() * np.sqrt(BARS_PER_YEAR_1H))

    net_pnl = capital - params.initial_capital
    return BacktestResult(
        symbol="PORTFOLIO",
        n_trades=len(trades_df),
        net_pnl=net_pnl,
        gross_pnl=float(trades_df["pnl_gross"].sum()) if len(trades_df) else 0.0,
        fees=float(trades_df["fees"].sum()) if len(trades_df) else 0.0,
        final_capital=capital,
        return_pct=net_pnl / params.initial_capital,
        hit_rate=float((trades_df["pnl"] > 0).mean()) if len(trades_df) else 0.0,
        max_drawdown_pct=float(-drawdown.min()),
        sharpe=sharpe,
        trades=trades_df,
        equity=equity_s,
    )
