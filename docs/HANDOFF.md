# Pulse handoff: 2026-10-20

Where WellPeps Pulse stands after the review-desk merge, and what's left to finalize the agent. Read this first, then `CLAUDE.md`, `docs/RULES-OF-ENGAGEMENT.md` and `docs/REVISIONS-LOG.md` (R-16 to R-21 are this round).

## Where things are

| What | Where |
|---|---|
| The app | `C:\dev\wellpeps-pulse` (package `harvey`), GitHub `MsGuided73/wellpeps-pulse`, branch `main` |
| Live data | `data/live.db` (gitignored): 43 Reddit mentions, 3 drafts waiting, 0 of 34 communities cleared |
| Run the dashboard (live data, local, no sign-in) | `.\scripts\run_live.ps1` → http://127.0.0.1:5556 (`-Pull` first pulls Reddit through Apify, capped) |
| Design prototype (reference only) | `design/pulse-dashboard/` (`python design/pulse-dashboard/serve.py` → :5180) |
| Product context | `design/pulse-dashboard/PRODUCT.md` |
| Brand | `images/logo/Pulse-Brand-Style-Guide.pdf` (Segoe UI, ink #01264C, action #005FCC, light-only, calm statuses) |
| WellPeps source documents | `C:\dev\W\docs\community-engagement-agent\` (Derek Goldberg's guides, protocol, module, gameplan) |
| Tests | `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` → 2401 passed, 20 skipped |

## What changed this round

- **R-16** Disclosure within the first two sentences, before any WellPeps mention; replies open by responding to the person (familiar voice).
- **R-17** Reddit collection sorted by relevance with an on-topic keyword gate (verified: 2 of 9 caught as off-topic, $0.068).
- **R-18 / R-19** Pain points & emerging ICPs in the brief and dashboard; stricter competitor-complaint rule, listed from one post; community attribution (r/IVIMHEALTH → Ivím Health). Competitors added: Gimme Care, PreventiveMD, Mondae, Lavender Sky Health, Riovie Health, YourMD Online.
- **R-20** WellPeps guidance of Oct 8–10 (conversation selection, HOLD on unverified facts, education drafted) plus session safeguards: competitor-owned communities listen-only; no replies to medical questions about a third party; no DMs/calls/private details; approval held until community permission is recorded; targets are warnings.
- **R-21** Review-desk APIs: AI rewrite (guardrails + compliance filter + audit + daily cap), knowledge documents (upload → admin approval → drafter reference), community permissions (DB overrides YAML), spend.
- **Dashboard** rebuilt in the Pulse style: Pulse (queue first) · Operator HQ · Knowledge; older tabs under More.

## Owner preferences (keep these)

- Replies should sound like a knowledgeable, friendly person ("Soul Garden" voice): react to the person first, disclose plainly within two sentences, no boilerplate closings.
- Point to a Smart Patient's Guide chapter when it genuinely helps; say it's free and asks for an email, in natural words.
- Calm interface: gray for routine, blue for worth a look, green for healthy, amber/red only for something someone must act on. No alarm styling for normal states.
- A WellPeps rep posts every reply by hand; Pulse never posts.
- Track complaints to find pain points and emerging customer types; competitor complaints count from a single public post.

## Open items to finalize the agent

1. **Clear communities to post.** All 34 are "unknown", so no reply can be approved. Record moderator permission (Operator HQ → Communities → Record permission) for the communities WellPeps will engage in.
2. **Load WellPeps' documents into Knowledge.** The library is empty; Pulse's rules live in `config/*.yaml`. Decide which source docs to upload as "must follow" / "reference" (needs `pypdf` for PDF text: currently PDFs store as "pending").
3. **Derek's three missing attachments** (emails of 2026-10-10): "Community Engagement – Offering Links", "Community Agent Voice", "Community Engagement – Why and When to Engage". Save them to `C:\dev\W\docs\community-engagement-agent\Knowledge Base\` and fold them in.
4. **Drafter voice is still stiffer than wanted** (e.g. the r/TeleMedicine draft glues approved sentences together and skipped the "Why medical review matters" guide). Tune prompts/examples and re-draft the 3 waiting posts to compare.
5. **Show selection details in the review desk:** `/api/mentions/{id}` returns `engagement.selection`, `engagement.posting_hold`, `engagement.warnings`; the new UI doesn't display them yet.
6. **Review insights rules** in `config/insights.yaml` (the "Safety-first seeker" keywords are broad) and verify the new competitors' websites.
7. **Deployment:** apply `db/postgres/0005_review_desk.sql` (untested on a real Postgres) before deploying; deployed AI rewrite and drafting bill the Anthropic API key (`harvey/claude_billing.py`).
8. **Guide pages aren't live** on wellpeps.com yet (links return errors), so any reply with a guide link is held.
9. **Apify scheduling:** WellPeps legal approved scraping under the current guardrails (owner, 2026-10-20). `collectors.apify_reddit` is still `enabled: false` until the owner confirms the schedule and budget (12-hour interval, 30 posts and $0.25 per run, $4.00 per month); scheduled runs also need the worker heartbeat running. Scope (owner, 2026-10-20): Reddit, Facebook groups and competitors' Trustpilot pages. Still to record: who signed off.
10. **Add Instagram, Facebook, Threads and Trustpilot** alongside Reddit (owner request 2026-10-20). Read-only collectors; a rep still posts every reply by hand. Prefer official APIs: Meta Graph API for WellPeps' own Instagram and Facebook accounts (comments, tags, mentions; Instagram hashtag search is limited), the Threads API (own replies and mentions, keyword search), and Trustpilot's business API for WellPeps' own reviews. Third-party content (Facebook groups, competitor Trustpilot pages) has no official read access and means scraping: legal's 2026-10-20 sign-off covers Facebook groups and competitors' Trustpilot pages (under the current guardrails: public posts only, minimal author info, a permalink on every mention, capped runs). Each source is paid Apify usage with its own caps, so confirm budgets first. Needs: WellPeps' Meta business accounts and a Meta app with access approval (takes weeks; start early), a Trustpilot business plan decision (cost), `threads` added as a platform, per-platform rules in the filter (length, hashtags, Instagram links aren't clickable) and Derek's answer on company voice ("we") for owned channels and review sites. Verify each API's current terms before building.

## Waiting on Derek (sign-off)

- Disclosure placement within the first two sentences (R-16) and the rewritten reply examples.
- Company voice ("we") on owned channels and review sites.
- Paraphrasing approved claims instead of verbatim.
- Hair Restoration status (Live Reference says open; the site says coming soon).
- An approved statement for "how long does approval take?".
- Whether the "(it asks for your email)" sentence can be dropped as the Selection Module allows.
- The per-community limit of 3 replies per 7 days (a proposal).
