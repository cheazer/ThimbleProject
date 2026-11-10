def test_seed_loads_canonical_categories(fresh_db):
    cats = fresh_db.rows("SELECT * FROM categories")
    assert len(cats) == 7
    for c in cats:
        assert c["status"] == "canonical"
        assert c["definition"] and c["citations"]
    assert fresh_db.get_setting("taxonomy_version") == 1


def test_settings_round_trip(fresh_db):
    fresh_db.set_setting("cursor:fake_site", "2026-10-05")
    assert fresh_db.get_setting("cursor:fake_site") == "2026-10-05"
