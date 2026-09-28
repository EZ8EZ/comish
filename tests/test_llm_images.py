import json
from types import SimpleNamespace

import pytest

from comish.ingest.images import SCHEMA, TranscriptionError, compare, make_transcriber
from comish.llm.base import LLMError, check_schema
from comish.llm.gemini import GeminiLLM


def result(**overrides):
    base = {
        "kind": "poll",
        "is_league_decision": True,
        "decision": "Expand taxi squads to 3 slots.",
        "outcome": "passed",
        "votes_for": 8,
        "votes_against": 4,
        "date_text": "Aug 10, 2024",
        "verbatim_text": "Expand taxi to 3?\nYes 8\nNo 4",
        "uncertainties": [],
    }
    base.update(overrides)
    return base


class FakeLLM:
    model = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def generate_json(self, prompt, schema, images=None, system=None):
        self.prompts.append(prompt)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_schema_check_accepts_valid():
    check_schema(result(), SCHEMA)


@pytest.mark.parametrize(
    "bad",
    [
        {k: v for k, v in result().items() if k != "outcome"},
        result(outcome="probably"),
        result(votes_for="eight"),
        result(votes_for=True),
        result(uncertainties=[1]),
    ],
)
def test_schema_check_rejects_invalid(bad):
    with pytest.raises(LLMError):
        check_schema(bad, SCHEMA)


def test_agreeing_passes_propose_the_date():
    llm = FakeLLM(result(), result(date_text="8/10/2024", decision="Taxi goes to 3."))
    transcript = make_transcriber(llm)(b"img", "image/png", "IMG_1.png")
    assert transcript.disagreements == []
    assert transcript.proposed_date == "2024-08-10"
    assert "Votes: 8 for, 4 against" in transcript.text
    assert 'Text in image: "Expand taxi to 3?' in transcript.text
    assert len(set(llm.prompts)) == 2  # two independent, different instructions


def test_disagreement_is_flagged_and_blocks_date():
    llm = FakeLLM(result(), result(votes_for=9, date_text="Aug 11, 2024"))
    transcript = make_transcriber(llm)(b"img", "image/png", "IMG_1.png")
    assert transcript.disagreements == ["votes_for", "date"]
    assert transcript.proposed_date is None


def test_missing_date_proposes_nothing():
    llm = FakeLLM(result(date_text=None), result(date_text=None))
    assert make_transcriber(llm)(b"i", "image/png", "x").proposed_date is None


def test_llm_error_becomes_transcription_error():
    llm = FakeLLM(result(), LLMError("quota"))
    with pytest.raises(TranscriptionError, match="quota"):
        make_transcriber(llm)(b"i", "image/png", "x")


def test_compare_normalizes_date_formats():
    assert compare(result(date_text="2024-08-10"), result(date_text="August 10, 2024")) == []


class FakeGenaiClient:
    def __init__(self, text=None, error=None):
        self.text, self.error = text, error
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def test_gemini_returns_validated_json():
    client = FakeGenaiClient(text=json.dumps(result()))
    llm = GeminiLLM("key", model="m", client=client)
    assert llm.generate_json("p", SCHEMA)["outcome"] == "passed"
    (_, contents, config) = client.calls[0]
    assert contents == ["p"]
    assert config.response_mime_type == "application/json"
    assert config.temperature == 0


@pytest.mark.parametrize(
    "client,match",
    [
        (FakeGenaiClient(error=RuntimeError("429 quota")), "429 quota"),
        (FakeGenaiClient(text=""), "empty response"),
        (FakeGenaiClient(text="not json"), "not JSON"),
        (FakeGenaiClient(text=json.dumps({"kind": "poll"})), "missing"),
    ],
)
def test_gemini_failures_raise_llm_error(client, match):
    with pytest.raises(LLMError, match=match):
        GeminiLLM("key", client=client).generate_json("p", SCHEMA)
