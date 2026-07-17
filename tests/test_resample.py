"""
Aggregazione 1h -> timeframe lunghi (docs/PRE_REGISTRO_TIMEFRAME.md).

Un errore qui non fa fallire nulla: produce 5 risultati plausibili e falsi.
L'aggregazione deve essere ESATTA - se lo fosse solo approssimativamente,
misureremmo un mercato che non e' esistito.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research.resample import ORE_PER_BARRA, resample, soglia_scalata


def _candele(n=8, start="2024-01-01"):
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame(
        {"open": range(100, 100 + n),
         "high": range(110, 110 + n),
         "low": range(90, 90 + n),
         "close": range(105, 105 + n),
         "volume": 10.0, "n_trades": 5.0, "taker_buy_base": 6.0},
        index=idx,
    ).rename_axis("timestamp")


def test_ohlc_aggrega_correttamente():
    df = _candele(8)
    out = resample(df, "4h")
    assert len(out) == 2
    prima = out.iloc[0]
    assert prima["open"] == 100          # primo open della finestra
    assert prima["high"] == 113          # max dei 4 high (110..113)
    assert prima["low"] == 90            # min dei 4 low (90..93)
    assert prima["close"] == 108         # ultimo close (105..108)


def test_i_flussi_sono_sommati_non_mediati():
    """volume/n_trades/taker_buy_base sono flussi: mediarli dimezzerebbe il
    mercato e falserebbe le feature di order flow."""
    out = resample(_candele(8), "4h")
    assert out.iloc[0]["volume"] == 40.0          # 4 x 10
    assert out.iloc[0]["n_trades"] == 20.0        # 4 x 5
    assert out.iloc[0]["taker_buy_base"] == 24.0  # 4 x 6


def test_i_rapporti_di_order_flow_restano_coerenti():
    """taker_buy_ratio e' un rapporto fra somme: deve sopravvivere
    all'aggregazione, altrimenti le 4 feature di flow misurerebbero altro."""
    df = _candele(8)
    atteso = df["taker_buy_base"].iloc[:4].sum() / df["volume"].iloc[:4].sum()
    out = resample(df, "4h")
    assert out.iloc[0]["taker_buy_base"] / out.iloc[0]["volume"] == pytest.approx(atteso)


def test_nessuna_barra_persa_o_inventata():
    df = _candele(24)
    for tf, atteso in (("2h", 12), ("4h", 6), ("8h", 3), ("1d", 1)):
        assert len(resample(df, tf)) == atteso, tf
    # il volume totale si conserva: nulla creato, nulla distrutto
    for tf in ("2h", "4h", "8h", "1d"):
        assert resample(df, tf)["volume"].sum() == pytest.approx(df["volume"].sum())


def test_1h_e_identita():
    df = _candele(8)
    assert resample(df, "1h") is df


def test_buchi_non_riempiti_con_prezzi_inventati():
    """Un forward-fill inventerebbe prezzi mai scambiati."""
    df = pd.concat([_candele(4, "2024-01-01"), _candele(4, "2024-01-02")])
    out = resample(df, "4h")
    assert len(out) == 2                      # non 7 (le finestre vuote spariscono)
    assert not out.isna().any().any()


def test_soglia_scala_con_radice_del_tempo():
    """La volatilita' cresce con √tempo: la soglia deve seguirla, altrimenti
    confrontiamo filtri invece che ipotesi (l'errore di H3)."""
    assert soglia_scalata("1h") == pytest.approx(0.005)
    assert soglia_scalata("4h") == pytest.approx(0.010)      # 0.005 x √4
    assert soglia_scalata("1d") == pytest.approx(0.005 * 24 ** 0.5)
    # monotona e crescente
    valori = [soglia_scalata(tf) for tf in ("1h", "2h", "4h", "8h", "1d")]
    assert valori == sorted(valori)


def test_timeframe_fuori_dal_pre_registro_solleva():
    with pytest.raises(ValueError, match="pre-registro"):
        resample(_candele(), "3h")
