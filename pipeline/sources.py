"""Source adapters. Each adapter knows how to list and fetch papers from one source.

Only the fake site adapter exists. It is not a real scraper and must only be
pointed at the fake site in this repo.
"""

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "FairDigitDemoBot/0.1 (classroom demo)"


class TDMOptOut(Exception):
    """Raised when a page says text and data mining is reserved."""


class SourceAdapter:
    """Interface every source adapter follows."""

    def list_new(self, since):
        """Return refs (dicts with at least id, url, posted, title) posted after `since`."""
        raise NotImplementedError

    def fetch(self, ref):
        """Return metadata and abstract for one ref. Never the full text."""
        raise NotImplementedError

    def tdm_check(self, ref):
        """Return (allowed, signals). signals lists every opt-out found."""
        raise NotImplementedError


def tdmrep_rule_for(path, rules):
    """Pick the most specific tdmrep.json rule matching a path, or None.

    Locations are simple patterns: an exact path, or a prefix ending in "*".
    """
    best = None
    for rule in rules:
        loc = rule.get("location", "")
        matches = path.startswith(loc[:-1]) if loc.endswith("*") else path == loc
        if matches and (best is None or len(loc) > len(best["location"])):
            best = rule
    return best


class FakeSiteAdapter(SourceAdapter):
    def __init__(self, base_url, http=None):
        self.base_url = base_url.rstrip("/")
        # `http` is anything with a requests-like .get(); tests pass a TestClient
        self.http = http or requests.Session()
        self._robots = None
        self._tdmrep = None

    def _get(self, path_or_url):
        url = path_or_url if path_or_url.startswith("http") else self.base_url + path_or_url
        return self.http.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)

    # ---------- legal checks ----------

    def robots(self):
        if self._robots is None:
            self._robots = RobotFileParser()
            r = self._get("/robots.txt")
            # A missing robots.txt means everything is allowed
            self._robots.parse(r.text.splitlines() if r.status_code == 200 else [])
        return self._robots

    def tdmrep(self):
        if self._tdmrep is None:
            r = self._get("/.well-known/tdmrep.json")
            self._tdmrep = r.json() if r.status_code == 200 else []
        return self._tdmrep

    def tdm_check(self, ref):
        path = urlparse(ref["url"]).path
        signals = []
        if not self.robots().can_fetch(USER_AGENT, ref["url"]):
            signals.append("robots.txt Disallow")
        rule = tdmrep_rule_for(path, self.tdmrep())
        if rule and rule.get("tdm-reservation") == 1:
            signals.append(f"tdmrep.json tdm-reservation=1 for {rule['location']}")
        # The tdm-reservation header is checked in fetch(), on the page response itself
        return (not signals, signals)

    # ---------- listing and fetching ----------

    def list_new(self, since):
        soup = BeautifulSoup(self._get("/").text, "html.parser")
        refs = []
        for li in soup.select("li.paper"):
            a = li.find("a")
            ref = {
                "id": li["data-id"],
                "posted": li["data-posted"],
                "collection": li.get("data-collection", ""),
                "title": a.get_text(strip=True),
                "url": self.base_url + a["href"],
            }
            if not since or ref["posted"] > since:
                refs.append(ref)
        return sorted(refs, key=lambda r: r["posted"])  # oldest first

    def fetch(self, ref):
        r = self._get(ref["url"])
        r.raise_for_status()
        if r.headers.get("tdm-reservation") == "1":
            raise TDMOptOut("tdm-reservation: 1 header")
        soup = BeautifulSoup(r.text, "html.parser")

        def meta(name):
            tag = soup.find("meta", attrs={"name": name})
            return tag["content"] if tag else ""

        abstract = soup.select_one("#abstract p")
        return {
            "id": ref["id"],
            "url": ref["url"],
            "title": meta("citation_title") or ref["title"],
            "authors": [t["content"] for t in soup.find_all("meta", attrs={"name": "citation_author"})],
            "published": meta("citation_publication_date"),
            "doi": meta("citation_doi"),
            "language": meta("citation_language"),
            "license": meta("dc.rights"),
            "status": meta("publication_status"),
            "collection": meta("citation_journal_title"),
            # abstract only: the full text section is never read
            "abstract": abstract.get_text(" ", strip=True) if abstract else "",
        }
