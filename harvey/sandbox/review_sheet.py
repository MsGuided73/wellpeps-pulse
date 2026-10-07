"""DEMO review sheet: what Pulse did with each hand-written demo post.

``scripts/seed_demo.py --review-sheet PATH`` (and the ``--claude`` run) writes a
Markdown sheet with, per post: the post (trimmed), triage category / subtype,
the situation and reply mode, the protocol decision, the Smart Patient's Guide
it should point to (title -> chapter, and whether the reply does), the final
reply, its filter tier, the reviewer verdict and the model that wrote it. DEMO tooling
only: it reads the throwaway demo database and the active (DEMO) config.
"""

from harvey import engagement

POST_CHARS = 220


def _cell(text: str) -> str:
    return " ".join(str(text or "").split()).replace("|", "/")


def _trim(text: str, limit: int = POST_CHARS) -> str:
    text = _cell(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def _mention_ids(state, posts: list[str]) -> list[tuple[str, int | None]]:
    async with state.connect() as db:
        async with db.execute("SELECT id, text FROM mentions ORDER BY id") as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
    out = []
    for post in posts:
        match = next((r["id"] for r in rows if (r["text"] or "").strip() == post.strip()), None)
        out.append((post, match))
    return out


def _decision(status: str, triage, situation) -> str:
    parts = [f"status {status}"]
    if triage is not None:
        parts.append(f"reply mode {engagement.reply_mode(triage)}")
        if triage.protocol_decision:
            route = f" -> {triage.protocol_route}" if triage.protocol_route else ""
            parts.append(f"protocol {triage.protocol_decision}{route}")
    return "; ".join(parts)


async def entries(state, posts: list[str]) -> list[dict]:
    """One dict per hand-written post (in the given order)."""
    out = []
    records = await engagement.recent_replies(state)
    for post, mid in await _mention_ids(state, posts):
        if mid is None:
            out.append({"post": _trim(post), "missing": True})
            continue
        mention = await state.get_mention(mid)
        triage = await state.get_triage(mid)
        draft = await state.get_latest_draft(mid)
        situation = engagement.situation_of(triage) if triage is not None else None
        status = getattr(mention.status, "value", mention.status)
        guide = None
        if triage is not None and situation is not None:
            guide = engagement.guide_view(situation, engagement.context_from(mention, records), triage, mention,
                                          draft.text if draft else "", list(draft.claim_ids) if draft else [])
        out.append({
            "guide": _guide_cell(guide),
            "id": mid, "platform": getattr(mention.platform, "value", mention.platform), "post": _trim(post),
            "category": (f"{getattr(triage.category, 'value', triage.category)}"
                         f"{' / ' + triage.subtype if triage.subtype else ''}") if triage else "-",
            "situation": situation.label if situation else "-",
            "decision": _decision(status, triage, situation),
            "reply": _cell(draft.text) if draft and draft.text else "(no reply)",
            "tier": (draft.tier or "-") if draft else "-",
            "verdict": (getattr(draft.review_verdict, "value", draft.review_verdict) or "-") if draft else "-",
            "reasons": [_cell(r) for r in (draft.review_reasons if draft else [])][:4],
            "model": (draft.model or "-") if draft else "-",
            "missing": False,
        })
    return out


def _guide_cell(view: dict | None) -> str:
    if not view:
        return "-"
    if view["mode"] == "forbidden":
        return f"none (excluded: {view.get('why') or 'excluded situation'})"
    how = "linked" if view["mode"] == "link" else "named, no link"
    done = "referenced" if view.get("satisfied") else ("MISSING" if view.get("drafted") else "no reply text")
    return f"{view['title']} -> {view['chapter']} ({how}; {done})"


def render(rows: list[dict], title: str = "DEMO review sheet") -> str:
    lines = [f"# {title}", "", f"{len(rows)} hand-written demo post(s).", ""]
    for n, row in enumerate(rows, 1):
        lines.append(f"## {n}. {row['post']}")
        if row.get("missing"):
            lines += ["", "- not ingested (skipped by the collector)", ""]
            continue
        lines += [
            "",
            f"- Platform / mention: {row['platform']} #{row['id']}",
            f"- Category: {row['category']}",
            f"- Situation: {row['situation']}",
            f"- Decision: {row['decision']}",
            f"- Guide: {row.get('guide', '-')}",
            f"- Tier: {row['tier']} | Reviewer verdict: {row['verdict']} | Model: {row['model']}",
            f"- Final reply: {row['reply']}",
        ]
        if row["reasons"]:
            lines.append("- Review notes: " + " // ".join(row["reasons"]))
        lines.append("")
    return "\n".join(lines)


async def build(state, posts: list[str], title: str = "DEMO review sheet") -> str:
    return render(await entries(state, posts), title)
