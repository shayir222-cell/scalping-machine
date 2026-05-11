"""
Telegram control center via aiogram 3.x
"""
import asyncio
import os
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from loguru import logger

UTC = timezone.utc

# Initialize bot only if token is provided
token = os.getenv("TELEGRAM_BOT_TOKEN", "")
bot = Bot(token=token) if token else None
dp = Dispatcher()

# Injected at startup from main.py
_state: dict = {}


def inject_state(state: dict) -> None:
    global _state
    _state = state


# ─────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(msg: Message) -> None:
    if not _is_owner(msg):
        return
    _state["running"] = True
    _state["paused"] = False
    await msg.answer("✅ Bot started. Watching for signals.")


@dp.message(Command("stop"))
async def cmd_stop(msg: Message) -> None:
    if not _is_owner(msg):
        return
    _state["running"] = False
    await msg.answer("🛑 Bot stopped. No new trades will open.")


@dp.message(Command("pause"))
async def cmd_pause(msg: Message) -> None:
    if not _is_owner(msg):
        return
    _state["paused"] = not _state.get("paused", False)
    s = "paused ⏸" if _state["paused"] else "resumed ▶️"
    await msg.answer(f"Bot {s}.")


@dp.message(Command("aggressive", "a"))
async def cmd_aggressive(msg: Message) -> None:
    if not _is_owner(msg):
        return
    _state["mode"] = "aggressive"
    await msg.answer("⚡ Aggressive mode ON — max leverage on all valid setups.")


@dp.message(Command("safe", "s"))
async def cmd_safe(msg: Message) -> None:
    if not _is_owner(msg):
        return
    _state["mode"] = "safe"
    await msg.answer("🛡 Safe mode ON — risk halved, leverage halved.")


@dp.message(Command("normal", "n"))
async def cmd_normal(msg: Message) -> None:
    if not _is_owner(msg):
        return
    _state["mode"] = "normal"
    await msg.answer("✅ Normal mode.")


@dp.message(Command("status"))
async def cmd_status(msg: Message) -> None:
    if not _is_owner(msg):
        return
    risk_engine = _state.get("risk_engine")
    exec_engine = _state.get("execution")

    run = "✅ Running" if _state.get("running") else "🛑 Stopped"
    paused = " (PAUSED)" if _state.get("paused") else ""
    mode = _state.get("mode", "normal").upper()

    eq_text = "—"
    dd_text = "—"
    if exec_engine and risk_engine:
        try:
            wb, upnl = await exec_engine.get_balance_usdt()
            eq = risk_engine.true_equity(wb, upnl)
            dd = risk_engine.daily_dd_pct(eq)
            eq_text = f"${eq:.2f} (wallet ${wb:.2f})"
            dd_text = f"{dd:+.2f}%"
        except Exception:
            pass

    positions = _state.get("open_trades", {})
    pos_lines = ""
    for sym, t in positions.items():
        pos_lines += f"\n  {sym} {t.side} x{t.leverage} @ {t.entry_price:.4f}"
    if not pos_lines:
        pos_lines = "\n  None"

    await msg.answer(
        f"<b>Scalping Machine</b>\n"
        f"Status: {run}{paused}\n"
        f"Mode: {mode}\n"
        f"Equity: {eq_text}\n"
        f"Daily DD: {dd_text}\n"
        f"Loss streak: {risk_engine.loss_streak if risk_engine else '—'}\n"
        f"Open trades:{pos_lines}",
        parse_mode="HTML"
    )


@dp.message(Command("pnl"))
async def cmd_pnl(msg: Message) -> None:
    if not _is_owner(msg):
        return
    risk_engine = _state.get("risk_engine")
    exec_engine = _state.get("execution")
    if not exec_engine or not risk_engine:
        await msg.answer("Engine not initialized.")
        return
    try:
        wb, upnl = await exec_engine.get_balance_usdt()
        eq = risk_engine.true_equity(wb, upnl)
        dd = risk_engine.daily_dd_pct(eq)
        start = risk_engine.day_start_equity
        pnl = eq - start
        await msg.answer(
            f"<b>PnL Today</b>\n"
            f"Start: ${start:.2f}\n"
            f"Now: ${eq:.2f}\n"
            f"PnL: <b>{pnl:+.4f} USDT ({dd:+.2f}%)</b>\n"
            f"Unrealized: {upnl:+.4f} USDT",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.answer(f"Error: {e}")


@dp.message(Command("positions"))
async def cmd_positions(msg: Message) -> None:
    if not _is_owner(msg):
        return
    exec_engine = _state.get("execution")
    if not exec_engine:
        await msg.answer("Not initialized.")
        return
    try:
        positions = await exec_engine.get_open_positions()
        if not positions:
            await msg.answer("No open positions.")
            return
        lines = ["<b>Open Positions</b>"]
        for p in positions:
            side = "LONG" if float(p["positionAmt"]) > 0 else "SHORT"
            pnl = float(p.get("unRealizedProfit", 0))
            lines.append(
                f"{p['symbol']} {side} qty={abs(float(p['positionAmt'])):.4f} "
                f"@ {float(p['entryPrice']):.4f} | PnL: {pnl:+.4f}"
            )
        await msg.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await msg.answer(f"Error: {e}")


@dp.message(Command("top_pairs"))
async def cmd_top_pairs(msg: Message) -> None:
    if not _is_owner(msg):
        return
    from .pair_ranking import get_pair_ranking
    pr = get_pair_ranking()
    ranked = pr.get_ranked_pairs()
    
    if not ranked:
        await msg.answer("No pair stats yet (min 3 trades per pair).")
        return
    
    lines = ["<b>Pair Rankings (7D)</b>"]
    for i, (sym, score) in enumerate(ranked[:10], 1):
        stats = pr.stats.get(sym)
        if stats:
            lines.append(
                f"{i}. {sym} | Score: {score:.1f}/100\n"
                f"   WR: {stats.win_rate:.1f}% | Trades: {stats.total_trades} | "
                f"PnL: ${stats.total_pnl:.2f}"
            )
    
    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("report"))
async def cmd_report(msg: Message) -> None:
    if not _is_owner(msg):
        return
    from .analytics import get_last_report
    from .database import SessionLocal
    async with SessionLocal() as db:
        report = await get_last_report(db)
    if not report:
        await msg.answer("No report available yet. Run after 72h of trading.")
        return
    m = report["metrics"]
    recs = "\n".join(f"• {r}" for r in report["recommendations"])
    await msg.answer(
        f"<b>72h Evolution Report</b>\n"
        f"Win rate: {m.get('win_rate_pct', 0):.1f}%\n"
        f"Trades: {m.get('total_trades', 0)}\n"
        f"PnL: {m.get('total_pnl_usdt', 0):+.4f} USDT\n"
        f"Expectancy: {m.get('expectancy_usdt', 0):+.4f}/trade\n"
        f"Fee drag: {m.get('fee_drag_pct', 0):.1f}%\n\n"
        f"<b>Recommendations:</b>\n{recs}",
        parse_mode="HTML"
    )


@dp.message(Command("optimizer"))
async def cmd_optimizer(msg: Message) -> None:
    if not _is_owner(msg):
        return
    from .optimizer import get_optimizer
    opt = get_optimizer()
    report = await opt.analyze_performance()
    if report:
        msg_text = opt.format_report(report)
        await msg.answer(msg_text, parse_mode="HTML")
    else:
        await msg.answer("No trades in last 72h, or analysis not yet ready.")


# ─────────────────────────────────────────────
# Alert functions (called from main.py)
# ─────────────────────────────────────────────

async def alert_trade_opened(symbol: str, side: str, score: int, leverage: int,
                              entry: float, sl: float, tp1: float) -> None:
    emoji = "🟢" if side == "LONG" else "🔴"
    quality = "🔥 PREMIUM" if score >= 90 else "⚡ STRONG" if score >= 85 else "✅ GOOD" if score >= 75 else "⚠ WEAK"
    await _send(
        f"{emoji} <b>Trade Opened</b>\n"
        f"{symbol} {side} | x{leverage} | Score: {score} {quality}\n"
        f"Entry: {entry:.4f}\n"
        f"SL: {sl:.4f} | TP1: {tp1:.4f}"
    )


async def alert_tp_hit(symbol: str, side: str, tp_num: int, price: float, pnl: float) -> None:
    await _send(
        f"💰 <b>TP{tp_num} Hit</b> — {symbol} {side}\n"
        f"@ {price:.4f} | +{pnl:.4f} USDT"
    )


async def alert_sl_hit(symbol: str, side: str, price: float, pnl: float) -> None:
    await _send(
        f"🔴 <b>SL Hit</b> — {symbol} {side}\n"
        f"@ {price:.4f} | {pnl:.4f} USDT"
    )


async def alert_premium_setup(symbol: str, side: str, score: int) -> None:
    await _send(
        f"🔥 <b>PREMIUM SETUP</b>\n"
        f"{symbol} {side} | Score: {score}/100\n"
        f"4TF alignment detected!"
    )


async def alert_tp_filled(symbol: str, tp_level: str, entry: float) -> None:
    await _send(
        f"✅ <b>{tp_level} Filled</b>\n"
        f"{symbol} @ {entry:.4f}\n"
        f"SL moved to breakeven"
    )


async def alert_daily_warning(level: str, dd_pct: float) -> None:
    await _send(
        f"⚠️ <b>Daily DD Warning {level}</b>\n"
        f"Current drawdown: {dd_pct:.2f}%"
    )


async def alert_daily_stop() -> None:
    await _send("🛑 <b>DAILY STOP REACHED (-5%)</b>\nBot halted until tomorrow.")


async def alert_signal_rejected(symbol: str, action: str, score: int, reason: str) -> None:
    await _send(
        f"⚫ Signal rejected: {symbol} {action} score={score}\n"
        f"Reason: {reason}"
    )


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

async def _send(text: str) -> None:
    if bot is None:
        logger.warning("Telegram bot is not configured, message not sent")
        return

    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        return
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        # HTML parse can fail on user-supplied strings containing '<' / '>'.
        # Retry as plain text so the alert still gets through.
        try:
            import re
            plain = re.sub(r"</?[a-zA-Z][^>]*>", "", text)
            await bot.send_message(chat_id, plain)
        except Exception as e2:
            logger.warning(f"Telegram send failed: {e} / fallback: {e2}")


def _is_owner(msg: Message) -> bool:
    allowed = os.getenv("TELEGRAM_CHAT_ID", "")
    return str(msg.from_user.id) == allowed or str(msg.chat.id) == allowed


async def start_polling() -> None:
    if bot is None:
        logger.warning("Telegram bot token not configured; polling will not start")
        return
    await dp.start_polling(bot, skip_updates=True)
