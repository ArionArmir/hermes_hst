"""
Throttle per scritture ad alta frequenza (es. prezzi tick → Redis).

L'engine e l'inference ricevono ogni singolo trade Binance: su BTC/ETH sono
centinaia di messaggi al secondo nei picchi, e scrivere latest_price_* e
last_tick_* a ogni tick moltiplica il carico su Redis senza beneficio — la
dashboard si aggiorna ogni 5 s e il watchdog ha soglie di minuti. Lo stato
in memoria (latest_prices, controlli di uscita) resta aggiornato a ogni
tick: si limita SOLO la persistenza.
"""
import time
from typing import Dict


class WriteThrottle:
    def __init__(self, interval_seconds: float = 0.5):
        self.interval = interval_seconds
        self._last: Dict[str, float] = {}

    def ready(self, key: str) -> bool:
        """True se per questa chiave è passato almeno `interval` dall'ultimo
        via libera (e in tal caso registra il nuovo timestamp)."""
        now = time.monotonic()
        last = self._last.get(key)
        if last is None or now - last >= self.interval:
            self._last[key] = now
            return True
        return False
