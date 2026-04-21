#!/usr/bin/env bash
# pester Server Setup — run as root on a fresh Ubuntu 24.04 VPS
# Usage: ssh root@<IP> 'bash -s' < deploy/setup-server.sh
set -euo pipefail

echo "=== [1/6] System update ==="
apt-get update && apt-get upgrade -y
apt-get install -y ufw fail2ban unattended-upgrades curl

echo "=== [2/6] Create service user ==="
if ! id pester &>/dev/null; then
    adduser pester --disabled-password --gecos ""
    mkdir -p /home/pester/.ssh
    cp /root/.ssh/authorized_keys /home/pester/.ssh/
    chown -R pester:pester /home/pester/.ssh
    chmod 700 /home/pester/.ssh && chmod 600 /home/pester/.ssh/authorized_keys
    echo "pester ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker compose *" > /etc/sudoers.d/pester
fi

echo "=== [3/6] Harden SSH ==="
cat > /etc/ssh/sshd_config.d/hardening.conf << 'SSHEOF'
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
AllowUsers pester
X11Forwarding no
SSHEOF
systemctl restart ssh || systemctl restart sshd

echo "=== [4/6] Firewall ==="
# NOTE: Docker manipulates iptables directly and bypasses UFW rules.
# Containers publishing ports (e.g. -p 80:80) are reachable even if UFW
# blocks those ports.  We explicitly allow 80/443 here so that `ufw status`
# reflects the real exposure and to avoid confusion during audits.
# To truly restrict Docker-published ports, use DOCKER_IPTABLES=false or
# bind containers to 127.0.0.1 and proxy via Caddy.
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP (Caddy / Docker)'
ufw allow 443/tcp comment 'HTTPS (Caddy / Docker)'
ufw --force enable

echo "=== [5/6] Fail2ban ==="
cat > /etc/fail2ban/jail.local << 'F2BEOF'
[sshd]
enabled = true
port = 22
maxretry = 3
bantime = 3600
findtime = 600
F2BEOF
systemctl enable fail2ban && systemctl restart fail2ban

echo "=== [6/6] Install Docker ==="
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker pester
fi

echo ""
echo "============================================"
echo "  Server ready. SSH as: ssh pester@$(hostname -I | awk '{print $1}')"
echo "  Next: run deploy/deploy.sh from your machine"
echo "============================================"
