# Self-Hosting Guide (Beta)

Run pester on your own server with Docker Compose.

This Docker Compose deployment is a beta surface for the 1.0.0 soft launch. Use it if you want
remote MCP, daemon automation, or Telegram ingestion on your own server. The stable MCP path
today is local stdio with Claude Code / Claude Desktop.

## Prerequisites

- A VPS running Ubuntu 22.04+ (or any Linux with Docker)
- A domain name pointing to your server (for HTTPS via Caddy)
- SSH access to the server

## Quick Start

```bash
# 1. Set up the server (run from your Mac/laptop)
ssh root@<server-ip> 'bash -s' < deploy/setup-server.sh

# 2. Create your .env file
cp deploy/.env.example deploy/.env
# Edit deploy/.env with your API keys

# 3. Deploy
deploy/deploy.sh <server-ip>
```

## What Gets Deployed

The `docker-compose.yml` runs five services:

All services in this stack should be treated as beta for the 1.0.0 soft launch.

| Service | Purpose |
|---------|---------|
| `caddy` | Reverse proxy with automatic HTTPS and Bearer token auth |
| `mcp` | MCP server for Claude Code integration (remote beta) |
| `daemon` | Background file watcher, scheduler, escalation alerts (beta) |
| `telegram-sync` | Telegram message ingestion (long-polling, beta) |
| `ollama` | Local embedding model for semantic search |

## Environment Variables

Create a `.env` file (see `.env.example`):

```bash
# Required for deploy/deploy.sh (pre-flight checks fail if missing)
MCP_DOMAIN=mcp.your-domain.com  # Real domain, A-record must point to VPS IP
MCP_BEARER_TOKEN=               # 32+ hex chars: openssl rand -hex 32
OPENAI_API_KEY=sk-...           # For LLM extraction and bot

# Required if running the Telegram sync / bot services
TELEGRAM_BOT_TOKEN=123:ABC...

# Optional
GROQ_API_KEY=gsk_...            # For voice transcription (Telegram voice notes)
PESTER_VAULT=/vault             # Vault path inside container (default: /vault)
```

**MCP_DOMAIN must be a real domain you control** with an A-record pointing to the VPS.
Without it, Caddy will try to provision a Let's Encrypt certificate for the Caddyfile
fallback `mcp.example.com` and HTTPS will silently break. `deploy.sh` now fails fast
if either `MCP_DOMAIN` or `MCP_BEARER_TOKEN` is missing.

## Server Setup

`deploy/setup-server.sh` provisions a fresh Ubuntu VPS:

1. System update + security packages (UFW, fail2ban, unattended-upgrades)
2. Creates a `pester` service user with Docker access
3. Hardens SSH (key-only auth, no root login)
4. Configures firewall (ports 22, 80, 443)
5. Installs Docker

Run it as root on a fresh server:
```bash
ssh root@<server-ip> 'bash -s' < deploy/setup-server.sh
```

## Deploy Scripts

### First Deploy

```bash
deploy/deploy.sh <server-ip>
```

This:
1. Syncs project files via rsync
2. Uploads your `.env` file
3. Optionally uploads a vault config (pester.yaml)
4. Optionally syncs Google Drive credentials
5. Builds the Docker image
6. Initializes the vault (first deploy only)
7. Downloads the embedding model
8. Starts all services
9. Sets up a Drive sync cron job (every 30 minutes)

### Code Updates

```bash
deploy/update.sh <server-ip>
```

Syncs code changes and restarts services. Faster than a full deploy.

## Caddy Configuration

The `caddy/Caddyfile` provides:
- Automatic HTTPS via Let's Encrypt
- Bearer token authentication for the MCP endpoint
- Reverse proxy to the MCP service

Set `MCP_DOMAIN` and `MCP_BEARER_TOKEN` in your `.env` file.

## Google Drive Sync

1. Run `pester sync drive --setup` on your local machine to create OAuth credentials
2. Deploy will sync the credentials to the server
3. A cron job runs `pester sync drive` every 30 minutes

## Monitoring

```bash
# SSH to the server
ssh pester@<server-ip>

# Check service status
cd ~/pester && docker compose ps

# View logs
docker compose logs -f                    # All services
docker compose logs -f daemon             # Daemon only
docker compose logs -f telegram-sync      # Bot only

# Run a manual command
docker compose run --rm daemon pester --vault /vault health
docker compose run --rm daemon pester --vault /vault actions --overdue
```

## SSH Key

The deploy scripts default to `~/.ssh/id_rsa`. Override with:
```bash
SSH_KEY=~/.ssh/my-key deploy/deploy.sh <server-ip>
```

## Troubleshooting

**Services won't start:** Check `docker compose logs` for error messages. Most common: missing API keys in `.env`.

**MCP not reachable:** Verify your DNS points to the server. Check that Caddy got a certificate: `docker compose logs caddy`.

**Web Custom Connector:** Not supported yet. The remote MCP endpoint currently uses bearer auth; the web Custom Connector still needs OAuth 2.1 support.

**Drive sync fails:** Run `pester sync drive --setup` locally first. Credentials must exist at `~/.pester/credentials/`.

**Bot not responding:** Check `bot.allowed_users` in your vault's `pester.yaml`. An empty list denies all users (fail-closed). Add your Telegram user ID.
