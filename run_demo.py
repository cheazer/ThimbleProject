"""Headless end to end run in mock mode. Starts the fake site, runs the whole
flow with scripted review decisions, checks the results, then stops the site.

Run from the repo root: python run_demo.py
Uses its own database file and port, so it does not touch pipeline/demo.db.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("RUN_DEMO_PORT", "8011"))
SITE = f"http://127.0.0.1:{PORT}"

os.environ["LLM_MODE"] = "mock"
os.environ["FAKE_SITE_URL"] = SITE
os.environ["DEMO_DB"] = str(Path(tempfile.gettempdir()) / "fairdigit_run_demo.db")
sys.path.insert(0, str(ROOT))

from pipeline import db, orchestrator, review, terms, updater  # noqa: E402


def step(text):
    print(f"\n== {text}")


def check(condition, text):
    assert condition, f"FAILED: {text}"
    print(f"   ok  {text}")


def start_site():
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "fake_site.main:app",
                             "--port", str(PORT), "--log-level", "warning"], cwd=ROOT)
    for _ in range(50):
        try:
            requests.get(f"{SITE}/admin/status", timeout=1)
            return proc
        except requests.RequestException:
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("Fake site did not start")


def item_for(paper_id):
    return db.row("SELECT * FROM review_items WHERE paper_id = ?", (paper_id,))


def errors(item):
    return [w["message"] for w in item["warnings"] if w["level"] == "error"]


def main():
    step("Start fake site and reset")
    site = start_site()
    try:
        db.reset_and_seed(SITE)
        requests.post(f"{SITE}/admin/reset", timeout=5)
        check(db.get_setting("taxonomy_version") == 1, "taxonomy starts at version 1")

        step("Run pipeline on batch 1")
        orchestrator.run_pipeline()
        reasons = {e["paper_ref"]: e["reason"] for e in db.rows("SELECT * FROM exclusions")}
        expected = {"syn-003": "no_match", "syn-004": "duplicate_doi", "syn-005": "out_of_range",
                    "syn-006": "retracted", "syn-007": "not_english", "syn-009": "tdm_opt_out"}
        for pid, reason in expected.items():
            print(f"   {pid}: {reason}")
        check(reasons == expected, "exclusion reasons are right")
        included = sorted(p["id"] for p in db.rows("SELECT id FROM papers"))
        check(included == ["syn-001", "syn-002", "syn-008"], f"included {included}")
        check(db.row("SELECT * FROM papers WHERE id = 'syn-009'") is None
              and db.row("SELECT * FROM events WHERE action = 'TDM opt-out respected' "
                         "AND paper_id = 'syn-009'") is not None,
              "premium paper syn-009 skipped and logged as TDM opt-out respected")

        step("Interpreter checks")
        check(errors(item_for("syn-001")) == [], "syn-001 passes all checks")
        check(any("word for word" in m for m in errors(item_for("syn-008"))),
              "quote check fires on syn-008 (made-up quote)")

        step("Publish batch 2 BEFORE any approval and run again")
        requests.post(f"{SITE}/admin/advance", timeout=5)
        orchestrator.run_pipeline()
        reasons = {e["paper_ref"]: e["reason"] for e in db.rows("SELECT * FROM exclusions WHERE active = 1")}
        check(reasons.get("syn-011") == "no_match", "syn-011 excluded: no match with current search terms")
        check(reasons.get("syn-010") == "tdm_opt_out", "premium paper syn-010 skipped")
        check(item_for("syn-012") is not None, "syn-012 included and interpreted")
        check(db.row("SELECT * FROM events WHERE agent = 'interpreter' AND action = 'retry' "
                     "AND paper_id = 'syn-012'") is not None, "broken JSON for syn-012 retried once")
        check(any("instructions to an AI" in w["message"] for w in item_for("syn-012")["warnings"]),
              "instruction-like text in syn-012 flagged")

        before_terms = {t["term"] for t in terms.search_terms()}
        before_kw = db.row("SELECT count(*) AS n FROM keywords")["n"]
        before_version = db.get_setting("taxonomy_version")

        step("Human review: first approvals")
        review.decide(item_for("syn-002")["id"], True, ["ephemeral content", "disappearing stories"],
                      "NEW", "demo reviewer")
        review.decide(item_for("syn-001")["id"], True, ["autoplay", "stopping cue"],
                      "endless_content", "demo reviewer")
        review.decide(item_for("syn-008")["id"], True, ["loss aversion"], "streaks_goals",
                      "demo reviewer", note="Dropped 'streak repair': quote not in abstract")
        review.decide(item_for("syn-012")["id"], False, [], "monetisation", "demo reviewer",
                      note="Rejected to show a rejection in the log")
        applied = updater.apply_approved()
        check(len(applied) == 3, "updater applied the 3 approved items only")

        cat = db.row("SELECT * FROM categories WHERE id = 'ephemeral_content'")
        check(cat is not None and cat["status"] == "candidate", "new category 'Ephemeral content' is a candidate")
        after_terms = {t["term"] for t in terms.search_terms()}
        added = sorted(after_terms - before_terms)
        print(f"   new search terms: {added}")
        check({"ephemeral content", "stopping cue", "loss aversion"} <= after_terms,
              "search terms grew with approved keywords")
        check("streak repair" not in after_terms, "keyword with the made-up quote was not added")
        check(db.row("SELECT count(*) AS n FROM keywords")["n"] > before_kw, "taxonomy keywords grew")
        check(db.get_setting("taxonomy_version") == before_version + 3, "version bumped once per item")
        check(item_for("syn-012")["applied"] == 0, "rejected item never reached the taxonomy")

        step("Run pipeline after approval")
        summary = orchestrator.run_pipeline()
        check("syn-011" in summary["rescreened"], "syn-011 picked up only after the approval")
        item = item_for("syn-011")
        check(item is not None and errors(item) == [], "syn-011 interpreted into ephemeral_content")

        step("Approve syn-011 and promote the new category")
        review.decide(item["id"], True, ["countdown timer"], "ephemeral_content", "demo reviewer")
        updater.apply_approved()
        updater.promote("ephemeral_content", "demo reviewer")
        check(db.row("SELECT status FROM categories WHERE id = 'ephemeral_content'")["status"] == "canonical",
              "ephemeral_content promoted to canonical (has definition and citations)")
        check(updater.validate() == [], "every canonical category has a definition and a citation")

        n_events = db.row("SELECT count(*) AS n FROM events")["n"]
        print(f"\nAll checks passed. Taxonomy version {db.get_setting('taxonomy_version')}, "
              f"{n_events} events logged in {db.db_path()}")
    finally:
        site.terminate()
        site.wait(timeout=10)


if __name__ == "__main__":
    main()
