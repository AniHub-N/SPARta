from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jaw_ingest.cache import CacheManager
from jaw_ingest.llm_provider import (
    CachingLLMProvider,
    NullProvider,
    OpenAICompatibleProvider,
    ProviderNotConfigured,
    ProviderRequestError,
    _json_schema_name,
    _strict_json_schema,
    build_provider_from_settings,
)
from jaw_ingest.semantic_schemas import SemanticExtractionResult


class _FakeSettings:
    def __init__(self, **kwargs) -> None:
        self.llm_provider = kwargs.get("llm_provider", "none")
        self.llm_base_url = kwargs.get("llm_base_url")
        self.llm_api_key = kwargs.get("llm_api_key")
        self.llm_model = kwargs.get("llm_model")
        self.llm_timeout = kwargs.get("llm_timeout", 30)


def test_null_provider_raises_without_network_call() -> None:
    provider = NullProvider()
    with pytest.raises(ProviderNotConfigured):
        provider.complete(system="s", user="u", response_schema={})


def test_build_provider_defaults_to_null_when_unset() -> None:
    provider = build_provider_from_settings(_FakeSettings())
    assert isinstance(provider, NullProvider)


def test_build_provider_defaults_to_null_when_base_url_missing() -> None:
    provider = build_provider_from_settings(_FakeSettings(llm_provider="openai_compatible", llm_model="gpt-4o-mini"))
    assert isinstance(provider, NullProvider)


def test_build_provider_returns_openai_compatible_when_configured() -> None:
    provider = build_provider_from_settings(
        _FakeSettings(llm_provider="openai_compatible", llm_base_url="http://localhost:11434/v1", llm_model="llama3")
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.model == "llama3"


def test_openai_compatible_provider_construction_makes_no_network_call() -> None:
    # Constructing the provider must never touch the network - only .complete() does.
    OpenAICompatibleProvider(base_url="http://example.invalid/v1", api_key="k", model="m")


def test_openai_compatible_provider_sends_expected_request_shape() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"entities": [], "relationships": [], "attributes": []}'}}]},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    provider = OpenAICompatibleProvider(base_url="http://fake/v1", api_key="secret-key", model="test-model", client=client)

    result = provider.complete(system="sys", user="usr", response_schema={"type": "object"})

    assert result == {"entities": [], "relationships": [], "attributes": []}
    assert captured["url"] == "http://fake/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer secret-key"


def test_openai_compatible_provider_raises_on_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(base_url="http://fake/v1", api_key=None, model="test-model", client=client)

    with pytest.raises(ProviderRequestError):
        provider.complete(system="sys", user="usr", response_schema={"type": "object"})


def test_openai_compatible_provider_raises_on_malformed_json_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(base_url="http://fake/v1", api_key=None, model="test-model", client=client)

    with pytest.raises(ProviderRequestError):
        provider.complete(system="sys", user="usr", response_schema={"type": "object"})


def test_strict_json_schema_sets_required_and_additional_properties_at_every_level() -> None:
    raw = SemanticExtractionResult.json_schema()
    # Precondition: Pydantic's raw schema is NOT strict-mode compliant - optional
    # (defaulted) fields are omitted from "required", and nested $defs lack
    # additionalProperties. If this precondition ever stops holding (a Pydantic
    # version change), the sanitizer is still safe to run, just less necessary.
    assert "required" not in raw or set(raw["required"]) != set(raw["properties"].keys())

    strict = _strict_json_schema(raw)

    assert strict["additionalProperties"] is False
    assert set(strict["required"]) == set(strict["properties"].keys())
    for name, sub_schema in strict.get("$defs", {}).items():
        if sub_schema.get("type") == "object" and "properties" in sub_schema:
            assert sub_schema["additionalProperties"] is False, name
            assert set(sub_schema["required"]) == set(sub_schema["properties"].keys()), name


def test_strict_json_schema_does_not_mutate_the_input() -> None:
    raw = SemanticExtractionResult.json_schema()
    raw_copy = json.loads(json.dumps(raw))

    _strict_json_schema(raw)

    assert raw == raw_copy


def test_strict_json_schema_handles_schema_with_no_properties() -> None:
    assert _strict_json_schema({"type": "object"}) == {"type": "object"}


def test_json_schema_name_uses_pydantic_title() -> None:
    assert _json_schema_name(SemanticExtractionResult.json_schema()) == "SemanticExtractionResult"


def test_json_schema_name_falls_back_and_sanitizes() -> None:
    assert _json_schema_name({}) == "response"
    assert _json_schema_name({"title": "weird name! with $ymbols"}) == "weird_name__with__ymbols"


def test_falls_back_to_json_object_mode_when_json_schema_mode_gets_a_400() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "Failed to validate JSON. Please adjust your prompt.", "code": "json_validate_failed"}})
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"entities": [], "relationships": [], "attributes": []}'}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(base_url="http://fake/v1", api_key=None, model="openai/gpt-oss-120b", client=client)

    result = provider.complete(system="sys", user="usr", response_schema=SemanticExtractionResult.json_schema())

    assert result == {"entities": [], "relationships": [], "attributes": []}
    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"] == {"type": "json_object"}
    # The schema must still be communicated to the model in the fallback path.
    assert "properties" in calls[1]["messages"][-1]["content"]


def test_still_raises_if_both_json_schema_and_json_object_modes_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "nope"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(base_url="http://fake/v1", api_key=None, model="test-model", client=client)

    with pytest.raises(ProviderRequestError):
        provider.complete(system="sys", user="usr", response_schema={"type": "object", "properties": {}})


class _CountingProvider:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = 0

    def complete(self, system: str, user: str, response_schema: dict) -> dict:
        self.calls += 1
        return self.response


def test_caching_provider_calls_underlying_provider_only_once_for_identical_request(tmp_path: Path) -> None:
    inner = _CountingProvider({"entities": [], "relationships": [], "attributes": []})
    cache = CacheManager(tmp_path / "cache")
    provider = CachingLLMProvider(inner, cache)

    first = provider.complete(system="sys", user="same question", response_schema={"type": "object"})
    second = provider.complete(system="sys", user="same question", response_schema={"type": "object"})

    assert first == second == {"entities": [], "relationships": [], "attributes": []}
    assert inner.calls == 1


def test_caching_provider_calls_again_for_a_different_prompt(tmp_path: Path) -> None:
    inner = _CountingProvider({"ok": True})
    cache = CacheManager(tmp_path / "cache")
    provider = CachingLLMProvider(inner, cache)

    provider.complete(system="sys", user="question A", response_schema={"type": "object"})
    provider.complete(system="sys", user="question B", response_schema={"type": "object"})

    assert inner.calls == 2


def test_caching_provider_persists_across_new_instances(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    inner1 = _CountingProvider({"v": 1})
    CachingLLMProvider(inner1, CacheManager(cache_dir)).complete(system="s", user="u", response_schema={})
    assert inner1.calls == 1

    inner2 = _CountingProvider({"v": 2})
    result = CachingLLMProvider(inner2, CacheManager(cache_dir)).complete(system="s", user="u", response_schema={})

    assert result == {"v": 1}  # served from disk cache, inner2 never called
    assert inner2.calls == 0


def test_post_retries_transient_timeout_then_succeeds(monkeypatch) -> None:
    from jaw_ingest import llm_provider as llm_provider_module

    sleeps: list[float] = []
    monkeypatch.setattr(llm_provider_module.time, "sleep", lambda seconds: sleeps.append(seconds))

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(base_url="http://fake/v1", api_key=None, model="test-model", client=client)

    result = provider.complete(system="sys", user="usr", response_schema={"type": "object"})

    assert result == {"ok": True}
    assert attempts["count"] == 3
    assert len(sleeps) == 2  # backoff before each of the two retried attempts, no sleep needed had it not failed


def test_post_gives_up_after_max_retries_on_persistent_timeout(monkeypatch) -> None:
    from jaw_ingest import llm_provider as llm_provider_module

    monkeypatch.setattr(llm_provider_module.time, "sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("always times out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(base_url="http://fake/v1", api_key=None, model="test-model", client=client)

    with pytest.raises(ProviderRequestError):
        provider.complete(system="sys", user="usr", response_schema={"type": "object"})
