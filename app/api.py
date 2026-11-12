"""REST API under /api. Interactive docs: /api/docs.

Every endpoint works on the signed-in user's own data (see app/auth.py).
"""

import csv
import io
import math
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import ai, apify, billing, jobs, passwords, plans, storage, transcripts, translations
from app.auth import (
    CurrentUser,
    me,
    OwnerUser,
    PayingUser,
    SessionDep,
    StaffUser,
    allowance,
    is_owner,
    label,
    normalise_email,
    role,
)
from app.config import settings
from app.handles import parse_handle, profile_url
from app.models import (
    ACTIVE_STATUSES,
    ACTIVE_SUBSCRIPTIONS,
    PLAN_STATUSES,
    Admin,
    AdminIn,
    Competitor,
    CompetitorCreate,
    CompetitorRead,
    FetchJob,
    JobDetail,
    JobRead,
    Me,
    MeUpdate,
    PasswordSet,
    RemixOut,
    Reel,
    ReelRead,
    ReelUpdate,
    UsageEvent,
    User,
    utcnow,
)

router = APIRouter(prefix="/api")


# ---------- Helpers ----------

def job_read(job: FetchJob, username: str) -> JobRead:
    return JobRead(**job.model_dump(), username=username)


def translation_status(reel: Reel) -> str:
    if reel.translated_at is not None:
        return "done"  # for a reel without speech: its caption is translated
    if reel.transcript is None or not (reel.transcript or reel.caption):
        return "none"  # no transcript yet, or nothing to translate
    return "failed" if reel.translation_error else "pending"


def reel_read(reel: Reel, username: str) -> ReelRead:
    cost = apify.transcript_cost_usd(reel.duration_seconds)
    status_ = translation_status(reel)
    return ReelRead(
        **reel.model_dump(),
        username=username,
        transcript_cost_usd=cost,
        translation_status=status_,
        translated=status_ == "done" and (reel.transcript_language or "en") != (reel.translated_into or "en"),
    )


def set_plan_status(reel: Reel, plan_status: str) -> None:
    if reel.plan_status is None:
        reel.planned_at = utcnow()
        translations.start_from_ai(reel)
    if reel.plan_status != plan_status:
        reel.plan_status, reel.plan_status_at = plan_status, utcnow()


def competitor_read(session: Session, competitor: Competitor) -> CompetitorRead:
    reel_count = session.exec(
        select(func.count()).select_from(Reel).where(Reel.competitor_id == competitor.id)
    ).one()
    latest = session.exec(
        select(FetchJob).where(FetchJob.competitor_id == competitor.id).order_by(FetchJob.id.desc())
    ).first()
    return CompetitorRead(
        **competitor.model_dump(),
        profile_url=profile_url(competitor.username),
        reel_count=reel_count,
        latest_job=job_read(latest, competitor.username) if latest else None,
    )


def get_competitor(session: Session, competitor_id: int, user: User) -> Competitor:
    competitor = session.get(Competitor, competitor_id)
    if competitor is None or competitor.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Competitor not found")
    return competitor


def get_reel(session: Session, reel_id: int, user: User) -> tuple[Reel, str]:
    row = session.exec(
        select(Reel, Competitor.username).join(Competitor)
        .where(Reel.id == reel_id, Competitor.user_id == user.id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reel not found")
    return row


def hours_until_refresh(competitor: Competitor, user: User) -> float | None:
    """Each competitor can be fetched once a day (the owner any time): hours left, or None."""
    if is_owner(user) or competitor.last_fetched_at is None:
        return None
    left = (competitor.last_fetched_at + timedelta(hours=plans.REFRESH_HOURS) - utcnow()).total_seconds() / 3600
    return left if left > 0 else None


def check_daily_fetches(session: Session, user: User) -> None:
    """402 when the trial is over or the month's reels are used; 429 when the day's fetches are."""
    allowed = allowance(session, user)
    over = plans.trial_over(session, user, allowed)
    if over:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            (f"Your {plans.TRIAL_DAYS}-day free trial has ended" if over == "ended" else "Your free trial is used")
            + ". Choose a plan to keep fetching reels.",
        )
    left = plans.reels_left(session, user.id, allowed)
    if left is not None and left < settings.reels_per_competitor:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"This month's {allowed.reels:,} reels on your {allowed.label} plan are used up. "
            f"They start again on {plans.resets_on()}, or choose a bigger plan.",
        )
    wait = plans.fetches_wait(session, user.id, allowed)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Your {allowed.label} plan fetches reels up to {plans.daily_fetches(allowed)} times a day "
            f"(once per competitor, plus {plans.SPARE_FETCHES} spare), and today's are used. "
            f"Try again in about {max(1, math.ceil(wait))} hour(s).",
        )


def start_fetch(
    session: Session, background: BackgroundTasks, competitor: Competitor, just_added: bool = False
) -> FetchJob:
    job, is_new = jobs.queue_fetch(session, competitor, just_added)
    if is_new:
        background.add_task(jobs.run_fetch_job, job.id)
    return job


# ---------- Health ----------

@router.get("/health")
def health(session: SessionDep, response: Response) -> dict[str, str]:
    try:
        session.exec(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if database == "ok" else "error",
        "database": database,
        "apify": "connected" if apify.is_configured() else "not connected",
    }


# ---------- Competitors ----------

@router.get("/competitors")
def list_competitors(session: SessionDep, user: CurrentUser) -> list[CompetitorRead]:
    competitors = session.exec(
        select(Competitor).where(Competitor.user_id == user.id).order_by(Competitor.created_at.desc())
    ).all()
    return [competitor_read(session, c) for c in competitors]


@router.post("/competitors", status_code=status.HTTP_201_CREATED)
def add_competitor(
    payload: CompetitorCreate, session: SessionDep, background: BackgroundTasks, user: PayingUser
) -> CompetitorRead:
    """Save the account and start fetching its latest reels."""
    username = parse_handle(payload.handle)
    if username is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter an Instagram username or profile link"
        )
    if session.exec(
        select(Competitor).where(Competitor.user_id == user.id, Competitor.username == username)
    ).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"@{username} is already in your competitors")
    allowed = allowance(session, user)
    if allowed.competitors is not None and plans.competitors_used(session, user.id) >= allowed.competitors:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Your {allowed.label} plan tracks up to {allowed.competitors} competitors. "
            "Choose a bigger plan in Settings to add more.",
        )
    check_daily_fetches(session, user)

    competitor = Competitor(username=username, user_id=user.id)
    session.add(competitor)
    try:
        session.commit()
    except IntegrityError:  # added twice at the same moment
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"@{username} is already in your competitors")
    session.refresh(competitor)

    start_fetch(session, background, competitor, just_added=True)
    return competitor_read(session, competitor)


@router.delete("/competitors/{competitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_competitor(competitor_id: int, session: SessionDep, user: CurrentUser) -> None:
    """Remove the account together with its reels, texts, covers and jobs."""
    session.delete(get_competitor(session, competitor_id, user))
    session.commit()


@router.post("/competitors/{competitor_id}/fetch", status_code=status.HTTP_202_ACCEPTED)
def fetch_competitor(
    competitor_id: int, session: SessionDep, background: BackgroundTasks, user: PayingUser
) -> JobRead:
    competitor = get_competitor(session, competitor_id, user)
    running = jobs.active_job(session, competitor.id)
    wait = None if running else hours_until_refresh(competitor, user)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"@{competitor.username} was fetched less than {plans.REFRESH_HOURS} hours ago, so its reels are fresh. "
            f"You can fetch it again in about {math.ceil(wait)} hour(s).",
        )
    if not running:
        check_daily_fetches(session, user)
    return job_read(start_fetch(session, background, competitor), competitor.username)


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh_all(session: SessionDep, background: BackgroundTasks, user: PayingUser) -> list[JobRead]:
    """Fetch the latest reels of every competitor not fetched in the last day."""
    competitors = session.exec(select(Competitor).where(Competitor.user_id == user.id)).all()
    running = [c for c in competitors if jobs.active_job(session, c.id)]
    due = [c for c in competitors if c not in running and not hours_until_refresh(c, user)]
    if competitors and not (running or due):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Every competitor was fetched in the last {plans.REFRESH_HOURS} hours, so your reels are fresh.",
        )
    allowed = allowance(session, user)
    left = plans.fetches_left(session, user.id, allowed)
    reels_left = plans.reels_left(session, user.id, allowed)
    if reels_left is not None:  # each fetch brings about 20 reels
        left = min(left if left is not None else len(due), reels_left // settings.reels_per_competitor)
    if due and plans.trial_over(session, user, allowed):
        check_daily_fetches(session, user)  # explains the trial
    if left is not None:
        if due and not left and not running:
            check_daily_fetches(session, user)
        due = due[:left]
    return [job_read(start_fetch(session, background, c), c.username) for c in running + due]


# ---------- Jobs ----------

@router.get("/jobs")
def list_jobs(
    session: SessionDep,
    user: CurrentUser,
    active: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[JobRead]:
    stmt = select(FetchJob, Competitor.username).join(Competitor).where(Competitor.user_id == user.id)
    if active:
        stmt = stmt.where(FetchJob.status.in_(ACTIVE_STATUSES))
    rows = session.exec(stmt.order_by(FetchJob.id.desc()).limit(limit)).all()
    return [job_read(job, username) for job, username in rows]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: SessionDep, user: CurrentUser) -> JobDetail:
    """A job with its pipeline steps and full log (the activity panel polls this).

    The owner and admins can open anyone's job, from the overview in Settings.
    """
    stmt = select(FetchJob, Competitor.username).join(Competitor).where(FetchJob.id == job_id)
    if role(session, user) == "user":
        stmt = stmt.where(Competitor.user_id == user.id)
    row = session.exec(stmt).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return jobs.job_detail(session, *row)


# ---------- Reels ----------

@router.get("/reels")
def list_reels(
    session: SessionDep,
    user: CurrentUser,
    sort: Literal["latest", "views"] = "latest",
    competitor_id: Annotated[list[int] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 20,
) -> list[ReelRead]:
    """The user's reels; with competitor_id (repeatable), only those accounts' reels."""
    stmt = select(Reel, Competitor.username).join(Competitor).where(Competitor.user_id == user.id)
    if competitor_id:
        stmt = stmt.where(Reel.competitor_id.in_(competitor_id))
    if sort == "views":
        stmt = stmt.order_by(Reel.views.desc(), Reel.id.desc())
    else:
        stmt = stmt.order_by(Reel.posted_at.desc().nulls_last(), Reel.id.desc())
    return [reel_read(reel, username) for reel, username in session.exec(stmt.limit(limit)).all()]


@router.get("/reels/{reel_id}")
def read_reel(reel_id: int, session: SessionDep, user: CurrentUser) -> ReelRead:
    return reel_read(*get_reel(session, reel_id, user))


@router.patch("/reels/{reel_id}")
def update_reel(reel_id: int, payload: ReelUpdate, session: SessionDep, user: CurrentUser) -> ReelRead:
    """Save the user's hook, script and call to action, and/or its content plan status."""
    reel, username = get_reel(session, reel_id, user)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    plan_status = changes.pop("plan_status", None)
    if plan_status:
        set_plan_status(reel, plan_status)
    if changes:
        for key, value in changes.items():
            setattr(reel, key, value.strip())
        reel.edited_at = utcnow()
    if changes or plan_status:
        reel.updated_at = utcnow()
        session.commit()
        session.refresh(reel)
    return reel_read(reel, username)


# ---------- Content plan ----------

@router.get("/plan")
def content_plan(session: SessionDep, user: CurrentUser) -> list[ReelRead]:
    """Reels in the content plan: to do and ready first, posted last; newest additions first."""
    rows = session.exec(
        select(Reel, Competitor.username)
        .join(Competitor)
        .where(Reel.plan_status.is_not(None), Competitor.user_id == user.id)
        .order_by((Reel.plan_status == "posted"), Reel.planned_at.desc(), Reel.id.desc())
    ).all()
    return [reel_read(reel, username) for reel, username in rows]


@router.post("/reels/{reel_id}/plan")
def add_to_plan(reel_id: int, session: SessionDep, user: CurrentUser) -> ReelRead:
    """Put the reel in the content plan (❌ to do), starting the user's version from the AI's text."""
    reel, username = get_reel(session, reel_id, user)
    if reel.plan_status is None:
        set_plan_status(reel, PLAN_STATUSES[0])
        reel.updated_at = utcnow()
        session.commit()
        session.refresh(reel)
    return reel_read(reel, username)


@router.delete("/reels/{reel_id}/plan")
def remove_from_plan(reel_id: int, session: SessionDep, user: CurrentUser) -> ReelRead:
    """Take the reel out of the content plan; the user's text is kept."""
    reel, username = get_reel(session, reel_id, user)
    reel.plan_status = reel.planned_at = reel.plan_status_at = None
    reel.updated_at = utcnow()
    session.commit()
    session.refresh(reel)
    return reel_read(reel, username)


@router.post("/reels/{reel_id}/skip")
def skip_reel(reel_id: int, session: SessionDep, user: CurrentUser) -> ReelRead:
    """Skip the idea on the Home page's swipe deck."""
    reel, username = get_reel(session, reel_id, user)
    reel.skipped_at = reel.skipped_at or utcnow()
    session.commit()
    session.refresh(reel)
    return reel_read(reel, username)


@router.delete("/reels/{reel_id}/skip")
def unskip_reel(reel_id: int, session: SessionDep, user: CurrentUser) -> ReelRead:
    """Undo a skip."""
    reel, username = get_reel(session, reel_id, user)
    reel.skipped_at = None
    session.commit()
    session.refresh(reel)
    return reel_read(reel, username)


@router.delete("/reels/{reel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reel(reel_id: int, session: SessionDep, user: CurrentUser) -> None:
    """Remove one reel with its texts and cover."""
    reel, _ = get_reel(session, reel_id, user)
    session.delete(reel)
    session.commit()


@router.post("/reels/{reel_id}/transcript")
def fetch_transcript(reel_id: int, session: SessionDep, user: PayingUser) -> ReelRead:
    """Get what the author says in the reel.

    Paid per minute of audio (see transcript_cost_usd), so it is fetched only on
    request, stored, and never fetched twice. Takes about 30 seconds.
    """
    reel, username = get_reel(session, reel_id, user)
    allowed = allowance(session, user)
    if reel.transcript is None and plans.videos_left(session, user.id, allowed) == 0:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"This month's {allowed.videos} videos on your {allowed.label} plan are used up. "
            f"They start again on {plans.resets_on()}, or choose a bigger plan in Settings.",
        )
    paid = reel.transcript is None
    try:
        transcripts.transcribe(session, reel)
    except transcripts.AlreadyFetching:
        raise HTTPException(status.HTTP_409_CONFLICT, "This transcript is already being fetched")
    except apify.ApifyError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    if paid:
        plans.record(session, user.id, "video", cost_usd=apify.transcript_cost_usd(reel.duration_seconds))
        session.commit()
    ai.wake()
    return reel_read(reel, username)


# ---------- Exports ----------

def _title(reel: Reel) -> str:
    """A short heading: your hook, else the AI's, else the caption's first line."""
    title = (reel.hook or reel.translation_hook or (reel.caption or "").split("\n")[0] or reel.shortcode).strip()
    return title if len(title) <= 80 else title[:79].rstrip() + "…"


@router.get("/export/reels.csv", responses={200: {"content": {"text/csv": {}}}})
def export_csv(session: SessionDep, user: CurrentUser) -> Response:
    """Every reel with its numbers, your version and the AI's, for Excel or Google Sheets (Pro and Studio)."""
    allowed = allowance(session, user)
    if not (allowed and allowed.exports):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "CSV export comes with the Pro and Studio plans")
    rows = session.exec(
        select(Reel, Competitor.username).join(Competitor)
        .where(Competitor.user_id == user.id).order_by(Reel.views.desc(), Reel.id)
    ).all()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Account", "Reel", "Posted", "Views", "Likes", "Comments", "Length (s)", "Content plan",
        "Your hook", "Your script", "Your call to action", "AI hook", "AI script", "AI call to action",
        "Language", "Caption",
    ])
    for reel, username in rows:
        writer.writerow([
            f"@{username}", reel.url or "", reel.posted_at.date().isoformat() if reel.posted_at else "",
            reel.views, reel.likes, reel.comments, round(reel.duration_seconds or 0),
            reel.plan_status or "", reel.hook, reel.script, reel.cta,
            reel.translation_hook or "", reel.translation_script or "", reel.translation_cta or "",
            reel.transcript_language or "", reel.caption,
        ])
    # The byte order mark makes Excel read the file as UTF-8 (emoji, accents).
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="remixfox-reels.csv"'})


@router.get("/export/plan.md", responses={200: {"content": {"text/markdown": {}}}})
def export_markdown(session: SessionDep, user: CurrentUser) -> Response:
    """The content plan as Markdown, which pastes straight into Notion (Studio)."""
    allowed = allowance(session, user)
    if not (allowed and allowed.notion):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Notion export comes with the Studio plan")
    rows = session.exec(
        select(Reel, Competitor.username).join(Competitor)
        .where(Competitor.user_id == user.id, Reel.plan_status.is_not(None))
        .order_by(Reel.planned_at.desc())
    ).all()
    lines = ["# Remixfox content plan", ""]
    for status_key, heading in (("todo", "❌ Not started"), ("ready", "⏳ Ready to film"), ("posted", "✅ Posted")):
        group = [(r, u) for r, u in rows if r.plan_status == status_key]
        if not group:
            continue
        lines += [f"## {heading}", ""]
        for reel, username in group:
            lines += [f"### {_title(reel)}", "",
                      f"@{username} · {reel.views:,} views" + (f" · [Open on Instagram]({reel.url})" if reel.url else ""), ""]
            for name, text_ in (("Hook", reel.hook or reel.translation_hook),
                                ("Script", reel.script or reel.translation_script),
                                ("Call to action", reel.cta or reel.translation_cta)):
                if text_:
                    lines += [f"**{name}:** {text_.strip()}", ""]
    return Response("\n".join(lines), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="remixfox-content-plan.md"'})


# ---------- Owner ----------

@router.get("/owner/overview")
def owner_overview(session: SessionDep, user: StaffUser) -> dict:
    """Everyone's accounts and the latest fetches, for the owner and admins (to help people)."""
    admin_emails = set(session.exec(select(Admin.email)).all())
    competitors = (
        select(Competitor.user_id, func.count(Competitor.id).label("n")).group_by(Competitor.user_id).subquery()
    )
    reels = (
        select(Competitor.user_id, func.count(Reel.id).label("n"))
        .join(Reel, Reel.competitor_id == Competitor.id).group_by(Competitor.user_id).subquery()
    )
    users = session.exec(
        select(User, competitors.c.n, reels.c.n)
        .outerjoin(competitors, competitors.c.user_id == User.id)
        .outerjoin(reels, reels.c.user_id == User.id)
        .order_by(User.created_at.desc()).limit(200)
    ).all()
    recent = session.exec(
        select(FetchJob, Competitor.username, User)
        .join(Competitor, FetchJob.competitor_id == Competitor.id)
        .outerjoin(User, Competitor.user_id == User.id)
        .order_by(FetchJob.id.desc()).limit(30)
    ).all()
    count = lambda stmt: session.exec(stmt).one()  # noqa: E731
    return {
        "totals": {
            "users": count(select(func.count(User.id))),
            "subscribed": count(select(func.count(User.id)).where(User.subscription_status.in_(("active", "trialing")))),
            "competitors": count(select(func.count(Competitor.id))),
            "reels": count(select(func.count(Reel.id))),
            "active_jobs": count(select(func.count(FetchJob.id)).where(FetchJob.status.in_(ACTIVE_STATUSES))),
        },
        "users": [
            {
                "label": label(u), "email": u.email, "telegram": u.telegram_id is not None,
                "created_at": u.created_at, "owner": is_owner(u), "admin": u.email in admin_emails,
                "subscription_status": u.subscription_status,
                "plan": u.plan if u.subscription_status in ACTIVE_SUBSCRIPTIONS else None,
                "plan_interval": u.plan_interval, "competitors": c or 0, "reels": r or 0,
            }
            for u, c, r in users
        ],
        "jobs": [
            {
                "id": job.id, "username": username, "account": label(owner) if owner else None,
                "status": job.status, "message": job.message,
                "created_at": job.created_at, "finished_at": job.finished_at,
            }
            for job, username, owner in recent
        ],
    }


# ---------- Your settings ----------

@router.patch("/me")
def update_me(payload: MeUpdate, session: SessionDep, user: CurrentUser) -> Me:
    """Your voice for AI remixes, and the AI's language (changing it sends your transcripts
    to the AI again; your own texts stay)."""
    if payload.remix_profile is not None:
        user.remix_profile = payload.remix_profile.strip() or None
        session.commit()
    if payload.translate_to is not None:
        if payload.translate_to not in translations.LANGUAGES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose one of the languages on offer")
        if payload.translate_to != user.translate_to:
            user.translate_to = payload.translate_to
            session.commit()
            if translations.requeue(session, user.id):
                ai.wake()
    return me(session, user)


def remix_source(reel: Reel) -> dict | None:
    """What the AI remixes: its split version (in the user's language) if done, else the
    transcript, else (no speech) the caption; None when there is nothing yet."""
    caption = reel.caption_translation or reel.caption or ""
    if reel.translated_at is not None and (reel.translation_hook or reel.translation_script):
        return {"views": reel.views, "hook": reel.translation_hook or "", "main_script": reel.translation_script or "",
                "cta": reel.translation_cta or "", "caption": caption[:2_000]}
    if reel.transcript:
        return {"views": reel.views, "transcript": reel.transcript, "caption": caption[:2_000]}
    if reel.transcript == "" and caption.strip():
        return {"views": reel.views, "caption": caption[:2_000]}
    return None


@router.post("/reels/{reel_id}/remix")
def remix_reel(reel_id: int, session: SessionDep, user: CurrentUser) -> RemixOut:
    """A new hook, script and CTA for this reel in your own voice (Settings → Your voice).
    Nothing is saved: the editor fills your version and you press Save. About 20 seconds."""
    reel, _ = get_reel(session, reel_id, user)
    source = remix_source(reel)
    if source is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Get the transcript first: the AI remixes what the author says.")
    allowed = allowance(session, user)
    if plans.remixes_left(session, user.id, allowed) == 0:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "The free trial's 10 AI remixes are used. Choose a plan to keep remixing." if allowed.trial else
            f"This month's {allowed.remixes} AI remixes on your {allowed.label} plan are used up. "
            f"They start again on {plans.resets_on()}, or choose a bigger plan.",
        )
    if not ai.configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The AI is not set up yet. Try again later.")
    try:
        parts, usage = ai.remix(source, user.remix_profile or "", user.translate_to or "en")
    except ai.AIError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    plans.record(session, user.id, "remix", cost_usd=ai.cost_usd(usage), tokens=int(usage.get("total_tokens") or 0))
    session.commit()
    return RemixOut(hook=parts["hook"], script=parts["main_script"], cta=parts["cta"],
                    remixes_left=plans.remixes_left(session, user.id, allowed))


# ---------- The admin panel (owner and admins) ----------

@router.get("/admin/stats")
def admin_stats(session: SessionDep, user: StaffUser) -> dict:
    """Everything at a glance: payments, people and plans, reels, and what Apify and OpenAI cost."""
    month = plans.month_start()
    count = lambda stmt: session.exec(stmt).one()  # noqa: E731

    # People and plans (MRR: what the active subscriptions bring in a month).
    by_plan = {key: {"month": 0, "year": 0} for key in plans.PLANS}
    mrr = 0.0
    for plan_key, interval in session.exec(
        select(User.plan, User.plan_interval).where(User.subscription_status.in_(ACTIVE_SUBSCRIPTIONS))
    ).all():
        plan = plans.PLANS.get(plan_key or "", plans.PLANS["start"])
        interval = interval if interval in plans.INTERVALS else "month"
        by_plan[plan.key][interval] += 1
        mrr += plan.monthly_pence if interval == "month" else plan.yearly_pence / 12
    staff_emails = set(session.exec(select(Admin.email)).all()) | {(settings.owner_email or '').strip().lower()}
    total = count(select(func.count(User.id)))
    subscribed = sum(n for counts in by_plan.values() for n in counts.values())
    staff = count(select(func.count(User.id)).where(User.email.in_(staff_emails)))

    # Usage and costs, from usage_events (kept when competitors are deleted).
    def usage_since(since):
        rows = session.exec(
            select(UsageEvent.kind, func.count(UsageEvent.id), func.coalesce(func.sum(UsageEvent.cost_usd), 0),
                   func.coalesce(func.sum(UsageEvent.tokens), 0), func.coalesce(func.sum(UsageEvent.units), 0))
            .where(UsageEvent.at >= since).group_by(UsageEvent.kind)
        ).all()
        by_kind = {kind: (n, float(cost), int(tokens), int(units)) for kind, n, cost, tokens, units in rows}
        fetches, videos, ai_, reels, remixes = (
            by_kind.get(k, (0, 0.0, 0, 0)) for k in ("fetch", "video", "ai", "reels", "remix"))
        apify_usd = fetches[1] + videos[1] + reels[1]
        openai_usd = ai_[1] + remixes[1]
        return {
            "fetches": fetches[0], "reels": reels[3], "videos": videos[0], "ai_reels": ai_[0], "remixes": remixes[0],
            "openai_tokens": ai_[2] + remixes[2], "apify_usd": round(apify_usd, 2), "openai_usd": round(openai_usd, 4),
            "total_usd": round(apify_usd + openai_usd, 2),
        }

    done = Reel.translated_at.is_not(None)
    return {
        "generated_at": utcnow(),
        "money": {**billing.payments(), "currency": "GBP", "mrr": round(mrr / 100, 2)},
        "users": {
            "total": total, "subscribed": subscribed, "free": max(0, total - subscribed - staff), "staff": staff,
            "new_this_month": count(select(func.count(User.id)).where(User.created_at >= month)),
        },
        "plans": [{"key": key, "name": plans.PLANS[key].name, **counts} for key, counts in by_plan.items()],
        "content": {
            "competitors": count(select(func.count(Competitor.id))),
            "reels": count(select(func.count(Reel.id))),
            "reels_this_month": count(select(func.count(Reel.id)).where(Reel.created_at >= month)),
            "transcribed": count(select(func.count(Reel.id)).where(Reel.transcript.is_not(None))),
            "scripted": count(select(func.count(Reel.id)).where(done)),
            "translated": count(select(func.count(Reel.id)).where(
                done, func.coalesce(Reel.transcript_language, "en") != func.coalesce(Reel.translated_into, "en"))),
            "failed": count(select(func.count(Reel.id)).where(Reel.translation_error.is_not(None))),
            "waiting": len(translations.waiting(session, limit=500)),
            "planned": count(select(func.count(Reel.id)).where(Reel.plan_status.is_not(None))),
        },
        "usage": {"all_time": usage_since(plans.EVER), "this_month": usage_since(month)},
    }


# ---------- The AI (owner only) ----------

@router.get("/ai")
def ai_status(session: SessionDep, user: OwnerUser) -> dict:
    """Is the OpenAI key set, the model, the queue and the last run."""
    return ai.status(session)


# ---------- Admins: up to 10 e-mails the owner adds (owner only) ----------

MAX_ADMINS = 10


@router.get("/admins")
def list_admins(session: SessionDep, user: OwnerUser) -> list[dict]:
    admins = session.exec(select(Admin).order_by(Admin.added_at)).all()
    accounts = {u.email: u for u in session.exec(select(User).where(User.email.in_([a.email for a in admins]))).all()}
    return [{"email": a.email, "added_at": a.added_at, "signed_up": a.email in accounts,
             "has_password": bool(accounts.get(a.email) and accounts[a.email].password_hash)} for a in admins]


@router.post("/admins", status_code=status.HTTP_201_CREATED)
def add_admin(payload: AdminIn, session: SessionDep, user: OwnerUser) -> dict:
    email = normalise_email(payload.email)
    if email == user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That is you: the owner already has every permission")
    if session.exec(select(Admin).where(Admin.email == email)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"{email} is already an admin")
    if session.exec(select(func.count(Admin.id))).one() >= MAX_ADMINS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"You can have up to {MAX_ADMINS} admins")
    session.add(Admin(email=email))
    if payload.password:
        set_account_password(session, email, payload.password)
    session.commit()
    return {"email": email}


def set_account_password(session: Session, email: str, password: str) -> None:
    """The owner gives an admin a password: their account is created if they have none yet."""
    passwords.check_strength(password)
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(email=email)
        session.add(user)
    user.password_hash = passwords.hash_password(password)


@router.put("/admins/{email}/password", status_code=status.HTTP_204_NO_CONTENT)
def set_admin_password(email: str, payload: PasswordSet, session: SessionDep, user: OwnerUser) -> None:
    """A new password for an admin (the owner tells them; they can change it in Settings)."""
    admin = session.exec(select(Admin).where(Admin.email == email.strip().lower())).first()
    if admin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not an admin")
    set_account_password(session, admin.email, payload.password)
    session.commit()


@router.delete("/admins/{email}", status_code=status.HTTP_204_NO_CONTENT)
def remove_admin(email: str, session: SessionDep, user: OwnerUser) -> None:
    admin = session.exec(select(Admin).where(Admin.email == email.strip().lower())).first()
    if admin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not an admin")
    session.delete(admin)
    session.commit()


@router.get("/reels/{reel_id}/cover", responses={200: {"content": {"image/*": {}}}})
def reel_cover(reel_id: int, session: SessionDep, user: CurrentUser) -> Response:
    get_reel(session, reel_id, user)
    stored = storage.get_cover(session, reel_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover stored for this reel")
    # Cover URLs carry a version, so the browser can keep them.
    return Response(
        stored.data,
        media_type=stored.content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )
