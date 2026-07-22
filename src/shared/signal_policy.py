"""
Regola di decisione dei segnali: unica fonte di verità, usata SIA
dall'inference live SIA dal backtester. Se la regola cambia qui, cambia
ovunque — una divergenza tra le due farebbe misurare al backtest una
strategia diversa da quella tradata.
"""

# Soglia unica e SIMMETRICA per emettere un segnale: P(up) > soglia → buy,
# P(down) > soglia → sell. Il vecchio criterio short "P(rialzo) < 0.4" era
# soddisfatto anche dal mercato laterale (docs/IMPROVEMENT_PLAN.md, S2).
# Questo è solo il DEFAULT/fallback: il valore operativo è
# config.ml_confidence_threshold (unico regolatore, tarabile da dashboard).
# 0.55 tarato il 2026-07-16 con tune_strategy.py su due finestre
# out-of-sample: sopra 0.60 i segnali quasi spariscono e il PnL crolla.
SIGNAL_PROB_THRESHOLD = 0.55


def signal_from_proba(p_down: float, p_up: float,
                      threshold: float = SIGNAL_PROB_THRESHOLD) -> tuple:
    """Decisione simmetrica sulle 3 classi. Restituisce (azione, confidenza):
    la confidenza è la probabilità della direzione scelta, o la migliore
    delle due per un hold."""
    p_down = float(p_down)
    p_up = float(p_up)
    if p_up > threshold and p_up > p_down:
        return "buy", p_up
    if p_down > threshold and p_down > p_up:
        return "sell", p_down
    return "hold", max(p_up, p_down)
