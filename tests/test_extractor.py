"""Extractor against the fake site, run in-process with a TestClient.

The test may import fake_site; the pipeline code itself never does.
"""

from fastapi.testclient import TestClient

from fake_site import main as site
from pipeline import extractor
from pipeline.sources import FakeSiteAdapter, TDMOptOut


def make_adapter():
    client = TestClient(site.app)
    client.post("/admin/reset")
    return FakeSiteAdapter("http://testserver", http=client), client


def test_batch_one_screening(fresh_db):
    adapter, _ = make_adapter()
    summary = extractor.run(adapter)
    assert sorted(summary["included"]) == ["syn-001", "syn-002", "syn-008"]
    reasons = {pid: r for pid, r in summary["excluded"]}
    assert reasons == {
        "syn-003": "no_match", "syn-004": "duplicate_doi", "syn-005": "out_of_range",
        "syn-006": "retracted", "syn-007": "not_english", "syn-009": "tdm_opt_out",
    }
    assert fresh_db.get_setting("cursor:fake_site") == "2026-10-09"


def test_premium_skipped_with_all_signals(fresh_db):
    adapter, _ = make_adapter()
    extractor.run(adapter)
    ex = fresh_db.row("SELECT * FROM exclusions WHERE paper_ref = 'syn-009'")
    assert "robots.txt Disallow" in ex["detail"] and "tdmrep.json" in ex["detail"]
    assert fresh_db.row("SELECT * FROM events WHERE action = 'TDM opt-out respected'")
    # nothing from the premium paper is stored
    assert fresh_db.row("SELECT * FROM papers WHERE id = 'syn-009'") is None


def test_header_opt_out_is_respected_on_fetch():
    adapter, _ = make_adapter()
    ref = {"id": "syn-009", "title": "x", "url": "http://testserver/premium/paper/syn-009"}
    try:
        adapter.fetch(ref)
        assert False, "expected TDMOptOut"
    except TDMOptOut as e:
        assert "tdm-reservation" in str(e)


def test_cursor_means_second_run_finds_nothing(fresh_db):
    adapter, _ = make_adapter()
    extractor.run(adapter)
    summary = extractor.run(adapter)
    assert summary["included"] == [] and summary["excluded"] == []


def test_only_abstract_is_stored(fresh_db):
    adapter, _ = make_adapter()
    extractor.run(adapter)
    p = fresh_db.row("SELECT * FROM papers WHERE id = 'syn-001'")
    assert p["abstract"].startswith("This synthetic study")
    assert "Full text" not in p["abstract"]
