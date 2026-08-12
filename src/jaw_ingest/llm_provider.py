from __future__ import annotations

import copy
import json
import logging
import re
import time
from typing import Any, Protocol

import httpx

from .cache import CacheManager

logger = logging.getLogger(__name__)


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrites a (typically Pydantic-generated) JSON schema so it satisfies the strict
    structured-output requirements shared by OpenAI and OpenAI-compatible providers
    (Groq included): every object node must set `additionalProperties: false` and list
    every one of its properties in `required` - fields a Pydantic model treats as
    optional (via a default) are still validated/defaulted client-side afterwards, so
    forcing them into `required` here doesn't change what the caller receives.
    Recurses through `properties`, `items`, `$defs`, and `anyOf`/`oneOf`/`allOf`, which
    is where Pydantic nests sub-models.
    """
    schema = copy.deepcopy(schema)

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for value in node.get("properties", {}).values():
            _walk(value)
        if "items" in node:
            _walk(node["items"])
        for value in node.get("$defs", {}).values():
            _walk(value)
        for key in ("anyOf", "oneOf", "allOf"):
            for value in node.get(key, []):
                _walk(value)

    _walk(schema)
    return schema


def _json_schema_name(schema: dict[str, Any]) -> str:
    """Structured-output APIs require a short identifier-like name for the schema.
    Pydantic's model_json_schema() includes the model's class name as "title" - use
    that (sanitized) instead of a hardcoded name unrelated to the actual schema.
    """
    title = schema.get("title") or "response"
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", title)[:64]
    return name or "response"


class ProviderNotConfigured(RuntimeError):
    """Raised when a caller attempts to use an LLM provider that has no credentials/endpoint configured."""


class ProviderRequestError(RuntimeError):
    """Raised when a configured provider's HTTP call fails or returns an unusable response."""


class LLMProvider(Protocol):
    """Generic contract for a chat-completion-style LLM provider.

    Implementations must not perform any network I/O outside of `complete()` -
    constructing a provider is always safe, even with no credentials.
    """

    def complete(self, system: str, user: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object validated (at minimum, parsed) against response_schema's shape."""
        ...


class NullProvider:
    """Default provider when none is configured. Every call raises ProviderNotConfigured."""

    def complete(self, system: str, user: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        raise ProviderNotConfigured(
            "No LLM provider is configured (JAW_LLM_PROVIDER is unset or 'none'). "
            "Set JAW_LLM_PROVIDER=openai_compatible plus JAW_LLM_BASE_URL/JAW_LLM_API_KEY/JAW_LLM_MODEL to enable extraction."
        )


class OpenAICompatibleProvider:
    """LLM provider speaking the OpenAI chat-completions HTTP contract.

    Works against any server implementing that contract by configuring base_url:
    OpenAI itself, Groq, a self-hosted vLLM/Ollama-with-OpenAI-shim endpoint, etc.
    No SDK dependency - raw HTTP via httpx, since the specific provider SDK is not
    assumed to be installed.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required for OpenAICompatibleProvider")
        if not model:
            raise ValueError("model is required for OpenAICompatibleProvider")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def complete(self, system: str, user: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": _json_schema_name(response_schema),
                    "schema": _strict_json_schema(response_schema),
                    "strict": True,
                },
            },
        }
        response = self._post(payload)

        if response.status_code == 400:
            # A 400 specifically on the structured-output request means the server
            # rejected (or the model couldn't satisfy) strict json_schema mode - not a
            # generic failure. Fall back to the more broadly supported json_object mode
            # with the schema inlined as instructions, rather than erroring outright.
            # This is provider-agnostic: any OpenAI-compatible server with partial
            # structured-output support hits the same path, not just one named vendor.
            logger.warning(
                "Provider rejected strict json_schema structured output (HTTP 400: %s); "
                "retrying with json_object mode and an inlined schema.",
                response.text[:300],
            )
            response = self._post(self._json_object_payload(system, user, response_schema))

        return self._parse_content(response)

    def _json_object_payload(self, system: str, user: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        schema_text = json.dumps(response_schema, ensure_ascii=False)
        augmented_system = (
            f"{system}\n\nYou must respond with a single valid JSON object and nothing else - "
            "no prose, no markdown code fences, no commentary."
        )
        augmented_user = (
            f"{user}\n\nRespond with ONLY a single JSON object that conforms exactly to this "
            f"JSON Schema:\n{schema_text}"
        )
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": augmented_system},
                {"role": "user", "content": augmented_user},
            ],
            "response_format": {"type": "json_object"},
        }

    def _post(self, payload: dict[str, Any], max_retries: int = 10) -> httpx.Response:
        client = self._client or httpx.Client(timeout=self.timeout)
        owns_client = self._client is None
        try:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    res = client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
                    if res.status_code == 429 and attempt < max_retries:
                        wait_seconds = 30.0
                        try:
                            msg = res.json().get("error", {}).get("message", "")
                            match = re.search(r"retry in (\d+(?:\.\d+)?)s", msg, re.IGNORECASE)
                            if match:
                                wait_seconds = float(match.group(1)) + 2.0
                        except Exception:
                            pass
                        logger.warning(
                            "LLM request hit HTTP 429 Rate Limit (attempt %d/%d); sleeping %.1fs before retrying...",
                            attempt + 1,
                            max_retries + 1,
                            wait_seconds,
                        )
                        time.sleep(wait_seconds)
                        continue
                    return res
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    # Transient - retry with a short backoff. This is what makes batched
                    # extraction calls over a real corpus survive occasional slow
                    # responses instead of permanently losing that chunk's data on the
                    # first hiccup.
                    last_exc = exc
                    if attempt < max_retries:
                        wait_seconds = 2.0 * (attempt + 1)
                        logger.warning(
                            "LLM request attempt %d/%d timed out/failed to connect (%s); retrying in %.1fs.",
                            attempt + 1,
                            max_retries + 1,
                            exc,
                            wait_seconds,
                        )
                        time.sleep(wait_seconds)
                except httpx.HTTPError as exc:
                    raise ProviderRequestError(f"LLM request failed: {exc}") from exc
            raise ProviderRequestError(f"LLM request failed after {max_retries + 1} attempts: {last_exc}")
        finally:
            if owns_client:
                client.close()

    def _parse_content(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code != 200:
            raise ProviderRequestError(
                f"LLM provider returned HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderRequestError(f"Could not parse LLM response as JSON: {exc}") from exc


class CachingLLMProvider:
    """Wraps another provider with a persistent, on-disk response cache (reusing the
    existing CacheManager - the same JSON-file-per-key store used for document
    extraction caching). Keyed on the exact (system, user, response_schema) triple, so
    a re-run over the same evidence with the same prompts/model never re-pays for an
    LLM call it already made. Only actual `.complete()` calls are cached; a
    ProviderNotConfigured/ProviderRequestError is never cached, so a transient failure
    (or a fixed API key) is retried on the next run rather than being stuck.
    """

    def __init__(self, provider: LLMProvider, cache: CacheManager) -> None:
        self.provider = provider
        self.cache = cache

    def _key(self, system: str, user: str, response_schema: dict[str, Any]) -> str:
        return json.dumps({"system": system, "user": user, "schema": response_schema}, sort_keys=True, ensure_ascii=False)

    def complete(self, system: str, user: str, response_schema: dict[str, Any]) -> dict[str, Any]:
        key = self._key(system, user, response_schema)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        result = self.provider.complete(system=system, user=user, response_schema=response_schema)
        self.cache.set(key, result)
        return result


def build_provider_from_settings(settings: Any) -> LLMProvider:
    """Factory selecting a provider by JAW_LLM_PROVIDER. Never performs network I/O."""
    provider_name = (getattr(settings, "llm_provider", None) or "none").strip().lower()
    if provider_name in ("", "none"):
        return NullProvider()
    if provider_name in ("openai_compatible", "openai", "groq", "ollama"):
        base_url = getattr(settings, "llm_base_url", None)
        model = getattr(settings, "llm_model", None)
        if not base_url or not model:
            logger.warning(
                "JAW_LLM_PROVIDER=%s but JAW_LLM_BASE_URL/JAW_LLM_MODEL are not both set; falling back to NullProvider.",
                provider_name,
            )
            return NullProvider()
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=getattr(settings, "llm_api_key", None),
            model=model,
            timeout=float(getattr(settings, "llm_timeout", 30) or 30),
        )
    logger.warning("Unknown JAW_LLM_PROVIDER=%s; falling back to NullProvider.", provider_name)
    return NullProvider()
