"""
Modelli dati per Project Hermes HFT
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime, timezone

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
    """Vincoli di range (docs/IMPROVEMENT_PLAN.md, V6/N5): senza, un valore
    fuori scala (leverage: 100, max_exposure: 5.0, un simbolo non-USDT...)
    veniva accettato e applicato A CALDO da qualunque punto scriva
    trading_config — dashboard, un edit manuale del YAML, un comando Redis
    diretto — senza che nulla lo segnalasse. Pydantic solleva ValidationError
    alla costruzione di Config(**dati): la dashboard la mostra già
    (dashboard/app_pages/configuration.py), ora ha davvero qualcosa da
    intercettare. I limiti sono ampi apposta (proteggono da errori grossolani,
    non impongono la "taratura giusta" — quella resta compito di
    tune_strategy.py/walk_forward.py)."""

    leverage: int = Field(3, ge=1, le=20)
    stop_loss_pct: float = Field(0.01, gt=0, le=0.5)
    take_profit_pct: float = Field(0.02, gt=0, le=1.0)
    max_position_size_usdt: float = Field(200.0, gt=0, le=1_000_000)
    trailing_stop_pct: float = Field(0.005, gt=0, le=0.5)
    # Frazione massima del capitale impegnabile come margine, sommando TUTTE
    # le posizioni aperte: l'engine rifiuta le aperture oltre questo cap.
    max_exposure: float = Field(0.5, gt=0, le=1.0)
    # Numero massimo di posizioni aperte simultaneamente nella STESSA
    # direzione (long o short), su simboli diversi. Con crypto altamente
    # correlate, max_exposure da solo non basta: al sizing attuale (~50 USDT
    # di margine/posizione) restano ampi margini di manovra anche con 6-7
    # simboli aperti nella stessa direzione — un ribasso sincrono del
    # mercato li chiude quasi tutti in stop loss insieme (scoperto con
    # walk_forward.py, docs/IMPROVEMENT_PLAN.md). Tarato a 3 con
    # walk_forward.py --folds 4: sotto questo valore il fold peggiore
    # migliora sensibilmente senza sacrificare i fold buoni.
    max_positions_same_direction: int = Field(3, ge=1, le=50)
    # Fee taker Binance Futures (0,05% per lato), simulate a ogni chiusura
    # per un PnL paper realistico.
    taker_fee_pct: float = Field(0.0005, ge=0, le=0.05)
    symbols: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    # Deve coincidere con il timeframe delle candele di training
    # (train_all_models.py): l'inference lo usa per scaricare le candele su
    # cui calcola le feature.
    timeframe: str = '1h'
    ml_confidence_threshold: float = Field(0.55, gt=0, lt=1.0)
    sentiment_weight: float = Field(0.3, ge=0, le=1.0)
    reverse_trading_enabled: bool = True
    # Anti flip-flop (docs/IMPROVEMENT_PLAN.md, S3). Le feature cambiano solo
    # alla chiusura di ogni candela: senza cooldown lo stesso segnale
    # riaprirebbe una posizione appena chiusa entro pochi secondi.
    reverse_cooldown_minutes: int = Field(15, ge=0)       # età minima della posizione per invertirla
    reverse_confidence_margin: float = Field(0.05, ge=0, le=0.5)  # confidenza extra richiesta per invertire
    entry_cooldown_minutes: int = Field(60, ge=0)         # attesa dopo una chiusura prima di rientrare
    pattern_confirmation_enabled: bool = True
    dynamic_exit_enabled: bool = True
    # Backstop temporale: deve coprire l'orizzonte del target del modello
    # (TARGET_HORIZON_BARS × timeframe = 5 × 1h = 300 min), altrimenti le
    # posizioni vengono chiuse prima che la predizione possa realizzarsi.
    max_holding_minutes: int = Field(300, ge=1)
    # Circuit breaker (docs/IMPROVEMENT_PLAN.md, V1/N1): il fold peggiore del
    # walk-forward era una sequenza di 6 stop loss consecutivi in ~15 ore, non
    # troppe posizioni correlate (il cap direzionale non copre questo caso).
    # Valori tarati con walk_forward.py sweep 4 fold: un cooldown breve
    # (60-360 min) non protegge affatto — il "regime cattivo" durava ~15h,
    # più del cooldown. consec=3/cooldown=1440 min (24h) è il migliore
    # trovato (fold peggiore −33→+2 USDT, totale −23→+12, fold buoni invariati).
    circuit_breaker_enabled: bool = True
    circuit_breaker_max_consecutive_losses: int = Field(3, ge=1)
    circuit_breaker_cooldown_minutes: int = Field(1440, ge=0)
    circuit_breaker_max_daily_loss_pct: float = Field(0.05, gt=0, le=1.0)
    circuit_breaker_max_drawdown_pct: float = Field(0.20, gt=0, le=1.0)

    @field_validator("symbols")
    @classmethod
    def _validate_symbols(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("la lista symbols non può essere vuota")
        for s in v:
            if not s.upper().endswith("USDT"):
                raise ValueError(
                    f"simbolo non supportato: {s!r} — tutte le coppie di questo "
                    f"progetto quotano in USDT (src/data_collector.to_ccxt_symbol e altri "
                    f"assumono il suffisso)"
                )
        return v

    @field_validator("timeframe")
    @classmethod
    def _validate_timeframe(cls, v: str) -> str:
        from src.shared.features import timeframe_minutes  # import lazy: evita cicli
        try:
            timeframe_minutes(v)
        except ValueError as e:
            raise ValueError(f"timeframe non valido: {e}")
        return v
