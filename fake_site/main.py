"""Fake publisher site. Serves synthetic papers as HTML.

Run: uvicorn fake_site.main:app --port 8001

Two collections:
  ojss  Open Journal of Synthetic Studies, open licence, TDM allowed, pages at /paper/{id}
  spp   Synthetic Press Premium, TDM reserved, pages at /premium/paper/{id}
The premium collection opts out of text and data mining in three ways:
robots.txt Disallow, a /.well-known/tdmrep.json entry and a tdm-reservation header.
"""

import html
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

DATA = json.loads((Path(__file__).parent / "papers.json").read_text(encoding="utf-8"))
COLLECTIONS = DATA["collections"]
PAPERS = {p["id"]: p for p in DATA["papers"]}
MAX_BATCH = max(p["batch"] for p in PAPERS.values())

# Which batch has been released. In memory only, resets when the server restarts.
state = {"batch": 1}

app = FastAPI(title="Fake publisher (synthetic)")

BANNER = (
    '<p style="background:#fff3cd;padding:6px">SYNTHETIC DEMO SITE. '
    "All papers, authors and DOIs are invented.</p>"
)


def released():
    return [p for p in PAPERS.values() if p["batch"] <= state["batch"]]


def paper_path(p):
    return f"/premium/paper/{p['id']}" if p["collection"] == "spp" else f"/paper/{p['id']}"


def page(title, body):
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title></head><body>{BANNER}{body}</body></html>"


@app.get("/", response_class=HTMLResponse)
def listing():
    items = []
    # newest first by the date the site posted the paper
    for p in sorted(released(), key=lambda p: p["posted"], reverse=True):
        coll = COLLECTIONS[p["collection"]]["name"]
        items.append(
            f'<li class="paper" data-id="{p["id"]}" data-posted="{p["posted"]}" data-collection="{p["collection"]}">'
            f'<a href="{paper_path(p)}">{html.escape(p["title"])}</a> '
            f'<span class="collection">{html.escape(coll)}</span> '
            f'<span class="posted">posted {p["posted"]}</span></li>'
        )
    body = f"<h1>Latest papers</h1><p>Batch {state['batch']} of {MAX_BATCH} released.</p><ul>{''.join(items)}</ul><p><a href='/terms'>Terms</a></p>"
    return page("Latest papers", body)


def paper_html(p):
    coll = COLLECTIONS[p["collection"]]
    metas = [
        ("citation_title", p["title"]),
        ("citation_publication_date", p["published"]),
        ("citation_doi", p["doi"]),
        ("citation_language", p["language"]),
        ("citation_journal_title", coll["name"]),
        ("dc.rights", coll["license"]),
        ("publication_status", p["status"]),
    ] + [("citation_author", a) for a in p["authors"]]
    meta_tags = "".join(
        f'<meta name="{k}" content="{html.escape(v, quote=True)}">' for k, v in metas
    )
    notice = "<p><strong>RETRACTED</strong></p>" if p["status"] == "retracted" else ""
    body = (
        f"<h1>{html.escape(p['title'])}</h1>{notice}"
        f"<p class='authors'>{html.escape(', '.join(p['authors']))} (invented authors)</p>"
        f"<p>{html.escape(coll['name'])}, published {p['published']}, DOI {p['doi']}</p>"
        f"<section id='abstract'><h2>Abstract</h2><p>{html.escape(p['abstract'])}</p></section>"
        "<section id='fulltext'><h2>Full text</h2><p>Full text is not part of this demo.</p></section>"
    )
    return f"<!doctype html><html><head><meta charset='utf-8'>{meta_tags}<title>{html.escape(p['title'])}</title></head><body>{BANNER}{body}</body></html>"


def get_released(paper_id, collection):
    p = PAPERS.get(paper_id)
    if not p or p["collection"] != collection or p["batch"] > state["batch"]:
        raise HTTPException(404, "Paper not found")
    return p


@app.get("/paper/{paper_id}", response_class=HTMLResponse)
def open_paper(paper_id: str):
    p = get_released(paper_id, "ojss")
    return HTMLResponse(paper_html(p), headers={"tdm-reservation": "0"})


@app.get("/premium/paper/{paper_id}", response_class=HTMLResponse)
def premium_paper(paper_id: str):
    p = get_released(paper_id, "spp")
    return HTMLResponse(
        paper_html(p), headers={"tdm-reservation": "1", "tdm-policy": "/terms"}
    )


@app.get("/terms", response_class=HTMLResponse)
def terms():
    body = (
        "<h1>Terms</h1>"
        "<h2>Open Journal of Synthetic Studies</h2><p>Licensed CC-BY-4.0 (synthetic). Text and data mining is allowed.</p>"
        "<h2>Synthetic Press Premium</h2><p>All rights reserved (synthetic). Text and data mining rights are reserved "
        "under the TDM Reservation Protocol. Do not mine /premium/.</p>"
    )
    return page("Terms", body)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /premium/\nAllow: /\n"


@app.get("/.well-known/tdmrep.json")
def tdmrep():
    # Modelled on the W3C TDM Reservation Protocol (TDMRep) well-known file.
    return JSONResponse([
        {"location": "/premium/*", "tdm-reservation": 1, "tdm-policy": "/terms"},
        {"location": "/paper/*", "tdm-reservation": 0},
    ])


@app.post("/admin/advance")
def advance():
    state["batch"] = min(state["batch"] + 1, MAX_BATCH)
    return {"batch": state["batch"], "max_batch": MAX_BATCH}


@app.post("/admin/reset")
def reset():
    state["batch"] = 1
    return {"batch": state["batch"], "max_batch": MAX_BATCH}


@app.get("/admin/status")
def status():
    return {"batch": state["batch"], "max_batch": MAX_BATCH, "released": len(released())}
