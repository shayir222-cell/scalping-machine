"""
Exports trades and daily reports to an Obsidian vault.
"""
import os
from datetime import date, datetime, timezone
from pathlib import Path

from loguru import logger

UTC = timezone.utc


def _vault() -> Path:
    p = os.getenv("OBSIDIAN_VAULT_PATH", "")
    if not p:
        raise EnvironmentError("OBSIDIAN_VAULT_PATH not set")
    return Path(p)


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─────────────────────────────────────────────────────────
# Trade export
# ─────────────────────────────────────────────────────────

def export_trade(trade_data: dict) -> Path:
    """
    Write a single trade to Obsidian.
    trade_data keys: symbol, side, score, leverage, risk_pct,
      entry_price, exit_price, pnl_usdt, pnl_pct, fees_usdt,
      exit_reason, hold_time_sec, session, opened_at
    """
    vault = _vault()
    folder = _ensure(vault / "Scalping" / "Trades")

    opened_at: datetime = trade_data.get("opened_at", datetime.now(UTC))
    fname = f"{opened_at.strftime('%Y-%m-%d_%H%M')}_{trade_data['symbol']}_{trade_data['side']}.md"
    fpath = folder / fname

    pnl_usdt = trade_data.get("pnl_usdt", 0)
    pnl_pct = trade_data.get("pnl_pct", 0)
    result_emoji = "✅" if pnl_usdt > 0 else "❌"
    hold_min = (trade_data.get("hold_time_sec") or 0) // 60

    content = f"""---
date: {opened_at.strftime('%Y-%m-%d')}
time: {opened_at.strftime('%H:%M')} UTC
symbol: {trade_data['symbol']}
side: {trade_data['side']}
score: {trade_data.get('score', 0)}
leverage: x{trade_data.get('leverage', 0)}
risk_pct: {trade_data.get('risk_pct', 0):.2f}%
session: {trade_data.get('session', 'unknown')}
tags: [trade, {trade_data['symbol'].lower()}, scalping]
---

# {result_emoji} {trade_data['symbol']} {trade_data['side']} — {opened_at.strftime('%Y-%m-%d %H:%M')}

## Result
| | |
|---|---|
| PnL | **{pnl_usdt:+.4f} USDT** ({pnl_pct:+.2f}%) |
| Fees | {trade_data.get('fees_usdt', 0):.4f} USDT |
| Net | {pnl_usdt - trade_data.get('fees_usdt', 0):+.4f} USDT |
| Exit reason | {trade_data.get('exit_reason', '—')} |
| Hold time | {hold_min} min |

## Setup
| | |
|---|---|
| Score | {trade_data.get('score', 0)}/100 |
| Leverage | x{trade_data.get('leverage', 0)} |
| Risk | {trade_data.get('risk_pct', 0):.2f}% |
| Entry | {trade_data.get('entry_price', 0):.4f} |
| Exit | {trade_data.get('exit_price', 0):.4f} |
| SL | {trade_data.get('sl_price', 0):.4f} |
| TP1 | {trade_data.get('tp1_price', 0):.4f} |
| TP2 | {trade_data.get('tp2_price', 0):.4f} |

## Notes
{trade_data.get('entry_reason', '')}
"""

    fpath.write_text(content, encoding="utf-8")
    logger.info(f"Trade exported to Obsidian: {fpath.name}")
    return fpath


# ─────────────────────────────────────────────────────────
# Daily report export
# ─────────────────────────────────────────────────────────

def export_daily_report(report: dict) -> Path:
    """
    report keys: date, equity_start, equity_end, pnl_usdt, pnl_pct,
      max_drawdown_pct, total_trades, wins, losses, best_pair, worst_pair,
      notes, trades (list of trade dicts)
    """
    vault = _vault()
    folder = _ensure(vault / "Scalping" / "Daily")

    d = report.get("date", date.today())
    fpath = folder / f"{d}.md"

    wr = (
        report["wins"] / report["total_trades"] * 100
        if report["total_trades"] > 0 else 0
    )
    pnl = report.get("pnl_usdt", 0)
    result_emoji = "📈" if pnl > 0 else "📉"

    trades_md = ""
    for t in report.get("trades", []):
        e = "✅" if (t.get("pnl_usdt") or 0) > 0 else "❌"
        trades_md += (
            f"| {e} | {t.get('opened_at', '')[:16]} | {t['symbol']} | "
            f"{t['side']} | {t.get('score',0)} | x{t.get('leverage',0)} | "
            f"{t.get('pnl_usdt',0):+.4f} | {t.get('exit_reason','—')} |\n"
        )

    content = f"""---
date: {d}
pnl_usdt: {pnl:+.4f}
win_rate: {wr:.1f}%
total_trades: {report['total_trades']}
tags: [daily-report, scalping]
---

# {result_emoji} Daily Report — {d}

## Summary
| | |
|---|---|
| Equity start | ${report.get('equity_start', 0):.2f} |
| Equity end | ${report.get('equity_end', 0):.2f} |
| PnL | **{pnl:+.4f} USDT ({report.get('pnl_pct', 0):+.2f}%)** |
| Max drawdown | {report.get('max_drawdown_pct', 0):.2f}% |
| Trades | {report['total_trades']} (W:{report['wins']} / L:{report['losses']}) |
| Win rate | {wr:.1f}% |
| Best pair | {report.get('best_pair', '—')} |
| Worst pair | {report.get('worst_pair', '—')} |

## Trades
| | Time | Symbol | Side | Score | Lev | PnL | Exit |
|---|---|---|---|---|---|---|---|
{trades_md}
## Notes
{report.get('notes', '')}
"""

    fpath.write_text(content, encoding="utf-8")
    logger.info(f"Daily report exported: {fpath.name}")
    return fpath


# ─────────────────────────────────────────────────────────
# Evolution report export
# ─────────────────────────────────────────────────────────

def export_evolution_report(report: dict) -> Path:
    vault = _vault()
    folder = _ensure(vault / "Scalping" / "Evolution")
    today = date.today()
    fpath = folder / f"cycle_{today}.md"

    metrics = report.get("metrics", {})
    recs = report.get("recommendations", [])
    recs_md = "\n".join(f"- {r}" for r in recs)

    content = f"""---
date: {today}
cycle: {report.get('cycle_start')} → {report.get('cycle_end')}
tags: [evolution, scalping]
---

# 3-Day Evolution Report — {today}

## Performance Metrics
| Metric | Value |
|---|---|
| Win rate | {metrics.get('win_rate_pct', 0):.1f}% |
| Total trades | {metrics.get('total_trades', 0)} |
| Total PnL | {metrics.get('total_pnl_usdt', 0):+.4f} USDT |
| Expectancy | {metrics.get('expectancy_usdt', 0):+.4f} USDT/trade |
| Fee drag | {metrics.get('fee_drag_pct', 0):.1f}% |
| Best pair | {metrics.get('best_pair', '—')} |
| Worst pair | {metrics.get('worst_pair', '—')} |
| Best hour | {metrics.get('best_hour_utc', '—')}:00 UTC |
| Avg score (wins) | {metrics.get('avg_score_wins', 0):.1f} |
| Avg score (losses) | {metrics.get('avg_score_losses', 0):.1f} |

## Recommendations
{recs_md}
"""

    fpath.write_text(content, encoding="utf-8")
    logger.info(f"Evolution report exported: {fpath.name}")
    return fpath
