"""What each account may use: plan limits, the owner's free access, admins, exports."""

from datetime import timedelta

from sqlalchemy import text
from sqlmodel import Session

from app import plans
from app.db import engine
from app.models import Competitor, UsageEvent, utcnow
from tests.conftest import OWNER, reel_item, sign_in_by_email, sign_up, user_id


def on_plan(email: str, plan: str | None = "start") -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET subscription_status = 'active', plan = :p WHERE email = :e"),
                     {"p": plan, "e": email})


def analysed_this_month(email: str, count: int) -> None:
    """Log this many videos (transcripts) analysed this month for the account."""
    with Session(engine) as session:
        session.add_all(UsageEvent(user_id=user_id(email), kind="video") for _ in range(count))
        session.commit()


def test_each_plan_tracks_only_so_many_competitors(client, fake_apify):
    sign_up(client, "someone@example.com")
    on_plan("someone@example.com", "start")
    for handle in ("natgeo", "nasa", "bbc"):
        assert client.post("/api/competitors", json={"handle": handle}).status_code == 201
    refused = client.post("/api/competitors", json={"handle": "vogue"})
    assert refused.status_code == 402
    assert refused.json()["detail"].startswith("Your Start plan tracks up to 3 competitors")
    me = client.get("/api/me").json()
    assert (me["usage"]["competitors"], me["allowance"]["competitors"]) == (3, 3)


def test_a_paid_subscription_without_a_known_plan_counts_as_start(client):
    sign_up(client, "someone@example.com")
    on_plan("someone@example.com", None)
    assert client.get("/api/me").json()["allowance"]["label"] == "Start"


def test_monthly_videos_stop_transcripts_but_keep_the_reels(client, fake_apify, auto_transcribe):
    sign_up(client, "someone@example.com")
    on_plan("someone@example.com", "start")
    analysed_this_month("someone@example.com", plans.PLANS["start"].videos - 1)  # one video left
    fake_apify.items = [reel_item("LOW", plays=100), reel_item("HIGH", plays=9000)]
    fake_apify.transcripts = {"LOW": "low words", "HIGH": "high words"}

    client.post("/api/competitors", json={"handle": "natgeo"})
    assert fake_apify.transcript_calls == [["HIGH"]]  # the most viewed reel gets the last video
    me = client.get("/api/me").json()
    assert me["usage"]["videos"] == 60 and me["usage"]["resets_at"].startswith(plans.next_month().date().isoformat())

    low = next(r for r in client.get("/api/reels").json() if r["shortcode"] == "LOW")
    refused = client.post(f"/api/reels/{low['id']}/transcript")
    assert refused.status_code == 402
    assert "60 videos on your Start plan are used up" in refused.json()["detail"]


def test_deleting_a_competitor_does_not_give_videos_back(client, fake_apify, auto_transcribe):
    sign_up(client, "someone@example.com")
    on_plan("someone@example.com", "start")
    fake_apify.transcripts = {"REEL1": "first words", "REEL2": "second words"}
    competitor = client.post("/api/competitors", json={"handle": "natgeo"}).json()
    assert client.get("/api/me").json()["usage"]["videos"] == 2
    assert client.delete(f"/api/competitors/{competitor['id']}").status_code == 204
    usage = client.get("/api/me").json()["usage"]
    assert (usage["videos"], usage["competitors"]) == (2, 0)


def test_removing_and_adding_competitors_again_stops_at_the_daily_fetches(client, fake_apify):
    sign_up(client, "someone@example.com")
    on_plan("someone@example.com", "start")  # 3 competitors, so 3 + 2 fetches a day
    for _ in range(plans.PLANS["start"].competitors + plans.SPARE_FETCHES):
        competitor = client.post("/api/competitors", json={"handle": "natgeo"}).json()
        client.delete(f"/api/competitors/{competitor['id']}")
    refused = client.post("/api/competitors", json={"handle": "natgeo"})
    assert refused.status_code == 429
    assert refused.json()["detail"].startswith("Your Start plan fetches reels up to 5 times a day")
    assert "again in about 24 hour(s)" in refused.json()["detail"]

    with engine.begin() as conn:  # a day later
        conn.execute(text("UPDATE usage_events SET at = at - interval '25 hours'"))
    assert client.post("/api/competitors", json={"handle": "natgeo"}).status_code == 201


def test_each_competitor_is_fetched_at_most_once_a_day(client, fake_apify):
    sign_up(client, "someone@example.com")
    on_plan("someone@example.com", "pro")
    competitor = client.post("/api/competitors", json={"handle": "natgeo"}).json()
    again = client.post(f"/api/competitors/{competitor['id']}/fetch")
    assert again.status_code == 429 and "fetch it again in about 24 hour(s)" in again.json()["detail"]
    assert client.post("/api/refresh").status_code == 429

    with Session(engine) as session:
        stored = session.get(Competitor, competitor["id"])
        stored.last_fetched_at = utcnow() - timedelta(hours=25)
        session.commit()
    assert client.post(f"/api/competitors/{competitor['id']}/fetch").status_code == 202


def test_the_owner_pays_nothing_and_has_no_limits(client, fake_apify):
    me = client.get("/api/me").json()
    assert (me["role"], me["can_fetch"], me["allowance"]["competitors"], me["allowance"]["videos"]) == ("owner", True, None, None)
    competitor = client.post("/api/competitors", json={"handle": "natgeo"}).json()
    assert client.post(f"/api/competitors/{competitor['id']}/fetch").status_code == 202  # no daily limit


def test_the_owner_manages_up_to_ten_admins(client):
    assert client.post("/api/admins", json={"email": "Helper@Example.com"}).status_code == 201
    assert client.post("/api/admins", json={"email": "helper@example.com"}).status_code == 409
    assert client.post("/api/admins", json={"email": OWNER}).status_code == 400
    for i in range(9):
        client.post("/api/admins", json={"email": f"admin{i}@example.com"})
    assert client.post("/api/admins", json={"email": "eleventh@example.com"}).status_code == 409
    admins = client.get("/api/admins").json()
    assert len(admins) == 10 and admins[0] == {**admins[0], "email": "helper@example.com", "signed_up": False}

    sign_up(client, "helper@example.com")  # the admin signs in with a code to their own e-mail
    me = client.get("/api/me").json()
    assert (me["role"], me["can_fetch"], me["allowance"]["label"], me["allowance"]["competitors"]) == ("admin", True, "Admin", 20)
    assert client.get("/api/owner/overview").status_code == 200  # to help people
    for forbidden in (client.get("/api/admins"), client.post("/api/admins", json={"email": "x@example.com"}),
                      client.get("/api/ai")):
        assert forbidden.status_code == 403

    sign_in_by_email(client, OWNER)
    assert client.delete("/api/admins/helper@example.com").status_code == 204
    assert client.delete("/api/admins/helper@example.com").status_code == 404
    sign_in_by_email(client, "helper@example.com")
    assert client.get("/api/me").json()["role"] == "user"
    assert client.get("/api/owner/overview").status_code == 403


def test_exports_follow_the_plan(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    reel = client.get("/api/reels").json()[0]
    client.patch(f"/api/reels/{reel['id']}", json={"hook": "My hook", "plan_status": "ready"})

    csv_file = client.get("/api/export/reels.csv")  # the owner gets everything
    assert csv_file.headers["content-type"].startswith("text/csv")
    assert csv_file.text.startswith("﻿Account,Reel,Posted,Views")
    assert "@natgeo" in csv_file.text and "My hook" in csv_file.text
    notion = client.get("/api/export/plan.md").text
    assert notion.startswith("# Remixfox content plan") and "## ⏳ Ready to film" in notion and "**Hook:** My hook" in notion

    sign_up(client, "someone@example.com")
    on_plan("someone@example.com", "start")
    assert client.get("/api/export/reels.csv").status_code == 402
    on_plan("someone@example.com", "pro")
    assert client.get("/api/export/reels.csv").status_code == 200
    assert client.get("/api/export/plan.md").status_code == 402
    on_plan("someone@example.com", "studio")
    assert client.get("/api/export/plan.md").status_code == 200


def test_the_reels_of_some_accounts_only(client, fake_apify):
    natgeo = client.post("/api/competitors", json={"handle": "natgeo"}).json()
    fake_apify.items = [reel_item("NASA1", plays=50), reel_item("NASA2", plays=70000)]
    nasa = client.post("/api/competitors", json={"handle": "nasa"}).json()
    codes = lambda url: [r["shortcode"] for r in client.get(url).json()]  # noqa: E731
    assert codes(f"/api/reels?competitor_id={nasa['id']}&sort=views") == ["NASA2", "NASA1"]
    assert len(codes(f"/api/reels?competitor_id={nasa['id']}&competitor_id={natgeo['id']}&limit=100")) == 4
    sign_up(client, "someone@example.com")
    assert codes(f"/api/reels?competitor_id={nasa['id']}") == []  # not theirs


def test_the_admin_panel_sees_money_people_reels_and_costs(client, fake_apify, auto_transcribe):
    fake_apify.transcripts = {"REEL1": "first words"}
    client.post("/api/competitors", json={"handle": "natgeo"})
    sign_up(client, "someone@example.com")
    on_plan("someone@example.com", "pro")
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET plan_interval = 'year' WHERE email = 'someone@example.com'"))
    assert client.get("/api/admin/stats").status_code == 403

    sign_in_by_email(client, OWNER)
    stats = client.get("/api/admin/stats").json()
    assert stats["users"] == {**stats["users"], "total": 2, "subscribed": 1, "free": 0, "staff": 1}
    assert {p["key"]: (p["month"], p["year"]) for p in stats["plans"]}["pro"] == (0, 1)
    assert stats["money"]["mrr"] == round(37400 / 12 / 100, 2) and stats["money"]["configured"] is False
    assert (stats["content"]["reels"], stats["content"]["transcribed"], stats["content"]["waiting"]) == (2, 1, 1)
    usage = stats["usage"]["all_time"]
    assert (usage["fetches"], usage["reels"], usage["videos"]) == (1, 2, 1)
    assert usage["apify_usd"] == round(0.001 + 2 * 0.0026 + 0.001 + 0.0026 + 0.048, 2)


def test_each_plan_fetches_so_many_reels_a_month(client, fake_apify):
    sign_up(client, "someone@example.com")
    on_plan("someone@example.com", "start")
    with Session(engine) as session:  # 890 of the Start plan's 900 reels fetched this month
        session.add(UsageEvent(user_id=user_id("someone@example.com"), kind="reels", units=890))
        session.commit()
    assert client.get("/api/me").json()["usage"]["reels"] == 890
    refused = client.post("/api/competitors", json={"handle": "natgeo"})
    assert refused.status_code == 402 and refused.json()["detail"].startswith("This month's 900 reels on your Start plan")
    assert [p["reels"] for p in client.get("/api/billing/plans").json()["plans"]] == [900, 2400, 6000]


def test_plan_prices_and_profit_stay_sensible():
    for plan in plans.PLANS.values():
        assert plan.yearly_pence == round(plan.monthly_pence * 12 * 0.8 / 100) * 100
        # Worst case: every video at £0.06, every daily fetch at £0.04 (one per competitor
        # plus the spare ones), and Stripe's 2.2% + 20p; the owner still makes money.
        # Reels are about 0.25p each, run fees included; the monthly reel limit caps them.
        # An AI remix is at most about 0.4p.
        cost = plan.videos * 6 + plan.reels * 0.25 + plan.remixes * 0.4 + plan.monthly_pence * 0.022 + 20
        assert plan.monthly_pence - cost > 0, plan.key
