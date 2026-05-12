"""
Leverage engine — Variant B: leverage decoupled from score.

Each pair has a single max-safe leverage chosen so that the bot's ATR-based
stop-loss (~0.3–0.7% of entry) sits comfortably inside Binance's
liquidation distance (1 / leverage). We keep a ≥5× safety margin: at
max=20× the liq distance is 5% — vs a typical SL of 0.4–1% that gives
plenty of room for a wick before liquidation triggers.

Score no longer affects leverage. It only scales `risk_pct` in risk.py
(1.0% → 1.25% at score≥85 → 1.5% at score≥90).

Auto-reductions still apply: loss streak, daily drawdown, chaotic ATR,
out-of-session, safe mode — each halves leverage. Aggressive mode adds
nothing extra (we're already at max).
"""
from dataclasses import dataclass


# Safe max leverage per symbol. Calibrated so liq_distance / typical_sl_distance >= 5.
# Volatile pairs get lower caps; majors (BTC/ETH) higher.
_MAX_LEV: dict[str, int] = {
    "BTCUSDT":      25,
    "ETHUSDT":      20,
    "SOLUSDT":      15,
    "BNBUSDT":      15,
    "XRPUSDT":      12,
    "DOGEUSDT":     12,
    "SUIUSDT":      10,
}
_DEFAULT_MAX = 8  # unknown symbol — conservative


@dataclass
class MarketState:
    score: int = 0
    tf_alignment: int = 0
    loss_streak: int = 0
    daily_dd_pct: float = 0.0
    atr_chaotic: bool = False
    spread_widened: bool = False
    outside_session: bool = False
    slippage_high: bool = False
    mode: str = "normal"   # normal | aggressive | safe


def _max_for(symbol: str) -> int:
    return _MAX_LEV.get(symbol, _DEFAULT_MAX)


def _should_reduce(s: MarketState) -> bool:
    return (
        s.loss_streak >= 2
        or s.daily_dd_pct <= -3.0
        or s.spread_widened
        or s.atr_chaotic
        or s.outside_session
        or s.slippage_high
        or s.mode == "safe"
    )


def get_leverage(symbol: str, state: MarketState) -> int:
    base = _max_for(symbol)
    if _should_reduce(state):
        return max(2, base // 2)
    return base


def leverage_note(symbol: str, state: MarketState) -> str:
    lev = get_leverage(symbol, state)
    if _should_reduce(state):
        reasons = []
        if state.loss_streak >= 2:
            reasons.append(f"streak={state.loss_streak}")
        if state.daily_dd_pct <= -3.0:
            reasons.append(f"dd={state.daily_dd_pct:.1f}%")
        if state.mode == "safe":
            reasons.append("safe")
        if state.atr_chaotic:
            reasons.append("atr_chaotic")
        if state.outside_session:
            reasons.append("off_session")
        return f"x{lev} (REDUCED — {', '.join(reasons)})"
    return f"x{lev}"
