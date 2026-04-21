#!/usr/bin/env bash
# pester Update — redeploy after code changes
# Usage: deploy/update.sh <server-ip>
set -euo pipefail

SERVER_IP="${1:?Usage: update.sh <server-ip>}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_rsa}"
REMOTE="pester@${SERVER_IP}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Syncing code ==="
rsync -avz --delete \
    --exclude-from="$REPO_ROOT/deploy/.rsync-excludes" \
    -e "ssh -i $SSH_KEY" \
    "$REPO_ROOT/" "$REMOTE:~/pester/"

echo "=== Rebuilding and restarting ==="
ssh -i "$SSH_KEY" "$REMOTE" << 'EOF'
set -euo pipefail
cd ~/pester
docker compose build
docker compose up -d --force-recreate

# Ensure embedding model is present (idempotent — skips if already cached).
# Without this, vault_search returns "Model not found" until a manual
# `pester model download` is run on the VPS.
docker compose run --rm daemon pester --vault /vault model download

docker compose ps
EOF

echo "=== Done ==="
