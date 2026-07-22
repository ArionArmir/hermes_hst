"""Definizioni di target alternative — lo spazio di ricerca del pre-registro.

Il target è la DOMANDA che poniamo al modello. Oggi in produzione è una sola:
"fra 5 ore il prezzo sarà oltre ±0.5%?" (feature_engine.py). Era il paradigma
di prova iniziale, l'architettura è cresciuta sopra, e non è mai stato
riesaminato. Qui viene parametrizzato per poterlo mettere alla prova.

Vedi docs/PRE_REGISTRO_TARGET.md: spazio, criteri e budget sono dichiarati lì,
PRIMA dei risultati.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd

from src.training.feature_engine import TARGET_DOWN, TARGET_FLAT, TARGET_UP

ATR_WINDOW = 14


@dataclass(frozen=True)
class TargetSpec:
    """Una definizione di target = una domanda posta al modello."""
    horizon: int            # barre di futuro osservate
    thr_kind: str           # 'fixed' (% assoluta) | 'atr' (multiplo di ATR)
    thr_val: float          # 0.005 se fixed, 1.0 se atr
    label: str              # 'fixed_horizon' | 'triple_barrier'

    @property
    def name(self) -> str:
        t = f"{self.thr_val:.3f}pct" if self.thr_kind == "fixed" else f"{self.thr_val:.1f}atr"
        return f"h{self.horizon}_{t}_{'tb' if self.label == 'triple_barrier' else 'fh'}"

    def as_dict(self) -> dict:
        return {"horizon": self.horizon, "thr_kind": self.thr_kind,
                "thr_val": self.thr_val, "label": self.label}


def search_space() -> list[TargetSpec]:
    """Le 48 configurazioni dichiarate nel pre-registro. Non allargare a run
    iniziato: il budget è ciò che fissa la soglia di significatività, e
    aggiungere tentativi dopo aver visto i primi risultati è esattamente come
    il conteggio ha perso senso il 2026-07-16."""
    horizons = (2, 5, 10, 20)
    thresholds = (("fixed", 0.003), ("fixed", 0.005), ("fixed", 0.010),
                  ("atr", 0.5), ("atr", 1.0), ("atr", 1.5))
    labels = ("fixed_horizon", "triple_barrier")
    return [TargetSpec(h, k, v, l) for h, (k, v), l in product(horizons, thresholds, labels)]


def _atr_pct(df: pd.DataFrame) -> pd.Series:
    """ATR relativo al prezzo, stessa formula di features.atr_pct."""
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(ATR_WINDOW).mean() / close


def _thresholds(df: pd.DataFrame, spec: TargetSpec) -> pd.Series:
    """Soglia per barra: costante, oppure scalata sulla volatilità.

    È l'ipotesi H1: una soglia fissa pone domande diverse in regimi diversi
    (la volatilità annualizzata varia 0.40-0.78 fra i fold), mentre un
    multiplo di ATR pone la stessa domanda ovunque.
    """
    if spec.thr_kind == "fixed":
        return pd.Series(spec.thr_val, index=df.index)
    return spec.thr_val * _atr_pct(df)


def _fixed_horizon(df: pd.DataFrame, spec: TargetSpec, thr: pd.Series):
    """Il target attuale, generalizzato: dov'è il prezzo dopo `horizon` barre?

    Ignora il percorso: +0.5% alla barra 5 è una vittoria anche se nel mezzo è
    sceso del 3% e lo stop sarebbe scattato (sospetto S3 del pre-registro).
    """
    fut = df["close"].shift(-spec.horizon) / df["close"] - 1
    target = pd.Series(TARGET_FLAT, index=df.index)
    target[fut > thr] = TARGET_UP
    target[fut < -thr] = TARGET_DOWN
    return target, fut.notna() & thr.notna()


def _triple_barrier(df: pd.DataFrame, spec: TargetSpec, thr: pd.Series):
    """Quale barriera viene toccata per prima: sopra, sotto, o tempo scaduto?

    È l'ipotesi H3: un'etichetta che descrive ciò che accade DAVVERO a un
    trade dovrebbe convertire meglio in PnL di una che guarda solo il punto
    d'arrivo. Usa high/low come fa il backtester per SL/TP.

    Se entrambe le barriere cadono nella stessa barra, l'ordine non è
    ricostruibile dall'OHLC: si etichetta FLAT invece di indovinare.
    """
    n = len(df)
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    t = thr.to_numpy()

    up_lvl = close * (1 + t)
    dn_lvl = close * (1 - t)
    first_up = np.full(n, np.inf)
    first_dn = np.full(n, np.inf)

    for j in range(1, spec.horizon + 1):
        h_j = np.full(n, np.nan); h_j[:n - j] = high[j:]
        l_j = np.full(n, np.nan); l_j[:n - j] = low[j:]
        with np.errstate(invalid="ignore"):
            hit_u = h_j >= up_lvl
            hit_d = l_j <= dn_lvl
        first_up = np.where(np.isinf(first_up) & hit_u, j, first_up)
        first_dn = np.where(np.isinf(first_dn) & hit_d, j, first_dn)

    target = np.full(n, TARGET_FLAT)
    target[first_up < first_dn] = TARGET_UP
    target[first_dn < first_up] = TARGET_DOWN
    # entrambe nella stessa barra -> ordine ignoto -> resta FLAT

    # Serve tutto l'orizzonte di futuro osservabile, altrimenti "nessuna
    # barriera toccata" è indistinguibile da "dati finiti"
    valid = np.zeros(n, dtype=bool)
    valid[: max(0, n - spec.horizon)] = True
    return pd.Series(target, index=df.index), pd.Series(valid, index=df.index) & thr.notna()


def make_target(df: pd.DataFrame, spec: TargetSpec) -> tuple[pd.Series, pd.Series]:
    """(target, valid) per una definizione. `valid` marca le righe con futuro
    osservabile: le altre vanno ESCLUSE, non etichettate FLAT — trasformarle in
    esempi negativi fittizi è un bug già visto in questo progetto."""
    thr = _thresholds(df, spec)
    if spec.label == "fixed_horizon":
        return _fixed_horizon(df, spec, thr)
    return _triple_barrier(df, spec, thr)
