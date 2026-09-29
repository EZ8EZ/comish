"""Gemini API provider (free tier in v1).

Free-tier content may be used by Google to improve its products; the commissioner
accepted that for v1 (docs/PLAN.md, Q1). The model name is configurable because
free-tier availability changes; check the pricing page's free-tier column.
"""

import json
from typing import Any

from comish.llm.base import Image, LLMError, check_schema

DEFAULT_MODEL = "gemini-2.5-flash"
TIMEOUT_MS = 90_000


class GeminiLLM:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, client: Any = None):
        self.model = model
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS)
            )
        self._client = client

    def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        images: list[Image] | None = None,
        system: str | None = None,
    ) -> dict[str, Any]:
        from google.genai import types

        contents: list[Any] = [
            types.Part.from_bytes(data=img.data, mime_type=img.mime_type) for img in images or []
        ]
        contents.append(prompt)
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            response_mime_type="application/json",
            response_json_schema=schema,
        )
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
            text = response.text
        except Exception as exc:  # every SDK/network failure becomes LLMError
            raise LLMError(f"{self.model}: {type(exc).__name__}: {exc}") from exc
        if not text:
            raise LLMError(f"{self.model}: empty response (blocked or no candidates)")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"{self.model}: response is not JSON") from exc
        check_schema(value, schema)
        if not isinstance(value, dict):
            raise LLMError(f"{self.model}: expected a JSON object")
        return value
