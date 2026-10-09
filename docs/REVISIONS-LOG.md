# WellPeps Pulse: Revisions Log

A running, plain-language list of every change to how Pulse behaves, for team
review. Newest first. Each entry says what changed, why, the source behind it,
and whether it still needs a WellPeps decision or sign-off.

**Status key:** **Live** = built and tested. **In progress** = being built now.
**Needs sign-off** = built (or agreed) but waiting on a named WellPeps owner.

The detailed rules live in `docs/RULES-OF-ENGAGEMENT.md`; this log is the
summary for discussion.

---

## 2026-10-09: Changes from the first live data pull

The first live Reddit pull (22 real posts) showed Pulse reviewing and paging
on posts that could never benefit WellPeps. Agreed changes:

### R-14. Three-gate review: relevant, allowed, worth it (Live)
- **Before:** every relevant-looking post with a question was drafted.
- **After:** a post reaches the review queue only if it (1) is relevant: it
  involves WellPeps, a competitor, a WellPeps program/treatment, or someone
  shopping for telehealth; (2) is allowed: community rules permit it and it
  is not an individual clinical question; and (3) is worth it: a reply could
  plausibly benefit WellPeps (choosing a provider, how it works, comparing
  price or programs, asking about WellPeps, or a guide chapter answers it).
  Everything else feeds trends and the language bank only.
- **Order matters:** safety and permission are checked before benefit, so
  benefit is never a reason to engage with someone vulnerable.
- **Why:** Pre-LegitScript plan ("Respond only where WellPeps can add value";
  "Don't respond merely because a keyword appeared"); first live pull.

### R-13. Side-effect posts that don't involve WellPeps (Live, needs sign-off)
- **Before:** an adverse event in any thread got the approved safety reply
  and paged the clinical owner.
- **After:**
  - WellPeps named, or the person appears to be a WellPeps patient: page the
    clinical owner and draft the approved safety reply (unchanged).
  - Serious (ER, hospitalization, liver, pancreas, gallbladder, allergic
    reaction, self-harm, emergency) with the provider not named: no page, no
    reply; listed on a daily **safety watch** a person scans.
  - Another provider named, or expected effects (shedding, nausea,
    puffiness, fatigue): nothing; trends only.
- **Why:** user decision 2026-10-09; first live pull paged on routine
  hair-shedding chatter and drafted an unsolicited WellPeps reply to a
  stranger's side-effect post.
- **Needs sign-off:** WellPeps clinical / compliance lead. This narrows the
  Approved Messaging Guide §10 and Competitor Protocol Example 4, which apply
  to adverse events "in any thread". Also confirm whether WellPeps has any
  duty to act on side effects it reads about involving other providers.

### R-12. Keyword escalations only on relevant posts (Live)
- **Before:** the emergency-keyword safety net ("lawsuit", "fraud",
  "gallstones"...) escalated any post containing the word.
- **After:** it fires only when the post involves WellPeps, a competitor, a
  WellPeps program/treatment or telehealth; legal, privacy and billing pages
  only when WellPeps is the subject.
- **Why:** first live pull escalated a religious-law post to legal and a
  business-news roundup as billing fraud.

### R-11. Buying-focused Reddit searches (Live)
- **Before:** 8 broad searches; about half the results were off-topic.
- **After:** exact-phrase searches aimed at people choosing or comparing
  providers; side-effect-heavy searches removed.
- **Why:** first live pull; saves Apify credit.

### R-15. Fixes found while testing R-12 to R-14 (Live)
- A person can still escalate any post by hand from the review desk (legal,
  adverse event or safety watch), even when it isn't about WellPeps; the
  automatic WellPeps-only rule no longer blocks a human's decision.
- Pages and the dashboard keep showing why a post was urgent (keyword, safety
  screen...) after a review gate is recorded.

### Open question for the team
- **General treatment-education posts** (e.g. "how do GLP-1s work?", no
  provider named, nothing about buying): under R-14 these are not drafted,
  because a reply is unlikely to benefit WellPeps. The Competitor Protocol's
  Example 10 treats such a post as worth an educational answer. Decide
  whether general education should count as "worth it".

### R-10. Suspected seeded posts flagged (Proposed)
- Several shopper posts appeared the same morning with similar wording and
  the same lesser-known providers named. Proposal: flag posts that look
  seeded for a person to check before WellPeps engages.

---

## 2026-10-07 to 2026-10-09: Live data, billing, guide refinements

### R-9. Live Reddit collection through Apify (Live; scheduling off pending legal sign-off)
- Pulse can pull public Reddit posts through Apify (`trudax/reddit-scraper-lite`),
  capped at 30 posts and $0.25 per run and $4 per month; Apify's account
  limit also applies. Live posts go to a separate live database, never the
  demo. Scheduled collection stays **off** until WellPeps' legal sign-off on
  scraping; pulls are by hand meanwhile. A slow run is stopped but the posts
  it already collected (and paid for) are kept.
- **Needs sign-off:** WellPeps legal (scraping of Reddit, and later Meta / TikTok).

### R-8. Who pays for Claude (Live)
- Local runs and demos use the Claude subscription; the deployed version
  uses the Anthropic API key automatically. API keys are never passed to the
  local Claude CLI, so billing can't silently switch.

### R-7. Guide only when a chapter helps (Live)
- **Before:** every answering reply had to include a Smart Patient's Guide.
- **After:** Pulse considers the guide every time and includes it only when a
  specific chapter answers the question; otherwise it is omitted and the
  reason recorded.
- **Why:** Derek Goldberg's review; Pre-LegitScript plan ("Not every comment
  gets a link"; "Don't mention WellPeps in every response").

### R-6. No guide in communities with unchecked rules (Live)
- A guide is a company resource offer, so none (not even by name) where a
  community's rules haven't been verified. Basis: Competitor Protocol §2,
  §10; Operations Manual §5.1.

### R-5. Chapter titles must be real (Live)
- A reply that cites a "chapter" not found word-for-word in the guides is
  flagged for review.

### R-4. Provider line only when relevant (Live)
- "A licensed provider determines whether treatment is appropriate" only on
  treatment, suitability, eligibility or results questions, not on process,
  availability, pricing or service answers. Basis: Approved Messaging Guide §3
  ("states the boundary when necessary").

### R-3. Program status only when asked (Live)
- The reviewer flags a "coming soon / available now" sentence the poster
  didn't ask for.

### R-2. Competitor complaints asking for alternatives get answered (Live, bug fix)
- A complaint about a competitor that asks "who else should I try?" was
  silently dropped. It now gets an answer under the competitor protocol;
  pure venting still gets none.

### R-1. Open items from Derek's review not yet built
- Block approval of replies in communities with unverified rules
  (drafting allowed, posting not).
- Clinical-only approval of safety replies (admins only with a clinical role).
- Escalation stages "action pending" and "resolved".
- Narrow the privacy phrase rule to record access / patient-status
  confirmation.
- Real-model evaluation set built from raw posts.
- Record the Competitor Protocol's approval status, approvers and date.
- Approved wording for official-account company voice.
