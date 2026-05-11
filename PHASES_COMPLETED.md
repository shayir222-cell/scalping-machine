# Scalping Machine — Completed Development Phases

## Overview
Binance Futures scalping bot with Telegram control, signal validation, and AI optimization.  
**Status**: All 3 phases implemented and integrated. Ready for Binance API key validation.

---

## PHASE 1: Core Trading Engine ✅

### Components
- **FastAPI Webhook Receiver** (`app/main.py`)
  - Signal ingestion via `/webhook` endpoint
  - Token-based authentication
  - Async event processing

- **Binance Futures Client** (`app/execution.py`)
  - Direct HTTP API (no SDK)
  - Market & limit order execution
  - Stop-loss and take-profit management
  - Position mode configuration
  - Real-time balance & position queries

- **Risk Management Engine** (`app/risk.py`)
  - Dynamic position sizing based on score/mode
  - ATR-based stop-loss calculation
  - Daily drawdown tracking (±5% thresholds)
  - Loss streak detection
  - Mode-based leverage scaling (SAFE/NORMAL/AGGRESSIVE)

- **Leverage Optimization** (`app/leverage_engine.py`)
  - Per-pair leverage table (BTCUSDT=20x, ETHUSDT=10x, etc.)
  - Dynamic reduction based on:
    - Market volatility (VIX proxy)
    - Loss streaks (reduce after 3+ losses)
    - Daily drawdown level

- **Signal Models** (`app/models.py`)
  - WebhookSignal: TradingView format validation
  - TradeState: In-memory trade tracking
  - BotMode: SAFE/NORMAL/AGGRESSIVE enum

---

## PHASE 2: Signal Protection & Pair Ranking ✅

### 2.1 Self-Protection Layer (`app/self_protection.py`)
Prevents overtrading, duplicates, and stale signals.

**Features:**
- **Overtrading Detection**: Max 6 trades per symbol per hour
- **Duplicate Signals**: Reject same (symbol, action) within 5 seconds
- **Stale Alert Detection**: Reject signals older than 60 seconds
- **API Order Deduplication**: Track order IDs to prevent double-execution

**Integration:**
- Webhook validates all signals before processing
- Self-protection check result logged and alerted to Telegram

**Implementation:**
```python
sp = get_protection()
valid, issues = sp.validate_signal(symbol, action, timestamp)
if not valid:
    # Reject with reasons in Telegram alert
    sp.mark_signal_processed(symbol, action)
```

### 2.2 Stale Alert Detection
- Parses ISO 8601 timestamps from TradingView
- Compares alert age with current UTC time
- Threshold: 60 seconds (configurable as `STALE_ALERT_THRESHOLD`)
- Handles missing timestamps gracefully

### 2.3 Pair Ranking & Prioritization (`app/pair_ranking.py`)
Tracks pair performance over 7-day rolling window.

**Metrics:**
- Win rate (%)
- Total PnL
- Average PnL per trade
- Expectancy (avg profit per trade)

**Score Calculation:**
```
score = (win_rate / 100 * 70) + (max(0, expectancy_factor * 100) * 0.3)
```
- Requires minimum 3 trades to rank
- Normalized to 0–100 scale

**Priorities:**
- HIGH (≥70): Trade more frequently
- NORMAL (50–70): Standard approach
- LOW (30–50): Reduce frequency
- AVOID (<30): Skip or exit early

**Integration:**
- Updates on trade close (`_handle_close()`)
- Accessible via `/top_pairs` Telegram command
- Informs pair selection for future signals

---

## PHASE 3: AI Optimizer (72h Analysis) ✅

### Core Function (`app/optimizer.py`)
Analyzes trading performance every 72 hours and generates recommendations.

**Metrics Analyzed:**
- Total trades in period
- Win rate %
- Expectancy (avg profit per trade)
- Best/worst performing pairs
- Average leverage used
- Score accuracy (how well score predicts wins)
- Leverage efficiency (profit per leverage unit)

**Recommendations Generated:**
1. **Win Rate Analysis**
   - If WR < 45%: Suggest stricter score threshold (+5 points)
   - If WR > 65%: Allow leverage increase

2. **Expectancy Analysis**
   - If negative: Review entry logic
   - If strong (>$5/trade): Consider AGGRESSIVE mode

3. **Pair Concentration**
   - Highlight best/worst performers
   - Suggest allocation changes

4. **Leverage Optimization**
   - If high leverage + low WR: Reduce leverage 25%
   - If low leverage + good WR: Increase leverage 25%

5. **Score Accuracy**
   - Track how well the 0–100 score predicts wins
   - Recommend recalibration if <50%

**Integration:**
- Background task runs every hour (checks if 72h elapsed)
- Results reported to Telegram via `/optimizer` command
- Exported to Obsidian vault for historical tracking

**Implementation:**
```python
opt = get_optimizer()
if await opt.should_analyze():  # 72h interval
    report = await opt.analyze_performance()
    await tg._send(opt.format_report(report))
```

---

## Integration Map

```
TradingView Alert
    ↓
[/webhook] → Token check
    ↓
Self-Protection Validation
    ├─ Overtrading check
    ├─ Duplicate detection
    └─ Stale alert check
    ↓
Signal Processing
    ├─ Score gate (70/75/80 based on mode)
    ├─ Equity/risk checks
    └─ Leverage calculation
    ↓
Binance Execution
    ├─ Market/limit order
    ├─ Stop-loss placement
    └─ Take-profit levels
    ↓
Trade Close (Manual Signal)
    ├─ Cancel all orders
    ├─ Close position
    ├─ Record PnL
    ├─ Update Pair Ranking
    └─ Alert Telegram
    ↓
Background Tasks (Every Hour)
    ├─ TP monitor (move SL to breakeven)
    ├─ 72h Optimizer check
    └─ Risk engine updates
    ↓
Telegram Reporting
    ├─ /status → Current equity & positions
    ├─ /top_pairs → Best 10 pairs by score
    ├─ /optimizer → 72h analysis + recommendations
    └─ /report → 72h evolution metrics
```

---

## Database Schema

### trades
- symbol, side, score, leverage, risk_pct
- entry_price, exit_price, quantity, remaining_qty
- sl_price, tp1_price, tp2_price, tp3_price
- pnl_usdt, pnl_pct, fees_usdt
- opened_at, closed_at, hold_time_sec
- session (London/NY/Overlap/Other)

### signals
- symbol, action, price, score, atr, tf_alignment
- raw_payload (full TradingView JSON)

### evolution_reports
- timestamp, metrics (JSON), recommendations (JSON)

---

## Telegram Commands

### Control
- `/start` — Resume trading
- `/stop` — Stop accepting new signals
- `/pause` — Toggle pause (no new trades, exist positions active)
- `/aggressive`, `/safe`, `/normal` — Set risk mode

### Status
- `/status` — Equity, positions, loss streak, mode
- `/pnl` — Daily P&L, unrealized, start equity
- `/positions` — Open positions with entry/PnL

### Analytics
- `/top_pairs` — Best 10 pairs (7-day rolling)
- `/report` — 72h evolution report (if available)
- `/optimizer` — Current 72h analysis & recommendations

---

## Configuration (.env)

```
BINANCE_API_KEY=...
BINANCE_SECRET=...
BINANCE_TESTNET=false (or true for testnet)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
WEBHOOK_TOKEN=scalper2024
```

---

## Deployment Checklist

- [ ] Validate Binance API keys (add IP to whitelist if needed)
- [ ] Configure `.env` with valid credentials
- [ ] Set Telegram bot token and chat ID
- [ ] Test webhook endpoint: `curl -X POST http://localhost:8000/webhook -H "Content-Type: application/json" -d '{"token":"scalper2024","symbol":"BTCUSDT","action":"buy","price":65000,"score":85,"atr":200,"tf_alignment":4,"time":"2026-05-11T12:00:00Z"}'`
- [ ] Verify TradingView webhook URL points to live server
- [ ] Monitor first 72h for optimizer initialization
- [ ] Adjust leverage/risk parameters based on live performance

---

## Files

- `app/main.py` — FastAPI app, webhook, lifespan, background tasks
- `app/execution.py` — Binance HTTP client
- `app/risk.py` — Risk management & position sizing
- `app/leverage_engine.py` — Dynamic leverage per pair
- `app/self_protection.py` — Signal validation layer
- `app/pair_ranking.py` — Pair performance tracking
- `app/optimizer.py` — 72h analysis & recommendations
- `app/telegram_bot.py` — Telegram polling & command handlers
- `app/database.py` — SQLAlchemy async ORM
- `app/models.py` — Pydantic signal/trade models
- `app/analytics.py` — Evolution report generation
- `app/obsidian_export.py` — Markdown export for history
- `pine/scalp_strategy.pine` — 4-TF TradingView script

---

**Last Updated**: May 11, 2026  
**Status**: Ready for live deployment after Binance API validation
