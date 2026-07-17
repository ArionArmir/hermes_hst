"""
Normalizzazione della frequenza di segnale (docs/PRE_REGISTRO_H3.md).

È il pezzo su cui poggia l'intero esperimento H3: se la soglia non produce
davvero la frequenza richiesta, il confronto fra etichette resta truccato
esattamente come nel primo tentativo, ma in modo meno visibile.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research.evaluation import soglia_per_frequenza
from src.training.feature_engine import TARGET_DOWN, TARGET_FLAT, TARGET_UP


def _proba(p_down, p_flat, p_up):
    """Colonne nell'ordine che XGBoost espone: down < flat < up."""
    out = np.zeros((len(p_up), 3))
    out[:, TARGET_DOWN] = p_down
    out[:, TARGET_FLAT] = p_flat
    out[:, TARGET_UP] = p_up
    return out


def test_soglia_produce_la_frequenza_richiesta():
    rng = np.random.default_rng(0)
    p_up = rng.uniform(0, 1, 10_000)
    proba = _proba(1 - p_up, np.zeros(10_000), p_up)
    for q in (0.01, 0.02, 0.04, 0.10):
        s = soglia_per_frequenza(proba, q)
        conf = np.maximum(proba[:, TARGET_UP], proba[:, TARGET_DOWN])
        assert (conf > s).mean() == pytest.approx(q, abs=0.005)


def test_frequenza_appaiata_fra_distribuzioni_diverse():
    """Il punto dell'intero esperimento: due etichette con priorità di classe
    molto diverse devono generare la STESSA frequenza di segnale.

    Con soglia fissa a 0.50 non succede — è l'errore del primo pre-registro,
    dove il triple barrier (FLAT all'11.8%) tradava 4.4x."""
    rng = np.random.default_rng(1)
    # etichetta A: molto FLAT -> probabilità up/down basse (come orizzonte fisso)
    a_up = rng.beta(2, 6, 20_000)
    A = _proba(rng.beta(2, 6, 20_000), np.zeros(20_000), a_up)
    # etichetta B: poco FLAT -> probabilità up/down alte (come triple barrier)
    b_up = rng.beta(6, 2, 20_000)
    B = _proba(rng.beta(6, 2, 20_000), np.zeros(20_000), b_up)

    q = 0.02
    sa, sb = soglia_per_frequenza(A, q), soglia_per_frequenza(B, q)
    conf_a = np.maximum(A[:, TARGET_UP], A[:, TARGET_DOWN])
    conf_b = np.maximum(B[:, TARGET_UP], B[:, TARGET_DOWN])
    # Le soglie sono diverse...
    assert sb > sa
    # ...ma la frequenza di segnale è la stessa: il confronto è alla pari
    assert (conf_a > sa).mean() == pytest.approx((conf_b > sb).mean(), abs=0.005)
    # A soglia FISSA 0.50 invece B tradava molto di più: l'errore da correggere
    assert (conf_b > 0.5).mean() > (conf_a > 0.5).mean() * 3


def test_soglia_usa_il_max_di_up_e_down():
    """Il backtester apre se P(up) > soglia OPPURE P(down) > soglia: guardare
    solo una delle due sottostimerebbe la frequenza."""
    n = 1000
    # up sempre basso, down alto in metà dei casi
    p_up = np.full(n, 0.1)
    p_down = np.concatenate([np.full(n // 2, 0.9), np.full(n // 2, 0.1)])
    proba = _proba(p_down, np.zeros(n), p_up)
    s = soglia_per_frequenza(proba, 0.25)
    assert s > 0.5, "la soglia deve riflettere i P(down) alti, non solo P(up)"
