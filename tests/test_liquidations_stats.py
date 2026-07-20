"""Statistiche liquidazioni (src/liquidations/stats.py) — le funzioni che
finiscono in dashboard e rapporto mensile: un errore qui diventa un giudizio
sbagliato sulla tipicità del periodo o sulla salute dei dataset.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.liquidations.stats import (quota_censura, regime_mensile,
                                    salute_registratore)

ADESSO = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _ts(secondo, ms=0):
    return pd.Timestamp(2026, 7, 20, 11, 30, secondo, ms * 1000, tz="UTC")


def test_quota_censura_conta_oltre_il_primo_per_simbolo_secondo():
    """3 eventi nello stesso simbolo-secondo: 2 sarebbero nascosti; eventi in
    secondi o simboli diversi restano visibili."""
    df = pd.DataFrame({
        "ts": [_ts(1, 100), _ts(1, 400), _ts(1, 900), _ts(2), _ts(1)],
        "symbol": ["A", "A", "A", "A", "B"],
    })
    c = quota_censura(df)
    assert c["eventi"] == 5 and c["nascosti"] == 2
    assert abs(c["quota"] - 0.4) < 1e-9


def test_quota_censura_vuota():
    assert quota_censura(pd.DataFrame(columns=["ts", "symbol"])) is None


def test_salute_registratore(tmp_path):
    pd.DataFrame({"ts": [_ts(1), _ts(2)], "symbol": ["A", "B"]}).to_parquet(
        tmp_path / "2026-07-20.parquet")
    pd.DataFrame({"ts": [_ts(1)], "symbol": ["A"]}).to_parquet(
        tmp_path / "2026-07-19.parquet")
    s = salute_registratore(tmp_path, adesso=ADESSO)
    assert s["giorni_raccolti"] == 2 and s["eventi_oggi"] == 2
    assert s["eventi_ultima_ora"] == 2 and s["simboli_oggi"] == 2


def test_salute_directory_vuota(tmp_path):
    assert salute_registratore(tmp_path) is None


def test_regime_mensile_percentile_mediano():
    """Simbolo con mese corrente sopra tutto lo storico -> percentile alto;
    la mediana tra due simboli opposti finisce NELLA NORMA."""
    giorni = pd.date_range("2026-01-01", "2026-07-15", freq="D", tz="UTC")
    alto = pd.DataFrame({"t": giorni, "symbol": "SU",
                         "liq_long": [1.0] * (len(giorni) - 5) + [100.0] * 5,
                         "liq_short": 0.0})
    basso = pd.DataFrame({"t": giorni, "symbol": "GIU",
                          "liq_long": [100.0] * (len(giorni) - 5) + [1.0] * 5,
                          "liq_short": 0.0})
    r = regime_mensile(pd.concat([alto, basso]), "2026-07")
    assert r["percentili"]["SU"] > 0.9 and r["percentili"]["GIU"] < 0.1
    assert r["fascia"] == "NELLA NORMA"


def test_regime_mese_senza_dati():
    giorni = pd.date_range("2026-01-01", "2026-01-31", freq="D", tz="UTC")
    df = pd.DataFrame({"t": giorni, "symbol": "A", "liq_long": 1.0, "liq_short": 0.0})
    assert regime_mensile(df, "2026-07") is None
