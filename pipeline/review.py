"""Human decisions on review items. Every decision is logged."""

from pipeline import db


def pending():
    return db.rows("SELECT r.*, p.title, p.doi, p.url, p.abstract FROM review_items r "
                   "JOIN papers p ON p.id = r.paper_id WHERE r.status = 'pending' ORDER BY r.id")


def decided():
    return db.rows("SELECT r.*, p.title FROM review_items r JOIN papers p ON p.id = r.paper_id "
                   "WHERE r.status != 'pending' ORDER BY r.decided_at DESC")


def decide(item_id, approve, keywords, category, reviewer, note="",
           new_category_name="", new_category_definition=""):
    """Record a reviewer's decision. `keywords` is the list of approved terms."""
    item = db.row("SELECT * FROM review_items WHERE id = ?", (item_id,))
    if item is None or item["status"] != "pending":
        raise ValueError(f"Review item {item_id} is not pending")
    interp = item["interpretation"]
    proposed_terms = [k["term"] for k in interp["keywords"]]
    unknown = set(keywords) - set(proposed_terms)
    if unknown:
        raise ValueError(f"Only proposed keywords can be approved, not {sorted(unknown)}")
    failed_quotes = [k["term"] for k in interp["keywords"]
                     if k["term"] in keywords and not k["quote_ok"]]
    decision = {
        "approved": approve,
        "keywords": keywords if approve else [],
        "dropped_keywords": [t for t in proposed_terms if t not in keywords] if approve else proposed_terms,
        "category": category,
        "category_changed": category != interp["category"],
        "new_category_name": new_category_name or interp["new_category_name"],
        "new_category_definition": new_category_definition or interp["new_category_definition"],
        "approved_despite_failed_quote": failed_quotes if approve else [],
        "note": note,
    }
    status = "approved" if approve else "rejected"
    db.execute("UPDATE review_items SET status = ?, decision = ?, reviewer = ?, decided_at = ? "
               "WHERE id = ?", (status, db.dumps(decision), reviewer or "anonymous", db.now(), item_id))
    detail = f"by {reviewer or 'anonymous'}; category {category}"
    if approve:
        detail += f"; keywords {keywords or 'none'}"
        if decision["dropped_keywords"]:
            detail += f"; dropped {decision['dropped_keywords']}"
        if failed_quotes:
            detail += f"; approved despite failed quote check: {failed_quotes}"
    if note:
        detail += f"; note: {note}"
    db.log("reviewer", status, item["paper_id"], detail)
    return decision
