"""Chat model construction and provider error classification; provider specifics stay here."""

from typing import Literal

import groq
import httpx
import ollama
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from src.config import Settings

ProviderErrorKind = Literal["config", "rate_limit", "other"]

GROQ_TIMEOUT_S = 60.0
GROQ_MAX_RETRIES = 3
GROQ_MAX_TOKENS = 2048
GROQ_REASONING_EFFORT = "low"
OLLAMA_TIMEOUT_S = 120.0
OLLAMA_SEED = 42


def build_chat_model(
    settings: Settings, *, http_client: httpx.Client | None = None
) -> BaseChatModel:
    if settings.llm_provider == "groq":
        return init_chat_model(
            settings.llm_model,
            model_provider="groq",
            api_key=settings.groq_api_key,
            temperature=0,
            max_tokens=GROQ_MAX_TOKENS,
            reasoning_effort=GROQ_REASONING_EFFORT,
            max_retries=GROQ_MAX_RETRIES,
            timeout=GROQ_TIMEOUT_S,
            http_client=http_client,
        )
    # ChatOllama would silently drop http_client and hit the real network.
    if http_client is not None:
        raise ValueError("http_client is only supported for the groq provider")
    return init_chat_model(
        settings.llm_model,
        model_provider="ollama",
        base_url=settings.ollama_base_url,
        temperature=0,
        num_ctx=settings.llm_num_ctx,
        reasoning=False,
        seed=OLLAMA_SEED,
        client_kwargs={"timeout": OLLAMA_TIMEOUT_S},
    )


def classify_provider_error(exc: BaseException) -> ProviderErrorKind:
    if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError, groq.NotFoundError)):
        return "config"
    if isinstance(exc, ollama.ResponseError) and exc.status_code == 404:
        return "config"
    if isinstance(exc, groq.RateLimitError):
        return "rate_limit"
    return "other"
