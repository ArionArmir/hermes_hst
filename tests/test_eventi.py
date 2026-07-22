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


def test_cascate_soglie_e_rilevamento():
    """Fase 2: soglie dal formato Coinalyze, rilevamento per simbolo e di
    mercato, chiave di dedup con l'ora dentro."""
    from datetime import datetime, timezone
    from src.eventi.cascate import rileva, soglie_da_storico
    orario = pd.DataFrame({
        "symbol": ["BTCUSDT_PERP.A"] * 200 + ["ETHUSDT_PERP.A"] * 200,
        "liq_long": [1.0] * 199 + [100.0] + [10.0] * 199 + [1000.0],
        "liq_short": 0.0,
    })
    soglie = soglie_da_storico(orario)
    assert set(soglie) == {"BTCUSDT", "ETHUSDT"}
    assert 1.0 < soglie["BTCUSDT"] <= 100.0          # P99.5 sopra il grosso, sotto il max

    adesso = datetime(2026, 7, 21, 12, 30, tzinfo=timezone.utc)
    dentro = pd.Timestamp(adesso) - pd.Timedelta(minutes=10)
    recorder = pd.DataFrame({
        "ts": [dentro, dentro, pd.Timestamp(adesso) - pd.Timedelta(hours=3)],
        "symbol": ["BTCUSDT", "BTCUSDT", "ETHUSDT"],
        "qty": [80.0, 80.0, 99999.0],                # ETH fuori finestra: non conta
    })
    eventi = rileva(recorder, soglie, adesso)
    assert len(eventi) == 1
    assert "BTCUSDT" in eventi[0]["titolo"] and eventi[0]["severita"] == "nota"
    assert eventi[0]["chiave"] == "cascata:BTCUSDT:2026-07-21 12"


def test_cascata_di_mercato_a_tre_simboli():
    from datetime import datetime, timezone
    from src.eventi.cascate import rileva
    adesso = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    t = pd.Timestamp(adesso) - pd.Timedelta(minutes=5)
    recorder = pd.DataFrame({"ts": [t] * 3,
                             "symbol": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                             "qty": [10.0, 10.0, 10.0]})
    eventi = rileva(recorder, {"BTCUSDT": 5, "ETHUSDT": 5, "SOLUSDT": 5}, adesso)
    assert len(eventi) == 4                          # 3 simboli + 1 di mercato
    assert any("MERCATO" in e["titolo"] for e in eventi)


def test_cascata_stessa_ora_non_duplica(tmp_path):
    from datetime import datetime, timezone
    from src.eventi.cascate import rileva
    adesso = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    t = pd.Timestamp(adesso) - pd.Timedelta(minutes=5)
    recorder = pd.DataFrame({"ts": [t], "symbol": ["BTCUSDT"], "qty": [10.0]})
    path = tmp_path / "eventi.jsonl"
    e1 = rileva(recorder, {"BTCUSDT": 5}, adesso)
    e2 = rileva(recorder, {"BTCUSDT": 5}, adesso)    # il giro dopo, stessa ora
    assert registra_eventi(e1, path) == 1
    assert registra_eventi(e2, path) == 0


def test_sezione_mensile_conta_e_elenca_allarmi(tmp_path):
    """Fase 3: il mese in eventi nel rapporto — conteggi per tipo, allarmi
    mai sommersi nella somma."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mr", Path(__file__).parent.parent / "scripts" / "monthly_report.py")
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    path = tmp_path / "eventi.jsonl"
    path.write_text("\n".join([
        json.dumps({"ts": "2026-07-05T10:00:00", "tipo": "carry", "severita": "info",
                    "titolo": "Ribilanciamento", "chiave": "k1"}),
        json.dumps({"ts": "2026-07-06T11:00:00", "tipo": "deriva", "severita": "allarme",
                    "titolo": "Allarme: config drift", "chiave": "k2"}),
        json.dumps({"ts": "2026-06-30T10:00:00", "tipo": "carry", "severita": "info",
                    "titolo": "Fuori mese", "chiave": "k3"}),
    ]) + "\n")
    righe = mr.sezione_eventi("2026-07", path)
    testo = "\n".join(righe)
    assert "carry: 1" in testo and "deriva: 1" in testo
    assert "config drift" in testo and "Fuori mese" not in testo


def test_annunci_estrazione_e_impatto():
    """Delisting: estrazione coppie dal corpo, allarme solo se tocca i
    nostri universi, dedup per codice articolo."""
    from src.eventi.annunci import estrai_coppie, eventi_da_annunci
    corpo = "<p>At 2026-07-24, pairs ALPHA/USDT, BETA/BTC will be removed</p>"
    assert estrai_coppie(corpo) == {"ALPHAUSDT", "BETABTC"}

    articoli = [{"code": "abc", "title": "Notice of Removal - 2026-07-24"}]
    universi = {"carry": {"ALPHAUSDT"}, "motore": {"BTCUSDT"}}
    eventi, visti = eventi_da_annunci(articoli, {"abc": corpo}, universi, [])
    assert eventi[0]["severita"] == "allarme" and "carry" in eventi[0]["dettaglio"]
    # già visto: niente doppioni
    ancora, visti = eventi_da_annunci(articoli, {"abc": corpo}, universi, visti)
    assert ancora == []
    # notice che non ci tocca: info
    eventi2, _ = eventi_da_annunci([{"code": "xyz", "title": "Notice"}],
                                   {"xyz": "GAMMA/USDT removed"}, universi, [])
    assert eventi2[0]["severita"] == "info"


def test_depeg_soglia_e_chiave_oraria():
    from datetime import datetime, timezone
    from src.eventi.annunci import evento_depeg
    adesso = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    assert evento_depeg(1.0006, adesso) is None            # 0.06%: pace
    e = evento_depeg(0.9938, adesso)                       # 0.62%: allarme
    assert e["severita"] == "allarme" and e["chiave"] == "depeg:USDC:2026-07-21 12"


def test_delisting_matcha_simboli_concatenati():
    """Revisione branch 2026-07-21: le notice futures elencano i simboli
    CONCATENATI (TRXUSDT); il vecchio match solo BASE/QUOTE era cieco a un
    delisting di un nostro simbolo → nessun allarme."""
    from src.eventi.annunci import simboli_citati, eventi_da_annunci
    assert simboli_citati("Will Delist TRXUSDT, 1000SHIBUSDT Perpetual",
                          {"TRXUSDT", "BTCUSDT"}) == {"TRXUSDT"}
    assert simboli_citati("Delist BTC/USDT", {"BTCUSDT"}) == {"BTCUSDT"}   # anche slash
    # il simbolo nel solo titolo fa scattare l'allarme
    articoli = [{"code": "z", "title": "Binance Futures Will Delist TRXUSDT Perpetual"}]
    eventi, _ = eventi_da_annunci(articoli, {"z": ""}, {"motore": {"TRXUSDT", "BTCUSDT"}}, [])
    assert eventi[0]["severita"] == "allarme" and "TRXUSDT" in eventi[0]["dettaglio"]


def test_dedup_ora_granulare_tiene_ricorrenze(tmp_path):
    """Revisione branch 2026-07-21: allarme@10 + riallarme@15 lo stesso giorno
    non devono collassare (il flapping deve restare visibile), ma un duplicato
    nella STESSA ora sì."""
    from src.eventi.osservatore import registra_eventi, _evento
    path = tmp_path / "eventi.jsonl"
    a = _evento("deriva", "allarme", "Allarme: config drift"); a["ts"] = "2026-07-21T10:05:00"
    b = dict(a); b["ts"] = "2026-07-21T15:00:00"            # 5 ore dopo
    c = dict(a); c["ts"] = "2026-07-21T10:50:00"            # stessa ora del primo
    assert registra_eventi([a], path) == 1
    assert registra_eventi([b], path) == 1                 # ora diversa: tenuto
    assert registra_eventi([c], path) == 0                 # stessa ora: dedup


def test_check_annunci_non_consuma_finestra_su_errore(monkeypatch):
    """Revisione branch 2026-07-21: un errore di rete non deve consumare la
    finestra di 30 min lasciando cieco il controllo delisting."""
    import src.eventi.annunci as ann
    def boom(*a, **k):
        raise ann.requests.exceptions.ConnectionError("giù")
    monkeypatch.setattr(ann.requests, "get", boom)
    cursori = {}
    import pytest
    with pytest.raises(Exception):
        ann.check_annunci(cursori)
    assert "annunci_ultimo_check" not in cursori           # cursore NON avanzato
