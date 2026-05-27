"""
Binance Futures (USDT-M Linear Perpetuals) execution engine.
API v1, direct httpx — no third-party SDK.
"""
import hashlib
import hmac
import json
import time
from decimal import Decimal, ROUND_DOWN
from typing import Optional

import httpx
from loguru import logger

BINANCE_LIVE = "https://fapi.binance.com"
BINANCE_TEST = "https://testnet.binancefuture.com"

TAKER_FEE   = 0.0004   # 0.04%
MAKER_FEE   = 0.0002   # 0.02%

# Cache: symbol → {tick_size, step_size, min_qty}
_info_cache: dict[str, dict] = {}


class BinanceFutures:
    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        self._key    = api_key
        self._secret = secret.encode()
        base_url     = BINANCE_TEST if testnet else BINANCE_LIVE
        self._http   = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        logger.info(f"Binance engine → {'TESTNET' if testnet else 'LIVE'}")

    # ─────────────────────────────────────────────
    # Auth
    # ─────────────────────────────────────────────

    def _sign(self, params: dict) -> str:
        # IMPORTANT: do NOT sort. httpx sends params in dict-insertion order,
        # so the signature must be computed over the same order. Sorting here
        # would diverge from the actual URL query string Binance sees,
        # causing -1022 "Signature for this request is not valid".
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()

    def _headers(self) -> dict:
        return {
            "X-MBX-APIKEY": self._key,
            "Content-Type": "application/json",
        }

    # ─────────────────────────────────────────────
    # HTTP helpers
    # ─────────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None, auth: bool = True) -> dict:
        if auth:
            if params is None:
                params = {}
            params["timestamp"] = str(int(time.time() * 1000))
            params["signature"] = self._sign(params)
        r = await self._http.get(path, params=params, headers=self._headers() if auth else {})
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Binance GET {exc.response.status_code}: {exc.response.text}"
            ) from exc
        data = r.json()
        if "code" in data and data["code"] != 0:
            raise RuntimeError(f"Binance [{data['code']}]: {data.get('msg')}")
        return data

    async def _post(self, path: str, params: dict | None = None) -> dict:
        if params is None:
            params = {}
        params["timestamp"] = str(int(time.time() * 1000))
        params["signature"] = self._sign(params)
        r = await self._http.post(path, params=params, headers=self._headers())
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Binance POST {exc.response.status_code}: {exc.response.text}"
            ) from exc
        data = r.json()
        if "code" in data and data["code"] != 0:
            raise RuntimeError(f"Binance [{data['code']}]: {data.get('msg')}")
        return data

    async def _delete(self, path: str, params: dict | None = None) -> dict:
        if params is None:
            params = {}
        params["timestamp"] = str(int(time.time() * 1000))
        params["signature"] = self._sign(params)
        r = await self._http.delete(path, params=params, headers=self._headers())
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Binance DELETE {exc.response.status_code}: {exc.response.text}"
            ) from exc
        data = r.json()
        if "code" in data and data["code"] != 0:
            raise RuntimeError(f"Binance [{data['code']}]: {data.get('msg')}")
        return data

    # ─────────────────────────────────────────────
    # Instrument info + rounding
    # ─────────────────────────────────────────────

    async def load_instrument(self, symbol: str) -> dict:
        if symbol in _info_cache:
            return _info_cache[symbol]
        data = await self._get("/fapi/v1/exchangeInfo", auth=False)
        for item in data["symbols"]:
            if item["symbol"] == symbol:
                filters = {f["filterType"]: f for f in item["filters"]}
                info = {
                    "tick_size": filters["PRICE_FILTER"]["tickSize"],
                    "step_size": filters["LOT_SIZE"]["stepSize"],
                    "min_qty":   float(filters["LOT_SIZE"]["minQty"]),
                }
                _info_cache[symbol] = info
                logger.debug(f"{symbol} rules: {info}")
                return info
        raise ValueError(f"Symbol {symbol} not found")

    @staticmethod
    def _px(price: float, tick: str) -> str:
        return str(Decimal(str(price)).quantize(Decimal(tick), rounding=ROUND_DOWN))

    @staticmethod
    def _qty(qty: float, step: str, min_qty: float) -> str:
        step_d = Decimal(step)
        rounded = Decimal(str(qty)).quantize(step_d, rounding=ROUND_DOWN)
        if rounded < Decimal(str(min_qty)):
            rounded = Decimal(str(min_qty)).quantize(step_d, rounding=ROUND_DOWN)
        return str(rounded)

    # ─────────────────────────────────────────────
    # Account
    # ─────────────────────────────────────────────

    async def get_balance_usdt(self) -> tuple[float, float]:
        """Returns (wallet_balance, unrealized_pnl)."""
        data = await self._get("/fapi/v2/balance")
        for asset in data:
            if asset["asset"] == "USDT":
                wallet = float(asset["balance"])
                upnl = float(asset["crossUnPnl"])
                return wallet, upnl
        return 0.0, 0.0

    async def get_position(self, symbol: str) -> Optional[dict]:
        data = await self._get("/fapi/v2/positionRisk")
        for p in data:
            if p["symbol"] == symbol and abs(float(p["positionAmt"])) > 0:
                return p
        return None

    async def get_open_positions(self) -> list[dict]:
        data = await self._get("/fapi/v2/positionRisk")
        return [p for p in data if abs(float(p["positionAmt"])) > 0]

    async def get_user_trades(self, symbol: str, limit: int = 20) -> list[dict]:
        """Recent fills for a symbol. Used to reconstruct exit price after
        a position closes on Binance side (SL/TP fired)."""
        try:
            data = await self._get("/fapi/v1/userTrades", {
                "symbol": symbol,
                "limit": str(limit),
            })
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"get_user_trades {symbol}: {e}")
            return []

    # ─── Public market microstructure (no auth needed) ───────────

    async def get_order_book_imbalance(self, symbol: str, depth: int = 20) -> float | None:
        """Bid-side share of total top-N book qty. >0.5 = bids dominate (bullish),
        <0.5 = asks dominate (bearish). Returns None on failure."""
        try:
            r = await self._http.get("/fapi/v1/depth", params={"symbol": symbol, "limit": str(depth)})
            r.raise_for_status()
            data = r.json()
            bid_qty = sum(float(b[1]) for b in data.get("bids", []))
            ask_qty = sum(float(a[1]) for a in data.get("asks", []))
            total = bid_qty + ask_qty
            if total <= 0:
                return None
            return bid_qty / total
        except Exception as e:
            logger.warning(f"get_order_book_imbalance {symbol}: {e}")
            return None

    async def get_open_interest(self, symbol: str) -> float | None:
        """Current open interest (in contract units). Returns None on failure."""
        try:
            r = await self._http.get("/fapi/v1/openInterest", params={"symbol": symbol})
            r.raise_for_status()
            return float(r.json().get("openInterest", 0))
        except Exception as e:
            logger.warning(f"get_open_interest {symbol}: {e}")
            return None

    async def get_oi_change_pct(self, symbol: str, period: str = "5m") -> float | None:
        """5-min open-interest delta as a percent (positive = OI growing).
        Period: '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d'."""
        try:
            r = await self._http.get(
                "/futures/data/openInterestHist",
                params={"symbol": symbol, "period": period, "limit": "2"},
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or len(data) < 2:
                return None
            old_oi = float(data[0].get("sumOpenInterest", 0))
            new_oi = float(data[-1].get("sumOpenInterest", 0))
            if old_oi <= 0:
                return None
            return (new_oi - old_oi) / old_oi * 100
        except Exception as e:
            logger.warning(f"get_oi_change_pct {symbol}: {e}")
            return None

    async def get_funding_rate(self, symbol: str) -> dict | None:
        """Returns {'rate': float, 'next_funding_ms': int, 'mark_price': float}
        for the symbol, or None on failure."""
        try:
            r = await self._http.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
            r.raise_for_status()
            data = r.json()
            return {
                "rate": float(data.get("lastFundingRate", 0)),
                "next_funding_ms": int(data.get("nextFundingTime", 0)),
                "mark_price": float(data.get("markPrice", 0)),
            }
        except Exception as e:
            logger.warning(f"get_funding_rate {symbol}: {e}")
            return None

    # ─────────────────────────────────────────────
    # Configuration
    # ─────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._post("/fapi/v1/leverage", {
            "symbol": symbol,
            "leverage": leverage,
        })
        logger.info(f"{symbol} leverage → x{leverage}")

    async def set_position_mode_oneway(self) -> None:
        """One-way mode (no hedge). Call once at startup."""
        try:
            await self._post("/fapi/v1/positionSide/dual", {
                "dualSidePosition": "false",
            })
            logger.info("Position mode: one-way")
        except RuntimeError as e:
            message = str(e).lower()
            if "already" in message:
                return
            logger.warning(f"set position mode failed: {e}")
            return

    # ─────────────────────────────────────────────
    # Orders
    # ─────────────────────────────────────────────

    async def market_order(self, symbol: str, side: str, qty: float) -> dict:
        """side: BUY | SELL"""
        info = await self.load_instrument(symbol)
        sz = self._qty(qty, info["step_size"], info["min_qty"])
        data = await self._post("/fapi/v1/order", {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": sz,
        })
        return data

    async def limit_order(
        self, symbol: str, side: str, qty: float, price: float, post_only: bool = True,
    ) -> dict:
        """Limit order. With post_only=True, uses GTX (post-only): the order
        is REJECTED by Binance if it would match immediately, guaranteeing
        maker-fee pricing (0.02% vs 0.04% taker)."""
        info = await self.load_instrument(symbol)
        sz = self._qty(qty, info["step_size"], info["min_qty"])
        px = self._px(price, info["tick_size"])
        data = await self._post("/fapi/v1/order", {
            "symbol": symbol,
            "side": side.upper(),
            "type": "LIMIT",
            "quantity": sz,
            "price": px,
            "timeInForce": "GTX" if post_only else "GTC",
        })
        return data

    async def place_sl(
        self, symbol: str, side: str, qty: float, sl_price: float
    ) -> dict:
        """Stop-loss via the new Algo Order endpoint (Binance migrated all
        conditional orders off /fapi/v1/order on 2025-12-09)."""
        info = await self.load_instrument(symbol)
        sz = self._qty(qty, info["step_size"], info["min_qty"])
        px = self._px(sl_price, info["tick_size"])
        data = await self._post("/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side.upper(),
            "type": "STOP_MARKET",
            "quantity": sz,
            "triggerPrice": px,
            "reduceOnly": "true",
        })
        return data

    async def place_tp(
        self, symbol: str, side: str, qty: float, tp_price: float
    ) -> dict:
        """Take-profit via the Algo Order endpoint (taker on fill)."""
        info = await self.load_instrument(symbol)
        sz = self._qty(qty, info["step_size"], info["min_qty"])
        px = self._px(tp_price, info["tick_size"])
        data = await self._post("/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side.upper(),
            "type": "TAKE_PROFIT_MARKET",
            "quantity": sz,
            "triggerPrice": px,
            "reduceOnly": "true",
        })
        return data

    async def place_tp_limit(
        self, symbol: str, side: str, qty: float, tp_price: float
    ) -> dict:
        """Take-profit as a passive post-only LIMIT reduce-only order.
        Fills as MAKER (0.02% fee vs 0.04% taker on TAKE_PROFIT_MARKET) —
        recovers ~0.02% per side, ≈0.04% per round trip. On a 0.5-1.0% TP
        leg that's 4-8% of the gross win recovered. Falls back to algo
        TAKE_PROFIT_MARKET if Binance rejects with -2021 (price already
        through the TP)."""
        info = await self.load_instrument(symbol)
        sz = self._qty(qty, info["step_size"], info["min_qty"])
        px = self._px(tp_price, info["tick_size"])
        try:
            data = await self._post("/fapi/v1/order", {
                "symbol": symbol,
                "side": side.upper(),
                "type": "LIMIT",
                "quantity": sz,
                "price": px,
                "timeInForce": "GTX",  # post-only — guarantees maker
                "reduceOnly": "true",
            })
            return data
        except RuntimeError as e:
            if "-2021" in str(e) or "would immediately match" in str(e).lower():
                logger.info(f"{symbol} TP @ {px} would match → fallback to TAKE_PROFIT_MARKET")
                return await self.place_tp(symbol, side, qty, tp_price)
            raise

    async def get_open_algo_orders(self, symbol: str) -> list[dict]:
        """List open conditional/algo orders for a symbol (SL + TP)."""
        try:
            data = await self._get("/fapi/v1/openAlgoOrders", {"symbol": symbol})
            if isinstance(data, dict) and "orders" in data:
                return data["orders"]
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"get_open_algo_orders {symbol}: {e}")
            return []

    async def cancel_algo_orders(self, symbol: str, only_sl: bool = False) -> None:
        """Cancel algo (conditional) orders. With only_sl=True, cancels only
        STOP_MARKET (SL) and leaves any fallback TAKE_PROFIT_MARKET orders
        alone — used by BE-move to preserve TP runners even when one TP fell
        back to the algo endpoint via place_tp_limit's -2021 path."""
        try:
            algo_orders = await self.get_open_algo_orders(symbol)
            for ao in algo_orders:
                if only_sl and ao.get("type") != "STOP_MARKET":
                    continue
                algo_id = ao.get("algoId") or ao.get("orderId")
                if not algo_id:
                    continue
                try:
                    await self._delete("/fapi/v1/algoOrder", {
                        "symbol": symbol,
                        "algoId": str(algo_id),
                    })
                except Exception as e:
                    logger.warning(f"cancel algo {symbol} #{algo_id}: {e}")
        except Exception as e:
            logger.warning(f"cancel algo batch {symbol}: {e}")

    async def cancel_all_orders(self, symbol: str) -> None:
        # Cancel regular pending orders (limit entries, post-only TPs, etc.)
        try:
            await self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
        except Exception as e:
            logger.warning(f"cancel regular {symbol}: {e}")
        # Cancel each open algo order (SL) individually
        await self.cancel_algo_orders(symbol)

    # ─────────────────────────────────────────────
    # High-level: open / manage / close
    # ─────────────────────────────────────────────

    async def open_trade(
        self,
        symbol: str,
        side: str,           # LONG | SHORT
        qty: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        tp3_price: Optional[float] = None,
        use_limit: bool = False,
        limit_price: Optional[float] = None,
    ) -> dict:
        """Entry + SL (full qty) + TP1/TP2/TP3 as post-only LIMITs
        (40/40/20 split — matches Pine v7). If tp3_price is None,
        falls back to legacy 40/60 split."""
        order_side = "BUY"  if side == "LONG" else "SELL"
        exit_side  = "SELL" if side == "LONG" else "BUY"

        if use_limit and limit_price:
            try:
                entry = await self.limit_order(symbol, order_side, qty, limit_price, post_only=True)
            except RuntimeError as e:
                # Binance rejects post-only (GTX) with -2021 if it would match
                # immediately. Fall back to market so the signal isn't lost.
                if "-2021" in str(e) or "would immediately match" in str(e).lower():
                    logger.info(f"{symbol} post-only rejected, falling back to market")
                    entry = await self.market_order(symbol, order_side, qty)
                else:
                    raise
        else:
            entry = await self.market_order(symbol, order_side, qty)

        # SL + TPs placement. If anything raises we MUST not leave an
        # unhedged position — emergency-close and re-raise.
        try:
            await self.place_sl(symbol, exit_side, qty, sl_price)

            if tp3_price is not None:
                tp1_qty = qty * 0.4
                tp2_qty = qty * 0.4
                tp3_qty = qty * 0.2
                await self.place_tp_limit(symbol, exit_side, tp1_qty, tp1_price)
                await self.place_tp_limit(symbol, exit_side, tp2_qty, tp2_price)
                await self.place_tp_limit(symbol, exit_side, tp3_qty, tp3_price)
            else:
                tp1_qty = qty * 0.4
                tp2_qty = qty * 0.6
                await self.place_tp_limit(symbol, exit_side, tp1_qty, tp1_price)
                await self.place_tp_limit(symbol, exit_side, tp2_qty, tp2_price)
        except Exception as e:
            logger.error(
                f"{symbol}: SL/TP placement failed after entry — emergency closing. {e}"
            )
            try:
                await self.cancel_all_orders(symbol)
                # Re-fetch position to know actual filled qty (post-only may be partial)
                pos = await self.get_position(symbol)
                if pos:
                    filled_qty = abs(float(pos.get("positionAmt", 0)))
                    if filled_qty > 0:
                        await self.market_order(symbol, exit_side, filled_qty)
                        logger.info(f"{symbol}: emergency-closed {filled_qty} after SL/TP failure")
            except Exception as cleanup_err:
                logger.error(f"{symbol}: emergency cleanup ALSO failed: {cleanup_err}")
            raise

        return entry

    async def close_trade(self, symbol: str, side: str, qty: float) -> dict:
        """Market close."""
        exit_side = "SELL" if side == "LONG" else "BUY"
        return await self.market_order(symbol, exit_side, qty)

    async def close_all_positions(self) -> None:
        positions = await self.get_open_positions()
        for p in positions:
            symbol = p["symbol"]
            amt = abs(float(p["positionAmt"]))
            side = "LONG" if float(p["positionAmt"]) > 0 else "SHORT"
            await self.cancel_all_orders(symbol)
            await self.close_trade(symbol, side, amt)
            logger.info(f"Closed {symbol} {side} {amt}")

    async def get_open_orders(self, symbol: str) -> list[dict]:
        """Get all open orders for a symbol."""
        try:
            data = await self._get("/fapi/v1/openOrders", {"symbol": symbol})
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"get_open_orders {symbol}: {e}")
            return []

    async def move_sl_to_breakeven(
        self, symbol: str, side: str, entry_price: float
    ) -> None:
        """Move SL to breakeven (with buffer covering fees on runner).
        Cancels only the SL (only_sl=True) — leaves all TPs on the book,
        including any that fell back to algo TAKE_PROFIT_MARKET. Buffer
        covers maker-entry (0.02%) + taker-SL-exit (0.04%) = 0.06% net
        cost on the BE-stopped runner, plus a small slippage margin."""
        await self.cancel_algo_orders(symbol, only_sl=True)
        exit_side = "SELL" if side == "LONG" else "BUY"
        buf = entry_price * 0.0007  # +0.07% — covers maker in + taker SL out + slippage
        be  = entry_price + buf if side == "LONG" else entry_price - buf
        pos = await self.get_position(symbol)
        if not pos:
            return
        qty = float(pos.get("positionAmt", 0))
        await self.place_sl(symbol, exit_side, abs(qty), be)
        logger.info(f"{symbol} SL → breakeven @ {be:.6f} (TPs preserved)")

    async def close_position(
        self, symbol: str, side: str, qty: Optional[float] = None
    ) -> dict:
        close_side = "SELL" if side == "LONG" else "BUY"
        if qty and qty > 0:
            return await self.market_order(symbol, close_side, qty)
        pos = await self.get_position(symbol)
        if not pos:
            return {}
        pos_qty = abs(float(pos.get("positionAmt", 0)))
        if pos_qty <= 0:
            return {}
        return await self.market_order(symbol, close_side, pos_qty)

    @staticmethod
    def estimate_fees(notional: float, n_orders: int = 2) -> float:
        return notional * TAKER_FEE * n_orders
