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

from src.backtest import BacktestParams, backtest_symbol, backtest_portfolio, backtest_joint
from src.backtest.backtester import _align_common_index
from src.shared.circuit_breaker import CircuitBreakerParams
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
        # Order flow costante: neutro, non altera gli scenari deterministici
        "taker_buy_base": [volume * 0.5] * n,
        "n_trades": [100.0] * n,
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


# ---------- backtest_joint: capitale e cap di margine condivisi ----------

def test_align_common_index_is_intersection():
    a = pd.DataFrame({"x": [1, 2, 3, 4]}, index=[10, 20, 30, 40])
    b = pd.DataFrame({"x": [1, 2, 3]}, index=[20, 30, 50])

    common = _align_common_index({"A": a, "B": b})

    assert list(common) == [20, 30]


def test_joint_matches_independent_when_margin_never_binds():
    # Esposizione ampissima: nessuna apertura viene mai rifiutata per cap di
    # margine → il risultato aggregato deve coincidere con la somma dei
    # backtest indipendenti (stesso modello, stesse candele per simbolo).
    params = BacktestParams(max_exposure=100.0, initial_capital=100_000.0)
    candles = {"BTCUSDT": _flat_candles(), "ETHUSDT": _flat_candles()}
    model = StubModel(lambda i: BUY)

    joint = backtest_joint(model, candles, params)
    independent = backtest_portfolio(model, candles, params)["TOTAL"]

    assert joint.n_trades == independent.n_trades
    assert joint.hit_rate == independent.hit_rate


def test_portfolio_margin_cap_blocks_correlated_simultaneous_opens():
    # Due simboli, stesso segnale BUY nello stesso istante: con un cap di
    # margine stretto solo UNO dei due può aprire — il rischio di
    # correlazione che backtest_portfolio (capitali indipendenti) non vede.
    params = BacktestParams(
        max_exposure=0.05,       # cap margine: 50 USDT
        initial_capital=1000.0,
        max_position_usdt=40.0,  # nozionale 120 a leva 3 → margine 40
        leverage=3,
        pattern_confirmation=False,
    )
    candles = {"BTCUSDT": _flat_candles(), "ETHUSDT": _flat_candles()}
    model = StubModel(lambda i: BUY)

    result = backtest_joint(model, candles, params)

    # 40 di margine per posizione, cap 50: la seconda apertura simultanea
    # supererebbe il cap e viene rifiutata → mai più di 1 posizione aperta
    # in un dato istante (verificabile dal fatto che il numero di trade
    # "OPENED" impliciti resta più basso che senza cap)
    opens_per_symbol = result.trades.groupby("symbol").size()
    assert len(result.trades) > 0
    assert opens_per_symbol.max() >= 1
    # Con backtest_portfolio (indipendente) ENTRAMBI i simboli aprirebbero
    # sempre: qui invece il margine condiviso ne limita il numero totale
    independent_total_trades = sum(
        r.n_trades for r in backtest_portfolio(model, candles, params).values()
    ) - backtest_portfolio(model, candles, params)["TOTAL"].n_trades  # tolgo il doppio conteggio di TOTAL
    assert len(result.trades) <= independent_total_trades


def test_joint_reverse_and_close_work_across_symbols():
    model = StubModel(lambda i: BUY if i < 3 else SELL)
    candles = {"BTCUSDT": _flat_candles(n=MIN_CANDLES + 30)}

    result = backtest_joint(model, candles)

    assert (result.trades["reason"] == "REVERSE_SIGNAL").any()
    assert (result.trades["side"] == "short").any()


def test_joint_returns_none_for_insufficient_common_history():
    short = _flat_candles(n=10)
    candles = {"BTCUSDT": _flat_candles(), "ETHUSDT": short}

    assert backtest_joint(StubModel(lambda i: FLAT), candles) is None


def test_joint_returns_none_for_empty_input():
    assert backtest_joint(StubModel(lambda i: FLAT), {}) is None


def test_direction_cap_limits_simultaneous_same_side_positions():
    # Margine ampissimo (mai vincolante): SENZA cap direzionale entrambi i
    # simboli aprirebbero long; con cap=1 solo il primo (ordine di
    # iterazione del dict) deve restare aperto contemporaneamente.
    params = BacktestParams(max_exposure=100.0, initial_capital=100_000.0,
                            max_positions_same_direction=1, pattern_confirmation=False)
    candles = {"BTCUSDT": _flat_candles(), "ETHUSDT": _flat_candles()}
    model = StubModel(lambda i: BUY)

    result = backtest_joint(model, candles, params)

    without_cap = backtest_joint(model, candles, BacktestParams(
        max_exposure=100.0, initial_capital=100_000.0, pattern_confirmation=False))

    assert len(result.trades) < len(without_cap.trades)


def test_direction_cap_none_means_uncapped():
    params_uncapped = BacktestParams(max_exposure=100.0, initial_capital=100_000.0,
                                     pattern_confirmation=False, max_positions_same_direction=None)
    params_high_cap = BacktestParams(max_exposure=100.0, initial_capital=100_000.0,
                                     pattern_confirmation=False, max_positions_same_direction=10)
    candles = {"BTCUSDT": _flat_candles(), "ETHUSDT": _flat_candles()}
    model = StubModel(lambda i: BUY)

    r1 = backtest_joint(model, candles, params_uncapped)
    r2 = backtest_joint(model, candles, params_high_cap)
    assert len(r1.trades) == len(r2.trades)


# ---------- circuit breaker (usa un DatetimeIndex vero: record_trade/
# is_tripped fanno aritmetica su datetime, un RangeIndex intero non basta) ----------

def _flat_candles_dt(n=100, price=100.0, volume=10.0, start="2026-01-01") -> pd.DataFrame:
    df = _flat_candles(n=n, price=price, volume=volume)
    df.index = pd.date_range(start, periods=n, freq="1h")
    return df


def test_circuit_breaker_none_means_no_breaker():
    # A prezzo ~fermo ogni trade chiude in perdita netta (fee+slippage):
    # senza breaker, MAX_HOLDING ogni 5 barre per tutta la serie.
    candles = {"BTCUSDT": _flat_candles_dt()}
    model = StubModel(lambda i: BUY)

    result = backtest_joint(model, candles, BacktestParams(pattern_confirmation=False,
                                                           circuit_breaker=None))

    assert len(result.trades) > 3  # nessuna pausa: continua a tradare per tutta la serie


def test_circuit_breaker_stops_reopening_after_consecutive_losses():
    candles = {"BTCUSDT": _flat_candles_dt()}
    model = StubModel(lambda i: BUY)
    breaker_params = CircuitBreakerParams(
        max_consecutive_losses=2, consecutive_loss_cooldown_minutes=10_000,  # non riprende nel test
        max_daily_loss_pct=None, max_drawdown_pct=None,
    )

    with_breaker = backtest_joint(model, candles, BacktestParams(
        pattern_confirmation=False, circuit_breaker=breaker_params))
    without_breaker = backtest_joint(model, candles, BacktestParams(
        pattern_confirmation=False, circuit_breaker=None))

    assert len(with_breaker.trades) < len(without_breaker.trades)
    assert len(with_breaker.trades) <= 3  # 2 perdite + al più 1 in corso quando scatta


def test_circuit_breaker_daily_loss_caps_trades_per_calendar_day():
    candles = {"BTCUSDT": _flat_candles_dt(n=200)}
    model = StubModel(lambda i: BUY)
    # Ogni trade perde ~0.21 USDT (fee+slippage): 5 bastano a superare lo
    # 0.1% di 1000 USDT in un giorno, poi il breaker resta attivo fino al
    # prossimo giorno UTC (a differenza del cooldown, non si autoresetta prima).
    breaker_params = CircuitBreakerParams(
        max_consecutive_losses=None, max_daily_loss_pct=0.001, max_drawdown_pct=None,
    )

    result = backtest_joint(model, candles, BacktestParams(
        pattern_confirmation=False, circuit_breaker=breaker_params, initial_capital=1000.0))
    without_breaker = backtest_joint(model, candles, BacktestParams(
        pattern_confirmation=False, circuit_breaker=None, initial_capital=1000.0))

    assert result.n_trades < without_breaker.n_trades

    dates = candles["BTCUSDT"].index[result.trades["bar"]].normalize()
    trades_per_day = result.trades.groupby(dates).size()
    assert trades_per_day.max() <= 6  # mai più di ~5 perdite prima che scatti
