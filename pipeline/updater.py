"""Updater agent (taxonomy builder). Plain Python, no LLM.

Runs only on review items a human approved. Adds keywords with citations,
creates new categories as "candidate", links provisions, bumps the version
and refreshes the search terms.
"""

import re

from pipeline import db, terms


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _add_citation(cites, cite):
    if not any(c.get("paper_id") == cite["paper_id"] for c in cites):
        cites.append(cite)
    return cites


def apply(item_id):
    """Apply one approved review item to the taxonomy. Returns a list of changes."""
    item = db.row("SELECT * FROM review_items WHERE id = ?", (item_id,))
    if item is None or item["status"] != "approved":
        raise ValueError(f"Review item {item_id} is not approved; the updater only runs on approved items")
    if item["applied"]:
        return []
    paper = db.row("SELECT * FROM papers WHERE id = ?", (item["paper_id"],))
    interp, decision = item["interpretation"], item["decision"]
    cite = {"paper_id": paper["id"], "doi": paper["doi"], "title": paper["title"],
            "review_item": item_id}
    changes = []

    # 1. category: existing, or a new candidate
    cat_id = decision["category"]
    if cat_id == "NEW":
        name = decision["new_category_name"]
        cat_id = slug(name)
        if db.row("SELECT id FROM categories WHERE id = ?", (cat_id,)) is None:
            db.execute("INSERT INTO categories VALUES(?,?,?,?,?,?,?,?)",
                       (cat_id, name, decision["new_category_definition"], "candidate",
                        db.dumps([cite]), db.dumps([]), f"review item {item_id}", db.now()))
            changes.append(f"new candidate category '{name}' ({cat_id})")
    cat = db.row("SELECT * FROM categories WHERE id = ?", (cat_id,))
    if cat is None:
        raise ValueError(f"Category {cat_id} does not exist")
    db.execute("UPDATE categories SET citations = ? WHERE id = ?",
               (db.dumps(_add_citation(cat["citations"], cite)), cat_id))

    # 2. keywords the reviewer ticked, each with a citation and its quote
    quotes = {k["term"]: k["quote"] for k in interp["keywords"]}
    for term in decision["keywords"]:
        kw_cite = dict(cite, quote=quotes.get(term, ""))
        existing = db.row("SELECT * FROM keywords WHERE lower(term) = lower(?) AND category_id = ?",
                          (term, cat_id))
        if existing:
            db.execute("UPDATE keywords SET citations = ? WHERE id = ?",
                       (db.dumps(_add_citation(existing["citations"], kw_cite)), existing["id"]))
            changes.append(f"citation added to keyword '{term}'")
        else:
            db.execute("INSERT INTO keywords(term, category_id, citations, origin, added_at) "
                       "VALUES(?,?,?,?,?)", (term, cat_id, db.dumps([kw_cite]),
                                             f"approved from {paper['id']}", db.now()))
            changes.append(f"keyword '{term}' added under {cat_id}")

    # 3. provision link ("may be relevant", never a finding)
    prov = interp.get("provision", "none")
    if prov and prov != "none":
        cat = db.row("SELECT * FROM categories WHERE id = ?", (cat_id,))
        provs = cat["provisions"]
        if not any(p["provision"] == prov and p["paper_id"] == paper["id"] for p in provs):
            provs.append({"provision": prov, "paper_id": paper["id"]})
            db.execute("UPDATE categories SET provisions = ? WHERE id = ?", (db.dumps(provs), cat_id))
            changes.append(f"provision link {prov} on {cat_id}")

    # 4. version, rules, search terms
    version = bump_version()
    db.execute("UPDATE review_items SET applied = 1 WHERE id = ?", (item_id,))
    for c in changes:
        db.log("updater", "taxonomy change", paper["id"], c)
    db.log("updater", "version bumped", paper["id"], f"Taxonomy version {version}")
    for problem in validate():
        db.log("updater", "rule check failed", paper["id"], problem)
    terms.refresh()
    return changes


def apply_approved():
    """Apply every approved item not yet applied. Returns the item ids."""
    ids = [r["id"] for r in db.rows("SELECT id FROM review_items WHERE status = 'approved' "
                                    "AND applied = 0 ORDER BY id")]
    for i in ids:
        apply(i)
    return ids


def bump_version():
    version = db.get_setting("taxonomy_version", 1) + 1
    db.set_setting("taxonomy_version", version)
    return version


def validate():
    """Rule: a canonical category needs a definition and at least one citation."""
    problems = []
    for c in db.rows("SELECT * FROM categories WHERE status = 'canonical'"):
        if not (c["definition"] or "").strip():
            problems.append(f"Canonical category {c['id']} has no definition")
        if not c["citations"]:
            problems.append(f"Canonical category {c['id']} has no citation")
    return problems


def promote(cat_id, reviewer):
    """A human promotes a candidate to canonical. The rule is checked first."""
    c = db.row("SELECT * FROM categories WHERE id = ?", (cat_id,))
    if c is None or c["status"] != "candidate":
        raise ValueError(f"{cat_id} is not a candidate category")
    if not (c["definition"] or "").strip() or not c["citations"]:
        raise ValueError("A canonical category needs a definition and at least one citation")
    db.execute("UPDATE categories SET status = 'canonical' WHERE id = ?", (cat_id,))
    version = bump_version()
    db.log("reviewer", "promoted category", detail=f"{cat_id} to canonical by {reviewer or 'anonymous'}")
    db.log("updater", "version bumped", detail=f"Taxonomy version {version}")
    return version
