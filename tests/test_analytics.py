"""Analytics tab (harvey/analytics.py + /api/analytics/*): bucketing in the org
timezone, filters, share of voice, privacy floors, SLA stats, validation,
permissions, SQL portability, and the static assets."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest

import harvey.dashboard as dashboard
from harvey import analytics
from harvey.db import dialect
from harvey.models import Escalation, Mention, MentionStatus, Platform, Triage
from tests.dashboard_helpers import VIEWER, client_for, run, setup_app, teardown_app

NY = "America/New_York"
NOW = datetime(2026, 9, 28, 16, 0)  # naive UTC; noon in New York


def _params(**kw):
    kw.setdefault("tz", NY)
    kw.setdefault("now", NOW)
    return analytics.parse_params(**kw)


def _row(at, **kw):
    base = {"id": 1, "platform": "reddit", "at": at, "subject_type": "", "competitor": "", "drug": "",
            "category": "other", "sentiment_score": 0.0, "title": "", "text": "", "phrases_json": "[]"}
    base.update(kw)
    return base


async def _add(state, key, *, at, platform=Platform.REDDIT, status=MentionStatus.TRIAGED,
               posted=True, relevant=True, text="", **triage):
    mention_id, _ = await state.upsert_mention(Mention(
        platform=platform, external_id=key, url=f"https://www.reddit.com/r/test/comments/{key}/",
        text=text or f"post {key}", posted_at=at if posted else None, collected_at=at,
    ))
    await state.save_triage(Triage(mention_id=mention_id, relevant=relevant, **triage))
    if status is MentionStatus.DROPPED:
        await state.set_mention_status(mention_id, MentionStatus.DROPPED)
    elif status is not MentionStatus.NEW:
        await state.set_mention_status(mention_id, MentionStatus.TRIAGED)
    return mention_id


# ── Parameters ──


def test_default_range_is_last_30_local_days_by_day():
    p = _params()
    assert p.days == 30 and p.bucket == "day"
    assert p.end == date(2026, 9, 29) and p.start == date(2026, 8, 30)
    assert p.start_utc == datetime(2026, 8, 30, 4, 0)  # EDT midnight
    assert len(analytics.bucket_keys(p)) == 30


def test_auto_bucket_switches_to_weeks_past_31_days():
    assert _params(days="31").bucket == "day"
    assert _params(days="90").bucket == "week"
    assert _params(days="90", bucket="day").bucket == "day"


def test_custom_range_is_inclusive():
    p = _params(start="2026-09-01", end="2026-09-07")
    assert (p.start, p.end, p.days) == (date(2026, 9, 1), date(2026, 9, 8), 7)
    assert p.to_dict()["to"] == "2026-09-07"


@pytest.mark.parametrize("kw", [
    {"start": "2026-13-01", "end": "2026-09-07"},
    {"start": "yesterday", "end": "2026-09-07"},
    {"start": "2026-09-07"},
    {"start": "2026-09-08", "end": "2026-09-07"},
    {"start": "2025-01-01", "end": "2026-01-05"},  # 370 days
    {"days": "0"}, {"days": "367"}, {"days": "abc"},
    {"bucket": "month"},
    {"platforms": ["reddit", "myspace"]},
    {"category": "gossip"},
    {"competitors": [f"c{i}" for i in range(9)]},
])
def test_bad_parameters_are_rejected(kw):
    with pytest.raises(analytics.AnalyticsError):
        _params(**kw)


def test_filters_accept_repeated_and_comma_joined_values():
    p = _params(platforms=["reddit,x", "x"], competitors=["Ro", "Hims & Hers,Ro"])
    assert p.platforms == ("reddit", "x") and p.competitors == ("Ro", "Hims & Hers")


def test_366_days_is_the_cap():
    assert _params(start="2025-09-01", end="2026-09-01").days == 366


# ── Buckets: timezone, DST, weeks ──


def test_day_buckets_follow_local_midnight_across_dst():
    p = _params(start="2026-10-31", end="2026-11-02")
    assert p.start_utc == datetime(2026, 10, 31, 4, 0)       # EDT
    assert p.end_utc == datetime(2026, 11, 3, 5, 0)          # EST after the fall-back
    assert p.utc(date(2026, 11, 2)) == datetime(2026, 11, 2, 5, 0)
    assert analytics.bucket_keys(p) == ["2026-10-31", "2026-11-01", "2026-11-02"]
    assert analytics.bucket_key(p, datetime(2026, 11, 1, 3, 30)) == "2026-10-31"  # 23:30 EDT
    assert analytics.bucket_key(p, datetime(2026, 11, 1, 4, 30)) == "2026-11-01"  # 00:30 EDT
    assert analytics.bucket_key(p, datetime(2026, 11, 2, 4, 30)) == "2026-11-01"  # 23:30 EST


def test_week_buckets_start_on_monday():
    p = _params(start="2026-09-01", end="2026-10-15")
    assert p.bucket == "week"
    keys = analytics.bucket_keys(p)
    assert keys[0] == "2026-08-31" and keys[1] == "2026-09-07" and keys[-1] == "2026-10-12"
    assert all(date.fromisoformat(k).weekday() == 0 for k in keys)
    assert analytics.bucket_key(p, datetime(2026, 9, 7, 3, 0)) == "2026-08-31"   # Sun 23:00 local
    assert analytics.bucket_key(p, datetime(2026, 9, 7, 5, 0)) == "2026-09-07"   # Mon 01:00 local


# ── Pure aggregates ──


def test_volume_stacks_categories_and_folds_small_series():
    p = _params(start="2026-09-01", end="2026-09-03")
    day1, day2 = datetime(2026, 9, 1, 15), datetime(2026, 9, 2, 15)
    current = [_row(day1, category="complaint"), _row(day1, category="complaint"),
               _row(day2, category="praise"), _row(day2, category="praise"),
               _row(day2, category="question"),                       # 1 mention: folds into other
               _row(day2, category="adverse_event"), _row(day1, category="privacy")]
    out = analytics.volume(p, current, [_row(day1)] * 5)
    by_key = {s["key"]: s for s in out["series"]}
    assert by_key["complaint"]["values"] == [2, 0, 0]
    assert by_key["safety_legal"]["total"] == 2
    assert "question" not in by_key and by_key["other"]["total"] == 1
    assert out["totals"] == [3, 4, 0] and out["change_pct"] == 40.0
    assert "up 40%" in out["takeaway"]


def test_volume_by_platform_and_bad_by():
    p = _params(start="2026-09-01", end="2026-09-01")
    at = datetime(2026, 9, 1, 15)
    out = analytics.volume(p, [_row(at, platform="x"), _row(at, platform="x"), _row(at, platform="bbb")], [],
                           by="platform")
    assert [(s["key"], s["total"]) for s in out["series"]] == [("x", 2), ("other", 1)]
    with pytest.raises(analytics.AnalyticsError):
        analytics.volume(p, [], [], by="author")


def test_share_of_voice_sums_to_one_and_folds_small_brands():
    p = _params(start="2026-09-01", end="2026-09-02")
    d1, d2 = datetime(2026, 9, 1, 15), datetime(2026, 9, 2, 15)
    current = ([_row(d1, subject_type="wellpeps")] * 3 + [_row(d1, competitor="Ro")] * 2 +
               [_row(d2, competitor="Ro")] * 2 + [_row(d2, competitor="Eden")] +          # Eden: 1 -> Other
               [_row(d2, subject_type="category")])                                        # no brand
    previous = [_row(d1, subject_type="wellpeps")] + [_row(d1, competitor="Ro")] * 3
    out = analytics.share_of_voice(p, current, previous, anchors=["Ro", "Eden"])
    keys = [s["key"] for s in out["series"]]
    assert keys == ["WellPeps", "Ro", "Other"]
    for i, total in enumerate(out["bucket_totals"]):
        assert total and abs(sum(s["values"][i] for s in out["series"]) - 1) < 1e-9
    wellpeps = out["series"][0]
    assert wellpeps["share"] == 0.375 and wellpeps["prev_share"] == 0.25 and wellpeps["delta_pts"] == 12.5
    assert "up 12.5 pts" in out["takeaway"]


def test_sentiment_points_need_three_mentions():
    p = _params(start="2026-09-01", end="2026-09-02")
    d1, d2 = datetime(2026, 9, 1, 15), datetime(2026, 9, 2, 15)
    current = ([_row(d1, subject_type="wellpeps", sentiment_score=s) for s in (0.2, 0.4, 0.6)] +
               [_row(d2, subject_type="wellpeps", sentiment_score=-0.5)] * 2 +
               [_row(d1, competitor="Ro", sentiment_score=-0.4)] * 2)
    out = analytics.sentiment(p, current, [], anchors=["Ro"])
    wellpeps, ro = out["series"]
    assert wellpeps["values"] == [0.4, None] and wellpeps["n"] == [3, 2]
    assert wellpeps["mean"] == round((1.2 - 1.0) / 5, 3) and wellpeps["delta"] is None
    assert ro["values"] == [None, None] and ro["mean"] is None
    assert out["takeaway"].startswith("WellPeps averaged +0.04")


def test_complaint_heatmap_themes_and_min_count():
    p = _params(start="2026-09-01", end="2026-09-01")
    at = datetime(2026, 9, 1, 15)
    texts = ("shipping delay again", "the shipping delay continues", "a shipping delay this week")
    current = ([_row(at, competitor="Ro", category="complaint", text=t) for t in texts] +
               [_row(at, competitor="Ro", category="complaint", text="the price hike is steep")] +
               [_row(at, competitor="Ro", category="praise", text="shipping was fine")] +
               [_row(at, competitor="Eden", category="complaint", text="support never answered")])
    out = analytics.complaint_heatmap(p, current, anchors=["Ro", "Eden"])
    assert out["rows"] == ["Ro", "Other"]
    themes = [t["key"] for t in out["themes"]]
    assert themes == ["shipping"]  # price (1) and support (1) stay under the floor
    cell = out["cells"][0][0]
    assert cell["count"] == 3 and cell["terms"] == [{"term": "shipping delay", "count": 3}]
    assert out["cells"][1][0] == {"count": None, "suppressed": False, "terms": []}
    assert "Ro × shipping" in out["takeaway"]


def test_themes_of_uses_text_and_phrases():
    assert analytics.themes_of(_row(NOW, text="refill stuck", phrases_json='["price hike"]')) == \
        ["price", "refills"]
    assert analytics.themes_of(_row(NOW, text="meh")) == ["other"]


def test_emerging_terms_flag_new_and_respect_min_count():
    p = _params(start="2026-09-01", end="2026-09-07")
    at = datetime(2026, 9, 3, 15)
    variants = ("oral wegovy pills", "oral wegovy tablets")
    current = [_row(at, id=i, text=variants[i % 2] + " are here") for i in range(4)] + \
              [_row(at, id=9, text="lonely unique phrase")]
    baseline = [_row(at - timedelta(days=10), text="wegovy shots")]
    out = analytics.emerging(p, current, baseline, baseline_days=28, min_count=1)
    terms = {t["term"]: t for t in out["terms"]}
    assert terms["oral wegovy"]["is_new"] and terms["oral wegovy"]["count"] == 4
    assert "lonely unique phrase" not in terms  # min count is never below 2
    assert "example_ids" not in terms["oral wegovy"]


def test_emerging_terms_skip_bare_brand_names():
    p = _params(start="2026-09-01", end="2026-09-07")
    at = datetime(2026, 9, 3, 15)
    current = [_row(at, id=i, competitor="Hims & Hers", text=f"hims wellpeps refill delay {i}") for i in range(3)]
    terms = {t["term"] for t in analytics.emerging(p, current, [], baseline_days=28, min_count=2)["terms"]}
    assert "hims" not in terms and "wellpeps" not in terms
    assert any("refill delay" in t for t in terms)


def test_drug_momentum_change_and_suppression():
    p = _params(start="2026-09-01", end="2026-09-02")
    at = datetime(2026, 9, 1, 15)
    current = [_row(at, drug="Tirzepatide")] * 6 + [_row(at, drug="semaglutide")] * 2 + [_row(at, drug="NAD+")]
    previous = [_row(at, drug="tirzepatide")] * 4 + [_row(at, drug="semaglutide")]
    out = analytics.drug_momentum(p, current, previous)
    by = {d["drug"]: d for d in out["drugs"]}
    assert by["tirzepatide"]["total"] == 6 and by["tirzepatide"]["change_pct"] == 50.0
    assert by["tirzepatide"]["values"] == [6, 0]
    assert by["semaglutide"]["change_pct"] is None      # previous period under the floor
    assert by["NAD+"]["suppressed"] and by["NAD+"]["values"] is None
    assert out["drugs"][0]["drug"] == "tirzepatide"
    assert "tirzepatide is up 50%" in out["takeaway"]


def test_escalation_stats_median_ack_and_breach_rate():
    p = _params(start="2026-09-01", end="2026-09-02")
    t0 = datetime(2026, 9, 1, 14)
    iso = datetime.isoformat
    rows = [
        {"kind": "legal", "created_at": iso(t0), "sla_due_at": iso(t0 + timedelta(minutes=15)),
         "acked_at": iso(t0 + timedelta(minutes=5)), "breached": 0, "platform": "reddit"},
        {"kind": "legal", "created_at": iso(t0), "sla_due_at": iso(t0 + timedelta(minutes=15)),
         "acked_at": iso(t0 + timedelta(minutes=45)), "breached": 0, "platform": "reddit"},  # acked late
        {"kind": "privacy", "created_at": iso(t0 + timedelta(days=1)), "sla_due_at": iso(t0 + timedelta(days=1, minutes=15)),
         "acked_at": iso(t0 + timedelta(days=1, minutes=10)), "breached": 0, "platform": "x"},
        {"kind": "privacy", "created_at": iso(t0 + timedelta(days=1)), "sla_due_at": iso(t0 + timedelta(days=1, minutes=15)),
         "acked_at": None, "breached": 1, "platform": "reddit"},
        {"kind": "legal", "created_at": iso(t0 - timedelta(days=5)), "sla_due_at": None,
         "acked_at": None, "breached": 0, "platform": "reddit"},                              # out of range
    ]
    open_rows = [{"sla_due_at": iso(NOW - timedelta(minutes=1)), "breached": 0},
                 {"sla_due_at": iso(NOW + timedelta(minutes=10)), "breached": 0}]
    out = analytics.escalation_stats(p, rows, open_rows)
    assert out["total"] == 4 and out["acked"] == 3
    assert out["median_ack_minutes"] == 10.0
    assert out["breached"] == 2 and out["breached_pct"] == 50.0
    assert out["open"] == 2 and out["open_breached"] == 1
    assert {s["key"]: s["values"] for s in out["series"]} == {"legal": [2, 0], "privacy": [0, 2]}
    filtered = analytics.escalation_stats(analytics.parse_params(
        start="2026-09-01", end="2026-09-02", platforms=["x"], tz=NY, now=NOW), rows, [])
    assert filtered["total"] == 1 and filtered["breached_pct"] == 0.0


# ── Against a database ──


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    state, _ = setup_app(tmp_path, monkeypatch)
    yield state
    teardown_app()


def _recent(hours: float) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)


def _seed_recent(state):
    run(_add(state, "w1", at=_recent(2), subject_type="wellpeps", category="praise", sentiment_score=0.5))
    run(_add(state, "w2", at=_recent(3), subject_type="wellpeps", category="praise", sentiment_score=0.3,
             posted=False))  # collected_at fallback
    run(_add(state, "r1", at=_recent(4), competitor="Ro", category="complaint", platform=Platform.X))
    run(_add(state, "r2", at=_recent(5), competitor="Ro", category="complaint", platform=Platform.X))
    run(_add(state, "d1", at=_recent(2), competitor="Ro", category="complaint", status=MentionStatus.DROPPED))
    run(_add(state, "n1", at=_recent(2), competitor="Ro", category="complaint", status=MentionStatus.NEW))
    run(_add(state, "i1", at=_recent(2), competitor="Ro", category="complaint", relevant=False))


def test_fetch_excludes_dropped_untriaged_and_irrelevant(app_db):
    _seed_recent(app_db)
    p = analytics.parse_params(days="7", tz=NY)
    rows = run(analytics.fetch_mentions(app_db, p, p.start_utc, p.end_utc))
    assert len(rows) == 4
    x_only = analytics.parse_params(days="7", tz=NY, platforms=["x"])
    assert len(run(analytics.fetch_mentions(app_db, x_only, p.start_utc, p.end_utc))) == 2
    ro = analytics.parse_params(days="7", tz=NY, competitors=["Ro"], category="complaint")
    assert len(run(analytics.fetch_mentions(app_db, ro, p.start_utc, p.end_utc))) == 2


def test_anchor_competitors_need_two_mentions(app_db):
    _seed_recent(app_db)
    run(_add(app_db, "e1", at=_recent(2), competitor="Eden", category="complaint"))
    assert run(analytics.anchor_competitors(app_db)) == ["Ro"]


def test_every_chart_endpoint_answers_a_viewer(app_db):
    _seed_recent(app_db)
    esc_mention = run(_add(app_db, "esc", at=_recent(1), subject_type="wellpeps", category="legal_regulatory"))
    created = _recent(1)
    run(app_db.create_escalation(Escalation(mention_id=esc_mention, kind="legal", created_at=created,
                                            sla_due_at=created + timedelta(minutes=15))))
    client, _ = client_for(VIEWER)
    for chart in analytics.CHARTS:
        resp = client.get(f"/api/analytics/{chart}?days=7&platform=reddit,x")
        assert resp.status_code == 200, (chart, resp.text)
        body = resp.json()
        assert body["takeaway"] and body["params"]["days"] == 7
    summary = client.get("/api/analytics/summary?days=7").json()["tiles"]
    assert summary["mentions"]["value"] == 5
    assert summary["open_escalations"] == {"value": 1, "breached": 1}
    options = client.get("/api/analytics/options").json()
    assert options["anchors"] == ["Ro"] and "reddit" in options["platforms"]
    assert "Hims & Hers" in options["competitors"]


def test_analytics_needs_a_session(app_db):
    client, _ = client_for()
    assert client.get("/api/analytics/volume").status_code == 401
    assert client.get("/api/analytics/options").status_code == 401


@pytest.mark.parametrize("query", [
    "from=2026-02-30&to=2026-03-02", "from=2025-01-01&to=2026-06-01", "days=400", "platform=myspace",
    "bucket=hour", "category=nope", "by=author",
])
def test_bad_query_is_a_400(app_db, query):
    client, _ = client_for(VIEWER)
    resp = client.get(f"/api/analytics/volume?{query}")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]


def test_unknown_chart_is_a_404(app_db):
    client, _ = client_for(VIEWER)
    assert client.get("/api/analytics/leaderboard").status_code == 404


def test_analytics_sql_is_postgres_portable():
    for sql in analytics.ALL_SQL:
        dialect.check_portable(sql)
        translated = dialect.to_postgres(sql)
        assert "?" not in translated
    assert "$4" in dialect.to_postgres(analytics.mentions_sql(2))


# ── Feed sort ──


def test_feed_sorts_newest_first_by_default_and_oldest_on_request(app_db):
    run(_add(app_db, "old", at=_recent(30)))
    run(_add(app_db, "new", at=_recent(1)))
    client, _ = client_for(VIEWER)
    newest = [m["external_id"] for m in client.get("/api/mentions").json()["items"]]
    oldest = [m["external_id"] for m in client.get("/api/mentions?sort=oldest").json()["items"]]
    assert newest == ["new", "old"] and oldest == ["old", "new"]
    assert client.get("/api/mentions?sort=random").status_code == 400


# ── Page and static assets ──


def test_feed_is_the_first_and_default_tab(app_db):
    client, _ = client_for(VIEWER)
    html = client.get("/").text
    order = [html.index(f'data-tab="{t}"') for t in
             ("feed", "urgent", "review", "analytics", "pulse", "usage", "users", "controls")]
    assert order == sorted(order)
    assert re.search(r'class="active" data-tab="feed"', html)
    assert re.search(r'id="feed" class="section active"', html)
    assert 'id="urgent-banner"' in html


@pytest.mark.parametrize("name", ["analytics.js", "charts.js", "feed.js"])
def test_analytics_scripts_are_served_and_local(app_db, name):
    client, _ = client_for()
    resp = client.get(f"/static/{name}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")
    text = resp.text
    assert not re.search(r"(?:url\(|href=|src=)['\"]?(?:https?:)?//", text)
    # The only absolute URL allowed is the SVG namespace (an identifier, never fetched).
    assert not re.search(r"https?://", text.replace("'http://www.w3.org/2000/svg'", ""))
    assert "style=" not in text and not re.search(r"\son[a-z]+=", text)
    assert "eval(" not in text and "new Function" not in text
    html = (dashboard.WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert html.index("/static/app.js") < html.index("/static/charts.js") < html.index("/static/analytics.js")
