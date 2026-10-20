# Pulse dashboard: design prototype

A clickable **design prototype** of the next Pulse review desk. It is not wired to Pulse's database, auth or API: approvals, uploads and Operator HQ controls only change the page. It exists to agree the design before porting it into `harvey/web/`.

- **Views:** Pulse (summary + approval queue), Operator HQ (WellPeps' agents, posting mode, community permissions, guardrails) and Knowledge (documents the agent follows).
- **Design system:** Pulse Brand Style Guide v1.0 (`images/logo/Pulse-Brand-Style-Guide.pdf`): Segoe UI only, ink `#01264C`, action `#005FCC`, light-only, 44 px controls, calm status system (gray = routine, blue = worth a look, green = healthy, amber/red only for things someone must act on).
- **Product context:** `PRODUCT.md` (users, purpose, principles, accessibility).
- **Content:** the queue uses real Reddit posts collected by Pulse on 2026-10-09 with links to the threads; weekly numbers, charts and spend figures are illustrative.
- **Human posting:** every community is listen-only, so each post says a WellPeps rep posts the reply; Approve copies the reply, then "Mark as posted" records it.
- **AI Rewrite:** select text in a draft (or right-click a selection) to rewrite it friendlier, more professional, shorter, simpler or by your own instruction. `serve.py` runs it through the local Claude Code CLI (`claude -p`, subscription billing, API key stripped). In the real app this becomes an endpoint behind Pulse's login using `harvey/claude_billing.py`.

## Run

```bash
python design/pulse-dashboard/serve.py
```

Then open http://127.0.0.1:5180. Localhost only; no login.
