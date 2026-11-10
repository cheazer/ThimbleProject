from pipeline import flagger


def test_flag_matches_keywords_and_never_claims_a_finding(fresh_db):
    hits = flagger.flag("Videos autoplay and a streak counter keeps you coming back.")
    by_cat = {h["category"]: h for h in hits}
    assert by_cat["endless_content"]["keywords"] == ["autoplay"]
    assert "streak" in by_cat["streaks_goals"]["keywords"]
    assert flagger.DISCLAIMER == "For legal review, not a finding."


def test_flag_with_no_match(fresh_db):
    assert flagger.flag("A calculator app with a plain interface.") == []
