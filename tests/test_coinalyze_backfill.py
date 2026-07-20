"""Backfill Coinalyze (scripts/coinalyze_backfill.py): le funzioni pure.

Il dataset aggregato serve da metro di paragone per quello live: un errore
di normalizzazione qui falserebbe il giudizio sul registratore fra mesi.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from coinalyze_backfill import normalizza_storia, unisci

STORIA = [{"t": 1784530800, "l": 1234.5, "s": 678.9},
          {"t": 1784534400, "l": 0, "s": 42.0}]


def test_normalizzazione():
    df = normalizza_storia("BTCUSDT_PERP.A", STORIA)
    assert list(df.columns) == ["t", "symbol", "liq_long", "liq_short"]
    assert len(df) == 2 and (df["symbol"] == "BTCUSDT_PERP.A").all()
    assert df["t"].dt.tz is not None          # UTC esplicito, come il registratore
    assert df.iloc[0]["liq_long"] == 1234.5


def test_storia_vuota_non_esplode():
    df = normalizza_storia("X", [])
    assert len(df) == 0 and "liq_long" in df.columns


def test_valori_non_numerici_scartati():
    df = normalizza_storia("X", [{"t": 1784530800, "l": "boh", "s": 1.0}])
    assert len(df) == 0


def test_unione_rilanciabile_deduplica():
    """Due run sulla stessa finestra non devono raddoppiare le ore, e la
    ri-scarica più recente deve vincere (ore parziali corrette)."""
    a = normalizza_storia("X", STORIA)
    b = normalizza_storia("X", [{"t": 1784534400, "l": 99.0, "s": 42.0}])
    df = unisci(a, b)
    assert len(df) == 2
    assert df[df["t"] == pd.Timestamp(1784534400, unit="s", tz="UTC")]["liq_long"].item() == 99.0


def test_unione_con_vecchio_none():
    df = unisci(None, normalizza_storia("X", STORIA))
    assert len(df) == 2
