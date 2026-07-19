"""
Registratore di liquidazioni (src/liquidations/recorder.py).

Il dataset nasce oggi e verra' giudicato fra mesi: un bug di normalizzazione
adesso significa mesi di dati sporchi scoperti troppo tardi.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.liquidations.recorder import BufferGiornaliero, normalizza

PAYLOAD = {"e": "forceOrder", "E": 1784500000000,
           "o": {"s": "BTCUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC",
                 "q": "0.014", "p": "64000", "ap": "64100.5", "X": "FILLED",
                 "l": "0.014", "z": "0.014", "T": 1784500000123}}


def test_normalizzazione():
    r = normalizza(PAYLOAD)
    assert r["symbol"] == "BTCUSDT" and r["side"] == "SELL"
    assert r["qty"] == 0.014 and r["prezzo_medio"] == 64100.5
    assert r["notional_usdt"] == pytest.approx(0.014 * 64100.5)
    assert r["ts"].tzinfo is not None


def test_payload_malformato_torna_none():
    assert normalizza({"e": "forceOrder"}) is None
    assert normalizza({"o": {"s": "X", "z": "non-numero"}}) is None


def test_flush_scrive_parquet_giornaliero(tmp_path):
    b = BufferGiornaliero(out_dir=tmp_path, max_righe=100)
    b.aggiungi(normalizza(PAYLOAD))
    assert b.flush() == 1
    files = list(tmp_path.glob("*.parquet"))
    assert len(files) == 1
    df = pd.read_parquet(files[0])
    assert len(df) == 1 and df.iloc[0]["symbol"] == "BTCUSDT"


def test_restart_non_duplica(tmp_path):
    """Il merge col file esistente deve deduplicare: un riavvio che rielabora
    lo stesso evento non deve contarlo due volte."""
    for _ in range(2):
        b = BufferGiornaliero(out_dir=tmp_path)
        b.aggiungi(normalizza(PAYLOAD))
        b.flush()
    df = pd.read_parquet(next(tmp_path.glob("*.parquet")))
    assert len(df) == 1


def test_max_righe_chiede_flush(tmp_path):
    b = BufferGiornaliero(out_dir=tmp_path, max_righe=2)
    assert not b.aggiungi(normalizza(PAYLOAD))
    p2 = dict(PAYLOAD, o=dict(PAYLOAD["o"], T=1784500001123))
    assert b.aggiungi(normalizza(p2))
