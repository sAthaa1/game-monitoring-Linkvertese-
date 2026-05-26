"""
ROBLOX MATURITY CHECKER (Python port of debug_maturity.js)
Check if the experience questionnaire maturity has been filled.

Exit codes:
  0 = maturity filled
  1 = maturity missing
  2 = auth error (CSRF)
  3 = transient/unknown error
"""

import sys
import os
import aiohttp
import asyncio
import json
from dotenv import load_dotenv

load_dotenv()

def safe_print(msg: str):
    enc = sys.stdout.encoding or "utf-8"
    safe = msg.encode(enc, errors="replace").decode(enc, errors="replace")
    print(safe)

ROBLOSECURITY = os.getenv("ROBLOSECURITY", "")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


async def get_csrf_token(session: aiohttp.ClientSession) -> str | None:
    """Get CSRF token by hitting the logout endpoint."""
    try:
        headers = {
            "Cookie": f".ROBLOSECURITY={ROBLOSECURITY}",
            "User-Agent": USER_AGENT,
        }
        async with session.post(
            "https://auth.roblox.com/v2/logout",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return resp.headers.get("x-csrf-token")
    except Exception:
        return None


async def check_maturity(universe_id: str) -> int:
    """
    Check maturity status for a given Universe ID.
    Returns exit code: 0 (filled), 1 (missing), 2 (auth error), 3 (transient).
    """
    if not universe_id:
        safe_print("ERROR: No Universe ID provided.")
        return 3

    safe_print(f"\n[checker] Checking Maturity for Universe: {universe_id}...")

    if not ROBLOSECURITY:
        safe_print("[checker] WARNING: ROBLOSECURITY missing. Running in ANONYMOUS mode.")
        safe_print("[checker] Skipping maturity check (Assuming OK based on public link).")
        return 0

    async with aiohttp.ClientSession() as session:
        csrf = await get_csrf_token(session)
        if not csrf:
            safe_print("[checker] WARNING: Gagal mendapatkan CSRF Token atau cookie tidak valid.")
            safe_print("[checker] Falling back to ANONYMOUS mode...")
            safe_print("[checker] Skipping maturity check (Assuming OK).")
            return 0

        headers = {
            "Cookie": f".ROBLOSECURITY={ROBLOSECURITY}",
            "x-csrf-token": csrf,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

        try:
            # 1. Check detailed guidelines (age rating)
            async with session.post(
                "https://apis.roblox.com/experience-guidelines-service/v1beta1/detailed-guidelines",
                headers=headers,
                json={"universeId": int(universe_id)},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    details = data.get("ageRecommendationDetails", {})
                    summary = details.get("ageRecommendationSummary", {})
                    rec = summary.get("ageRecommendation", {})

                    content_maturity = rec.get("contentMaturity", "unrated")
                    display_name = rec.get("displayName", "Unknown")

                    if content_maturity != "unrated" and display_name != "Unknown":
                        short_name = rec.get("displayNameWithHeaderShort", display_name)
                        safe_print(f"[checker] OK Rating: {short_name}")
                        safe_print("[checker] STATUS: MATURITY SUDAH TERISI (Guidelines found)\n")
                        return 0
                else:
                    err_txt = await resp.text()
                    safe_print(f"[checker] Guidelines API status: {resp.status}. Body: {err_txt[:100]}")

            # 2. Check via Games API — contentRatingTypeId is non-zero when filled
            try:
                async with session.get(
                    f"https://games.roblox.com/v1/games?universeIds={universe_id}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    safe_print(f"[checker] Games API: HTTP {resp.status}")
                    if resp.status == 200:
                        gdata = await resp.json(content_type=None)
                        games = gdata.get("data", [])
                        if games:
                            rating_id = games[0].get("contentRatingTypeId", 0)
                            safe_print(f"[checker] contentRatingTypeId={rating_id}")
                            if rating_id and int(rating_id) != 0:
                                safe_print("[checker] STATUS: MATURITY SUDAH TERISI (Games API rating confirmed)")
                                return 0
            except Exception as e:
                safe_print(f"[checker] Games API exception: {e}")

            # 3. Fallback: experience-questionnaire responses
            try:
                url = f"https://apis.roblox.com/experience-questionnaire/v1/responses/{universe_id}"
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    safe_print(f"[checker] Responses API: HTTP {resp.status}")
                    if resp.status == 200:
                        sub_data = await resp.json(content_type=None)
                        # Check if response has answers
                        resp_obj = sub_data.get("response", {})
                        if isinstance(resp_obj, dict) and resp_obj.get("answers"):
                            safe_print("[checker] STATUS: MATURITY SUDAH TERISI (responses confirmed)")
                            return 0
                        else:
                            safe_print("[checker] STATUS: MATURITY BELUM TERISI (no answers in response)")
                            return 1
            except Exception as e:
                safe_print(f"[checker] Responses check exception: {e}")

            safe_print("[checker] STATUS: MATURITY BELUM TERISI (No submission / unrated)")
            return 1

        except Exception as e:
            safe_print(f"[checker] Error: {e}")
            return 3


async def main():
    if len(sys.argv) < 2:
        safe_print("Usage: python maturity_checker.py <UniverseID>")
        sys.exit(3)
        
    uid = sys.argv[1]
    code = await check_maturity(uid)
    sys.exit(code)


if __name__ == "__main__":
    asyncio.run(main())
