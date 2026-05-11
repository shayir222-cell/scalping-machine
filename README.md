# Scalping Machine v1.0

Fully automated aggressive growth scalping bot for **Binance Futures USDT-M**.

> **Separate project.** No dependency on CrossX Trading Bot.

---

## Architecture

```
TradingView (Pine Script v6)
        ↓ webhook JSON
FastAPI /webhook  (port 8002)
        ↓
Signal validation → Score gate → Risk engine
        ↓
Leverage engine → Position sizing
        ↓
Binance Futures API (market/limit orders)
        ↓
Telegram alerts + PostgreSQL + Obsidian export
        ↓
Every 72h: Evolution engine → upgrade recommendations
```

---

## Quick Start

### 1. PostgreSQL
```bash
docker-compose up -d db
```

### 2. Install dependencies
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure
Fill in `.env`:
- `BINANCE_API_KEY` / `BINANCE_API_SECRET` (Futures API, NOT spot)
- `TELEGRAM_CHAT_ID` — your Telegram user ID (use @userinfobot)
- `OBSIDIAN_VAULT_PATH` — path to your Obsidian vault

### 4. Start
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8002
```
Or double-click `start.bat`.

---

## TradingView Setup

1. Open **5M chart** on any supported pair (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT)
2. Add script from `pine/scalp_strategy.pine`
3. Create alert → **"Once per bar close"**
4. Webhook URL: `http://YOUR_SERVER:8002/webhook`
5. Message body: leave empty (script sends JSON automatically)

---

## Telegram Commands

| Command | Action |
|---|---|
| `/start` | Enable trading |
| `/stop` | Disable trading |
| `/pause` | Toggle pause |
| `/status` | Full status |
| `/pnl` | Today's PnL |
| `/positions` | Open positions |
| `/top_pairs` | Pair performance |
| `/report` | 72h evolution report |
| `/aggressive` | Max leverage mode |
| `/safe` | Half risk mode |
| `/normal` | Default mode |

---

## Signal Score Engine (max 100)

| Component | Max |
|---|---|
| Trend (1H + 15M EMA alignment) | 20 |
| Structure (BOS / CHoCH / Liquidity sweep) | 20 |
| Volume spike | 15 |
| Momentum (RSI) | 15 |
| VWAP context | 10 |
| Spread / ATR health | 10 |
| Session edge | 10 |

Minimum to trade: **70** (default). Premium: **90+**.

---

## Dynamic Leverage

| Symbol | 70-74 | 75-84 | 85-89 | 90+ |
|---|---|---|---|---|
| BTCUSDT | x4 | x7 | x10 | x12 |
| ETHUSDT | x4 | x6 | x8 | x10 |
| SOLUSDT | x3 | x5 | x7 | x8 |
| BNBUSDT | x3 | x5 | x6 | x7 |
| XRP/DOGE | x3 | x4 | x5 | x6 |

Leverage auto-halved on: 2+ loss streak, DD > 3%, chaotic ATR, outside session.

---

## Risk Rules

- Base risk: **1%** per trade
- Strong setup (score ≥85): **1.25%**
- Premium (score ≥90): **1.5%**
- Daily stop: **-5%** true equity → halt
- Pause: 30 min after 2 losses, 60 min after 3+

---

## 3-Day Evolution Cycle

POST `/evolve` every 72h (or automate with cron/Task Scheduler).
Analyzes: win rate, fee drag, best pairs/hours, score accuracy.
Exports recommendations to Obsidian and Telegram `/report`.

---

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/webhook` | POST | Receive TradingView signal |
| `/status` | GET | Bot status |
| `/trades` | GET | Last 50 trades |
| `/evolve` | POST | Run 3-day analysis |
| `/control/{action}` | POST | start/stop/pause/resume/normal/aggressive/safe |
