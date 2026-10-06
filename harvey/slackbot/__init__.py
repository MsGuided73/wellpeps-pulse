"""Read-only #pulse-query Slack bot (Socket Mode).

Staff @mention the bot in #pulse-query with a market question. The pipeline
is plan -> execute -> answer -> guards, with every Claude call tool-less
through ``harvey.brain.Brain``:

- ``planner``: haiku turns the question (untrusted, nonce-delimited) into a
  strict ``QuerySpec``; anything invalid becomes the help reply.
- ``executor``: runs the spec through the existing deterministic aggregate
  code (``harvey.analytics``, ``harvey.pulse_store``). The model never writes
  SQL; the privacy floor (counts < 2 suppressed, sentiment needs 3) applies.
- ``answer``: sonnet words <= 120 words from the aggregate result JSON only,
  then ``guards`` strip invented numbers and scrub handles, links, e-mails
  and long quotes; a deterministic dashboard link is appended.
- ``audit``: per-day / per-user-hour limits and one ``actions`` row per query.
- ``app``: the thin Bolt wiring (``QueryBot`` holds the testable logic).

It never changes state: no approve, ack, post or escalate from Slack.
"""
