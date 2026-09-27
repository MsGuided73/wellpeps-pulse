# Skills

Skills are Markdown files of WellPeps knowledge (products, competitors,
approved-claims guidance, brand voice) that the Brain can inject into an
agent's prompt via `Brain.load_skills_for_agent`. The agent-to-skill mapping
is `SKILL_MAP` in `harvey/brain.py`.

None exist yet: they are added in later phases (see `docs/PLAN.md`).

Rule: supplier costs, margins, or any other internal pricing never go in a
skill file. Skills end up inside prompts.
