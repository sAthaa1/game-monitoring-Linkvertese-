import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
from datetime import datetime, timezone
import sys
import base64
from dotenv import load_dotenv
import certifi

# Fix SSL certificate verification on Windows Server / RDP environments
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
MONITOR_CHANNEL_IDS_STR = os.getenv("MONITOR_CHANNEL_IDS", os.getenv("MONITOR_CHANNEL_ID", "0"))
MONITOR_CHANNEL_IDS = [int(cid.strip()) for cid in MONITOR_CHANNEL_IDS_STR.split(",") if cid.strip() and cid.strip().isdigit()]
GAME_DISPLAY_NAME = os.getenv("GAME_DISPLAY_NAME", "")
TARGET_PLACE_FILE = os.getenv("TARGET_PLACE_FILE", "target_place.json")
LINKVERTISE_USER_ID = os.getenv("LINKVERTISE_USER_ID", "")
GROUP_URL = os.getenv("GROUP_URL", "")
INSTANT_PLAY_ROLE_IDS_STR = os.getenv("INSTANT_PLAY_ROLE_IDS", os.getenv("INSTANT_PLAY_ROLE_ID", "0"))
INSTANT_PLAY_ROLE_IDS = [int(rid.strip()) for rid in INSTANT_PLAY_ROLE_IDS_STR.split(",") if rid.strip() and rid.strip().isdigit()]
ROBLOSECURITY = os.getenv("ROBLOSECURITY", "")

# Feature toggles
USE_LINKVERTISE = os.getenv("USE_LINKVERTISE", "false").lower() == "true"
SHOW_INSTANT_PLAY = os.getenv("SHOW_INSTANT_PLAY", "false").lower() == "true"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────────
# State
# ─────────────────────────────────────────────
monitored_games: dict[str, dict] = {}
status_message_ids: dict[str, dict[int, int]] = {}
MESSAGES_FILE = "active_messages.json"
BANNED_GAMES_FILE = "banned_games.json"
last_known_place_id: str | None = None
uptime_start: dict[str, datetime] = {}
last_rendered_data: dict[str, str] = {}
_session: aiohttp.ClientSession | None = None
banned_games: set[str] = set()

# Cache universe IDs so we don't re-fetch them every loop tick
_universe_id_cache: dict[str, int] = {}

COLORS = {
    "online": 0x000000,
    "offline": 0x000000,
    "banned": 0x000000,
    "unknown": 0x000000,
}

STATUS_EMOJIS = {
    "online": "",
    "offline": "",
    "banned": "",
    "unknown": "",
}


# ─────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────
def save_active_messages():
    try:
        with open(MESSAGES_FILE, "w") as f:
            json.dump(status_message_ids, f)
    except Exception:
        pass


def load_active_messages():
    global status_message_ids
    if os.path.exists(MESSAGES_FILE):
        try:
            with open(MESSAGES_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # Structure: {gid: {channel_id_str: msg_id}}
                    status_message_ids = {
                        gid: {int(cid): int(mid) for cid, mid in channel_msgs.items()}
                        for gid, channel_msgs in data.items()
                        if isinstance(channel_msgs, dict)
                    }
        except Exception:
            pass


def save_banned_games():
    try:
        with open(BANNED_GAMES_FILE, "w") as f:
            json.dump(list(banned_games), f)
    except Exception:
        pass


def load_banned_games():
    global banned_games
    if os.path.exists(BANNED_GAMES_FILE):
        try:
            with open(BANNED_GAMES_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    banned_games = set(data)
                    print(f"Loaded {len(banned_games)} banned games from previous sessions")
        except Exception:
            pass


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def format_uptime(start: datetime) -> str:
    total_seconds = int((datetime.now(timezone.utc) - start).total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


async def fetch_place_id_from_file() -> str | None:
    try:
        if not os.path.exists(TARGET_PLACE_FILE):
            return None
        with open(TARGET_PLACE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        place_id = data.get("targetPlaceId")
        if place_id is None or str(place_id).strip() in ("0", "", "null"):
            return None
        return str(place_id).strip()
    except Exception as e:
        print(f"WARNING: Error reading {TARGET_PLACE_FILE}: {e}")
        return None


async def get_session():
    global _session
    if _session is None or _session.closed:
        headers = {}
        if ROBLOSECURITY:
            headers["Cookie"] = f".ROBLOSECURITY={ROBLOSECURITY}"
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers=headers,
        )
    return _session


# ─────────────────────────────────────────────
# Roblox API (Public, no auth needed)
# ─────────────────────────────────────────────
async def get_universe_id_from_place(session: aiohttp.ClientSession, place_id: str) -> int | None:
    # Return cached value if available
    if place_id in _universe_id_cache:
        return _universe_id_cache[place_id]
    try:
        url = f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                uid = data.get("universeId")
                if uid:
                    _universe_id_cache[place_id] = uid
                return uid
    except Exception as e:
        print(f"WARNING: [{place_id}] Error getting Universe ID: {e}")
    return None


async def fetch_roblox_server_count(session: aiohttp.ClientSession, universe_id: str) -> tuple[int, int]:
    try:
        url = f"https://games.roblox.com/v1/games/{universe_id}/servers/Public?sortOrder=Asc&limit=100"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                servers = data.get("data", [])
                live_players = sum(s.get("playing", 0) for s in servers)
                return len(servers), live_players
    except Exception:
        pass
    return 0, 0


async def fetch_roblox_status(session: aiohttp.ClientSession, place_id: str) -> dict:
    game_name = monitored_games.get(place_id, {}).get("name", f"Roblox Place {place_id}")

    try:
        # Use multiget-place-details — works for private/17+ games unlike the games API
        url = f"https://games.roblox.com/v1/games/multiget-place-details?placeIds={place_id}"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                if not data:
                    return {"name": game_name, "status": "unknown", "players": 0, "servers": 0}

                place = data[0]
                name = place.get("name", game_name)
                display_name = GAME_DISPLAY_NAME if GAME_DISPLAY_NAME else name

                if not place.get("isPlayable", True):
                    reason = place.get("reasonProhibited", "Unknown")
                    print(f"WARNING: [{place_id}] {name} - {reason}")
                    return {"name": display_name, "status": "offline", "players": 0, "servers": 0}

                # Get player/server count via universe ID
                universe_id = place.get("universeId")
                players = 0
                servers = 0
                if universe_id:
                    servers, players = await fetch_roblox_server_count(session, str(universe_id))
                    if players == 0:
                        # Fallback to cached universe API player count
                        uid_url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
                        async with session.get(uid_url) as gr:
                            if gr.status == 200:
                                gdata = await gr.json()
                                if gdata.get("data"):
                                    players = gdata["data"][0].get("playing", 0)

                return {
                    "name": display_name,
                    "status": "online",
                    "players": players,
                    "servers": servers,
                }

        return {"name": game_name, "status": "unknown", "players": 0, "servers": 0}

    except Exception as e:
        print(f"WARNING: Error fetching place {place_id}: {e}")
        return {"name": game_name, "status": "unknown", "players": 0, "servers": 0}


# ─────────────────────────────────────────────
# Linkvertise
# ─────────────────────────────────────────────
def generate_linkvertise_url(place_id: str) -> str:
    """Generate Linkvertise URL or return direct Roblox launch link based on config."""
    if not USE_LINKVERTISE or not LINKVERTISE_USER_ID:
        return f"https://www.roblox.com/games/start?placeId={place_id}"

    # Use Linkvertise
    roblox_url = f"https://www.roblox.com/games/start?placeId={place_id}"
    encoded_url = base64.b64encode(roblox_url.encode()).decode()
    return f"https://linkvertise.com/{LINKVERTISE_USER_ID}/game/dynamic?r={encoded_url}"


# ─────────────────────────────────────────────
# Embed Builder
# ─────────────────────────────────────────────
def create_status_embed(game_id: str, data: dict) -> discord.Embed:
    status = data.get("status", "unknown").lower()
    name = data.get("name", "Unknown Game")
    players = data.get("players", 0)

    color = COLORS.get(status, 0x000000)
    embed = discord.Embed(title=f"{name}", color=color)

    if status == "banned":
        embed.description = "🚫 **PLACE BANNED / MODERATED**\nThis place is no longer accessible."
    else:
        embed.add_field(
            name="⚠️ Reminder",
            value="If you can't join the game, you need to create a new Roblox account using a VPN.",
            inline=False
        )

        if status == "online" and game_id in uptime_start:
            embed.add_field(name="Uptime", value=format_uptime(uptime_start[game_id]), inline=False)
        elif status == "offline":
            embed.add_field(name="Uptime", value="`Offline`", inline=False)

        if GROUP_URL:
            embed.add_field(name="Group Required", value=f"[Click Here to Join]({GROUP_URL})", inline=False)

    return embed


# ─────────────────────────────────────────────
# Button View
# ─────────────────────────────────────────────
class GameLinkView(discord.ui.View):
    def __init__(self, place_id: str):
        super().__init__(timeout=None)
        self.place_id = place_id

        link_url = generate_linkvertise_url(place_id)
        self.add_item(discord.ui.Button(
            label="Play Game",
            style=discord.ButtonStyle.link,
            url=link_url,
        ))

        # Only add Instant Play button if enabled
        if SHOW_INSTANT_PLAY:
            booster_btn = discord.ui.Button(
                label="Instant Play",
                style=discord.ButtonStyle.success,
                custom_id=f"booster_play_{place_id}",
                emoji="🚀"
            )
            booster_btn.callback = self.booster_callback
            self.add_item(booster_btn)

    async def booster_callback(self, interaction: discord.Interaction):
        member = interaction.user
        has_role = any(role.id in INSTANT_PLAY_ROLE_IDS for role in member.roles)

        if has_role:
            direct_link = f"https://www.roblox.com/games/start?placeId={self.place_id}"
            await interaction.response.send_message(
                content=f"🚀 **Instant Access!**\n<{direct_link}>",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                content="❌ **Access Denied!** You don't have the required role to use this button.",
                ephemeral=True
            )


# ─────────────────────────────────────────────
# Message Manager (prevents duplicate embeds)
# ─────────────────────────────────────────────
async def update_status_message(channels: list, game_id: str, data: dict):
    """Send/edit embed to all channels. Tracks message IDs per channel."""
    embed = create_status_embed(game_id, data)
    view = GameLinkView(game_id)

    if game_id not in status_message_ids:
        status_message_ids[game_id] = {}

    for channel in channels:
        try:
            channel_id = channel.id

            # Try to edit existing message first
            if channel_id in status_message_ids[game_id]:
                try:
                    message = await channel.fetch_message(status_message_ids[game_id][channel_id])
                    await message.edit(content=None, embed=embed, view=view)
                    continue
                except discord.NotFound:
                    del status_message_ids[game_id][channel_id]
                    save_active_messages()

            # Send new message
            message = await channel.send(embed=embed, view=view)
            status_message_ids[game_id][channel_id] = message.id
            save_active_messages()

        except discord.Forbidden:
            pass
        except Exception:
            pass


# ─────────────────────────────────────────────
# Monitor Loop
# ─────────────────────────────────────────────
@tasks.loop(seconds=30)
async def monitor_loop():
    global last_known_place_id

    channels = [bot.get_channel(cid) for cid in MONITOR_CHANNEL_IDS]
    channels = [c for c in channels if c is not None]
    if not channels:
        return

    session = await get_session()
    new_place_id = await fetch_place_id_from_file()

    if new_place_id is None:
        return

    # Detect place ID change
    if new_place_id != last_known_place_id:
        old_id = last_known_place_id

        # Clean up old game from all channels
        if old_id and old_id in monitored_games:
            if old_id in status_message_ids:
                for channel in channels:
                    try:
                        channel_msgs = status_message_ids[old_id]
                        if channel.id in channel_msgs:
                            old_msg = await channel.fetch_message(channel_msgs[channel.id])
                            await old_msg.delete()
                    except Exception:
                        pass
                del status_message_ids[old_id]
                save_active_messages()
            del monitored_games[old_id]
            uptime_start.pop(old_id, None)
            last_rendered_data.pop(old_id, None)
            _universe_id_cache.pop(old_id, None)
            print(f"SWITCH: {old_id} -> {new_place_id}")

        # Register new game (skip if previously banned)
        if new_place_id in banned_games:
            print(f"WARNING: Place {new_place_id} was previously banned, skipping")
            last_known_place_id = None
        else:
            monitored_games[new_place_id] = {
                "name": f"Roblox Place {new_place_id}",
                "status": "unknown",
                "players": 0,
            }
            last_known_place_id = new_place_id

    # Fetch and update
    for game_id in list(monitored_games.keys()):
        if game_id in banned_games:
            continue

        data = await fetch_roblox_status(session, game_id)
        curr_status = data.get("status", "unknown")

        # Skip unknown status (network errors) - don't treat as ban
        if curr_status == "unknown":
            continue

        # Uptime tracking
        if curr_status == "online":
            if game_id not in uptime_start:
                uptime_start[game_id] = datetime.now(timezone.utc)
                print(f"ONLINE: {game_id} | Players: {data.get('players', 0)}")
        else:
            if game_id in uptime_start:
                uptime_start.pop(game_id, None)

        monitored_games[game_id].update(data)

        # Build signature to detect meaningful changes
        uptime_ticks = 0
        if game_id in uptime_start:
            delta = datetime.now(timezone.utc) - uptime_start[game_id]
            uptime_ticks = int(delta.total_seconds() // 60)  # tick every minute

        data_signature = f"{curr_status}|{data.get('players', 0)}|{data.get('servers', 0)}|{uptime_ticks}"

        # Only update Discord if data actually changed
        if last_rendered_data.get(game_id) != data_signature:
            await update_status_message(channels, game_id, monitored_games[game_id])
            last_rendered_data[game_id] = data_signature
            print(f"UPDATE: [{game_id}] {curr_status} | Players: {data.get('players', 0)}")

        await asyncio.sleep(0.5)


@monitor_loop.before_loop
async def before_monitor():
    await bot.wait_until_ready()


# ─────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────
@bot.command(name="syncnow")
@commands.has_permissions(administrator=True)
async def syncnow(ctx):
    """Force reload targetPlaceId."""
    place_id = await fetch_place_id_from_file()
    if place_id:
        await ctx.send(f"✅ Active Place ID: `{place_id}`")
    else:
        await ctx.send(f"❌ Cannot read `{TARGET_PLACE_FILE}`")


@bot.command(name="status")
async def status_cmd(ctx):
    """Show current monitoring status."""
    if not monitored_games:
        await ctx.send("No games being monitored.")
        return

    lines = []
    for gid, info in monitored_games.items():
        uptime_str = format_uptime(uptime_start[gid]) if gid in uptime_start else "N/A"
        lines.append(f"**{info['name']}** | `{gid}` | Players: {info.get('players', 0)} | Uptime: {uptime_str}")

    embed = discord.Embed(title="📊 Monitor Status", description="\n".join(lines), color=0x5865F2)
    await ctx.send(embed=embed)


@bot.command(name="skip")
@commands.has_permissions(administrator=True)
async def skip_cmd(ctx):
    """Skip current stock ID and move to next one."""
    # Only works in one of the monitor channels
    if ctx.channel.id not in MONITOR_CHANNEL_IDS:
        return

    from stock_manager import load_stock, save_stock

    stock = load_stock()
    if not stock:
        await ctx.send("❌ Stock is empty, nothing to skip.")
        return

    skipped_id = stock.pop(0)
    success = save_stock(stock)

    if not success:
        # Restore the ID if save failed
        stock.insert(0, skipped_id)
        await ctx.send(f"❌ Failed to save stock. Skip cancelled. Run: `python stock_manager.py recover`")
        return

    # Clean up current embed
    if skipped_id in status_message_ids:
        try:
            msg = await ctx.channel.fetch_message(status_message_ids[skipped_id])
            await msg.delete()
        except Exception as e:
            print(f"WARNING: Failed to delete message for {skipped_id}: {e}")
        del status_message_ids[skipped_id]
        save_active_messages()

    # Clear monitoring state for this game
    monitored_games.pop(skipped_id, None)
    uptime_start.pop(skipped_id, None)
    last_rendered_data.pop(skipped_id, None)

    # Reset place ID tracking to force re-sync
    global last_known_place_id
    last_known_place_id = None

    # Wait a bit to ensure old embed is fully deleted before monitor loop picks up new ID
    await asyncio.sleep(1)

    # Write next place ID to file so orchestrator picks it up
    remaining = len(stock)
    if stock:
        try:
            with open(TARGET_PLACE_FILE, "w", encoding="utf-8") as f:
                json.dump({"targetPlaceId": int(stock[0])}, f, indent=2)
            print(f"SKIP: Skipped {skipped_id}, next -> {stock[0]}")
            await ctx.send(f"⏭️ Skipped `{skipped_id}`. Next: `{stock[0]}` ({remaining} left)")
        except Exception as e:
            print(f"ERROR: Failed to write next place ID: {e}")
            await ctx.send(f"❌ Error writing next place ID: {e}")
    else:
        try:
            with open(TARGET_PLACE_FILE, "w", encoding="utf-8") as f:
                json.dump({"targetPlaceId": 0}, f, indent=2)
        except Exception:
            pass
        print(f"SKIP: Skipped {skipped_id}, stock now empty")
        await ctx.send(f"⏭️ Skipped `{skipped_id}`. Stock is now empty.")


@bot.command(name="clearmonitor")
@commands.has_permissions(administrator=True)
async def clearmonitor(ctx):
    """Delete all monitoring messages and reset state."""
    global last_known_place_id

    channels = [bot.get_channel(cid) for cid in MONITOR_CHANNEL_IDS]
    channels = [c for c in channels if c is not None]
    if not channels:
        return

    count = 0
    errors = 0
    for gid, channel_msgs in list(status_message_ids.items()):
        for channel in channels:
            try:
                if channel.id in channel_msgs:
                    msg = await channel.fetch_message(channel_msgs[channel.id])
                    await msg.delete()
                    count += 1
            except discord.NotFound:
                pass
            except Exception as e:
                print(f"WARNING: Failed to delete message: {e}")
                errors += 1

    status_message_ids.clear()
    save_active_messages()
    monitored_games.clear()
    uptime_start.clear()
    last_rendered_data.clear()
    last_known_place_id = None

    print(f"CLEAR: Deleted {count} messages, {errors} errors")
    await ctx.send(f"✅ Cleared {count} messages and reset state.")


# ─────────────────────────────────────────────
# Error Handler
# ─────────────────────────────────────────────
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ No permission.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"[ERROR] {error}")


# ─────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"OK: Bot online as {bot.user} (ID: {bot.user.id})")
    print(f"Channels: {MONITOR_CHANNEL_IDS} | File: {TARGET_PLACE_FILE}")

    valid_channels = [bot.get_channel(cid) for cid in MONITOR_CHANNEL_IDS if bot.get_channel(cid)]
    print(f"Valid channels: {len(valid_channels)}/{len(MONITOR_CHANNEL_IDS)}")

    # Load banned games from previous sessions
    load_banned_games()

    # Clean up messages from previous session
    load_active_messages()
    if status_message_ids:
        print(f"Cleaning {len(status_message_ids)} old messages...")
        channels = [bot.get_channel(cid) for cid in MONITOR_CHANNEL_IDS]
        channels = [c for c in channels if c is not None]
        for gid, channel_msgs in list(status_message_ids.items()):
            for channel in channels:
                try:
                    if channel.id in channel_msgs:
                        msg = await channel.fetch_message(channel_msgs[channel.id])
                        await msg.delete()
                except Exception:
                    pass
        status_message_ids.clear()
        save_active_messages()

    if not monitor_loop.is_running():
        monitor_loop.start()


# Cleanup on shutdown
_original_close = bot.close


async def _cleanup_close():
    channels = [bot.get_channel(cid) for cid in MONITOR_CHANNEL_IDS]
    channels = [c for c in channels if c is not None]
    for gid, channel_msgs in list(status_message_ids.items()):
        for channel in channels:
            try:
                if channel.id in channel_msgs:
                    msg = await channel.fetch_message(channel_msgs[channel.id])
                    await msg.delete()
            except Exception:
                pass
    status_message_ids.clear()
    save_active_messages()
    await _original_close()


bot.close = _cleanup_close


# ─────────────────────────────────────────────
# Disconnect / Resume handlers
# ─────────────────────────────────────────────
@bot.event
async def on_disconnect():
    print("WARNING: Bot disconnected from Discord. Attempting to reconnect...")


@bot.event
async def on_resumed():
    print("OK: Bot reconnected and session resumed.")
    if not monitor_loop.is_running():
        monitor_loop.start()


# ─────────────────────────────────────────────
# Standalone Run
# ─────────────────────────────────────────────
async def run_cleanup():
    """One-time cleanup of active messages."""
    load_active_messages()
    if not status_message_ids:
        print("No active messages to clean.")
        return
    try:
        await bot.login(TOKEN)
        for channel_id in MONITOR_CHANNEL_IDS:
            channel = await bot.fetch_channel(channel_id)
            if channel:
                for gid, channel_msgs in list(status_message_ids.items()):
                    try:
                        if channel.id in channel_msgs:
                            msg = await channel.fetch_message(channel_msgs[channel.id])
                            await msg.delete()
                    except Exception:
                        pass
        status_message_ids.clear()
        save_active_messages()
    finally:
        await bot.close()


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN not set in .env!")
    if not MONITOR_CHANNEL_IDS or MONITOR_CHANNEL_IDS == [0]:
        raise ValueError("MONITOR_CHANNEL_IDS not set in .env!")

    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        asyncio.run(run_cleanup())
    else:
        bot.run(TOKEN, reconnect=True)
