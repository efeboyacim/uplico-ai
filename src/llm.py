"""Chat model construction and provider error classification; provider specifics stay here."""

import re
from typing import Any, Literal

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.exceptions import (
    ModelAuthenticationError,
    ModelNotFoundError,
    ModelPermissionDeniedError,
    ModelRateLimitError,
)
from langchain_core.language_models import BaseChatModel

from src.config import DEFAULT_MAX_TOKENS, ConfigError, Settings

ProviderErrorKind = Literal["config", "rate_limit", "other"]

GROQ_TIMEOUT_S = 60.0
GROQ_MAX_RETRIES = 3
GROQ_MAX_TOKENS = DEFAULT_MAX_TOKENS
GROQ_REASONING_EFFORT = "low"
OLLAMA_TIMEOUT_S = 120.0
OLLAMA_SEED = 42
# The common parameters every provider outside groq / ollama gets (docs/tech_stack.md).
COMMON_TIMEOUT_S = 60.0
COMMON_MAX_RETRIES = 3

# Providers whose model takes a sync `http_client` (the tests' fake transport). Anthropic would
# fail at call time; Google and Ollama would drop it silently and reach the real network.
_HTTP_CLIENT_PROVIDERS = frozenset({"groq", "openai"})
# Package groups in pyproject.toml's [project.optional-dependencies], by provider.
_PACKAGE_GROUPS = {"openai": "openai", "anthropic": "anthropic"}
# Core dependencies: a missing one means the environment was not installed from the lock.
_CORE_PACKAGE_PROVIDERS = frozenset({"groq", "ollama"})
# Public endpoints passed when LLM_BASE_URL is empty: otherwise the integration and its SDK read
# the address from their own env variables (OPENAI_BASE_URL, ANTHROPIC_BASE_URL, ...).
_PUBLIC_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}
_CONFIG_STATUSES = frozenset({401, 403, 404})
_RATE_LIMIT_STATUS = 429
# langchain's own ImportError text; only a matching package name is ever repeated.
_MISSING_PACKAGE_RE = re.compile(r"requires the (langchain-[a-z0-9-]+) package")
_UNKNOWN_PROVIDER_PREFIX = "Unsupported provider="


def build_chat_model(
    settings: Settings, *, http_client: httpx.Client | None = None
) -> BaseChatModel:
    provider = settings.llm_provider
    if http_client is not None and provider not in _HTTP_CLIENT_PROVIDERS:
        raise ValueError("http_client is only supported for the groq and openai providers")
    if provider == "groq":
        groq_kwargs: dict[str, Any] = {
            "model_provider": "groq",
            "api_key": settings.groq_api_key or settings.llm_api_key,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
            "reasoning_effort": GROQ_REASONING_EFFORT,
            "max_retries": GROQ_MAX_RETRIES,
            "timeout": GROQ_TIMEOUT_S,
            "http_client": http_client,
        }
        if settings.llm_base_url is not None:
            groq_kwargs["base_url"] = settings.llm_base_url
        groq_model: BaseChatModel = init_chat_model(settings.llm_model, **groq_kwargs)
        return groq_model
    if provider == "ollama":
        # No key and no max_tokens: ChatOllama ignores both. LLM_BASE_URL is refused by config.
        return init_chat_model(
            settings.llm_model,
            model_provider="ollama",
            base_url=settings.ollama_base_url,
            temperature=settings.llm_temperature,
            num_ctx=settings.llm_num_ctx,
            reasoning=False,
            seed=OLLAMA_SEED,
            client_kwargs={"timeout": OLLAMA_TIMEOUT_S},
        )
    # Every other provider gets the common parameters only. `temperature=None` is passed
    # explicitly: an omitted argument lets an integration send its own default.
    kwargs: dict[str, Any] = {
        "model_provider": provider,
        "max_tokens": settings.llm_max_tokens,
        "timeout": COMMON_TIMEOUT_S,
        "max_retries": COMMON_MAX_RETRIES,
        "temperature": settings.llm_temperature,
    }
    # An empty key is never passed: the integration would fall back to its own env variable.
    if settings.llm_api_key is not None:
        kwargs["api_key"] = settings.llm_api_key
    if settings.llm_base_url is not None:
        kwargs["base_url"] = settings.llm_base_url
        if provider == "openai":
            # OpenAI-compatible servers implement /chat/completions; langchain-openai would send
            # some model names (codex, gpt-5-pro) to /responses.
            kwargs["use_responses_api"] = False
    elif provider in _PUBLIC_BASE_URLS:
        kwargs["base_url"] = _PUBLIC_BASE_URLS[provider]
    if http_client is not None:
        kwargs["http_client"] = http_client
    model: BaseChatModel = init_chat_model(settings.llm_model, **kwargs)
    return model


def _missing_package_message(provider: str, exc: ImportError) -> str:
    match = _MISSING_PACKAGE_RE.search(str(exc))
    package = f"{match.group(1)} paketi" if match else "entegrasyon paketi"
    if provider in _CORE_PACKAGE_PROVIDERS:
        return (
            f"LLM_PROVIDER={provider} için {package} kurulu değil; bu paket çekirdek "
            "bağımlılıktır, yani ortam kilitten kurulmamış: yerelde uv sync çalıştır, Docker'da "
            "imajı yeniden kur."
        )
    group = _PACKAGE_GROUPS.get(provider)
    if group is not None:
        return (
            f"LLM_PROVIDER={provider} için {package} kurulu değil: Docker'da LLM_EXTRAS build "
            f"argümanına '{group}' grubunu ekleyip imajı yeniden kur (yerelde: uv sync "
            f"--extra {group})."
        )
    return (
        f"LLM_PROVIDER={provider} için {package} kurulu değil ve LLM_EXTRAS'ta bu sağlayıcının "
        "grubu yok: paketi pyproject.toml'a ekle, kilidi yenile (uv lock) ve imajı yeniden kur."
    )


def check_chat_model(settings: Settings) -> None:
    """Build the chat model once at startup; a construction error becomes `ConfigError`.

    The message is built from the provider name and the exception type only: an integration's
    construction error (a pydantic ValidationError, for one) can repeat its input values, the
    API key among them.
    """
    provider = settings.llm_provider
    try:
        build_chat_model(settings)
    except ImportError as exc:
        message = _missing_package_message(provider, exc)
    except ValueError as exc:
        if str(exc).startswith(_UNKNOWN_PROVIDER_PREFIX):
            message = f"LLM_PROVIDER={provider} LangChain'in tanıdığı bir sağlayıcı değil."
        else:
            message = _construction_message(provider, exc)
    except Exception as exc:
        message = _construction_message(provider, exc)
    else:
        return
    raise ConfigError(message) from None


def _construction_message(provider: str, exc: Exception) -> str:
    return (
        f"LLM_PROVIDER={provider} için sohbet modeli kurulamadı ({type(exc).__name__}); "
        "LLM_MODEL, LLM_API_KEY, LLM_BASE_URL, LLM_TEMPERATURE ve LLM_MAX_TOKENS değerlerini "
        "kontrol et."
    )


def _int_status(value: object) -> int | None:
    # bool is an int subclass and a text code (openai's `code`) is not a status.
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _attribute(obj: object, name: str) -> object:
    # Only `status_code`, `response` and `code` are read. The SDK errors seen so far hold them as
    # plain attributes; an arbitrary exception may define one as a property that raises, and that
    # must read as "absent" rather than replace the error being classified.
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def provider_status_code(exc: BaseException) -> int | None:
    """The HTTP status of a provider error, read from the exception, then from its `__cause__`.

    On each: `status_code`, else `response.status_code`, else an int `code`. `__context__` is
    not walked. The classifier and the `model_call` audit row both read the status here.
    """
    for node in (exc, exc.__cause__):
        if node is None:
            continue
        for value in (
            _attribute(node, "status_code"),
            _attribute(_attribute(node, "response"), "status_code"),
            _attribute(node, "code"),
        ):
            status = _int_status(value)
            if status is not None:
                return status
    return None


def classify_provider_error(exc: BaseException) -> ProviderErrorKind:
    """`config` (stop the review), `rate_limit` or `other`; the provider's type is not looked at.

    langchain_core's error classes first (openai, anthropic and google_genai raise subclasses of
    them; groq and ollama do not), then the HTTP status.
    """
    if isinstance(exc, (ModelAuthenticationError, ModelPermissionDeniedError, ModelNotFoundError)):
        return "config"
    if isinstance(exc, ModelRateLimitError):
        return "rate_limit"
    status = provider_status_code(exc)
    if status in _CONFIG_STATUSES:
        return "config"
    if status == _RATE_LIMIT_STATUS:
        return "rate_limit"
    return "other"
