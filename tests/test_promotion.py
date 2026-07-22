"""
Promozione multi-fold (docs/IMPROVEMENT_PLAN.md, V3/N3): la validation
riservata viene divisa in più sotto-finestre non sovrapposte, promuove solo
se il challenger vince nella maggioranza dei fold E non è peggiore nel fold
peggiore di ciascuno.

build_fold_windows è testata su DataFrame reali (pura logica di slicing);
decide_promotion mocka backtest_joint per isolare la logica di
maggioranza/caso-peggiore dal comportamento del motore di backtest (già
testato a fondo altrove).
"""
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.shared.features import MIN_CANDLES
from src.training.promotion import build_fold_windows, decide_promotion


def _candles(n) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                        "volume": 10.0}, index=idx)


@dataclass
class _FakeResult:
    net_pnl: float


# ---------- build_fold_windows ----------

def test_returns_empty_when_history_too_short():
    candles = {"BTCUSDT": _candles(MIN_CANDLES + 5)}  # una sola barra utile/fold nel migliore dei casi
    assert build_fold_windows(candles, n_folds=3) == []


def test_returns_n_folds_windows_when_enough_history():
    n = MIN_CANDLES * 4 + 300
    candles = {"BTCUSDT": _candles(n)}
    windows = build_fold_windows(candles, n_folds=3)
    assert len(windows) == 3
    for w in windows:
        assert len(w["BTCUSDT"]) > MIN_CANDLES  # ogni fold ha il proprio warmup + barre utili


def test_folds_are_consecutive_and_cover_the_series():
    n = MIN_CANDLES * 4 + 300
    candles = {"BTCUSDT": _candles(n)}
    windows = build_fold_windows(candles, n_folds=3)

    # L'ultimo indice dell'ultimo fold coincide con la fine della serie
    assert windows[-1]["BTCUSDT"].index[-1] == candles["BTCUSDT"].index[-1]
    # I fold sono in ordine cronologico crescente
    starts = [w["BTCUSDT"].index[0] for w in windows]
    assert starts == sorted(starts)


def test_multiple_symbols_are_aligned_per_fold():
    n = MIN_CANDLES * 4 + 300
    candles = {"BTCUSDT": _candles(n), "ETHUSDT": _candles(n)}
    windows = build_fold_windows(candles, n_folds=3)
    for w in windows:
        assert list(w["BTCUSDT"].index) == list(w["ETHUSDT"].index)


# ---------- decide_promotion ----------

def test_none_when_insufficient_history():
    candles = {"BTCUSDT": _candles(MIN_CANDLES + 5)}
    assert decide_promotion(object(), object(), candles) is None


def test_promotes_when_challenger_wins_majority_and_worst_case():
    candles = {"BTCUSDT": _candles(MIN_CANDLES * 4 + 300)}
    # challenger vince 2 fold su 3 (perde solo il fold 2), e il suo fold
    # peggiore (+3.0) batte comunque il fold peggiore del champion (+2.0)
    challenger_results = [_FakeResult(10.0), _FakeResult(3.0), _FakeResult(8.0)]
    champion_results = [_FakeResult(2.0), _FakeResult(5.0), _FakeResult(3.0)]

    with patch("src.training.promotion.backtest_joint",
              side_effect=_interleave(challenger_results, champion_results)):
        verdict = decide_promotion(object(), object(), candles)

    assert verdict.promote is True
    assert verdict.challenger_pnls == [10.0, 3.0, 8.0]
    assert "2/3" in verdict.reason


def test_does_not_promote_when_challenger_loses_majority():
    candles = {"BTCUSDT": _candles(MIN_CANDLES * 4 + 300)}
    challenger_results = [_FakeResult(-1.0), _FakeResult(5.0), _FakeResult(-2.0)]
    champion_results = [_FakeResult(1.0), _FakeResult(4.0), _FakeResult(1.0)]

    with patch("src.training.promotion.backtest_joint",
              side_effect=_interleave(challenger_results, champion_results)):
        verdict = decide_promotion(object(), object(), candles)

    assert verdict.promote is False


def test_worst_case_veto_blocks_promotion_despite_majority():
    candles = {"BTCUSDT": _candles(MIN_CANDLES * 4 + 300)}
    # Challenger vince 2 fold su 3 (maggioranza OK), ma nel terzo fold
    # affonda molto più del peggiore del champion: veto.
    challenger_results = [_FakeResult(5.0), _FakeResult(3.0), _FakeResult(-50.0)]
    champion_results = [_FakeResult(1.0), _FakeResult(1.0), _FakeResult(-2.0)]

    with patch("src.training.promotion.backtest_joint",
              side_effect=_interleave(challenger_results, champion_results)):
        verdict = decide_promotion(object(), object(), candles)

    assert verdict.promote is False
    assert "-50.00" in verdict.reason


def test_none_backtest_result_is_treated_as_zero_pnl():
    candles = {"BTCUSDT": _candles(MIN_CANDLES * 4 + 300)}
    challenger_results = [_FakeResult(5.0), None, _FakeResult(5.0)]
    champion_results = [_FakeResult(-1.0), _FakeResult(-1.0), _FakeResult(-1.0)]

    with patch("src.training.promotion.backtest_joint",
              side_effect=_interleave(challenger_results, champion_results)):
        verdict = decide_promotion(object(), object(), candles)

    assert verdict.challenger_pnls[1] == 0.0
    assert verdict.promote is True  # vince comunque 3/3 (0.0 > -1.0 nel fold nullo)


def _interleave(challenger_results, champion_results):
    """backtest_joint viene chiamato come (model, window, params): per ogni
    fold prima con il challenger poi col champion, nell'ordine del ciclo in
    decide_promotion — un side_effect iterabile pop-a i risultati in
    quest'ordine a ogni chiamata mockata."""
    results = []
    for c, h in zip(challenger_results, champion_results):
        results.append(c)
        results.append(h)
    return results
