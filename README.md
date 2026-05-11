# Scalping Machine v1.0

Fully automated scalping bot for **Binance Futures USDT-M**, driven by TradingView Pine-Script alerts.

## Architecture

```
TradingView Pine v5 alertcondition
        ↓ JSON webhook (token-auth)
FastAPI /webhook
        ↓
Self-protection → Score gate → Risk engine → Leverage engine
        ↓
Binance Futures: market/limit + SL + TP1 + TP2
        ↓
SQLite trades log + Telegram alerts + Obsidian export
        ↓
Every 72h: AI optimizer → recommendations to Telegram
```

## Production deployment (Ubuntu 24.04 VPS)

```bash
ssh root@YOUR_VPS_IP
curl -fsSL https://raw.githubusercontent.com/shayir222-cell/scalping-machine/main/deploy.sh -o deploy.sh
bash deploy.sh
# → first pass creates a stub /home/app/scalping-machine/.env and exits
nano /home/app/scalping-machine/.env   # fill in credentials
bash deploy.sh                          # second pass finishes setup
```

`deploy.sh` provisions: Python 3.12 venv, systemd service with one uvicorn worker, nginx reverse proxy, UFW firewall (only 22/80/443), Let's Encrypt HTTPS via `<IP>.sslip.io` (no domain required). Before activating the service it probes Binance API to catch IP-whitelist / auth issues early. See the script for details.

## Local dev (Windows)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
# edit .env with Binance + Telegram credentials, BINANCE_TESTNET=true for paper trading
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload
```

Or run `start.bat`.

## TradingView setup

1. Open Pine Editor → paste contents of [`pine/scalp_strategy.pine`](pine/scalp_strategy.pine) → Save → Add to chart.
2. Open settings of the script on the chart → fill **Webhook token** with the same value as `WEBHOOK_TOKEN` in your `.env`.
3. Create an alert (Alt+A) → Condition: the script's `LONG`/`SHORT`/`CLOSE_LONG`/`CLOSE_SHORT` alerts. Frequency: **Once per bar close**.
4. Notifications → **Webhook URL**:
   ```
   https://YOUR_VPS_IP.sslip.io/webhook
   ```
5. Leave **Message** empty — the Pine script's `alertcondition(message=...)` payload is used automatically.

Use a 5M chart on a supported pair (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT).

## Telegram commands

| Command | Action |
|---|---|
| `/start` | Enable trading |
| `/stop` | Disable new trades |
| `/pause` | Toggle pause |
| `/status` | Equity, mode, open trades, loss streak |
| `/pnl` | Daily PnL |
| `/positions` | Open positions |
| `/top_pairs` | 7-day pair ranking |
| `/optimizer` | Latest 72h analysis & recommendations |
| `/report` | 72h evolution report |
| `/aggressive` `/normal` `/safe` | Switch risk mode |

## Score engine (max 100)

| Component | Max |
|---|---|
| Trend (1H + 15M EMA alignment) | 20 |
| Structure (BOS / CHoCH / liquidity sweep) | 20 |
| Volume spike | 15 |
| Momentum (RSI) | 15 |
| VWAP context | 10 |
| Spread / ATR health | 10 |
| Session edge | 10 |

Score gate: 70 in AGGRESSIVE mode, 75 in NORMAL, 80 in SAFE. Premium = score ≥ 90.

## Dynamic leverage

| Symbol | 70-74 | 75-84 | 85-89 | 90+ |
|---|---|---|---|---|
| BTCUSDT | x4 | x7 | x10 | x12 |
| ETHUSDT | x4 | x6 | x8 | x10 |
| SOLUSDT | x3 | x5 | x7 | x8 |
| BNBUSDT | x3 | x5 | x6 | x7 |
| XRP/DOGE | x3 | x4 | x5 | x6 |

Auto-halved on: 3+ loss streak, daily DD > 3%, chaotic ATR, outside session.

## Risk rules

- Base risk: 1% per trade
- Strong setup (score ≥ 85): 1.25%
- Premium (score ≥ 90): 1.5%
- Daily stop: −5% true equity → halt until next UTC day

## HTTP endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/webhook` | POST | TradingView signal ingress (token-protected) |
| `/status` | GET | Bot status |
| `/trades` | GET | Last 50 trades |
| `/evolve` | POST | Force 72h analysis |
| `/control/{action}` | POST | start/stop/pause/resume/normal/aggressive/safe |
