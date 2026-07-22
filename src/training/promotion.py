"""
Decisione di promozione multi-fold (docs/IMPROVEMENT_PLAN.md, V3/N3): un
singolo confronto sull'ultima finestra di validation (~15-20 trade) è
rumore statistico — il walk-forward ha mostrato varianza enorme tra fold
sullo stesso periodo (+8 / −33 USDT). Valuta champion e challenger (già
addestrati, NESSUN retraining aggiuntivo: solo più backtest) su più
sotto-finestre non sovrapposte ricavate dalla stessa validation già
riservata — promuove solo se il challenger vince nella MAGGIORANZA dei
fold E non è peggiore nel FOLD PEGGIORE di ciascuno: un modello che vince
"in media" ma affonda in un solo scenario avverso non è meglio, è solo
diverso (esattamente il pattern del fold catastrofico scoperto con
walk_forward.py).

Perché solo sulla validation riservata e non su tutto lo storico: il
champion è stato addestrato in un run precedente su un cutoff più vecchio,
ma i dati continuano a crescere ogni settimana (update_historical) — usare
finestre troppo indietro nel tempo rischierebbe di testare il champion su
dati che ha già visto in training (leakage), dandogli un vantaggio
sleale. La validation riservata di QUESTO run (l'ultimo 20% dei dati
aggiornati) è garantita fuori-campione per il challenger per costruzione,
e nella pratica lo è anche per il champion (più vecchio, quindi la
finestra è ancora più "nuova" per lui).
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger

from src.backtest import BacktestParams, backtest_joint
from src.backtest.backtester import _align_common_index
from src.shared.features import MIN_CANDLES

DEFAULT_N_FOLDS = 3


@dataclass
class MultiFoldVerdict:
    promote: bool
    reason: str
    challenger_pnls: List[float]
    champion_pnls: List[float]


def build_fold_windows(candles_by_symbol: Dict[str, pd.DataFrame],
                       n_folds: int = DEFAULT_N_FOLDS) -> List[Dict[str, pd.DataFrame]]:
    """n_folds finestre non sovrapposte e consecutive, ciascuna con il
    proprio warmup di MIN_CANDLES barre. Lista vuota se lo storico comune
    ai simboli non basta per n_folds finestre statisticamente sensate —
    il chiamante deve ripiegare su un confronto a finestra singola."""
    common_index = _align_common_index(candles_by_symbol)
    usable = len(common_index) - MIN_CANDLES
    if usable <= 0 or n_folds <= 0:
        return []
    fold_len = usable // n_folds
    if fold_len < MIN_CANDLES:  # ogni fold deve poter scaldare le proprie feature
        return []

    windows = []
    for k in range(n_folds):
        start = k * fold_len
        end = start + MIN_CANDLES + fold_len if k < n_folds - 1 else len(common_index)
        fold_index = common_index[start:end]
        windows.append({sym: df.loc[fold_index] for sym, df in candles_by_symbol.items()})
    return windows


def decide_promotion(challenger, champion, val_candles: Dict[str, pd.DataFrame],
                     backtest_params: Optional[BacktestParams] = None,
                     n_folds: int = DEFAULT_N_FOLDS) -> Optional[MultiFoldVerdict]:
    """Confronta due modelli già addestrati su n_folds sotto-finestre della
    validation riservata. None se lo storico non basta per n_folds finestre
    sensate: il chiamante deve ripiegare su un confronto più semplice
    (mai un verdetto "finto" con dati insufficienti sotto)."""
    windows = build_fold_windows(val_candles, n_folds)
    if not windows:
        return None

    # NOTA (confound transitorio noto): challenger e champion passano lo stesso
    # prob_threshold in backtest_params. Alla PRIMA promozione dopo l'introduzione
    # della calibrazione, il challenger è calibrato (sigmoid) e il champion su
    # disco può essere ancora grezzo: probabilità su scale diverse → conteggio
    # trade diverso a parità di soglia, quindi il confronto di quel singolo ciclo
    # è viziato dallo shift di scala, non solo dalla skill. Si auto-risolve dal
    # ciclo successivo (anche il champion è calibrato). Non compensato qui a
    # posta: una ri-taratura della soglia per-modello sarebbe più fragile del
    # difetto che risolve.
    challenger_pnls, champion_pnls = [], []
    for i, window in enumerate(windows):
        c_result = backtest_joint(challenger, window, backtest_params)
        h_result = backtest_joint(champion, window, backtest_params)
        c_pnl = c_result.net_pnl if c_result is not None else 0.0
        h_pnl = h_result.net_pnl if h_result is not None else 0.0
        challenger_pnls.append(c_pnl)
        champion_pnls.append(h_pnl)
        logger.info(f"  Fold {i + 1}/{len(windows)}: challenger {c_pnl:+.2f} vs champion {h_pnl:+.2f} USDT")

    wins = sum(1 for c, h in zip(challenger_pnls, champion_pnls) if c > h)
    majority = wins > len(windows) / 2
    worst_challenger = min(challenger_pnls)
    worst_champion = min(champion_pnls)
    not_worse_in_worst_case = worst_challenger >= worst_champion

    promote = majority and not_worse_in_worst_case
    reason = (
        f"vinti {wins}/{len(windows)} fold, fold peggiore challenger {worst_challenger:+.2f} "
        f"vs fold peggiore champion {worst_champion:+.2f} USDT"
    )
    return MultiFoldVerdict(promote, reason, challenger_pnls, champion_pnls)
