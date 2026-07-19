"""
Tripwire del carry (src/invest/tripwire.py).

Se scatta quando non deve, fa scrivere un pre-registro inutile; se non
scatta quando deve, il regime ricco passa inosservato. Entrambi i lati.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.invest.tripwire as T


def test_scatta_solo_con_due_ricco_consecutivi():
    stato = {"storia": [], "scattato": False}
    stato, ora = T.aggiorna(stato, "2026-08", "RICCO", 0.09)
    assert not ora and T.consecutivi_correnti(stato) == 1
    stato, ora = T.aggiorna(stato, "2026-09", "RICCO", 0.10)
    assert ora and stato["scattato"]


def test_interruzione_azzera_la_serie():
    stato = {"storia": [], "scattato": False}
    for mese, fascia in (("2026-08", "RICCO"), ("2026-09", "NELLA NORMA"),
                         ("2026-10", "RICCO")):
        stato, ora = T.aggiorna(stato, mese, fascia, 0.05)
    assert not ora and not stato["scattato"]
    assert T.consecutivi_correnti(stato) == 1


def test_idempotente_sullo_stesso_mese():
    """Rilanciare il rapporto nello stesso mese non deve contare doppio."""
    stato = {"storia": [], "scattato": False}
    stato, _ = T.aggiorna(stato, "2026-08", "RICCO", 0.09)
    stato, ora = T.aggiorna(stato, "2026-08", "RICCO", 0.09)
    assert not ora and len(stato["storia"]) == 1


def test_scattato_resta_scattato():
    """Una volta scattato non si disinnesca da solo: il reset e' umano."""
    stato = {"storia": [], "scattato": False}
    for mese in ("2026-08", "2026-09"):
        stato, _ = T.aggiorna(stato, mese, "RICCO", 0.09)
    stato, ora = T.aggiorna(stato, "2026-10", "COMPRESSO", 0.01)
    assert not ora and stato["scattato"]


def test_marker_scritto_solo_allo_scatto(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "STATE", tmp_path / "s.json")
    monkeypatch.setattr(T, "MARKER", tmp_path / "M")
    T.salva({"storia": [], "scattato": False}, scattato_ora=False)
    assert not (tmp_path / "M").exists()
    T.salva({"storia": [], "scattato": True}, scattato_ora=True)
    assert "pre-registro" in (tmp_path / "M").read_text()
