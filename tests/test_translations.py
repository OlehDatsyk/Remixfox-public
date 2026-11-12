"""The AI step: the queue, the worker and the OpenAI API call (with OpenAI faked)."""

import json
import re
from dataclasses import replace
from datetime import timedelta

import httpx

import pytest
from sqlmodel import Session

from app import ai, translations
from app.db import engine
from sqlmodel import select

from app.models import Competitor, Reel, TranslationIn, UsageEvent, utcnow
from tests.conftest import sign_up, user_id

TRANSCRIPT = (
    "Most people use Claude incorrectly. Here is the colour coded setup I use every day "
    "to organize my projects. Write PLUGIN in the comments and I'll send it to you."
)
# The English transcript split as it is, and a Spanish one translated.
ENGLISH = {
    "language": "en",
    "hook": "Most people use Claude incorrectly.",
    "main_script": "Here is the colour coded setup I use every day to organize my projects.",
    "cta": "Write PLUGIN in the comments and I'll send it to you.",
}
SPANISH = "La mayoría usa Claude mal. Esta es mi configuración. Escribe PLUGIN en los comentarios."
FROM_SPANISH = {
    "language": "es",
    "hook": "Most people use Claude badly.",
    "main_script": "This is my set-up.",
    "cta": "Write PLUGIN in the comments.",
}
MODEL_LABEL = "gpt-5.6-luna (high) via OpenAI API"


def add_reels(*transcripts) -> list[int]:
    """The owner's reels of @natgeo with these transcripts (None: no transcript yet), oldest first."""
    with Session(engine) as session:
        competitor = Competitor(username="natgeo", user_id=user_id())
        session.add(competitor)
        session.flush()
        reels = [
            Reel(
                competitor_id=competitor.id,
                shortcode=f"R{i}",
                url=f"https://www.instagram.com/reel/R{i}/",
                transcript=text,
                transcript_fetched_at=utcnow() + timedelta(seconds=i) if text is not None else None,
            )
            for i, text in enumerate(transcripts)
        ]
        session.add_all(reels)
        session.commit()
        return [reel.id for reel in reels]


def waiting_ids() -> list[int]:
    with Session(engine) as session:
        return [reel.id for reel, _ in translations.waiting(session)]


# ---------- The queue ----------

def test_only_transcripts_without_a_result_are_waiting(client):
    first, no_transcript, no_speech, second = add_reels(TRANSCRIPT, None, "", "Second one.")
    assert waiting_ids() == [first, second]  # oldest transcript first

    statuses = {r["id"]: r["translation_status"] for r in client.get("/api/reels").json()}
    assert statuses == {first: "pending", no_transcript: "none", no_speech: "none", second: "pending"}


def test_a_claimed_transcript_is_handed_out_again_only_after_a_timeout(client):
    [reel_id] = add_reels(TRANSCRIPT)
    with Session(engine) as session:
        assert [reel.id for reel, _ in translations.claim(session)] == [reel_id]
        assert translations.claim(session) == []
        session.get(Reel, reel_id).translation_started_at = utcnow() - timedelta(minutes=16)  # the worker died
        session.commit()
        assert [reel.id for reel, _ in translations.claim(session)] == [reel_id]


@pytest.mark.parametrize(("raw", "code"), [("en-GB", "en"), ("English", "en"), ("ES", "es"), ("pt_BR", "pt")])
def test_language_codes_are_normalised(raw, code):
    assert translations.language_code(raw) == code


# ---------- The worker ----------

def test_the_worker_stores_ai_results(client, fake_ai):
    english, spanish = add_reels(TRANSCRIPT, SPANISH)
    fake_ai.answers = {TRANSCRIPT: ENGLISH, SPANISH: FROM_SPANISH}

    assert fake_ai.process() == 2
    reel = client.get(f"/api/reels/{english}").json()
    assert (reel["translation_status"], reel["transcript_language"], reel["translated"]) == ("done", "en", False)
    assert reel["translation_hook"] == "Most people use Claude incorrectly."
    assert reel["translation_cta"] == "Write PLUGIN in the comments and I'll send it to you."
    assert reel["translation_model"] == MODEL_LABEL
    reel = client.get(f"/api/reels/{spanish}").json()
    assert (reel["transcript_language"], reel["translated"]) == ("es", True)
    assert reel["translation_hook"] == "Most people use Claude badly."
    assert waiting_ids() == [] and fake_ai.process() == 0


def test_the_worker_processes_everything_waiting_in_batches_of_ten(client, fake_ai):
    ids = add_reels(*[TRANSCRIPT] * 12)
    fake_ai.answers = {TRANSCRIPT: ENGLISH}

    assert fake_ai.process() == 12
    assert [prompt.count('"id":') for prompt in fake_ai.prompts] == [10, 2]
    assert {r["id"]: r["translation_status"] for r in client.get("/api/reels").json()} == dict.fromkeys(ids, "done")


def test_a_rejected_result_is_stored_as_an_error_and_not_retried(client, fake_ai):
    [reel_id] = add_reels(TRANSCRIPT)
    # English must not be changed, not even its spelling.
    fake_ai.answers = {TRANSCRIPT: {**ENGLISH, "main_script": ENGLISH["main_script"].replace("organize", "organise")}}
    fake_ai.process()

    reel = client.get(f"/api/reels/{reel_id}").json()
    assert reel["translation_status"] == "failed"
    assert reel["translation_error"].startswith("The AI changed the British English text")
    assert waiting_ids() == []


def test_when_the_ai_cannot_run_the_batch_waits_for_the_next_try(client, fake_ai, monkeypatch):
    ids = add_reels(TRANSCRIPT, TRANSCRIPT)

    def usage_limit(prompt):
        raise ai.AIError("OpenAI: You exceeded your current quota")

    monkeypatch.setattr(ai, "run_model", usage_limit)
    with pytest.raises(ai.AIError):
        ai.process_once()
    assert waiting_ids() == ids
    assert client.get("/api/ai").json()["last_result"] == "OpenAI: You exceeded your current quota"


def test_the_worker_waits_for_an_api_key(client, fake_ai, monkeypatch):
    add_reels(TRANSCRIPT)
    monkeypatch.setattr(ai, "settings", replace(ai.settings, openai_api_key=""))
    assert ai.process_once() == 0 and fake_ai.prompts == []


def test_prompt_carries_every_reel_its_language_and_the_rules():
    prompt = ai.build_prompt([(7, TRANSCRIPT), (8, SPANISH, "uk")])
    assert '"id": 7' in prompt and TRANSCRIPT in prompt and '"into": "British English"' in prompt
    assert '"into": "Ukrainian"' in prompt and '"caption": ""' in prompt
    assert 'If the transcript is already in the "into" language, keep it exactly as it' in prompt
    assert "word for word" in prompt and "British spelling" in prompt
    assert "Most people use Claude incorrectly." in prompt  # the hook example


def test_batch_results_are_checked_reel_by_reel():
    items = [(1, TRANSCRIPT), (2, SPANISH), (3, TRANSCRIPT)]
    answer = {"reels": [{"id": 1, **ENGLISH}, {"id": 2, **FROM_SPANISH}]}

    results, usage = ai.translate_batch(items, run=lambda prompt: answer)
    results = dict(results)
    assert usage == {}

    assert results[1] == TranslationIn(**ENGLISH, model=MODEL_LABEL)
    assert results[2].language == "es" and results[2].hook == "Most people use Claude badly."
    assert results[3] == TranslationIn(error="The AI returned no result for this reel.")


def test_a_moved_call_to_action_still_counts_as_word_for_word():
    transcript = "Comment PLUGIN for the link. Most people use Claude incorrectly. Here is why."
    moved = {"hook": "Most people use Claude incorrectly.", "main_script": "Here is why.",
             "cta": "Comment PLUGIN for the link."}
    assert ai.check(transcript, moved, "en") is None


def test_a_translation_that_summarises_is_rejected():
    summary = {"hook": "Claude.", "main_script": "", "cta": FROM_SPANISH["cta"]}
    assert ai.check(SPANISH, summary, "es").startswith("The AI shortened the text")


def test_a_translation_may_need_many_more_words():
    # Ukrainian has no articles, so a faithful English version is much longer (this was rejected before).
    ukrainian = "Нічний сталкер, вбивця, який очевидно знову вдарив у вихідні."
    english = {"hook": "The night stalker, a killer who has evidently struck again at the weekend.",
               "main_script": "", "cta": ""}
    assert ai.check(ukrainian, english, "uk", "en") is None


def test_a_transcript_in_the_chosen_language_stays_as_it_is():
    kept = {"hook": "La mayoría usa Claude mal.", "main_script": "Esta es mi configuración.",
            "cta": "Escribe PLUGIN en los comentarios."}
    assert ai.check(SPANISH, kept, "es", "es") is None
    changed = {**kept, "hook": "La mayoría usa Claude fatal."}
    assert ai.check(SPANISH, changed, "es", "es").startswith("The AI changed the Spanish text")


@pytest.mark.parametrize(("text", "count"), [("I'll send it to you", 5), ("colour-coded set-up", 4), ("", 0)])
def test_word_count(text, count):
    assert len(ai.words(text)) == count


# ---------- The OpenAI API call ----------

def openai_reply(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def test_the_model_is_asked_for_strict_json_with_high_reasoning(monkeypatch):
    monkeypatch.setattr(ai, "settings", replace(ai.settings, openai_api_key="sk-test"))
    sent = {}
    answer = {"reels": [{"id": 1, **ENGLISH}]}
    text = json.dumps(answer)

    def post(url, json, headers, timeout):  # httpx.post's own keyword names
        sent.update(url=url, body=json, headers=headers)
        return openai_reply({"status": "completed", "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ]})

    monkeypatch.setattr(ai.httpx, "post", post)
    assert ai.run_model("prompt") == {**answer, "usage": {}}
    assert sent["url"] == "https://api.openai.com/v1/responses"
    assert sent["headers"] == {"Authorization": "Bearer sk-test"}
    body = sent["body"]
    assert (body["model"], body["reasoning"], body["input"]) == ("gpt-5.6-luna", {"effort": "high"}, "prompt")
    assert body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["strict"] is True


@pytest.mark.parametrize(("status_code", "payload", "message"), [
    (401, {"error": {"message": "Incorrect API key provided: sk-proj-****abcd."}},
     "OpenAI refused the API key (OPENAI_API_KEY): Incorrect API key provided: sk-…."),
    (429, {"error": {"message": "You exceeded your current quota"}}, "OpenAI: You exceeded your current quota"),
    (200, {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "OpenAI stopped before finishing (max_output_tokens)"),
    (200, {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not json"}]}]}, "OpenAI did not reply with JSON"),
])
def test_openai_problems_are_explained(monkeypatch, status_code, payload, message):
    monkeypatch.setattr(ai, "settings", replace(ai.settings, openai_api_key="sk-test"))
    monkeypatch.setattr(ai.httpx, "post", lambda *args, **kwargs: openai_reply(payload, status_code))
    with pytest.raises(ai.AIError, match=re.escape(message)):
        ai.run_model("prompt")


def test_no_key_means_no_calls(monkeypatch):
    monkeypatch.setattr(ai, "settings", replace(ai.settings, openai_api_key=""))
    with pytest.raises(ai.AIError, match="No OpenAI API key"):
        ai.run_model("prompt")


def test_the_ai_writes_in_the_language_chosen_in_settings(client, fake_ai):
    english, spanish = add_reels(TRANSCRIPT, SPANISH)
    fake_ai.answers = {TRANSCRIPT: ENGLISH, SPANISH: FROM_SPANISH}
    fake_ai.process()
    assert client.get(f"/api/reels/{english}").json()["translated"] is False
    client.patch(f"/api/reels/{english}", json={"hook": "My own hook"})

    assert client.patch("/api/me", json={"translate_to": "ru"}).status_code == 422  # never offered
    me = client.patch("/api/me", json={"translate_to": "es"}).json()
    assert me["translate_to"] == "es" and "ru" not in me["languages"] and "be" not in me["languages"]
    assert sorted(waiting_ids()) == [english, spanish]  # both go back to the AI

    to_spanish = {"language": "en", "hook": "La mayoría usa Claude incorrectamente.",
                  "main_script": "Aquí está mi configuración por colores para organizar mis proyectos cada día.",
                  "cta": "Escribe PLUGIN en los comentarios y te lo envío."}
    kept = {"language": "es", "hook": "La mayoría usa Claude mal.", "main_script": "Esta es mi configuración.",
            "cta": "Escribe PLUGIN en los comentarios."}
    fake_ai.answers = {TRANSCRIPT: to_spanish, SPANISH: kept}
    fake_ai.process()
    assert '"into": "Spanish"' in fake_ai.prompts[-1]
    reel = client.get(f"/api/reels/{english}").json()
    assert (reel["translated_into"], reel["translated"], reel["translation_hook"]) == ("es", True, to_spanish["hook"])
    assert reel["hook"] == "My own hook"  # your own text is never replaced
    reel = client.get(f"/api/reels/{spanish}").json()
    assert (reel["translated_into"], reel["translated"]) == ("es", False)


def test_the_ai_logs_its_tokens_and_cost(client, fake_ai):
    add_reels(TRANSCRIPT, SPANISH)
    fake_ai.answers = {TRANSCRIPT: ENGLISH, SPANISH: FROM_SPANISH}
    fake_ai.process()
    with Session(engine) as session:
        rows = session.exec(select(UsageEvent).where(UsageEvent.kind == "ai")).all()
    assert [(e.user_id, e.tokens) for e in rows] == [(user_id(), 750), (user_id(), 750)]
    assert round(sum(e.cost_usd for e in rows), 6) == round((1000 * 0.20 + 500 * 1.20) / 1_000_000, 6)


def test_captions_are_translated_too_even_without_speech(client, fake_ai):
    speech, silent = add_reels(TRANSCRIPT, "")
    with Session(engine) as session:
        for reel_id in (speech, silent):
            session.get(Reel, reel_id).caption = "Ловлю моменти, де живе ніжність 🌸 #весна"
        session.commit()
    assert sorted(waiting_ids()) == [speech, silent]  # a reel with no speech still has a caption to translate
    fake_ai.answers = {TRANSCRIPT: {**ENGLISH, "caption": "Catching moments where tenderness lives 🌸 #весна"},
                       "": {"language": "uk", "hook": "", "main_script": "", "cta": "",
                            "caption": "Catching moments where tenderness lives 🌸 #весна"}}
    fake_ai.process()
    assert '"caption": "Ловлю моменти' in fake_ai.prompts[-1]
    for reel_id in (speech, silent):
        reel = client.get(f"/api/reels/{reel_id}").json()
        assert reel["translation_status"] == "done"
        assert reel["caption_translation"] == "Catching moments where tenderness lives 🌸 #весна"


def test_polish_russian_and_belarusian_are_never_offered(client):
    languages = client.get("/api/me").json()["languages"]
    assert list(languages) == ["en", "uk", "es", "fr", "de", "it", "pt"]  # Ukrainian right after British English
    for code in ("pl", "ru", "be"):
        assert client.patch("/api/me", json={"translate_to": code}).status_code == 422


def test_ai_settings_are_for_the_owner_only(client, fake_ai):
    add_reels(TRANSCRIPT)
    status = client.get("/api/ai").json()
    assert (status["configured"], status["model"], status["effort"], status["waiting"]) == (True, "gpt-5.6-luna", "high", 1)
    sign_up(client, "someone@example.com")
    assert client.get("/api/ai").status_code == 403
