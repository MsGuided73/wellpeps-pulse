# Pulse handoff: 2026-10-22

Where WellPeps Pulse stands after the review-desk merge, and what's left to finalize the agent. Read this first, then `CLAUDE.md`, `docs/RULES-OF-ENGAGEMENT.md` and `docs/REVISIONS-LOG.md` (R-16 to R-28 are the latest rounds).

## Update 2026-10-22 (read this first)

**Done today** (revisions log R-31 to R-38): passing-mention and no-useful-role skips; the on-topic check reads titles;
product list matches wellpeps.com (18 offerings, one monthly price, no membership fee, shipping included, labs
extra); Hair Restoration available (Derek); R8/R18 pre-certification phrase bans dropped; guide pages live;
Communities page (every community, link, clearance checklist, what is blocked); out of scope = no escalation and
no reply (Derek); narrower "personal medical question" tag; answer-first-then-boundary built but OFF; regression
set (`scripts/regression_check.py`, 16 posts, all pass); every Word/PDF upload converted to Markdown; harness
pins the chosen guide chapter, skips admin/other-platform sections, 12,000 characters.

**Waiting on the owner**
1. Approve the eleven Markdown library versions: Knowledge -> Library -> In review (#33-43).
2. Clear communities on the Communities page (all are "rules not checked"): this unlocks guides and links.
3. Start the worker (Operator HQ -> Start) for daily collection and drafting.

**Waiting on Derek**
1. Pricing statement CLM-PRICE-ALLIN and the comparison checklist CLM-CHECKLIST-COST-COMPARE (most price replies
   are held or rejected until one is approved).
2. Answer-first-then-boundary (`switching_protocol.answer_first_boundary`).
3. Advisory community rules (R-30).

**Next build steps** (memo "How Pulse's context harness works", Claude Docs): a situation playbook for the harness;
respect the document priority order and cap sections per document; learn from the review desk (Proposal E); extend
the harness to triage and the safety screen; label out-of-scope posts "out of scope" (they show "clinical route");
decide about the partner names in config/keywords.yaml; catalog sync (Proposal C); deploy (Coolify, Postgres).

**Working rules:** run the full suite before every commit and read pytest's own summary line (a piped exit code
hides failures); run `scripts/regression_check.py` before and after any prompt or model change (subscription only).

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
| Tests | `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` → 2530 passed, 20 skipped |

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
2. ~~Load WellPeps' documents into Knowledge.~~ **Done 2026-10-21:** the library holds Derek's documents, the Community Agent Voice, Offering Links and the five e-books; PDFs are read with `pypdf`. Two text-filled versions still wait for approval in Knowledge → Library (filter "In review"): "Why and When to Engage" (id 19) and the Conversation Selection Module (id 20, Must follow). Newer e-book drafts (GLP-1 v21, Hair v8, Healthy Aging v11, Modern Healthcare v4, Sexual Wellness v9) and a NAD+ guide are not uploaded yet.
3. ~~Derek's missing attachments.~~ **Done:** Community Agent Voice applied (R-26); Offering Links (id 18) is in the library but not yet checked against the link rules.
4. ~~Drafter voice.~~ **Done 2026-10-21:** warmer drafting prompt and examples, the voice guide always in view, no repeating a poster's health details (R-27/R-28). The context harness is **on** in `harvey.yaml` (code default off); `scripts/harness_compare.py` compares on a DB copy.
5. ~~Show selection details in the review desk~~ **Done 2026-10-20:** each Pulse queue card has a calm "Why this post" line (situation, decision and reason in plain words from `LABELS.pick` / `pickReason` in labels.js, posting hold with a link to Communities, activity warnings as "Heads-up"); the rep note no longer claims the community bans agents. Also fixed: the Pulse tab could stay on "Checking what needs you…" after a cold load (app.js now starts after every script has loaded).
6. **Review insights rules** in `config/insights.yaml` (the "Safety-first seeker" keywords are broad) and verify the new competitors' websites.
7. **Deployment:** apply `db/postgres/0005_review_desk.sql` (untested on a real Postgres) before deploying; deployed AI rewrite and drafting bill the Anthropic API key (`harvey/claude_billing.py`).
8. **Guide pages aren't live** on wellpeps.com yet (links return errors), so any reply with a guide link is held.
9. **Apify scheduling is on** (R-23): legal approved scraping under the current guardrails; Mark St. Pierre received oral approval from counsel on 2026-10-20, **written confirmation to follow** (file it with the source documents when it arrives). Scope: Reddit, Facebook groups and competitors' Trustpilot pages. `collectors.apify_reddit`: once a day, 30 posts and $0.25 per run, at most $5.00 a day (owner), $155 a month as a backstop. Scheduled runs happen only while the worker heartbeat runs (Operator HQ → Start, or `pulse run`).
10. **Add Instagram, Facebook, Threads and Trustpilot** alongside Reddit (owner request 2026-10-20). Read-only collectors; a rep still posts every reply by hand. Prefer official APIs: Meta Graph API for WellPeps' own Instagram and Facebook accounts (comments, tags, mentions; Instagram hashtag search is limited), the Threads API (own replies and mentions, keyword search), and Trustpilot's business API for WellPeps' own reviews. Third-party content (Facebook groups, competitor Trustpilot pages) has no official read access and means scraping: legal's 2026-10-20 sign-off covers Facebook groups and competitors' Trustpilot pages (under the current guardrails: public posts only, minimal author info, a permalink on every mention, capped runs). Each source is paid Apify usage with its own caps, so confirm budgets first. Needs: WellPeps' Meta business accounts and a Meta app with access approval (takes weeks; start early), a Trustpilot business plan decision (cost), `threads` added as a platform, per-platform rules in the filter (length, hashtags, Instagram links aren't clickable) and Derek's answer on company voice ("we") for owned channels and review sites. Verify each API's current terms before building.

11. **Lessons from the review desk (Proposal E, owner request 2026-10-21):** Pulse learns from reps' edits and rejections; a weekly tool-less job proposes short lessons with evidence, a person approves each in Knowledge → Lessons, approved ones reach the drafter and reviewer through the harness, anyone can review or retire them at any time. Lessons never add facts, claims, links, post text or usernames; max 30 active. Written up in the design doc for Derek; not built. Build after a few weeks of real edits with the harness on.
12. **Start the worker** (Operator HQ → Start, or `pulse run`) so the daily Reddit collection and drafting run.

## Waiting on Derek (sign-off)

- Proposals A, C, D and E in the design doc "Pulse knowledge design" (the harness, B, is built and on), and who approves lessons.

- Disclosure placement within the first two sentences (R-16) and the rewritten reply examples.
- ~~Company voice ("we") on owned channels and review sites.~~ Settled by Derek's Community Agent Voice: the official account may say "We're the WellPeps team." (R-26).
- Paraphrasing approved claims instead of verbatim.
- Hair Restoration status (Live Reference says open; the site says coming soon).
- An approved statement for "how long does approval take?".
- ~~Whether the "(it asks for your email)" sentence can be dropped.~~ Dropped in favor of Derek's wording "Here's a link to get a free copy if you'd like" (owner, 2026-10-20; R-26).
- The patient-dashboard claim (CLM-CAV-01, owner-confirmed) sits close to the Competitor Protocol §6 ban on "direct physician access": confirm the wording is fine (it never promises a response time).
- The per-community limit of 3 replies per 7 days (a proposal).
