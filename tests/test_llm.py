import json
import sys
import time
from collections.abc import Callable, Iterator
from typing import Any

import groq
import httpx
import langchain.chat_models.base as chat_models_base
import ollama
import pytest
from langchain.chat_models import init_chat_model
from langchain_core.exceptions import (
    ModelAPIError,
    ModelAuthenticationError,
    ModelNotFoundError,
    ModelPermissionDeniedError,
    ModelRateLimitError,
)
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_google_genai.chat_models import (
    GoogleAPIError,
    GoogleAuthenticationError,
    GoogleInvalidRequestError,
    GoogleModelNotFoundError,
    GooglePermissionDeniedError,
    GoogleRateLimitError,
)

import src.llm as llm_module
from src.config import ConfigError, Settings, load_settings
from src.llm import (
    COMMON_MAX_RETRIES,
    COMMON_TIMEOUT_S,
    GROQ_MAX_RETRIES,
    GROQ_MAX_TOKENS,
    GROQ_REASONING_EFFORT,
    GROQ_TIMEOUT_S,
    OLLAMA_SEED,
    OLLAMA_TIMEOUT_S,
    ProviderErrorKind,
    build_chat_model,
    check_chat_model,
    classify_provider_error,
    provider_status_code,
)
from src.tools import TOOLS

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


# --- Provider-agnostic model setup (step 18c) -----------------------------------------------------

_CHAT_COMPLETION = {
    "id": "x",
    "object": "chat.completion",
    "created": 1,
    "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
# Every field the openai chat completions body may carry with the common parameters only.
_COMMON_BODY_KEYS = {"model", "messages", "stream", "max_completion_tokens", "temperature"}


def _env_settings(provider: str, **extra: str) -> Settings:
    env = {"LLM_PROVIDER": provider, "TENANT_API_KEYS": json.dumps(_TENANT_KEYS)} | extra
    return load_settings(env)


def _openai_settings(**extra: str) -> Settings:
    return _env_settings("openai", **({"LLM_MODEL": "gpt-test", "LLM_API_KEY": "sk-test"} | extra))


def _capture(status: int = 200) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status == 200:
            return httpx.Response(200, json=_CHAT_COMPLETION)
        return httpx.Response(status, json={"error": {"message": "x", "type": "t", "code": "c"}})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _body(request: httpx.Request) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(request.content)
    return body


def _recording_init(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Replace `init_chat_model` in src.llm; record (model, kwargs) and build nothing real."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake(model: str, **kwargs: Any) -> object:
        calls.append((model, kwargs))
        return object()

    monkeypatch.setattr(llm_module, "init_chat_model", fake)
    return calls


def test_openai_request_carries_only_common_parameters() -> None:
    client, seen = _capture()
    model = build_chat_model(_openai_settings(), http_client=client)
    model.invoke("hi")
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    body = _body(request)
    assert set(body) <= _COMMON_BODY_KEYS
    assert "reasoning_effort" not in body
    assert body["model"] == "gpt-test"
    assert body["temperature"] == 0
    assert body["max_completion_tokens"] == 2048
    assert model.max_retries == COMMON_MAX_RETRIES == 3  # type: ignore[attr-defined]
    assert model.request_timeout == COMMON_TIMEOUT_S == 60  # type: ignore[attr-defined]


_ENV_BASE_URL_VARIABLES = ("OPENAI_BASE_URL", "OPENAI_API_BASE")


def test_openai_without_llm_base_url_ignores_base_url_variables_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _ENV_BASE_URL_VARIABLES:
        monkeypatch.setenv(name, "http://attacker.invalid/v1")
    client, seen = _capture()
    build_chat_model(_openai_settings(), http_client=client).invoke("hi")
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.openai.com/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-test"


def test_openai_default_address_is_passed_explicitly_without_responses_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _recording_init(monkeypatch)
    build_chat_model(_openai_settings())
    [(_, kwargs)] = calls
    assert kwargs["base_url"] == "https://api.openai.com/v1"
    assert "use_responses_api" not in kwargs


def test_anthropic_default_address_is_passed_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_URL"):
        monkeypatch.setenv(name, "http://attacker.invalid")
    calls = _recording_init(monkeypatch)
    build_chat_model(_env_settings("anthropic", LLM_MODEL="claude-test", LLM_API_KEY="k"))
    [(_, kwargs)] = calls
    assert kwargs["base_url"] == "https://api.anthropic.com"
    assert "use_responses_api" not in kwargs


def test_openai_temperature_none_leaves_the_field_out() -> None:
    client, seen = _capture()
    build_chat_model(_openai_settings(LLM_TEMPERATURE="none"), http_client=client).invoke("hi")
    body = _body(seen[0])
    assert "temperature" not in body
    assert set(body) <= _COMMON_BODY_KEYS


def test_openai_temperature_value_reaches_the_request() -> None:
    client, seen = _capture()
    build_chat_model(_openai_settings(LLM_TEMPERATURE="0.4"), http_client=client).invoke("hi")
    assert _body(seen[0])["temperature"] == pytest.approx(0.4)


def test_openai_max_tokens_reaches_the_output_limit() -> None:
    client, seen = _capture()
    build_chat_model(_openai_settings(LLM_MAX_TOKENS="321"), http_client=client).invoke("hi")
    assert _body(seen[0])["max_completion_tokens"] == 321


@pytest.mark.parametrize("model_name", ["gpt-test", "my-codex-finetune", "gpt-5-pro-x"])
def test_openai_request_goes_to_llm_base_url_chat_completions(model_name: str) -> None:
    client, seen = _capture()
    settings = _env_settings(
        "openai",
        LLM_MODEL=model_name,
        LLM_API_KEY="sk-test",
        LLM_BASE_URL="http://compat.local:9000/v1",
    )
    build_chat_model(settings, http_client=client).invoke("hi")
    assert str(seen[0].url) == "http://compat.local:9000/v1/chat/completions"
    assert set(_body(seen[0])) <= _COMMON_BODY_KEYS


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "config"), (403, "config"), (404, "config"), (400, "other")],
)
def test_openai_wire_errors_are_classified(status: int, expected: ProviderErrorKind) -> None:
    client, seen = _capture(status)
    model = build_chat_model(_openai_settings(), http_client=client)
    with pytest.raises(Exception) as info:
        model.invoke("hi")
    assert len(seen) == 1  # 4xx (but 408 / 409 / 429) is not retried
    assert classify_provider_error(info.value) == expected
    assert provider_status_code(info.value) == status


@pytest.mark.parametrize("provider", ["anthropic", "google_genai", "mistralai", "ollama"])
def test_http_client_is_refused_outside_groq_and_openai(provider: str) -> None:
    extra = {} if provider == "ollama" else {"LLM_MODEL": "m", "LLM_API_KEY": "k"}
    settings = _env_settings(provider, **extra)
    with httpx.Client() as client, pytest.raises(ValueError, match="only supported"):
        build_chat_model(settings, http_client=client)


def test_groq_kwargs_unchanged_when_new_variables_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _recording_init(monkeypatch)
    build_chat_model(_settings())
    # Literal 2048, not GROQ_MAX_TOKENS: that constant now follows DEFAULT_MAX_TOKENS, so a moved
    # default would move the expectation with it. The measured Groq request carries 2048.
    assert calls == [
        (
            "openai/gpt-oss-120b",
            {
                "model_provider": "groq",
                "api_key": "gsk-test",
                "temperature": 0.0,
                "max_tokens": 2048,
                "reasoning_effort": GROQ_REASONING_EFFORT,
                "max_retries": GROQ_MAX_RETRIES,
                "timeout": GROQ_TIMEOUT_S,
                "http_client": None,
            },
        )
    ]
    [(_, kwargs)] = calls
    # `0 == 0.0`, so the dict comparison above cannot see an int; the request needs a float 0.0.
    assert type(kwargs["temperature"]) is float
    assert kwargs["temperature"] == 0.0
    assert type(kwargs["max_tokens"]) is int


def test_ollama_kwargs_unchanged_when_new_variables_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _recording_init(monkeypatch)
    settings = _env_settings("ollama", LLM_API_KEY="left-over", LLM_MAX_TOKENS="99")
    build_chat_model(settings)
    assert calls == [
        (
            "qwen3.5:9b",
            {
                "model_provider": "ollama",
                "base_url": settings.ollama_base_url,
                "temperature": 0,
                "num_ctx": settings.llm_num_ctx,
                "reasoning": False,
                "seed": OLLAMA_SEED,
                "client_kwargs": {"timeout": OLLAMA_TIMEOUT_S},
            },
        )
    ]


def test_ollama_temperature_none_is_passed_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _recording_init(monkeypatch)
    build_chat_model(_env_settings("ollama", LLM_TEMPERATURE="none"))
    [(_, kwargs)] = calls
    assert "temperature" in kwargs
    assert kwargs["temperature"] is None


def test_groq_uses_llm_api_key_when_groq_api_key_is_empty() -> None:
    client, seen = _capture(401)
    settings = _env_settings("groq", LLM_API_KEY="llm-key")
    with pytest.raises(groq.AuthenticationError):
        build_chat_model(settings, http_client=client).invoke("hi")
    assert seen[0].headers["authorization"] == "Bearer llm-key"


def test_groq_prefers_groq_api_key_over_llm_api_key() -> None:
    client, seen = _capture(401)
    settings = _env_settings("groq", GROQ_API_KEY="groq-key", LLM_API_KEY="llm-key")
    with pytest.raises(groq.AuthenticationError):
        build_chat_model(settings, http_client=client).invoke("hi")
    assert seen[0].headers["authorization"] == "Bearer groq-key"


def test_groq_new_variables_reach_the_request() -> None:
    client, seen = _capture(401)
    settings = _env_settings(
        "groq",
        GROQ_API_KEY="k",
        LLM_MAX_TOKENS="512",
        LLM_TEMPERATURE="0.25",
        LLM_BASE_URL="http://groq-proxy.local:8080",
    )
    with pytest.raises(groq.AuthenticationError):
        build_chat_model(settings, http_client=client).invoke("hi")
    body = _body(seen[0])
    assert seen[0].url.host == "groq-proxy.local"
    assert body["max_tokens"] == 512
    assert body["temperature"] == pytest.approx(0.25)
    assert body["reasoning_effort"] == "low"  # still a groq model


@pytest.mark.parametrize(
    ("provider", "extra", "expected_extra"),
    [
        ("openai", {}, {"api_key": "k", "base_url": "https://api.openai.com/v1"}),
        ("anthropic", {}, {"api_key": "k", "base_url": "https://api.anthropic.com"}),
        ("mistralai", {}, {"api_key": "k"}),
        (
            "openai",
            {"LLM_BASE_URL": "http://compat/v1"},
            {"api_key": "k", "base_url": "http://compat/v1", "use_responses_api": False},
        ),
        (
            "anthropic",
            {"LLM_BASE_URL": "http://proxy"},
            {"api_key": "k", "base_url": "http://proxy"},
        ),
        (
            "google_genai",
            {"LLM_TEMPERATURE": "none"},
            {
                "api_key": "k",
                "temperature": None,
                "base_url": "https://generativelanguage.googleapis.com/",
            },
        ),
    ],
)
def test_common_parameters_only_outside_groq_and_ollama(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    extra: dict[str, str],
    expected_extra: dict[str, object],
) -> None:
    calls = _recording_init(monkeypatch)
    build_chat_model(_env_settings(provider, LLM_MODEL="the-model", LLM_API_KEY="k", **extra))
    expected: dict[str, object] = {
        "model_provider": provider,
        "max_tokens": 2048,
        "timeout": COMMON_TIMEOUT_S,
        "max_retries": COMMON_MAX_RETRIES,
        "temperature": 0,
    } | expected_extra
    assert calls == [("the-model", expected)]


def test_unlisted_provider_without_llm_api_key_passes_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _recording_init(monkeypatch)
    build_chat_model(_env_settings("bedrock_converse", LLM_MODEL="m"))
    [(_, kwargs)] = calls
    assert "api_key" not in kwargs
    assert "base_url" not in kwargs


# --- Error classification, provider-agnostic ------------------------------------------------------


class _Status(Exception):
    def __init__(self, status_code: object) -> None:
        super().__init__("status")
        self.status_code = status_code


class _WithResponse(Exception):
    def __init__(self, status: int) -> None:
        super().__init__("response")
        self.response = httpx.Response(status)


class _Code(Exception):
    def __init__(self, code: object) -> None:
        super().__init__("code")
        self.code = code


def _wrapped(cause: BaseException) -> BaseException:
    wrapper = RuntimeError("wrapper without a status")
    wrapper.__cause__ = cause
    return wrapper


def _in_context(inner: BaseException) -> BaseException:
    outer = RuntimeError("raised while handling")
    outer.__context__ = inner
    return outer


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_Status(401), "config"),
        (_Status(403), "config"),
        (_Status(404), "config"),
        (_Status(429), "rate_limit"),
        (_Status(400), "other"),
        (_Status(500), "other"),
        (_WithResponse(401), "config"),
        (_WithResponse(429), "rate_limit"),
        (_WithResponse(503), "other"),
        (_Code(404), "config"),
        (_Code(429), "rate_limit"),
        (_Code("401"), "other"),
        (_Code("insufficient_quota"), "other"),
        (_Code(True), "other"),
        (_Status(True), "other"),
        (_Status("401"), "other"),
        (RuntimeError("no code"), "other"),
        (_wrapped(_Code(401)), "config"),
        (_wrapped(_Code(403)), "config"),
        (_wrapped(_Code(404)), "config"),
        (_wrapped(_Code(429)), "rate_limit"),
        (_wrapped(_WithResponse(401)), "config"),
        (_wrapped(_Status(500)), "other"),
        (_wrapped(_Code(True)), "other"),
        (_wrapped(RuntimeError("no code")), "other"),
        (_wrapped(_wrapped(_Code(401))), "other"),  # only one step into __cause__
        (_in_context(_Status(401)), "other"),  # __context__ is not walked
        (ModelAuthenticationError("x"), "config"),
        (ModelPermissionDeniedError("x"), "config"),
        (ModelNotFoundError("x"), "config"),
        (ModelRateLimitError("x"), "rate_limit"),
        (ModelAPIError("x"), "other"),
    ],
)
def test_classify_provider_error_by_status_and_core_classes(
    exc: BaseException, expected: ProviderErrorKind
) -> None:
    assert classify_provider_error(exc) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "config"), (403, "config"), (429, "rate_limit")],
)
def test_real_ollama_response_errors_follow_the_general_status_rule(
    status: int, expected: ProviderErrorKind
) -> None:
    # The accepted widening (docs/agent_design.md §6): a remote or proxied Ollama.
    exc = ollama.ResponseError("remote ollama", status)
    assert provider_status_code(exc) == status
    assert classify_provider_error(exc) == expected


class _ResponseRaises(Exception):
    @property
    def response(self) -> object:
        raise RuntimeError("no response attached")


def test_status_reader_treats_a_raising_property_as_absent() -> None:
    exc = _ResponseRaises("x")
    assert provider_status_code(exc) is None
    assert classify_provider_error(exc) == "other"


def test_core_class_wins_over_the_status_code() -> None:
    class _AuthWith500(ModelAuthenticationError):
        status_code = 500

    assert classify_provider_error(_AuthWith500("x")) == "config"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_Status(429), 429),
        (_WithResponse(404), 404),
        (_Code(401), 401),
        (_wrapped(_Code(403)), 403),
        (ollama.ResponseError("stream", -1), -1),
        (_Status(True), None),
        (_Code("401"), None),
        (RuntimeError("x"), None),
        (_in_context(_Status(401)), None),
    ],
)
def test_provider_status_code_reader(exc: BaseException, expected: int | None) -> None:
    assert provider_status_code(exc) == expected


def test_provider_status_code_prefers_the_exception_over_its_cause() -> None:
    exc = _Status(429)
    exc.__cause__ = _Code(401)
    assert provider_status_code(exc) == 429
    assert classify_provider_error(exc) == "rate_limit"


# --- Startup model check --------------------------------------------------------------------------


@pytest.fixture
def fresh_creator_cache() -> Iterator[None]:
    """langchain caches a provider's creator after a successful import; start and end clean."""
    chat_models_base._get_chat_model_creator.cache_clear()
    yield
    chat_models_base._get_chat_model_creator.cache_clear()


@pytest.mark.parametrize(
    ("provider", "module", "package", "group"),
    [
        ("openai", "langchain_openai", "langchain-openai", "openai"),
        ("anthropic", "langchain_anthropic", "langchain-anthropic", "anthropic"),
        ("google_genai", "langchain_google_genai", "langchain-google-genai", "google"),
        ("mistralai", "langchain_mistralai", "langchain-mistralai", None),
    ],
)
def test_missing_integration_package_stops_startup_naming_it(
    monkeypatch: pytest.MonkeyPatch,
    fresh_creator_cache: None,
    provider: str,
    module: str,
    package: str,
    group: str | None,
) -> None:
    monkeypatch.setitem(sys.modules, module, None)  # import fails, whatever is installed
    settings = _env_settings(provider, LLM_MODEL="m", LLM_API_KEY="secret-key-value")
    with pytest.raises(ConfigError) as info:
        check_chat_model(settings)
    message = str(info.value)
    assert package in message
    assert "LLM_EXTRAS" in message
    if group is not None:
        assert f"--extra {group}" in message
    assert "secret-key-value" not in message
    assert "\n" not in message


def test_unknown_provider_stops_startup_naming_llm_provider() -> None:
    settings = _env_settings("acme_llm", LLM_MODEL="m", LLM_API_KEY="secret-key-value")
    with pytest.raises(ConfigError) as info:
        check_chat_model(settings)
    message = str(info.value)
    assert "LLM_PROVIDER" in message
    assert "acme_llm" in message
    # The unknown-provider wording, not the generic construction message that also names it.
    assert "LangChain'in tanıdığı bir sağlayıcı değil" in message
    assert "kurulamadı" not in message
    assert "secret-key-value" not in message
    assert "\n" not in message


@pytest.mark.parametrize(
    ("provider", "module", "package"),
    [
        ("groq", "langchain_groq", "langchain-groq"),
        ("ollama", "langchain_ollama", "langchain-ollama"),
    ],
)
def test_missing_core_package_says_the_environment_is_not_installed_from_the_lock(
    monkeypatch: pytest.MonkeyPatch,
    fresh_creator_cache: None,
    provider: str,
    module: str,
    package: str,
) -> None:
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(ConfigError) as info:
        check_chat_model(_settings(provider))
    message = str(info.value)
    assert package in message
    assert "uv sync" in message
    assert "pyproject.toml" not in message
    assert "gsk-test" not in message
    assert "\n" not in message


class _CredentialRejected(Exception):
    """Stands for an SDK's own credential error raised at construction (not a ValueError)."""


def _raiser(exc: BaseException) -> Callable[..., object]:
    def fake(*args: object, **kwargs: object) -> object:
        raise exc

    return fake


@pytest.mark.parametrize(
    "make",
    [
        lambda key: ValueError(f"1 validation error\n  input_value={{'api_key': '{key}'}}"),
        lambda key: _CredentialRejected(f"bad credential {key}"),
        lambda key: RuntimeError(key),
        lambda key: ImportError(f"no module {key}"),
        lambda key: ImportError(f"Initializing X requires the {key} package."),
    ],
)
def test_construction_error_text_never_reaches_the_config_error(
    monkeypatch: pytest.MonkeyPatch, make: Callable[[str], BaseException]
) -> None:
    key = "sk-LEAK-0123456789"
    monkeypatch.setattr(llm_module, "init_chat_model", _raiser(make(key)))
    for settings in (
        _openai_settings(LLM_API_KEY=key),
        _env_settings("groq", GROQ_API_KEY=key),
        _env_settings("groq", LLM_API_KEY=key),
        _env_settings("mistralai", LLM_MODEL="m", LLM_API_KEY=key),
    ):
        with pytest.raises(ConfigError) as info:
            check_chat_model(settings)
        message = str(info.value)
        assert key not in message
        assert "LEAK" not in message
        assert "\n" not in message
        assert "LLM_PROVIDER" in message
        assert info.value.__cause__ is None  # nothing chains the original text along
        assert info.value.__suppress_context__ is True  # `from None`


def test_check_chat_model_passes_for_the_default_providers() -> None:
    check_chat_model(_settings())
    check_chat_model(_settings("ollama"))
    check_chat_model(_openai_settings())


# --- google_genai (step 18d) ----------------------------------------------------------------------

_GEMINI_PUBLIC = "https://generativelanguage.googleapis.com/"
_GEMINI_URL = _GEMINI_PUBLIC + "v1beta/models/gemini-test:generateContent"
_GEMINI_OK = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": "ok"}]},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
}
# Every variable the integration or the google-genai SDK reads for the backend, address or key.
_GOOGLE_ENV_VARIABLES = (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GEMINI_BASE_URL",
    "GOOGLE_VERTEX_BASE_URL",
    "GOOGLE_GENAI_API_VERSION",
    "GOOGLE_GENAI_HTTP_CLIENT",
    "LANGSMITH_GATEWAY",
    "LANGSMITH_GATEWAY_API_KEY",
)


def _google_settings(**extra: str) -> Settings:
    return _env_settings(
        "google_genai", **({"LLM_MODEL": "gemini-test", "LLM_API_KEY": "llm-key"} | extra)
    )


@pytest.fixture
def clean_google_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GOOGLE_ENV_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _google_wire(
    monkeypatch: pytest.MonkeyPatch,
    status: int = 200,
    replies: tuple[dict[str, Any], ...] = (_GEMINI_OK,),
) -> list[httpx.Request]:
    """Build google_genai models with a fake transport: `init_chat_model` in src.llm is wrapped
    and `client_args` added to the arguments `build_chat_model` passes. No retry sleeps.
    With status 200 the n-th request gets the n-th reply (the last one repeats)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status == 200:
            return httpx.Response(200, json=replies[min(len(seen), len(replies)) - 1])
        error = {"error": {"code": status, "message": "x", "status": "X"}}
        return httpx.Response(status, json=error)

    def with_transport(model: str, **kwargs: Any) -> object:
        assert "client_args" not in kwargs  # build_chat_model passes no transport of its own
        transport = {"transport": httpx.MockTransport(handler)}
        return init_chat_model(model, **kwargs, client_args=transport)

    monkeypatch.setattr(llm_module, "init_chat_model", with_transport)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    return seen


def _generation_config(request: httpx.Request) -> dict[str, Any]:
    config: dict[str, Any] = _body(request)["generationConfig"]
    return config


def test_google_genai_gets_the_common_parameters_and_the_public_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _recording_init(monkeypatch)
    build_chat_model(_google_settings())
    assert calls == [
        (
            "gemini-test",
            {
                "model_provider": "google_genai",
                "max_tokens": 2048,
                "timeout": COMMON_TIMEOUT_S,
                "max_retries": COMMON_MAX_RETRIES,
                "temperature": 0,
                "api_key": "llm-key",
                "base_url": _GEMINI_PUBLIC,
            },
        )
    ]


def test_google_genai_llm_base_url_replaces_the_public_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _recording_init(monkeypatch)
    build_chat_model(_google_settings(LLM_BASE_URL="http://gemini-proxy.local:7000/"))
    [(_, kwargs)] = calls
    assert kwargs["base_url"] == "http://gemini-proxy.local:7000/"
    assert "vertexai" not in kwargs
    assert "use_responses_api" not in kwargs


def test_google_genai_request_on_the_wire(
    monkeypatch: pytest.MonkeyPatch, clean_google_env: None
) -> None:
    # Keys in the environment must not replace LLM_API_KEY on the request.
    monkeypatch.setenv("GOOGLE_API_KEY", "google-env-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-env-key")
    seen = _google_wire(monkeypatch)
    model = build_chat_model(_google_settings())
    model.invoke("hi")
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == _GEMINI_URL
    assert request.headers["x-goog-api-key"] == "llm-key"
    config = _generation_config(request)
    assert config["maxOutputTokens"] == 2048
    assert config["temperature"] == 0
    assert model.max_retries == COMMON_MAX_RETRIES  # type: ignore[attr-defined]


def test_google_genai_temperature_none_leaves_the_field_out(
    monkeypatch: pytest.MonkeyPatch, clean_google_env: None
) -> None:
    seen = _google_wire(monkeypatch)
    build_chat_model(_google_settings(LLM_TEMPERATURE="none")).invoke("hi")
    config = _generation_config(seen[0])
    assert "temperature" not in config
    assert config["maxOutputTokens"] == 2048


def test_google_genai_max_tokens_and_temperature_reach_the_request(
    monkeypatch: pytest.MonkeyPatch, clean_google_env: None
) -> None:
    seen = _google_wire(monkeypatch)
    settings = _google_settings(LLM_MAX_TOKENS="321", LLM_TEMPERATURE="0.4")
    build_chat_model(settings).invoke("hi")
    config = _generation_config(seen[0])
    assert config["maxOutputTokens"] == 321
    assert config["temperature"] == pytest.approx(0.4)


def test_google_genai_request_goes_to_llm_base_url(
    monkeypatch: pytest.MonkeyPatch, clean_google_env: None
) -> None:
    seen = _google_wire(monkeypatch)
    build_chat_model(_google_settings(LLM_BASE_URL="http://gemini-proxy.local:7000/")).invoke("hi")
    assert (
        str(seen[0].url)
        == "http://gemini-proxy.local:7000/v1beta/models/gemini-test:generateContent"
    )
    assert seen[0].headers["x-goog-api-key"] == "llm-key"


@pytest.mark.parametrize(
    "variables",
    [
        {"GOOGLE_GEMINI_BASE_URL": "http://attacker.invalid/"},
        {"LANGSMITH_GATEWAY": "true", "LANGSMITH_GATEWAY_API_KEY": "ls-key"},
        {"LANGSMITH_GATEWAY": "http://gateway.invalid"},
        {"GOOGLE_GEMINI_BASE_URL": "http://attacker.invalid/", "LANGSMITH_GATEWAY": "true"},
        {"GOOGLE_CLOUD_PROJECT": "proj-x", "GOOGLE_CLOUD_LOCATION": "us-central1"},
    ],
)
def test_google_genai_without_llm_base_url_ignores_address_variables_in_the_environment(
    monkeypatch: pytest.MonkeyPatch, clean_google_env: None, variables: dict[str, str]
) -> None:
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    seen = _google_wire(monkeypatch)
    build_chat_model(_google_settings()).invoke("hi")
    assert len(seen) == 1
    assert str(seen[0].url) == _GEMINI_URL
    assert seen[0].headers["x-goog-api-key"] == "llm-key"


@pytest.mark.parametrize(
    ("status", "error_type", "expected", "requests"),
    [
        (401, GoogleAuthenticationError, "config", 1),
        (403, GooglePermissionDeniedError, "config", 1),
        (404, GoogleModelNotFoundError, "config", 1),
        (429, GoogleRateLimitError, "rate_limit", COMMON_MAX_RETRIES),  # 3 means 3 requests here
        (400, GoogleInvalidRequestError, "other", 1),
        (500, GoogleAPIError, "other", COMMON_MAX_RETRIES),
    ],
)
def test_google_genai_wire_errors_are_classified(
    monkeypatch: pytest.MonkeyPatch,
    clean_google_env: None,
    status: int,
    error_type: type[Exception],
    expected: ProviderErrorKind,
    requests: int,
) -> None:
    seen = _google_wire(monkeypatch, status)
    model = build_chat_model(_google_settings())
    with pytest.raises(error_type) as info:
        model.invoke("hi")
    assert len(seen) == requests
    assert classify_provider_error(info.value) == expected
    assert provider_status_code(info.value) == status


@pytest.mark.parametrize(
    ("error_type", "status"),
    [
        (GoogleAuthenticationError, 401),
        (GooglePermissionDeniedError, 403),
        (GoogleModelNotFoundError, 404),
        (GoogleRateLimitError, 429),
        (GoogleInvalidRequestError, 400),
    ],
)
def test_google_genai_wrapper_carries_the_status_only_on_its_cause(
    monkeypatch: pytest.MonkeyPatch,
    clean_google_env: None,
    error_type: type[Exception],
    status: int,
) -> None:
    _google_wire(monkeypatch, status)
    with pytest.raises(error_type) as info:
        build_chat_model(_google_settings()).invoke("hi")
    wrapper = info.value
    assert getattr(wrapper, "status_code", None) is None
    assert getattr(wrapper, "code", None) is None
    assert wrapper.__cause__ is not None
    assert getattr(wrapper.__cause__, "code", None) == status
    assert provider_status_code(wrapper) == status


def test_google_genai_sends_the_thought_signature_back_after_a_tool_call(
    monkeypatch: pytest.MonkeyPatch, clean_google_env: None
) -> None:
    # Gemini 3.x rejects the follow-up without it; the OpenAI-compatible path failed on this.
    signature = "c2lnbmF0dXJlLW9uZQ=="
    call_part = {
        "functionCall": {"name": "search_contract", "args": {"query": "fesih"}},
        "thoughtSignature": signature,
    }
    function_call = {
        "candidates": [
            {"content": {"role": "model", "parts": [call_part]}, "finishReason": "STOP"}
        ],
        "usageMetadata": _GEMINI_OK["usageMetadata"],
    }
    seen = _google_wire(monkeypatch, replies=(function_call, _GEMINI_OK))
    model = build_chat_model(_google_settings()).bind_tools(list(TOOLS))
    messages: list[BaseMessage] = [HumanMessage("hi")]
    reply = model.invoke(messages)
    [tool_call] = reply.tool_calls
    messages += [reply, ToolMessage("[]", tool_call_id=tool_call["id"])]
    model.invoke(messages)
    assert len(seen) == 2
    model_turns = [turn for turn in _body(seen[1])["contents"] if turn["role"] == "model"]
    assert model_turns == [{"role": "model", "parts": [call_part]}]


def test_check_chat_model_passes_for_google_genai(clean_google_env: None) -> None:
    # Construction contacts no network on the Developer API with a key.
    check_chat_model(_google_settings())
