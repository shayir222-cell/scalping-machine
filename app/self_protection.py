"""
Self-protection layer: guard against overtrading, duplicates, stale alerts, API desync.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from collections import defaultdict
from loguru import logger


UTC = timezone.utc
MAX_TRADES_PER_HOUR = 6
STALE_ALERT_THRESHOLD = 60  # seconds


class SelfProtection:
    def __init__(self):
        self.trade_times: dict[str, list[datetime]] = defaultdict(list)
        self.processed_signals: dict[str, datetime] = {}
        self.api_order_ids: set[str] = set()
        self.last_alert: dict[str, datetime] = {}

    # ─────────────────────────────────────────────────
    # Overtrading detection
    # ─────────────────────────────────────────────────

    def check_overtrading(self, symbol: str) -> tuple[bool, str]:
        """Check if symbol is being overtrade (>N trades per hour)."""
        now = datetime.now(UTC)
        one_hour_ago = now - timedelta(hours=1)

        # Cleanup old entries
        self.trade_times[symbol] = [
            t for t in self.trade_times[symbol] if t > one_hour_ago
        ]

        if len(self.trade_times[symbol]) >= MAX_TRADES_PER_HOUR:
            oldest = self.trade_times[symbol][0]
            wait_mins = int(((oldest + timedelta(hours=1)) - now).total_seconds() / 60) + 1
            return False, f"Overtrading: {len(self.trade_times[symbol])}/{MAX_TRADES_PER_HOUR} in 1h. Wait {wait_mins}min"

        return True, ""

    def record_trade(self, symbol: str) -> None:
        """Record trade timestamp for overtrading check."""
        self.trade_times[symbol].append(datetime.now(UTC))
        logger.debug(f"{symbol} trade recorded (total: {len(self.trade_times[symbol])}/h)")

    # ─────────────────────────────────────────────────
    # Duplicate detection
    # ─────────────────────────────────────────────────

    def check_duplicate_signal(self, symbol: str, action: str) -> tuple[bool, str]:
        """Check if this signal was already processed recently."""
        key = f"{symbol}:{action}"
        now = datetime.now(UTC)

        if key in self.processed_signals:
            age = (now - self.processed_signals[key]).total_seconds()
            if age < 5:  # within 5 seconds = duplicate
                return False, f"Duplicate signal (processed {age:.0f}s ago)"

        return True, ""

    def mark_signal_processed(self, symbol: str, action: str) -> None:
        """Mark signal as processed."""
        key = f"{symbol}:{action}"
        self.processed_signals[key] = datetime.now(UTC)

    # ─────────────────────────────────────────────────
    # Stale alert detection
    # ─────────────────────────────────────────────────

    def check_stale_alert(self, signal_timestamp: Optional[str]) -> tuple[bool, str]:
        """Check if alert is stale (old timestamp from TradingView)."""
        if not signal_timestamp:
            return True, ""  # No timestamp, assume fresh

        try:
            # Assuming ISO format: 2026-05-10T15:30:45Z
            alert_time = datetime.fromisoformat(signal_timestamp.replace("Z", "+00:00"))
            age = (datetime.now(UTC) - alert_time).total_seconds()

            if age > STALE_ALERT_THRESHOLD:
                return False, f"Stale alert (age: {age:.0f}s > {STALE_ALERT_THRESHOLD}s)"

            return True, ""
        except Exception as e:
            logger.warning(f"Could not parse alert timestamp: {e}")
            return True, ""

    # ─────────────────────────────────────────────────
    # API desync detection
    # ─────────────────────────────────────────────────

    def register_order(self, order_id: str) -> None:
        """Register order sent to exchange."""
        self.api_order_ids.add(order_id)
        logger.debug(f"Order registered: {order_id}")

    def check_duplicate_order(self, order_id: str) -> tuple[bool, str]:
        """Check if order was already submitted to exchange."""
        if order_id in self.api_order_ids:
            return False, f"Duplicate order ID detected: {order_id}"
        return True, ""

    def clear_orders(self) -> None:
        """Clear order registry (on successful trade close)."""
        self.api_order_ids.clear()

    # ─────────────────────────────────────────────────
    # Aggregate check
    # ─────────────────────────────────────────────────

    def validate_signal(
        self,
        symbol: str,
        action: str,
        timestamp: Optional[str] = None,
    ) -> tuple[bool, list[str]]:
        """Comprehensive validation of incoming signal."""
        issues = []

        # Overtrading
        ok, msg = self.check_overtrading(symbol)
        if not ok:
            issues.append(msg)

        # Duplicate
        ok, msg = self.check_duplicate_signal(symbol, action)
        if not ok:
            issues.append(msg)

        # Stale
        ok, msg = self.check_stale_alert(timestamp)
        if not ok:
            issues.append(msg)

        return len(issues) == 0, issues


# Singleton instance
_instance: Optional[SelfProtection] = None


def get_protection() -> SelfProtection:
    global _instance
    if _instance is None:
        _instance = SelfProtection()
    return _instance
