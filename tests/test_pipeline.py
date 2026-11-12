"""The "Fetch reels" pipeline as the activity panel sees it: steps, counts and log."""

TALK = "Most people use Claude incorrectly. Here is how I use it. Comment PLUGIN and I'll send it."


def steps(job: dict) -> dict:
    return {s["key"]: s for s in job["steps"]}


def latest_job(client) -> dict:
    job_id = client.get("/api/jobs").json()[0]["id"]
    return client.get(f"/api/jobs/{job_id}").json()


SPANISH = "La mayoría usa Claude mal."
SPLIT = {"language": "en", "hook": "Most people use Claude incorrectly.",
         "main_script": "Here is how I use it.", "cta": "Comment PLUGIN and I'll send it."}
TRANSLATED = {"language": "es", "hook": "Most people use Claude badly.", "main_script": "", "cta": ""}


def test_the_whole_pipeline_step_by_step(client, fake_apify, auto_transcribe, fake_ai):
    fake_apify.transcripts = {"REEL1": TALK, "REEL2": SPANISH}
    client.post("/api/competitors", json={"handle": "natgeo"})
    job = latest_job(client)

    assert [s["key"] for s in job["steps"]] == ["database", "queue", "apify", "save", "transcripts", "ai"]
    s = steps(job)
    assert {k: v["state"] for k, v in s.items()} == {
        "database": "done", "queue": "done", "apify": "done", "save": "done", "transcripts": "done", "ai": "active",
    }
    assert s["database"]["detail"].startswith("Saved @natgeo to the database")
    assert s["apify"]["detail"].startswith("Apify returned 2 reels in")
    assert (s["save"]["done_count"], s["save"]["total_count"]) == (2, 2)
    assert (s["transcripts"]["done_count"], s["transcripts"]["total_count"]) == (2, 2)
    assert s["ai"]["detail"] == "2 transcript(s) queued for the AI"
    assert job["in_progress"] is True and job["status"] == "succeeded"
    assert fake_apify.transcript_calls == [["REEL1", "REEL2"]]  # one run for both

    log = [e["message"] for e in job["events"]]
    assert any(m.startswith("Asking Apify for the latest 20 reels of @natgeo") for m in log)
    assert any(m.startswith("REEL1: new, 5.0K views") for m in log)
    assert any(m.startswith("Asking Apify for 2 transcript(s) in 1 run(s)") for m in log)

    # The AI worker takes both: one English reel and one Spanish.
    fake_ai.answers = {TALK: SPLIT, SPANISH: TRANSLATED}
    seen = []
    fake_ai.during = lambda: seen.append(steps(latest_job(client))["ai"]["detail"])
    assert fake_ai.process() == 2
    assert seen[0].startswith("The AI is working on 2 transcript(s)")

    job = latest_job(client)
    assert steps(job)["ai"]["state"] == "done"
    assert steps(job)["ai"]["detail"] == "2 processed (1 translated, 1 kept as is)"
    assert job["in_progress"] is False
    assert any("English, kept word for word" in e["message"] for e in job["events"])

    reels = {r["shortcode"]: r for r in client.get("/api/reels").json()}
    assert (reels["REEL1"]["transcript_language"], reels["REEL1"]["translated"]) == ("en", False)
    assert (reels["REEL2"]["transcript_language"], reels["REEL2"]["translated"]) == ("es", True)


def test_a_refetch_pays_for_no_transcript_twice(client, fake_apify, auto_transcribe):
    fake_apify.transcripts = {"REEL1": TALK, "REEL2": TALK}
    competitor = client.post("/api/competitors", json={"handle": "natgeo"}).json()
    client.post(f"/api/competitors/{competitor['id']}/fetch")

    job = latest_job(client)
    assert steps(job)["transcripts"]["state"] == "skipped"
    assert steps(job)["transcripts"]["detail"] == "Every reel already has a transcript, so nothing is paid for"
    assert steps(job)["ai"]["state"] == "skipped"
    assert steps(job)["database"]["detail"].startswith("@natgeo is already in the database")
    assert fake_apify.transcript_calls == [["REEL1", "REEL2"]]  # only the first fetch


def test_reels_are_kept_when_transcripts_fail(client, fake_apify, auto_transcribe):
    fake_apify.transcript_error = "The Apify account has run out of credit."
    client.post("/api/competitors", json={"handle": "natgeo"})
    job = latest_job(client)
    assert job["status"] == "succeeded" and len(client.get("/api/reels").json()) == 2
    assert steps(job)["transcripts"]["state"] == "failed"
    assert "run out of credit" in steps(job)["transcripts"]["detail"]


def test_a_failed_apify_step_skips_the_rest(client, fake_apify):
    fake_apify.items = [{"error": "not_found", "errorDescription": "Profile does not exist"}]
    client.post("/api/competitors", json={"handle": "nobody.here"})
    job = latest_job(client)
    assert job["status"] == "failed" and job["in_progress"] is False
    assert {k: v["state"] for k, v in steps(job).items()} == {
        "database": "done", "queue": "done", "apify": "failed", "save": "skipped",
        "transcripts": "skipped", "ai": "skipped",
    }
    assert steps(job)["apify"]["detail"] == "Profile does not exist"


def test_transcripts_are_off_without_auto_transcribe(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    job = latest_job(client)
    assert steps(job)["transcripts"]["state"] == "skipped"
    assert fake_apify.transcript_calls == []


def test_deleting_a_reel(client, fake_apify):
    client.post("/api/competitors", json={"handle": "natgeo"})
    reel = client.get("/api/reels").json()[0]
    assert client.delete(f"/api/reels/{reel['id']}").status_code == 204
    assert client.get(f"/api/reels/{reel['id']}").status_code == 404
    assert client.get(f"/api/reels/{reel['id']}/cover").status_code == 404
    assert client.delete(f"/api/reels/{reel['id']}").status_code == 404

