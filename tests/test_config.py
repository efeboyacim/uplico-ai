import json
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


def test_load_settings_unknown_provider_raises_config_error() -> None:
    env = _valid_env()
    env["LLM_PROVIDER"] = "openai"
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert "LLM_PROVIDER" in str(info.value)


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
