"""
Logica pura del paper executor (src/carry/paper.py).

Il paper esiste per misurare la divergenza backtest/realtà: se la sua
contabilità divergesse dal backtest per un bug, misureremmo il bug.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.carry.paper as P
from src.research.carry import COSTO_APERTURA, COSTO_CHIUSURA


@pytest.fixture
def stato(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "STATE_DIR", tmp_path)
    monkeypatch.setattr(P, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(P, "LEDGER", tmp_path / "ledger.jsonl")
    return P.carica_stato()


def _t(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_selezione_all_positive():
    medie = {"A": 0.0001, "B": -0.0001, "C": None, "D": 0.0}
    assert P.selezione_w30(medie) == {"A"}


def test_lunedi_00():
    # domenica 19 luglio 2026 -> il lunedì corrente è il 13
    assert P.ultimo_lunedi_00(_t("2026-07-19T20:00")) == _t("2026-07-13T00:00")
    # lunedì stesso, dopo mezzanotte -> oggi
    assert P.ultimo_lunedi_00(_t("2026-07-20T00:30")) == _t("2026-07-20T00:00")


def test_serve_ribilanciamento():
    assert P.serve_ribilanciamento(None, _t("2026-07-19T10:00"))
    # ribilanciato martedì scorso, oggi è domenica -> no
    assert not P.serve_ribilanciamento("2026-07-14T00:05+00:00", _t("2026-07-19T10:00"))
    # oggi è lunedì 00:30 e l'ultimo è della settimana scorsa -> sì
    assert P.serve_ribilanciamento("2026-07-14T00:05+00:00", _t("2026-07-20T00:30"))


def test_ciclo_completo_stessa_contabilita_del_backtest(stato):
    """Apri con basis 0.002, incassa 3 funding da 0.0001, chiudi a basis 0:
    PnL = funding + Δbasis − costi, identico a pnl_chiusura del backtest."""
    P.apri_posizione(stato, "X", 0.002, _t("2026-07-13T00:00"))
    P.accredita_funding(stato, "X", [(_t("2026-07-13T08:00"), 0.0001),
                                     (_t("2026-07-13T16:00"), 0.0001),
                                     (_t("2026-07-14T00:00"), 0.0001)])
    ev = P.chiudi_posizione(stato, "X", 0.0)
    atteso = (3 * 0.0001 * P.NOTIONAL + 0.002 * P.NOTIONAL
              - (COSTO_APERTURA + COSTO_CHIUSURA) * P.NOTIONAL)
    assert ev["pnl"] == pytest.approx(atteso)
    assert stato["pnl_realizzato"] == pytest.approx(atteso)
    assert stato["posizioni"] == {}


def test_funding_negativo_e_un_pagamento(stato):
    P.apri_posizione(stato, "X", 0.0, _t("2026-07-13T00:00"))
    P.accredita_funding(stato, "X", [(_t("2026-07-13T08:00"), -0.0002)])
    assert stato["posizioni"]["X"]["funding_incassato"] < 0
    # il high-water mark è il max fundingTime accreditato, non un pre-fetch
    assert stato["posizioni"]["X"]["ultimo_accredito"] == _t("2026-07-13T08:00").isoformat()


def test_stato_sopravvive_al_riavvio(stato):
    P.apri_posizione(stato, "X", 0.001, _t("2026-07-13T00:00"))
    P.salva_stato(stato, [{"evento": "apertura", "simbolo": "X"}])
    ricaricato = P.carica_stato()
    assert "X" in ricaricato["posizioni"]
    assert P.LEDGER.exists() and "apertura" in P.LEDGER.read_text()


def test_ciclo_con_funding_non_solleva_nameerror(monkeypatch):
    """Revisione branch (regressione): il ciclo emetteva len(rates) su una
    variabile rinominata → NameError in crash-loop al primo funding."""
    import src.carry.main as cm
    from datetime import datetime, timezone
    monkeypatch.setattr(cm, "_eventi_funding",
                        lambda sym, da: [(datetime.now(timezone.utc), 0.0001)])
    monkeypatch.setattr(cm, "serve_ribilanciamento", lambda *a, **k: False)
    stato = {"posizioni": {"BTCUSDT": {"aperta": "2026-07-20T00:00:00+00:00",
                                       "funding_incassato": 0.0, "notional": 100.0}},
             "funding_totale": 0.0}
    eventi = cm.ciclo(stato)                              # non deve sollevare
    assert any(e["evento"] == "funding" and e["eventi"] == 1 for e in eventi)
