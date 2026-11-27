#!/usr/bin/env bash
set -euo pipefail

# Run from the intended production checkout, as its non-root service account.
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$(id -u)" -eq 0 ]; then
    echo "Run as the non-root service account with sudo access." >&2
    exit 1
fi
sudo apt-get update -y
sudo apt-get install -y git python3 python3-venv python3-pip fonts-noto-core ffmpeg
cd "$repo_dir"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
sudo .venv/bin/python -m playwright install-deps chromium
umask 077
if [ ! -f .env ]; then
    cp .env.example .env
fi
chmod 600 .env
mkdir -p state/backups

# Retire the legacy competing cron process before enabling the single worker.
(crontab -l 2>/dev/null | awk -v repo="$repo_dir" '!(index($0, repo) && index($0, "autopilot.py"))'  || true) | crontab -
sudo tee /etc/systemd/system/autopilot-daemon.service >/dev/null <<EOF
[Unit]
Description=Choicematrix single autoposter worker
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$repo_dir
ExecStart=$repo_dir/.venv/bin/python -u autopilot.py --daemon
Restart=on-failure
RestartSec=10
TimeoutStopSec=180
KillSignal=SIGINT
UMask=0077
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
echo "Installed without starting or restarting production."
echo "Configure .env, Git push access, and settings; run .venv/bin/python autopilot.py --preflight --online."
echo "Back up legacy state, stop the old worker, then enable and start autopilot-daemon.service."
