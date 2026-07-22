"""Aggregazione esatta dei parquet 1h a timeframe piu' lunghi.

Nessun download, nessuna nuova fonte: 4h sono gli stessi prezzi di 1h,
aggregati. Serve al pre-registro timeframe (docs/PRE_REGISTRO_TIMEFRAME.md).

Il costo di un trade e' FISSO (0.14% round-trip) mentre il movimento cresce con
√tempo: se il modello cattura una frazione costante del movimento, il timeframe
lungo converte meglio per pura aritmetica. E' l'unica leva mai misurata che
cambia il COSTO RELATIVO invece del segnale.
"""
from __future__ import annotations

import pandas as pd

# open/high/low/close aggregano come ci si aspetta; volume, n_trades e
# taker_buy_base sono flussi e vanno SOMMATI. Sommarli e' cio' che rende
# l'aggregazione esatta invece che approssimata: le feature di order flow
# derivate (taker_buy_ratio, trade_intensity, avg_trade_size_ratio) restano
# coerenti perche' sono rapporti fra somme.
AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "n_trades": "sum",
    "taker_buy_base": "sum",
}

# ore per barra: fissa la scala della soglia (la volatilita' cresce con √tempo)
ORE_PER_BARRA = {"1h": 1, "2h": 2, "4h": 4, "8h": 8, "1d": 24}

_REGOLA = {"2h": "2h", "4h": "4h", "8h": "8h", "1d": "1D"}


def soglia_scalata(tf: str, base_1h: float = 0.005) -> float:
    """Soglia del target che tiene COSTANTE il tasso di eventi fra timeframe.

    La volatilita' scala con √tempo, quindi la soglia deve fare lo stesso: una
    soglia fissa su timeframe diversi confronterebbe filtri invece che ipotesi
    (e' l'errore che ha reso ininterpretabile il braccio triple barrier di H3).

    Regola derivata, non manopola: non consuma tentativi.
    """
    return base_1h * (ORE_PER_BARRA[tf] ** 0.5)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Aggrega candele 1h al timeframe `tf`. '1h' restituisce l'originale."""
    if tf == "1h":
        return df
    if tf not in _REGOLA:
        raise ValueError(f"timeframe non previsto dal pre-registro: {tf}")
    out = df.resample(_REGOLA[tf]).agg(AGG)
    # Le finestre senza candele 1h vanno tolte, non riempite: un forward-fill
    # inventerebbe prezzi mai scambiati.
    #
    # Si filtra su `close` e non con dropna(how="all"): sum() su un gruppo
    # vuoto restituisce 0, non NaN, quindi una finestra vuota produce una riga
    # con prezzi NaN ma volume 0 — che how="all" NON scarta. Quelle righe
    # avvelenerebbero le feature. `close` NaN e' il testimone affidabile che
    # nella finestra non e' stata scambiata alcuna candela.
    return out[out["close"].notna()]
