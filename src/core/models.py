"""
Modelli dati per Project Hermes HFT
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from decimal import Decimal

class Position(BaseModel):
    symbol: str
    side: str  # 'long' o 'short'
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: float
    take_profit: float
    trailing_stop: Optional[float] = None
    entry_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pnl: float = 0.0
    is_open: bool = True

    def to_dict(self) -> dict:
        return self.model_dump()

class Signal(BaseModel):
    symbol: str
    action: str  # 'buy', 'sell', 'hold', 'close'
    confidence: float  # 0-1
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str  # 'ml' o 'sentiment'

    def to_dict(self) -> dict:
        return self.model_dump()

class Config(BaseModel):
    leverage: int = 3
    stop_loss_pct: float = 0.01
    take_profit_pct: float = 0.02
    max_position_size_usdt: float = 200.0
    trailing_stop_pct: float = 0.005
    # Frazione massima del capitale impegnabile come margine, sommando TUTTE
    # le posizioni aperte: l'engine rifiuta le aperture oltre questo cap.
    max_exposure: float = 0.5
    # Numero massimo di posizioni aperte simultaneamente nella STESSA
    # direzione (long o short), su simboli diversi. Con crypto altamente
    # correlate, max_exposure da solo non basta: al sizing attuale (~50 USDT
    # di margine/posizione) restano ampi margini di manovra anche con 6-7
    # simboli aperti nella stessa direzione — un ribasso sincrono del
    # mercato li chiude quasi tutti in stop loss insieme (scoperto con
    # walk_forward.py, docs/IMPROVEMENT_PLAN.md). Tarato a 3 con
    # walk_forward.py --folds 4: sotto questo valore il fold peggiore
    # migliora sensibilmente senza sacrificare i fold buoni.
    max_positions_same_direction: int = 3
    # Fee taker Binance Futures (0,05% per lato), simulate a ogni chiusura
    # per un PnL paper realistico.
    taker_fee_pct: float = 0.0005
    min_volatility_threshold: float = 0.001
    max_volatility_threshold: float = 0.02
    volatility_adjustment: bool = True
    symbols: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    # Deve coincidere con il timeframe delle candele di training
    # (train_all_models.py): l'inference lo usa per scaricare le candele su
    # cui calcola le feature.
    timeframe: str = '1h'
    ml_confidence_threshold: float = 0.55
    sentiment_weight: float = 0.3
    sentiment_asset_enabled: bool = True
    reverse_trading_enabled: bool = True
    # Anti flip-flop (docs/IMPROVEMENT_PLAN.md, S3). Le feature cambiano solo
    # alla chiusura di ogni candela: senza cooldown lo stesso segnale
    # riaprirebbe una posizione appena chiusa entro pochi secondi.
    reverse_cooldown_minutes: int = 15      # età minima della posizione per invertirla
    reverse_confidence_margin: float = 0.05  # confidenza extra richiesta per invertire
    entry_cooldown_minutes: int = 60        # attesa dopo una chiusura prima di rientrare
    pattern_confirmation_enabled: bool = True
    dynamic_exit_enabled: bool = True
    # Backstop temporale: deve coprire l'orizzonte del target del modello
    # (TARGET_HORIZON_BARS × timeframe = 5 × 1h = 300 min), altrimenti le
    # posizioni vengono chiuse prima che la predizione possa realizzarsi.
    max_holding_minutes: int = 300
    # Circuit breaker (docs/IMPROVEMENT_PLAN.md, V1/N1): il fold peggiore del
    # walk-forward era una sequenza di 6 stop loss consecutivi in ~15 ore, non
    # troppe posizioni correlate (il cap direzionale non copre questo caso).
    # Valori tarati con walk_forward.py sweep 4 fold: un cooldown breve
    # (60-360 min) non protegge affatto — il "regime cattivo" durava ~15h,
    # più del cooldown. consec=3/cooldown=1440 min (24h) è il migliore
    # trovato (fold peggiore −33→+2 USDT, totale −23→+12, fold buoni invariati).
    circuit_breaker_enabled: bool = True
    circuit_breaker_max_consecutive_losses: int = 3
    circuit_breaker_cooldown_minutes: int = 1440
    circuit_breaker_max_daily_loss_pct: float = 0.05
    circuit_breaker_max_drawdown_pct: float = 0.20
