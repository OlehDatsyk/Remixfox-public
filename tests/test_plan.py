"""The content plan: adding reels, statuses (❌ todo, ⏳ ready, ✅ posted) and the user's version."""

from tests.conftest import reel_item

TALK = "Most people use Claude incorrectly. Here is how I use it. Comment PLUGIN and I'll send it."
AI_SPLIT = {"language": "en", "hook": "Most people use Claude incorrectly.",
         "main_script": "Here is how I use it.", "cta": "Comment PLUGIN and I'll send it."}


def reels_by_code(client) -> dict:
    return {r["shortcode"]: r for r in client.get("/api/reels").json()}


def with_ai_split(client, fake_apify, fake_ai) -> dict:
    """@natgeo's two reels, REEL1 already split by the AI."""
    fake_apify.transcripts = {"REEL1": TALK}
    fake_ai.answers = {TALK: AI_SPLIT}
    client.post("/api/competitors", json={"handle": "natgeo"})
    assert fake_ai.process() == 1
    return reels_by_code(client)


def test_adding_a_reel_starts_my_version_from_the_ai(client, fake_apify, auto_transcribe, fake_ai):
    reel = with_ai_split(client, fake_apify, fake_ai)["REEL1"]
    assert reel["plan_status"] is None and reel["hook"] == ""

    added = client.post(f"/api/reels/{reel['id']}/plan").json()
    assert added["plan_status"] == "todo"
    assert added["planned_at"] is not None
    assert (added["hook"], added["script"], added["cta"]) == (
        "Most people use Claude incorrectly.", "Here is how I use it.", "Comment PLUGIN and I'll send it.",
    )
    assert [r["id"] for r in client.get("/api/plan").json()] == [reel["id"]]

    # Adding again changes nothing.
    again = client.post(f"/api/reels/{reel['id']}/plan").json()
    assert again["planned_at"] == added["planned_at"]


def test_my_own_text_is_never_overwritten(client, fake_apify, auto_transcribe, fake_ai):
    reel = with_ai_split(client, fake_apify, fake_ai)["REEL1"]
    client.patch(f"/api/reels/{reel['id']}", json={"hook": "My own hook"})
    added = client.post(f"/api/reels/{reel['id']}/plan").json()
    assert (added["hook"], added["script"]) == ("My own hook", "")


def test_a_planned_reel_gets_the_ai_text_when_it_arrives(client, fake_apify, auto_transcribe, fake_ai):
    fake_apify.transcripts = {"REEL1": TALK}
    client.post("/api/competitors", json={"handle": "natgeo"})
    reel = reels_by_code(client)["REEL1"]
    assert client.post(f"/api/reels/{reel['id']}/plan").json()["hook"] == ""  # the AI is not done yet

    fake_ai.answers = {TALK: AI_SPLIT}
    assert fake_ai.process() == 1
    assert client.get(f"/api/reels/{reel['id']}").json()["script"] == "Here is how I use it."


def test_statuses_and_order(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    reels = reels_by_code(client)
    one, two = reels["REEL1"]["id"], reels["REEL2"]["id"]

    # Setting a status puts a reel in the plan too.
    ready = client.patch(f"/api/reels/{one}", json={"plan_status": "ready"}).json()
    assert ready["plan_status"] == "ready" and ready["plan_status_at"] is not None
    client.post(f"/api/reels/{two}/plan")
    assert [r["id"] for r in client.get("/api/plan").json()] == [two, one]  # newest first

    client.patch(f"/api/reels/{two}", json={"plan_status": "posted"})
    assert [(r["id"], r["plan_status"]) for r in client.get("/api/plan").json()] == [
        (one, "ready"), (two, "posted"),  # posted ones last
    ]
    assert client.patch(f"/api/reels/{one}", json={"plan_status": "filmed"}).status_code == 422


def test_editing_text_and_status_together(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    reel_id = reels_by_code(client)["REEL1"]["id"]
    client.post(f"/api/reels/{reel_id}/plan")
    saved = client.patch(
        f"/api/reels/{reel_id}", json={"hook": "New hook", "script": "New script", "plan_status": "ready"}
    ).json()
    assert (saved["hook"], saved["script"], saved["plan_status"]) == ("New hook", "New script", "ready")
    assert saved["edited_at"] is not None


def test_removing_from_the_plan_keeps_my_text(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    reel_id = reels_by_code(client)["REEL1"]["id"]
    client.patch(f"/api/reels/{reel_id}", json={"hook": "Keep me", "plan_status": "ready"})

    removed = client.delete(f"/api/reels/{reel_id}/plan").json()
    assert (removed["plan_status"], removed["planned_at"], removed["hook"]) == (None, None, "Keep me")
    assert client.get("/api/plan").json() == []
    assert client.post("/api/reels/9999/plan").status_code == 404
    assert reel_item  # shared fixture builder


def test_skipping_and_undoing_an_idea(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    reel_id = reels_by_code(client)["REEL1"]["id"]
    skipped = client.post(f"/api/reels/{reel_id}/skip").json()
    assert skipped["skipped_at"] is not None
    assert client.post(f"/api/reels/{reel_id}/skip").json()["skipped_at"] == skipped["skipped_at"]
    assert client.delete(f"/api/reels/{reel_id}/skip").json()["skipped_at"] is None
    assert client.post("/api/reels/9999/skip").status_code == 404
