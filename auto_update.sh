#!/bin/bash
set -euo pipefail

# Directory where the bot is cloned
APP_DIR="/opt/arvan-cdn-monitor-bot"
cd "$APP_DIR"

# Ensure git safe directory
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

# Fetch updates from GitHub
git fetch origin main --quiet 2>/dev/null || exit 0

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

# If remote commit differs from local commit, update and restart
if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🔄 New update detected ($LOCAL -> $REMOTE). Pulling changes..."
    git reset --hard origin/main
    
    # Update python dependencies if requirements.txt changed
    if [ -f "requirements.txt" ]; then
        "$APP_DIR/venv/bin/pip" install -r requirements.txt --quiet
    fi
    
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🔁 Restarting arvan-cdn-bot.service..."
    systemctl restart arvan-cdn-bot.service
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ arvan-cdn-bot updated successfully to commit $REMOTE!"
fi
