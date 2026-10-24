"""Condition words and broader Reddit searches (R-45, owner request 2026-10-23).

"What about words that describe the condition they are trying to treat: balding; in hair
restoration." People write "I'm balding" or "my hairline is receding", not "finasteride". The
topic check (collection keyword filter and triage's off-topic gate both use knowledge.on_topic)
now knows the condition words, and the daily Reddit run searches every WellPeps product line.
No network, no Claude.
"""

import pytest

from harvey import knowledge
from harvey.config import load_config


@pytest.mark.parametrize("text", [
    "I'm 26 and balding, what are my options?",
    "My receding hairline is getting worse",
    "Anyone else dealing with thinning hair after pregnancy?",
    "male pattern baldness runs in my family",
    "Diagnosed with androgenetic alopecia last month",
    "I'm a Norwood 3 now",
    "My doctor says I have insulin resistance and prediabetes",
    "Erection problems at 35, too embarrassed to see my GP",
    "trouble getting hard lately",
    "Is low testosterone why I feel like this?",
])
def test_condition_words_are_on_topic(text):
    assert knowledge.on_topic(text)


@pytest.mark.parametrize("text", [
    "The Lakers won again last night",
    "My tires are wearing thin on the left side",
    "I could not find nada",
])
def test_unrelated_posts_stay_off_topic(text):
    assert not knowledge.on_topic(text)


def test_the_reddit_run_searches_every_product_line():
    cfg = load_config().collectors.apify_reddit
    searches = " ".join(cfg.searches).lower()
    assert len(cfg.searches) == 31
    for term in ("wellpeps", "balding", "receding hairline", "thinning hair", "minoxidil", "sildenafil",
                 "nad+", "sermorelin", "mondae", "gimme care", "membership fee"):
        assert term in searches, term
    assert cfg.max_charge_usd == 0.9 and cfg.daily_budget_usd == 5.0
    assert cfg.max_items // len(cfg.searches) >= 4          # at least 4 posts per search
