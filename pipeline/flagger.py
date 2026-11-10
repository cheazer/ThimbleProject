"""Flag an app: match a typed description against taxonomy keywords.

Keyword matching only. The output is a list of signals for a lawyer to look
at, never a finding that anything is unlawful.
"""

from pipeline import db, terms

DISCLAIMER = "For legal review, not a finding."


def flag(description):
    """Return one entry per category with at least one matching keyword."""
    cats = {c["id"]: c for c in db.rows("SELECT * FROM categories")}
    kws = db.rows("SELECT * FROM keywords ORDER BY id")
    hits = {}
    for k in kws:
        if terms.match_terms(description, [{"term": k["term"]}]):
            hits.setdefault(k["category_id"], []).append(k)
    out = []
    for cat_id, matched in hits.items():
        c = cats[cat_id]
        out.append({
            "category": cat_id,
            "name": c["name"],
            "status": c["status"],
            "keywords": [k["term"] for k in matched],
            "provisions": sorted({p["provision"] for p in c["provisions"]}),
            "citations": sorted({x["paper_id"] for k in matched for x in k["citations"]}),
        })
    return sorted(out, key=lambda h: -len(h["keywords"]))
