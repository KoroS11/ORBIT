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
import hashlib
import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
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

# Default model that provides fast responses without 429 quota exhaustion.
DEFAULT_MODEL = "gemini-3.1-flash-lite"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
RETRY_STATUS = {429, 500, 503}
MAX_ATTEMPTS = 4


def _get_cache_file() -> Path:
    state_dir = os.environ.get("ORBIT_STATE_DIR")
    if state_dir:
        p = Path(state_dir)
    else:
        p = Path(__file__).resolve().parents[3] / "data" / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p / "gemini_cache.json"


class GeminiPromptCache:
    """Persistent prompt-hash cache under ORBIT_STATE_DIR."""

    def __init__(self, cache_file: Path | None = None) -> None:
        self._path = cache_file if cache_file is not None else _get_cache_file()
        self._memory: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("Failed to load Gemini cache from %s", self._path)
        return {}

    def _persist(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._memory, indent=2), encoding="utf-8")
        except Exception:
            log.warning("Failed to persist Gemini cache to %s", self._path)

    def get(self, key: str) -> Any | None:
        return self._memory.get(key)

    def set(self, key: str, value: Any) -> None:
        self._memory[key] = value
        self._persist()

    def compute_key(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        temperature: float = 0.2,
        thinking_budget: int | None = None,
        schema: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "temperature": temperature,
            "thinking_budget": thinking_budget,
            "schema": schema,
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _retry_delay(resp: Any, attempt: int) -> float:
    try:
        if hasattr(resp, "headers") and "retry-after" in resp.headers:
            return min(float(resp.headers["retry-after"]), 30.0)
    except (KeyError, ValueError):
        pass
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
        thinking_budget: int | None = None,
        transport: Any | None = None,
        cache: GeminiPromptCache | None = None,
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
        raw_budget = (
            thinking_budget
            if thinking_budget is not None
            else os.environ.get("ORBIT_GEMINI_THINKING_BUDGET")
        )
        self._thinking_budget = (
            int(raw_budget)
            if raw_budget is not None and str(raw_budget).strip() != ""
            else None
        )
        self._transport = transport
        self._cache = cache if cache is not None else GeminiPromptCache()
        self.last_provider: str = "gemini"
        log.info(
            "gemini: model=%s timeout=%.0fs thinking_budget=%s",
            self._model,
            self._timeout_s,
            self._thinking_budget,
        )

    async def _chat(self, *, system: str, user: str, schema: dict[str, Any]) -> dict:
        import httpx

        prompt = (
            f"{user}\n\n"
            "Reply with a single JSON object that conforms to this schema:\n"
            f"```json\n{json.dumps(schema, indent=2)}\n```"
        )
        cache_key = self._cache.compute_key(
            model=self._model,
            system=system,
            prompt=prompt,
            temperature=0.2,
            thinking_budget=self._thinking_budget,
            schema=schema,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            log.info("gemini: cache hit for key %s", cache_key[:12])
            self.last_provider = "gemini-cache"
            return cached

        gen_config: dict[str, Any] = {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        }
        if self._thinking_budget is not None:
            gen_config["thinkingConfig"] = {"thinkingBudget": self._thinking_budget}

        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
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
                parsed = _extract_json(_response_text(resp.json()))
                self._cache.set(cache_key, parsed)
                self.last_provider = "gemini"
                return parsed
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
        self.last_provider: str = getattr(primary, "last_provider", "gemini")

    @property
    def current_provider(self) -> str:
        return self.last_provider

    async def _call(self, name: str, *args: Any) -> Any:
        try:
            result = await getattr(self._primary, name)(*args)
            self.last_provider = getattr(self._primary, "last_provider", "gemini")
            return result
        except Exception as exc:  # noqa: BLE001 - CancelledError is not an Exception
            log.warning(
                "llm: %s failed (%s: %s); using fallback",
                name,
                type(exc).__name__,
                exc,
            )
            self.last_provider = "stub"
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
