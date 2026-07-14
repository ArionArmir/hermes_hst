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

Semplificazioni note (documentate, non nascoste):
- niente sentiment (non esiste uno storico di sentiment);
- simboli simulati indipendentemente, senza cap di margine incrociato: il
  sizing per trade è comunque quello dell'engine (min(nozionale max,
  capitale × esposizione));
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
from src.exit_model.profiles import build_exit_model
from src.volume_pattern import VolumePatternAnalyzer

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
