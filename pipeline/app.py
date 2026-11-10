"""Streamlit dashboard. Run from the repo root: streamlit run pipeline/app.py"""

import re
import sys
from collections import Counter
from pathlib import Path

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import db, llm, orchestrator, review, terms, updater  # noqa: E402

st.set_page_config(page_title="FairDigit taxonomy demo", layout="wide")

if not db.db_path().exists():
    db.reset_and_seed(orchestrator.site_url())
SITE = orchestrator.site_url()


def md_escape(text):
    """Paper text is untrusted: show it as plain text, not markdown."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~:$])", r"\\\1", text or "")


def flash(msg, kind="success"):
    st.session_state["flash"] = (msg, kind)


def site_post(path):
    try:
        r = requests.post(f"{SITE}{path}", timeout=5)
        return r.json()
    except requests.RequestException:
        flash(f"Fake site not reachable at {SITE}. Start it with: "
              "uvicorn fake_site.main:app --port 8001", "error")
        return None


# ---------- sidebar ----------

with st.sidebar:
    st.header("FairDigit demo")
    st.caption("Synthetic data only. This tool does not decide legality.")
    reviewer = st.text_input("Reviewer name", value=st.session_state.get("reviewer", "reviewer 1"))
    st.session_state["reviewer"] = reviewer

    if st.button("Run pipeline", type="primary", use_container_width=True):
        try:
            s = orchestrator.run_pipeline()
            flash(f"Included {len(s['included'])}, excluded {len(s['excluded'])}, "
                  f"interpreted {len(s['interpreted'])}, re-screened {len(s['rescreened'])}.")
        except requests.RequestException:
            flash(f"Fake site not reachable at {SITE}. Start it with: "
                  "uvicorn fake_site.main:app --port 8001", "error")
        st.rerun()
    if st.button("Publish next batch", use_container_width=True):
        r = site_post("/admin/advance")
        if r:
            flash(f"Fake site now shows batch {r['batch']} of {r['max_batch']}. Run the pipeline to fetch it.")
        st.rerun()
    if st.button("Reset demo", use_container_width=True):
        db.reset_and_seed(SITE)
        if site_post("/admin/reset"):
            flash("Database reseeded and fake site reset to batch 1.")
        st.rerun()

    st.divider()
    try:
        status = requests.get(f"{SITE}/admin/status", timeout=2).json()
        st.write(f"Fake site: batch {status['batch']} of {status['max_batch']}")
    except requests.RequestException:
        st.write(":red[Fake site not reachable]")
    st.write(f"LLM mode: `{llm.mode()}`")
    st.write(f"Taxonomy version: {db.get_setting('taxonomy_version', 1)}")

if "flash" in st.session_state:
    msg, kind = st.session_state.pop("flash")
    getattr(st, kind)(msg)

st.title("Addictive design taxonomy: living update demo")

n_pending = len(review.pending())
tabs = st.tabs(["Pipeline", f"Review queue ({n_pending})", "Taxonomy", "Search terms",
                "Activity log", "Flag an app"])

# ---------- Pipeline ----------

with tabs[0]:
    papers = db.rows("SELECT * FROM papers ORDER BY added_at")
    excl = db.rows("SELECT * FROM exclusions ORDER BY id")
    active = [e for e in excl if e["active"]]
    items = db.rows("SELECT status FROM review_items")
    c = st.columns(5)
    c[0].metric("Papers seen", len(papers) + len(active))
    c[1].metric("Included", len(papers))
    c[2].metric("Excluded", len(active))
    c[3].metric("Pending review", sum(i["status"] == "pending" for i in items))
    c[4].metric("Approved", sum(i["status"] == "approved" for i in items))

    tdm = [e for e in active if e["reason"] == "tdm_opt_out"]
    if tdm:
        st.warning("**TDM opt-out respected** for " + ", ".join(
            f"{e['paper_ref']} ({e['detail']})" for e in tdm)
            + ". Nothing from these pages was downloaded or stored.")

    st.subheader("Exclusions by reason")
    from pipeline.extractor import REASONS
    counts = Counter(e["reason"] for e in active)
    st.dataframe([{"reason": REASONS[r], "count": n} for r, n in counts.most_common()], hide_index=True)
    st.dataframe([{"paper": e["paper_ref"], "title": e["title"], "reason": REASONS[e["reason"]],
                   "detail": e["detail"], "time": e["time"]} for e in active], hide_index=True)
    rescreened = [e for e in excl if not e["active"]]
    if rescreened:
        st.caption("Re-screened and later included after the search terms grew: "
                   + ", ".join(e["paper_ref"] for e in rescreened))

    st.subheader("Included papers (metadata and abstract only)")
    st.dataframe([{"paper": p["id"], "title": p["title"], "published": p["published"], "doi": p["doi"],
                   "matched terms": ", ".join(p["matched_terms"] or [])} for p in papers], hide_index=True)

# ---------- Review queue ----------

with tabs[1]:
    st.caption("Evidence first: check each quote against the abstract before reading the model's proposal. "
               "Keywords whose quote failed the check start unticked.")
    cats = db.rows("SELECT id, name FROM categories ORDER BY name")
    names = {c["id"]: c["name"] for c in cats}
    pend = review.pending()
    if not pend:
        st.info("Nothing to review. Run the pipeline to fetch new papers.")
    for it in pend:
        interp = it["interpretation"]
        with st.form(f"review-{it['id']}"):
            st.subheader(it["title"])
            st.caption(f"{it['paper_id']} | DOI {it['doi']} | {it['url']}")

            st.markdown("**1. Evidence: quotes the model says support each keyword**")
            ticks = {}
            for i, kw in enumerate(interp["keywords"]):
                ticks[kw["term"]] = st.checkbox(f"Approve keyword: {kw['term']}", value=kw["quote_ok"],
                                                key=f"kw-{it['id']}-{i}")
                st.markdown("> " + (md_escape(kw["quote"]) or "(no quote)"))
                st.markdown(":green[Quote found word for word in the abstract]" if kw["quote_ok"]
                            else ":red[Quote NOT found in the abstract]")
            if not interp["keywords"]:
                st.write("No keywords proposed.")
            with st.expander("Full abstract (untrusted text)"):
                st.text(it["abstract"])

            st.markdown("**2. Model proposal (AI generated, check before approving)**")
            st.write(md_escape(interp["summary"]) or "(no summary)")
            proposed = interp["category"]
            label = (f"NEW: {interp['new_category_name']}" if proposed == "NEW"
                     else f"{names.get(proposed, proposed)} ({proposed})")
            c1, c2, c3 = st.columns(3)
            c1.write(f"Category: {label}")
            c2.write(f"Provision that may be relevant: {interp['provision']}")
            c3.write(f"Confidence: {interp['confidence']:.2f}")
            for w in it["warnings"]:
                (st.error if w["level"] == "error" else st.warning)(w["message"])

            st.markdown("**3. Your decision**")
            options = (["NEW"] if proposed == "NEW" else []) + [c["id"] for c in cats]
            index = options.index(proposed) if proposed in options else 0
            category = st.selectbox("Category", options, index=index, key=f"cat-{it['id']}",
                                    format_func=lambda x: label if x == "NEW" else f"{names[x]} ({x})")
            new_name = new_def = ""
            if proposed == "NEW":
                new_name = st.text_input("New category name (used if NEW is kept)", interp["new_category_name"],
                                         key=f"name-{it['id']}")
                new_def = st.text_input("New category definition", interp["new_category_definition"],
                                        key=f"def-{it['id']}")
            note = st.text_input("Note (optional)", key=f"note-{it['id']}")
            b1, b2, _ = st.columns([1, 1, 4])
            approve = b1.form_submit_button("Approve", type="primary")
            reject = b2.form_submit_button("Reject")
            if approve or reject:
                chosen = [t for t, on in ticks.items() if on]
                review.decide(it["id"], bool(approve), chosen, category, reviewer, note, new_name, new_def)
                if approve:
                    changes = updater.apply(it["id"])
                    flash(f"Approved {it['paper_id']}. Taxonomy changes: " + ("; ".join(changes) or "none"))
                else:
                    flash(f"Rejected {it['paper_id']}. No taxonomy change.", "info")
                st.rerun()

    done = review.decided()
    if done:
        with st.expander(f"Decided items ({len(done)})"):
            st.dataframe([{"paper": d["paper_id"], "title": d["title"], "status": d["status"],
                           "reviewer": d["reviewer"], "keywords": ", ".join(d["decision"]["keywords"]),
                           "dropped": ", ".join(d["decision"]["dropped_keywords"]),
                           "category": d["decision"]["category"], "note": d["decision"]["note"],
                           "time": d["decided_at"]} for d in done], hide_index=True)

# ---------- Taxonomy ----------


def cite_text(c):
    return f"{c['paper_id']}: {c['title']}" + (f" (DOI {c['doi']})" if c.get("doi") else "")


with tabs[2]:
    st.write(f"**Version {db.get_setting('taxonomy_version', 1)}**. Canonical categories need a definition "
             "and at least one citation. New categories start as candidates until a person promotes them.")
    for p in updater.validate():
        st.error(p)
    kws = db.rows("SELECT * FROM keywords ORDER BY id")
    for c in db.rows("SELECT * FROM categories ORDER BY status DESC, created_at, name"):
        tag = ":green[canonical]" if c["status"] == "canonical" else ":orange[candidate]"
        with st.expander(f"{c['name']} ({c['id']})  {tag}", expanded=c["status"] == "candidate"):
            st.write(c["definition"] or ":red[no definition]")
            st.dataframe([{"keyword": k["term"], "origin": k["origin"],
                           "citations": "; ".join(x["paper_id"] for x in k["citations"])}
                          for k in kws if k["category_id"] == c["id"]], hide_index=True)
            if c["provisions"]:
                st.write("Provisions that may be relevant (for legal review): " + ", ".join(
                    f"{p['provision']} (from {p['paper_id']})" for p in c["provisions"]))
            st.caption("Citations: " + " | ".join(cite_text(x) for x in c["citations"]))
            if c["status"] == "candidate":
                if st.button("Promote to canonical", key=f"promote-{c['id']}"):
                    try:
                        v = updater.promote(c["id"], reviewer)
                        flash(f"{c['name']} is now canonical. Taxonomy version {v}.")
                    except ValueError as e:
                        flash(str(e), "error")
                    st.rerun()

# ---------- Search terms ----------

with tabs[3]:
    tl = terms.search_terms()
    st.write(f"The scraper looks for **{len(tl)} terms** in each paper's title and abstract: "
             "seed terms plus every keyword in the taxonomy. Approved keywords widen the next search.")
    approved = [t for t in tl if t["origin"].startswith("approved")]
    if approved:
        st.success("Added by approved keywords: " + ", ".join(f"{t['term']} ({t['origin']})" for t in approved))
    st.dataframe(tl, hide_index=True)

# ---------- Activity log ----------

with tabs[4]:
    agents = ["all", "extractor", "interpreter", "reviewer", "updater", "orchestrator", "system"]
    pick = st.selectbox("Agent", agents)
    ev = db.rows("SELECT time, agent, action, paper_id AS paper, detail FROM events ORDER BY id DESC")
    st.dataframe([e for e in ev if pick == "all" or e["agent"] == pick], hide_index=True)

# ---------- Flag an app ----------

with tabs[5]:
    st.write("Placeholder, added in the next step.")
