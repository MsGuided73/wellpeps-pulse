"""The "why is this urgent" line (harvey/explain.py) and the richer /api/urgent
items the dashboard's Urgent cards are built from. Dashboard-only: Slack
pages stay link + category (tests/test_escalation.py keeps that green)."""

from datetime import datetime, timedelta, timezone

import pytest

from harvey import knowledge
from harvey.explain import (
    CONTEXT_CHARS,
    EVIDENCE_CHARS,
    REASON_CHARS,
    excerpt,
    screen_evidence,
    urgency_explanation,
)
from harvey.models import AuditEvent, AuditEventType, Category, Escalation, MentionStatus, Triage, Urgency
from tests.dashboard_helpers import (
    REVIEWER,
    VIEWER,
    add_mention,
    client_for,
    post,
    run,
    setup_app,
    teardown_app,
)

ER_TEXT = ("Third week on semaglutide and honestly the nausea was bad all weekend, then I ended up "
           "in the Emergency  Room last night with stomach pain that would not stop at all.")


def _triage(reason: str, category=Category.ADVERSE_EVENT, urgency=Urgency.URGENT) -> Triage:
    return Triage(mention_id=1, relevant=True, category=category, urgency=urgency,
                  urgency_reason=reason)


# --- keyword override ------------------------------------------------------------------


def test_keyword_override_quotes_the_matched_text_not_the_regex():
    hit = knowledge.urgent_override(ER_TEXT)
    assert hit is not None
    _, pattern = hit

    why = urgency_explanation(ER_TEXT, _triage(f"override:{pattern}"))

    assert why["source"] == "keyword"
    assert why["text"].startswith('Matched: "')
    assert "Emergency Room" in why["text"]             # from the post, whitespace collapsed
    assert pattern not in why["text"]
    for regex_bit in ("\\b", "\\s", "(?", "[", "+"):
        assert regex_bit not in why["text"]


def test_keyword_context_window_is_bounded_and_marks_trimmed_ends():
    why = urgency_explanation(ER_TEXT, _triage("override:whatever"))

    quoted = why["text"][len('Matched: "'):-1]
    assert len(quoted) <= CONTEXT_CHARS + 2            # two ellipses at most
    assert quoted.startswith("…") and quoted.endswith("…")
    assert "Emergency Room" in quoted


def test_keyword_match_at_the_start_has_no_leading_ellipsis():
    text = "ICU tonight. " + "x" * 200
    why = urgency_explanation(text, _triage("override:\\bICU\\b"))
    assert why["text"].startswith('Matched: "ICU')


def test_keyword_override_that_no_longer_matches_never_shows_the_pattern():
    reason = "override:\\bsome\\s+retired\\s+rule\\b"
    why = urgency_explanation("nothing alarming here", _triage(reason))
    assert why["source"] == "keyword"
    assert "retired" not in why["text"] and "\\b" not in why["text"]
    assert why["text"]


def test_keyword_override_keeps_html_like_text_raw():
    text = "<b>went to the emergency room</b> & now <script>x</script>"
    why = urgency_explanation(text, _triage("override:x"))
    assert "<b>went to the emergency room</b>" in why["text"]   # escaping is the UI's job


def test_keyword_override_with_a_failed_triage_is_still_a_keyword():
    why = urgency_explanation(ER_TEXT, _triage("override:x; triage_failed"))
    assert why["source"] == "keyword"


# --- safety screen ------------------------------------------------------------------------


@pytest.mark.parametrize("flag,label", [
    ("adverse_event", "adverse event"), ("self_harm", "self-harm"), ("minor", "minor"),
])
def test_safety_screen_names_the_flag(flag, label):
    why = urgency_explanation("some post", _triage(f"safety_screen:{flag}; model said fine"))
    assert why == {"source": "safety_screen", "text": f"Independent safety check flagged: {label}"}


def test_safety_screen_adds_verbatim_evidence_only():
    text = "I'm 16 and want to order semaglutide without my parents knowing"
    triage = _triage("safety_screen:minor", category=Category.OTHER, urgency=Urgency.HIGH)

    quoted = urgency_explanation(text, triage, evidence="I'm 16")
    invented = urgency_explanation(text, triage, evidence="the user is a teenager")

    assert quoted["text"] == 'Independent safety check flagged: minor — "I\'m 16"'
    assert invented["text"] == "Independent safety check flagged: minor"


def test_safety_screen_evidence_is_capped():
    text = "a" * 400
    why = urgency_explanation(text, _triage("safety_screen:adverse_event"), evidence="a" * 300)
    quote = why["text"].split(' — "', 1)[1][:-1]
    assert len(quote) <= EVIDENCE_CHARS


def test_safety_screen_failure_is_explained_without_a_flag():
    why = urgency_explanation("post", _triage("safety_screen:failed", category=Category.OTHER,
                                              urgency=Urgency.HIGH))
    assert why["source"] == "safety_screen"
    assert "could not" in why["text"].lower()


# --- severe category, model, triage failed, manual ------------------------------------------------


def test_severe_category_uses_the_label_and_the_model_reason():
    why = urgency_explanation("post", _triage("severe_category: user threatens to sue over charges",
                                              category=Category.LEGAL_REGULATORY))
    assert why["source"] == "severe_category"
    assert why["text"] == ("Classified as a legal / regulatory threat by triage: "
                           "user threatens to sue over charges")


def test_severe_category_without_a_reason_and_long_reason_truncated():
    bare = urgency_explanation("post", _triage("severe_category", category=Category.PRIVACY))
    long = urgency_explanation("post", _triage("severe_category: " + "y" * 400,
                                               category=Category.BILLING_FRAUD))
    assert bare["text"] == "Classified as a privacy complaint by triage"
    reason = long["text"].split(": ", 1)[1]
    assert len(reason) <= REASON_CHARS and reason.endswith("…")


def test_model_reason_is_truncated_free_text():
    why = urgency_explanation("post", _triage("  Many   replies calling it a scam.  " + "z" * 300,
                                              category=Category.COMPLAINT))
    assert why["source"] == "model"
    assert why["text"].startswith("Many replies calling it a scam.")
    assert len(why["text"]) <= REASON_CHARS


def test_model_with_empty_reason_still_says_something():
    why = urgency_explanation("post", _triage("", category=Category.COMPLAINT, urgency=Urgency.HIGH))
    assert why["source"] == "model"
    assert why["text"] == "Rated high urgency by triage"


def test_triage_failed():
    why = urgency_explanation("post", _triage("triage_failed", category=Category.OTHER,
                                              urgency=Urgency.HIGH))
    assert why == {"source": "triage_failed", "text": "Triage failed — flagged for human review"}


def test_manual_escalation_names_the_actor():
    from_reason = urgency_explanation("post", _triage("manual: escalated by lee@pulse.test"))
    from_audit = urgency_explanation("post", _triage("model text", category=Category.QUESTION),
                                     manual_by="kim@pulse.test")
    assert from_reason == {"source": "manual", "text": "Escalated manually by lee@pulse.test"}
    assert from_audit == {"source": "manual", "text": "Escalated manually by kim@pulse.test"}


def test_accepts_a_plain_dict_triage_and_none():
    as_dict = urgency_explanation("post", {"urgency_reason": "triage_failed", "category": "other",
                                           "urgency": "high"})
    assert as_dict["source"] == "triage_failed"
    assert urgency_explanation("post", None) is None


# --- helpers --------------------------------------------------------------------------------------


def test_excerpt_collapses_whitespace_and_adds_an_ellipsis():
    assert excerpt("  a\n\n b\tc  ") == "a b c"
    long = excerpt("word " * 100)
    assert len(long) <= 220 and long.endswith("…")
    assert excerpt(None) == ""


def test_screen_evidence_reads_the_latest_triage_audit_event():
    events = [
        AuditEvent(mention_id=1, event=AuditEventType.TRIAGED, actor="triage",
                   verdict={"safety_screen": {"evidence": "old"}}),
        AuditEvent(mention_id=1, event=AuditEventType.EDITED, actor="x", verdict={}),
        AuditEvent(mention_id=1, event=AuditEventType.TRIAGED, actor="triage",
                   verdict={"safety_screen": {"evidence": "new"}}),
    ]
    assert screen_evidence(events) == "new"
    assert screen_evidence([]) == ""


# --- /api/urgent shape ------------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    state, notifier = setup_app(tmp_path, monkeypatch)
    yield state, notifier
    teardown_app()


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_api_urgent_items_carry_excerpt_why_and_post_metadata(env):
    state, _ = env
    pattern = knowledge.urgent_override(ER_TEXT)[1]
    mid = run(add_mention(state, "er1", text=ER_TEXT, title="Rough week",
                          triage={"category": "adverse_event", "urgency": "urgent", "relevant": True,
                                  "urgency_reason": f"override:{pattern}"},
                          status=MentionStatus.ESCALATED))
    run(state.create_escalation(Escalation(mention_id=mid, kind="adverse_event",
                                           sla_due_at=_utcnow() + timedelta(minutes=30))))
    client, _ = client_for(VIEWER)

    item = client.get("/api/urgent").json()["items"][0]

    assert {"excerpt", "title", "posted_at", "collected_at", "author_handle", "why",
            "platform"} <= set(item)
    assert item["excerpt"].startswith("Third week on semaglutide")
    assert "  " not in item["excerpt"] and len(item["excerpt"]) <= 220
    assert item["title"] == "Rough week"
    assert item["platform"] == "reddit"
    assert item["why"]["source"] == "keyword"
    assert pattern not in item["why"]["text"]


def test_api_urgent_marks_a_manual_escalation(env):
    state, _ = env
    mid = run(add_mention(state, "man1", text="Is WellPeps legit?",
                          triage={"category": "question", "urgency": "normal", "relevant": True,
                                  "urgency_reason": "just a question"},
                          status=MentionStatus.TRIAGED))
    client, csrf = client_for(REVIEWER)
    assert post(client, csrf, f"/api/mentions/{mid}/escalate", {"kind": "legal"}).status_code == 200

    item = client.get("/api/urgent").json()["items"][0]

    assert item["why"] == {"source": "manual", "text": f"Escalated manually by {REVIEWER}"}


def test_api_urgent_uses_verbatim_safety_screen_evidence(env):
    state, _ = env
    text = "Took two doses and my throat swelled shut, had to use my inhaler"
    mid = run(add_mention(state, "ss1", text=text,
                          triage={"category": "adverse_event", "urgency": "urgent", "relevant": True,
                                  "urgency_reason": "safety_screen:adverse_event"},
                          status=MentionStatus.ESCALATED))
    run(state.append_audit(AuditEvent(
        mention_id=mid, event=AuditEventType.TRIAGED, actor="triage",
        verdict={"safety_screen": {"adverse_event": True, "evidence": "my throat swelled shut"}},
        permalink="https://www.reddit.com/r/test/comments/ss1/")))
    run(state.create_escalation(Escalation(mention_id=mid, kind="adverse_event",
                                           sla_due_at=_utcnow() + timedelta(minutes=30))))
    client, _ = client_for(VIEWER)

    item = client.get("/api/urgent").json()["items"][0]

    assert item["why"]["text"] == ('Independent safety check flagged: adverse event — '
                                   '"my throat swelled shut"')


def test_mention_detail_includes_why(env):
    state, _ = env
    mid = run(add_mention(state, "d1", text="post", status=MentionStatus.TRIAGED,
                          triage={"category": "complaint", "urgency": "high", "relevant": True,
                                  "urgency_reason": "lots of angry replies"}))
    client, _ = client_for(VIEWER)

    detail = client.get(f"/api/mentions/{mid}").json()

    assert detail["why"] == {"source": "model", "text": "lots of angry replies"}


def test_keyword_context_does_not_cut_words_at_trimmed_edges():
    text = ("Pretty sure WellPeps shared my health info with advertisers. Started seeing ads "
            "everywhere right after my intake form. Is that even allowed under HIPAA?")
    why = urgency_explanation(text, _triage("override:x", category=Category.PRIVACY))
    quoted = why["text"][len('Matched: "'):-1].strip("…")
    words = text.split()
    assert quoted.split()[0] in words and quoted.split()[-1] in words
    assert "HIPAA" in quoted
