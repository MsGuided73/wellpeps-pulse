"""The regression set (memo to Derek, 2026-10-22, item 4): its file and its checker.

The real run is scripts/regression_check.py (Claude on the subscription); these
tests keep the set well formed and the comparison honest. No network, no Claude.
"""

import pytest
import yaml

from harvey import regression
from harvey.regression import Observed


def test_the_set_loads_and_covers_the_memo_cases():
    cases = regression.load()
    ids = {c.id for c in cases}
    assert len(cases) >= 15
    # The three posts that got the clinical boundary reply on 2026-10-22, the out-of-scope and
    # passing-mention cases, and real clinical questions that must keep the boundary.
    assert {"R01-provider-checklist", "R02-which-pharmacies", "R03-own-doctor-process", "R09-separate-beds",
            "R10-paper-white-phone", "R06-dose-heart-racing", "R14-wellpeps-er"} <= ids
    assert all(c.expect for c in cases)


def test_matching_handling_has_no_mismatches():
    expect = {"decision": "RESPOND", "drafts": True, "escalates": False, "absent_intents": ["individual_clinical_concern"],
              "present_intents": ["general_information"]}
    observed = Observed("RESPOND", True, False, ("general_information", "comparison_request"))
    assert regression.mismatches(expect, observed) == []


def test_each_kind_of_change_is_reported():
    expect = {"decision": "RESPOND", "drafts": True, "escalates": False,
              "absent_intents": ["individual_clinical_concern"], "present_intents": ["general_information"]}
    observed = Observed("ROUTE", False, True, ("individual_clinical_concern",))
    problems = regression.mismatches(expect, observed)
    assert len(problems) == 5
    assert "decision ROUTE, expected RESPOND" in problems and "tagged individual_clinical_concern" in problems


@pytest.mark.parametrize("bad, message", [
    ({"id": "X", "text": "see https://example.com", "expect": {"drafts": True}}, "no links"),
    ({"id": "X", "text": "ask u/someone", "expect": {"drafts": True}}, "no links"),
    ({"id": "X", "text": "fine", "expect": {"decision": "MAYBE"}}, "decision must be"),
    ({"id": "X", "text": "fine", "expect": {"replies": True}}, "unknown expectation"),
])
def test_bad_cases_are_refused(tmp_path, bad, message):
    path = tmp_path / "set.yaml"
    path.write_text(yaml.safe_dump({"posts": [bad]}), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        regression.load(path)
