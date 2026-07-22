"""Registratore liquidazioni Bybit (src/liquidations/bybit.py).

Stesso principio del gemello Binance: il dataset verrà giudicato fra mesi,
un bug di normalizzazione adesso è sporcizia scoperta troppo tardi.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.liquidations.bybit import lotti_sottoscrizione, normalizza
from src.liquidations.recorder import BufferGiornaliero

EVENTO = {"T": 1784500000123, "s": "ROSEUSDT", "S": "Sell",
          "v": "20000", "p": "0.04499"}


def test_normalizzazione_schema_binance():
    r = normalizza(EVENTO)
    assert r["symbol"] == "ROSEUSDT" and r["side"] == "Sell"
    assert r["qty"] == 20000.0 and r["prezzo_medio"] == 0.04499
    assert abs(r["notional_usdt"] - 20000 * 0.04499) < 1e-9
    assert r["ts"].tzinfo is not None
    # stesse colonne del registratore Binance: le analisi girano su entrambi
    assert set(r) == {"ts", "symbol", "side", "qty", "prezzo_medio", "notional_usdt"}


def test_evento_malformato_torna_none():
    assert normalizza({"s": "X"}) is None
    assert normalizza({**EVENTO, "v": "non-numero"}) is None


def test_sottoscrizione_a_lotti():
    lotti = lotti_sottoscrizione([f"S{i}USDT" for i in range(25)], batch=10)
    assert [len(l) for l in lotti] == [10, 10, 5]
    assert lotti[0][0] == "allLiquidation.S0USDT"


def test_buffer_riusato_su_directory_separata(tmp_path):
    b = BufferGiornaliero(out_dir=tmp_path)
    b.aggiungi(normalizza(EVENTO))
    assert b.flush() == 1
    df = pd.read_parquet(next(tmp_path.glob("*.parquet")))
    assert df.iloc[0]["symbol"] == "ROSEUSDT"


def test_bybit_non_deduplica_eventi_simultanei_distinti(tmp_path):
    """Revisione branch 2026-07-21: allLiquidation pubblica TUTTI gli eventi;
    due liquidazioni distinte con stessa (ts,symbol,qty) NON vanno collassate,
    o quota_censura misurerebbe una censura falsa verso zero."""
    ev = {"T": 1784500000000, "s": "ROSEUSDT", "S": "Sell", "v": "20000", "p": "0.045"}
    for _ in range(2):                                     # due flush separati
        b = BufferGiornaliero(out_dir=tmp_path, dedup=False)
        b.aggiungi(normalizza(dict(ev)))
        b.flush()
    df = pd.read_parquet(next(tmp_path.glob("*.parquet")))
    assert len(df) == 2                                    # entrambi conservati


def test_flush_fallito_non_duplica_gruppo_scritto(tmp_path, monkeypatch):
    """Revisione branch (regressione): con dedup=False, un flush che fallisce
    sul 2° day-group non deve riscrivere (duplicare) il 1° al retry."""
    import pandas as pd
    from datetime import datetime, timezone
    def riga(ts):
        return {"ts": ts, "symbol": "X", "side": "Sell", "qty": 1.0,
                "prezzo_medio": 1.0, "notional_usdt": 1.0}
    b = BufferGiornaliero(out_dir=tmp_path, dedup=False)
    b.righe = [riga(datetime(2026, 7, 20, 23, 59, tzinfo=timezone.utc)),
               riga(datetime(2026, 7, 21, 0, 1, tzinfo=timezone.utc))]
    orig = pd.DataFrame.to_parquet
    calls = {"n": 0}
    def flaky(self, path, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disco pieno")
        return orig(self, path, *a, **k)
    monkeypatch.setattr(pd.DataFrame, "to_parquet", flaky)
    try:
        b.flush()
    except OSError:
        pass
    assert len(b.righe) == 1                              # solo il gruppo fallito resta
    assert not list(tmp_path.glob("*.tmp"))               # niente .tmp orfano
    monkeypatch.setattr(pd.DataFrame, "to_parquet", orig)
    b.flush()
    assert len(pd.read_parquet(tmp_path / "2026-07-20.parquet")) == 1   # NO duplicato
