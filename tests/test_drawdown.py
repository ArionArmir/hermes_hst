"""
Simulatore di drawdown (src/invest/drawdown.py).

La matematica qui decide una taglia di investimento reale: un errore di segno
o un episodio perso produrrebbe una decisione sbagliata con soldi veri.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.invest.drawdown import drawdown_episodes, simulate_dca


def _prezzi(valori, start="2020-01"):
    idx = pd.period_range(start, periods=len(valori), freq="M")
    return pd.DataFrame({"X": valori}, index=idx)


def test_dca_contabilita_base():
    # prezzo costante: il conto vale esattamente il versato
    out = simulate_dca(_prezzi([100.0] * 6), {"X": 1.0}, mensile=200)
    assert out.versato.iloc[-1] == 1200
    assert out.conto.iloc[-1] == pytest.approx(1200)


def test_dca_compra_di_piu_quando_il_prezzo_scende():
    # 100 -> 50 -> 100: il DCA compra a 50 e chiude in utile
    out = simulate_dca(_prezzi([100.0, 50.0, 100.0]), {"X": 1.0}, mensile=100)
    assert out.conto.iloc[-1] > out.versato.iloc[-1]
    assert out.conto.iloc[-1] == pytest.approx(100 + 200 + 100)  # quote 1+2+1 a 100


def test_versamento_iniziale():
    out = simulate_dca(_prezzi([100.0, 100.0]), {"X": 1.0}, mensile=100, iniziale=1000)
    assert out.versato.iloc[0] == 1100


def test_pesi_su_due_asset():
    idx = pd.period_range("2020-01", periods=2, freq="M")
    prezzi = pd.DataFrame({"A": [100.0, 200.0], "B": [100.0, 100.0]}, index=idx)
    out = simulate_dca(prezzi, {"A": 0.5, "B": 0.5}, mensile=100)
    # mese1: 0.5 quote A + 0.5 quote B; mese2: A raddoppia
    assert out.conto.iloc[0] == pytest.approx(100)
    assert out.conto.iloc[1] == pytest.approx(0.5 * 200 + 0.5 * 100 + 100)


def test_episodio_rilevato_con_profondita_e_recupero():
    # 100 -> 120 -> 60 -> 90 -> 130: dd del -50% dal picco 120, recupero a 130
    unit = pd.Series([100, 120, 60, 90, 130.0],
                     index=pd.period_range("2020-01", periods=5, freq="M"))
    eps = drawdown_episodes(unit, minimo=0.15)
    assert len(eps) == 1
    e = eps[0]
    assert e.profondita == pytest.approx(-0.5)
    assert str(e.picco) == "2020-02" and str(e.fondo) == "2020-03"
    assert str(e.recupero) == "2020-05"


def test_drawdown_ancora_aperto_a_fine_serie():
    unit = pd.Series([100, 120, 70.0],
                     index=pd.period_range("2020-01", periods=3, freq="M"))
    eps = drawdown_episodes(unit)
    assert len(eps) == 1 and eps[0].recupero is None


def test_cali_sotto_soglia_ignorati():
    unit = pd.Series([100, 95, 100.0],
                     index=pd.period_range("2020-01", periods=3, freq="M"))
    assert drawdown_episodes(unit, minimo=0.15) == []


def test_episodi_ordinati_dal_peggiore():
    unit = pd.Series([100, 80, 100, 50, 100.0],
                     index=pd.period_range("2020-01", periods=5, freq="M"))
    eps = drawdown_episodes(unit)
    assert eps[0].profondita < eps[1].profondita
