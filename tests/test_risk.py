"""
Risk engine tests — locks down behaviour after the 2026-05-16 tuning:
- risk_pct is flat 1% in normal/aggressive, 0.5% in safe
- daily DD ≤ -5% and weekly DD ≤ -10% halt trading
- loss streak triggers cooldown at 2 (30m) and ≥3 (60m)
- position_size respects both risk-pct AND 95% margin cap
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.risk import RiskEngine


UTC = timezone.utc


# ── risk_pct (flat after 2026-05-16) ─────────────────────────────

class TestRiskPct:
    def setup_method(self):
        self.r = RiskEngine()

    @pytest.mark.parametrize("score", [60, 70, 80, 85, 90, 95, 100])
    def test_normal_mode_is_flat_one_percent(self, score):
        # Premium bonuses (1.25/1.5) removed — premium ≥90 had 29% WR.
        assert self.r.risk_pct(score, "normal") == 1.0

    @pytest.mark.parametrize("score", [60, 90, 100])
    def test_aggressive_mode_is_flat_one_percent(self, score):
        assert self.r.risk_pct(score, "aggressive") == 1.0

    @pytest.mark.parametrize("score", [60, 90, 100])
    def test_safe_mode_halves_to_half_percent(self, score):
        assert self.r.risk_pct(score, "safe") == 0.5


# ── can_trade gating ─────────────────────────────────────────────

class TestCanTrade:
    def setup_method(self):
        self.r = RiskEngine()
        self.r.day_start_equity = 100.0
        self.r.week_start_equity = 100.0
        self.r.week_start = datetime.now(UTC)

    def test_full_equity_ok(self):
        ok, msg = self.r.can_trade(100.0)
        assert ok is True
        assert msg == ""

    def test_minus_5_pct_daily_halts(self):
        ok, msg = self.r.can_trade(95.0)  # -5% exactly
        assert ok is False
        assert "Daily stop" in msg

    def test_just_above_daily_halt_passes_with_warning(self):
        # -4.5% — past -4 warning, not yet halt
        ok, msg = self.r.can_trade(95.5)
        assert ok is True
        assert "WARNING" in msg

    def test_minus_10_pct_weekly_halts(self):
        ok, msg = self.r.can_trade(90.0)  # -10% weekly
        assert ok is False
        # Daily stop fires first since daily is also -10%, accept either.
        assert "stop" in msg.lower()

    def test_paused_until_blocks(self):
        self.r.paused_until = datetime.now(UTC) + timedelta(minutes=15)
        self.r.loss_streak = 2
        ok, msg = self.r.can_trade(100.0)
        assert ok is False
        assert "Cooling down" in msg

    def test_paused_until_in_past_does_not_block(self):
        self.r.paused_until = datetime.now(UTC) - timedelta(minutes=5)
        ok, _ = self.r.can_trade(100.0)
        assert ok is True


# ── Loss-streak cooldown ─────────────────────────────────────────

class TestLossStreak:
    def setup_method(self):
        self.r = RiskEngine()

    def test_one_loss_no_pause(self):
        self.r.record_result(False)
        assert self.r.loss_streak == 1
        assert self.r.paused_until is None

    def test_two_losses_pause_30m(self):
        self.r.record_result(False)
        self.r.record_result(False)
        assert self.r.loss_streak == 2
        assert self.r.paused_until is not None
        delta = self.r.paused_until - datetime.now(UTC)
        assert timedelta(minutes=29) <= delta <= timedelta(minutes=31)

    def test_three_losses_pause_60m(self):
        for _ in range(3):
            self.r.record_result(False)
        assert self.r.loss_streak == 3
        delta = self.r.paused_until - datetime.now(UTC)
        assert timedelta(minutes=59) <= delta <= timedelta(minutes=61)

    def test_win_resets_loss_streak(self):
        self.r.record_result(False)
        self.r.record_result(False)
        self.r.record_result(True)
        assert self.r.loss_streak == 0
        assert self.r.win_streak == 1


# ── position_size ────────────────────────────────────────────────

class TestPositionSize:
    def setup_method(self):
        self.r = RiskEngine()

    def test_basic_risk_at_one_pct(self):
        # equity 100, risk 1%, entry 100, SL 99 (1% SL distance), no lev cap
        # risk_usdt = 1.0; sl_dist = 0.01; qty = 1.0 / (100 * 0.01) = 1.0
        qty = self.r.position_size(100.0, 1.0, 100.0, 99.0, 10)
        assert qty == pytest.approx(1.0, rel=1e-6)

    def test_margin_cap_overrides_risk(self):
        # equity 100, leverage 1, entry 100 → max_qty = 0.95
        # Risk-based qty would be 1.0 → cap kicks in.
        qty = self.r.position_size(100.0, 1.0, 100.0, 99.0, 1)
        assert qty == pytest.approx(0.95, rel=1e-6)

    def test_tight_sl_returns_zero(self):
        # SL distance < 0.01% — bot refuses to size
        qty = self.r.position_size(100.0, 1.0, 100.0, 99.999, 10)
        assert qty == 0.0

    def test_doge_realistic_sizing(self):
        # equity $70, 1% risk, DOGE @ 0.10, SL @ 0.0985 (1.5% SL)
        # risk_usdt = 0.70; sl_dist = 0.015; qty = 0.70 / (0.10 * 0.015) = 466.67
        # margin cap at lev=6: 70 * 0.95 * 6 / 0.10 = 3990 → not binding
        qty = self.r.position_size(70.0, 1.0, 0.10, 0.0985, 6)
        assert qty == pytest.approx(466.67, rel=1e-3)


# ── TP/SL math ───────────────────────────────────────────────────

class TestPriceLevels:
    def test_tp_long_1r_2r_3r(self):
        # entry 100, sl 99 → risk = 1
        tp1, tp2, tp3 = RiskEngine.tp_prices(100.0, 99.0, "LONG")
        assert (tp1, tp2, tp3) == (101.0, 102.0, 103.0)

    def test_tp_short_1r_2r_3r(self):
        tp1, tp2, tp3 = RiskEngine.tp_prices(100.0, 101.0, "SHORT")
        assert (tp1, tp2, tp3) == (99.0, 98.0, 97.0)

    def test_sl_atr_long(self):
        # entry 100, atr 1.0, multiplier 1.5 → SL = 100 - 1.5 = 98.5
        # min_pct floor 0.0025 → 0.25 → not binding
        sl = RiskEngine.sl_from_atr(100.0, 1.0, "LONG", multiplier=1.5)
        assert sl == pytest.approx(98.5, rel=1e-6)

    def test_sl_atr_short(self):
        sl = RiskEngine.sl_from_atr(100.0, 1.0, "SHORT", multiplier=1.5)
        assert sl == pytest.approx(101.5, rel=1e-6)

    def test_sl_atr_min_pct_floor(self):
        # entry 100, atr 0.01, multiplier 1.5 → atr-based dist = 0.015
        # min_pct=0.0025 → floor 0.25 wins
        sl = RiskEngine.sl_from_atr(100.0, 0.01, "LONG", multiplier=1.5)
        assert sl == pytest.approx(99.75, rel=1e-6)


# ── new_day / week rollover ──────────────────────────────────────

class TestNewDay:
    def test_new_day_resets_warnings_and_streak(self):
        r = RiskEngine()
        r._warned_3 = True
        r._warned_4 = True
        r.loss_streak = 5
        r.win_streak = 0
        r.paused_until = datetime.now(UTC) + timedelta(hours=1)
        r.new_day(50.0)
        assert r.day_start_equity == 50.0
        assert r.loss_streak == 0
        assert r._warned_3 is False
        assert r._warned_4 is False
        assert r.paused_until is None

    def test_new_day_initialises_week_first_time(self):
        r = RiskEngine()
        assert r.week_start is None
        r.new_day(100.0)
        assert r.week_start_equity == 100.0
        assert r.week_start is not None
