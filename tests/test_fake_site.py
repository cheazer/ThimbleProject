from fastapi.testclient import TestClient

from fake_site import main

client = TestClient(main.app)


def setup_function():
    client.post("/admin/reset")


def test_twelve_synthetic_papers():
    assert len(main.PAPERS) == 12
    for p in main.PAPERS.values():
        assert p["title"].endswith("(synthetic)")
        assert p["doi"].startswith("10.0000/syn.")


def test_listing_shows_batch_one_only_until_advanced():
    assert "syn-011" not in client.get("/").text
    client.post("/admin/advance")
    assert "syn-011" in client.get("/").text
    client.post("/admin/reset")
    assert "syn-011" not in client.get("/").text


def test_premium_opt_out_signals():
    assert "Disallow: /premium/" in client.get("/robots.txt").text
    rules = client.get("/.well-known/tdmrep.json").json()
    assert {"location": "/premium/*", "tdm-reservation": 1, "tdm-policy": "/terms"} in rules
    r = client.get("/premium/paper/syn-009")
    assert r.headers["tdm-reservation"] == "1"


def test_open_paper_has_meta_tags():
    r = client.get("/paper/syn-001")
    assert r.headers["tdm-reservation"] == "0"
    for name in ["citation_title", "citation_author", "citation_publication_date",
                 "citation_doi", "dc.rights"]:
        assert f'name="{name}"' in r.text


def test_unreleased_paper_is_404():
    assert client.get("/paper/syn-011").status_code == 404
