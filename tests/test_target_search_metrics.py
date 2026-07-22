"""
Metriche del motore di ricerca sul target.

La quota di concentrazione è un gate di promozione: se è rotta, promuove o
boccia per il motivo sbagliato. La prima versione (max/somma_netta) produceva
quote fino al 3420% con attribuzioni di segno misto.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_spec = importlib.util.spec_from_file_location(
    "target_search", Path(__file__).parent.parent / "scripts" / "target_search.py")
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


def _res(per_symbol_pnls, n_fold_pnls=(10.0, 10.0, 10.0, 10.0)):
    righe = []
    for sym, pnls in per_symbol_pnls.items():
        for p in pnls:
            righe.append({"symbol": sym, "pnl": p,
                          "ts": pd.Timestamp("2024-01-01")})
    return {"pnls": list(n_fold_pnls), "trades": pd.DataFrame(righe)}


def test_quota_mai_oltre_il_100_percento_con_segni_misti():
    """Il bug reale: un simbolo a +100 e uno a -90 danno somma netta 10,
    e 100/10 = 1000%. Una quota non può superare il 100%."""
    res = _res({"DOGEUSDT": [100.0] * 20, "BNBUSDT": [-90.0] * 20})
    m = ts._metriche(res)
    assert 0.0 <= m["quota_simbolo_top"] <= 1.0


def test_quota_e_la_frazione_del_profitto_lordo():
    # DOGE +60 lordo, SOL +40 lordo -> DOGE fa il 60%
    res = _res({"DOGEUSDT": [2.0] * 30, "SOLUSDT": [2.0] * 20})
    m = ts._metriche(res)
    assert m["simbolo_top"] == "DOGEUSDT"
    assert m["quota_simbolo_top"] == pytest.approx(0.6, abs=0.01)


def test_un_solo_simbolo_in_utile_da_quota_piena():
    res = _res({"DOGEUSDT": [5.0] * 20, "BNBUSDT": [-1.0] * 20})
    assert ts._metriche(res)["quota_simbolo_top"] == pytest.approx(1.0)


def test_nessun_simbolo_in_utile_viene_bocciato():
    """Senza profitto lordo la concentrazione non è definita: si boccia,
    non si promuove per assenza di prove."""
    res = _res({"DOGEUSDT": [-5.0] * 20, "BNBUSDT": [-1.0] * 20})
    assert ts._metriche(res)["quota_simbolo_top"] == 1.0


def test_pochi_trade_scartati():
    # Sotto i 30 trade non si misura nulla di sensato
    assert ts._metriche(_res({"DOGEUSDT": [1.0] * 5})) is None
