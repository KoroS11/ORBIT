"""Gemini-backed LLMClient (Google Generative Language REST API).

Reuses the prompt/parse plumbing of OllamaLLMClient and only swaps the HTTP
call. Attribution and decision output goes through the same validators the
Anthropic client uses.

Env:
  GEMINI_API_KEY (or GOOGLE_API_KEY)  required
  ORBIT_GEMINI_MODEL                  model id from your key's model list
  ORBIT_GEMINI_TIMEOUT_S              per-request timeout (default 60)
  ORBIT_LLM_FALLBACK=0                disable the stub fallback
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterable
from typing import Any

from orbit.services.kb import KB
from orbit.services.kb.models import KBEntry
from orbit.services.llm.ollama_client import OllamaLLMClient, _extract_json
from orbit.services.llm.validation import (
    validate_and_repair_attribution,
    validate_and_repair_decision,
)
from orbit.services.schemas.events import (
    Anomaly,
    Attribution,
    AttributionChallenge,
    Decision,
)

log = logging.getLogger(__name__)

# Placeholder: confirm against the model list returned for your key.
DEFAULT_MODEL = "gemini-2.5-flash"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
RETRY_STATUS = {429, 500, 503}
MAX_ATTEMPTS = 4


def _retry_delay(resp: Any, attempt: int) -> float:
    try:
        return min(float(resp.headers["retry-after"]), 30.0)
    except (KeyError, ValueError):
        return float(2**attempt)


def _response_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError(f"Gemini returned no candidates: {json.dumps(data)[:300]}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise ValueError(
            f"empty Gemini response (finishReason={candidates[0].get('finishReason')})"
        )
    return text


def _repair_attribution(attr: Attribution) -> Attribution:
    fixed = validate_and_repair_attribution(attr.model_dump()).repaired
    keys = ("actor", "confidence", "evidence", "kb_citations")
    return attr.model_copy(update={k: fixed[k] for k in keys})


class GeminiLLMClient(OllamaLLMClient):
    def __init__(
        self,
        kb: KB,
        *,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
        transport: Any | None = None,
    ) -> None:
        # Deliberately skip OllamaLLMClient.__init__ (it configures an Ollama URL).
        self._kb = kb
        self._model = model or os.environ.get("ORBIT_GEMINI_MODEL") or DEFAULT_MODEL
        self._api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        raw_timeout = (
            timeout_s
            if timeout_s is not None
            else os.environ.get("ORBIT_GEMINI_TIMEOUT_S")
        )
        self._timeout_s = float(raw_timeout) if raw_timeout else 60.0
        self._transport = transport
        log.info("gemini: model=%s timeout=%.0fs", self._model, self._timeout_s)

    async def _chat(self, *, system: str, user: str, schema: dict[str, Any]) -> dict:
        import httpx

        prompt = (
            f"{user}\n\n"
            "Reply with a single JSON object that conforms to this schema:\n"
            f"```json\n{json.dumps(schema, indent=2)}\n```"
        )
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        url = f"{API_ROOT}/models/{self._model}:generateContent"
        headers = {"x-goog-api-key": self._api_key}  # header, so it never lands in URLs/logs
        client_kwargs: dict[str, Any] = {"timeout": self._timeout_s}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                    delay = _retry_delay(resp, attempt)
                    log.warning(
                        "gemini: HTTP %s, retry %d in %.0fs",
                        resp.status_code,
                        attempt,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                return _extract_json(_response_text(resp.json()))
        raise RuntimeError("gemini: retries exhausted")

    async def attribute_primary(
        self, anomalies: list[Anomaly], kb_context: Iterable[KBEntry] = ()
    ) -> Attribution:
        return _repair_attribution(await super().attribute_primary(anomalies, kb_context))

    async def reconcile(
        self,
        primary: Attribution,
        challenge: AttributionChallenge,
        anomalies: list[Anomaly],
        kb_context: Iterable[KBEntry] = (),
    ) -> Attribution:
        return _repair_attribution(
            await super().reconcile(primary, challenge, anomalies, kb_context)
        )

    async def decide(self, attribution: Attribution) -> Decision:
        decision = await super().decide(attribution)
        fixed = validate_and_repair_decision(decision.model_dump())
        return decision.model_copy(update={"request_packet": fixed.get("request_packet")})


class FallbackLLMClient:
    """Use the primary client; on any failure use the fallback so the demo never stalls."""

    def __init__(self, primary: Any, fallback: Any) -> None:
        self._primary = primary
        self._fallback = fallback

    async def _call(self, name: str, *args: Any) -> Any:
        try:
            return await getattr(self._primary, name)(*args)
        except Exception as exc:  # noqa: BLE001 - CancelledError is not an Exception
            log.warning(
                "llm: %s failed (%s: %s); using fallback",
                name,
                type(exc).__name__,
                exc,
            )
            return await getattr(self._fallback, name)(*args)

    async def attribute(self, anomalies, kb_context=()):
        return await self._call("attribute", anomalies, kb_context)

    async def attribute_primary(self, anomalies, kb_context=()):
        return await self._call("attribute_primary", anomalies, kb_context)

    async def attribute_redteam(self, primary, anomalies, kb_context=()):
        return await self._call("attribute_redteam", primary, anomalies, kb_context)

    async def reconcile(self, primary, challenge, anomalies, kb_context=()):
        return await self._call("reconcile", primary, challenge, anomalies, kb_context)

    async def decide(self, attribution):
        return await self._call("decide", attribution)
