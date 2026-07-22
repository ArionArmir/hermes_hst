"""Metriche e normalizzazioni condivise dai motori di ricerca.

Estratte da scripts/target_search.py perché il motore H3 usa le stesse: due
copie divergerebbero, e un gate di promozione che si comporta diversamente fra
esperimenti renderebbe i risultati non confrontabili.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.shared.holdout import deflated_sharpe_ratio
from src.training.feature_engine import TARGET_DOWN, TARGET_UP

MIN_TRADE = 30


def soglia_per_frequenza(proba: np.ndarray, q: float) -> float:
    """Soglia di probabilità che fa scattare un segnale sul `q` delle barre.

    Il backtester apre se P(up) > soglia oppure P(down) > soglia: la frequenza
    di segnale dipende quindi dal MAX delle due.

    Va calcolata sulle probabilità del set di CALIBRAZIONE (che è dentro il
    train), mai del test: prenderla dal test sarebbe lookahead e gonfierebbe
    ogni risultato.

    Serve a confrontare etichette diverse a parità di occasioni prese. Con
    soglia fissa il confronto è truccato: cambiare etichetta cambia le
    probabilità a priori delle classi (FLAT crolla dal 47.9% all'11.8% col
    triple barrier), quindi la stessa soglia diventa un filtro molto più largo.
    """
    conf = np.maximum(proba[:, TARGET_UP], proba[:, TARGET_DOWN])
    return float(np.quantile(conf, 1.0 - q))


def metriche(pnls: list[float], trades: pd.DataFrame, n_trials: int) -> dict | None:
    """Metriche di una configurazione, o None se i trade non bastano a misurare."""
    n = len(trades)
    if n < MIN_TRADE:
        return None
    pnl = trades["pnl"].to_numpy()
    sd = pnl.std(ddof=1)
    sr = pnl.mean() / sd if sd > 0 else 0.0
    attr = trades.groupby("symbol")["pnl"].sum()
    # Quota sul profitto LORDO (soli simboli in utile), non sul netto: dividere
    # per la somma netta esplode con attribuzioni di segno misto (+100 e -90
    # danno 100/10 = 1000%). La prima versione arrivava al 3420%.
    lordo = attr.clip(lower=0).sum()
    quota = (attr.max() / lordo) if lordo > 0 else 1.0
    return {
        "pnl_totale": round(float(sum(pnls)), 2),
        "worst_fold": round(float(min(pnls)), 2),
        "fold_positivi": int(sum(1 for p in pnls if p > 0)),
        "n_trade": n,
        "sharpe_trade": round(float(sr), 4),
        "dsr": round(float(deflated_sharpe_ratio(pnl, n_trials)), 4),
        "quota_simbolo_top": round(float(quota), 3),
        "simbolo_top": str(attr.idxmax()) if len(attr) else "",
    }


def bootstrap_mensile(trades: pd.DataFrame, n_boot: int = 10_000) -> dict | None:
    """IC 95% del PnL totale, ricampionando i MESI e non i singoli trade.

    I trade dentro un mese sono correlati (i simboli si muovono insieme):
    ricampionarli singolarmente assumerebbe un'indipendenza che non c'è e
    restringerebbe l'intervallo a torto.
    """
    per_mese = trades.groupby(trades["ts"].dt.to_period("M"))["pnl"].sum().to_numpy()
    if len(per_mese) < 6:
        return None
    rng = np.random.default_rng(42)
    boot = rng.choice(per_mese, (n_boot, len(per_mese)), replace=True).sum(axis=1)
    return {"ic95_basso": round(float(np.percentile(boot, 2.5)), 1),
            "ic95_alto": round(float(np.percentile(boot, 97.5)), 1),
            "p_perdita": round(float(np.mean(boot <= 0)), 4)}
