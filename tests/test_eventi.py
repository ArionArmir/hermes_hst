"""Osservatore eventi (src/eventi/osservatore.py): cursori, dedup e le
derivazioni per fonte. Il feed è memoria del sistema: un evento perso o
duplicato è una storia raccontata male.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eventi.osservatore import (eventi_watchdog, leggi_eventi,
                                    nuovi_da_ledger, nuovi_da_signals,
                                    nuovi_da_trades, registra_eventi)


def _signals(righe):
    return pd.DataFrame(righe, columns=["id", "timestamp", "symbol", "action",
                                        "confidence", "weighted_confidence",
                                        "outcome", "detail"])


def test_signals_cursore_e_tipi():
    df = _signals([(1, "2026-07-21T00:00:00", "BTCUSDT", "buy", 0.6, 0.55, "OPENED", ""),
                   (2, "2026-07-21T01:00:00", "ETHUSDT", "sell", 0.5, 0.51,
                    "SENTIMENT_VETO", "sentiment -0.7")])
    eventi, cursore = nuovi_da_signals(df, 0)
    assert cursore == 2 and len(eventi) == 2
    assert eventi[0]["tipo"] == "trade_forward" and eventi[0]["severita"] == "allarme"
    assert eventi[1]["tipo"] == "veto_sentiment"
    # secondo giro: niente di nuovo
    ancora, cursore = nuovi_da_signals(df, cursore)
    assert ancora == [] and cursore == 2


def test_trades_chiusi():
    df = pd.DataFrame([{"id": 7, "timestamp": "2026-07-21T02:00:00",
                        "symbol": "SOLUSDT", "side": "long", "entry": 1, "exit": 2,
                        "pnl": 3.21, "reason": "take_profit"}])
    eventi, cursore = nuovi_da_trades(df, 0)
    assert cursore == 7 and "+3.21" in eventi[0]["titolo"]


def test_ledger_solo_ribilanciamenti():
    righe = [json.dumps({"ts": "t", "evento": "funding", "simbolo": "X"}),
             json.dumps({"ts": "t", "evento": "ribilanciamento", "posizioni": 35,
                         "selezionati": 35}),
             "riga rotta {"]
    eventi, cursore = nuovi_da_ledger(righe, 0)
    assert len(eventi) == 1 and cursore == 3
    assert "35 posizioni" in eventi[0]["titolo"]


def test_transizioni_watchdog_e_deriva():
    eventi = eventi_watchdog({"config drift": "soglia 0.55", "engine": "fermo"},
                             ["sentiment"])
    tipi = {e["tipo"] for e in eventi}
    assert "deriva" in tipi and "watchdog" in tipi
    assert sum(e["severita"] == "allarme" for e in eventi) == 2
    assert any(e["titolo"].startswith("Rientrato") for e in eventi)


def test_registra_deduplica_stesso_giorno(tmp_path):
    path = tmp_path / "eventi.jsonl"
    e = eventi_watchdog({"engine": "fermo"}, [])
    assert registra_eventi(e, path) == 1
    assert registra_eventi(e, path) == 0            # stessa chiave, stesso giorno
    assert len(leggi_eventi(10, path)) == 1


def test_fire_drill_completo(tmp_path):
    """Un evento per fonte attraversa scrittura e lettura, ordine dal più
    recente: il giro completo che il widget consumerà."""
    path = tmp_path / "eventi.jsonl"
    segnali, _ = nuovi_da_signals(_signals(
        [(1, "2026-07-21T00:00:00", "BTCUSDT", "buy", 0.6, 0.55, "OPENED", "")]), 0)
    ledger, _ = nuovi_da_ledger(
        [json.dumps({"ts": "2026-07-21T01:00:00", "evento": "ribilanciamento",
                     "posizioni": 35, "selezionati": 35})], 0)
    watchdog = eventi_watchdog({}, ["carry"])
    assert registra_eventi(segnali + ledger + watchdog, path) == 3
    letti = leggi_eventi(10, path)
    assert len(letti) == 3
    assert letti[0]["titolo"].startswith("Rientrato")   # l'ultimo scritto esce primo
