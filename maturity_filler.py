"""
ROBLOX MATURITY & COMPLIANCE AUTO-FILLER (Browserless API Edition)
Replaces the Playwright version to save 170MB+ disk space.

Uses apis.roblox.com/experience-guidelines-api/v1 to submit answers.
"""

import sys
import os
import asyncio
import aiohttp
import json
from dotenv import load_dotenv

load_dotenv()

ROBLOSECURITY = os.getenv("ROBLOSECURITY", "")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
LOCALE_CODE = os.getenv("MATURITY_LOCALE", "en_us")
DEBUG_DUMP = os.getenv("MATURITY_DEBUG_DUMP", "0").strip() in ("1", "true", "yes")
DEBUG_DUMP_FILE = os.getenv("MATURITY_DEBUG_FILE", "latest_questionnaire_dump.json")
DEBUG_DUMP_ON_ERROR = os.getenv("MATURITY_DEBUG_DUMP_ON_ERROR", "1").strip() in ("1", "true", "yes")
DEFAULT_SCHEMA_FILE = os.getenv("MATURITY_SCHEMA_FILE", "questionnaire_schema_dump.json")
DEFAULT_QUESTIONNAIRE_ID = "a29afc58-9f00-beb4-3daf-e2ee73104428"

# Fallback "No" answers — question IDs verified against questionnaire_schema_dump.json.
# Used only when dynamic schema fetch fails entirely.
ANSWERS_MAP = [
    {"questionId": "d5ef1b27-908d-7f3c-6842-7206aba90d76", "answerId": "4aa6fa96-e1b5-de55-b5d3-6c3fdd5b9752"},   # Violence - No
    {"questionId": "9872fca1-aa7a-6441-f92d-36ad9e927be8", "answerId": "49fa5b39-5ac6-3eac-203b-40d1705b88f9"},   # Blood - No
    {"questionId": "9859f700-a346-c671-20b2-5cc1d30179ca", "answerId": "33106e18-6bec-4707-ec33-7c7c9f9ee08f"},   # Fear - No
    {"questionId": "fcf54dc5-fb73-5c87-ef7d-f7c0bf2f3a85", "answerId": "2085d210-0f8f-d7b1-7246-e1a9a8132a47"},   # Crude Humor - No
    {"questionId": "4991d5e0-983e-1d03-0981-9eed9d1f7dd7", "answerId": "7a5c244a-6b41-4f43-8262-cf554b5853d9"},   # Gambling - No
    {"questionId": "1bd4dc33-1ace-2b47-9b0d-c887d07dde4c", "answerId": "e4d1a4c4-0460-ce97-d19f-c4345f5edf0b"},   # Social Hangout - No
    {"questionId": "c2aa9de2-c2db-132a-3d16-dcba5587921a", "answerId": "91caf5a6-6f36-0fc2-af73-41d2616e70f5"},   # Free-form UGC - No
    {"questionId": "37ce5336-8135-c8df-7ca4-78ccd290df61", "answerId": "514b5a03-5615-74c5-5962-b5b2307cc241"},   # Sensitive Issues - No
    {"questionId": "cb8aa900-cf18-4951-b72d-9ec83d6ec935", "answerId": "cc6b0b2c-1b55-3998-c0af-192d3586eceb"},   # Cross-Experience - No
    {"questionId": "d981b1c7-3b0b-a864-552b-8992f7e051a2", "answerId": "2f3fc68b-7969-c469-f564-3d24a2e72c05"},   # AI Interaction - No
    {"questionId": "78db65d1-cb54-969f-083d-7e6a52c73de2", "answerId": "3d2f845c-23fd-94fa-09e7-1af571b00984"},   # Paid Random Items - No
    {"questionId": "7222bbd3-b9c9-ef2c-1b3f-bbb55b23d57d", "answerId": "5b78446b-f832-2d7e-f6e4-7fb4b6084890"},   # Paid Item Trading - No
    {"questionId": "b01d5384-d43d-3c73-7430-481280381d6b", "answerId": "d8638059-3977-5cd4-a61e-06b6d6a2f6b2"},   # Media Sharing - No
    {"questionId": "7ea1e626-c622-7d05-9ce2-df180651c2af", "answerId": "a29ff5df-4969-f28c-8b94-c6cc4b465881"},   # Continuous Media Feed - No
]

def log(msg: str):
    safe = msg.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace")
    print(f"[maturity] {safe}")


async def _set_archive_status(session: aiohttp.ClientSession, universe_id: str, csrf: str, archived: bool) -> bool:
    """Set the archived status of a universe via develop.roblox.com/v2 interface."""
    url = f"https://develop.roblox.com/v2/universes/{universe_id}/configuration"
    headers = {
        "Cookie": f".ROBLOSECURITY={ROBLOSECURITY}",
        "x-csrf-token": csrf,
        "Content-Type": "application/json"
    }
    payload = {"isArchived": archived}
    try:
        async with session.patch(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("isArchived") == archived
            await _log_http_error(resp, f"Set archive status to {archived} failed")
            return False
    except Exception as e:
        log(f"ERROR: Archive status exception: {e}")
        return False


async def _get_universe_info(session: aiohttp.ClientSession, universe_id: str) -> dict | None:
    """Fetch basic universe info from develop.roblox.com."""
    url = f"https://develop.roblox.com/v1/universes/{universe_id}"
    headers = {"Cookie": f".ROBLOSECURITY={ROBLOSECURITY}"}
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            return None
    except Exception:
        return None


async def _check_rating_status(session: aiohttp.ClientSession, universe_id: str, headers: dict) -> str:
    """Query experience-guidelines-api to see the current rating."""
    url = f"https://apis.roblox.com/experience-guidelines-api/v1/submission-detailed-guidelines/{universe_id}"
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                # Determine rating from results
                return data.get("ageRecommendation", {}).get("displayName", "Unrated")
            return "Unknown"
    except Exception:
        return "Error"


async def _log_http_error(resp: aiohttp.ClientResponse, label: str):
    try:
        body = await resp.text()
    except Exception as e:
        body = f"<failed to read body: {e}>"

    interesting_headers = {}
    for k in ["x-request-id", "x-roblox-id", "x-roblox-trace-id", "roblox-machine-id", "x-cache", "cf-ray", "retry-after", "content-type"]:
        if k in resp.headers:
            interesting_headers[k] = resp.headers.get(k)

    preview = body if len(body) <= 2000 else body[:2000] + "…(truncated)"
    log(f"FAILED: {label} (HTTP {resp.status})")
    if interesting_headers:
        log(f"Response headers: {interesting_headers}")
    if preview.strip():
        log(f"Response body: {preview}")
    else:
        log("Response body: <empty>")


async def _fetch_active_questionnaire_id(
    session: aiohttp.ClientSession,
    universe_id: str,
    headers: dict,
) -> str | None:
    """Fetch the active questionnaireId for this universe."""
    url = f"https://apis.roblox.com/experience-questionnaire/v1/responses/{universe_id}"
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                await _log_http_error(resp, "Fetch active questionnaire info failed")
                return None
            data = await resp.json(content_type=None)
            return data.get("questionnaireId")
    except Exception as e:
        log(f"WARNING: Active questionnaire ID fetch exception: {e}")
        return None


async def _fetch_questionnaire_schema(
    session: aiohttp.ClientSession,
    universe_id: str,
    headers: dict,
    questionnaire_id: str | None = None,
) -> dict | None:
    """Fetch the full questionnaire schema (questions/options) from the API."""
    # Use supplied ID or fetch the active one
    qid = questionnaire_id
    if not qid:
        qid = await _fetch_active_questionnaire_id(session, universe_id, headers)
    if not qid:
        log(f"WARNING: Active questionnaireId not found. Falling back to default ID: {DEFAULT_QUESTIONNAIRE_ID}")
        qid = DEFAULT_QUESTIONNAIRE_ID

    url = f"https://apis.roblox.com/experience-questionnaire/v1/questionnaires/{qid}"
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                await _log_http_error(resp, "Fetch questionnaire schema failed")
                return None
            data = await resp.json(content_type=None)

            if DEBUG_DUMP:
                try:
                    with open(DEBUG_DUMP_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    log(f"Debug dump written: {DEBUG_DUMP_FILE}")
                except Exception as e:
                    log(f"WARNING: Failed to write debug dump: {e}")

            return data
    except Exception as e:
        log(f"WARNING: Questionnaire schema fetch exception: {e}")
        return None


def _sanitize_answers_map(raw_answers: list[dict]) -> list[dict]:
    """Normalize raw answer map to API format (plain UUID strings, no extra encoding)."""
    cleaned: list[dict] = []
    for a in raw_answers:
        qid = a.get("questionId")
        val = a.get("value") or a.get("answerId")
        if not qid or val is None:
            continue
        # Send the option UUID as a plain string — do NOT json.dumps() it,
        # because aiohttp's json= parameter will encode the whole payload once.
        cleaned.append({"questionId": str(qid), "value": str(val).strip()})
    return cleaned


def _pick_no_option(options: list[dict]) -> str | None:
    """Select the 'No' option id from a list of options."""
    def label_of(opt: dict) -> str:
        for k in ("displayText", "text", "label", "name", "title", "valueText"):
            v = opt.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
        return ""

    def id_of(opt: dict) -> str | None:
        for k in ("id", "value", "optionId", "answerId", "uuid"):
            v = opt.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return None

    for opt in options:
        lbl = label_of(opt)
        if lbl in ("no", "tidak", "false") or "no" == lbl.replace(" ", ""):
            return id_of(opt)
    
    for opt in options:
        oid = id_of(opt)
        if oid:
            return oid
    return None


def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _extract_questions_from_schema(schema: dict) -> list[dict]:
    """Extract questions from Guidelines API schema."""
    if not isinstance(schema, dict):
        return []

    questionnaire = schema.get("questionnaire")
    if isinstance(questionnaire, dict):
        sections = questionnaire.get("sections", [])
        if isinstance(sections, list):
            questions = []
            for section in sections:
                qs = section.get("questions", [])
                if isinstance(qs, list):
                    questions.extend(qs)
            if questions:
                return questions

    candidates = []
    for d in _iter_dicts(schema):
        qid = d.get("questionId") or d.get("id")
        opts = d.get("options") or d.get("answerOptions") or d.get("choices")
        if qid and isinstance(opts, list) and opts:
            candidates.append(d)
    return candidates


async def _try_build_dynamic_answers(
    session: aiohttp.ClientSession,
    universe_id: str,
    headers: dict,
    questionnaire_id: str | None = None,
) -> list[dict] | None:
    """Fetch current schema and build 'No' answers dynamically."""
    schema = await _fetch_questionnaire_schema(session, universe_id, headers, questionnaire_id)
    if not schema:
        return None

    if DEFAULT_SCHEMA_FILE:
        try:
            with open(DEFAULT_SCHEMA_FILE, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2, ensure_ascii=False)
            log(f"Schema dump written: {DEFAULT_SCHEMA_FILE}")
        except Exception as e:
            log(f"WARNING: Failed to write schema dump: {e}")

    questions = _extract_questions_from_schema(schema)
    log(f"Questionnaire schema parse: found {len(questions)} question candidates")

    answers = []
    for q in questions:
        qid = q.get("questionId") or q.get("id")
        opts = q.get("options") or q.get("answerOptions") or q.get("choices")
        if not qid or not isinstance(opts, list) or not opts:
            continue
        picked = _pick_no_option(opts)
        if picked:
            # BUG FIX: send the option UUID as a plain string.
            # Do NOT wrap with json.dumps() — aiohttp json= already serializes the payload.
            answers.append({"questionId": str(qid), "value": str(picked)})

    if answers:
        seen = set()
        deduped = []
        for a in answers:
            if a["questionId"] in seen:
                continue
            seen.add(a["questionId"])
            deduped.append(a)
        log(f"Built dynamic answers: {len(deduped)} answers")
        return deduped
    return None


async def fill_maturity(universe_id: str) -> bool:
    """Check, generate answers from schema, and submit questionnaire."""
    if not ROBLOSECURITY:
        log("ERROR: ROBLOSECURITY missing. Cannot fill maturity anonymously.")
        return False

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        csrf = ""
        # Get CSRF token
        headers_csrf = {"Cookie": f".ROBLOSECURITY={ROBLOSECURITY}"}
        async with session.post("https://auth.roblox.com/v2/logout", headers=headers_csrf) as resp:
            csrf = resp.headers.get("x-csrf-token") or ""

        if not csrf:
            log("FAILED: Could not get CSRF token.")
            return False

        headers = {
            "Cookie": f".ROBLOSECURITY={ROBLOSECURITY}",
            "x-csrf-token": csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://create.roblox.com",
            "Referer": f"https://create.roblox.com/dashboard/creations/experiences/{universe_id}/questionnaire",
        }

        # 0. Check Archive Status
        info = await _get_universe_info(session, universe_id)
        originally_archived = False
        if info and info.get("isArchived"):
            log("Universe is ARCHIVED. Unarchiving temporarily...")
            originally_archived = True
            if not await _set_archive_status(session, universe_id, csrf, False):
                log("FAILED: Could not unarchive universe.")
                return False
            await asyncio.sleep(1)

        success_final = False
        try:
            # 1. Fetch the active questionnaireId once — reuse in schema fetch AND payloads.
            questionnaire_id = await _fetch_active_questionnaire_id(session, universe_id, headers)
            if not questionnaire_id:
                log(f"WARNING: No questionnaireId returned; using default: {DEFAULT_QUESTIONNAIRE_ID}")
                questionnaire_id = DEFAULT_QUESTIONNAIRE_ID
            log(f"questionnaireId: {questionnaire_id}")

            # 2. Build answers from schema (pass the already-fetched ID to avoid a second request)
            dynamic_answers = await _try_build_dynamic_answers(
                session, universe_id, headers, questionnaire_id
            )
            answers = dynamic_answers if dynamic_answers else _sanitize_answers_map(ANSWERS_MAP)

            if not answers:
                log("FAILED: No answers generated.")
            else:
                log(f"Payload ready: {len(answers)} answers")

                # 2.5 Save Draft
                # Include questionnaireId so the backend knows which schema version we're answering.
                save_url = f"https://apis.roblox.com/experience-questionnaire/v1/responses/{universe_id}"
                save_payload = {"questionnaireId": questionnaire_id, "answers": answers}
                log("Saving questionnaire draft...")
                async with session.post(save_url, headers=headers, json=save_payload) as resp:
                    if resp.status in (200, 201, 204):
                        log(f"Draft saved (HTTP {resp.status})")
                    else:
                        # Log but continue — some universes return 409/404 here yet still accept submit
                        await _log_http_error(resp, "Draft save (non-fatal)")

                # 3. Submit (Finalize)
                # BUG FIX: The real payload is {questionnaireId, answers} at the top level.
                # NOT {"response": {"answers": ...}} — that wrapper is wrong and causes silent failures.
                log("Submitting questionnaire...")
                submit_url = f"https://apis.roblox.com/experience-questionnaire/v1/responses/{universe_id}/submissions"
                submit_payload = {"questionnaireId": questionnaire_id, "answers": answers}

                async with session.post(submit_url, headers=headers, json=submit_payload) as resp:
                    if resp.status in (200, 201, 204, 409):
                        if resp.status == 409:
                            log("Questionnaire already submitted (409 Conflict).")
                        else:
                            log(f"SUCCESS: Questionnaire submitted (HTTP {resp.status})!")
                        success_final = True
                    else:
                        await _log_http_error(resp, "Submission failed")

                # 4. Verify Rating
                log("Verifying calculated rating...")
                rating = await _check_rating_status(session, universe_id, headers)
                log(f"Current Rating: {rating}")
                if rating not in ("Unrated", "Unknown", "Error"):
                    success_final = True

        finally:
            # Re-archive if necessary
            if originally_archived:
                log("Re-archiving universe...")
                await _set_archive_status(session, universe_id, csrf, True)

        return success_final


async def main():
    uids = sys.argv[1:]
    
    if not uids:
        print("Usage: python maturity_filler.py <universe_id_1> <universe_id_2> ...")
        sys.exit(1)

    results = []
    log(f"START: Bulk processing {len(uids)} universes...")
    
    for uid in uids:
        uid = uid.strip()
        if not uid: continue
        
        success = await fill_maturity(uid)
        results.append((uid, success))
        print("-" * 30)

    # Summary report
    print("\n" + "="*40)
    print("       MATURITY FILLER SUMMARY")
    print("="*40)
    success_count = sum(1 for _, s in results if s)
    fail_count = len(results) - success_count
    
    for uid, success in results:
        status = "[OK] SUCCESS" if success else "[!!] FAILED "
        print(f"[{uid}] {status}")
    
    print("-" * 40)
    print(f"TOTAL: {len(results)} | SUCCESS: {success_count} | FAILED: {fail_count}")
    print("="*40 + "\n")

    sys.exit(0 if fail_count == 0 else 1)

if __name__ == "__main__":
    asyncio.run(main())
