#!/usr/bin/env bash
# pester Deploy — run from your Mac
# Usage: deploy/deploy.sh <server-ip> [vault-config]
#   deploy/deploy.sh 192.168.1.100
#   deploy/deploy.sh 192.168.1.100 ~/my-pester.yaml
set -euo pipefail

SERVER_IP="${1:?Usage: deploy.sh <server-ip> [vault-config]}"
VAULT_CONFIG="${2:-}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_rsa}"
REMOTE="pester@${SERVER_IP}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== [0/7] Pre-flight checks ==="
if [ ! -f "$REPO_ROOT/deploy/.env" ]; then
    echo "ERROR: deploy/.env not found"
    echo "  cp deploy/.env.example deploy/.env"
    echo "  # Fill in your API keys, then re-run deploy.sh"
    exit 1
fi
# Caddy's bearer-token matcher would accept Bearer "" if MCP_BEARER_TOKEN
# expands empty. The MCP container itself refuses to start without a token,
# but we fail-fast here so a misconfigured deploy never reaches the VPS.
if ! grep -qE '^MCP_BEARER_TOKEN=.{20,}' "$REPO_ROOT/deploy/.env"; then
    echo "ERROR: MCP_BEARER_TOKEN missing or shorter than 20 chars in deploy/.env"
    echo "  Generate one: openssl rand -hex 32"
    exit 1
fi
# If MCP_DOMAIN is empty, Caddyfile falls back to mcp.example.com and Let's
# Encrypt tries to provision a cert for a domain you do not control, silently
# breaking HTTPS with no clear error. Fail fast instead.
if ! grep -qE '^MCP_DOMAIN=[^[:space:]]+' "$REPO_ROOT/deploy/.env"; then
    echo "ERROR: MCP_DOMAIN missing or empty in deploy/.env"
    echo "  This must be a domain you control with an A-record pointing to the VPS."
    echo "  Without it, Caddy uses 'mcp.example.com' and TLS provisioning will fail."
    exit 1
fi

echo "=== [1/7] Checking SSH connection ==="
ssh -i "$SSH_KEY" -o ConnectTimeout=5 "$REMOTE" "echo 'Connected'" || {
    echo "ERROR: Cannot SSH to $REMOTE"
    echo "Run: deploy/setup-server.sh first"
    exit 1
}

echo "=== [2/7] Syncing project files ==="
rsync -avz --delete \
    --exclude-from="$REPO_ROOT/deploy/.rsync-excludes" \
    -e "ssh -i $SSH_KEY" \
    "$REPO_ROOT/" "$REMOTE:~/pester/"

echo "=== [3/7] Uploading .env ==="
if [ -f "$REPO_ROOT/deploy/.env" ]; then
    scp -i "$SSH_KEY" "$REPO_ROOT/deploy/.env" "$REMOTE:~/pester/.env"
else
    echo "WARNING: deploy/.env not found. Create it from deploy/.env.example"
    echo "  cp deploy/.env.example deploy/.env"
    echo "  # Fill in your API keys"
    exit 1
fi

echo "=== [4/7] Uploading vault config ==="
if [ -n "$VAULT_CONFIG" ] && [ -f "$VAULT_CONFIG" ]; then
    scp -i "$SSH_KEY" "$VAULT_CONFIG" "$REMOTE:~/pester-vault-config.yaml"
fi

echo "=== [5/7] Uploading Drive credentials ==="
DRIVE_CREDS="$HOME/.pester/credentials"
if [ -d "$DRIVE_CREDS" ]; then
    rsync -avz -e "ssh -i $SSH_KEY" "$DRIVE_CREDS/" "$REMOTE:~/pester/credentials/"
    echo "Drive credentials synced"
else
    echo "SKIP: No Drive credentials found at $DRIVE_CREDS"
    echo "  Run: pester sync drive --setup (on your Mac first)"
fi

echo "=== [6/7] Building and starting containers ==="
ssh -i "$SSH_KEY" "$REMOTE" << 'BUILDEOF'
set -euo pipefail
cd ~/pester

# Build image
docker compose build

# Ensure volumes are writable by the in-container pester user (UID 1000).
# Fresh docker volumes are owned by root by default; chown before any pester command.
docker compose run --rm --user root --no-deps --entrypoint sh daemon \
    -c "chown -R 1000:1000 /vault /home/pester/.pester"

# First deploy: detect by presence of pester.yaml in the vault, not by volume
# existence. A half-initialized volume (e.g., from a failed earlier run) must
# still trigger init so we do not leave an empty vault.
if ! docker compose run --rm --no-deps --entrypoint sh daemon \
        -c "test -f /vault/pester.yaml" >/dev/null 2>&1; then
    echo "First deploy — initializing vault"
    docker compose run --rm daemon pester init /vault
    if [ -f ~/pester-vault-config.yaml ]; then
        docker compose run --rm -v ~/pester-vault-config.yaml:/tmp/pester.yaml:ro \
            daemon sh -c "cp /tmp/pester.yaml /vault/pester.yaml"
    fi
fi

# Download embedding model into volume (idempotent — skips if model present)
docker compose run --rm daemon pester --vault /vault model download

# Start services
docker compose up -d
echo "Services started"
docker compose ps
BUILDEOF

echo "=== [7/7] Setting up Drive sync cron ==="
ssh -i "$SSH_KEY" "$REMOTE" << 'CRONEOF'
CRON_CMD="cd ~/pester && docker compose run --rm daemon pester --vault /vault sync drive"
(crontab -l 2>/dev/null | grep -v "pester.*sync.*drive" || true; \
 echo "*/30 * * * * $CRON_CMD >> /home/pester/drive-sync.log 2>&1") | crontab -
echo "Drive sync cron installed (every 30 min)"
CRONEOF

echo ""
echo "============================================"
echo "  pester deployed to $SERVER_IP"
echo ""
echo "  Check status:  ssh -i $SSH_KEY $REMOTE 'cd ~/pester && docker compose ps'"
echo "  View logs:     ssh -i $SSH_KEY $REMOTE 'cd ~/pester && docker compose logs -f'"
echo "  Run Drive sync: ssh -i $SSH_KEY $REMOTE 'cd ~/pester && docker compose run --rm daemon pester --vault /vault sync drive'"
echo "============================================"
