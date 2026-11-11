import json

import groq
import httpx
import ollama
import pytest

from src.config import Settings, load_settings
from src.llm import (
    GROQ_MAX_RETRIES,
    GROQ_MAX_TOKENS,
    GROQ_REASONING_EFFORT,
    GROQ_TIMEOUT_S,
    OLLAMA_SEED,
    OLLAMA_TIMEOUT_S,
    ProviderErrorKind,
    build_chat_model,
    classify_provider_error,
)

_TENANT_KEYS = {"test-key": {"tenant_id": "acme", "role": "lawyer", "name": "Ayse"}}
_REQUEST = httpx.Request("POST", "https://api.groq.com")


def _settings(provider: str = "groq") -> Settings:
    env = {"LLM_PROVIDER": provider, "TENANT_API_KEYS": json.dumps(_TENANT_KEYS)}
    if provider == "groq":
        env["GROQ_API_KEY"] = "gsk-test"
    return load_settings(env)


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=_REQUEST)


def test_rate_limit_retries_are_bounded() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            429,
            headers={"retry-after-ms": "1"},
            json={"error": {"message": "rate limited", "type": "rate_limit"}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = build_chat_model(_settings(), http_client=client)
    with pytest.raises(groq.RateLimitError):
        model.invoke("hi")
    assert len(calls) == GROQ_MAX_RETRIES + 1


def test_groq_model_parameters() -> None:
    settings = _settings()
    model = build_chat_model(settings)
    assert model.max_tokens == GROQ_MAX_TOKENS == 2048  # type: ignore[attr-defined]
    assert model.reasoning_effort == GROQ_REASONING_EFFORT == "low"  # type: ignore[attr-defined]
    assert model.max_retries == GROQ_MAX_RETRIES == 3  # type: ignore[attr-defined]
    assert model.request_timeout == GROQ_TIMEOUT_S == 60  # type: ignore[attr-defined]
    assert model.reasoning_format is None  # type: ignore[attr-defined]
    assert model.temperature <= 1e-8  # type: ignore[attr-defined]


def test_groq_request_body_on_the_wire() -> None:
    settings = _settings()
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(401, json={"error": {"message": "stop", "type": "auth"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = build_chat_model(settings, http_client=client)
    with pytest.raises(groq.AuthenticationError):
        model.invoke("hi")
    assert len(bodies) == 1
    body = bodies[0]
    assert body["model"] == settings.llm_model
    assert body["max_tokens"] == GROQ_MAX_TOKENS
    assert body["reasoning_effort"] == "low"
    temperature = body["temperature"]
    assert isinstance(temperature, float) and temperature <= 1e-8
    assert body.get("reasoning_format") is None


def test_ollama_model_parameters() -> None:
    settings = _settings("ollama")
    model = build_chat_model(settings)
    assert model.temperature == 0  # type: ignore[attr-defined]
    assert model.base_url == settings.ollama_base_url  # type: ignore[attr-defined]
    assert model.num_ctx == settings.llm_num_ctx  # type: ignore[attr-defined]
    assert model.reasoning is False  # type: ignore[attr-defined]
    assert model.seed == OLLAMA_SEED  # type: ignore[attr-defined]
    assert model.client_kwargs == {"timeout": OLLAMA_TIMEOUT_S}  # type: ignore[attr-defined]
    assert model.client_kwargs["timeout"] == pytest.approx(120.0)  # type: ignore[attr-defined]
    assert model.model == settings.llm_model  # type: ignore[attr-defined]


def test_ollama_rejects_http_client() -> None:
    with httpx.Client() as client, pytest.raises(ValueError, match="only supported"):
        build_chat_model(_settings("ollama"), http_client=client)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (groq.AuthenticationError("x", response=_response(401), body=None), "config"),
        (groq.PermissionDeniedError("x", response=_response(403), body=None), "config"),
        (groq.NotFoundError("x", response=_response(404), body=None), "config"),
        (ollama.ResponseError("not found", 404), "config"),
        (groq.RateLimitError("x", response=_response(429), body=None), "rate_limit"),
        (groq.BadRequestError("x", response=_response(400), body=None), "other"),
        (groq.InternalServerError("x", response=_response(500), body=None), "other"),
        (groq.APITimeoutError(request=_REQUEST), "other"),
        (httpx.ConnectError("refused"), "other"),
        (ConnectionError("refused"), "other"),
        (ollama.ResponseError("boom", 500), "other"),
        (ValueError("nope"), "other"),
    ],
)
def test_classify_provider_error(exc: BaseException, expected: ProviderErrorKind) -> None:
    assert classify_provider_error(exc) == expected


def test_unauthorized_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "bad key", "type": "auth"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = build_chat_model(_settings(), http_client=client)
    with pytest.raises(groq.AuthenticationError) as info:
        model.invoke("hi")
    assert len(calls) == 1
    assert classify_provider_error(info.value) == "config"
