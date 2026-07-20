"""Sentiment v2 (src/sentiment/v2.py) — le funzioni pure.

La v2 verrà giudicata su criteri scritti prima (docs/CRITERI_SENTIMENT_V2.md):
decadimento, novità, guardia anti-degenerazione e parsing sono i mattoni di
quel giudizio.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.sentiment.v2 import combina, decadi, degenere, estrai_score, novita

ADESSO = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def test_decadimento_mezza_vita():
    assert abs(decadi(0.8, 360) - 0.4) < 1e-9        # una mezza vita
    assert abs(decadi(0.8, 720) - 0.2) < 1e-9        # due
    assert decadi(0.8, 0) == 0.8


def test_novita_riconosce_titoli_gia_visti():
    nuovi, viste = novita(["Bitcoin crolla", "ETF approvato"], {}, ADESSO)
    assert len(nuovi) == 2
    dopo, viste = novita(["bitcoin CROLLA  ", "Notizia inedita"], viste, ADESSO)
    assert dopo == ["Notizia inedita"]               # case/spazi non contano


def test_novita_pota_la_memoria_vecchia():
    _, viste = novita(["vecchia"], {}, ADESSO - timedelta(days=10))
    nuovi, viste = novita(["vecchia"], viste, ADESSO)
    assert len(nuovi) == 1                           # dimenticata dopo 7 giorni


def test_novita_per_asset():
    """Una notizia di mercato vista per BTC resta nuova per ETH: la novità
    vive nello spazio dell'asset, non globale."""
    nuovi, viste = novita(["Fed alza i tassi"], {}, ADESSO, spazio="BTC")
    assert len(nuovi) == 1
    nuovi, viste = novita(["Fed alza i tassi"], viste, ADESSO, spazio="ETH")
    assert len(nuovi) == 1
    nuovi, _ = novita(["Fed alza i tassi"], viste, ADESSO, spazio="BTC")
    assert nuovi == []


def test_combina_e_clamp():
    import pytest
    assert combina(0.4, 0.8) == pytest.approx(0.6)
    assert combina(-1.0, -1.0) == -1.0


def test_estrai_score():
    assert estrai_score('{"score": -0.7}') == -0.7
    assert estrai_score('{"score": 5}') is None      # fuori range
    assert estrai_score("non json") is None
    assert estrai_score('{"altro": 1}') is None


def test_degenere_identici_e_scala():
    assert degenere({"A": 0.5, "B": 0.5, "C": 0.5})
    assert degenere({"A": -0.8, "B": -0.7, "C": -0.6, "D": -0.5})   # la scala di v1
    assert not degenere({"A": 0.0, "B": 0.0, "C": 0.0})             # zeri: non degenere
    assert not degenere({"A": 0.3, "B": -0.2, "C": 0.7})
