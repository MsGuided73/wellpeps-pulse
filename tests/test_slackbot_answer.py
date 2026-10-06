"""#pulse-query answers: sonnet sees the aggregate payload only; the numeric
guard strips invented numbers (templated fallback if nothing is left); the
scrubber removes handles, URLs, e-mails and long quotes but keeps the
dashboard link; <= 120 words of model text, <= 1500 chars in total."""

import re

import pytest

from harvey.slackbot import answer, guards
from harvey.slackbot.executor import QueryResult
from tests.slackbot_helpers import ScriptedBrain

DASH = "https://pulse.example.com"


def _result(**data) -> QueryResult:
    data = data or {"mentions": 42, "previous_period_mentions": 30, "change_pct": 40.0,
                    "by_category": [{"category": "complaint", "mentions": 12}]}
    return QueryResult("volume", 7, {"competitors": [], "drug": None, "platforms": [], "category": None},
                       data, "#analytics?days=7")


# ── Numeric guard ──


def test_sentences_with_invented_numbers_are_stripped():
    payload = _result().payload()
    text = "Mentions rose to 42 this week. That is 99 more than ever. Complaints were 12."
    kept, stripped = guards.verify_numbers(text, payload)
    assert kept == "Mentions rose to 42 this week. Complaints were 12."
    assert stripped == ["That is 99 more than ever."]


def test_numbers_in_the_payload_pass_in_any_format():
    payload = _result().payload()
    kept, stripped = guards.verify_numbers("Up 40% (40.0) over 7 days.", payload)
    assert stripped == [] and kept


def test_names_with_digits_are_not_figures():
    payload = _result().payload()
    kept, stripped = guards.verify_numbers("GLP-1 and BPC-157 chatter led; Q3 looks busy.", payload)
    assert stripped == []


def test_bullets_are_checked_line_by_line():
    payload = _result().payload()
    kept, _ = guards.verify_numbers("*Volume*\n- complaints: 12\n- praise: 77\n- steady", payload)
    assert kept == "*Volume*\n- complaints: 12\n- steady"


# ── Scrubber ──


def test_scrubber_removes_handles_urls_emails_and_long_quotes():
    text = ('People like @jane_doe and u/someone_else said "this is the worst refill experience I have ever had" '
            "see https://reddit.com/r/x/1 or <https://tiktok.com/v/2|this video> or mail a.b@c.example. "
            "<!channel> <@U123> r/glp1 talk. Short \"price hike\" stays.")
    out = guards.scrub(text, DASH)
    for leak in ("@jane_doe", "u/someone_else", "worst refill experience", "reddit.com", "tiktok.com",
                 "a.b@c.example", "<!channel>", "<@U123>", "r/glp1"):
        assert leak not in out, leak
    assert guards.QUOTE_REMOVED in out
    assert '"price hike"' in out and "this video" in out


def test_scrubber_keeps_dashboard_links_only():
    text = f"See <{DASH}/#analytics?days=7|the chart> and {DASH}/#feed?q=x but not https://evil.example/{DASH}"
    out = guards.scrub(text, DASH)
    assert f"<{DASH}/#analytics?days=7|the chart>" in out and f"{DASH}/#feed?q=x" in out
    assert "evil.example" not in out


def test_scrubber_removes_long_blockquotes():
    out = guards.scrub("Summary\n> one two three four five six seven eight nine\n> short one", DASH)
    assert "one two three" not in out and "> short one" in out


def test_without_a_dashboard_every_url_goes():
    assert "pulse.example.com" not in guards.scrub(f"go to {DASH}/#feed", "")


# ── Full answer ──


@pytest.mark.asyncio
async def test_answer_prompt_carries_the_payload_only():
    brain = ScriptedBrain(answers=["*42* relevant mentions, up 40%."])
    result = _result()
    raw = await answer.write(brain, result.payload())
    agent, task, prompt = brain.prompts[0]
    assert (agent, task) == ("slackbot", "answer")
    nonce = re.search(r"BEGIN_UNTRUSTED_RESULT (\w+)", prompt).group(1)
    assert prompt.rstrip().endswith(f"END_UNTRUSTED_RESULT {nonce}")
    assert '"mentions": 42' in prompt
    assert raw == "*42* relevant mentions, up 40%."


def test_finalize_appends_a_deterministic_dashboard_footer():
    out = answer.finalize("*42* relevant mentions, up 40%.", _result(), DASH)
    assert out.fallback is False
    assert out.text.startswith("*42* relevant mentions, up 40%.")
    assert out.text.endswith(f"<{DASH}/#analytics?days=7|Open in Pulse dashboard>")


def test_finalize_without_dashboard_url_has_no_link():
    out = answer.finalize("*42* relevant mentions.", _result(), "")
    assert "http" not in out.text and "Open in Pulse" not in out.text


@pytest.mark.parametrize("raw", ["", "Mentions hit 9000 and 1234 new users.", "@someone https://x.example"])
def test_fallback_to_templated_answer_when_nothing_survives(raw):
    out = answer.finalize(raw, _result(), DASH)
    assert out.fallback is True
    assert "42" in out.text and "9000" not in out.text
    assert out.text.endswith("|Open in Pulse dashboard>")


def test_model_text_capped_at_120_words_and_1500_chars():
    long = " ".join(["mentions"] * 400)
    out = answer.finalize(long, _result(), DASH)
    body = out.text.rsplit("\n", 1)[0]
    assert len(body.split()) <= guards.MAX_WORDS + 1  # + the ellipsis
    assert len(out.text) <= guards.MAX_CHARS
    huge = " ".join(["x" * 50] * 100)
    assert len(answer.finalize(huge, _result(), DASH).text) <= guards.MAX_CHARS


@pytest.mark.parametrize("intent,data", [
    ("share_of_voice", {"brand_mentions": 8, "brands": [{"brand": "WellPeps", "mentions": 4, "share_pct": 50.0,
                                                        "previous_share_pct": None, "change_pts": None}]}),
    ("sentiment", {"scale": "-1 (negative) to +1 (positive)", "min_scored_mentions": 3, "brands": [
        {"brand": "WellPeps", "mean": None, "previous_mean": None, "change": None, "scored_mentions": None}]}),
    ("emerging_terms", {"baseline_days": 28, "terms": []}),
    ("complaints", {"complaint_mentions": None, "top_clusters": []}),
    ("complaints", {"term": "shipping", "mentions": 3, "previous_period_mentions": None, "by_category": []}),
    ("drug_momentum", {"drugs": [], "below_floor": ["NAD+"]}),
    ("escalations_sla", {"escalations": 2, "acknowledged": 1, "median_minutes_to_acknowledge": 4.5,
                         "breached_pct": 50.0, "open_now": 1, "open_breached_now": 0, "by_kind": []}),
    ("latest_brief", {"brief": None}),
    ("latest_brief", {"brief": {"period": "daily", "window_start": "2026-09-27", "status": "ok",
                                "headline": "Shipping up", "top_actions": ["Refresh FAQ"]}}),
    ("search_count", {"term": "price", "mentions": None, "previous_period_mentions": 0, "by_category": []}),
])
def test_templated_answers_for_every_intent(intent, data):
    result = QueryResult(intent, 7, {}, data, "#x")
    text = answer.templated(result)
    assert text.strip()
    # The template only uses payload numbers, so it survives its own guard.
    kept, stripped = guards.verify_numbers(text, result.payload())
    assert stripped == []


def test_scrubber_removes_long_single_quoted_spans():
    out = guards.scrub("They said 'one two three four five six seven eight' and 'short one'. Don't stop.", DASH)
    assert "one two three" not in out and "'short one'" in out and "Don't" in out
