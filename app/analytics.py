"""
3-day evolution engine.
Queries last 72h of trades and generates upgrade recommendations.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .database import Trade, DailyStat, EvolutionReport

UTC = timezone.utc


async def run_evolution_cycle(session: AsyncSession) -> dict[str, Any]:
    cycle_end = datetime.now(UTC)
    cycle_start = cycle_end - timedelta(hours=72)

    result = await session.execute(
        select(Trade).where(
            and_(Trade.opened_at >= cycle_start, Trade.status != "open")
        )
    )
    trades = result.scalars().all()

    if not trades:
        return {"status": "no_data", "recommendations": []}

    metrics = _compute_metrics(trades)
    recs = _recommendations(metrics)

    report = EvolutionReport(
        cycle_start=cycle_start.date(),
        cycle_end=cycle_end.date(),
        metrics=metrics,
        recommendations=recs,
    )
    session.add(report)
    await session.commit()

    return {"metrics": metrics, "recommendations": recs}


def _compute_metrics(trades: list[Trade]) -> dict[str, Any]:
    wins = [t for t in trades if (t.pnl_usdt or 0) > 0]
    losses = [t for t in trades if (t.pnl_usdt or 0) <= 0]

    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = sum(t.pnl_usdt or 0 for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_usdt or 0 for t in losses) / len(losses) if losses else 0
    total_fees = sum(t.fees_usdt or 0 for t in trades)
    total_pnl = sum(t.pnl_usdt or 0 for t in trades)
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    # Best/worst pairs
    by_pair: dict[str, list[float]] = {}
    for t in trades:
        by_pair.setdefault(t.symbol, []).append(t.pnl_usdt or 0)
    pair_pnl = {sym: sum(pnls) for sym, pnls in by_pair.items()}
    best_pair = max(pair_pnl, key=pair_pnl.get) if pair_pnl else None
    worst_pair = min(pair_pnl, key=pair_pnl.get) if pair_pnl else None

    # Best hours (UTC)
    hour_pnl: dict[int, float] = {}
    for t in trades:
        if t.opened_at:
            h = t.opened_at.hour
            hour_pnl[h] = hour_pnl.get(h, 0) + (t.pnl_usdt or 0)
    best_hour = max(hour_pnl, key=hour_pnl.get) if hour_pnl else None
    worst_hour = min(hour_pnl, key=hour_pnl.get) if hour_pnl else None

    # Score accuracy: avg score of winners vs losers
    avg_score_wins = (
        sum(t.score or 0 for t in wins) / len(wins) if wins else 0
    )
    avg_score_losses = (
        sum(t.score or 0 for t in losses) / len(losses) if losses else 0
    )

    # Fee drag
    fee_drag_pct = (total_fees / max(abs(total_pnl), 0.01)) * 100 if total_pnl else 0

    return {
        "total_trades": len(trades),
        "win_rate_pct": round(win_rate, 1),
        "avg_win_usdt": round(avg_win, 3),
        "avg_loss_usdt": round(avg_loss, 3),
        "expectancy_usdt": round(expectancy, 4),
        "total_pnl_usdt": round(total_pnl, 3),
        "total_fees_usdt": round(total_fees, 3),
        "fee_drag_pct": round(fee_drag_pct, 1),
        "best_pair": best_pair,
        "worst_pair": worst_pair,
        "pair_pnl": {k: round(v, 3) for k, v in pair_pnl.items()},
        "best_hour_utc": best_hour,
        "worst_hour_utc": worst_hour,
        "avg_score_wins": round(avg_score_wins, 1),
        "avg_score_losses": round(avg_score_losses, 1),
    }


def _recommendations(m: dict) -> list[str]:
    recs: list[str] = []
    wr = m["win_rate_pct"]
    exp = m["expectancy_usdt"]
    fee_drag = m["fee_drag_pct"]
    score_gap = m["avg_score_wins"] - m["avg_score_losses"]

    if wr < 40:
        recs.append("WIN RATE <40%: Raise min_score to 80. Tighten session filter.")
    elif wr > 65:
        recs.append("WIN RATE >65%: Consider lowering min_score to 72 for more volume.")

    if exp < 0:
        recs.append("NEGATIVE EXPECTANCY: Stop trading until strategy is reviewed.")
    elif exp < 0.5:
        recs.append("LOW EXPECTANCY: Widen TP2 target to 6R or improve entry precision.")

    if fee_drag > 40:
        recs.append(f"FEE DRAG {fee_drag:.0f}%: Reduce trade frequency or use limit orders.")

    if score_gap < 3:
        recs.append("SCORE NOT PREDICTIVE: Review indicator weights. Consider score ≥85 only.")

    if m["best_pair"] and m["worst_pair"] and m["best_pair"] != m["worst_pair"]:
        recs.append(f"FOCUS: Prioritize {m['best_pair']}. Consider pausing {m['worst_pair']}.")

    if m["best_hour_utc"] is not None:
        recs.append(f"BEST HOUR: {m['best_hour_utc']}:00 UTC. Worst: {m['worst_hour_utc']}:00 UTC.")

    if not recs:
        recs.append("System healthy. No changes needed this cycle.")

    return recs


async def get_last_report(session: AsyncSession) -> dict | None:
    result = await session.execute(
        select(EvolutionReport).order_by(EvolutionReport.created_at.desc()).limit(1)
    )
    r = result.scalar_one_or_none()
    if not r:
        return None
    return {
        "cycle_start": str(r.cycle_start),
        "cycle_end": str(r.cycle_end),
        "metrics": r.metrics,
        "recommendations": r.recommendations,
    }
