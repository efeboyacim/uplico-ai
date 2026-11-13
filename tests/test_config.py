import json
import math
from pathlib import Path

import pytest

from src.config import (
    DEFAULT_DATA_DIR,
    DEFAULT_NUM_CTX,
    DEFAULT_OLLAMA_BASE_URL,
    PROVIDER_DEFAULTS,
    ConfigError,
    Principal,
    load_settings,
    load_settings_or_exit,
    main,
)

GROQ_KEY = "gsk-test"
LAWYER_KEY = "test-key-acme-lawyer"
VIEWER_KEY = "test-key-globex-viewer"


def _tenant_keys() -> dict[str, dict[str, str]]:
    return {
        LAWYER_KEY: {"tenant_id": "acme", "role": "lawyer", "name": "Ayşe Yılmaz"},
        VIEWER_KEY: {"tenant_id": "globex", "role": "viewer", "name": "Can Öztürk"},
    }


def _valid_env() -> dict[str, str]:
    return {
        "LLM_PROVIDER": "groq",
        "GROQ_API_KEY": GROQ_KEY,
        "TENANT_API_KEYS": json.dumps(_tenant_keys()),
    }


@pytest.mark.parametrize("value", [None, "", "   "])
def test_load_settings_groq_without_api_key_raises_config_error(value: str | None) -> None:
    env = _valid_env()
    if value is None:
        del env["GROQ_API_KEY"]
    else:
        env["GROQ_API_KEY"] = value
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "GROQ_API_KEY" in str(info.value)
    assert "LLM_PROVIDER=ollama" in str(info.value)


def test_load_settings_ollama_without_groq_key_keeps_none() -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = "ollama"
    del env["GROQ_API_KEY"]
    assert load_settings(env).groq_api_key is None


def test_load_settings_openai_without_model_raises_config_error() -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = "openai"
    env["LLM_API_KEY"] = "sk-test"
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_MODEL" in str(info.value)
    assert "sk-test" not in str(info.value)


@pytest.mark.parametrize(
    "value", ["1groq", "gr oq", "groq!", "_groq", "gröq", "open.ai", "-groq", "a/b", "İ"]
)
def test_load_settings_malformed_provider_raises_config_error(value: str) -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = value
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    message = str(info.value)
    assert "LLM_PROVIDER" in message
    assert "a-z" in message  # ASCII only: `gröq` and `İ` are refused although they are letters
    assert "\n" not in message


@pytest.mark.parametrize("provider", ["groq", "ollama"])
@pytest.mark.parametrize("value", [None, "", "{}", "not json", "[]"])
def test_load_settings_bad_tenant_api_keys_raises_config_error(
    provider: str, value: str | None
) -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = provider
    if value is None:
        del env["TENANT_API_KEYS"]
    else:
        env["TENANT_API_KEYS"] = value
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "TENANT_API_KEYS" in str(info.value)


@pytest.mark.parametrize(
    "entry",
    [
        {"tenant_id": "acme", "role": "admin", "name": "Ayşe"},
        {"tenant_id": "acme", "role": "lawyer"},
        {"tenant_id": "acme", "role": "lawyer", "name": "Ayşe", "extra": "x"},
        {"tenant_id": "../acme", "role": "lawyer", "name": "Ayşe"},
        {"tenant_id": "acme\n", "role": "lawyer", "name": "Ayşe"},
    ],
    ids=["invalid_role", "missing_name", "extra_field", "bad_tenant_id", "tenant_id_newline"],
)
def test_load_settings_invalid_entry_raises_without_leaking_key(entry: dict[str, str]) -> None:
    secret = "super-secret-entry-key"
    env = _valid_env()
    env["TENANT_API_KEYS"] = json.dumps({secret: entry})
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert secret not in str(info.value)
    assert "TENANT_API_KEYS" in str(info.value)


def test_load_settings_duplicate_api_key_raises_without_leaking_key() -> None:
    secret = "super-secret-entry-key"
    entry = json.dumps({"tenant_id": "acme", "role": "lawyer", "name": "Ayşe"})
    other = json.dumps({"tenant_id": "globex", "role": "viewer", "name": "Can"})
    env = _valid_env()
    env["TENANT_API_KEYS"] = f'{{"{secret}": {entry}, "{secret}": {other}}}'
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert secret not in str(info.value)
    assert "TENANT_API_KEYS" in str(info.value)


def test_load_settings_api_key_with_surrounding_space_raises_config_error() -> None:
    env = _valid_env()
    env["TENANT_API_KEYS"] = json.dumps({" padded-key ": _tenant_keys()[LAWYER_KEY]})
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "padded-key" not in str(info.value)


@pytest.mark.parametrize("provider", ["groq", "ollama"])
def test_load_settings_empty_model_uses_provider_default(provider: str) -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = provider
    env["LLM_MODEL"] = ""
    settings = load_settings(env)
    assert settings.llm_model == PROVIDER_DEFAULTS[settings.llm_provider].model


def test_load_settings_given_model_is_kept() -> None:
    env = _valid_env()
    env["LLM_MODEL"] = "custom-model"
    assert load_settings(env).llm_model == "custom-model"


@pytest.mark.parametrize(("provider", "expected"), [("groq", 1), ("ollama", 1)])
def test_load_settings_default_concurrency_follows_provider(provider: str, expected: int) -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = provider
    assert load_settings(env).max_concurrency == expected


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_load_settings_invalid_concurrency_raises_config_error(value: str) -> None:
    env = _valid_env()
    env["MAX_CONCURRENCY"] = value
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "MAX_CONCURRENCY" in str(info.value)


def test_load_settings_explicit_concurrency_with_ollama_is_kept() -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = "ollama"
    env["MAX_CONCURRENCY"] = "2"
    assert load_settings(env).max_concurrency == 2


@pytest.mark.parametrize("value", ["0", "abc"])
def test_load_settings_invalid_num_ctx_raises_config_error(value: str) -> None:
    env = _valid_env()
    env["LLM_NUM_CTX"] = value
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_NUM_CTX" in str(info.value)


def test_load_settings_missing_provider_defaults_to_groq() -> None:
    env = _valid_env()
    del env["LLM_PROVIDER"]
    assert load_settings(env).llm_provider == "groq"


def test_load_settings_provider_is_trimmed_and_lowercased() -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = " Ollama "
    assert load_settings(env).llm_provider == "ollama"


def test_settings_repr_with_secrets_hides_them() -> None:
    env = _valid_env()
    env["LANGSMITH_API_KEY"] = "ls-secret"
    text = repr(load_settings(env))
    assert GROQ_KEY not in text
    assert LAWYER_KEY not in text
    assert VIEWER_KEY not in text
    assert "ls-secret" not in text


def test_load_settings_or_exit_bad_env_exits_with_one_stderr_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = _valid_env()
    del env["GROQ_API_KEY"]
    with pytest.raises(SystemExit) as info:
        load_settings_or_exit(env)
    assert info.value.code == 1
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert lines[0].strip()
    assert "GROQ_API_KEY" in lines[0]


def test_main_ollama_empty_model_prints_provider_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = "ollama"
    env["LLM_MODEL"] = ""
    main(env)
    assert capsys.readouterr().out == PROVIDER_DEFAULTS["ollama"].model + "\n"


def test_main_ollama_given_model_prints_it(capsys: pytest.CaptureFixture[str]) -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = "ollama"
    env["LLM_MODEL"] = "x"
    main(env)
    assert capsys.readouterr().out == "x\n"


def test_main_groq_exits_with_one_and_prints_nothing_on_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as info:
        main(_valid_env())
    assert info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "LLM_PROVIDER" in captured.err


def test_main_broken_config_exits_with_one(capsys: pytest.CaptureFixture[str]) -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = "ollama"
    del env["TENANT_API_KEYS"]
    with pytest.raises(SystemExit) as info:
        main(env)
    assert info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "TENANT_API_KEYS" in captured.err


def test_load_settings_valid_tenant_api_keys_map_to_principals() -> None:
    settings = load_settings(_valid_env())
    assert settings.api_keys[LAWYER_KEY] == Principal("acme", "lawyer", "Ayşe Yılmaz")
    assert settings.api_keys[VIEWER_KEY] == Principal("globex", "viewer", "Can Öztürk")


def test_load_settings_optional_vars_unset_use_defaults() -> None:
    settings = load_settings(_valid_env())
    assert settings.data_dir == DEFAULT_DATA_DIR == Path("data")
    assert settings.llm_num_ctx == DEFAULT_NUM_CTX == 16384
    assert settings.ollama_base_url == DEFAULT_OLLAMA_BASE_URL
    assert settings.langsmith_api_key is None


def test_load_settings_data_dir_set_returns_path() -> None:
    env = _valid_env()
    env["DATA_DIR"] = "x/y"
    assert load_settings(env).data_dir == Path("x/y")


# --- Provider-agnostic settings (step 18c) --------------------------------------------------------


def _provider_env(provider: str, **extra: str) -> dict[str, str]:
    env = _valid_env()
    del env["GROQ_API_KEY"]
    env["LLM_PROVIDER"] = provider
    return env | extra


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Groq", "groq"),
        ("GROQ", "groq"),
        (" ollama\t", "ollama"),
        ("google-genai", "google_genai"),
        ("Google-GenAI", "google_genai"),
        ("mistralai", "mistralai"),
    ],
)
def test_load_settings_provider_name_is_normalized(raw: str, expected: str) -> None:
    env = _provider_env(raw, GROQ_API_KEY=GROQ_KEY, LLM_MODEL="some-model", LLM_API_KEY="k")
    assert load_settings(env).llm_provider == expected


def test_load_settings_unlisted_provider_with_model_is_accepted() -> None:
    settings = load_settings(_provider_env("mistralai", LLM_MODEL="mistral-large-latest"))
    assert settings.llm_provider == "mistralai"
    assert settings.llm_model == "mistral-large-latest"
    assert settings.llm_api_key is None  # optional outside the known table
    assert settings.max_concurrency == 1


@pytest.mark.parametrize("model", [None, "", "   "])
def test_load_settings_unlisted_provider_without_model_raises(model: str | None) -> None:
    env = _provider_env("mistralai")
    if model is not None:
        env["LLM_MODEL"] = model
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_MODEL" in str(info.value)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("key", [None, "", "   "])
def test_load_settings_openai_anthropic_without_llm_api_key_raises(
    provider: str, key: str | None
) -> None:
    env = _provider_env(provider, LLM_MODEL="m", GROQ_API_KEY=GROQ_KEY)
    if key is not None:
        env["LLM_API_KEY"] = key
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_API_KEY" in str(info.value)
    assert GROQ_KEY not in str(info.value)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_load_settings_openai_anthropic_with_key_and_model(provider: str) -> None:
    settings = load_settings(_provider_env(provider, LLM_MODEL="m", LLM_API_KEY=" sk-x "))
    assert settings.llm_provider == provider
    assert settings.llm_model == "m"
    assert settings.llm_api_key == "sk-x"
    assert settings.max_concurrency == 1
    assert PROVIDER_DEFAULTS[provider].model is None


def test_load_settings_groq_accepts_llm_api_key_instead_of_groq_api_key() -> None:
    settings = load_settings(_provider_env("groq", LLM_API_KEY="llm-key"))
    assert settings.groq_api_key is None
    assert settings.llm_api_key == "llm-key"
    assert settings.llm_model == PROVIDER_DEFAULTS["groq"].model


def test_load_settings_groq_without_any_key_names_both_variables() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings(_provider_env("groq", LLM_API_KEY=" "))
    assert "GROQ_API_KEY" in str(info.value)
    assert "LLM_API_KEY" in str(info.value)
    assert "LLM_PROVIDER=ollama" in str(info.value)


def test_load_settings_ollama_ignores_llm_api_key() -> None:
    settings = load_settings(_provider_env("ollama", LLM_API_KEY="left-over"))
    assert settings.llm_provider == "ollama"


def test_load_settings_ollama_with_llm_base_url_raises_naming_ollama_base_url() -> None:
    env = _provider_env("ollama", LLM_BASE_URL="http://somewhere:11434")
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_BASE_URL" in str(info.value)
    assert "OLLAMA_BASE_URL" in str(info.value)


def test_load_settings_llm_base_url_is_kept_for_other_providers() -> None:
    env = _provider_env("openai", LLM_MODEL="m", LLM_API_KEY="k", LLM_BASE_URL=" http://x/v1 ")
    assert load_settings(env).llm_base_url == "http://x/v1"
    assert load_settings(_valid_env()).llm_base_url is None


def test_settings_repr_hides_llm_api_key() -> None:
    secret = "sk-very-secret-llm-key"
    env = _provider_env("openai", LLM_MODEL="m", LLM_API_KEY=secret)
    text = repr(load_settings(env))
    assert secret not in text
    groq_text = repr(load_settings(_provider_env("groq", LLM_API_KEY=secret)))
    assert secret not in groq_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 0.0), ("", 0.0), ("  ", 0.0), ("0", 0.0), ("0.3", 0.3), ("1", 1.0), (" 2.5 ", 2.5)],
)
def test_load_settings_temperature_values(raw: str | None, expected: float) -> None:
    env = _valid_env()
    if raw is not None:
        env["LLM_TEMPERATURE"] = raw
    assert load_settings(env).llm_temperature == expected


@pytest.mark.parametrize("raw", ["none", "None", " NONE "])
@pytest.mark.parametrize("provider", ["ollama", "openai", "mistralai"])
def test_load_settings_temperature_none_means_omitted(raw: str, provider: str) -> None:
    env = _provider_env(provider, LLM_MODEL="m", LLM_API_KEY="k", LLM_TEMPERATURE=raw)
    assert load_settings(env).llm_temperature is None


@pytest.mark.parametrize("raw", ["none", " NONE "])
@pytest.mark.parametrize("key", [{"GROQ_API_KEY": GROQ_KEY}, {"LLM_API_KEY": GROQ_KEY}])
@pytest.mark.parametrize("provider", ["groq", "Groq", ""])
def test_load_settings_groq_with_temperature_none_raises(
    raw: str, key: dict[str, str], provider: str
) -> None:
    # ChatGroq has no "no temperature" value: None fails its validation and an omitted
    # argument would send ChatGroq's own 0.7.
    env = _provider_env(provider, LLM_TEMPERATURE=raw, **key)
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    message = str(info.value)
    assert "LLM_TEMPERATURE" in message
    assert "groq" in message
    assert GROQ_KEY not in message
    assert "\n" not in message


@pytest.mark.parametrize("raw", ["-0", "-0.0", " -0e0 "])
def test_load_settings_negative_zero_temperature_is_plain_zero(raw: str) -> None:
    env = _valid_env()
    env["LLM_TEMPERATURE"] = raw
    value = load_settings(env).llm_temperature
    assert value == 0.0
    assert value is not None and math.copysign(1.0, value) == 1.0  # not -0.0
    assert json.dumps(value) == "0.0"


def test_settings_repr_hides_llm_base_url() -> None:
    url = "https://user:sk-url-secret@compat.example/v1"
    env = _provider_env("openai", LLM_MODEL="m", LLM_API_KEY="k", LLM_BASE_URL=url)
    settings = load_settings(env)
    assert settings.llm_base_url == url
    assert "sk-url-secret" not in repr(settings)
    assert "compat.example" not in repr(settings)


@pytest.mark.parametrize("raw", ["-0.1", "-1", "abc", "nan", "inf", "-inf", "0,5", "null"])
def test_load_settings_invalid_temperature_raises(raw: str) -> None:
    env = _valid_env()
    env["LLM_TEMPERATURE"] = raw
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_TEMPERATURE" in str(info.value)


def test_load_settings_max_tokens_default_and_given() -> None:
    assert load_settings(_valid_env()).llm_max_tokens == 2048
    env = _valid_env()
    env["LLM_MAX_TOKENS"] = "4096"
    assert load_settings(env).llm_max_tokens == 4096


@pytest.mark.parametrize("raw", ["0", "-5", "1.5", "abc"])
def test_load_settings_invalid_max_tokens_raises(raw: str) -> None:
    env = _valid_env()
    env["LLM_MAX_TOKENS"] = raw
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_MAX_TOKENS" in str(info.value)


@pytest.mark.parametrize("provider", ["groq", "ollama"])
def test_load_settings_new_variables_unset_keep_known_provider_defaults(provider: str) -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = provider
    settings = load_settings(env)
    assert settings.llm_api_key is None
    assert settings.llm_base_url is None
    assert settings.llm_temperature == 0
    assert settings.llm_max_tokens == 2048
    assert settings.llm_model == PROVIDER_DEFAULTS[provider].model


# --- google_genai (step 18d) ----------------------------------------------------------------------

_VERTEX_SWITCHES = ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE")


def _google_env(**extra: str) -> dict[str, str]:
    return _provider_env(
        "google_genai", **({"LLM_MODEL": "gemini-test", "LLM_API_KEY": "k"} | extra)
    )


def test_load_settings_google_genai_with_key_and_model() -> None:
    settings = load_settings(_google_env(LLM_API_KEY=" gm-key "))
    assert settings.llm_provider == "google_genai"
    assert settings.llm_model == "gemini-test"
    assert settings.llm_api_key == "gm-key"
    assert settings.max_concurrency == 1
    defaults = PROVIDER_DEFAULTS["google_genai"]
    assert defaults.model is None
    assert defaults.requires_api_key is True
    assert defaults.input_usd_per_mtok is None
    assert defaults.output_usd_per_mtok is None


@pytest.mark.parametrize("model", [None, "", "   "])
def test_load_settings_google_genai_without_model_raises_naming_llm_model(
    model: str | None,
) -> None:
    env = _google_env()
    del env["LLM_MODEL"]
    if model is not None:
        env["LLM_MODEL"] = model
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_MODEL" in str(info.value)
    assert "google_genai" in str(info.value)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_load_settings_google_genai_without_llm_api_key_raises(key: str | None) -> None:
    env = _google_env()
    del env["LLM_API_KEY"]
    if key is not None:
        env["LLM_API_KEY"] = key
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_API_KEY" in str(info.value)


def test_load_settings_google_genai_ignores_google_and_gemini_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The integration would fall back to these; the config must not.
    env = _google_env(GOOGLE_API_KEY="google-env-key", GEMINI_API_KEY="gemini-env-key")
    del env["LLM_API_KEY"]
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    for given in (env, None):  # the mapping, then the process environment
        with pytest.raises(ConfigError) as info:
            load_settings(given)
        message = str(info.value)
        assert "LLM_API_KEY" in message
        assert "google-env-key" not in message
        assert "gemini-env-key" not in message


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", " true ", "maybe-vertex"])
@pytest.mark.parametrize("name", _VERTEX_SWITCHES)
def test_load_settings_google_genai_with_vertex_switch_raises_naming_it(
    name: str, value: str
) -> None:
    with pytest.raises(ConfigError) as info:
        load_settings(_google_env(**{name: value}))
    message = str(info.value)
    assert name in message
    assert "google_genai" in message
    assert "maybe-vertex" not in message  # the value is not echoed
    assert "\n" not in message


@pytest.mark.parametrize("value", ["", "   ", "false", "False", "0", "no", "NO"])
@pytest.mark.parametrize("name", _VERTEX_SWITCHES)
def test_load_settings_google_genai_with_vertex_switch_off_is_accepted(
    name: str, value: str
) -> None:
    assert load_settings(_google_env(**{name: value})).llm_provider == "google_genai"


def test_load_settings_google_genai_reads_the_vertex_switch_from_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in _google_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert "GOOGLE_GENAI_USE_VERTEXAI" in str(info.value)


def test_load_settings_google_genai_cloud_project_and_location_alone_are_accepted() -> None:
    # Without a Vertex switch these do not change the request (they are Vertex-only settings).
    env = _google_env(GOOGLE_CLOUD_PROJECT="proj-x", GOOGLE_CLOUD_LOCATION="us-central1")
    assert load_settings(env).llm_provider == "google_genai"


@pytest.mark.parametrize("provider", ["groq", "ollama", "openai"])
def test_load_settings_vertex_switch_is_only_checked_for_google_genai(provider: str) -> None:
    extra = {"GROQ_API_KEY": GROQ_KEY} if provider == "groq" else {"LLM_MODEL": "m"}
    if provider == "openai":
        extra["LLM_API_KEY"] = "k"
    env = _provider_env(provider, GOOGLE_GENAI_USE_VERTEXAI="true", **extra)
    env["GOOGLE_GENAI_USE_ENTERPRISE"] = "1"
    assert load_settings(env).llm_provider == provider


@pytest.mark.parametrize("value", ["record", "replay", "auto", "api", " canary-client-mode "])
def test_load_settings_google_genai_with_client_mode_raises_naming_it(value: str) -> None:
    with pytest.raises(ConfigError) as info:
        load_settings(_google_env(GOOGLE_GENAI_CLIENT_MODE=value))
    message = str(info.value)
    assert "GOOGLE_GENAI_CLIENT_MODE" in message
    assert "google_genai" in message
    assert "canary-client-mode" not in message  # the value is not echoed
    assert "\n" not in message


@pytest.mark.parametrize("value", ["", "   "])
def test_load_settings_google_genai_with_empty_client_mode_is_accepted(value: str) -> None:
    env = _google_env(GOOGLE_GENAI_CLIENT_MODE=value)
    assert load_settings(env).llm_provider == "google_genai"


def test_load_settings_google_genai_reads_the_client_mode_from_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in _google_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GOOGLE_GENAI_CLIENT_MODE", "record")
    with pytest.raises(ConfigError) as info:
        load_settings()
    assert "GOOGLE_GENAI_CLIENT_MODE" in str(info.value)


@pytest.mark.parametrize("provider", ["groq", "openai"])
def test_load_settings_client_mode_is_only_checked_for_google_genai(provider: str) -> None:
    extra = {"GROQ_API_KEY": GROQ_KEY} if provider == "groq" else {"LLM_MODEL": "m"}
    if provider == "openai":
        extra["LLM_API_KEY"] = "k"
    env = _provider_env(provider, GOOGLE_GENAI_CLIENT_MODE="record", **extra)
    assert load_settings(env).llm_provider == provider
