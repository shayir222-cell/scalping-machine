#!/usr/bin/env bash
# Pings the bot every 5 min via cron. If /status doesn't reply healthy,
# fires a Telegram alert directly through the Bot API. Reads creds from
# the same .env the bot uses.
#
# Cron suggestion (every 5 min, single line):
#   */5 * * * *  /home/app/scalping-machine/scripts/healthcheck.sh
set -euo pipefail

ENV_FILE="/home/app/scalping-machine/.env"
STATE_FILE="/tmp/scalping_healthcheck_alerted"
URL="http://127.0.0.1:8002/status"

# Read creds (POSIX-friendly — only the two lines we need)
TG_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2 | tr -d '" \r')
TG_CHAT=$(grep  '^TELEGRAM_CHAT_ID='   "$ENV_FILE" | head -1 | cut -d= -f2 | tr -d '" \r')

# Probe — 5s timeout, no follow redirects, want JSON with "running":true
RESP=$(curl -sS --max-time 5 "$URL" 2>/dev/null || echo "")
if echo "$RESP" | grep -q '"running":true'; then
    # All good. If we previously alerted, clear the flag and send recovery.
    if [ -f "$STATE_FILE" ]; then
        curl -sS --max-time 10 -X POST \
            "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${TG_CHAT}" \
            --data-urlencode "text=✅ Bot recovered — /status responding" >/dev/null
        rm -f "$STATE_FILE"
    fi
    exit 0
fi

# Failure. Alert at most once per outage (until recovery clears state file).
if [ -f "$STATE_FILE" ]; then
    exit 0
fi
touch "$STATE_FILE"
curl -sS --max-time 10 -X POST \
    "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TG_CHAT}" \
    --data-urlencode "text=🚨 Bot DOWN — /status not responding. Check: systemctl status scalping" >/dev/null
exit 1
