"""Contabilita' del carry delta-neutro (docs/PRE_REGISTRO_CARRY.md).

Posizione = long spot 1x + short perp 1x sullo stesso simbolo. Delta zero:
il PnL viene da tre soli termini, tutti in frazione del notional:

  + funding incassati    (lo short riceve il rate quando e' positivo)
  + basis_entrata - basis_uscita   (short perp guadagna se il perp scende
                                    rispetto allo spot; basis=(perp-spot)/spot)
  - costi                (0.07% per gamba: taker 0.05% + slippage 0.02%;
                          2 gambe in apertura + 2 in chiusura = 0.28%/ciclo)

Nessun parametro appreso: le regole sono dichiarate nel pre-registro.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

COSTO_GAMBA = 0.0005 + 0.0002          # taker + slippage, per gamba
COSTO_APERTURA = 2 * COSTO_GAMBA       # spot buy + perp sell
COSTO_CHIUSURA = 2 * COSTO_GAMBA


@dataclass
class Posizione:
    simbolo: str
    apertura: pd.Timestamp
    basis_entrata: float
    funding_incassato: float = 0.0     # cumulato, in frazione del notional


def basis(perp: float, spot: float) -> float:
    return (perp - spot) / spot


def pnl_chiusura(pos: Posizione, basis_uscita: float) -> float:
    """PnL totale del ciclo, in frazione del notional."""
    return (pos.funding_incassato
            + pos.basis_entrata - basis_uscita
            - COSTO_APERTURA - COSTO_CHIUSURA)


def funding_medio_trailing(funding: pd.DataFrame, quando: pd.Timestamp,
                           giorni: int) -> float | None:
    """Media dei funding negli ultimi `giorni` PRIMA di `quando`.

    Solo eventi con calc_time < quando: usare l'evento contestuale al
    ribilanciamento sarebbe gia' lookahead (il rate delle 00:00 si conosce
    alle 00:00, ma la decisione va presa con l'informazione precedente per
    poter essere eseguita a quel prezzo).
    """
    da = quando - pd.Timedelta(days=giorni)
    m = (funding["calc_time"] >= da) & (funding["calc_time"] < quando)
    if not m.any():
        return None
    return float(funding.loc[m, "last_funding_rate"].mean())


def seleziona(medie: dict[str, float], regola: str) -> set[str]:
    """`all-positive`: chi ha funding medio > 0. `top-10`: i 10 migliori."""
    validi = {s: v for s, v in medie.items() if v is not None}
    if regola == "all-positive":
        return {s for s, v in validi.items() if v > 0}
    if regola == "top-10":
        return set(sorted(validi, key=validi.get, reverse=True)[:10])
    raise ValueError(regola)


def funding_incassato_tra(funding: pd.DataFrame, da: pd.Timestamp,
                          a: pd.Timestamp) -> float:
    """Somma dei rate con calc_time in (da, a]: lo short perp li riceve
    (positivi) o li paga (negativi)."""
    m = (funding["calc_time"] > da) & (funding["calc_time"] <= a)
    return float(funding.loc[m, "last_funding_rate"].sum())
