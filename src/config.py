"""Runtime settings, read from environment variables. The only module that touches the env."""

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

Provider = Literal["groq", "ollama"]
Role = Literal["lawyer", "viewer"]

_PROVIDERS: tuple[Provider, ...] = ("groq", "ollama")
_ROLES: tuple[Role, ...] = ("lawyer", "viewer")
_TENANT_ID_RE = re.compile(r"[a-z0-9_]+")
_ENTRY_FIELDS = frozenset({"tenant_id", "role", "name"})


@dataclass(frozen=True)
class ProviderDefaults:
    model: str
    max_concurrency: int


PROVIDER_DEFAULTS: Mapping[Provider, ProviderDefaults] = MappingProxyType(
    {
        "groq": ProviderDefaults(model="openai/gpt-oss-120b", max_concurrency=4),
        "ollama": ProviderDefaults(model="qwen3.5:9b", max_concurrency=1),
    }
)

DEFAULT_NUM_CTX = 16384
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_DATA_DIR = Path("data")


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    role: Role
    name: str


@dataclass(frozen=True)
class Settings:
    llm_provider: Provider
    llm_model: str
    groq_api_key: str | None = field(repr=False)
    ollama_base_url: str
    llm_num_ctx: int
    max_concurrency: int
    api_keys: Mapping[str, Principal] = field(repr=False)
    data_dir: Path
    langsmith_api_key: str | None = field(repr=False)


class ConfigError(Exception):
    pass


def _read(env: Mapping[str, str], name: str) -> str:
    return env.get(name, "").strip()


def _read_positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _read(env, name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} bir tam sayı olmalı.") from None
    if value < 1:
        raise ConfigError(f"{name} en az 1 olmalı.")
    return value


def _parse_entry(position: int, key: object, value: object) -> tuple[str, Principal]:
    prefix = f"TENANT_API_KEYS içindeki {position}. kayıt"
    if not isinstance(key, str) or not key or key != key.strip():
        raise ConfigError(f"{prefix}: anahtar boş olamaz, başında ya da sonunda boşluk olamaz.")
    if not isinstance(value, dict):
        raise ConfigError(f"{prefix}: değer bir nesne olmalı.")
    if set(value) != _ENTRY_FIELDS:
        raise ConfigError(f"{prefix}: alanlar tam olarak tenant_id, role ve name olmalı.")
    tenant_id = value["tenant_id"]
    role = value["role"]
    name = value["name"]
    if not isinstance(tenant_id, str) or not _TENANT_ID_RE.fullmatch(tenant_id):
        raise ConfigError(f"{prefix}: tenant_id yalnızca küçük harf, rakam ve _ içermeli.")
    if not isinstance(role, str) or role not in _ROLES:
        raise ConfigError(f"{prefix}: role lawyer ya da viewer olmalı.")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{prefix}: name boş olmayan bir metin olmalı.")
    return key, Principal(tenant_id=tenant_id, role=role, name=name.strip())


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    # json.loads keeps the last value of a repeated key; a repeated API key must not pass silently.
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("TENANT_API_KEYS içinde aynı anahtar ya da alan iki kez yazılmış.")
        result[key] = value
    return result


def _parse_api_keys(raw: str) -> Mapping[str, Principal]:
    if not raw:
        raise ConfigError("TENANT_API_KEYS tanımlı olmalı.")
    try:
        parsed: object = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError:
        raise ConfigError("TENANT_API_KEYS geçerli bir JSON değil.") from None
    if not isinstance(parsed, dict):
        raise ConfigError("TENANT_API_KEYS bir JSON nesnesi olmalı.")
    if not parsed:
        raise ConfigError("TENANT_API_KEYS en az bir kayıt içermeli.")
    result: dict[str, Principal] = {}
    for position, (key, value) in enumerate(parsed.items(), start=1):
        api_key, principal = _parse_entry(position, key, value)
        result[api_key] = principal
    return MappingProxyType(result)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        env = os.environ

    provider_raw = _read(env, "LLM_PROVIDER").lower() or "groq"
    if provider_raw not in _PROVIDERS:
        raise ConfigError(
            f"LLM_PROVIDER geçersiz: '{provider_raw}'. Geçerli değerler: groq, ollama."
        )
    provider = provider_raw

    groq_api_key = _read(env, "GROQ_API_KEY") or None
    if provider == "groq" and groq_api_key is None:
        raise ConfigError(
            "GROQ_API_KEY groq sağlayıcısı için zorunlu. Çevrimdışı mod için "
            "LLM_PROVIDER=ollama ve COMPOSE_PROFILES=offline kullan."
        )

    api_keys = _parse_api_keys(_read(env, "TENANT_API_KEYS"))

    defaults = PROVIDER_DEFAULTS[provider]
    llm_model = _read(env, "LLM_MODEL") or defaults.model
    max_concurrency = _read_positive_int(env, "MAX_CONCURRENCY", defaults.max_concurrency)
    llm_num_ctx = _read_positive_int(env, "LLM_NUM_CTX", DEFAULT_NUM_CTX)

    data_dir_raw = _read(env, "DATA_DIR")
    return Settings(
        llm_provider=provider,
        llm_model=llm_model,
        groq_api_key=groq_api_key,
        ollama_base_url=_read(env, "OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL,
        llm_num_ctx=llm_num_ctx,
        max_concurrency=max_concurrency,
        api_keys=api_keys,
        data_dir=Path(data_dir_raw) if data_dir_raw else DEFAULT_DATA_DIR,
        langsmith_api_key=_read(env, "LANGSMITH_API_KEY") or None,
    )


def load_settings_or_exit(env: Mapping[str, str] | None = None) -> Settings:
    try:
        return load_settings(env)
    except ConfigError as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
