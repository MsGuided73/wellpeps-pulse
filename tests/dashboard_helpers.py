"""Shared setup for dashboard/auth tests: temp DB, fast hashing, users, login.

Not a conftest: tests import what they need explicitly.
"""

import asyncio
import shutil
from pathlib import Path

import yaml
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

import harvey.auth as auth
import harvey.dashboard as dashboard
from harvey import knowledge
from harvey.config import PulseConfig
from harvey.models import Draft, Mention, MentionStatus, Platform, ReviewVerdict, Triage
from harvey.state import StateManager

# Test-only credential (>= 12 chars); never a real password.
PASSWORD = "fixture-pass-phrase-01"

ADMIN = "admin@pulse.test"
REVIEWER = "reviewer@pulse.test"
CLINICAL = "clinical@pulse.test"
VIEWER = "viewer@pulse.test"
DEFAULT_USERS = ((ADMIN, "admin"), (REVIEWER, "reviewer"), (CLINICAL, "clinical"), (VIEWER, "viewer"))

CLEAN_REPLY = (
    "Disclosure: I work with WellPeps, so I am not neutral. A few things worth checking with any "
    "provider are whether you are actually reviewed by a licensed clinician, what follow-up is "
    "included, how dose adjustments are handled, and which pharmacy dispenses the medication."
)
CLEAN_IDS = ["CLM-R3-DISCLOSURE", "CLM-R7-PROVIDER-CHECKLIST"]
RED_REPLY = "Our compounded tirzepatide is clinically proven and guaranteed to work."


def run(coro):
    return asyncio.run(coro)


def fast_hashing(monkeypatch) -> None:
    """argon2 with tiny parameters so tests stay quick."""
    monkeypatch.setattr(auth, "HASHER", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    monkeypatch.setattr(auth, "_DUMMY_HASH", None)


class FakeNotifier:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.sent: list[str] = []

    async def send(self, text, blocks=None) -> bool:
        self.sent.append(text)
        return self.ok


def setup_app(tmp_path, monkeypatch, *, config: PulseConfig | None = None, users=DEFAULT_USERS,
              reviewer=None, notifier=None):
    """Point the dashboard at a fresh temp DB; returns (state, notifier)."""
    fast_hashing(monkeypatch)
    db_path = tmp_path / "pulse.db"
    state = StateManager(str(db_path))
    run(state.init_db())
    store = auth.AuthStore(state)
    for email, role in users:
        run(store.create_user(email, PASSWORD, role, name=email.split("@")[0].title()))
    monkeypatch.setattr(dashboard, "DB_PATH", db_path)
    dashboard.LOGIN_LIMITER.clear()
    notifier = notifier or FakeNotifier()
    cfg = config or PulseConfig()
    dashboard.app.dependency_overrides[dashboard.get_config] = lambda: cfg
    dashboard.app.dependency_overrides[dashboard.get_notifier] = lambda: notifier
    dashboard.app.dependency_overrides[dashboard.get_briefs_notifier] = lambda: notifier
    if reviewer is not None:
        dashboard.app.dependency_overrides[dashboard.get_reviewer] = lambda: reviewer
    return state, notifier


def teardown_app() -> None:
    dashboard.app.dependency_overrides.clear()
    dashboard.LOGIN_LIMITER.clear()


def client_for(email: str | None = None) -> tuple[TestClient, str]:
    """A TestClient, logged in as ``email`` when given; returns (client, csrf)."""
    client = TestClient(dashboard.app)
    if email is None:
        return client, ""
    resp = client.post("/api/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return client, resp.json()["csrf"]


def post(client: TestClient, csrf: str, path: str, body: dict | None = None):
    return client.post(path, json=body or {}, headers={"X-CSRF-Token": csrf})


# --- Data seeding ------------------------------------------------------------------


async def add_mention(state: StateManager, key: str, *, text: str = "", platform=Platform.REDDIT,
                      triage: dict | None = None, status: MentionStatus = MentionStatus.NEW,
                      title: str = "") -> int:
    mention_id, _ = await state.upsert_mention(Mention(
        platform=platform, external_id=key, title=title,
        url=f"https://www.reddit.com/r/test/comments/{key}/", text=text or f"post {key}",
    ))
    if triage is not None:
        await state.save_triage(Triage(mention_id=mention_id, **triage))
    path = {
        MentionStatus.NEW: [],
        MentionStatus.TRIAGED: [MentionStatus.TRIAGED],
        MentionStatus.DROPPED: [MentionStatus.DROPPED],
        MentionStatus.ESCALATED: [MentionStatus.ESCALATED],
        MentionStatus.IN_REVIEW: [MentionStatus.TRIAGED, MentionStatus.DRAFTED, MentionStatus.IN_REVIEW],
    }[status]
    for step in path:
        await state.set_mention_status(mention_id, step)
    return mention_id


async def add_review_item(state: StateManager, key: str = "rev1", *, text: str = CLEAN_REPLY,
                          claim_ids=None, tier: str = "green") -> int:
    mention_id = await add_mention(
        state, key, text="Is WellPeps legit? Thinking of signing up.",
        triage={"category": "question", "subject_type": "wellpeps", "reply_appropriate": True},
        status=MentionStatus.IN_REVIEW,
    )
    await state.add_draft(Draft(
        mention_id=mention_id, text=text, claim_ids=list(CLEAN_IDS if claim_ids is None else claim_ids),
        model="sonnet", filter_ok=tier != "red", tier=tier, review_verdict=ReviewVerdict.PASS,
    ))
    return mention_id


def approved_claims_dir(tmp_path: Path, approve: list[str]) -> Path:
    """Copy config/*.yaml to a temp dir with ``approve`` claims signed off."""
    target = tmp_path / "config"
    shutil.copytree(knowledge.config_dir(), target)
    data = yaml.safe_load((target / "claims.yaml").read_text(encoding="utf-8"))
    for claim in data["claims"]:
        if claim["id"] in approve:
            claim["approved_by"] = "Fixture Compliance Officer"
            claim["approved_at"] = "2026-09-01"
    (target / "claims.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return target
