"""One function for model calls: ask_json(prompt, mock_key).

LLM_MODE in .env picks the backend:
  mock    scripted answers from seed/mock_interpretations.json (default, no network)
  gemini  Google Gemini free tier via google-genai (GEMINI_API_KEY, GEMINI_MODEL)
  ollama  local model via Ollama on localhost:11434 (OLLAMA_MODEL, e.g. gemma3:4b)
"""

import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MOCK_FILE = Path(__file__).parent / "seed" / "mock_interpretations.json"


def mode():
    return os.environ.get("LLM_MODE", "mock").strip().lower()


def _mock(mock_key, attempt):
    answers = json.loads(MOCK_FILE.read_text(encoding="utf-8"))
    answer = answers.get(mock_key)
    if isinstance(answer, list):  # a list scripts one answer per attempt
        answer = answer[min(attempt, len(answer) - 1)]
    if answer is None:
        return ""
    return answer if isinstance(answer, str) else json.dumps(answer)


def _gemini(prompt):
    from google import genai  # optional dependency

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt,
        config={"response_mime_type": "application/json", "temperature": 0},
    )
    return resp.text


def _ollama(prompt):
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    r = requests.post(f"{url}/api/generate", timeout=300, json={
        "model": os.environ.get("OLLAMA_MODEL", "gemma3:4b"),
        "prompt": prompt, "format": "json", "stream": False,
        "options": {"temperature": 0},
    })
    r.raise_for_status()
    return r.json()["response"]


def _raw(prompt, mock_key, attempt):
    m = mode()
    if m == "mock":
        return _mock(mock_key, attempt)
    if m == "gemini":
        return _gemini(prompt)
    if m == "ollama":
        return _ollama(prompt)
    raise ValueError(f"Unknown LLM_MODE {m!r}, use mock, gemini or ollama")


def _parse(text):
    text = (text or "").strip()
    if text.startswith("```"):  # some models wrap JSON in a code fence
        text = text.strip("`").removeprefix("json").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def ask_json(prompt, mock_key=None, on_retry=None):
    """Return a dict, retrying once on broken JSON. Returns None if both tries fail."""
    for attempt in range(2):
        text = _raw(prompt, mock_key, attempt)
        try:
            return _parse(text)
        except (ValueError, json.JSONDecodeError) as e:
            if on_retry and attempt == 0:
                on_retry(f"Broken JSON ({e.__class__.__name__}), retrying once")
    return None
