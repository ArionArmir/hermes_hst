"""
Backtester event-driven (src/backtest/backtester.py): scenari deterministici
con un modello stub e candele sintetiche costruite ad hoc. Verifica il
contratto economico (fee, slippage avverso, uscite) più che i numeri del
modello reale.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest import BacktestParams, backtest_symbol, backtest_portfolio
from src.shared.features import MIN_CANDLES

FLAT = [0.1, 0.8, 0.1]    # (down, flat, up)
BUY = [0.05, 0.05, 0.90]
SELL = [0.90, 0.05, 0.05]


class StubModel:
    """predict_proba deterministica: una riga di probabilità per ogni riga
    di feature valida, nell'ordine in cui il backtester le richiede."""
    classes_ = np.array([0, 1, 2])

    def __init__(self, proba_for_row):
        self.proba_for_row = proba_for_row

    def predict_proba(self, X):
        return np.array([self.proba_for_row(i) for i in range(len(X))], dtype=float)


def _flat_candles(n=100, price=100.0, volume=10.0) -> pd.DataFrame:
    """Prezzo ~fermo con micro-oscillazione (±0.05): con candele
    perfettamente piatte l'RSI è 0/0 → NaN e nessuna feature è valida.
    L'open resta esattamente `price` per assert deterministici sull'entry."""
    closes = [price + (0.05 if i % 2 == 0 else -0.05) for i in range(n)]
    return pd.DataFrame({
        "open": [price] * n,
        "high": [c + 0.1 for c in closes],
        "low": [c - 0.1 for c in closes],
        "close": closes,
        "volume": [volume] * n,
    })


def test_always_flat_means_no_trades_and_untouched_capital():
    result = backtest_symbol(StubModel(lambda i: FLAT), _flat_candles(), "BTCUSDT")

    assert result.n_trades == 0
    assert result.net_pnl == 0.0
    assert result.final_capital == BacktestParams().initial_capital


def test_flat_price_trade_costs_exactly_fees_and_slippage():
    params = BacktestParams()
    result = backtest_symbol(StubModel(lambda i: BUY), _flat_candles(), "BTCUSDT", params)

    assert result.n_trades > 0
    first = result.trades.iloc[0]
    # Slippage avverso sull'entry long: open 100 → 100 × (1 + slippage)
    assert first["entry"] == 100.0 * (1 + params.slippage_pct)
    # Uscita MAX_HOLDING al close (±0.05 di oscillazione) meno slippage
    assert abs(first["exit"] - 100.0) < 0.2
    assert first["reason"] == "MAX_HOLDING"
    assert first["bars_held"] == params.max_holding_bars
    # A prezzo ~fermo fee+slippage (≈0.21) dominano l'oscillazione (≈0.08):
    # il PnL netto deve essere negativo
    assert first["pnl"] < 0
    assert result.final_capital < params.initial_capital
    assert np.isclose(result.net_pnl, result.gross_pnl - result.fees)


def test_stop_loss_hit_intrabar_exits_at_stop_price():
    candles = _flat_candles(n=MIN_CANDLES + 20)
    # Dopo il warmup una candela crolla: low sotto qualunque SL plausibile
    crash_bar = MIN_CANDLES + 8
    candles.loc[crash_bar:, "low"] = 90.0
    candles.loc[crash_bar:, "close"] = 95.0
    result = backtest_symbol(StubModel(lambda i: BUY), candles, "BTCUSDT")

    stops = result.trades[result.trades["reason"] == "STOP_LOSS"]
    assert len(stops) >= 1
    first = stops.iloc[0]
    assert first["pnl"] < 0
    # Uscita al prezzo dello stop (meno slippage), non al close della barra
    assert first["exit"] < first["entry"]


def test_take_profit_hit_intrabar_is_profitable():
    candles = _flat_candles(n=MIN_CANDLES + 20)
    spike_bar = MIN_CANDLES + 8
    candles.loc[spike_bar:, "high"] = 110.0  # sopra il TP fallback (103)
    result = backtest_symbol(StubModel(lambda i: BUY), candles, "BTCUSDT")

    tps = result.trades[result.trades["reason"] == "TAKE_PROFIT"]
    assert len(tps) >= 1
    assert (tps["pnl"] > 0).all()


def test_opposite_signal_reverses_position():
    # Prime 3 righe valide: buy; poi sell → il long viene chiuso per
    # REVERSE_SIGNAL e si apre uno short. (Il sell deve arrivare PRIMA che
    # il max holding di 5 barre chiuda il long da solo.)
    model = StubModel(lambda i: BUY if i < 3 else SELL)
    result = backtest_symbol(model, _flat_candles(n=MIN_CANDLES + 30), "BTCUSDT")

    assert (result.trades["reason"] == "REVERSE_SIGNAL").any()
    assert (result.trades["side"] == "short").any()


def test_portfolio_total_aggregates_symbols():
    candles = {"BTCUSDT": _flat_candles(), "ETHUSDT": _flat_candles()}
    results = backtest_portfolio(StubModel(lambda i: BUY), candles)

    assert set(results.keys()) == {"BTCUSDT", "ETHUSDT", "TOTAL"}
    total = results["TOTAL"]
    assert total.n_trades == results["BTCUSDT"].n_trades + results["ETHUSDT"].n_trades
    assert np.isclose(total.net_pnl, results["BTCUSDT"].net_pnl + results["ETHUSDT"].net_pnl)


def test_too_few_candles_returns_none():
    assert backtest_symbol(StubModel(lambda i: FLAT), _flat_candles(n=30), "BTCUSDT") is None
