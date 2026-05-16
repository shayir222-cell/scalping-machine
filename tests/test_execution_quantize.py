"""
Regression tests for BinanceFutures._qty / _px.

Catches the DOGE precision bug fixed in ecb9150 — load_instrument used to
float()-then-str() the step_size, turning Binance's "1" into "1.0", which
gave Decimal exponent -1 and forced DOGE qty to 1 decimal place. Binance
then rejected every DOGE order with -1111.
"""
import pytest

from app.execution import BinanceFutures


# ── _qty ─────────────────────────────────────────────────────────

class TestQty:
    def test_doge_whole_step_rounds_to_integer(self):
        # The regression case: step="1" must round qty to integer string.
        # Pre-fix this returned "47.3" → Binance -1111.
        assert BinanceFutures._qty(47.3, "1", 1.0) == "47"

    def test_doge_below_min_qty_uses_min(self):
        # qty 0.5 floor-rounds to 0; min_qty=1.0 lifts it.
        assert BinanceFutures._qty(0.5, "1", 1.0) == "1"

    def test_doge_zero_qty_uses_min(self):
        assert BinanceFutures._qty(0.0, "1", 1.0) == "1"

    def test_xrp_one_decimal_step(self):
        assert BinanceFutures._qty(12.34, "0.1", 0.1) == "12.3"

    def test_xrp_below_min_uses_min(self):
        # qty 0.05 rounds to 0.0, min_qty 0.1 wins.
        assert BinanceFutures._qty(0.05, "0.1", 0.1) == "0.1"

    def test_eth_three_decimal_step(self):
        assert BinanceFutures._qty(0.02345, "0.001", 0.001) == "0.023"

    def test_sol_one_decimal_step(self):
        assert BinanceFutures._qty(5.789, "0.1", 0.1) == "5.7"

    def test_qty_exactly_at_min(self):
        assert BinanceFutures._qty(1.0, "1", 1.0) == "1"

    def test_qty_already_aligned_stays(self):
        assert BinanceFutures._qty(47.0, "1", 1.0) == "47"


# ── _px ──────────────────────────────────────────────────────────

class TestPx:
    def test_btc_one_decimal_tick(self):
        assert BinanceFutures._px(80123.456, "0.1") == "80123.4"

    def test_btc_whole_dollar_tick(self):
        # If Binance ever returns tick="1" for a high-priced asset.
        assert BinanceFutures._px(80123.7, "1") == "80123"

    def test_xrp_four_decimal_tick(self):
        assert BinanceFutures._px(1.44567, "0.0001") == "1.4456"

    def test_doge_five_decimal_tick(self):
        assert BinanceFutures._px(0.12345678, "0.00001") == "0.12345"

    def test_eth_two_decimal_tick(self):
        assert BinanceFutures._px(2256.078, "0.01") == "2256.07"

    def test_px_already_aligned_stays(self):
        assert BinanceFutures._px(1.4222, "0.0001") == "1.4222"

    def test_px_rounds_down_not_nearest(self):
        # ROUND_DOWN — never round up past the tick.
        assert BinanceFutures._px(1.99999, "0.1") == "1.9"


# Note: _qty currently relies on Decimal.quantize, which only rounds to the
# exponent of `step` — fine for powers-of-10 steps (1, 0.1, 0.001 …). Binance
# USDT-M perp filters only use powers of 10 in stepSize today, so this is
# safe. If we ever onboard a pair with a non-power-of-10 step (e.g. "5",
# "0.5"), we'd need a divide-floor-multiply rewrite — see backlog.


# ── Edge: ensure float→str→Decimal roundtrip is stable ───────────

@pytest.mark.parametrize("qty", [47.3, 0.123, 0.0001, 12345.678, 0.0])
def test_qty_returns_str(qty):
    assert isinstance(BinanceFutures._qty(qty, "0.001", 0.001), str)


@pytest.mark.parametrize("price", [1.4222, 80123.456, 0.00012345])
def test_px_returns_str(price):
    assert isinstance(BinanceFutures._px(price, "0.0001"), str)
