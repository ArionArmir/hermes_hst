"""
Definizioni di target alternative (docs/PRE_REGISTRO_TARGET.md).

Un bug qui non fa fallire nulla: produce 48 risultati plausibili e sbagliati.
Il target è la domanda posta al modello, e una domanda mal formulata dà
un'ottima risposta inutile.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research.target_space import TargetSpec, make_target, search_space
from src.training.feature_engine import TARGET_DOWN, TARGET_FLAT, TARGET_UP


def _candles(closes, highs=None, lows=None):
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.DataFrame(
        {"open": closes,
         "high": highs if highs is not None else [c * 1.001 for c in closes],
         "low": lows if lows is not None else [c * 0.999 for c in closes],
         "close": closes, "volume": 10.0, "n_trades": 5.0, "taker_buy_base": 5.0},
        index=idx,
    )


def test_spazio_e_esattamente_il_budget_dichiarato():
    # Il budget fissa la soglia-fortuna: allargarlo invalida il pre-registro
    spazio = search_space()
    assert len(spazio) == 48
    assert len(set(s.name for s in spazio)) == 48


def test_orizzonte_fisso_riproduce_il_target_di_produzione():
    """h=5, soglia 0.5% fissa deve dare esattamente il target attuale."""
    # +2% costante per barra: sempre UP a 5 barre
    closes = [100 * (1.02 ** i) for i in range(20)]
    df = _candles(closes)
    spec = TargetSpec(5, "fixed", 0.005, "fixed_horizon")
    target, valid = make_target(df, spec)
    assert (target[valid] == TARGET_UP).all()

    # -2% per barra: sempre DOWN
    closes = [100 * (0.98 ** i) for i in range(20)]
    target, valid = make_target(_candles(closes), spec)
    assert (target[valid] == TARGET_DOWN).all()

    # piatto: sempre FLAT
    target, valid = make_target(_candles([100.0] * 20), spec)
    assert (target[valid] == TARGET_FLAT).all()


def test_ultime_barre_escluse_non_etichettate_flat():
    """Senza futuro osservabile la riga va ESCLUSA. Etichettarla FLAT la
    trasformerebbe in un esempio negativo fittizio: bug già visto qui."""
    df = _candles([100.0] * 20)
    for label in ("fixed_horizon", "triple_barrier"):
        _, valid = make_target(df, TargetSpec(5, "fixed", 0.005, label))
        assert not valid.iloc[-5:].any(), f"{label}: ultime 5 barre devono essere invalide"
        assert valid.iloc[:10].any()


def test_soglia_atr_si_adatta_alla_volatilita():
    """H1: la stessa mossa è UP in regime calmo e FLAT in regime agitato.
    È l'intero motivo per cui sospettiamo la soglia fissa."""
    rng = np.random.default_rng(0)
    spec = TargetSpec(5, "atr", 1.0, "fixed_horizon")

    # regime calmo: range ristretto, poi salita dell'1%
    calmo = [100 + rng.normal(0, 0.02) for _ in range(30)] + [101.0] * 10
    df_calmo = _candles(calmo, highs=[c * 1.0005 for c in calmo],
                        lows=[c * 0.9995 for c in calmo])
    t_calmo, v_calmo = make_target(df_calmo, spec)

    # regime agitato: stesso +1% finale ma ATR molto più largo
    agitato = [100 + rng.normal(0, 3.0) for _ in range(30)] + [101.0] * 10
    df_agit = _candles(agitato, highs=[c * 1.05 for c in agitato],
                       lows=[c * 0.95 for c in agitato])
    t_agit, v_agit = make_target(df_agit, spec)

    # nel calmo l'1% supera 1 ATR -> eventi non-FLAT; nell'agitato no
    eventi_calmo = (t_calmo[v_calmo] != TARGET_FLAT).sum()
    eventi_agit = (t_agit[v_agit] != TARGET_FLAT).sum()
    assert eventi_calmo > eventi_agit


def test_triple_barrier_usa_il_percorso_non_il_punto_di_arrivo():
    """H3, il caso che separa le due etichette: il prezzo torna a 0 alla barra
    5 ma nel mezzo ha toccato -3%. Orizzonte fisso: FLAT. Triple barrier: DOWN
    (lo stop sarebbe scattato)."""
    closes = [100, 100, 97, 98, 99, 100, 100, 100, 100, 100]
    lows = [c * 0.999 for c in closes]
    lows[2] = 97.0                      # tocca -3% alla barra 2
    df = _candles(closes, lows=lows)

    fh, _ = make_target(df, TargetSpec(5, "fixed", 0.02, "fixed_horizon"))
    tb, _ = make_target(df, TargetSpec(5, "fixed", 0.02, "triple_barrier"))
    assert fh.iloc[0] == TARGET_FLAT, "orizzonte fisso guarda solo l'arrivo: 100 -> 100"
    assert tb.iloc[0] == TARGET_DOWN, "triple barrier vede il -3% toccato per primo"


def test_triple_barrier_prende_la_prima_barriera():
    # sale del 5% alla barra 1, poi crolla: vince la barriera SOPRA
    closes = [100, 105, 90, 90, 90, 90]
    df = _candles(closes, highs=[c * 1.001 for c in closes], lows=[c * 0.999 for c in closes])
    tb, _ = make_target(df, TargetSpec(4, "fixed", 0.02, "triple_barrier"))
    assert tb.iloc[0] == TARGET_UP


def test_triple_barrier_nessuna_barriera_toccata_e_flat():
    df = _candles([100.0] * 20)
    tb, valid = make_target(df, TargetSpec(5, "fixed", 0.05, "triple_barrier"))
    assert (tb[valid] == TARGET_FLAT).all()


def test_triple_barrier_ambiguo_resta_flat():
    """Entrambe le barriere nella stessa barra: l'ordine non è ricostruibile
    dall'OHLC. Indovinare introdurrebbe un bias sistematico."""
    df = _candles([100, 100, 100], highs=[100.5, 110, 100.5], lows=[99.5, 90, 99.5])
    tb, _ = make_target(df, TargetSpec(2, "fixed", 0.02, "triple_barrier"))
    assert tb.iloc[0] == TARGET_FLAT


def test_orizzonte_piu_lungo_produce_piu_eventi():
    """Coerenza: più tempo per muoversi = più barriere toccate."""
    rng = np.random.default_rng(1)
    closes = list(100 + np.cumsum(rng.normal(0, 0.3, 200)))
    df = _candles(closes)
    eventi = []
    for h in (2, 5, 10, 20):
        t, v = make_target(df, TargetSpec(h, "fixed", 0.005, "fixed_horizon"))
        eventi.append((t[v] != TARGET_FLAT).mean())
    assert eventi == sorted(eventi), f"eventi non monotoni con l'orizzonte: {eventi}"
