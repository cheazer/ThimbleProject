"""All SQLite access goes through this module. File: pipeline/demo.db (override with DEMO_DB)."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
SEED_DIR = HERE / "seed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, name TEXT, base_url TEXT, adapter TEXT
);
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY, source_id TEXT, doi TEXT, title TEXT, authors TEXT,
    published TEXT, language TEXT, license TEXT, collection TEXT, url TEXT,
    abstract TEXT, matched_terms TEXT, added_at TEXT
);
CREATE TABLE IF NOT EXISTS exclusions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, paper_ref TEXT, url TEXT,
    doi TEXT, title TEXT, reason TEXT, detail TEXT, active INTEGER DEFAULT 1, time TEXT
);
CREATE TABLE IF NOT EXISTS review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, paper_id TEXT, status TEXT,
    interpretation TEXT, warnings TEXT, decision TEXT, reviewer TEXT,
    created_at TEXT, decided_at TEXT, applied INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY, name TEXT, definition TEXT, status TEXT,
    citations TEXT, provisions TEXT, origin TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT, category_id TEXT,
    citations TEXT, origin TEXT, added_at TEXT, UNIQUE(term, category_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, agent TEXT, action TEXT,
    paper_id TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT
);
"""

# JSON columns are decoded automatically by rows()/row().
JSON_COLS = {"authors", "matched_terms", "interpretation", "warnings", "decision",
             "citations", "provisions"}


def db_path():
    return Path(os.environ.get("DEMO_DB", HERE / "demo.db"))


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def _decode(r):
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k] is not None:
            d[k] = json.loads(d[k])
    return d


def rows(sql, params=()):
    with connect() as conn:
        return [_decode(r) for r in conn.execute(sql, params).fetchall()]


def row(sql, params=()):
    found = rows(sql, params)
    return found[0] if found else None


def execute(sql, params=()):
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


# ---------- settings and events ----------

def get_setting(key, default=None):
    r = row("SELECT value FROM settings WHERE key = ?", (key,))
    return json.loads(r["value"]) if r else default


def set_setting(key, value):
    execute("INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, dumps(value)))


def log(agent, action, paper_id=None, detail=""):
    execute("INSERT INTO events(time, agent, action, paper_id, detail) VALUES(?,?,?,?,?)",
            (now(), agent, action, paper_id, detail))


# ---------- reset and seed ----------

def reset_and_seed(site_url):
    """Wipe the database and load the seed taxonomy."""
    path = db_path()
    if path.exists():
        path.unlink()
    init_db()
    seed = json.loads((SEED_DIR / "taxonomy.json").read_text(encoding="utf-8"))
    sources = {s["id"]: s for s in seed["seed_sources"]}

    execute("INSERT INTO sources VALUES(?,?,?,?)",
            ("fake_site", "Fake publisher (synthetic)", site_url, "FakeSiteAdapter"))

    for c in seed["categories"]:
        cites = [{"paper_id": sources[s]["id"], "doi": sources[s]["doi"],
                  "title": sources[s]["title"]} for s in c["sources"]]
        execute("INSERT INTO categories VALUES(?,?,?,?,?,?,?,?)",
                (c["id"], c["name"], c["definition"], "canonical", dumps(cites),
                 dumps([]), "seed", now()))
        for term in c["keywords"]:
            execute("INSERT INTO keywords(term, category_id, citations, origin, added_at) "
                    "VALUES(?,?,?,?,?)", (term, c["id"], dumps(cites), "seed taxonomy", now()))

    set_setting("seed_search_terms", seed["seed_search_terms"])
    # starting point for terms.refresh(), so later additions can be logged
    set_setting("search_terms_snapshot", seed["seed_search_terms"]
                + [k for c in seed["categories"] for k in c["keywords"]])
    set_setting("seed_sources", seed["seed_sources"])
    set_setting("taxonomy_version", 1)
    log("system", "reset", detail="Database wiped and seed taxonomy loaded (version 1)")
