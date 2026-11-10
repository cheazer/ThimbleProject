"""Screening rules and TDM checks (no network)."""

import pytest

from pipeline.extractor import screen
from pipeline.sources import tdmrep_rule_for
from pipeline.terms import match_terms

TERMS = [{"term": "autoplay", "origin": "seed"}, {"term": "streak", "origin": "seed"}]


def paper(**overrides):
    base = {"doi": "10.0000/syn.900", "published": "2024-01-01", "status": "published",
            "language": "en", "title": "A test (synthetic)", "abstract": "We study autoplay."}
    base.update(overrides)
    return base


@pytest.mark.parametrize("overrides, known, reason", [
    ({}, {"10.0000/syn.900"}, "duplicate_doi"),
    ({"published": "2014-12-31"}, set(), "out_of_range"),
    ({"published": "2027-01-01"}, set(), "out_of_range"),
    ({"status": "retracted"}, set(), "retracted"),
    ({"language": "de"}, set(), "not_english"),
    ({"abstract": "Soil sensors on farms."}, set(), "no_match"),
])
def test_exclusion_reasons(overrides, known, reason):
    assert screen(paper(**overrides), known, TERMS)[0] == reason


def test_included_paper_returns_matched_terms():
    reason, matched = screen(paper(), set(), TERMS)
    assert reason is None
    assert [t["term"] for t in matched] == ["autoplay"]


def test_year_bounds_are_inclusive():
    assert screen(paper(published="2015-01-01"), set(), TERMS)[0] is None
    assert screen(paper(published="2026-12-31"), set(), TERMS)[0] is None


def test_term_match_word_start():
    assert match_terms("Long streaks matter", TERMS)
    assert not match_terms("bestreak", TERMS)


def test_tdmrep_most_specific_rule_wins():
    rules = [{"location": "/*", "tdm-reservation": 0},
             {"location": "/premium/*", "tdm-reservation": 1}]
    assert tdmrep_rule_for("/premium/paper/x", rules)["tdm-reservation"] == 1
    assert tdmrep_rule_for("/paper/x", rules)["tdm-reservation"] == 0
