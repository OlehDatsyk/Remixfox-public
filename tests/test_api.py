"""The flow from the diagram: add competitor -> fetch -> save -> list -> edit -> save."""

from sqlmodel import Session

from app import apify, jobs
from app.db import engine
from app.models import QUEUED, RUNNING, Competitor, FetchJob
from tests.conftest import FAKE_JPEG, OWNER, migrate_from_scratch, reel_item, sign_up, user_id


def test_health(client):
    assert client.get("/api/health").json() == {
        "status": "ok",
        "database": "ok",
        "apify": "not connected",
    }


def test_front_end_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Remixfox" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_a_new_database_is_empty(migrated):
    migrate_from_scratch()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        sign_up(client, OWNER)
        assert client.get("/api/reels").json() == []
        assert client.get("/api/competitors").json() == []


def test_add_competitor_fetches_and_stores_reels(client, fake_apify):
    response = client.post("/api/competitors", json={"handle": "https://www.instagram.com/NatGeo/?hl=en"})
    assert response.status_code == 201
    competitor = response.json()
    assert competitor["username"] == "natgeo"
    assert competitor["profile_url"] == "https://www.instagram.com/natgeo/"
    assert fake_apify.calls == [("natgeo", 20)]

    # The background job has run by the time TestClient returns.
    [job] = client.get("/api/jobs").json()
    assert (job["status"], job["reels_saved"], job["message"]) == ("succeeded", 2, "Saved 2 reels from @natgeo (2 new)")

    reels = client.get("/api/reels", params={"sort": "views"}).json()
    assert [r["shortcode"] for r in reels] == ["REEL1", "REEL2"]
    reel = reels[0]
    assert reel["username"] == "natgeo"
    assert reel["views"] == 5000 and reel["likes"] == 340 and reel["comments"] == 12
    assert reel["url"] == "https://www.instagram.com/reel/REEL1/"  # opens the reel itself
    assert reel["tags"] == ["travel"]
    assert reel["posted_at"].startswith("2026-11-01T10:00:00")

    # The cover is served from our own storage, not from Instagram.
    assert reel["cover_url"].startswith(f"/api/reels/{reel['id']}/cover?v=")
    cover = client.get(reel["cover_url"])
    assert cover.status_code == 200
    assert cover.content == FAKE_JPEG
    assert cover.headers["content-type"] == "image/jpeg"

    [listed] = client.get("/api/competitors").json()
    assert listed["reel_count"] == 2
    assert listed["last_fetched_at"] is not None
    assert listed["latest_job"]["status"] == "succeeded"


def test_add_competitor_validates_input(client, fake_apify):
    assert client.post("/api/competitors", json={"handle": "not a username!"}).status_code == 422
    assert client.post("/api/competitors", json={"handle": "instagram.com/reel/abc"}).status_code == 422
    assert client.post("/api/competitors", json={"handle": "@NatGeo"}).status_code == 201
    duplicate = client.post("/api/competitors", json={"handle": "natgeo"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "@natgeo is already in your competitors"


def test_fetch_without_apify_token_fails_clearly(client):
    client.post("/api/competitors", json={"handle": "natgeo"})
    [job] = client.get("/api/jobs").json()
    assert job["status"] == "failed"
    assert job["message"].startswith("Apify is not connected yet")


def test_fetch_reports_private_or_missing_accounts(client, fake_apify):
    fake_apify.items = [{"error": "not_found", "errorDescription": "Profile does not exist"}]
    client.post("/api/competitors", json={"handle": "nobody.here"})
    [job] = client.get("/api/jobs").json()
    assert (job["status"], job["message"]) == ("failed", "Profile does not exist")


def test_refetch_updates_numbers_and_keeps_the_users_texts(client, fake_apify):
    competitor = client.post("/api/competitors", json={"handle": "natgeo"}).json()
    [reel] = [r for r in client.get("/api/reels").json() if r["shortcode"] == "REEL1"]
    client.patch(f"/api/reels/{reel['id']}", json={"hook": "My hook"})

    fake_apify.items = [reel_item("REEL1", plays=9000), reel_item("REEL3", plays=10)]
    job = client.post(f"/api/competitors/{competitor['id']}/fetch")
    assert job.status_code == 202

    reels = {r["shortcode"]: r for r in client.get("/api/reels").json()}
    assert set(reels) == {"REEL1", "REEL2", "REEL3"}
    assert reels["REEL1"]["views"] == 9000
    assert reels["REEL1"]["hook"] == "My hook"
    # Covers are downloaded once per reel: REEL1 is not downloaded again.
    assert [url.rsplit("/", 1)[-1] for url in fake_apify.downloads] == [
        "REEL1_p828x828.jpg", "REEL2_p828x828.jpg", "REEL3_p828x828.jpg",
    ]


def test_cover_falls_back_to_the_full_size_image(client, fake_apify):
    fake_apify.items = [reel_item("REEL1")]
    fake_apify.broken_urls = {fake_apify.items[0]["images"][0]}
    client.post("/api/competitors", json={"handle": "natgeo"})

    [reel] = client.get("/api/reels").json()
    assert fake_apify.downloads == [fake_apify.items[0]["images"][0], fake_apify.items[0]["displayUrl"]]
    assert client.get(reel["cover_url"]).status_code == 200


def test_reels_keep_working_without_a_cover(client, fake_apify):
    fake_apify.items = [reel_item("REEL1")]
    fake_apify.broken_urls = {fake_apify.items[0]["images"][0], fake_apify.items[0]["displayUrl"]}
    client.post("/api/competitors", json={"handle": "natgeo"})

    [reel] = client.get("/api/reels").json()
    assert reel["cover_url"] is None
    assert client.get("/api/jobs").json()[0]["status"] == "succeeded"


def test_edit_reel_texts(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    reel = client.get("/api/reels").json()[0]
    assert reel["edited_at"] is None

    response = client.patch(
        f"/api/reels/{reel['id']}",
        json={"hook": "  Stop scrolling  ", "script": "Shot 1\nShot 2", "cta": "Follow for more"},
    )
    assert response.status_code == 200
    edited = response.json()
    assert (edited["hook"], edited["script"], edited["cta"]) == ("Stop scrolling", "Shot 1\nShot 2", "Follow for more")
    assert edited["edited_at"] is not None
    assert client.get(f"/api/reels/{reel['id']}").json()["hook"] == "Stop scrolling"

    # Only the fields sent are changed.
    client.patch(f"/api/reels/{reel['id']}", json={"cta": "Save this"})
    assert client.get(f"/api/reels/{reel['id']}").json()["hook"] == "Stop scrolling"

    assert client.patch("/api/reels/9999", json={"hook": "x"}).status_code == 404
    assert client.patch(f"/api/reels/{reel['id']}", json={"hook": "x" * 5001}).status_code == 422


def test_delete_competitor_removes_its_reels(client, fake_apify):
    competitor = client.post("/api/competitors", json={"handle": "natgeo"}).json()
    reel_id = client.get("/api/reels").json()[0]["id"]

    assert client.delete(f"/api/competitors/{competitor['id']}").status_code == 204
    assert client.get("/api/competitors").json() == []
    assert client.get("/api/reels").json() == []
    assert client.get(f"/api/reels/{reel_id}/cover").status_code == 404
    assert client.delete(f"/api/competitors/{competitor['id']}").status_code == 404


def test_refresh_fetches_every_competitor(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    client.post("/api/competitors", json={"handle": "nasa"})
    fake_apify.calls.clear()

    response = client.post("/api/refresh")
    assert response.status_code == 202
    assert sorted(job["username"] for job in response.json()) == ["nasa", "natgeo"]
    assert sorted(username for username, _ in fake_apify.calls) == ["nasa", "natgeo"]


def test_a_competitor_never_has_two_fetches_at_once(client, fake_apify):
    competitor = client.post("/api/competitors", json={"handle": "natgeo"}).json()
    with Session(engine) as session:
        session.add(FetchJob(competitor_id=competitor["id"], status=RUNNING))
        session.commit()

    response = client.post(f"/api/competitors/{competitor['id']}/fetch")
    assert response.json()["status"] == "running"
    assert len(client.get("/api/jobs").json()) == 2
    assert [job["status"] for job in client.get("/api/jobs", params={"active": True}).json()] == ["running"]


def test_jobs_left_running_by_a_restart_are_marked_failed(client):
    with Session(engine) as session:
        competitor = Competitor(username="natgeo", user_id=user_id())
        session.add(competitor)
        session.flush()
        session.add(FetchJob(competitor_id=competitor.id, status=QUEUED))
        session.commit()

    jobs.fail_interrupted_jobs()

    [job] = client.get("/api/jobs").json()
    assert job["status"] == "failed"
    assert job["message"].startswith("Interrupted by a server restart")


def test_transcript_is_fetched_once_and_stored(client, fake_apify, monkeypatch):
    client.post("/api/competitors", json={"handle": "natgeo"})
    [reel] = [r for r in client.get("/api/reels").json() if r["shortcode"] == "REEL1"]
    assert reel["transcript"] is None
    assert reel["transcript_cost_usd"] == 0.0516  # 14.5 s: one started minute

    calls = []

    def fetch_transcript(shortcode, duration_seconds):
        calls.append((shortcode, duration_seconds))
        return apify.parse_item(reel_item(shortcode, plays=8000, transcript="What the author says."))

    monkeypatch.setattr(apify, "fetch_transcript", fetch_transcript)

    response = client.post(f"/api/reels/{reel['id']}/transcript")
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "What the author says."
    assert body["transcript_fetched_at"] is not None
    assert body["views"] == 8000  # fresh numbers from the same run

    # Asking again costs nothing: the stored transcript comes back.
    assert client.post(f"/api/reels/{reel['id']}/transcript").json()["transcript"] == "What the author says."
    assert calls == [("REEL1", 14.5)]
    assert client.get(f"/api/reels/{reel['id']}").json()["transcript"] == "What the author says."


def test_reel_without_speech_gets_an_empty_transcript(client, fake_apify, monkeypatch):
    client.post("/api/competitors", json={"handle": "natgeo"})
    reel = client.get("/api/reels").json()[0]
    monkeypatch.setattr(apify, "fetch_transcript", lambda code, secs: apify.parse_item(reel_item(code)))

    assert client.post(f"/api/reels/{reel['id']}/transcript").json()["transcript"] == ""


def test_transcript_errors_are_explained(client, fake_apify, monkeypatch):
    client.post("/api/competitors", json={"handle": "natgeo"})
    reel = client.get("/api/reels").json()[0]

    def fail(code, secs):
        raise apify.ApifyError("The Apify account has run out of credit.")

    monkeypatch.setattr(apify, "fetch_transcript", fail)
    response = client.post(f"/api/reels/{reel['id']}/transcript")
    assert response.status_code == 502
    assert response.json()["detail"] == "The Apify account has run out of credit."
    assert client.get(f"/api/reels/{reel['id']}").json()["transcript"] is None  # can try again

    assert client.post("/api/reels/9999/transcript").status_code == 404



def test_pages_at_the_old_address_move_to_the_own_domain(migrated, monkeypatch):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "settings", replace(main.settings, public_url="https://remixfox.co.uk"))
    old = TestClient(main.app, base_url="https://reels-remix.onrender.com", follow_redirects=False)
    moved = old.get("/welcome.html?mode=signin")
    assert (moved.status_code, moved.headers["location"]) == (301, "https://remixfox.co.uk/welcome.html?mode=signin")
    assert old.get("/").headers["location"] == "https://remixfox.co.uk/"
    # The API keeps answering at the old address (health checks, webhooks).
    assert old.get("/api/health").status_code == 200
    assert old.post("/api/auth/email/verify", json={"email": "a@example.com", "code": "000000"}).status_code == 400
    # The own domain and local runs are served as they are; HTTPS adds HSTS.
    own = TestClient(main.app, base_url="https://remixfox.co.uk").get("/")
    assert (own.status_code, own.headers["strict-transport-security"]) == (200, "max-age=31536000")
    local = TestClient(main.app).get("/")
    assert local.status_code == 200 and "strict-transport-security" not in local.headers
