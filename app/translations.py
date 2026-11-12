"""The AI queue: transcripts waiting to be split into hook, script and CTA.

Used by the AI worker (app/ai.py).
"""

from datetime import timedelta

from sqlalchemy import update
from sqlmodel import Session, select

from app import jobs
from app.models import Competitor, Reel, TranslationIn, User, utcnow

# A claimed transcript that gets no result within this time is handed out again.
CLAIM_TIMEOUT = timedelta(minutes=15)

# What the AI can write the hook, script, CTA and caption in (Settings). By the owner's
# rule, Russian, Belarusian and Polish are never offered.
LANGUAGES = {  # in the order Settings lists them: British English, then Ukrainian, then the rest
    "en": "British English", "uk": "Ukrainian", "es": "Spanish", "fr": "French",
    "de": "German", "it": "Italian", "pt": "Portuguese",
}


def waiting(session: Session, limit: int = 10, lock: bool = False) -> list[tuple[Reel, str]]:
    """Reels with a transcript (or no speech but a caption) and no result, error or recent
    claim, oldest first. With lock, rows another AI worker is claiming right now are skipped.
    """
    stmt = (
        select(Reel, Competitor.username)
        .join(Competitor)
        .where(Reel.transcript.is_not(None), Reel.translated_at.is_(None))
        .where((Reel.transcript != "") | (Reel.caption != ""))
        .where(
            Reel.translation_started_at.is_(None) | (Reel.translation_started_at < utcnow() - CLAIM_TIMEOUT)
        )
        .where(Reel.translation_error.is_(None))
    )
    stmt = stmt.order_by(Reel.transcript_fetched_at, Reel.id).limit(limit)
    if lock:
        stmt = stmt.with_for_update(skip_locked=True, of=Reel)
    return session.exec(stmt).all()


def claim(session: Session, limit: int = 10) -> list[tuple[Reel, str]]:
    """Mark the next batch as taken, so the pipeline shows the AI at work."""
    rows = waiting(session, limit, lock=True)
    for reel, _ in rows:
        reel.translation_started_at = utcnow()
    session.commit()
    for reel, _ in rows:
        jobs.log_ai(session, reel, "debug", f"taken by the AI ({len(rows)} in this batch)")
    return rows


def owners(session: Session, reel_ids: list[int]) -> dict[int, tuple[int | None, str]]:
    """Reel id -> (its user, the language they want); reels from before accounts get English."""
    rows = session.exec(
        select(Reel.id, User.id, User.translate_to)
        .join(Competitor, Reel.competitor_id == Competitor.id)
        .outerjoin(User, Competitor.user_id == User.id)
        .where(Reel.id.in_(reel_ids))
    ).all()
    return {reel_id: (user_id, into if into in LANGUAGES else "en") for reel_id, user_id, into in rows}


def requeue(session: Session, user_id: int) -> int:
    """Send all of a user's transcripts back to the AI (a new language); their own texts stay."""
    mine = select(Competitor.id).where(Competitor.user_id == user_id)
    result = session.exec(
        update(Reel).where(Reel.competitor_id.in_(mine), Reel.transcript.is_not(None))
        .values(translated_at=None, translation_started_at=None, translation_error=None, translated_into=None,
                translation_hook=None, translation_script=None, translation_cta=None, translation_model=None,
                caption_translation=None)
    )
    session.commit()
    return result.rowcount


def start_from_ai(reel: Reel) -> None:
    """Give an untouched version the AI's hook, main script and CTA to edit."""
    if reel.translated_at is not None and not (reel.hook or reel.script or reel.cta):
        reel.hook = reel.translation_hook or ""
        reel.script = reel.translation_script or ""
        reel.cta = reel.translation_cta or ""


def language_code(raw: str) -> str:
    """"en-GB", "EN" or "English" -> "en"; other languages keep their ISO 639-1 code."""
    value = raw.strip().lower()
    return "en" if value.startswith("english") else value.split("-")[0].split("_")[0][:16]


def store(session: Session, reel: Reel, result: TranslationIn, into: str = "en") -> None:
    """Save the AI's result for a reel (or why it failed). ValueError if it is empty."""
    if result.error:
        reel.translation_error = result.error.strip()
        message, kind = f"failed: {reel.translation_error}", "error"
    else:
        if reel.transcript and not (result.hook.strip() or result.main_script.strip()):
            raise ValueError("The result is empty")
        reel.translation_hook = result.hook.strip()
        reel.translation_script = result.main_script.strip()
        reel.translation_cta = result.cta.strip()
        reel.translation_model = result.model.strip() or None
        reel.transcript_language = language_code(result.language) or None
        reel.translated_at = utcnow()
        reel.translated_into = into
        reel.caption_translation = result.caption.strip() or None
        reel.translation_error = None
        if reel.plan_status is not None:
            start_from_ai(reel)  # planned before the AI had finished
        language = reel.transcript_language or "unknown language"
        name = "English" if language == "en" else LANGUAGES.get(language, language)
        how = (f"{name}, kept word for word" if language == into
               else f"{name}, translated into {LANGUAGES.get(into, into)}")
        counts = " / ".join(
            str(len(part.split())) for part in (reel.translation_hook, reel.translation_script, reel.translation_cta)
        )
        message, kind = f"{how}; hook / script / CTA {counts} words", "progress"
    reel.updated_at = utcnow()
    session.commit()
    jobs.log_ai(session, reel, kind, message)
