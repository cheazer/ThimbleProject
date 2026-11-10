"""Wipe demo.db, reseed the taxonomy and reset the fake site to batch 1.

Run from the repo root: python scripts/reset_demo.py
"""

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import db  # noqa: E402


def main():
    load_dotenv()
    site = os.environ.get("FAKE_SITE_URL", "http://localhost:8001")
    db.reset_and_seed(site)
    print(f"Reset {db.db_path()}")
    try:
        requests.post(f"{site}/admin/reset", timeout=5)
        print(f"Reset fake site at {site}")
    except requests.RequestException:
        print(f"Fake site not reachable at {site}, only the database was reset")


if __name__ == "__main__":
    main()
