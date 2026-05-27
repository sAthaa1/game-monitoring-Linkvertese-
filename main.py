"""
ROBLOX UPLOADER - UNIFIED ENTRY POINT

Flow:
  1. Pick first stock ID
  2. Check maturity -> fill if needed
  3. Write active Place ID locally
  4. Start Discord monitoring bot (sends embed automatically)
  5. Health check loop -- if banned/maturity lost:
     a. Delete Discord embed
     b. Remove ONLY the failed stock ID
     c. Loop back to step 1 with next stock ID

Usage:
  python main.py           # Run full orchestrator
  python main.py monitor   # Run monitoring bot only
  python main.py cleanup   # Cleanup Discord messages only
"""

import asyncio
import signal
import sys
import os
import json
import aiohttp
from dotenv import load_dotenv

load_dotenv()

from maturity_checker import check_maturity
from maturity_filler import fill_maturity
from stock_manager import load_stock, save_stock

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "10"))  # seconds - changed to 10s for faster detection
PLAYABILITY_CHECK_TIMEOUT = 10  # seconds for playability checks
TARGET_PLACE_FILE = os.getenv("TARGET_PLACE_FILE", "target_place.json")


# ---------------------------------------------
# Helpers
# ---------------------------------------------
def set_active_place_id(place_id: str) -> bool:
    """Write the active Place ID locally so the monitoring bot can read it."""
    try:
        with open(TARGET_PLACE_FILE, "w", encoding="utf-8") as f:
            json.dump({"targetPlaceId": int(place_id)}, f, indent=2)
        return True
    except OSError as e:
        if e.errno == 28:
            print(f"SKIP: Disk full — cannot write place ID {place_id}.")
            return False
        print(f"FAILED: Failed to write {TARGET_PLACE_FILE}: {e}")
        return False
    except Exception as e:
        print(f"FAILED: Failed to write {TARGET_PLACE_FILE}: {e}")
        return False


def remove_from_stock(place_id: str, mem_stock: list[str] | None = None) -> list[str]:
    """
    Remove a specific place ID from stock.
    If mem_stock is provided, removes from that list directly (avoids disk reload).
    Always attempts to persist to disk, but continues in memory if disk write fails.
    Returns the updated stock list.
    """
    from stock_manager import load_stock, save_stock

    # Use provided in-memory list, or load from disk as fallback
    stock = mem_stock if mem_stock is not None else load_stock()

    if place_id in stock:
        stock = [x for x in stock if x != place_id]
        success = save_stock(stock)
        if success:
            print(f"STOCK: Removed {place_id}. Remaining: {len(stock)} IDs")
        else:
            print(f"WARNING: Could not persist stock removal for {place_id} (disk full?). Continuing in memory.")
    return stock


# ---------------------------------------------
# Roblox Public API Checks
# ---------------------------------------------
async def is_place_openable(place_id: str) -> tuple[bool, int | None]:
    """
    Check if a place is playable using public Roblox APIs only.
    Returns (is_playable, universe_id).
    Timeout: 10 seconds total for faster detection.
    """
    universe_id = None

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=PLAYABILITY_CHECK_TIMEOUT)) as session:
            # Step 1: Place ID -> Universe ID
            url = f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        universe_id = data.get("universeId")
            except asyncio.TimeoutError:
                print(f"WARNING: [{place_id}] Timeout resolving Universe ID")
                return False, None

            if not universe_id:
                print(f"WARNING: [{place_id}] Cannot resolve Universe ID")
                return False, None

            # Step 2: Check via Games API
            url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        games = data.get("data", [])
                        if games:
                            g = games[0]
                            is_playable = g.get("isPlayable", True)

                            if is_playable:
                                print(f"OK: Place {place_id} (Universe {universe_id}) is playable.")
                                return True, universe_id

                            reason = g.get("reasonProhibited", "Unknown")
                            print(f"BANNED: Place {place_id} is NOT playable. Reason: {reason}")
                            return False, universe_id
            except asyncio.TimeoutError:
                print(f"WARNING: [{place_id}] Timeout checking Games API")
                return False, universe_id

            # Step 3: Fallback - public page check
            public_url = f"https://www.roblox.com/games/start?placeId={place_id}/"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            try:
                async with session.get(public_url, headers=headers, allow_redirects=True) as resp:
                    if resp.status == 200:
                        final_url = str(resp.url)
                        if "roblox.com/games/" in final_url and "/request-error" not in final_url:
                            return True, universe_id
            except asyncio.TimeoutError:
                print(f"WARNING: [{place_id}] Timeout checking public page")

            return False, universe_id

    except Exception as e:
        print(f"EXCEPTION: Playability check failed for {place_id}: {e}")
        return False, universe_id


# ---------------------------------------------
# Discord Embed Cleanup (uses the RUNNING bot)
# ---------------------------------------------
async def cleanup_discord_messages():
    """Delete all active Discord embed messages via the running bot instance."""
    try:
        import monitoring
        channels = [monitoring.bot.get_channel(cid) for cid in monitoring.MONITOR_CHANNEL_IDS]
        channels = [c for c in channels if c is not None]
        if channels:
            for gid, channel_msgs in list(monitoring.status_message_ids.items()):
                for channel in channels:
                    try:
                        if channel.id in channel_msgs:
                            msg = await channel.fetch_message(channel_msgs[channel.id])
                            await msg.delete()
                    except Exception:
                        pass
            monitoring.status_message_ids.clear()
            monitoring.save_active_messages()
            monitoring.monitored_games.clear()
            monitoring.last_known_place_id = None
            monitoring.uptime_start.clear()
            monitoring.last_rendered_data.clear()
            print("OK: Discord messages cleaned up.")
    except Exception as e:
        pass


# ---------------------------------------------
# Maturity Helpers
# ---------------------------------------------
async def check_maturity_safe(universe_id: str, max_retries: int = 3) -> int:
    """Returns: 0 (filled), 1 (missing), -1 (persistent failure)"""
    for attempt in range(max_retries):
        exit_code = await check_maturity(universe_id)
        if exit_code == 0:
            return 0
        if exit_code == 1:
            return 1
        print(f"WARNING: Transient error (Code {exit_code}). Retry {attempt+1}/{max_retries}...")
        await asyncio.sleep(10)
    return -1


async def ensure_maturity(universe_id: str) -> bool:
    """Check maturity, fill if missing. Returns True if OK."""
    print(f"Verifying maturity for Universe {universe_id}...")
    status = await check_maturity_safe(universe_id)

    if status == 0:
        print("OK: Maturity already filled.")
        return True

    if status == -1:
        print("FAILED: Persistent error checking maturity.")
        return False

    # status == 1 -> missing, fill it
    print("WARNING: Maturity missing. Filling...")
    fill_ok = await fill_maturity(universe_id)

    if not fill_ok:
        print("FAILED: Maturity fill rejected.")
        return False

    print("OK: Submission accepted. Waiting 20s for propagation...")
    await asyncio.sleep(20)

    verify = await check_maturity_safe(universe_id, max_retries=2)
    if verify == 0:
        print("OK: Maturity confirmed!")
        return True

    # Trust the API even if verification is slow
    print("WARNING: Not confirmed yet, but API accepted. Proceeding.")
    return True


# ---------------------------------------------
# Main Orchestrator
# ---------------------------------------------
async def run_orchestrator():
    """
    Main orchestrator loop:
      1. Pick first stock ID
      2. Write it to target_place.json
      3. Start Discord bot in background
      4. Health-check loop -- if banned/unplayable:
         a. Cleanup Discord embed
         b. Remove failed ID from stock
         c. Loop back to step 1 with next ID
    """
    print("Orchestrator Started")
    print(f"Check interval: {CHECK_INTERVAL}s\n")

    import monitoring

    if not monitoring.TOKEN:
        print("ERROR: DISCORD_TOKEN not found in .env!")
        return
    if not monitoring.MONITOR_CHANNEL_IDS or monitoring.MONITOR_CHANNEL_IDS == [0]:
        print("ERROR: MONITOR_CHANNEL_IDS not set in .env!")
        return

    # Event that gets set once the bot fires on_ready
    bot_ready_event = asyncio.Event()

    original_on_ready = monitoring.bot.extra_events.get("on_ready", [])

    @monitoring.bot.listen("on_ready")
    async def _orchestrator_ready_listener():
        bot_ready_event.set()

    # Start the bot in the background
    bot_task = asyncio.create_task(monitoring.bot.start(monitoring.TOKEN, reconnect=True))
    print("Starting monitoring bot...")

    # Wait for on_ready to fire (with a 60s timeout)
    try:
        await asyncio.wait_for(bot_ready_event.wait(), timeout=60)
    except asyncio.TimeoutError:
        print("ERROR: Bot did not become ready within 60s. Check your DISCORD_TOKEN.")
        bot_task.cancel()
        return
    print("Bot is ready. Starting orchestrator health-check loop.")

    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3  # skip after this many network errors in a row
    # Work from an in-memory list so disk-full errors don't cause infinite loops
    mem_stock: list[str] = []
    _disk_reload_attempts = 0

    while True:
        # Reload from disk only when our in-memory list is empty
        if not mem_stock:
            fresh = load_stock()
            # Only use disk data if it's different from what we already exhausted
            if fresh:
                mem_stock = fresh
                _disk_reload_attempts = 0
            else:
                _disk_reload_attempts += 1
                wait = min(60 * _disk_reload_attempts, 300)
                print(f"STOCK: Empty. Waiting {wait}s before rechecking...")
                await asyncio.sleep(wait)
                continue

        current_id = mem_stock[0]
        print(f"\nORCHESTRATOR: Using Place ID {current_id} ({len(mem_stock)} in stock)")

        # Write active place ID so the monitoring bot picks it up
        ok = set_active_place_id(current_id)
        if not ok:
            # Disk full or write error — skip this ID in memory and wait before retrying
            print(f"ORCHESTRATOR: Cannot write place ID {current_id}, skipping to next.")
            mem_stock = remove_from_stock(current_id, mem_stock)
            if not mem_stock:
                print("ORCHESTRATOR: All IDs exhausted due to disk full. Waiting 120s for disk space to free up...")
                await asyncio.sleep(120)
                mem_stock = load_stock()  # try reloading after wait
            else:
                await asyncio.sleep(5)
            continue

        # Give the monitoring bot time to pick up the new ID and post embed
        await asyncio.sleep(CHECK_INTERVAL * 2)

        # Health-check loop for this place ID
        while True:
            # Check if stock changed externally (e.g. !skip command)
            disk_stock = load_stock()
            if disk_stock and disk_stock[0] != current_id:
                print(f"ORCHESTRATOR: Place ID changed externally, re-syncing.")
                mem_stock = disk_stock
                break

            playable, universe_id = await is_place_openable(current_id)

            if playable:
                consecutive_failures = 0
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            # Not playable -- check if it's a network error or a real ban
            if universe_id is None:
                consecutive_failures += 1
                print(f"ORCHESTRATOR: [{current_id}] Network error ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                if consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue
                else:
                    print(f"ORCHESTRATOR: [{current_id}] Too many network errors, skipping.")
                    consecutive_failures = 0
            else:
                print(f"ORCHESTRATOR: [{current_id}] Place is banned/unplayable. Switching...")
                consecutive_failures = 0

            # Cleanup Discord embed for this game
            await cleanup_discord_messages()

            # Remove failed ID from stock (returns updated list)
            mem_stock = remove_from_stock(current_id, mem_stock)

            # Small delay before picking next ID
            await asyncio.sleep(3)
            break  # back to outer loop to pick next stock ID


# ---------------------------------------------
# Bot Runners
# ---------------------------------------------
async def run_monitoring_bot():
    """Run the Discord monitoring bot."""
    import monitoring

    if not monitoring.TOKEN:
        print("ERROR: DISCORD_TOKEN not found in .env!")
        return
    if not monitoring.MONITOR_CHANNEL_IDS or monitoring.MONITOR_CHANNEL_IDS == [0]:
        print("ERROR: MONITOR_CHANNEL_IDS not set in .env!")
        return

    print(f"DEBUG: Starting bot with token: {monitoring.TOKEN[:20]}...")
    print(f"DEBUG: Channels: {monitoring.MONITOR_CHANNEL_IDS}")

    try:
        print("DEBUG: Calling bot.start()...")
        await monitoring.bot.start(monitoring.TOKEN)
    except asyncio.CancelledError:
        print("DEBUG: Bot cancelled")
        await monitoring.bot.close()
    except Exception as e:
        print(f"ERROR: Monitoring bot error: {e}")
        import traceback
        traceback.print_exc()


async def run_monitoring_cleanup():
    """Run one-time cleanup of active Discord messages."""
    import monitoring
    await monitoring.run_cleanup()


# ---------------------------------------------
# Entry Point
# ---------------------------------------------
async def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "monitor":
            await run_monitoring_bot()
        elif cmd == "cleanup":
            await run_monitoring_cleanup()
        else:
            print(f"Unknown command: {cmd}")
            print("Usage:")
            print("  python main.py           # Full orchestrator")
            print("  python main.py monitor   # Monitoring bot only")
            print("  python main.py cleanup   # Cleanup messages")
    else:
        await run_orchestrator()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGoodbye!")
