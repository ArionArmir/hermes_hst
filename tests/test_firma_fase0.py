"""Fase 0 firma liquidazioni (scripts/firma_fase0.py): l'aggancio.

Il gate del pre-registro (>=90%) si giudica su questa funzione: un bug qui
farebbe passare o fallire la fase per il motivo sbagliato.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from firma_fase0 import aggancia, filtra_sigillati

TS = pd.Timestamp("2026-07-20 10:00:00.500", tz="UTC")
TS_MS = int(TS.timestamp() * 1000)


def _etichetta(side="SELL", qty=1.0, prezzo=100.0):
    return pd.DataFrame([{"ts": TS, "symbol": "TESTUSDT", "side": side,
                          "qty": qty, "prezzo_medio": prezzo}])


def _tape(righe):
    return pd.DataFrame(righe, columns=["price", "quantity", "transact_time",
                                        "is_buyer_maker"])


def test_aggancio_riuscito():
    """Ordine forzato SELL = venditore aggressore = is_buyer_maker True."""
    tape = _tape([(100.0, 0.6, TS_MS + 100, True), (100.05, 0.5, TS_MS + 300, True)])
    assert aggancia(_etichetta(), tape).all()


def test_lato_sbagliato_non_aggancia():
    tape = _tape([(100.0, 2.0, TS_MS, False)])
    assert not aggancia(_etichetta(), tape).any()


def test_prezzo_fuori_banda_non_aggancia():
    tape = _tape([(101.0, 2.0, TS_MS, True)])          # +1% >> banda 0.1%
    assert not aggancia(_etichetta(), tape).any()


def test_quantita_insufficiente_non_aggancia():
    tape = _tape([(100.0, 0.5, TS_MS, True)])          # 50% < quota 80%
    assert not aggancia(_etichetta(), tape).any()


def test_finestra_esclude_trade_lontani():
    tape = _tape([(100.0, 2.0, TS_MS - 5000, True), (100.0, 2.0, TS_MS + 5000, True)])
    assert not aggancia(_etichetta(), tape).any()


def test_holdout_escluso():
    df = pd.DataFrame({"symbol": ["BTCUSDT", "BCHUSDT", "AAVEUSDT"]})
    rimasti = filtra_sigillati(df)["symbol"].tolist()
    assert rimasti == ["BTCUSDT"]                      # BCH lotto A, AAVE lotto B
