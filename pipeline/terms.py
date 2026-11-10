"""Search terms: seed terms plus every keyword in the taxonomy."""

import re

from pipeline import db


def search_terms():
    """Return [{term, origin}] with each term once. Seed terms first."""
    out, seen = [], set()

    def add(term, origin):
        key = term.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append({"term": term, "origin": origin})

    for t in db.get_setting("seed_search_terms", []):
        add(t, "seed search term")
    for k in db.rows("SELECT term, category_id, origin FROM keywords ORDER BY id"):
        add(k["term"], f"{k['origin']} ({k['category_id']})")
    return out


def match_terms(text, terms):
    """Term dicts found in text. Case insensitive, must start at a word boundary
    (so "streak" matches "streaks" but not "bestreak")."""
    text = text.lower()
    return [t for t in terms if re.search(r"(?<!\w)" + re.escape(t["term"].lower()), text)]


def refresh():
    """Store a snapshot of the current terms and log what changed."""
    old = set(db.get_setting("search_terms_snapshot", []))
    new = [t["term"] for t in search_terms()]
    added = [t for t in new if t not in old]
    db.set_setting("search_terms_snapshot", new)
    if added:
        db.log("updater", "search terms refreshed", detail="Added: " + ", ".join(added))
    return added
