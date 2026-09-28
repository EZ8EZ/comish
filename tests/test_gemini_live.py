"""Opt-in: `COMISH_GEMINI_API_KEY=... uv run pytest -m live tests/test_gemini_live.py`."""

import os

import pytest

from comish.llm.gemini import GeminiLLM

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
}


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("COMISH_GEMINI_API_KEY"), reason="no Gemini key")
def test_gemini_structured_output():
    llm = GeminiLLM(os.environ["COMISH_GEMINI_API_KEY"])
    assert llm.generate_json("What is 2 + 3? Reply as JSON.", SCHEMA) == {"answer": 5}
