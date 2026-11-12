"""The AI step: GPT-5.6 Luna through the OpenAI API splits transcripts into hook,
main script and call to action.

It detects each transcript's language: a transcript already in the user's
language (British English unless they chose another in Settings) stays word for
word, others are translated into it. Model gpt-5.6-luna at high reasoning effort,
called with OPENAI_API_KEY through the Responses API with a JSON schema
(structured output), so every answer is valid JSON. About $0.0002 a reel; the
tokens of every batch are logged in usage_events (kind "ai") for the admin panel.

A background worker in the web process takes transcripts from the shared queue
(app/translations.py) in batches of 10, three batches side by side, and is woken
at once when new transcripts arrive.
"""

import json
import logging
import re
import threading
from collections import Counter

import httpx
from sqlmodel import Session

from app import translations
from app.config import settings
from app.db import engine
from app import plans
from app.models import Reel, TranslationIn, utcnow

log = logging.getLogger(__name__)

MODEL = "gpt-5.6-luna"
EFFORT = "high"
BATCH_SIZE = 10
PARALLEL_BATCHES = 3  # side by side, so many users' transcripts do not queue for long
POLL_SECONDS = 20
TIMEOUT_SECONDS = 300
API_URL = "https://api.openai.com/v1/responses"
# GPT-5.6 Luna's price per million tokens (reasoning tokens count as output).
INPUT_USD_PER_M = 0.20
OUTPUT_USD_PER_M = 1.20

PROMPT = """\
You are a careful editor. Below, as JSON, are Instagram reels: "transcript" (the \
words the author says; "" when the reel has no speech), "caption" (the text \
posted with it), and "into": the language the reader wants.

For each reel:

1. Detect the language the transcript is in and give its ISO 639-1 code \
("en" for English, "es" for Spanish, "uk" for Ukrainian, and so on).

2. If the transcript is already in the "into" language, keep it exactly as it \
is: do not change a single word, spelling or punctuation mark. Otherwise \
translate the whole transcript into the "into" language, word for word: keep \
every sentence and every point in the same order, as literally as that \
language naturally allows. Into British English, use British spelling \
(colour, organise, centre, programme). Never summarise, shorten, paraphrase, \
add or explain anything. Keep names, brands, products, numbers and any word \
the viewer is asked to type or comment exactly as they are.

3. Split that text (the original, or the translation) into three parts:
   - hook: the opening line or two that grab attention, for example \
"Most people use Claude incorrectly."
   - main_script: the body, everything that is neither the hook nor the call \
to action.
   - cta: the call to action, telling the viewer what to do, for example \
"Write PLUGIN in the comments and I'll send it to you." Use "" if there is none.
   Together the three parts must contain the whole text exactly once. When \
the transcript is "", all three are "".

4. caption: the caption in the "into" language, translated the same careful way \
(or kept exactly as it is when it is already in that language). Keep emoji, \
hashtags, @mentions and links exactly as they are. Use "" if there is no caption.

Reply only with JSON matching the schema, one entry per reel, with the same ids.

Reels:
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reels"],
    "properties": {
        "reels": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "language", "hook", "main_script", "cta", "caption"],
                "properties": {
                    "id": {"type": "integer"},
                    "language": {"type": "string"},
                    "hook": {"type": "string"},
                    "main_script": {"type": "string"},
                    "cta": {"type": "string"},
                    "caption": {"type": "string"},
                },
            },
        }
    },
}

PARTS = ("hook", "main_script", "cta")


class AIError(Exception):
    """The OpenAI API could not do the work (no key, quota, outage...)."""


def configured() -> bool:
    return bool(settings.openai_api_key)


# ---------- Calling the model ----------

CAPTION_CHARS = 2_000  # long captions are cut; the hashtags at the end matter least


def _item(item: tuple) -> tuple[int, str, str, str]:
    """(reel id, transcript[, language code[, caption]]); English and no caption by default."""
    reel_id, transcript, into, caption = (*item, "en", "")[:4]
    return reel_id, transcript or "", into, (caption or "")[:CAPTION_CHARS]


def build_prompt(items: list[tuple]) -> str:
    reels = [{"id": i, "into": translations.LANGUAGES.get(into, into), "transcript": t, "caption": c}
             for i, t, into, c in map(_item, items)]
    return PROMPT + json.dumps(reels, ensure_ascii=False, indent=1)


def cost_usd(usage: dict) -> float:
    return ((usage.get("input_tokens") or 0) * INPUT_USD_PER_M
            + (usage.get("output_tokens") or 0) * OUTPUT_USD_PER_M) / 1_000_000


def run_model(prompt: str, schema: dict | None = None, name: str = "reels") -> dict:
    """One request to the Responses API with a strict JSON schema; returns the JSON reply."""
    if not configured():
        raise AIError("No OpenAI API key (OPENAI_API_KEY) on the server")
    body = {
        "model": MODEL,
        "reasoning": {"effort": EFFORT},
        "input": prompt,
        "text": {"format": {"type": "json_schema", "name": name, "schema": schema or SCHEMA, "strict": True}},
    }
    try:
        response = httpx.post(
            API_URL, json=body, timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )
    except httpx.HTTPError as exc:
        raise AIError(f"OpenAI could not be reached ({type(exc).__name__})") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise AIError(f"OpenAI sent no JSON (HTTP {response.status_code})") from exc
    if response.status_code >= 400:
        # OpenAI's reason, with any part of a key it quotes masked.
        message = re.sub(r"sk-[\w*-]+", "sk-…", str((data.get("error") or {}).get("message") or response.reason_phrase))
        if response.status_code == 401:
            raise AIError(f"OpenAI refused the API key (OPENAI_API_KEY): {message[:300]}")
        raise AIError(f"OpenAI: {message[:300]}")
    if data.get("status") != "completed":
        reason = (data.get("incomplete_details") or {}).get("reason") or data.get("status")
        raise AIError(f"OpenAI stopped before finishing ({reason})")
    text = "".join(
        part.get("text", "")
        for item in data.get("output", []) if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") == "output_text"
    )
    try:
        answer = json.loads(text)
    except ValueError as exc:
        raise AIError("OpenAI did not reply with JSON") from exc
    if isinstance(answer, dict):
        answer["usage"] = data.get("usage") or {}  # tokens, for the admin panel's costs
    return answer


# ---------- Checking each answer ----------

def words(text: str) -> list[str]:
    return re.findall(r"\w+(?:['’]\w+)*", text.lower().replace("’", "'"))


def check(transcript: str, parts: dict, language: str = "", into: str = "en") -> str | None:
    """Why a result cannot be accepted, or None.

    A transcript already in the wanted language must keep exactly the same words
    (in any order, since the call to action may be moved out of the middle). A
    translation must not be much shorter: languages differ a lot in word counts
    (Ukrainian has no articles, so English takes a third more words), but a
    summary loses at least half.
    """
    if not transcript.strip():
        return None  # no speech: only the caption is translated
    if not (parts["hook"] or parts["main_script"]):
        return "The AI returned an empty result."
    split = [w for part in PARTS for w in words(parts[part])]
    original = words(transcript)
    if translations.language_code(language) == into:
        if Counter(split) != Counter(original):
            missing = sum((Counter(original) - Counter(split)).values())
            added = sum((Counter(split) - Counter(original)).values())
            name = translations.LANGUAGES.get(into, into)
            return f"The AI changed the {name} text ({missing} word(s) missing, {added} added)."
        return None
    if original and len(split) < 0.5 * len(original):
        return f"The AI shortened the text ({len(split)} words for {len(original)}), so it was not a full translation."
    return None


def translate_batch(items: list[tuple], run=None) -> tuple[list[tuple[int, TranslationIn]], dict]:
    """([(reel id, result)], token usage) for one batch of (reel id, transcript[, language])."""
    items = [_item(item) for item in items]
    answer = (run or run_model)(build_prompt(items))
    by_id = {}
    for item in answer.get("reels", []) if isinstance(answer, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            by_id[item["id"]] = item
    results = []
    for reel_id, transcript, into, _ in items:
        item = by_id.get(reel_id)
        if item is None:
            results.append((reel_id, TranslationIn(error="The AI returned no result for this reel.")))
            continue
        parts = {part: str(item.get(part) or "").strip() for part in PARTS}
        language = str(item.get("language") or "").strip()
        problem = check(transcript, parts, language, into)
        caption = str(item.get("caption") or "").strip()
        results.append((reel_id, TranslationIn(error=problem) if problem else TranslationIn(
            **parts, language=language, caption=caption, model=f"{MODEL} ({EFFORT}) via OpenAI API",
        )))
    usage = answer.get("usage") if isinstance(answer, dict) else None
    return results, usage if isinstance(usage, dict) else {}


# ---------- Remix in the user's voice ----------

REMIX_PROMPT = """\
You write short vertical videos (Instagram reels) for one person, described below \
under "about_them". Under "original" is a reel that did well for another creator: \
its views, and its hook, main script and call to action (or its transcript, or only \
its caption when it has no speech).

Write a new reel for that person, in {language}:

- Keep what made the original work: its structure and pacing, the kind of hook, and \
the promise it makes to the viewer.
- Make everything else theirs: their niche, audience, examples, offer and tone of \
voice. Do not copy the original's sentences, and leave out names, brands, numbers \
and claims that belong to the original creator.
- Never invent facts about them that "about_them" does not support: no made-up \
results, numbers, clients or credentials. If "about_them" is empty, write for a \
creator in the same niche as the original.
- hook: one or two short lines that stop the scroll. main_script: what they say to \
camera, about as long as the original, in short spoken sentences, no hashtags and \
no directions in brackets. cta: what viewers should do next; use their own call to \
action if "about_them" has one.
{spelling}
Reply only with JSON matching the schema.

"""

REMIX_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(PARTS),
    "properties": {part: {"type": "string"} for part in PARTS},
}


def remix(original: dict, profile: str, into: str = "en", run=None) -> tuple[dict, dict]:
    """A new hook, main script and CTA in the user's voice: (parts, token usage)."""
    language = translations.LANGUAGES.get(into, into)
    spelling = "- Use British spelling (colour, organise, centre, programme).\n" if into == "en" else ""
    prompt = REMIX_PROMPT.format(language=language, spelling=spelling) + json.dumps(
        {"about_them": profile.strip(), "original": original}, ensure_ascii=False, indent=1)
    answer = (run or run_model)(prompt, schema=REMIX_SCHEMA, name="remix")
    parts = {part: str(answer.get(part) or "").strip() for part in PARTS} if isinstance(answer, dict) else {}
    if not (parts.get("hook") or parts.get("main_script")):
        raise AIError("The AI returned an empty remix. Try again.")
    usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
    return parts, usage


# ---------- The worker ----------

_wake = threading.Event()
_worker: dict = {}


def wake() -> None:
    """New transcripts: process them now rather than at the next poll."""
    _wake.set()


def status(session: Session) -> dict:
    return {
        "configured": configured(),
        "model": MODEL,
        "effort": EFFORT,
        "waiting": len(translations.waiting(session, limit=500)),
        "last_run_at": _worker.get("last_run_at"),
        "last_result": _worker.get("last_result", ""),
    }


def process_once() -> int:
    """Claim and process one batch; returns how many reels got a result."""
    if not configured():
        return 0
    with Session(engine) as session:
        rows = translations.claim(session, BATCH_SIZE)
        if not rows:
            return 0
        owners = translations.owners(session, [reel.id for reel, _ in rows])
        try:
            results, usage = translate_batch(
                [(reel.id, reel.transcript, owners.get(reel.id, (None, "en"))[1], reel.caption) for reel, _ in rows]
            )
        except AIError as exc:
            for reel, _ in rows:  # hand them out again on the next try
                reel.translation_started_at = None
            session.commit()
            _worker.update(last_run_at=utcnow().isoformat(), last_result=str(exc))
            raise
        # Each reel's owner gets an equal share of the batch's tokens and cost.
        tokens = int(usage.get("total_tokens") or 0)
        for reel_id, _ in results:
            plans.record(session, owners.get(reel_id, (None, "en"))[0], "ai",
                         cost_usd=cost_usd(usage) / len(results), tokens=tokens // len(results))
        session.commit()
        for reel_id, result in results:
            reel = session.get(Reel, reel_id)
            if reel is not None:
                translations.store(session, reel, result, owners.get(reel_id, (None, "en"))[1])
        _worker.update(last_run_at=utcnow().isoformat(), last_result=f"Processed {len(results)} transcript(s)")
        failed = sum(1 for _, result in results if result.error)
        log.info("AI: %d transcript(s) processed (%d rejected), %d tokens, about $%.4f",
                 len(results), failed, tokens, cost_usd(usage))
        return len(results)


def _loop() -> None:
    while True:
        pause = POLL_SECONDS
        try:
            while process_once():
                pass
        except AIError as exc:
            log.warning("AI: %s", exc)
            pause = 300  # e.g. a quota or an outage: try again in five minutes
        except Exception:
            log.exception("The AI worker failed")
        _wake.wait(pause)
        _wake.clear()


def start_worker() -> None:
    if settings.ai_worker:
        for n in range(PARALLEL_BATCHES):
            threading.Thread(target=_loop, daemon=True, name=f"ai-worker-{n + 1}").start()
