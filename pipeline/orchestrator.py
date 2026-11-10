"""Plain Python coordinator. Moves papers through the agents in a fixed order.

No LLM decides what happens next. An interpretation only reaches the
taxonomy after a human approves it.
"""

import os

from pipeline import db, extractor, interpreter, llm
from pipeline.sources import FakeSiteAdapter


def site_url():
    src = db.row("SELECT base_url FROM sources WHERE id = 'fake_site'")
    return os.environ.get("FAKE_SITE_URL") or (src["base_url"] if src else "http://localhost:8001")


def interpret_new():
    """Run the interpreter on included papers that have no review item yet."""
    categories = db.rows("SELECT id, name FROM categories")
    done = []
    for p in db.rows("SELECT * FROM papers WHERE id NOT IN (SELECT paper_id FROM review_items) "
                     "ORDER BY added_at, id"):
        def on_retry(msg, pid=p["id"]):
            db.log("interpreter", "retry", pid, msg)

        result, warnings = interpreter.interpret(p, categories, log=on_retry)
        db.execute("INSERT INTO review_items(paper_id, status, interpretation, warnings, created_at) "
                   "VALUES(?,?,?,?,?)",
                   (p["id"], "pending", db.dumps(result), db.dumps(warnings), db.now()))
        errors = sum(w["level"] == "error" for w in warnings)
        db.log("interpreter", "proposed", p["id"],
               f"mode={llm.mode()}, category={result['category'] or '?'}, "
               f"{len(result['keywords'])} keywords, {errors} errors, "
               f"{len(warnings) - errors} warnings")
        done.append(p["id"])
    return done


def run_pipeline(http=None):
    """Extractor, then interpreter, then updater for anything already approved."""
    from pipeline import updater  # imported here to keep the order of agents readable

    adapter = FakeSiteAdapter(site_url(), http=http)
    summary = extractor.run(adapter)
    summary["interpreted"] = interpret_new()
    summary["applied"] = updater.apply_approved()
    db.log("orchestrator", "pipeline run",
           detail=f"included {len(summary['included'])}, excluded {len(summary['excluded'])}, "
                  f"interpreted {len(summary['interpreted'])}, applied {len(summary['applied'])}")
    return summary
