# 🎮 Roblox Game Monitoring Bot

A Discord bot that monitors Roblox game availability and automatically switches to the next game in stock when one gets banned or taken down. Supports Linkvertise links, instant play roles, and multi-channel embeds.

---

## ✨ Features

- **Auto-switching** — detects when a game is banned/unplayable and moves to the next stock ID automatically
- **Live embeds** — updates Discord embed in real-time with player count, server count, and uptime
- **Linkvertise support** — optionally wrap game links through Linkvertise for monetization
- **Instant Play button** — role-gated direct play button for boosters/VIPs
- **Multi-channel** — post the same embed to multiple Discord channels at once
- **Stock management** — add/remove/list Place IDs via CLI
- **Auto-recovery** — stock file is backed up before every write, recoverable if corrupted

---

## 📁 File Structure

```
deploy/
├── main.py              # Entry point & orchestrator loop
├── monitoring.py        # Discord bot (embeds, commands, monitor loop)
├── stock_manager.py     # Stock CRUD with backup/recovery
├── maturity_checker.py  # Checks Roblox maturity rating
├── maturity_filler.py   # Fills missing maturity rating
├── stock.json           # List of Roblox Place IDs to cycle through
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
└── .gitignore
```

---

## ⚙️ Setup

### 1. Clone the repo

```bash
git clone https://github.com/sAthaa1/game-monitoring-Linkvertese-
cd game-monitoring-Linkvertese-
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Your Discord bot token |
| `MONITOR_CHANNEL_IDS` | ✅ | Comma-separated channel IDs to post embeds |
| `ROBLOSECURITY` | ✅ | Roblox cookie for maturity checks |
| `GAME_DISPLAY_NAME` | ❌ | Override the game name shown in embed |
| `USE_LINKVERTISE` | ❌ | `true` to wrap links with Linkvertise |
| `LINKVERTISE_USER_ID` | ❌ | Your Linkvertise user ID |
| `SHOW_INSTANT_PLAY` | ❌ | `true` to show Instant Play button |
| `INSTANT_PLAY_ROLE_IDS` | ❌ | Comma-separated role IDs for Instant Play |
| `GROUP_URL` | ❌ | Roblox group URL shown in embed |
| `CHECK_INTERVAL` | ❌ | Health check interval in seconds (default: `10`) |

### 4. Add Place IDs to stock

```bash
python stock_manager.py add 1234567890
python stock_manager.py add 9876543210
python stock_manager.py list
```

### 5. Run the bot

```bash
python main.py
```

---

## 🤖 Discord Commands

All commands require **Administrator** permission unless noted.

| Command | Description |
|---|---|
| `!status` | Show currently monitored games (anyone) |
| `!syncnow` | Force reload the active Place ID from file |
| `!skip` | Skip current stock ID and move to the next one |
| `!clearmonitor` | Delete all monitoring embeds and reset state |

---

## 🔄 How It Works

```
Start
  │
  ▼
Pick first Place ID from stock.json
  │
  ▼
Write to target_place.json
  │
  ▼
Discord bot posts embed ──────────────────────────────┐
  │                                                   │
  ▼                                                   │ (updates every 10s)
Health check loop                                     │
  │                                                   │
  ├─ Game is playable ──► wait CHECK_INTERVAL ────────┘
  │
  └─ Game is banned/down
        │
        ▼
      Delete Discord embed
        │
        ▼
      Remove Place ID from stock
        │
        ▼
      Pick next Place ID ──► repeat
```

---

## 🛠️ Stock Management CLI

```bash
python stock_manager.py add <place_id>      # Add a Place ID
python stock_manager.py remove <place_id>   # Remove a Place ID
python stock_manager.py list                # List all Place IDs
python stock_manager.py info                # Show stock info + backup status
python stock_manager.py recover             # Recover from backup if corrupted
```

---

## 🚀 Running Modes

```bash
python main.py            # Full orchestrator (recommended)
python main.py monitor    # Run monitoring bot only (no auto-switching)
python main.py cleanup    # Delete all active Discord embeds and exit
```

---

## 📋 Requirements

- Python 3.10+
- A Discord bot with **Message Content Intent** enabled
- Bot invited to your server with `Send Messages`, `Embed Links`, `Read Message History` permissions

---

## ⚠️ Notes

- **Never commit your `.env` file** — it contains sensitive tokens
- The `target_place.json`, `active_messages.json`, and `banned_games.json` files are generated at runtime and excluded from git
- Stock backups are stored in `.stock_backups/` (last 5 kept automatically)
