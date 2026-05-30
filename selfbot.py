"""
SELFBOT - Auto Share Game Link

Standalone script. Run separately from main.py:
    python selfbot.py

What it does:
  - Reads the current active place ID from target_place.json
    (same one the monitoring bot is showing in embeds)
  - Falls back to the first ID in stock.json if target_place.json is unavailable
  - Sends "https://www.roblox.com/games/start?placeId={id} sword" to all listed channels
  - Uses Discord user token (SELFBOT_TOKEN) via raw API — no discord.py

Config (in .env):
  SELFBOT_TOKEN      = your Discord user account token
  SHARE_CHANNEL_IDS  = comma-separated channel IDs to send to
  GAME_DISPLAY_NAME  = optional label appended after the link (default: "sword")
  SHARE_MESSAGE      = optional full custom message (overrides default format)
"""

import asyncio
import aiohttp
import json
import os
import sys
import certifi
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
SELFBOT_TOKEN = os.getenv("SELFBOT_TOKEN", "")
SHARE_CHANNEL_IDS_STR = os.getenv("SHARE_CHANNEL_IDS", os.getenv("SHARE_CHANNEL_ID", ""))
SHARE_CHANNEL_IDS = [
    int(cid.strip())
    for cid in SHARE_CHANNEL_IDS_STR.split(",")
    if cid.strip() and cid.strip().isdigit()
]
TARGET_PLACE_FILE = os.getenv("TARGET_PLACE_FILE", "target_place.json")
STOCK_FILE = "stock.json"
GAME_LABEL = os.getenv("GAME_DISPLAY_NAME", "sword")
CUSTOM_MESSAGE = os.getenv("SHARE_MESSAGE", "")

DISCORD_API = "https://discord.com/api/v10"

# SSL fix for Windows Server
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def get_active_place_id() -> str | None:
    """Read current active place ID from target_place.json."""
    try:
        if os.path.exists(TARGET_PLACE_FILE):
            with open(TARGET_PLACE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            place_id = data.get("targetPlaceId")
            if place_id and str(place_id).strip() not in ("0", "", "null"):
                return str(place_id).strip()
    except Exception:
        pass
    return None


def get_first_stock_id() -> str | None:
    """Fallback: read first ID from stock.json."""
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


# ─────────────────────────────────────────────
# Discord API
# ─────────────────────────────────────────────
async def send_message(session: aiohttp.ClientSession, channel_id: int, content: str) -> bool:
    """Send a message to a Discord channel using the user token."""
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    payload = {"content": content}
    headers = {
        "Authorization": SELFBOT_TOKEN,
        "Content-Type": "application/json",
    }
    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                print(f"OK: Sent to channel {channel_id}")
                return True
            elif resp.status == 429:
                data = await resp.json()
                retry_after = data.get("retry_after", 1)
                print(f"RATE LIMITED: Waiting {retry_after}s for channel {channel_id}")
                await asyncio.sleep(retry_after)
                # Retry once
                async with session.post(url, json=payload, headers=headers) as retry:
                    if retry.status == 200:
                        print(f"OK: Sent to channel {channel_id} (after retry)")
                        return True
                    print(f"FAILED: {retry.status} on channel {channel_id}")
                    return False
            else:
                text = await resp.text()
                print(f"FAILED: {resp.status} on channel {channel_id}: {text}")
                return False
    except Exception as e:
        print(f"ERROR: channel {channel_id}: {e}")
        return False


async def verify_token(session: aiohttp.ClientSession) -> bool:
    """Check if the selfbot token is valid."""
    headers = {"Authorization": SELFBOT_TOKEN}
    try:
        async with session.get(f"{DISCORD_API}/users/@me", headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                print(f"OK: Logged in as {data.get('username')}#{data.get('discriminator')} (ID: {data.get('id')})")
                return True
            print(f"ERROR: Token invalid (status {resp.status})")
            return False
    except Exception as e:
        print(f"ERROR: Cannot reach Discord API: {e}")
        return False


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
async def main():
    if not SELFBOT_TOKEN:
        print("ERROR: SELFBOT_TOKEN not set in .env")
        sys.exit(1)

    if not SHARE_CHANNEL_IDS:
        print("ERROR: SHARE_CHANNEL_IDS not set in .env")
        sys.exit(1)

    # Get place ID
    place_id = get_active_place_id()
    if place_id:
        print(f"Using active place ID from target_place.json: {place_id}")
    else:
        place_id = get_first_stock_id()
        if place_id:
            print(f"Using first stock ID from stock.json: {place_id}")
        else:
            print("ERROR: No place ID found in target_place.json or stock.json")
            sys.exit(1)

    message = build_message(place_id)
    print(f"Message: {message}")
    print(f"Sending to {len(SHARE_CHANNEL_IDS)} channel(s)...\n")

    ssl_ctx = __import__("ssl").create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Verify token first
        if not await verify_token(session):
            sys.exit(1)

        # Send to all channels with a small delay between each
        success = 0
        for channel_id in SHARE_CHANNEL_IDS:
            ok = await send_message(session, channel_id, message)
            if ok:
                success += 1
            await asyncio.sleep(1)  # avoid rate limits

    print(f"\nDone: {success}/{len(SHARE_CHANNEL_IDS)} channels sent successfully.")


if __name__ == "__main__":
    asyncio.run(main())
