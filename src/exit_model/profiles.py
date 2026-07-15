"""
Profili ATR per simbolo, condivisi tra engine live e backtester: entrambi
devono costruire ATRExitModel con gli stessi moltiplicatori, altrimenti il
backtest misura uscite diverse da quelle tradate.

Valori tarati il 2026-07-16 con tune_strategy.py (griglia soglia×SL×TP su
due finestre out-of-sample: ultimo 20% e ultimi 45 giorni): SL=3×ATR e
TP=3×ATR uniformi battono i vecchi valori per-simbolo (5.0-8.5), che erano
stati stimati quando l'ATR era calcolato su high/low sintetici. Il TP è
risultato quasi irrilevante (con max holding a 5 barre l'uscita dominante è
temporale); l'SL è robusto nella zona 2-4. I dizionari restano per-simbolo
per consentire una futura taratura individuale (servono più mesi di dati).
"""
from src.exit_model.atr_exit import ATRExitModel

TUNED_SL = 3.0
TUNED_TP = 3.0

DEFAULT_SL_MULTIPLIERS = {
    "BTCUSDT": TUNED_SL,
    "ETHUSDT": TUNED_SL,
    "SOLUSDT": TUNED_SL,
    "DOGEUSDT": TUNED_SL,
    "XRPUSDT": TUNED_SL,
    "BNBUSDT": TUNED_SL,
    "TRXUSDT": TUNED_SL,
}
DEFAULT_TP_MULTIPLIERS = {
    "BTCUSDT": TUNED_TP,
    "ETHUSDT": TUNED_TP,
    "SOLUSDT": TUNED_TP,
    "DOGEUSDT": TUNED_TP,
    "XRPUSDT": TUNED_TP,
    "BNBUSDT": TUNED_TP,
    "TRXUSDT": TUNED_TP,
}


def build_exit_model(symbol_upper: str) -> ATRExitModel:
    return ATRExitModel(
        atr_multiplier_sl=DEFAULT_SL_MULTIPLIERS.get(symbol_upper, TUNED_SL),
        atr_multiplier_tp=DEFAULT_TP_MULTIPLIERS.get(symbol_upper, TUNED_TP),
    )
