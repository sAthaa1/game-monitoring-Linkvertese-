"""
SELFBOT - Auto Share Game Link (Loop Mode)

Standalone script. Run separately from main.py:
    python selfbot.py

What it does:
  - Every 5 minutes, reads the current active place ID
  - Deletes the previous message it sent in each channel
  - Sends a fresh message with the current game link
  - Uses Discord user token (SELFBOT_TOKEN) via raw API

Config (in .env):
  SELFBOT_TOKEN      = your Discord user account token
  SHARE_CHANNEL_IDS  = comma-separated channel IDs to send to
  GAME_DISPLAY_NAME  = label appended after the link (default: "sword")
  SHARE_MESSAGE      = optional full custom message, use {place_id} as placeholder
  SHARE_INTERVAL     = seconds between each share (default: 300 = 5 minutes)
"""

import asyncio
import aiohttp
import json
import os
import sys
import ssl
import certifi
from dotenv import load_dotenv

load_dotenv()

# SSL fix for Windows Server / RDP
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
SELFBOT_TOKEN     = os.getenv("SELFBOT_TOKEN", "")
_ids_str          = os.getenv("SHARE_CHANNEL_IDS", os.getenv("SHARE_CHANNEL_ID", ""))
SHARE_CHANNEL_IDS = [int(c.strip()) for c in _ids_str.split(",") if c.strip().isdigit()]
TARGET_PLACE_FILE = os.getenv("TARGET_PLACE_FILE", "target_place.json")
STOCK_FILE        = "stock.json"
GAME_LABEL        = os.getenv("GAME_DISPLAY_NAME", "sword")
CUSTOM_MESSAGE    = os.getenv("SHARE_MESSAGE", "")
SHARE_INTERVAL    = int(os.getenv("SHARE_INTERVAL", "300"))  # seconds

DISCORD_API = "https://discord.com/api/v10"

# Tracks last sent message ID per channel: {channel_id: message_id}
last_message_ids: dict[int, int] = {}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def get_active_place_id() -> str | None:
    try:
        if os.path.exists(TARGET_PLACE_FILE):
            with open(TARGET_PLACE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            pid = data.get("targetPlaceId")
            if pid and str(pid).strip() not in ("0", "", "null"):
                return str(pid).strip()
    except Exception:
        pass
    return None


def get_first_stock_id() -> str | None:
    try:
        if os.path.exists(STOCK_FILE):
            with open(STOCK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            stock = data.get("stock", [])
            if stock:
                return str(stock[0]).strip()
    except Exception:
        pass
    return None


def build_message(place_id: str) -> str:
    if CUSTOM_MESSAGE:
        return CUSTOM_MESSAGE.replace("{place_id}", place_id)
    return f"https://www.roblox.com/games/start?placeId={place_id} {GAME_LABEL}"


def make_headers() -> dict:
    return {
        "Authorization": SELFBOT_TOKEN,
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────
# Discord API calls
# ─────────────────────────────────────────────
async def verify_token(session: aiohttp.ClientSession) -> bool:
    try:
        async with session.get(f"{DISCORD_API}/users/@me", headers=make_headers()) as r:
            if r.status == 200:
                d = await r.json()
                print(f"OK: Logged in as {d.get('username')} (ID: {d.get('id')})")
                return True
            print(f"ERROR: Token invalid (status {r.status})")
            return False
    except Exception as e:
        print(f"ERROR: Cannot reach Discord: {e}")
        return False


async def delete_message(session: aiohttp.ClientSession, channel_id: int, message_id: int):
    url = f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}"
    try:
        async with session.delete(url, headers=make_headers()) as r:
            if r.status in (200, 204):
                print(f"DELETED: old message in channel {channel_id}")
            elif r.status == 404:
                pass  # already deleted
            else:
                print(f"WARNING: Could not delete message in {channel_id}: {r.status}")
    except Exception as e:
        print(f"WARNING: Delete error in {channel_id}: {e}")


async def send_message(session: aiohttp.ClientSession, channel_id: int, content: str) -> int | None:
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    try:
        async with session.post(url, json={"content": content}, headers=make_headers()) as r:
            if r.status == 200:
                data = await r.json()
                msg_id = int(data["id"])
                print(f"SENT: channel {channel_id} -> message {msg_id}")
                return msg_id
            elif r.status == 429:
                data = await r.json()
                wait = data.get("retry_after", 2)
                print(f"RATE LIMITED: channel {channel_id} — skipping this round (retry_after: {wait:.0f}s)")
                return None
            else:
                print(f"FAILED: {r.status} on channel {channel_id}: {await r.text()}")
    except Exception as e:
        print(f"ERROR: send to {channel_id}: {e}")
    return None


# ─────────────────────────────────────────────
# Share loop
# ─────────────────────────────────────────────
async def share_once(session: aiohttp.ClientSession):
    place_id = get_active_place_id() or get_first_stock_id()
    if not place_id:
        print("WARNING: No place ID found, skipping this round.")
        return

    message = build_message(place_id)
    print(f"\n[SHARE] Place ID: {place_id}")
    print(f"[SHARE] Message: {message}")

    for channel_id in SHARE_CHANNEL_IDS:
        # Delete old message first
        if channel_id in last_message_ids:
            await delete_message(session, channel_id, last_message_ids[channel_id])

        # Send new message
        msg_id = await send_message(session, channel_id, message)
        if msg_id:
            last_message_ids[channel_id] = msg_id

        await asyncio.sleep(3)  # delay between channels to avoid rate limits


async def main():
    if not SELFBOT_TOKEN:
        print("ERROR: SELFBOT_TOKEN not set in .env")
        sys.exit(1)
    if not SHARE_CHANNEL_IDS:
        print("ERROR: SHARE_CHANNEL_IDS not set in .env")
        sys.exit(1)

    print(f"Selfbot starting — sharing every {SHARE_INTERVAL}s to {len(SHARE_CHANNEL_IDS)} channel(s)")
    print(f"Channels: {SHARE_CHANNEL_IDS}")

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        if not await verify_token(session):
            sys.exit(1)

        while True:
            await share_once(session)
            print(f"Next share in {SHARE_INTERVAL}s...")
            await asyncio.sleep(SHARE_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSelfbot stopped.")
