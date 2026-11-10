"""Updater: only approved items, citations, candidates, canonical rule."""

import pytest

from pipeline import db, review, terms, updater


def add_item(paper_id="syn-900", category="endless_content", provision="DSA Art 25",
             keywords=(("stopping cue", "No stopping cue was shown."),), **extra):
    db.execute("INSERT INTO papers(id, source_id, doi, title, abstract) VALUES(?,?,?,?,?)",
               (paper_id, "fake_site", f"10.0000/{paper_id}", f"{paper_id} (synthetic)",
                "No stopping cue was shown."))
    interp = {"summary": "s", "category": category, "new_category_name": "",
              "new_category_definition": "", "provision": provision, "confidence": 0.5,
              "keywords": [{"term": t, "quote": q, "quote_ok": True, "term_found": True}
                           for t, q in keywords]}
    interp.update(extra)
    return db.execute("INSERT INTO review_items(paper_id, status, interpretation, warnings, created_at) "
                      "VALUES(?,?,?,?,?)", (paper_id, "pending", db.dumps(interp), "[]", db.now()))


def test_updater_refuses_unapproved_items(fresh_db):
    item = add_item()
    with pytest.raises(ValueError):
        updater.apply(item)
    review.decide(item, False, [], "endless_content", "tester")
    with pytest.raises(ValueError):
        updater.apply(item)
    assert updater.apply_approved() == []


def test_approved_keyword_added_with_citation_and_provision(fresh_db):
    item = add_item()
    review.decide(item, True, ["stopping cue"], "endless_content", "tester")
    updater.apply_approved()
    kw = db.row("SELECT * FROM keywords WHERE term = 'stopping cue'")
    assert kw["category_id"] == "endless_content"
    assert kw["citations"][0]["doi"] == "10.0000/syn-900"
    assert kw["citations"][0]["quote"] == "No stopping cue was shown."
    cat = db.row("SELECT * FROM categories WHERE id = 'endless_content'")
    assert {"provision": "DSA Art 25", "paper_id": "syn-900"} in cat["provisions"]
    assert db.get_setting("taxonomy_version") == 2
    assert "stopping cue" in [t["term"] for t in terms.search_terms()]


def test_unticked_keyword_is_not_added(fresh_db):
    item = add_item(keywords=(("stopping cue", "No stopping cue was shown."), ("made up", "x")))
    review.decide(item, True, ["stopping cue"], "endless_content", "tester")
    updater.apply_approved()
    assert db.row("SELECT * FROM keywords WHERE term = 'made up'") is None


def test_new_category_starts_as_candidate_and_reviewer_can_change_category(fresh_db):
    item = add_item(category="NEW", new_category_name="Ephemeral content",
                    new_category_definition="Short lived content.")
    review.decide(item, True, ["stopping cue"], "NEW", "tester")
    updater.apply_approved()
    cat = db.row("SELECT * FROM categories WHERE id = 'ephemeral_content'")
    assert cat["status"] == "candidate" and cat["citations"]

    item2 = add_item(paper_id="syn-901", category="NEW", new_category_name="Other",
                     new_category_definition="d")
    review.decide(item2, True, ["stopping cue"], "reengagement", "tester")
    updater.apply_approved()
    assert db.row("SELECT * FROM categories WHERE id = 'other'") is None
    assert db.row("SELECT * FROM keywords WHERE term = 'stopping cue' AND category_id = 'reengagement'")


def test_canonical_rule(fresh_db):
    db.execute("INSERT INTO categories VALUES(?,?,?,?,?,?,?,?)",
               ("bare", "Bare", "", "candidate", "[]", "[]", "test", db.now()))
    with pytest.raises(ValueError, match="definition and at least one citation"):
        updater.promote("bare", "tester")
    db.execute("UPDATE categories SET status = 'canonical' WHERE id = 'bare'")
    problems = updater.validate()
    assert any("bare has no definition" in p for p in problems)
    assert any("bare has no citation" in p for p in problems)


def test_decisions_are_logged(fresh_db):
    item = add_item()
    review.decide(item, False, [], "endless_content", "tester", note="off topic")
    ev = db.row("SELECT * FROM events WHERE agent = 'reviewer'")
    assert ev["action"] == "rejected" and "off topic" in ev["detail"]
