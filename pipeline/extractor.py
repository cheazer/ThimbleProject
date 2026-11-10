"""Extractor agent: finds new papers, checks TDM opt-outs, screens with fixed rules.

No LLM here. Every exclusion is stored with its reason.
"""

from pipeline import db, terms
from pipeline.sources import TDMOptOut

YEAR_MIN, YEAR_MAX = 2015, 2026

REASONS = {
    "tdm_opt_out": "TDM opt-out respected",
    "duplicate_doi": "Duplicate DOI",
    "out_of_range": f"Published outside {YEAR_MIN} to {YEAR_MAX}",
    "retracted": "Retracted",
    "not_english": "Not in English",
    "no_match": "No match with current search terms",
}


def screen(paper, known_dois, term_list):
    """Apply the screening rules in order.

    Returns (reason, detail) for an exclusion, or (None, matched_terms) to include.
    """
    if paper["doi"] and paper["doi"] in known_dois:
        return "duplicate_doi", f"DOI {paper['doi']} already stored"
    year = int(paper["published"][:4]) if paper["published"][:4].isdigit() else 0
    if not YEAR_MIN <= year <= YEAR_MAX:
        return "out_of_range", f"Published {paper['published'] or 'unknown'}"
    if paper.get("status", "").lower() == "retracted":
        return "retracted", "Publisher marks this paper as retracted"
    if paper.get("language", "").lower() not in ("en", "eng", "english"):
        return "not_english", f"Language {paper.get('language') or 'unknown'}"
    matched = terms.match_terms(paper["title"] + " " + paper["abstract"], term_list)
    if not matched:
        return "no_match", f"Checked {len(term_list)} search terms"
    return None, matched


def _exclude(source_id, ref, reason, detail, doi=""):
    db.execute("INSERT INTO exclusions(source_id, paper_ref, url, doi, title, reason, detail, active, time) "
               "VALUES(?,?,?,?,?,?,?,1,?)",
               (source_id, ref["id"], ref["url"], doi, ref["title"], reason, detail, db.now()))
    db.log("extractor", REASONS[reason], ref["id"], detail)


def _store(source_id, paper, matched):
    db.execute("INSERT INTO papers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (paper["id"], source_id, paper["doi"], paper["title"], db.dumps(paper["authors"]),
                paper["published"], paper["language"], paper["license"], paper["collection"],
                paper["url"], paper["abstract"], db.dumps(matched), db.now()))
    db.log("extractor", "included", paper["id"], "Matched: " + ", ".join(matched))


def _process(adapter, source_id, ref, term_list, known_dois):
    """Check, fetch and screen one ref. Returns (included, reason)."""
    allowed, signals = adapter.tdm_check(ref)
    if not allowed:
        _exclude(source_id, ref, "tdm_opt_out", "; ".join(signals))
        return False, "tdm_opt_out"
    try:
        paper = adapter.fetch(ref)
    except TDMOptOut as e:
        _exclude(source_id, ref, "tdm_opt_out", str(e))
        return False, "tdm_opt_out"
    reason, result = screen(paper, known_dois, term_list)
    if reason:
        _exclude(source_id, ref, reason, result, paper["doi"])
        return False, reason
    _store(source_id, paper, [t["term"] for t in result])
    known_dois.add(paper["doi"])
    return True, None


def run(adapter, source_id="fake_site"):
    """One extractor pass over a source. Returns a summary dict."""
    term_list = terms.search_terms()
    known_dois = {r["doi"] for r in db.rows("SELECT doi FROM papers")}
    seen = {r["id"] for r in db.rows("SELECT id FROM papers")}
    seen |= {r["paper_ref"] for r in db.rows("SELECT paper_ref FROM exclusions")}
    summary = {"included": [], "excluded": [], "rescreened": []}

    # 1. Papers excluded only for "no match" get another chance when the terms grow.
    for ex in db.rows("SELECT * FROM exclusions WHERE reason = 'no_match' AND active = 1 "
                      "AND source_id = ?", (source_id,)):
        ref = {"id": ex["paper_ref"], "url": ex["url"], "title": ex["title"]}
        if not adapter.tdm_check(ref)[0]:
            continue  # the source has opted out since, leave it alone
        paper = adapter.fetch(ref)
        reason, result = screen(paper, known_dois, term_list)
        if reason is None:
            db.execute("UPDATE exclusions SET active = 0 WHERE id = ?", (ex["id"],))
            db.log("extractor", "re-screened", ref["id"],
                   "Now matches new search terms: " + ", ".join(t["term"] for t in result))
            _store(source_id, paper, [t["term"] for t in result])
            known_dois.add(paper["doi"])
            summary["included"].append(ref["id"])
            summary["rescreened"].append(ref["id"])

    # 2. New papers since the cursor.
    cursor = db.get_setting(f"cursor:{source_id}", "")
    refs = adapter.list_new(cursor)
    db.log("extractor", "listed", detail=f"{len(refs)} new refs since cursor '{cursor or 'start'}', "
                                         f"{len(term_list)} search terms")
    for ref in refs:
        if ref["id"] not in seen:
            included, reason = _process(adapter, source_id, ref, term_list, known_dois)
            if included:
                summary["included"].append(ref["id"])
            else:
                summary["excluded"].append((ref["id"], reason))
        cursor = max(cursor, ref["posted"])
        db.set_setting(f"cursor:{source_id}", cursor)
    return summary
