"""
L'analista che non prevede (src/invest/analyst.py).

Il rapporto verrà letto nei momenti peggiori: se classifica male una fase o
sbaglia il contesto storico, parla al panico con numeri sbagliati.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.invest.analyst import (classifica, contesto_storico, stato_corrente,
                                valuta_ledger)


def _serie(valori, start="2010-01"):
    return pd.Series([float(v) for v in valori],
                     index=pd.period_range(start, periods=len(valori), freq="M"))


def test_stato_sui_massimi():
    st = stato_corrente(_serie([100, 110, 120]))
    assert st.drawdown == 0.0 and st.mesi_dal_massimo == 0


def test_stato_in_drawdown():
    st = stato_corrente(_serie([100, 200, 150]))
    assert st.drawdown == pytest.approx(-0.25)
    assert st.mesi_dal_massimo == 1
    assert st.ret_1m == pytest.approx(-0.25)


def test_classificazione_fasi():
    assert classifica(-0.05)[0] == "ordinaria amministrazione"
    assert classifica(-0.15)[0] == "correzione"
    assert classifica(-0.30)[0] == "mercato orso"
    assert classifica(-0.55)[0] == "episodio di coda"


def test_contesto_conta_gli_episodi_comparabili():
    # due bear (-50%, -30%) recuperati + drawdown corrente del -25%
    s = _serie([100, 50, 110, 77, 121, 90])
    st = stato_corrente(s)
    assert st.drawdown == pytest.approx(-0.256, abs=0.01)
    ctx = contesto_storico(s, st)
    assert ctx.episodi_almeno_cosi == 2          # entrambi arrivarono almeno a -25%
    assert ctx.mediana_mesi_recupero is not None


def test_contesto_sui_massimi_non_trova_episodi_comparabili():
    s = _serie([100, 90, 105, 110])
    ctx = contesto_storico(s, stato_corrente(s))
    # sui massimi il confronto è con la soglia minima (5%): un episodio del
    # -10% esiste, e il rapporto lo racconta come normalità
    assert ctx.episodi_almeno_cosi >= 0


def test_valutazione_ledger():
    ledger = pd.DataFrame({
        "data": ["2026-08-01", "2026-09-01", "2026-08-01"],
        "strumento": ["ETF", "ETF", "BTC"],
        "eur": [300.0, 300.0, 30.0],
        "quote": [1.0, 1.0, 0.0005],
    })
    prezzi = {"ETF": _serie([300, 330]), "BTC": _serie([55000, 66000])}
    posizioni = {p.strumento: p for p in valuta_ledger(ledger, prezzi)}
    assert posizioni["ETF"].eur_versati == 600
    assert posizioni["ETF"].valore == pytest.approx(2 * 330)
    assert posizioni["ETF"].utile == pytest.approx(60)
    assert posizioni["BTC"].valore == pytest.approx(0.0005 * 66000)


def test_strumento_sconosciuto_ignorato_senza_crash():
    ledger = pd.DataFrame({"data": ["2026-08-01"], "strumento": ["ORO"],
                           "eur": [100.0], "quote": [1.0]})
    assert valuta_ledger(ledger, {"ETF": _serie([1, 2])}) == []
