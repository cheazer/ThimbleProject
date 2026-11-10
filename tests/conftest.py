import pytest

from pipeline import db


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    """Tests always use scripted answers, whatever .env says."""
    monkeypatch.setenv("LLM_MODE", "mock")


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """A seeded database in a temp folder, so tests never touch demo.db."""
    monkeypatch.setenv("DEMO_DB", str(tmp_path / "test.db"))
    db.reset_and_seed("http://testserver")
    return db
