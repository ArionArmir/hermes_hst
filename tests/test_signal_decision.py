"""
Decisione segnale a 3 classi (src/inference/main.py::_signal_from_proba):
buy e sell devono essere SIMMETRICI — uno short parte solo con P(down) alta,
mai per semplice assenza di rialzo (docs/IMPROVEMENT_PLAN.md, S2).
L'ordine delle probabilità è [down, flat, up], garantito dalla validazione
delle classi in _load_model.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.inference.main import MLInference, SIGNAL_PROB_THRESHOLD


def _decide(p_down, p_flat, p_up):
    return MLInference()._signal_from_proba([p_down, p_flat, p_up])


def test_high_p_up_emits_buy_with_its_probability():
    action, confidence = _decide(0.1, 0.2, 0.7)
    assert action == "buy"
    assert confidence == 0.7


def test_high_p_down_emits_sell_with_its_probability():
    action, confidence = _decide(0.7, 0.2, 0.1)
    assert action == "sell"
    assert confidence == 0.7


def test_flat_market_is_hold_even_with_low_p_up():
    # Il caso che il vecchio criterio binario ("P(rialzo) < 0.4 → sell")
    # trasformava in short: mercato laterale, nessuna direzione prevista.
    action, _ = _decide(0.15, 0.70, 0.15)
    assert action == "hold"


def test_below_threshold_is_hold():
    just_below = SIGNAL_PROB_THRESHOLD - 0.01
    action, _ = _decide(just_below, 1 - just_below - 0.1, 0.1)
    assert action == "hold"
