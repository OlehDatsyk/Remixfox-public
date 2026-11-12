"""Fetch jobs: the pipeline behind "Fetch reels", step by step.

1. database     the competitor is saved (or is already there)
2. queue        the job waits its turn (up to three run at once)
3. apify        Apify fetches the latest reels
4. save         reels and covers are stored
5. transcripts  Apify transcribes the reels that have no transcript yet, as many
                as the plan's monthly videos allow (most viewed first)
6. ai           GPT-5.6 Luna (app/ai.py) detects each transcript's language and
                splits it into hook, main script and call to action
                (translating it into the user's language, British English unless
                they chose another in Settings)

Every step writes events (job_events); job_detail() turns them into the steps
and log the activity panel shows live. Jobs run in the background after the
request that created them returns, up to three at once (one per competitor).
"""

import logging
import threading
import time
from collections.abc import Iterator

from sqlmodel import Session, select

from app import apify, auth, plans, storage
from app.config import settings
from app.db import engine
from app.models import (
    ACTIVE_STATUSES,
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    Competitor,
    EventRead,
    FetchJob,
    JobDetail,
    JobEvent,
    Reel,
    StepRead,
    User,
    utcnow,
)

log = logging.getLogger(__name__)

# Up to three fetches run at once, so one user's fetch never waits long for
# another's; each holds a thread and Apify run for about a minute.
PARALLEL_JOBS = 3
_slots = threading.BoundedSemaphore(PARALLEL_JOBS)

STEPS = [
    ("database", "Saved to the database"),
    ("queue", "Queued the fetch job"),
    ("apify", "Apify: fetching the latest reels"),
    ("save", "Saving reels and covers"),
    ("transcripts", "Apify: transcribing reels"),
    ("ai", "AI (GPT-5.6 Luna): language, hook, script and CTA"),
]
# How long a transcript may wait before the panel says the AI may not be set up.
AI_WAIT_HINT_SECONDS = 90


def event(job_id: int, step: str, kind: str, message: str, total: int | None = None) -> None:
    """Write one log line in its own transaction, so it shows at once and survives a failed job."""
    with Session(engine) as session:
        session.add(JobEvent(job_id=job_id, step=step, kind=kind, message=message, total=total))
        session.commit()


def active_job(session: Session, competitor_id: int) -> FetchJob | None:
    return session.exec(
        select(FetchJob)
        .where(FetchJob.competitor_id == competitor_id, FetchJob.status.in_(ACTIVE_STATUSES))
        .order_by(FetchJob.id.desc())
    ).first()


def queue_fetch(session: Session, competitor: Competitor, just_added: bool = False) -> tuple[FetchJob, bool]:
    """Queue a fetch unless one is already waiting or running. Returns (job, is_new)."""
    existing = active_job(session, competitor.id)
    if existing:
        return existing, False
    job = FetchJob(
        competitor_id=competitor.id,
        message=f"Waiting to fetch the latest reels of @{competitor.username}",
    )
    session.add(job)
    plans.record(session, competitor.user_id, "fetch")  # its cost is logged with the reels it brings
    session.commit()
    session.refresh(job)
    if just_added:
        event(job.id, "database", "done", f"Saved @{competitor.username} to the database (competitor #{competitor.id})")
    else:
        event(job.id, "database", "done", f"@{competitor.username} is already in the database (competitor #{competitor.id})")
    waiting = len(session.exec(select(FetchJob.id).where(FetchJob.status == QUEUED)).all()) - 1
    ahead = f"; {waiting} job{'s' if waiting != 1 else ''} ahead of it" if waiting > 0 else ""
    event(job.id, "queue", "start", f"Queued job #{job.id}{ahead}. Up to {PARALLEL_JOBS} jobs run at once.")
    return job, True


def run_fetch_job(job_id: int) -> None:
    with _slots, Session(engine) as session:
        job = session.get(FetchJob, job_id)
        if job is None or job.status != QUEUED:
            return
        competitor = session.get(Competitor, job.competitor_id)
        username = competitor.username
        owner = session.get(User, competitor.user_id) if competitor.user_id else None
        allowed = auth.allowance(session, owner) if owner else None
        videos_left = plans.videos_left(session, owner.id, allowed) if owner else None

        job.status, job.started_at = RUNNING, utcnow()
        job.message = f"Fetching the latest reels of @{username}"
        session.commit()
        waited = (job.started_at - job.created_at).total_seconds()
        event(job_id, "queue", "done", f"Job #{job_id} started after {waited:.0f} s in the queue")

        step = "apify"
        try:
            limit = settings.reels_per_competitor
            event(
                job_id, "apify", "start",
                f"Asking Apify for the latest {limit} reels of @{username} "
                f"(actor {settings.apify_actor_id}, pinned reels skipped). This takes about a minute.",
            )
            started = time.monotonic()
            items = apify.fetch_reels(username, limit)
            reels = [r for r in map(apify.parse_item, items) if r is not None]
            event(job_id, "apify", "debug", f"Apify sent {len(items)} item(s); {len(reels)} are reels")
            if not reels:
                raise apify.ApifyError(apify.error_message(items) or f"No public reels found for @{username}.")
            cost = apify.START_PRICE_USD + len(reels) * apify.REEL_PRICE_USD
            plans.record(session, competitor.user_id, "reels", cost_usd=cost, units=len(reels))
            event(job_id, "apify", "done", f"Apify returned {len(reels)} reels in {time.monotonic() - started:.0f} s (about ${cost:.3f})")
            if allowed is not None and allowed.trial and len(reels) > plans.TRIAL_REELS:
                reels = sorted(reels, key=lambda r: r.views, reverse=True)[:plans.TRIAL_REELS]
                event(job_id, "apify", "debug", f"Free trial: keeping the {plans.TRIAL_REELS} most viewed reels")

            step = "save"
            job.message = f"Saving {len(reels)} reels"
            session.commit()
            new_ids, all_ids = save_reels(session, competitor, reels, job_id)
            competitor.last_fetched_at = utcnow()
            job.reels_saved, job.new_reel_ids = len(reels), new_ids
            session.commit()

            step = "transcripts"
            job.message = "Getting transcripts"
            session.commit()
            job.transcribed_reel_ids = transcribe_missing(
                session, job_id, all_ids, videos_left, owner.id if owner else None
            )
            if job.transcribed_reel_ids:
                from app import ai  # imported here: ai imports this module through translations

                ai.wake()

            job.status = SUCCEEDED
            sent = len(job.transcribed_reel_ids)
            job.message = f"Saved {len(reels)} reels from @{username} ({len(new_ids)} new)" + (
                f"; {sent} transcript(s) sent to the AI" if sent else ""
            )
        except apify.ApifyError as exc:
            session.rollback()
            job.status, job.message = FAILED, str(exc)
            event(job_id, step, "error", str(exc))
        except Exception:
            log.exception("Fetch job %s failed", job_id)
            session.rollback()
            job.status, job.message = FAILED, "Something went wrong while fetching reels."
            event(job_id, step, "error", f"{job.message} See the server log for details.")

        job.finished_at = utcnow()
        session.commit()


def save_reels(
    session: Session, competitor: Competitor, reels: list[apify.ReelData], job_id: int | None = None
) -> tuple[list[int], list[int]]:
    """Insert new reels and refresh the numbers of known ones (keeping the user's texts).

    Returns the ids of the new reels and of all the reels.
    """
    if job_id:
        event(job_id, "save", "start", f"Saving {len(reels)} reels and their covers", total=len(reels))
    new_ids, all_ids = [], []
    for data in reels:
        reel = session.exec(
            select(Reel).where(Reel.competitor_id == competitor.id, Reel.shortcode == data.shortcode)
        ).first()
        is_new = reel is None
        if is_new:
            reel = Reel(competitor_id=competitor.id, shortcode=data.shortcode)
            session.add(reel)
        reel.url = data.url[:500]
        reel.caption = data.caption
        reel.video_url = data.video_url
        reel.views, reel.likes, reel.comments = data.views, data.likes, data.comments
        reel.duration_seconds = data.duration_seconds
        reel.posted_at = data.posted_at
        reel.tags = data.tags
        reel.updated_at = utcnow()
        session.flush()  # gives new reels an id for their cover
        cover = ""
        # A reel's cover never changes, so it is downloaded only once.
        if reel.cover_url is None:
            size = storage.save_cover(session, reel, data.cover_source_urls)
            cover = f", cover {size // 1024} KB" if size else ", cover could not be downloaded"
        session.commit()
        if is_new:
            new_ids.append(reel.id)
        all_ids.append(reel.id)
        if job_id:
            event(
                job_id, "save", "progress",
                f"{data.shortcode}: {'new' if is_new else 'numbers updated'}, "
                f"{_views(data.views)} views, {round(data.duration_seconds or 0)} s{cover}",
            )
    if job_id:
        event(job_id, "save", "done", f"{len(reels)} reels saved: {len(new_ids)} new, {len(reels) - len(new_ids)} updated")
    return new_ids, all_ids


def transcribe_missing(
    session: Session, job_id: int, reel_ids: list[int], videos_left: int | None = None, user_id: int | None = None
) -> list[int]:
    """Get transcripts for these reels that have none yet, at most videos_left of them
    (None: no limit), most viewed first; returns the ids that now have speech."""
    if not settings.auto_transcribe:
        event(job_id, "transcripts", "skip", "Automatic transcripts are off (AUTO_TRANSCRIBE=false)")
        return []
    todo = session.exec(
        select(Reel).where(Reel.id.in_(reel_ids), Reel.transcript.is_(None)).order_by(Reel.views.desc(), Reel.id)
    ).all()
    if not todo:
        event(job_id, "transcripts", "skip", "Every reel already has a transcript, so nothing is paid for")
        return []
    if videos_left is not None and len(todo) > videos_left:
        if videos_left <= 0:
            event(job_id, "transcripts", "skip",
                  f"This month's videos on your plan are used up; new reels get transcripts again from {plans.resets_on()}")
            return []
        event(job_id, "transcripts", "debug",
              f"{len(todo)} reels need a transcript; your plan allows {videos_left} more this month, so the most viewed go first")
        todo = todo[:videos_left]

    cost = apify.transcripts_cost_usd([r.duration_seconds for r in todo])
    runs = list(_chunks(todo, apify.TRANSCRIPT_BATCH_SIZE))
    event(
        job_id, "transcripts", "start",
        f"Asking Apify for {len(todo)} transcript(s) in {len(runs)} run(s), about ${cost:.2f}",
        total=len(todo),
    )
    started = time.monotonic()
    with_speech = []
    for number, chunk in enumerate(runs, 1):
        event(job_id, "transcripts", "debug", f"Run {number} of {len(runs)}: {', '.join(r.shortcode for r in chunk)}")
        try:
            found = apify.fetch_transcripts({r.shortcode: r.duration_seconds for r in chunk})
        except apify.ApifyError as exc:
            event(job_id, "transcripts", "error", f"Transcripts stopped: {exc}. The saved reels are kept.")
            session.commit()
            return with_speech
        for reel in chunk:
            data = found.get(reel.shortcode)
            if data is None:
                event(job_id, "transcripts", "progress", f"{reel.shortcode}: Instagram returned no transcript")
                continue
            reel.transcript = data.transcript or ""
            reel.transcript_fetched_at = utcnow()
            plans.record(session, user_id, "video", cost_usd=apify.transcript_cost_usd(reel.duration_seconds))
            words = len(reel.transcript.split())
            event(job_id, "transcripts", "progress", f"{reel.shortcode}: {f'{words} words' if words else 'no speech'}")
            if words:
                with_speech.append(reel.id)
        session.commit()
    event(
        job_id, "transcripts", "done",
        f"{len(with_speech)} transcript(s) with speech in {time.monotonic() - started:.0f} s; queued for the AI",
    )
    return with_speech


def fail_interrupted_jobs() -> None:
    """Jobs run in this process, so any left active by a restart will never finish."""
    with Session(engine) as session:
        stale = session.exec(select(FetchJob).where(FetchJob.status.in_(ACTIVE_STATUSES))).all()
        stale_ids = [job.id for job in stale]
        for job in stale:
            job.status, job.finished_at = FAILED, utcnow()
            job.message = "Interrupted by a server restart. Fetch again."
        session.commit()
    for job_id in stale_ids:
        event(job_id, "queue", "error", "Interrupted by a server restart. Fetch again.")


# ---------- AI events and the steps the activity panel shows ----------

def log_ai(session: Session, reel: Reel, kind: str, message: str) -> None:
    """Add an AI line to the job that sent this reel's transcript, if there is one."""
    recent = session.exec(
        select(FetchJob).where(FetchJob.competitor_id == reel.competitor_id).order_by(FetchJob.id.desc()).limit(5)
    ).all()
    for job in recent:
        if reel.id in (job.transcribed_reel_ids or []):
            event(job.id, "ai", kind, f"{reel.shortcode}: {message}")
            return


def job_detail(session: Session, job: FetchJob, username: str) -> JobDetail:
    events = session.exec(select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.id)).all()
    steps = [_step(key, title, [e for e in events if e.step == key]) for key, title in STEPS[:-1]]
    steps.append(_ai_step(session, job, [e for e in events if e.step == "ai"]))
    if job.status == FAILED:  # steps after the failure did not happen
        failed_at = next((i for i, s in enumerate(steps) if s.state == "failed"), len(steps))
        for later in steps[failed_at + 1:]:
            later.state = "skipped" if later.state == "pending" else later.state
    in_progress = job.status in ACTIVE_STATUSES or steps[-1].state == "active"
    return JobDetail(
        **job.model_dump(),
        username=username,
        steps=steps,
        events=[EventRead(**e.model_dump()) for e in events],
        in_progress=in_progress,
    )


def _step(key: str, title: str, events: list[JobEvent]) -> StepRead:
    visible = [e for e in events if e.kind != "debug"]
    kinds = {e.kind for e in events}
    state = (
        "failed" if "error" in kinds else
        "done" if "done" in kinds else
        "skipped" if "skip" in kinds else
        "active" if events else "pending"
    )
    start = next((e for e in events if e.kind == "start"), None)
    finish = next((e for e in events if e.kind in ("done", "skip", "error")), None)
    total = start.total if start else None
    return StepRead(
        key=key,
        title=title,
        state=state,
        detail=visible[-1].message if visible else "",
        started_at=events[0].created_at if events else None,
        finished_at=finish.created_at if finish else None,
        done_count=sum(1 for e in events if e.kind == "progress") if total else None,
        total_count=total,
    )


def _ai_step(session: Session, job: FetchJob, events: list[JobEvent]) -> StepRead:
    title = dict(STEPS)["ai"]
    ids = job.transcribed_reel_ids or []
    if job.status != SUCCEEDED or not ids:
        state = "skipped" if job.status == SUCCEEDED or job.status == FAILED else "pending"
        detail = "No new transcripts to process" if job.status == SUCCEEDED else ""
        return StepRead(key="ai", title=title, state=state, detail=detail, started_at=None, finished_at=None)

    reels = session.exec(select(Reel).where(Reel.id.in_(ids))).all()
    done = [r for r in reels if r.translated_at is not None]
    failed = [r for r in reels if r.translated_at is None and r.translation_error]
    working = [r for r in reels if r.translated_at is None and not r.translation_error and r.translation_started_at]
    waiting = len(reels) - len(done) - len(failed) - len(working)
    finished = waiting == 0 and not working

    translated = sum(1 for r in done if (r.transcript_language or "en") != (r.translated_into or "en"))
    if finished:
        detail = f"{len(done)} processed ({translated} translated, {len(done) - translated} kept as is)"
        if failed:
            detail += f"; {len(failed)} failed"
    elif working:
        detail = f"The AI is working on {len(working)} transcript(s); {len(done)} of {len(reels)} done"
    else:
        since = (utcnow() - (job.finished_at or utcnow())).total_seconds()
        detail = (
            f"{waiting} transcript(s) waiting for the AI. Is OPENAI_API_KEY set on the server? (Settings → AI)"
            if since > AI_WAIT_HINT_SECONDS else
            f"{waiting} transcript(s) queued for the AI"
        )
    times = [r.translated_at for r in done]
    return StepRead(
        key="ai",
        title=title,
        state=("failed" if failed and not done else "done") if finished else "active",
        detail=detail,
        started_at=job.finished_at,
        finished_at=max(times) if finished and times else None,
        done_count=len(done) + len(failed),
        total_count=len(reels),
    )


def _chunks(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _views(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}K" if n >= 1e3 else str(n)
