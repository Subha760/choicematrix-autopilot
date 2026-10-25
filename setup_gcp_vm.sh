#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "🚀 Setting up Choicematrix Autopilot on Google Cloud VM"
echo "=========================================================="

# 1. System packages
echo "📦 Updating system packages..."
sudo apt-get update -y
sudo apt-get install -y git python3 python3-venv python3-pip curl fonts-noto-cjk fonts-noto-color-emoji

# 2. Setup directory
REPO_DIR="$HOME/choicematrix-autopilot"
if [ ! -d "$REPO_DIR" ]; then
    echo "📥 Cloning repository..."
    git clone https://github.com/Subha760/choicematrix-autopilot.git "$REPO_DIR"
fi

cd "$REPO_DIR"

# 3. Virtual environment
echo "🐍 Setting up Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install playwright pillow requests python-dotenv

# 4. Playwright Chromium & dependencies
echo "🌐 Installing Playwright Chromium browser & system dependencies..."
playwright install chromium
sudo .venv/bin/playwright install-deps chromium

# 5. Check .env
if [ ! -f ".env" ]; then
    echo "⚠️  No .env file found. Creating empty .env..."
    touch .env
    echo "👉 Please edit $REPO_DIR/.env with your API keys!"
fi

# 6. Setup systemd daemon for Telegram bot
echo "⚙️  Configuring 24/7 systemd background service..."
SERVICE_FILE="/etc/systemd/system/autopilot-daemon.service"
sudo bash -c "cat > $SERVICE_FILE" <<EOF
[Unit]
Description=Choicematrix Telegram Approval Daemon
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/.venv/bin/python -u telegram_bot_daemon.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable autopilot-daemon.service

# 7. Setup cron for 15-minute scheduled runs (Optional fallback to GitHub Actions)
echo "⏰ Adding cron job for local scheduled backup runs..."
(crontab -l 2>/dev/null | grep -v "autopilot.py" || true; echo "*/15 * * * * cd $REPO_DIR && .venv/bin/python autopilot.py >> $HOME/autopilot_cron.log 2>&1") | crontab -

echo ""
echo "=========================================================="
echo "✅ Setup Complete!"
echo "=========================================================="
echo "Next steps:"
echo "1. Paste your .env keys:   nano $REPO_DIR/.env"
echo "2. Start the daemon:       sudo systemctl start autopilot-daemon"
echo "3. Check live status:      sudo systemctl status autopilot-daemon"
echo "4. View live logs:         journalctl -u autopilot-daemon -f"
echo "=========================================================="
