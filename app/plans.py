"""The subscription plans: what each costs and includes, and what an account has used.

This file is the one place to change prices or limits: Stripe's products and prices
are created from it (app/billing.py), and the landing page and Settings show it.

How the numbers were set (November 2026):
- An analysed video costs about £0.04-0.08: Apify's transcript is $0.048 per started
  minute, and GPT-5.6 Luna (high reasoning) about $0.0015 a reel with its caption.
- A fetch (a competitor's 20 latest reels) costs about £0.04, about 0.25p a reel; each
  competitor can be refreshed once a day, an account fetches at most once per
  competitor plus two a day, and each plan has a monthly reel limit.
- An AI remix in the user's voice costs about $0.002-0.005 (one request, high reasoning).
- Stripe takes about 2.2% + 20p a payment.
- Usage is logged (usage_events), so deleting a competitor gives nothing back.
Even a customer who uses every limit to the full leaves about £8.20 a month on Start,
£15.30 on Pro and £27.60 on Studio (test_plan_prices_and_profit_stay_sensible); a
typical one leaves much more. Annual plans cost 20% less than twelve months. The free
trial (15 days, two competitors, five reels each) costs about $0.60 when used fully.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from app.models import Allowance, Competitor, Usage, UsageEvent, utcnow

INTERVALS = ("month", "year")
ANNUAL_DISCOUNT = 0.20
REFRESH_HOURS = 24  # a competitor can be fetched once a day (the owner has no limit)
SPARE_FETCHES = 2  # fetches a day on top of one per competitor, for swapping competitors


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    tagline: str
    monthly_pence: int
    competitors: int
    videos: int  # analysed videos (transcribed and scripted) per calendar month
    reels: int  # reels fetched from Instagram per calendar month (20 a fetch)
    remixes: int  # AI remixes in the user's voice per calendar month
    exports: bool  # CSV export of reels and scripts
    notion: bool  # Markdown export that pastes straight into Notion
    priority_support: bool

    @property
    def yearly_pence(self) -> int:
        """Twelve months less 20%, rounded to whole pounds."""
        return round(self.monthly_pence * 12 * (1 - ANNUAL_DISCOUNT) / 100) * 100

    def pence(self, interval: str) -> int:
        return self.monthly_pence if interval == "month" else self.yearly_pence

    def public(self) -> dict:
        return {
            "key": self.key, "name": self.name, "tagline": self.tagline,
            "monthly": self.monthly_pence / 100, "yearly": self.yearly_pence / 100,
            "yearly_per_month": round(self.yearly_pence / 1200, 2),
            "competitors": self.competitors, "videos": self.videos, "reels": self.reels, "remixes": self.remixes,
            "exports": self.exports, "notion": self.notion, "priority_support": self.priority_support,
        }


PLANS: dict[str, Plan] = {
    "start": Plan("start", "Start", "To try it out and for solo creators", 1500, 3, 60, 900, 100, False, False, False),
    "pro": Plan("pro", "Pro", "For creating content every day", 3900, 8, 250, 2400, 400, True, False, True),
    "studio": Plan("studio", "Studio", "For producers and teams", 9900, 20, 800, 6000, 1500, True, True, True),
}

OWNER = Allowance(label="Owner", competitors=None, videos=None, exports=True, notion=True)
# A new account gets 15 days free: two competitors, the 5 most viewed reels of each,
# with transcripts and scripts.
TRIAL = Allowance(label="Free trial", competitors=2, videos=10, remixes=10, exports=False, notion=False, trial=True)
TRIAL_DAYS = 15
TRIAL_REELS = 5  # the most viewed of each competitor's latest reels are kept
TRIAL_FETCHES = 4  # in all: one per competitor, and a second try for each
ADMIN = Allowance(label="Admin", competitors=PLANS["studio"].competitors, videos=PLANS["studio"].videos,
                  reels=PLANS["studio"].reels, remixes=PLANS["studio"].remixes, exports=True, notion=True)


def of_plan(plan: Plan) -> Allowance:
    return Allowance(label=plan.name, competitors=plan.competitors, videos=plan.videos, reels=plan.reels,
                     remixes=plan.remixes, exports=plan.exports, notion=plan.notion)


def month_start(now: datetime | None = None) -> datetime:
    now = now or utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month(now: datetime | None = None) -> datetime:
    start = month_start(now)
    return (start + timedelta(days=32)).replace(day=1)


def record(session: Session, user_id: int | None, kind: str, cost_usd: float = 0, tokens: int = 0,
           units: int = 0) -> None:
    """Note a video (transcript), a fetch, the reels it brought or the AI's work, with what it
    cost; the caller commits."""
    if user_id is not None:
        session.add(UsageEvent(user_id=user_id, kind=kind, cost_usd=cost_usd, tokens=tokens, units=units))


def _since(session: Session, user_id: int, kind: str, since: datetime):
    return (select(func.count(UsageEvent.id), func.min(UsageEvent.at))
            .where(UsageEvent.user_id == user_id, UsageEvent.kind == kind, UsageEvent.at >= since))


EVER = datetime(2000, 1, 1, tzinfo=timezone.utc)


def videos_used(session: Session, user_id: int, allowance: Allowance | None = None) -> int:
    """Videos analysed (transcripts fetched) this calendar month in UTC; on the trial, ever."""
    since = EVER if allowance is not None and allowance.trial else month_start()
    return session.exec(_since(session, user_id, "video", since)).one()[0]


def reels_used(session: Session, user_id: int) -> int:
    """Reels fetched from Instagram this calendar month (each fetch brings about 20)."""
    return session.exec(
        select(func.coalesce(func.sum(UsageEvent.units), 0))
        .where(UsageEvent.user_id == user_id, UsageEvent.kind == "reels", UsageEvent.at >= month_start())
    ).one()


def reels_left(session: Session, user_id: int, allowance: Allowance | None) -> int | None:
    if allowance is None or allowance.reels is None:
        return None
    return max(0, allowance.reels - reels_used(session, user_id))


def remixes_used(session: Session, user_id: int, allowance: Allowance | None = None) -> int:
    """AI remixes this calendar month in UTC; on the trial, ever."""
    since = EVER if allowance is not None and allowance.trial else month_start()
    return session.exec(_since(session, user_id, "remix", since)).one()[0]


def remixes_left(session: Session, user_id: int, allowance: Allowance | None) -> int | None:
    if allowance is None:
        return 0
    if allowance.remixes is None:
        return None
    return max(0, allowance.remixes - remixes_used(session, user_id, allowance))


def fetches_ever(session: Session, user_id: int) -> int:
    return session.exec(_since(session, user_id, "fetch", EVER)).one()[0]


def trial_ends_at(user) -> datetime:
    return user.created_at + timedelta(days=TRIAL_DAYS)


def trial_over(session: Session, user, allowance: Allowance | None) -> str | None:
    """Why the free trial can fetch no more ("ended" or "used"), or None (only ever set on the trial)."""
    if not (allowance and allowance.trial):
        return None
    if utcnow() >= trial_ends_at(user):
        return "ended"
    return "used" if fetches_ever(session, user.id) >= TRIAL_FETCHES else None


def daily_fetches(allowance: Allowance | None) -> int | None:
    """Fetches allowed in any 24 hours: one per competitor, plus a few for swapping competitors."""
    if allowance is None:
        return 0
    return None if allowance.competitors is None else allowance.competitors + SPARE_FETCHES


def fetches_wait(session: Session, user_id: int, allowance: Allowance | None) -> float | None:
    """Hours until the next fetch is allowed, or None when one is allowed now."""
    limit = daily_fetches(allowance)
    if limit is None:
        return None
    count, oldest = session.exec(_since(session, user_id, "fetch", utcnow() - timedelta(hours=REFRESH_HOURS))).one()
    if count < limit:
        return None
    return max(0.0, (oldest + timedelta(hours=REFRESH_HOURS) - utcnow()).total_seconds() / 3600)


def fetches_left(session: Session, user_id: int, allowance: Allowance | None) -> int | None:
    limit = daily_fetches(allowance)
    if limit is None:
        return None
    count, _ = session.exec(_since(session, user_id, "fetch", utcnow() - timedelta(hours=REFRESH_HOURS))).one()
    return max(0, limit - count)


def competitors_used(session: Session, user_id: int) -> int:
    return session.exec(select(func.count(Competitor.id)).where(Competitor.user_id == user_id)).one()


def usage(session: Session, user_id: int, allowance: Allowance | None = None) -> Usage:
    return Usage(competitors=competitors_used(session, user_id), videos=videos_used(session, user_id, allowance),
                 reels=reels_used(session, user_id), remixes=remixes_used(session, user_id, allowance),
                 resets_at=next_month())


def videos_left(session: Session, user_id: int, allowance: Allowance | None) -> int | None:
    """How many more videos may be analysed this month, or on the trial (None: no limit)."""
    if allowance is None:
        return 0
    if allowance.videos is None:
        return None
    return max(0, allowance.videos - videos_used(session, user_id, allowance))


def resets_on(now: datetime | None = None) -> str:
    """For messages: "1 December"."""
    day = next_month(now).astimezone(timezone.utc)
    return f"{day.day} {day.strftime('%B')}"
