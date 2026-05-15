import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from loguru import logger


UTC = timezone.utc


class RiskEngine:
    def __init__(self):
        self.day_start_equity: float = 0.0
        self.day_start: datetime = datetime.now(UTC)
        self.peak_equity: float = 0.0
        self.loss_streak: int = 0
        self.win_streak: int = 0
        self.paused_until: Optional[datetime] = None
        self._warned_3: bool = False
        self._warned_4: bool = False
        # Weekly DD tracking (ISO week, resets on Monday 00:00 UTC)
        self.week_start_equity: float = 0.0
        self.week_start: Optional[datetime] = None

    # ──────────────────────────────────────────────
    # Equity helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def true_equity(wallet_balance: float, unrealized_pnl: float) -> float:
        """wallet + unrealized; used margin is NOT counted as loss."""
        return wallet_balance + unrealized_pnl

    def daily_dd_pct(self, true_eq: float) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return (true_eq - self.day_start_equity) / self.day_start_equity * 100.0

    def weekly_dd_pct(self, true_eq: float) -> float:
        if self.week_start_equity <= 0:
            return 0.0
        return (true_eq - self.week_start_equity) / self.week_start_equity * 100.0

    def new_day(self, equity: float) -> None:
        self.day_start_equity = equity
        self.peak_equity = equity
        self.day_start = datetime.now(UTC)
        self._warned_3 = False
        self._warned_4 = False
        self.loss_streak = 0
        self.win_streak = 0
        self.paused_until = None
        # Rotate the week if we crossed Monday
        now = datetime.now(UTC)
        if (self.week_start is None
            or (now - self.week_start).days >= 7
            or now.weekday() == 0):  # Monday rollover
            self.week_start_equity = equity
            self.week_start = now
            logger.info(f"New week started. Equity: ${equity:.2f}")
        logger.info(f"New day started. Equity: ${equity:.2f}")

    # ──────────────────────────────────────────────
    # Trade gating
    # ──────────────────────────────────────────────

    def can_trade(self, true_eq: float) -> tuple[bool, str]:
        dd = self.daily_dd_pct(true_eq)
        wdd = self.weekly_dd_pct(true_eq)

        if dd <= -5.0:
            return False, f"Daily stop hit: {dd:.2f}% ≤ -5%"
        if wdd <= -10.0:
            return False, f"Weekly stop hit: {wdd:.2f}% ≤ -10%"

        now = datetime.now(UTC)
        if self.paused_until and now < self.paused_until:
            mins = (self.paused_until - now).seconds // 60
            return False, f"Cooling down — {mins}m remaining (streak={self.loss_streak})"

        warnings = []
        if dd <= -4.0 and not self._warned_4:
            self._warned_4 = True
            warnings.append("⚠️ WARNING: -4% daily DD")
        elif dd <= -3.0 and not self._warned_3:
            self._warned_3 = True
            warnings.append("⚠️ WARNING: -3% daily DD")
        if wdd <= -7.0:
            warnings.append(f"⚠️ Weekly DD {wdd:.1f}%")

        return True, " | ".join(warnings)

    # ──────────────────────────────────────────────
    # Risk sizing
    # ──────────────────────────────────────────────

    def risk_pct(self, score: int, mode: str = "normal") -> float:
        if mode == "safe":
            return 0.5
        if score >= 90:
            return 1.5
        if score >= 85:
            return 1.25
        return 1.0

    def position_size(
        self,
        equity: float,
        risk_pct: float,
        entry_price: float,
        sl_price: float,
        leverage: int,
    ) -> float:
        """
        Return quantity in base asset that risks exactly risk_pct% of equity.
        Capped so margin_required ≤ 95% of equity.
        """
        sl_dist = abs(entry_price - sl_price) / entry_price
        if sl_dist < 0.0001:
            return 0.0

        risk_usdt = equity * risk_pct / 100.0
        qty = risk_usdt / (entry_price * sl_dist)

        # Margin constraint: qty * entry / leverage ≤ 0.95 * equity
        max_qty = (equity * 0.95 * leverage) / entry_price
        qty = min(qty, max_qty)
        return max(qty, 0.0)

    # ──────────────────────────────────────────────
    # Streak tracking
    # ──────────────────────────────────────────────

    def record_result(self, is_win: bool) -> None:
        if is_win:
            self.loss_streak = 0
            self.win_streak += 1
            return

        self.win_streak = 0
        self.loss_streak += 1
        now = datetime.now(UTC)

        if self.loss_streak == 2:
            self.paused_until = now + timedelta(minutes=30)
            logger.warning("2 losses in a row → pause 30 min")
        elif self.loss_streak >= 3:
            self.paused_until = now + timedelta(minutes=60)
            logger.warning(f"{self.loss_streak} losses in a row → pause 60 min")

    # ──────────────────────────────────────────────
    # TP / SL price helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def tp_prices(entry: float, sl: float, side: str) -> tuple[float, float, float]:
        # 1.0R / 2.0R / 3.0R — matches Pine calibration (rr1/rr2/rr3 in
        # scalp_strategy.pine). Forensic on 20 live trades: median peak
        # excursion was 1.31R; 65% reached 1.0R but only 45% reached the
        # prior 1.5R TP1, so signal_close was systematically clipping
        # winners before TP1.
        risk = abs(entry - sl)
        if side == "LONG":
            return entry + risk * 1.0, entry + risk * 2.0, entry + risk * 3.0
        return entry - risk * 1.0, entry - risk * 2.0, entry - risk * 3.0

    @staticmethod
    def sl_from_atr(
        entry: float,
        atr: float,
        side: str,
        multiplier: float = 2.0,
        min_pct: float = 0.0025,
    ) -> float:
        # Floor SL at min_pct of entry so fees don't dominate net PnL
        # when ATR is unusually tight (e.g. XRP at 0.007% move → fee-kill).
        sl_dist = max(atr * multiplier, entry * min_pct)
        if side == "LONG":
            return entry - sl_dist
        return entry + sl_dist
