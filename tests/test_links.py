"""Public links registry (config/links.yaml), tracked UTM URLs, the link
rules in the compliance filter, and `pulse links check` (MockTransport only;
no network)."""

import asyncio
import json
import shutil

import httpx
import pytest
import yaml

from harvey import cli, knowledge, links
from harvey import state as state_module
from harvey.compliance import compliance_filter
from harvey.models import Mention, Platform
from harvey.state import StateManager

GLP1 = "https://wellpeps.com/smart-patient-guides/glp-1-weight-loss"
DISCLOSURE = "Disclosure: I work with WellPeps, so I am not neutral."
GUIDE_CLAIMS = ["CLM-R3-DISCLOSURE", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"]
# A real answer before the link: a link alone is a "naked link" (R43; Guide §14).
ANSWER = ("A few things worth checking with any provider are whether a licensed clinician reviews you and "
          "what follow-up is included.")


@pytest.fixture(autouse=True)
def _fresh_knowledge(monkeypatch):
    monkeypatch.delenv("PULSE_CONFIG_DIR", raising=False)
    knowledge.reload()
    yield
    knowledge.reload()


def _config_copy(tmp_path, monkeypatch, edit_links=None, edit_claims=None):
    target = tmp_path / "config"
    shutil.copytree(knowledge.config_dir(), target)
    for name, edit in (("links.yaml", edit_links), ("claims.yaml", edit_claims)):
        if edit is None:
            continue
        data = yaml.safe_load((target / name).read_text(encoding="utf-8"))
        edit(data)
        (target / name).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("PULSE_CONFIG_DIR", str(target))
    knowledge.reload()
    return target


def _mention(mention_id=42, url="https://www.reddit.com/r/Tirzepatide/comments/abc/title/",
             platform=Platform.REDDIT) -> Mention:
    return Mention(id=mention_id, platform=platform, text="post", url=url)


# --- Registry ---------------------------------------------------------------------


def test_registry_loads_guides_all_https_on_wellpeps_and_not_live():
    entries = knowledge.links()
    ids = {link.id for link in entries}
    assert {"LNK-GUIDES", "LNK-GUIDE-GLP-1-WEIGHT-LOSS", "LNK-GUIDE-SEXUAL-WELLNESS",
            "LNK-GUIDE-HAIR-RESTORATION", "LNK-GUIDE-HEALTHY-AGING-VITALITY", "LNK-GUIDE-NAD-THERAPY"} <= ids
    assert all(link.url.startswith("https://wellpeps.com/smart-patient-guides") for link in entries)
    # The guide pages 404 as of 2026-10-06: nothing may be marked live yet.
    assert not any(link.live for link in entries)
    assert knowledge.allowed_link_domains() == ["wellpeps.com"]


def test_every_linked_claim_points_at_a_registry_link():
    by_id = knowledge.links_by_id()
    linked = [c for c in knowledge.claims() if c.link_id]
    assert {c.id for c in linked} >= {"CLM-EDU-GUIDES", "CLM-EDU-GUIDE-GLP-1-WEIGHT-LOSS"}
    assert all(c.link_id in by_id for c in linked)


@pytest.mark.parametrize("url, problem", [
    ("http://wellpeps.com/smart-patient-guides/x", "https"),
    ("https://evil.example.com/smart-patient-guides", "allowed_domains"),
    ("https://wellpeps.com.evil.io/guide", "allowed_domains"),
    ("https://wellpeps.com/guide?utm_source=x", "query"),
    ("https://wellpeps.com/weight-loss/assessment", "R8"),
])
def test_registry_rejects_unsafe_urls(tmp_path, monkeypatch, url, problem):
    def edit(data):
        data["links"][0]["url"] = url
    _config_copy(tmp_path, monkeypatch, edit_links=edit)
    with pytest.raises(knowledge.KnowledgeError, match=problem):
        knowledge.links()


def test_registry_rejects_unknown_program(tmp_path, monkeypatch):
    def edit(data):
        data["links"][1]["programs"] = ["Underwater Basket Weaving"]
    _config_copy(tmp_path, monkeypatch, edit_links=edit)
    with pytest.raises(knowledge.KnowledgeError, match="unknown programs"):
        knowledge.links()


def test_subdomain_of_an_allowed_domain_is_allowed():
    assert knowledge.host_allowed("www.wellpeps.com", ["wellpeps.com"])
    assert not knowledge.host_allowed("notwellpeps.com", ["wellpeps.com"])


def test_claim_with_unknown_link_id_fails_to_load(tmp_path, monkeypatch):
    def edit(data):
        data["claims"][0]["link_id"] = "LNK-NOPE"
    _config_copy(tmp_path, monkeypatch, edit_claims=edit)
    with pytest.raises(knowledge.KnowledgeError, match="LNK-NOPE"):
        knowledge.claims()


# --- UTM ------------------------------------------------------------------------------


def test_tracked_url_adds_the_pulse_utm_scheme():
    url = links.tracked_url(GLP1, platform="Reddit", mention_id=42, community="Tirzepatide")
    assert url == (GLP1 + "?utm_source=reddit&utm_medium=social_reply&utm_campaign=pulse"
                   "&utm_content=m42&utm_term=tirzepatide")


def test_tracked_url_keeps_other_params_and_never_duplicates_utm():
    url = links.tracked_url(GLP1 + "?ref=a&utm_source=old&utm_content=x&b=", platform="x", mention_id=7)
    assert url.startswith(GLP1 + "?ref=a&b=&utm_source=x&")
    assert url.count("utm_source=") == 1 and url.count("utm_content=") == 1
    assert "utm_term" not in url  # no community -> no utm_term


def test_tracked_url_encodes_values():
    url = links.tracked_url(GLP1, platform="google reviews", mention_id=3, community="a&b c")
    assert "utm_source=google+reviews" in url and "utm_term=a%26b+c" in url


def test_tracked_url_is_deterministic():
    args = {"platform": "reddit", "mention_id": 9, "community": "semaglutide"}
    assert links.tracked_url(GLP1, **args) == links.tracked_url(GLP1, **args)


@pytest.mark.parametrize("permalink, slug", [
    ("https://www.reddit.com/r/Tirzepatide/comments/abc/title/", "tirzepatide"),
    ("https://old.reddit.com/r/loseit/comments/x/", "loseit"),
    ("https://x.com/someone/status/1", ""),
    ("", ""),
])
def test_community_slug(permalink, slug):
    assert links.community_slug(permalink) == slug


def test_tracked_url_for_a_mention():
    link = knowledge.links_by_id()["LNK-GUIDE-GLP-1-WEIGHT-LOSS"]
    url = links.tracked_url_for(link, _mention())
    assert "utm_source=reddit" in url and "utm_content=m42" in url and "utm_term=tirzepatide" in url


# --- Finding links ---------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    GLP1, GLP1 + "/", GLP1 + "?utm_content=m1", "https://www.wellpeps.com/smart-patient-guides/glp-1-weight-loss",
    "wellpeps.com/smart-patient-guides/glp-1-weight-loss",
])
def test_registry_link_matches_ignoring_www_slash_and_query(raw):
    assert links.registry_link(raw).id == "LNK-GUIDE-GLP-1-WEIGHT-LOSS"


@pytest.mark.parametrize("raw", [
    "https://wellpeps.com/weight-loss", "https://evil.com/smart-patient-guides/glp-1-weight-loss",
    "https://wellpeps.com/smart-patient-guides/glp-1-weight-loss-extra",
])
def test_non_registry_links_have_no_entry(raw):
    assert links.registry_link(raw) is None


def test_find_links_strips_trailing_punctuation():
    found = links.find_links(f"See ({GLP1}). Or www.example.org/x,")
    assert [u.raw for u in found] == [GLP1, "www.example.org/x"]
    assert found[0].link.id == "LNK-GUIDE-GLP-1-WEIGHT-LOSS" and found[1].link is None


def test_track_registry_links_rewrites_whatever_the_model_typed():
    text = f"Guide: {GLP1}?utm_source=made_up. Thanks."
    out = links.track_registry_links(text, _mention())
    assert "utm_source=made_up" not in out
    assert out.endswith("utm_content=m42&utm_term=tirzepatide. Thanks.")


def test_track_registry_links_leaves_other_links_alone():
    text = "See https://example.org/x for more."
    assert links.track_registry_links(text, _mention()) == text


def test_link_record_and_describe():
    url = links.tracked_url(GLP1, platform="reddit", mention_id=5, community="loseit")
    record = links.link_record(f"{DISCLOSURE} Guide: {url}.")
    assert record == {"id": "LNK-GUIDE-GLP-1-WEIGHT-LOSS", "label": "Smart Patient's Guide: GLP-1 Weight Loss",
                      "url": url, "utm_content": "m5", "utm_term": "loseit"}
    shown = links.describe(record)
    assert shown["live"] is False and shown["known"] is True
    assert links.link_record("no links here") is None
    assert links.describe(None) is None


# --- Compliance filter link rules ----------------------------------------------


def _hits(result, kind="links"):
    return [h for h in result.hits if h.kind == kind]


def test_link_outside_the_registry_is_red():
    result = compliance_filter(f"{DISCLOSURE} More at https://wellpeps.com/learn", "reddit", GUIDE_CLAIMS)
    assert result.tier == "red"
    assert any("not in the public links registry" in h.reason for h in _hits(result))


def test_registry_link_without_a_backing_claim_is_red():
    result = compliance_filter(f"{DISCLOSURE} Guide: {GLP1}", "reddit", ["CLM-R3-DISCLOSURE"])
    assert result.tier == "red"
    assert any("not backed by a cited claim" in h.reason for h in _hits(result))


def test_backed_registry_link_not_live_is_yellow():
    result = compliance_filter(f"{DISCLOSURE} {ANSWER} Guide: {GLP1}", "reddit", GUIDE_CLAIMS)
    assert result.ok is True and result.tier == "yellow"
    assert [(h.match, h.reason) for h in _hits(result)] == [("LNK-GUIDE-GLP-1-WEIGHT-LOSS", "link not live yet")]


def test_backed_live_registry_link_is_clean(tmp_path, monkeypatch):
    def edit(data):
        for link in data["links"]:
            link["live"] = True
    target = _config_copy(tmp_path, monkeypatch, edit_links=edit)
    # The guide claims also wait on the gated-download disclosure (FINALIZE, R42 yellow).
    result = compliance_filter(f"{DISCLOSURE} {ANSWER} Guide: {GLP1}", "reddit", GUIDE_CLAIMS)
    assert [h.rule_id for h in result.hits] == ["R42"], result.hits
    guide = yaml.safe_load((target / "engagement_guide.yaml").read_text(encoding="utf-8"))
    for item in guide["finalize"]:
        if item["key"] == "gated_download_disclosure":
            item["value"] = "Fixture: provided"
    (target / "engagement_guide.yaml").write_text(yaml.safe_dump(guide, sort_keys=False), encoding="utf-8")
    knowledge.reload()
    result = compliance_filter(f"{DISCLOSURE} {ANSWER} Guide: {GLP1}", "reddit", GUIDE_CLAIMS)
    assert result.tier == "green", result.hits


def test_words_inside_our_tracked_url_are_not_read_as_reply_wording():
    # A subreddit named after a drug lands in utm_term; R38 must not fire on it.
    url = links.tracked_url(GLP1, platform="reddit", mention_id=1, community="tirzepatide")
    result = compliance_filter(f"{DISCLOSURE} {ANSWER} Guide: {url}", "reddit", GUIDE_CLAIMS)
    assert "medication_name" not in {h.kind for h in result.hits}
    assert result.tier == "yellow"


def test_two_registry_links_still_break_max_links():
    text = f"{DISCLOSURE} {GLP1} and https://wellpeps.com/smart-patient-guides"
    result = compliance_filter(text, "reddit", GUIDE_CLAIMS + ["CLM-EDU-GUIDES"])
    assert result.tier == "red" and any(h.rule_id == "R6" for h in result.hits)


# --- pulse links check -----------------------------------------------------------


def _transport(handler):
    return httpx.MockTransport(handler)


def _run(coro):
    return asyncio.run(coro)


def test_check_links_reports_ok_and_failures():
    def handler(request):
        return httpx.Response(200 if request.url.path.endswith("glp-1-weight-loss") else 404)
    results = {r.id: r for r in _run(links.check_links(transport=_transport(handler)))}
    assert results["LNK-GUIDE-GLP-1-WEIGHT-LOSS"].ok and results["LNK-GUIDE-GLP-1-WEIGHT-LOSS"].status == 200
    assert not results["LNK-GUIDES"].ok and results["LNK-GUIDES"].status == 404


def test_check_links_falls_back_to_get_when_head_is_not_allowed():
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(405 if request.method == "HEAD" else 200)
    entry = knowledge.links_by_id()["LNK-GUIDES"]
    (result,) = _run(links.check_links([entry], transport=_transport(handler)))
    assert result.ok and methods == ["HEAD", "GET"]


def test_check_links_follows_redirects():
    def handler(request):
        if request.url.host == "wellpeps.com":
            return httpx.Response(301, headers={"location": "https://www.wellpeps.com" + request.url.path})
        return httpx.Response(200)
    entry = knowledge.links_by_id()["LNK-GUIDES"]
    (result,) = _run(links.check_links([entry], transport=_transport(handler)))
    assert result.ok and result.final_url.startswith("https://www.wellpeps.com/")


def test_check_links_records_network_errors():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)
    entry = knowledge.links_by_id()["LNK-GUIDES"]
    (result,) = _run(links.check_links([entry], transport=_transport(handler)))
    assert not result.ok and result.status is None and result.error == "ConnectError"


def test_suggestions_never_edit_but_say_what_to_flip():
    ok = links.LinkCheck("LNK-A", "https://wellpeps.com/a", False, 200, True)
    broken = links.LinkCheck("LNK-B", "https://wellpeps.com/b", True, 404, False)
    lines = links.suggestions([ok, broken])
    assert "LNK-A" in lines[0] and "live: true" in lines[0]
    assert "LNK-B" in lines[1] and "live: false" in lines[1]


def test_cli_links_check_prints_records_and_leaves_yaml_alone(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(state_module, "DB_PATH", tmp_path / "pulse.db")
    before = (knowledge.config_dir() / "links.yaml").read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        cli.cmd_links_check(None, transport=_transport(lambda request: httpx.Response(200)))

    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "LNK-GUIDE-GLP-1-WEIGHT-LOSS" in out and "set `live: true`" in out
    assert (knowledge.config_dir() / "links.yaml").read_text(encoding="utf-8") == before
    stored = json.loads(_run(StateManager(str(tmp_path / "pulse.db")).get_setting(links.CHECK_SETTING)))
    assert stored["checked_at"] and len(stored["results"]) == len(knowledge.links())
    assert all(r["ok"] for r in stored["results"])


def test_cli_links_check_exits_1_when_a_link_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(state_module, "DB_PATH", tmp_path / "pulse.db")
    with pytest.raises(SystemExit) as exit_info:
        cli.cmd_links_check(None, transport=_transport(lambda request: httpx.Response(404)))
    assert exit_info.value.code == 1
    assert "FAILED" in capsys.readouterr().out


def test_cli_parser_has_links_check():
    args = cli.build_parser().parse_args(["links", "check"])
    assert args.func is cli.cmd_links_check
