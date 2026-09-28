"""CLI: `pulse brief` and `pulse trends` (Phase 8)."""

from datetime import timedelta

import pytest

from harvey import cli, trends
from tests.pulse_helpers import WINDOW_END, WINDOW_START, add_triaged, fresh_state


def test_parser_accepts_brief_and_trends():
    parser = cli.build_parser()

    args = parser.parse_args(["brief", "--period", "weekly", "--force"])
    assert (args.period, args.force, args.func) == ("weekly", True, cli.cmd_brief)
    assert parser.parse_args(["brief"]).period == "daily"
    args = parser.parse_args(["trends", "--days", "3"])
    assert (args.days, args.func) == (3, cli.cmd_trends)
    with pytest.raises(SystemExit):
        parser.parse_args(["brief", "--period", "monthly"])


@pytest.mark.asyncio
async def test_trend_lines_show_terms_velocity_and_share(tmp_path):
    state = await fresh_state(tmp_path)
    for i in range(3):
        await add_triaged(state, f"t{i}", text="price hike again", posted_at=WINDOW_START + timedelta(hours=i),
                          competitor="Ro", subject_type="competitor")
    report = await trends.compute_trends(state, WINDOW_START, WINDOW_END)

    text = "\n".join(cli.trend_lines(report))

    assert "price hike" in text and "NEW" in text
    assert "Ro" in text and "100.0%" in text


def test_trend_lines_handle_an_empty_window():
    report = trends.TrendReport(window_start=WINDOW_START, window_end=WINDOW_END, baseline_days=28)
    assert any("No term" in line for line in cli.trend_lines(report))


def test_brief_lines_show_headline_and_cards_only():
    brief = {"id": 4, "period": "daily", "window_start": "2026-09-26T04:00:00", "status": "ok",
             "created": True, "headline": "Shipping talk climbs", "summary_md": "**Short** summary",
             "action_cards": [{"title": "Refresh FAQ", "owner_hint": "support", "urgency": "this_week"}]}

    text = "\n".join(cli.brief_lines(brief))

    assert "#4" in text and "Shipping talk climbs" in text and "Refresh FAQ" in text and "support" in text
