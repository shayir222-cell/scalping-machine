"""
FastAPI entry point — webhook receiver + bot state management
"""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

# Load .env before any application module imports that depend on environment variables
def load_env_file() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if "#" in value:
            value = value.split("#", 1)[0].strip()
        value = value.strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

load_env_file()

from fastapi import FastAPI, HTTPException, Request
from loguru import logger

from .database import init_db, SessionLocal, Signal as DbSignal, Trade as DbTrade
from .execution import BinanceFutures
from .leverage_engine import MarketState, get_leverage, leverage_note
from .models import WebhookSignal, TradeState, BotMode
from .obsidian_export import export_trade, export_daily_report, export_evolution_report
from .risk import RiskEngine
from .self_protection import get_protection
from .pair_ranking import get_pair_ranking
from . import telegram_bot as tg
from .analytics import run_evolution_cycle

UTC = timezone.utc
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "scalper2024")

# ─────────────────────────────────────────────
# Global state
# ─────────────────────────────────────────────
risk_engine = RiskEngine()
execution: BinanceFutures | None = None
open_trades: dict[str, TradeState] = {}   # symbol → TradeState

state = {
    "running": False,
    "paused": False,
    "mode": BotMode.NORMAL,
    "risk_engine": risk_engine,
    "execution": None,
    "open_trades": open_trades,
}


# ─────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global execution

    await init_db()
    logger.info("Database initialized")

    execution = BinanceFutures(
        api_key = os.getenv("BINANCE_API_KEY", ""),
        secret  = os.getenv("BINANCE_SECRET", ""),
        testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true",
    )
    state["execution"] = execution

    try:
        await execution.set_position_mode_oneway()
    except Exception as e:
        logger.warning(f"Binance startup check failed while setting position mode: {e}")

    # Fetch day start equity
    try:
        wb, upnl = await execution.get_balance_usdt()
        eq = risk_engine.true_equity(wb, upnl)
        risk_engine.new_day(eq)
        logger.info(f"Starting equity: ${eq:.2f}")
        state["running"] = True
    except Exception as e:
        message = str(e).lower()
        if "-2015" in message or "invalid api" in message:
            fallback_testnet = not (os.getenv("BINANCE_TESTNET", "false").lower() == "true")
            logger.warning(
                "Binance authentication failed on configured endpoint. "
                f"Trying {'TESTNET' if fallback_testnet else 'LIVE'} fallback."
            )
            fallback_execution = BinanceFutures(
                api_key = os.getenv("BINANCE_API_KEY", ""),
                secret  = os.getenv("BINANCE_SECRET", ""),
                testnet = fallback_testnet,
            )
            try:
                wb, upnl = await fallback_execution.get_balance_usdt()
                execution = fallback_execution
                state["execution"] = execution
                eq = risk_engine.true_equity(wb, upnl)
                risk_engine.new_day(eq)
                logger.info(
                    f"Binance keys validated on {'TESTNET' if fallback_testnet else 'LIVE'} endpoint. "
                    "Switched execution mode accordingly."
                )
                state["running"] = True
            except Exception as fallback_error:
                logger.error(
                    f"Fallback Binance endpoint also failed: {fallback_error}. Bot will NOT auto-start."
                )
        else:
            logger.error(f"Failed to fetch balance: {e}. Bot will NOT auto-start.")

    # Inject state into Telegram
    tg.inject_state(state)
    tg_task = asyncio.create_task(tg.start_polling())
    
    # Start TP monitor (move SL to breakeven after TP1 fills)
    monitor_task = asyncio.create_task(_monitor_tp_fills())
    
    # Start optimizer (analyze every 72h)
    optimizer_task = asyncio.create_task(_run_optimizer())

    yield  # app running

    optimizer_task.cancel()
    monitor_task.cancel()
    tg_task.cancel()
    await execution._http.aclose()  # type: ignore[attr-defined]
    logger.info("Shutdown complete")


# ─────────────────────────────────────────────
# Background: Monitor TP fills and move SL
# ─────────────────────────────────────────────

async def _monitor_tp_fills() -> None:
    """Check every 10s if TP1 was filled, then move SL to breakeven."""
    while True:
        try:
            if not state.get("running"):
                await asyncio.sleep(10)
                continue

            for symbol, trade in list(open_trades.items()):
                if not trade.tp1_filled:
                    filled = await execution.check_tp1_filled(symbol)
                    if filled:
                        trade.tp1_filled = True
                        await execution.move_sl_to_breakeven(
                            symbol, trade.side, trade.entry_price
                        )
                        logger.info(f"{symbol} TP1 filled → SL moved to BE")
                        await tg.alert_tp_filled(symbol, "TP1", trade.entry_price)

            await asyncio.sleep(10)
        except Exception as e:
            logger.warning(f"TP monitor error: {e}")
            await asyncio.sleep(10)


async def _run_optimizer() -> None:
    """Run AI optimizer every 72h."""
    from .optimizer import get_optimizer
    
    while True:
        try:
            if not state.get("running"):
                await asyncio.sleep(3600)  # Check every hour
                continue
            
            opt = get_optimizer()
            if await opt.should_analyze():
                report = await opt.analyze_performance()
                if report:
                    msg = opt.format_report(report)
                    await tg._send(msg)
                    logger.info(f"Optimizer analysis complete: {report.total_trades} trades analyzed")
            
            await asyncio.sleep(3600)  # Check every hour
        except Exception as e:
            logger.warning(f"Optimizer error: {e}")
            await asyncio.sleep(3600)


app = FastAPI(title="Scalping Machine", lifespan=lifespan)


# ─────────────────────────────────────────────
# Webhook
# ─────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request):
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # Token check
    if raw.get("token") != WEBHOOK_TOKEN:
        raise HTTPException(403, "Invalid token")

    try:
        signal = WebhookSignal(**raw)
    except Exception as e:
        raise HTTPException(400, str(e))

    # Self-protection checks
    sp = get_protection()
    valid, issues = sp.validate_signal(signal.symbol, signal.action, raw.get("time"))
    if not valid:
        reason = " | ".join(issues)
        await tg.alert_signal_rejected(signal.symbol, signal.action, signal.score, reason)
        raise HTTPException(400, f"Self-protection: {reason}")

    sp.mark_signal_processed(signal.symbol, signal.action)

    # Persist signal
    async with SessionLocal() as db:
        db_sig = DbSignal(
            symbol=signal.symbol,
            action=signal.action,
            price=signal.price,
            score=signal.score,
            atr=signal.atr,
            tf_alignment=signal.tf_alignment or 0,
            raw_payload=raw,
        )
        db.add(db_sig)
        await db.commit()
        await db.refresh(db_sig)
        sig_id = db_sig.id

    asyncio.create_task(_process_signal(signal, sig_id))
    return {"status": "queued", "signal_id": sig_id}


async def _process_signal(signal: WebhookSignal, sig_id: int) -> None:
    if not state.get("running") or state.get("paused"):
        await tg.alert_signal_rejected(signal.symbol, signal.action, signal.score, "Bot not running/paused")
        return

    mode = state.get("mode", BotMode.NORMAL)

    # ── Close signals ──
    if signal.action in ("close_long", "close_short"):
        await _handle_close(signal)
        return

    # ── Score gate ──
    min_score = 70 if mode == BotMode.AGGRESSIVE else 75 if mode == BotMode.NORMAL else 80
    if signal.score < min_score:
        await tg.alert_signal_rejected(signal.symbol, signal.action, signal.score,
                                        f"Score {signal.score} < {min_score}")
        return

    # ── Equity check ──
    try:
        wb, upnl = await execution.get_balance_usdt()
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")
        return

    true_eq = risk_engine.true_equity(wb, upnl)
    can, msg = risk_engine.can_trade(true_eq)
    if not can:
        await tg.alert_signal_rejected(signal.symbol, signal.action, signal.score, msg)
        if "Daily stop" in msg:
            await tg.alert_daily_stop()
        return
    if msg:  # warning, but still tradeable
        await tg.alert_daily_warning("WARNING", risk_engine.daily_dd_pct(true_eq))

    # ── Duplicate check ──
    if signal.symbol in open_trades:
        await tg.alert_signal_rejected(signal.symbol, signal.action, signal.score,
                                        "Already in trade on this symbol")
        return

    # ── Build market state ──
    ms = MarketState(
        score=signal.score,
        tf_alignment=signal.tf_alignment or 0,
        loss_streak=risk_engine.loss_streak,
        daily_dd_pct=risk_engine.daily_dd_pct(true_eq),
        mode=mode,
    )

    leverage = get_leverage(signal.symbol, ms)
    lev_note = leverage_note(signal.symbol, ms)
    risk_pct = risk_engine.risk_pct(signal.score, mode)

    # ── ATR / SL ──
    atr = signal.atr
    if not atr or atr <= 0:
        logger.warning(f"No ATR in signal for {signal.symbol}, skipping")
        return

    side = "LONG" if signal.action == "buy" else "SHORT"
    entry = signal.price
    sl = risk_engine.sl_from_atr(entry, atr, side, multiplier=2.0)
    tp1, tp2, tp3 = risk_engine.tp_prices(entry, sl, side)

    qty = risk_engine.position_size(true_eq, risk_pct, entry, sl, leverage)
    if qty <= 0:
        logger.warning(f"Position size 0 for {signal.symbol}")
        return

    # ── Set leverage ──
    try:
        await execution.set_leverage(signal.symbol, leverage)
    except Exception as e:
        logger.error(f"Set leverage failed: {e}")
        return

    # ── Alert premium ──
    if signal.score >= 90 and (signal.tf_alignment or 0) >= 4:
        await tg.alert_premium_setup(signal.symbol, side, signal.score)

    # ── Order type: limit (maker 0.02%) for score<90, market for premium ──
    # Limit offset: 0.05% inside the spread to get filled quickly
    use_limit   = signal.score < 90 and mode != BotMode.AGGRESSIVE
    limit_price = None
    if use_limit:
        offset = entry * 0.0005
        limit_price = entry - offset if side == "LONG" else entry + offset

    # ── Execute ──
    try:
        order = await execution.open_trade(
            symbol=signal.symbol,
            side=side,
            qty=qty,
            sl_price=sl,
            tp1_price=tp1,
            tp2_price=tp2,
            use_limit=use_limit,
            limit_price=limit_price,
        )
    except Exception as e:
        logger.error(f"Execution failed for {signal.symbol}: {e}")
        return

    # ── Record trade ──
    now = datetime.now(UTC)
    hour = now.hour
    session = "London" if 8 <= hour < 16 else "NY" if 13 <= hour < 21 else "Overlap" if 13 <= hour < 16 else "Other"

    ts = TradeState()
    ts.symbol = signal.symbol
    ts.side = side
    ts.score = signal.score
    ts.leverage = leverage
    ts.risk_pct = risk_pct
    ts.entry_price = entry
    ts.quantity = qty
    ts.remaining_qty = qty
    ts.sl_price = sl
    ts.tp1_price = tp1
    ts.tp2_price = tp2
    ts.tp3_price = tp3
    ts.opened_at = now
    ts.session = session

    # Register in self-protection (overtrading check)
    sp = get_protection()
    sp.record_trade(signal.symbol)

    async with SessionLocal() as db:
        db_trade = DbTrade(
            signal_id=sig_id,
            symbol=signal.symbol,
            side=side,
            score=signal.score,
            leverage=leverage,
            risk_pct=risk_pct,
            entry_price=entry,
            quantity=qty,
            sl_price=sl,
            tp1_price=tp1,
            tp2_price=tp2,
            tp3_price=tp3,
            session=session,
            status="open",
        )
        db.add(db_trade)
        await db.commit()
        await db.refresh(db_trade)
        ts.db_id = db_trade.id

    open_trades[signal.symbol] = ts

    await tg.alert_trade_opened(signal.symbol, side, signal.score, leverage, entry, sl, tp1)
    logger.info(f"Trade opened: {signal.symbol} {side} x{leverage} @ {entry:.4f} ({lev_note})")


async def _handle_close(signal: WebhookSignal) -> None:
    ts = open_trades.get(signal.symbol)
    if not ts:
        return
    try:
        await execution.cancel_all_orders(signal.symbol)
        await execution.close_position(signal.symbol, ts.side)
    except Exception as e:
        logger.error(f"Close failed for {signal.symbol}: {e}")
        return

    now = datetime.now(UTC)
    hold_sec = int((now - ts.opened_at).total_seconds()) if ts.opened_at else 0
    pnl = (signal.price - ts.entry_price) * ts.quantity * (1 if ts.side == "LONG" else -1)
    fees = execution.estimate_fees(ts.entry_price * ts.quantity, n_orders=2)
    net_pnl = pnl - fees
    pnl_pct = net_pnl / (ts.entry_price * ts.quantity / ts.leverage) * 100

    is_win = net_pnl > 0
    risk_engine.record_result(is_win)
    
    # Record pair performance
    pr = get_pair_ranking()
    pr.record_trade_result(ts.symbol, is_win, net_pnl)

    async with SessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(select(DbTrade).where(DbTrade.id == ts.db_id))
        db_trade = result.scalar_one_or_none()
        if db_trade:
            db_trade.exit_price = signal.price
            db_trade.pnl_usdt = net_pnl
            db_trade.pnl_pct = pnl_pct
            db_trade.fees_usdt = fees
            db_trade.hold_time_sec = hold_sec
            db_trade.exit_reason = "signal_close"
            db_trade.status = "closed"
            db_trade.closed_at = now
            await db.commit()

    trade_data = {
        "symbol": ts.symbol, "side": ts.side, "score": ts.score,
        "leverage": ts.leverage, "risk_pct": ts.risk_pct,
        "entry_price": ts.entry_price, "exit_price": signal.price,
        "pnl_usdt": net_pnl, "pnl_pct": pnl_pct, "fees_usdt": fees,
        "sl_price": ts.sl_price, "tp1_price": ts.tp1_price,
        "tp2_price": ts.tp2_price, "exit_reason": "signal_close",
        "hold_time_sec": hold_sec, "session": ts.session, "opened_at": ts.opened_at,
    }
    try:
        export_trade(trade_data)
    except Exception as e:
        logger.warning(f"Obsidian export failed: {e}")

    await tg.alert_sl_hit(ts.symbol, ts.side, signal.price, net_pnl) if not is_win \
        else await tg.alert_tp_hit(ts.symbol, ts.side, 3, signal.price, net_pnl)

    del open_trades[signal.symbol]


# ─────────────────────────────────────────────
# Status endpoints
# ─────────────────────────────────────────────

@app.get("/status")
async def status():
    return {
        "running": state["running"],
        "paused": state["paused"],
        "mode": state["mode"],
        "open_trades": list(open_trades.keys()),
        "loss_streak": risk_engine.loss_streak,
    }


@app.get("/trades")
async def get_trades():
    async with SessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(DbTrade).order_by(DbTrade.opened_at.desc()).limit(50)
        )
        trades = result.scalars().all()
    return [
        {
            "id": t.id, "symbol": t.symbol, "side": t.side,
            "score": t.score, "leverage": t.leverage,
            "entry_price": float(t.entry_price or 0),
            "exit_price": float(t.exit_price or 0),
            "pnl_usdt": float(t.pnl_usdt or 0),
            "status": t.status, "exit_reason": t.exit_reason,
            "opened_at": str(t.opened_at),
        }
        for t in trades
    ]


@app.post("/evolve")
async def trigger_evolution():
    async with SessionLocal() as db:
        report = await run_evolution_cycle(db)
    try:
        export_evolution_report(report)
    except Exception as e:
        logger.warning(f"Obsidian evolution export failed: {e}")
    return report


@app.post("/control/{action}")
async def control(action: str):
    if action == "start":
        state["running"] = True
    elif action == "stop":
        state["running"] = False
    elif action == "pause":
        state["paused"] = True
    elif action == "resume":
        state["paused"] = False
    elif action in ("normal", "aggressive", "safe"):
        state["mode"] = action
    else:
        raise HTTPException(400, f"Unknown action: {action}")
    return {"status": action}
