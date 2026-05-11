#!/usr/bin/env bash
#
# Scalping Machine — VPS production bootstrap
# Target: Ubuntu 24.04 LTS, fresh server
# Run as root:  bash deploy.sh
#
# Idempotent. First pass installs deps + creates stub .env then exits.
# Fill .env on the server, then re-run to finish setup.
#
set -euo pipefail

# ─── Config ─────────────────────────────────────────────────────
VPS_IP="209.250.234.36"
EMAIL="shayir_9090@mail.ru"
HOSTNAME="${VPS_IP}.sslip.io"
REPO_URL="https://github.com/shayir222-cell/scalping-machine.git"
APP_USER="app"
APP_HOME="/home/${APP_USER}"
APP_DIR="${APP_HOME}/scalping-machine"
ENV_FILE="${APP_DIR}/.env"
SERVICE="scalping"
APP_PORT=8002
PYTHON="python3.12"

# ─── Helpers ────────────────────────────────────────────────────
log()  { printf "\033[1;34m[%(%H:%M:%S)T]\033[0m %s\n" -1 "$*"; }
ok()   { printf "\033[1;32m[OK]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
die()  { printf "\033[1;31m[FAIL]\033[0m %s\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0"

# ─── 1. System packages ─────────────────────────────────────────
log "Updating apt and installing packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    python3.12 python3.12-venv python3-pip \
    git nginx certbot python3-certbot-nginx \
    ufw curl dnsutils ca-certificates
timedatectl set-timezone UTC
ok "System packages installed"

# ─── 2. App user ────────────────────────────────────────────────
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    log "Creating user '$APP_USER'..."
    adduser --disabled-password --gecos "" "$APP_USER"
    if [[ -f /root/.ssh/authorized_keys ]]; then
        install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$APP_HOME/.ssh"
        install -m 600 -o "$APP_USER" -g "$APP_USER" \
            /root/.ssh/authorized_keys "$APP_HOME/.ssh/authorized_keys"
    fi
    ok "User $APP_USER created"
else
    ok "User $APP_USER already exists"
fi

# ─── 3. Repo + venv ─────────────────────────────────────────────
if [[ ! -d "$APP_DIR" ]]; then
    log "Cloning repo..."
    sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
else
    log "Repo present, pulling latest..."
    sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only || \
        warn "git pull failed (local changes?). Continuing with current checkout."
fi

if [[ ! -d "$APP_DIR/.venv" ]]; then
    log "Creating venv..."
    sudo -u "$APP_USER" "$PYTHON" -m venv "$APP_DIR/.venv"
fi

log "Installing Python deps..."
sudo -u "$APP_USER" bash -c "
    source '$APP_DIR/.venv/bin/activate'
    pip install --upgrade pip --quiet
    pip install -r '$APP_DIR/requirements.txt' --quiet
"
ok "Python env ready"

# ─── 4. .env stub or validate ───────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
    log "Creating stub .env (will exit so you can fill it)..."
    sudo -u "$APP_USER" tee "$ENV_FILE" >/dev/null <<EOF
# Scalping Machine — production .env
# All __FILL_ME__ must be replaced before re-running deploy.sh

BINANCE_API_KEY=__FILL_ME__
BINANCE_SECRET=__FILL_ME__
BINANCE_TESTNET=false

TELEGRAM_BOT_TOKEN=__FILL_ME__
TELEGRAM_CHAT_ID=__FILL_ME__

WEBHOOK_TOKEN=__GENERATE_RANDOM_TOKEN__

DATABASE_URL=sqlite+aiosqlite:////home/app/scalping-machine/scalping.db
EOF
    chmod 600 "$ENV_FILE"
    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    warn "Stub .env created at: $ENV_FILE"
    cat <<MSG

  NEXT STEPS:
  1) Edit it:
       nano $ENV_FILE
  2) Generate a strong webhook token (run on VPS):
       python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
  3) Whitelist VPS IP in Binance: API Management → edit key →
     Trusted IPs → add ${VPS_IP}
  4) Re-run this script:
       bash $0
MSG
    exit 0
fi

if grep -qE "__FILL_ME__|__GENERATE_RANDOM_TOKEN__" "$ENV_FILE"; then
    die "$ENV_FILE still has placeholders. Fill it in, then re-run."
fi
ok ".env present, no placeholders"

# ─── 5. Binance connectivity probe ──────────────────────────────
log "Probing Binance API from this VPS..."
PROBE_FILE=$(sudo -u "$APP_USER" mktemp)
sudo -u "$APP_USER" tee "$PROBE_FILE" >/dev/null <<'PYTHON'
import os, sys, asyncio
sys.path.insert(0, '.')
from app.main import load_env_file
load_env_file()
from app.execution import BinanceFutures

async def main():
    b = BinanceFutures(
        os.getenv('BINANCE_API_KEY', ''),
        os.getenv('BINANCE_SECRET', ''),
        testnet=False,
    )
    try:
        wb, upnl = await b.get_balance_usdt()
        print(f'OK  balance={wb:.4f} USDT  uPnL={upnl:+.4f}')
    except Exception as e:
        print(f'FAIL {e}')
        sys.exit(1)
    finally:
        await b._http.aclose()

asyncio.run(main())
PYTHON

set +e
PROBE_OUT=$(sudo -u "$APP_USER" bash -c "
    cd '$APP_DIR' && source .venv/bin/activate && python3 '$PROBE_FILE'
" 2>&1)
PROBE_STATUS=$?
set -e
rm -f "$PROBE_FILE"
echo "  $PROBE_OUT"
[[ $PROBE_STATUS -eq 0 ]] || die "Binance API not reachable. Check IP whitelist & keys."
ok "Binance authenticated"

# ─── 6. systemd service ─────────────────────────────────────────
log "Writing systemd unit..."
cat > /etc/systemd/system/${SERVICE}.service <<EOF
[Unit]
Description=Scalping Machine — Binance Futures bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app \\
    --host 127.0.0.1 --port ${APP_PORT} --workers 1 \\
    --proxy-headers --forwarded-allow-ips=127.0.0.1 \\
    --no-access-log
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE}

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${APP_DIR}

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
ok "systemd unit deployed"

# ─── 7. UFW firewall (BEFORE certbot so 80/443 reachable) ──────
log "Configuring UFW..."
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
yes | ufw --force enable >/dev/null
ufw status verbose | head -20
ok "Firewall up"

# ─── 8. nginx (HTTP-only or HTTPS-aware based on cert state) ───
CERT_PATH="/etc/letsencrypt/live/${HOSTNAME}/fullchain.pem"
if [[ -f "$CERT_PATH" ]]; then
    log "Writing nginx config (HTTPS-aware, cert exists)..."
    cat > /etc/nginx/sites-available/${SERVICE} <<EOF
server {
    listen 80;
    server_name ${HOSTNAME};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ${HOSTNAME};

    ssl_certificate     /etc/letsencrypt/live/${HOSTNAME}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${HOSTNAME}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    client_max_body_size 1m;
    proxy_read_timeout 30s;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    access_log /var/log/nginx/${SERVICE}.access.log;
    error_log  /var/log/nginx/${SERVICE}.error.log;
}
EOF
else
    log "Writing nginx config (HTTP-only; certbot will add HTTPS next)..."
    cat > /etc/nginx/sites-available/${SERVICE} <<EOF
server {
    listen 80;
    server_name ${HOSTNAME};

    client_max_body_size 1m;
    proxy_read_timeout 30s;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    access_log /var/log/nginx/${SERVICE}.access.log;
    error_log  /var/log/nginx/${SERVICE}.error.log;
}
EOF
fi
ln -sf /etc/nginx/sites-available/${SERVICE} /etc/nginx/sites-enabled/${SERVICE}
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
ok "nginx configured"

# ─── 9. DNS sanity check before certbot ────────────────────────
log "Checking DNS for ${HOSTNAME}..."
RESOLVED=$(dig +short "${HOSTNAME}" | head -1)
if [[ "$RESOLVED" != "$VPS_IP" ]]; then
    warn "DNS: ${HOSTNAME} resolved to '${RESOLVED}', expected '${VPS_IP}'."
    warn "sslip.io should resolve automatically. If this persists, certbot will fail."
else
    ok "DNS OK: ${HOSTNAME} → ${VPS_IP}"
fi

# ─── 10. HTTPS via Let's Encrypt ───────────────────────────────
if [[ ! -f "$CERT_PATH" ]]; then
    log "Requesting Let's Encrypt certificate..."
    certbot --nginx -d "${HOSTNAME}" \
        --non-interactive --agree-tos -m "${EMAIL}" \
        --redirect
    ok "HTTPS certificate issued"
else
    ok "Certificate already present, skipping certbot"
fi

# ─── 11. Start service + smoke test ────────────────────────────
log "Enabling and (re)starting ${SERVICE}..."
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 4
systemctl status "$SERVICE" --no-pager -l | head -15

log "Smoke testing HTTPS endpoint..."
STATUS_JSON=$(curl -sS --max-time 10 "https://${HOSTNAME}/status" || true)
echo "  /status response: $STATUS_JSON"
if echo "$STATUS_JSON" | grep -q '"running":true'; then
    ok "Bot is live and running"
else
    warn "/status did not confirm running. Check: journalctl -u ${SERVICE} -n 50"
fi

# ─── 12. Summary ────────────────────────────────────────────────
cat <<EOF

================================================================
  SCALPING MACHINE — DEPLOYMENT COMPLETE
================================================================
  Webhook URL for TradingView:
      https://${HOSTNAME}/webhook

  Status:
      curl https://${HOSTNAME}/status

  Logs:
      journalctl -u ${SERVICE} -f
      tail -f /var/log/nginx/${SERVICE}.access.log

  Service management:
      systemctl restart ${SERVICE}
      systemctl stop    ${SERVICE}
      systemctl status  ${SERVICE}

  Cert renewal: automatic via certbot.timer
      systemctl list-timers | grep certbot
================================================================
EOF
