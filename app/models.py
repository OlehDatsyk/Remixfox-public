"""Database tables (SQLModel, table=True) and the JSON shapes the API sends."""

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import JSON, BigInteger, DateTime, Index, LargeBinary, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

# Predictable constraint names, so Alembic migrations can refer to them.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Job statuses, in order.
QUEUED, RUNNING, SUCCEEDED, FAILED = "queued", "running", "succeeded", "failed"
ACTIVE_STATUSES = (QUEUED, RUNNING)

# Content plan statuses: ❌ nothing ready yet, ⏳ script ready and waiting to
# be filmed, ✅ filmed and posted.
PlanStatus = Literal["todo", "ready", "posted"]
PLAN_STATUSES = ("todo", "ready", "posted")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(**kwargs):
    return Field(sa_type=DateTime(timezone=True), **kwargs)


# Subscription statuses that give access to paid features (fetching reels).
ACTIVE_SUBSCRIPTIONS = ("active", "trialing")


# ---------- Tables ----------

class User(SQLModel, table=True):
    """An account, signed in with an e-mail code, through the Telegram bot, or with a
    password (optional; see app/passwords.py). The owner is the account with
    OWNER_EMAIL (see app/auth.py)."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str | None = Field(default=None, max_length=254, unique=True)
    # From Telegram: the user's id (stable), @username and name.
    telegram_id: int | None = Field(default=None, sa_type=BigInteger, unique=True)
    telegram_username: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    created_at: datetime = _timestamp(default_factory=utcnow)
    # Stripe: the customer and subscription, its status and period end, and which
    # plan (start, pro or studio, see app/plans.py) is paid monthly or yearly.
    stripe_customer_id: str | None = Field(default=None, max_length=100, index=True)
    stripe_subscription_id: str | None = Field(default=None, max_length=100)
    subscription_status: str | None = Field(default=None, max_length=30)
    subscription_renews_at: datetime | None = _timestamp(default=None)
    plan: str | None = Field(default=None, max_length=20)
    plan_interval: str | None = Field(default=None, max_length=10)
    # scrypt hash of the optional password (app/passwords.py); None: code or Telegram only.
    password_hash: str | None = Field(default=None, max_length=200)
    # "Your voice": niche, audience, tone and offer, so AI remixes sound like the user.
    remix_profile: str | None = Field(default=None, sa_type=Text)
    # The language the AI writes the hook, script and CTA in (see app/translations.py).
    translate_to: str = Field(default="en", max_length=8, sa_column_kwargs={"server_default": "en"})


class Admin(SQLModel, table=True):
    """An e-mail the owner made an admin (up to 10): support staff with free use.

    Kept by e-mail, so it works before the person has signed in for the first time.
    """

    __tablename__ = "admins"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(max_length=254, unique=True)
    added_at: datetime = _timestamp(default_factory=utcnow)


class UsageEvent(SQLModel, table=True):
    """Something an account used that costs money; the plan limits count these.

    Kept when competitors and reels are deleted, so removing and adding a
    competitor again gives back neither the month's videos nor the day's fetches.
    """

    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_user_id_kind_at", "user_id", "kind", "at"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE")
    # "video": a transcript fetched; "fetch": a competitor's reels fetched; "ai": the AI's share of a batch.
    kind: str = Field(max_length=10)
    at: datetime = _timestamp(default_factory=utcnow)
    cost_usd: float = Field(default=0, sa_column_kwargs={"server_default": "0"})  # Apify or OpenAI, estimated
    tokens: int = Field(default=0, sa_column_kwargs={"server_default": "0"})  # OpenAI tokens (kind "ai")
    units: int = Field(default=0, sa_column_kwargs={"server_default": "0"})  # reels fetched (kind "reels")


class Avatar(SQLModel, table=True):
    """A user's profile photo, taken from Telegram (kept apart so users stay small)."""

    __tablename__ = "avatars"

    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", primary_key=True)
    content_type: str = Field(max_length=50)
    data: bytes = Field(sa_type=LargeBinary)
    updated_at: datetime = _timestamp(default_factory=utcnow)


class LoginCode(SQLModel, table=True):
    """A one-time sign-in code sent by e-mail. Only a keyed hash of it is kept."""

    __tablename__ = "login_codes"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(max_length=254, index=True)
    code_hash: str = Field(max_length=64)
    ip: str | None = Field(default=None, max_length=64, index=True)
    attempts: int = 0
    created_at: datetime = _timestamp(default_factory=utcnow)
    expires_at: datetime = _timestamp()
    used_at: datetime | None = _timestamp(default=None)


class TelegramLogin(SQLModel, table=True):
    """A sign-in through the Telegram bot (see app/telegram.py).

    From the website: token_hash is the Start link's token and browser_hash the
    waiting browser's cookie; user_id is set once the user pressed Start. From the
    bot: token_hash is a one-time link's token and user_id is known at once.
    connect_user_id: a signed-in user connecting Telegram to their account.
    """

    __tablename__ = "telegram_logins"

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(max_length=64, unique=True)
    browser_hash: str | None = Field(default=None, max_length=64)
    connect_user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE")
    user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE")
    error: str | None = Field(default=None, max_length=300)
    created_at: datetime = _timestamp(default_factory=utcnow)
    expires_at: datetime = _timestamp()
    used_at: datetime | None = _timestamp(default=None)


class LoginSession(SQLModel, table=True):
    """A signed-in browser. Only a hash of the cookie's token is stored."""

    __tablename__ = "sessions"

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(max_length=64, unique=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    created_at: datetime = _timestamp(default_factory=utcnow)
    expires_at: datetime = _timestamp()


class Competitor(SQLModel, table=True):
    """An Instagram account whose reels a user tracks."""

    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("user_id", "username"),)

    id: int | None = Field(default=None, primary_key=True)
    # None only for data from before accounts existed; the owner claims it.
    user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE", index=True)
    username: str = Field(max_length=30)
    created_at: datetime = _timestamp(default_factory=utcnow)
    last_fetched_at: datetime | None = _timestamp(default=None)


class Reel(SQLModel, table=True):
    """One reel: Instagram data plus the user's own texts (hook, script, CTA)."""

    __tablename__ = "reels"
    __table_args__ = (UniqueConstraint("competitor_id", "shortcode"),)

    id: int | None = Field(default=None, primary_key=True)
    competitor_id: int = Field(foreign_key="competitors.id", ondelete="CASCADE", index=True)
    shortcode: str = Field(max_length=64)
    url: str | None = Field(default=None, max_length=500)
    caption: str = Field(default="", sa_type=Text)
    # What the browser loads: /api/reels/{id}/cover, or None while there is no cover yet.
    cover_url: str | None = Field(default=None, max_length=500)
    video_url: str | None = Field(default=None, sa_type=Text)
    views: int = Field(default=0, sa_type=BigInteger)
    likes: int = Field(default=0, sa_type=BigInteger)
    comments: int = Field(default=0, sa_type=BigInteger)
    duration_seconds: float | None = None
    posted_at: datetime | None = _timestamp(default=None, index=True)
    tags: list[str] = Field(default_factory=list, sa_type=JSON)
    # What the author says, from Apify's paid transcript add-on, fetched only
    # when asked for. None: not fetched yet; "": fetched, but no speech.
    transcript: str | None = Field(default=None, sa_type=Text)
    transcript_fetched_at: datetime | None = _timestamp(default=None)
    # The AI (app/ai.py, OpenAI API) detects the transcript's language
    # and splits it into hook, main script and call to action: English stays
    # word for word as it is, other languages are translated into British
    # English. A non-empty transcript without translated_at or translation_error
    # waits in the queue; translation_started_at is when the worker took it.
    transcript_language: str | None = Field(default=None, max_length=16)
    translation_started_at: datetime | None = _timestamp(default=None)
    translation_hook: str | None = Field(default=None, sa_type=Text)
    translation_script: str | None = Field(default=None, sa_type=Text)
    translation_cta: str | None = Field(default=None, sa_type=Text)
    translation_model: str | None = Field(default=None, max_length=100)
    translated_at: datetime | None = _timestamp(default=None)
    translation_error: str | None = Field(default=None, sa_type=Text)
    translated_into: str | None = Field(default=None, max_length=8)  # the language of the AI's version
    caption_translation: str | None = Field(default=None, sa_type=Text)  # the caption in that language
    hook: str = Field(default="", sa_type=Text)
    script: str = Field(default="", sa_type=Text)
    cta: str = Field(default="", sa_type=Text)
    edited_at: datetime | None = _timestamp(default=None)
    # The content plan: None when the reel is not in it, else todo / ready / posted.
    plan_status: str | None = Field(default=None, max_length=20, index=True)
    planned_at: datetime | None = _timestamp(default=None)
    plan_status_at: datetime | None = _timestamp(default=None)
    # Skipped on the Home page's swipe deck (not shown there again).
    skipped_at: datetime | None = _timestamp(default=None)
    created_at: datetime = _timestamp(default_factory=utcnow)
    updated_at: datetime = _timestamp(default_factory=utcnow)


class FetchJob(SQLModel, table=True):
    """One "fetch the latest reels of this competitor" run, with its status."""

    __tablename__ = "fetch_jobs"

    id: int | None = Field(default=None, primary_key=True)
    competitor_id: int = Field(foreign_key="competitors.id", ondelete="CASCADE", index=True)
    status: str = Field(default=QUEUED, max_length=20)
    message: str = Field(default="", sa_type=Text)
    reels_saved: int = 0
    # Reels this job added, and reels it got transcripts for (for the AI step).
    new_reel_ids: list[int] = Field(default_factory=list, sa_type=JSON)
    transcribed_reel_ids: list[int] = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = _timestamp(default_factory=utcnow)
    started_at: datetime | None = _timestamp(default=None)
    finished_at: datetime | None = _timestamp(default=None)


class JobEvent(SQLModel, table=True):
    """One line of a job's log: a step starting, progressing, finishing or failing."""

    __tablename__ = "job_events"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="fetch_jobs.id", ondelete="CASCADE", index=True)
    created_at: datetime = _timestamp(default_factory=utcnow)
    step: str = Field(max_length=20)
    # start, progress, done, skip, error or debug
    kind: str = Field(max_length=10)
    message: str = Field(sa_type=Text)
    # On "start" events of counting steps: how many items the step will handle.
    total: int | None = None


class StoredFile(SQLModel, table=True):
    """File storage (covers for now), kept in Postgres so it survives redeploys.

    Render's free instances have no persistent disk.
    """

    __tablename__ = "stored_files"
    __table_args__ = (UniqueConstraint("reel_id", "kind"),)

    id: int | None = Field(default=None, primary_key=True)
    reel_id: int = Field(foreign_key="reels.id", ondelete="CASCADE")
    kind: str = Field(max_length=20)
    content_type: str = Field(max_length=100)
    data: bytes = Field(sa_type=LargeBinary)
    source_url: str | None = Field(default=None, sa_type=Text)
    created_at: datetime = _timestamp(default_factory=utcnow)


# ---------- API shapes ----------

class CompetitorCreate(SQLModel):
    handle: str = Field(max_length=300, description="Instagram username, @username or profile link")


class JobRead(SQLModel):
    id: int
    competitor_id: int
    username: str
    status: str
    message: str
    reels_saved: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class StepRead(SQLModel):
    key: str
    title: str
    # pending, active, done, skipped or failed
    state: str
    detail: str
    started_at: datetime | None
    finished_at: datetime | None
    # For steps that count reels (e.g. 12 of 20): None otherwise.
    done_count: int | None = None
    total_count: int | None = None


class EventRead(SQLModel):
    id: int
    created_at: datetime
    step: str
    kind: str
    message: str


class JobDetail(JobRead):
    """A job with its pipeline steps and full log, for the activity panel."""

    steps: list[StepRead]
    events: list[EventRead]
    # True while any step (including the AI, after the job itself) is unfinished.
    in_progress: bool


class CompetitorRead(SQLModel):
    id: int
    username: str
    profile_url: str
    created_at: datetime
    last_fetched_at: datetime | None
    reel_count: int
    latest_job: JobRead | None


class ReelRead(SQLModel):
    id: int
    competitor_id: int
    username: str
    shortcode: str
    url: str | None
    caption: str
    cover_url: str | None
    video_url: str | None
    views: int
    likes: int
    comments: int
    duration_seconds: float | None
    posted_at: datetime | None
    tags: list[str]
    transcript: str | None
    transcript_fetched_at: datetime | None
    # What fetching the transcript would cost (None when it cannot be fetched).
    transcript_cost_usd: float | None
    # none (nothing to process), pending, done or failed.
    translation_status: str
    transcript_language: str | None
    # True only when the AI translated it from another language.
    translated: bool
    translation_started_at: datetime | None
    translation_hook: str | None
    translation_script: str | None
    translation_cta: str | None
    translation_model: str | None
    translated_at: datetime | None
    translation_error: str | None
    translated_into: str | None
    caption_translation: str | None
    hook: str
    script: str
    cta: str
    edited_at: datetime | None
    plan_status: str | None
    planned_at: datetime | None
    plan_status_at: datetime | None
    skipped_at: datetime | None


class ReelUpdate(SQLModel):
    hook: str | None = Field(default=None, max_length=5_000)
    script: str | None = Field(default=None, max_length=20_000)
    cta: str | None = Field(default=None, max_length=5_000)
    # Setting a status also adds the reel to the content plan.
    plan_status: PlanStatus | None = None


class TranslationIn(SQLModel):
    """The AI's result for one reel: the parts, or why it failed."""

    hook: str = Field(default="", max_length=5_000)
    main_script: str = Field(default="", max_length=30_000)
    cta: str = Field(default="", max_length=5_000)
    language: str = Field(default="", max_length=16)
    model: str = Field(default="", max_length=100)
    caption: str = Field(default="", max_length=10_000)
    error: str | None = Field(default=None, max_length=2_000)


class EmailStart(SQLModel):
    email: str = Field(max_length=254)


class EmailVerify(SQLModel):
    email: str = Field(max_length=254)
    code: str = Field(max_length=20)


class TelegramFinish(SQLModel):
    token: str = Field(max_length=100)


class Allowance(SQLModel):
    """What an account may use: None means no limit (the owner)."""

    label: str  # Owner, Admin, or the plan's name
    competitors: int | None
    videos: int | None  # analysed videos per calendar month
    exports: bool  # CSV
    notion: bool  # Markdown that pastes into Notion
    reels: int | None = None  # reels fetched from Instagram per calendar month (None: no monthly limit)
    remixes: int | None = None  # AI remixes in your voice per calendar month (None: no limit)
    trial: bool = False  # the free trial: counted once, not each month


class Usage(SQLModel):
    competitors: int
    videos: int  # analysed this calendar month (on the trial: ever)
    reels: int = 0  # fetched this calendar month
    remixes: int = 0  # AI remixes this calendar month (on the trial: ever)
    resets_at: datetime  # the start of next month


class Me(SQLModel):
    email: str | None
    name: str | None
    telegram_username: str | None
    telegram_connected: bool
    avatar_url: str | None
    role: Literal["owner", "admin", "user"]
    is_owner: bool
    created_at: datetime
    subscription_status: str | None
    subscription_renews_at: datetime | None
    plan: str | None
    plan_interval: str | None
    # What the account may use (None: no plan, so nothing that costs money) and has used.
    allowance: Allowance | None
    usage: Usage
    # False only when the free trial is used up (or there is nothing to fetch with).
    can_fetch: bool
    billing_ready: bool
    translate_to: str
    languages: dict[str, str]  # code -> name, for the setting
    has_password: bool
    trial_ends_at: datetime | None  # on the free trial: when it ends
    remix_profile: str | None


class MeUpdate(SQLModel):
    translate_to: str | None = Field(default=None, max_length=8)
    remix_profile: str | None = Field(default=None, max_length=1_000)


class RemixOut(SQLModel):
    """An AI remix of a reel in the user's voice: not saved until they press Save."""

    hook: str
    script: str
    cta: str
    remixes_left: int | None  # this month (None: no limit)


class CheckoutIn(SQLModel):
    plan: Literal["start", "pro", "studio"]
    interval: Literal["month", "year"]
    # The box on the Plans page: agrees to the Terms and asks for the plan to start at once
    # (needed for a new subscription; see billing.checkout).
    consent: bool = False


class AdminIn(SQLModel):
    email: str = Field(max_length=254)
    password: str | None = Field(default=None, max_length=128)  # the owner gives it to the admin


class PasswordLogin(SQLModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class PasswordSet(SQLModel):
    password: str = Field(max_length=128)
