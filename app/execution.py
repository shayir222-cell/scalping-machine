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
                    "tick_size": float(filters["PRICE_FILTER"]["tickSize"]),
                    "step_size": float(filters["LOT_SIZE"]["stepSize"]),
                    "min_qty":   float(filters["LOT_SIZE"]["minQty"]),
                }
                _info_cache[symbol] = info
                logger.debug(f"{symbol} rules: {info}")
                return info
        raise ValueError(f"Symbol {symbol} not found")

    @staticmethod
    def _px(price: float, tick: float) -> str:
        return str(Decimal(str(price)).quantize(Decimal(str(tick)), rounding=ROUND_DOWN))

    @staticmethod
    def _qty(qty: float, step: float, min_qty: float) -> str:
        rounded = float(Decimal(str(qty)).quantize(Decimal(str(step)), rounding=ROUND_DOWN))
        rounded = max(rounded, min_qty)
        return str(Decimal(str(rounded)).quantize(Decimal(str(step))))

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
        self, symbol: str, side: str, qty: float, price: float
    ) -> dict:
        info = await self.load_instrument(symbol)
        sz = self._qty(qty, info["step_size"], info["min_qty"])
        px = self._px(price, info["tick_size"])
        data = await self._post("/fapi/v1/order", {
            "symbol": symbol,
            "side": side.upper(),
            "type": "LIMIT",
            "quantity": sz,
            "price": px,
            "timeInForce": "GTC",
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
        """Take-profit via the new Algo Order endpoint."""
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

    async def cancel_all_orders(self, symbol: str) -> None:
        # Cancel regular pending orders (limit entries, etc.)
        try:
            await self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
        except Exception as e:
            logger.warning(f"cancel regular {symbol}: {e}")
        # Cancel each open algo order (SL/TP) individually
        try:
            algo_orders = await self.get_open_algo_orders(symbol)
            for ao in algo_orders:
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
        use_limit: bool = False,
        limit_price: Optional[float] = None,
    ) -> dict:
        """Entry + SL (full) + TP1 (40%)."""
        order_side = "BUY"  if side == "LONG" else "SELL"
        exit_side  = "SELL" if side == "LONG" else "BUY"

        if use_limit and limit_price:
            entry = await self.limit_order(symbol, order_side, qty, limit_price)
        else:
            entry = await self.market_order(symbol, order_side, qty)

        # SL full qty
        await self.place_sl(symbol, exit_side, qty, sl_price)

        # TP1 40%
        tp1_qty = qty * 0.4
        await self.place_tp(symbol, exit_side, tp1_qty, tp1_price)

        # TP2 remaining 60% at tp2_price
        tp2_qty = qty * 0.6
        await self.place_tp(symbol, exit_side, tp2_qty, tp2_price)

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

    async def check_tp1_filled(self, symbol: str) -> bool:
        """Check if first TP (40%) was filled. TPs now live in the algo
        orders endpoint, not the regular /openOrders."""
        try:
            orders = await self.get_open_algo_orders(symbol)
            tp_orders = [o for o in orders if o.get("type") == "TAKE_PROFIT_MARKET"]
            # We open with 2 TPs; if only 1 remains, TP1 has filled.
            return len(tp_orders) <= 1
        except Exception as e:
            logger.warning(f"check_tp1_filled {symbol}: {e}")
            return False

    async def move_sl_to_breakeven(
        self, symbol: str, side: str, entry_price: float
    ) -> None:
        await self.cancel_all_orders(symbol)
        exit_side = "SELL" if side == "LONG" else "BUY"
        buf = entry_price * 0.0001
        be  = entry_price - buf if side == "LONG" else entry_price + buf
        pos = await self.get_position(symbol)
        if not pos:
            return
        qty = float(pos.get("positionAmt", 0))
        await self.place_sl(symbol, exit_side, abs(qty), be)
        logger.info(f"{symbol} SL → breakeven @ {be:.6f}")

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
