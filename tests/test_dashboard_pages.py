"""
Smoke test delle pagine dashboard con il runtime di test ufficiale di
Streamlit (AppTest): le pagine devono renderizzare senza eccezioni, con il
db isolato dalla fixture di conftest (tabelle vuote → stati "nessun dato").
Richiede Redis attivo (come l'ambiente di sviluppo).

NOTA: eseguire questa catena home→analysis in uno script AppTest standalone
(fuori da pytest) con il db REALE può segfaultare in pyarrow durante la
costruzione di stringhe pandas: è un artefatto del bare-mode che mescola
plotly e serializzazione arrow nello stesso processo, non un bug delle
pagine (verificato: pagine singole ok con dati reali, catena ok in pytest,
catena verso pagine senza DataFrame ok, server reale ok). Non inseguirlo.
"""
import sys
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "dashboard"))

from src.shared import store
from utils import formatting


def test_dashboard_home_and_analysis_render_without_exceptions():
    at = AppTest.from_file(str(REPO / "dashboard" / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception, at.exception

    at.switch_page("app_pages/analysis.py")
    at.run()
    assert not at.exception, at.exception


def test_load_trades_prefers_sqlite_store(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # nessun CSV qui: deve leggere dallo store
    store.insert_trade(symbol="BTCUSDT", side="long", entry=100.0, exit_price=110.0,
                       pnl=9.9, reason="TEST", capital_after=1009.9)

    df = formatting.load_trades()

    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "BTCUSDT"
    assert "capital_after" in df.columns


def test_load_trades_falls_back_to_csv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # store vuoto (db isolato) → fallback CSV
    (tmp_path / "data").mkdir()
    pd.DataFrame([{
        "timestamp": "2026-07-14T12:00:00+00:00", "symbol": "ETHUSDT", "side": "long",
        "entry": 1800.0, "exit": 1810.0, "pnl": 0.8, "reason": "LEGACY",
    }]).to_csv(tmp_path / "data" / "trades_history.csv", index=False)

    df = formatting.load_trades()

    assert len(df) == 1
    assert df.iloc[0]["reason"] == "LEGACY"
