"""
Semaforo del carry (src/research/carry_monitor.py) — le parti pure.

Il semaforo descrive un regime a chi decidera' se riattivare una strategia:
un'annualizzazione sbagliata mostrerebbe un regime che non esiste.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research.carry_monitor import (annualizza_basis, annualizza_funding,
                                        fascia_regime, scadenza_da_simbolo)


def test_annualizzazione_funding():
    # 0.01% a evento, 3 eventi/giorno -> ~10.95% annuo
    assert annualizza_funding([0.0001] * 90) == pytest.approx(0.1095)
    assert annualizza_funding([]) == 0.0


def test_fasce_descrittive():
    assert fascia_regime(0.005)[0] == "COMPRESSO"
    assert fascia_regime(0.05)[0] == "NELLA NORMA"
    assert fascia_regime(0.12)[0] == "RICCO"
    # i confini vengono dalla storia misurata: 2% (soglia operativa carry_v1)
    # e 8% (mediana storica)
    assert fascia_regime(0.02)[0] == "NELLA NORMA"
    assert fascia_regime(0.08)[0] == "RICCO"


def test_annualizzazione_basis():
    # +0.73% in 68.25 giorni -> ~3.9% annuo (il caso reale del 2026-07-19)
    assert annualizza_basis(64922.5, 64452.0, 68.25) == pytest.approx(0.039, abs=0.002)
    # basis negativo (backwardation) resta negativo
    assert annualizza_basis(99.0, 100.0, 91) < 0
    # protezioni sui degeneri
    assert annualizza_basis(100.0, 100.0, 0) == 0.0


def test_scadenza_da_simbolo():
    s = scadenza_da_simbolo("BTCUSDT_260925")
    assert (s.year, s.month, s.day, s.hour) == (2026, 9, 25, 8)
    assert s.tzinfo is not None and s.utcoffset().total_seconds() == 0


def test_pubblica_semaforo_schema_per_dashboard(monkeypatch):
    """Il servizio pubblica su Redis tutto ciò che la dashboard mostra senza
    ricalcolare: mediana, fascia, percentile, positivi/totale, basis, ts.
    Un campo mancante manderebbe in errore il render del tab Carry."""
    import json
    import src.carry.main as cm

    monkeypatch.setattr(cm, "funding_corrente",
                        lambda: {"mediana": 0.04, "positivi": 30, "totale": 40})
    monkeypatch.setattr(cm, "percentile_storico", lambda m: 0.33)
    monkeypatch.setattr(cm, "basis_corrente",
                        lambda: {"BTC": {"basis_annuo": 0.039, "giorni": 66}})

    class _R:
        def __init__(self): self.store = {}
        def set(self, k, v): self.store[k] = v

    r = _R()
    cm.pubblica_semaforo(r)
    sem = json.loads(r.store["carry_semaforo"])
    for campo in ("mediana", "fascia", "nota", "percentile", "positivi", "totale", "basis", "ts"):
        assert campo in sem, f"manca {campo}"
    assert sem["basis"]["BTC"]["giorni"] == 66


def test_pubblica_semaforo_funding_assente_non_scrive(monkeypatch):
    """API giù → funding_corrente None → non pubblica nulla (la dashboard
    mostra 'in attesa', non un semaforo vuoto)."""
    import src.carry.main as cm
    monkeypatch.setattr(cm, "funding_corrente", lambda: None)

    class _R:
        def __init__(self): self.store = {}
        def set(self, k, v): self.store[k] = v

    r = _R()
    cm.pubblica_semaforo(r)
    assert "carry_semaforo" not in r.store
