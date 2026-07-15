"""
Fixture globali della suite.

isolated_store_db è AUTOUSE: qualunque test che passi (anche indirettamente)
da src/shared/store scrive su un database temporaneo, mai su data/hermes.db.
Senza questa protezione i test di engine/sentiment inquinerebbero il
database di produzione con segnali fittizi (successo già: 21 righe di test
finite nel db reale prima di questa fixture).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.shared import store


@pytest.fixture(autouse=True)
def isolated_store_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "hermes_test.db")
