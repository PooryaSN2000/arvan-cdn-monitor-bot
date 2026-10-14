<div align="center">

# ArvanCloud CDN Monitor & Telegram Bot 🚀

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Telegram Bot API](https://img.shields.io/badge/Telegram%20Bot%20API-v4.20%2B-0088cc.svg)](https://core.telegram.org/bots/api)
[![ArvanCloud API](https://img.shields.io/badge/ArvanCloud%20API-v4.0-orange.svg)](https://www.arvancloud.ir/api/cdn/4.0)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

**An intelligent monitoring engine, CLI reporter, and Telegram bot for ArvanCloud CDN.**  
Monitors bypass bandwidth, enforces free-tier limits (8 GB/24h), predicts quota exhaustion (ETA), detects traffic spikes, toggles DNS proxy (cloud on/off), and generates rich graphical infographics.

[🇬🇧 English](README.md) • [🇮🇷 نسخه فارسی (Persian)](README.fa.md)

</div>

---

## 🌟 Key Features

### 1. 🧠 Predictive Monitoring & Burn Rate
* **ETA to 8 GB Quota Exhaustion:** Analyzes consumption velocity over the preceding 3 hours to calculate remaining time and estimate the exact hour the free quota will be exceeded (e.g., *ETA: ~4.7 hours (17:40 local time)*).
* **Spike & DDoS Detection:** Automatically triggers an alert if the latest hourly traffic surges beyond $2.8\times$ the preceding baseline average.
* **Overage Cost Estimator:** Calculates the financial cost of billable bandwidth beyond 8 GB based on custom per-GB rates.

### 2. ☁️ DNS Cloud / Proxy Toggle
* Inspect all DNS records (A, AAAA, CNAME) and toggle ArvanCloud's edge proxy (**Cloud On ☁️ / Cloud Off 🔘**) directly from Telegram with a single tap.

### 3. 🎨 Advanced Infographics & Charts
* **Dual-Axis Timeline Plots:** Dual subplots rendering bandwidth (Total, Saved, Bypass in MB) and request counts across 6 periods: `3h`, `6h`, `12h`, `24h`, `7d`, and `30d`.
* **Donut Chart of Domain Share:** Modern donut visualization breaking down each domain's share of total account bypass traffic.
* **Daily Digest Card:** A polished infographic poster summarizing domain progress bars, quota percentages, and total network bandwidth.

### 4. 🔍 Root-Cause & Traffic Analytics
* Pinpoints high-traffic countries and regions, with automatic fallback for Basic plans and IP-level analysis for Enterprise plans.

### 5. 🔒 Strict Multi-Admin Security
* Only authenticated Telegram user IDs listed in `admin_chat_ids` can interact with the bot. Unauthorized users receive an access denied notification containing their Chat ID.
* Manage administrators dynamically via `/addadmin` and `/deladmin`.

### 6. ⚙️ Production-Ready 24/7 Deployment
* Comes with pre-configured **Systemd service** (`arvan-bot.service`) and **Docker Compose** definitions for resilient background execution with auto-restart on boot.

---

## 📦 Project Structure

```
├── arvan_api.py            # Core ArvanCloud API client, analytics engine & Matplotlib renderer
├── arvan_traffic.py        # CLI tool for terminal reporting, ETA, DNS & image exports
├── telegram_bot.py         # Telegram bot daemon with background 30-min scheduled monitor
├── bot_config.example.json # Template configuration file
├── arvan-bot.service       # Systemd unit file for 24/7 Linux background service
├── Dockerfile              # Docker container definition
├── docker-compose.yml      # Docker Compose configuration
├── requirements.txt        # Python package dependencies
├── README.md               # English documentation
└── README.fa.md            # Persian documentation
```

---

## 💻 1. CLI Usage

Run terminal diagnostics and reports without launching the bot:

```bash
# 1. Inspect 24-hour quota status & progress bars across all domains
python3 arvan_traffic.py

# 2. Calculate Burn Rate and estimated time until 8 GB exhaustion (ETA)
python3 arvan_traffic.py --eta

# 3. View DNS records and ArvanCloud proxy status (Cloud On/Off)
python3 arvan_traffic.py --dns

# 4. Generate the Donut Chart of domain traffic distribution
python3 arvan_traffic.py --pie

# 5. Generate the Daily Digest Infographic Card
python3 arvan_traffic.py --digest

# 6. Full metrics table (traffic volume + request counts) across all 6 periods
python3 arvan_traffic.py --detailed

# 7. Target a single specific domain
python3 arvan_traffic.py -d example.com --detailed
```

---

## 🤖 2. Telegram Bot Setup

### Step 1: Create a Bot via @BotFather
1. Message **[@BotFather](https://t.me/BotFather)** on Telegram.
2. Send `/newbot`, choose a display name (e.g., `Arvan CDN Monitor 🚀`), and a username (e.g., `my_arvan_cdn_bot`).
3. Copy the generated API token.

### Step 2: Configure `bot_config.json`
Copy the sample config file to `bot_config.json`:
```bash
cp bot_config.example.json bot_config.json
```

Populate your credentials:
```json
{
  "telegram_bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
  "admin_chat_ids": [123456789],
  "arvan_api_key": "YOUR_ARVAN_API_KEY",
  "limit_gb": 8.0,
  "cost_per_gb_toman": 1500,
  "check_interval_minutes": 30,
  "auto_alert_enabled": true,
  "alert_threshold_percent": 75.0,
  "send_periodic_summary": true,
  "spike_alert_enabled": true
}
```

> **Note:** If you don't know your numeric Telegram Chat ID, start the bot with an empty `admin_chat_ids: []`. Send `/start` to the bot, and it will reply with your exact Chat ID so you can paste it into the configuration.

### Step 3: Start the Bot
```bash
python3 telegram_bot.py
```

---

## 📱 Bot Commands & Keyboards

| Button / Command | Description |
| :--- | :--- |
| **📊 وضعیت سقف ۸ گیگ** (`/status`) | Instant 24h bypass traffic report with graphical progress bars |
| **⏱ پیش‌بینی اتمام سقف** (`/eta`) | Real-time burn rate and estimated time to 8 GB exhaustion |
| **🎨 اینفوگرافیک و نمودارها** (`/charts`) | Interactive menu to generate Timeline, Donut, and Digest cards |
| **☁️ مدیریت ابر DNS** (`/dns`) | Toggle ArvanCloud edge proxy (Cloud On/Off) with 1 click |
| **🔍 منابع و ترافیک منطقه‌ای** (`/sources`) | Geographic regions and top traffic sources breakdown |
| **📑 آمار کامل دوره‌ها** (`/details`) | Comprehensive breakdown across 3h, 6h, 12h, 24h, 7d, 30d |
| **⚙️ وضعیت و تنظیمات** (`/settings`) | View and tweak thresholds, intervals, and cost parameters |
| `/addadmin <chat_id>` | Add a new authorized admin ID dynamically |
| `/deladmin <chat_id>` | Revoke an admin ID |
| `/setlimit <GB>` | Update the threshold quota (default: `8.0` GB) |
| `/interval <min>` | Adjust background monitoring cycle (default: `30` min) |

---

## ⚙️ 3. Deploying 24/7 as a Daemon

Keep the bot running continuously in the background with auto-restart on system boot.

### Option A: Linux Systemd Service (Recommended)
A pre-configured service unit is provided in [arvan-bot.service](arvan-bot.service).

```bash
# 1. Copy service file to systemd directory
sudo cp arvan-bot.service /etc/systemd/system/

# 2. Reload daemon and start service
sudo systemctl daemon-reload
sudo systemctl enable --now arvan-bot

# 3. Check live status and logs
sudo systemctl status arvan-bot
journalctl -u arvan-bot -f
```

### Option B: Docker Compose
```bash
# Build and run container in detached mode
docker compose up -d

# View live container logs
docker compose logs -f
```

---

## 🔒 Security & Privacy Notice
* **Never commit `bot_config.json`:** Your sensitive tokens and API keys are protected in `.gitignore`.
* Always use `bot_config.example.json` as the template for version control.

---

## 📄 License
This project is open-source under the [MIT License](LICENSE).
