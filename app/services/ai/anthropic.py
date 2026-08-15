"""The Anthropic provider — §16.

Off unless `ANTHROPIC_API_KEY` is set. The service layer falls back to the extractive
provider whenever this one is unconfigured, over budget, erroring, or produces something
the guard rejects (§15), so enabling it is an upgrade and never a dependency.

The prompts state §15's prohibitions explicitly, but the prompt is not what enforces
them — `guard.py` checks the output against the source text afterwards. A prompt is a
request; the guard is the rule.
"""

from __future__ import annotations

import json
import time

import httpx

from app.services.ai.base import (
    OPERATION_CONFLICTS,
    OPERATION_HEADLINE,
    OPERATION_SUMMARY,
    OPERATION_SYNTHESIS,
    Generation,
    GenerationRequest,
)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
PROMPT_VERSION = "v1"

LENGTH_GUIDANCE = {
    "short": "one or two sentences",
    "standard": "two to four sentences",
    "detailed": "one or two short paragraphs",
}

#: The prohibitions from §15, stated to the model. Enforcement is the guard's job.
RULES = """You are summarizing news coverage for an aggregator that credits the original
publishers. These rules are absolute:

- Use ONLY facts present in the provided reporting. Invent nothing.
- Never invent or alter a number, statistic, date, name, place, or quotation.
- Never introduce a fact, event, or source that is not in the reporting below.
- Never present speculation as established fact.
- Do not editorialize, and do not use clickbait phrasing.
- If the reporting disagrees, say so rather than choosing one version.
- Write plainly. No preamble, no "here is", no meta-commentary — output only the text
  requested."""


class AnthropicProvider:
    """Calls the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        timeout: float = 30.0,
        max_tokens: int = 600,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._client = client

    def supports(self, operation: str) -> bool:
        return operation in {
            OPERATION_HEADLINE,
            OPERATION_SUMMARY,
            OPERATION_SYNTHESIS,
            OPERATION_CONFLICTS,
        }

    # --- prompts -------------------------------------------------------------

    def _reporting_block(self, request: GenerationRequest) -> str:
        parts = []
        for source in request.sources:
            body = f"\n{source.description}" if source.description else ""
            parts.append(f"[{source.publication}] {source.headline}{body}")
        return "\n\n".join(parts)

    def _instruction(self, request: GenerationRequest) -> str:
        count = len(request.sources)
        if request.operation == OPERATION_HEADLINE:
            style = f" Write it in this style: {request.style}." if request.style else ""
            return (
                "Write one headline for this story. It must be concise, describe what "
                "actually happened, and avoid clickbait." + style + " Output the headline "
                "only, with no quotation marks."
            )
        if request.operation == OPERATION_SUMMARY:
            length = LENGTH_GUIDANCE.get(request.length, LENGTH_GUIDANCE["standard"])
            return (
                f"Write a summary of {length} explaining what happened. Include the "
                "important facts. Do not reproduce the article; do not pad."
            )
        if request.operation == OPERATION_SYNTHESIS:
            length = LENGTH_GUIDANCE.get(request.length, LENGTH_GUIDANCE["standard"])
            return (
                f"{count} independent publications reported this story. Write a synthesis "
                f"of {length} covering what they collectively report. Where they differ on "
                "a material detail, state the difference explicitly rather than choosing "
                "one version."
            )
        if request.operation == OPERATION_CONFLICTS:
            return (
                "List every material factual disagreement between these reports — "
                "differing figures, causes, attributions, or timelines. Respond with JSON: "
                '{"conflicts": [{"subject": "...", "values": [{"value": "...", '
                '"publications": ["..."]}]}]}. If they do not disagree, return '
                '{"conflicts": []}.'
            )
        return "Summarize the reporting below."

    # --- call ----------------------------------------------------------------

    def generate(self, request: GenerationRequest) -> Generation:
        if not request.sources:
            return Generation(None, self.name, self.model, error="no source reporting supplied")

        prompt = (
            f"{self._instruction(request)}\n\n"
            f"Reporting ({len(request.sources)} source"
            f"{'s' if len(request.sources) != 1 else ''}):\n\n"
            f"{self._reporting_block(request)}"
        )

        started = time.perf_counter()
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            response = client.post(
                API_URL,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": self._max_tokens,
                    "system": RULES,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=self._timeout,
            )
            latency = int((time.perf_counter() - started) * 1000)

            if response.status_code >= 400:
                # Never leak the key, whatever the API says back.
                detail = response.text[:200].replace(self._api_key, "***")
                return Generation(
                    None,
                    self.name,
                    self.model,
                    latency_ms=latency,
                    error=f"HTTP {response.status_code}: {detail}",
                )

            body = response.json()
            text = "".join(
                block.get("text", "")
                for block in body.get("content", [])
                if block.get("type") == "text"
            ).strip()
            usage = body.get("usage", {}) or {}

            payload = None
            if request.operation == OPERATION_CONFLICTS:
                payload = self._parse_conflicts(text)

            return Generation(
                text=text or None,
                provider=self.name,
                model=body.get("model", self.model),
                prompt_version=PROMPT_VERSION,
                confidence=0.8,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                latency_ms=latency,
                payload=payload,
                error=None if text else "provider returned no text",
            )
        except Exception as exc:
            return Generation(
                None,
                self.name,
                self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}"[:300],
            )
        finally:
            if owns_client:
                client.close()

    def _parse_conflicts(self, text: str) -> dict | None:
        """Best-effort JSON extraction; a chatty model must not break the pipeline."""
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`")
            candidate = candidate[candidate.find("{") :] if "{" in candidate else candidate
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
