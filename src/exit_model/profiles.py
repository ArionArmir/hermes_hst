"""
Profili ATR per simbolo, condivisi tra engine live e backtester: entrambi
devono costruire ATRExitModel con gli stessi moltiplicatori, altrimenti il
backtest misura uscite diverse da quelle tradate.

I valori sono una stima iniziale basata sulla volatilità realizzata (vedi
commit sull'aggiunta di TRX/DOGE/BNB/XRP) — la taratura vera va fatta con il
backtester ora che l'ATR è calcolato su candele reali. Simbolo non presente
nel dizionario → fallback 5.0/5.5.
"""
from src.exit_model.atr_exit import ATRExitModel

DEFAULT_SL_MULTIPLIERS = {
    "BTCUSDT": 5.0,
    "ETHUSDT": 5.5,
    "SOLUSDT": 6.0,
    "DOGEUSDT": 8.0,
    "XRPUSDT": 6.0,
    "BNBUSDT": 4.5,
    "TRXUSDT": 4.0,
}
DEFAULT_TP_MULTIPLIERS = {
    "BTCUSDT": 5.5,
    "ETHUSDT": 6.0,
    "SOLUSDT": 6.5,
    "DOGEUSDT": 8.5,
    "XRPUSDT": 6.5,
    "BNBUSDT": 5.0,
    "TRXUSDT": 4.5,
}


def build_exit_model(symbol_upper: str) -> ATRExitModel:
    return ATRExitModel(
        atr_multiplier_sl=DEFAULT_SL_MULTIPLIERS.get(symbol_upper, 5.0),
        atr_multiplier_tp=DEFAULT_TP_MULTIPLIERS.get(symbol_upper, 5.5),
    )
