"""
Contabilita' del carry (docs/PRE_REGISTRO_CARRY.md).

Un errore di segno qui ribalta l'esito dell'esperimento senza far fallire
nulla: il basis e il funding hanno entrambi direzioni facili da invertire.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research.carry import (COSTO_APERTURA, COSTO_CHIUSURA, Posizione,
                                basis, funding_incassato_tra,
                                funding_medio_trailing, pnl_chiusura,
                                seleziona)


def _f(times, rates):
    return pd.DataFrame({"calc_time": pd.to_datetime(times),
                         "last_funding_rate": rates})


def test_funding_positivo_e_un_incasso_per_lo_short():
    f = _f(pd.date_range("2024-01-01", periods=3, freq="8h"), [0.001] * 3)
    tot = funding_incassato_tra(f, pd.Timestamp("2023-12-31"),
                                pd.Timestamp("2024-01-02"))
    assert tot == pytest.approx(0.003)          # riceve, non paga


def test_basis_che_scende_e_un_guadagno():
    """Short perp + long spot: se il perp scende rispetto allo spot dopo
    l'entrata, la posizione guadagna. E' IL segno da non sbagliare."""
    pos = Posizione("X", pd.Timestamp("2024-01-01"), basis_entrata=0.002)
    guadagno = pnl_chiusura(pos, basis_uscita=0.000)
    perdita = pnl_chiusura(pos, basis_uscita=0.004)
    assert guadagno > perdita
    assert guadagno - perdita == pytest.approx(0.004)


def test_ciclo_senza_funding_ne_basis_perde_esattamente_i_costi():
    pos = Posizione("X", pd.Timestamp("2024-01-01"), basis_entrata=0.0)
    assert pnl_chiusura(pos, 0.0) == pytest.approx(-(COSTO_APERTURA + COSTO_CHIUSURA))
    assert COSTO_APERTURA + COSTO_CHIUSURA == pytest.approx(0.0028)  # da pre-registro


def test_trailing_esclude_l_evento_contestuale():
    """L'evento delle 00:00 del giorno di ribilanciamento non deve entrare
    nella media decisa alle 00:00: sarebbe lookahead."""
    f = _f(["2024-01-01 00:00", "2024-01-07 16:00", "2024-01-08 00:00"],
           [0.0001, 0.0001, 0.5])
    m = funding_medio_trailing(f, pd.Timestamp("2024-01-08 00:00"), giorni=30)
    assert m == pytest.approx(0.0001)           # lo 0.5 delle 00:00 escluso


def test_selezione_all_positive_e_top10():
    medie = {f"S{i}": v for i, v in enumerate([0.002, 0.001, -0.001, None] + [0.0005] * 12)}
    ap = seleziona(medie, "all-positive")
    assert "S0" in ap and "S2" not in ap and "S3" not in ap
    t10 = seleziona(medie, "top-10")
    assert len(t10) == 10 and "S0" in t10 and "S2" not in t10


def test_basis_definizione():
    assert basis(perp=101.0, spot=100.0) == pytest.approx(0.01)
    assert basis(perp=99.0, spot=100.0) == pytest.approx(-0.01)
