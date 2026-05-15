from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime


class WebhookSignal(BaseModel):
    action: str       # buy | sell | close_long | close_short
    symbol: str
    price: float
    score: int
    atr: Optional[float] = None
    tf_alignment: Optional[int] = 0
    time: Optional[str] = None
    token: Optional[str] = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in {"buy", "sell", "close_long", "close_short"}:
            raise ValueError(f"Invalid action: {v}")
        return v

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, v: str) -> str:
        v = v.upper().replace("/", "").replace(".P", "").replace("PERP", "")
        allowed = {
            "ETHUSDT", "SOLUSDT", "BNBUSDT",
            "XRPUSDT", "DOGEUSDT",
            "TONUSDT", "HYPEUSDT",
        }
        if v not in allowed:
            raise ValueError(f"Symbol {v} not in whitelist")
        return v


class TradeState:
    def __init__(self):
        self.symbol: str = ""
        self.side: str = ""          # LONG | SHORT
        self.score: int = 0
        self.leverage: int = 0
        self.risk_pct: float = 0.0
        self.entry_price: float = 0.0
        self.quantity: float = 0.0
        self.remaining_qty: float = 0.0
        self.sl_price: float = 0.0
        self.tp1_price: float = 0.0
        self.tp2_price: float = 0.0
        self.tp3_price: float = 0.0
        self.tp1_filled: bool = False
        self.tp2_filled: bool = False
        self.sl_order_id: Optional[str] = None
        self.tp_order_ids: list[str] = []
        self.db_id: Optional[int] = None
        self.opened_at: Optional[datetime] = None
        self.session: str = ""


class BotMode:
    NORMAL = "normal"
    AGGRESSIVE = "aggressive"
    SAFE = "safe"
