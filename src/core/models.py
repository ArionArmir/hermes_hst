"""
Modelli dati per Project Hermes HFT
"""
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
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
    entry_time: datetime = datetime.utcnow()
    pnl: float = 0.0
    is_open: bool = True

    def to_dict(self) -> dict:
        return self.model_dump()

class Signal(BaseModel):
    symbol: str
    action: str  # 'buy', 'sell', 'hold', 'close'
    confidence: float  # 0-1
    timestamp: datetime = datetime.utcnow()
    source: str  # 'ml' o 'sentiment'

    def to_dict(self) -> dict:
        return self.model_dump()

class Config(BaseModel):
    leverage: int = 3
    stop_loss_pct: float = 0.01
    take_profit_pct: float = 0.02
    max_position_size_usdt: float = 200.0
    trailing_stop_pct: float = 0.005
    max_exposure: float = 0.5
    min_volatility_threshold: float = 0.001
    max_volatility_threshold: float = 0.02
    volatility_adjustment: bool = True
    symbols: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    timeframe: str = '1m'
    ml_confidence_threshold: float = 0.55
    sentiment_weight: float = 0.3
    sentiment_asset_enabled: bool = True
