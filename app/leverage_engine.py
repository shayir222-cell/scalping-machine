from dataclasses import dataclass, field


# [score_min, score_max] → leverage
_TABLES: dict[str, list[tuple[int, int, int]]] = {
    "BTCUSDT":  [(70, 74, 4), (75, 84, 7),  (85, 89, 10), (90, 100, 12)],
    "ETHUSDT":  [(70, 74, 4), (75, 84, 6),  (85, 89, 8),  (90, 100, 10)],
    "SOLUSDT":  [(70, 74, 3), (75, 84, 5),  (85, 89, 7),  (90, 100, 8)],
    "BNBUSDT":  [(70, 74, 3), (75, 84, 5),  (85, 89, 6),  (90, 100, 7)],
    "XRPUSDT":  [(70, 74, 3), (75, 84, 4),  (85, 89, 5),  (90, 100, 6)],
    "DOGEUSDT": [(70, 74, 3), (75, 84, 4),  (85, 89, 5),  (90, 100, 6)],
    # SUI: $1.4B daily volume but 13%+ 24h range — keep leverage conservative
    # to avoid liquidation on a single strong swing
    "SUIUSDT":  [(70, 74, 3), (75, 84, 4),  (85, 89, 5),  (90, 100, 6)],
}
_DEFAULT_TABLE = _TABLES["XRPUSDT"]


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


def _base_leverage(symbol: str, score: int) -> int:
    table = _TABLES.get(symbol, _DEFAULT_TABLE)
    for lo, hi, lev in table:
        if lo <= score <= hi:
            return lev
    return 3


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


def _can_max(s: MarketState) -> bool:
    return (
        s.score >= 90
        and s.tf_alignment == 4
        and not _should_reduce(s)
        and s.mode in ("normal", "aggressive")
    )


def get_leverage(symbol: str, state: MarketState) -> int:
    base = _base_leverage(symbol, state.score)

    if state.mode == "safe":
        return max(2, base // 2)

    if _should_reduce(state):
        lev = max(2, base // 2)
        return lev

    if _can_max(state) or state.mode == "aggressive":
        table = _TABLES.get(symbol, _DEFAULT_TABLE)
        return table[-1][2]  # top bracket

    return base


def leverage_note(symbol: str, state: MarketState) -> str:
    lev = get_leverage(symbol, state)
    if _should_reduce(state):
        return f"x{lev} (REDUCED — streak={state.loss_streak}, dd={state.daily_dd_pct:.1f}%)"
    if _can_max(state):
        return f"x{lev} (MAX — score={state.score}, 4TF)"
    return f"x{lev}"
