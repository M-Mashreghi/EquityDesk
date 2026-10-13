from __future__ import annotations

import re
from pathlib import Path

import pytest

from equitydesk.config import ConfigError, Environment, LLMProviderName, load_settings

ROOT = Path(__file__).resolve().parents[2]


def test_defaults_load_without_any_env() -> None:
    s = load_settings(environ={})
    assert s.env is Environment.DEVELOPMENT
    assert s.llm_provider is LLMProviderName.FAKE
    assert s.llm_api_key is None


def test_env_values_are_typed() -> None:
    s = load_settings(
        environ={
            "EQUITYDESK_ENV": "test",
            "EQUITYDESK_DB_PATH": "x/y.db",
            "LLM_MAX_RETRIES": "4",
            "SEC_USER_AGENT": "EquityDesk research@example.com",
        }
    )
    assert s.env is Environment.TEST
    assert s.db_path == Path("x/y.db")
    assert s.llm_max_retries == 4


def test_invalid_values_fail_clearly_with_env_names() -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings(environ={"EQUITYDESK_ENV": "staging", "LLM_MAX_RETRIES": "-1"})
    text = str(exc.value)
    assert "EQUITYDESK_ENV" in text
    assert "LLM_MAX_RETRIES" in text


def test_real_provider_requires_key_and_model() -> None:
    with pytest.raises(ConfigError, match="LLM_API_KEY is required"):
        load_settings(environ={"LLM_PROVIDER": "anthropic"})


def test_production_requires_sec_user_agent_and_real_llm() -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings(environ={"EQUITYDESK_ENV": "production"})
    assert "SEC_USER_AGENT is required" in str(exc.value)
    assert "LLM_PROVIDER=fake is not allowed" in str(exc.value)


def test_sec_user_agent_needs_contact() -> None:
    with pytest.raises(ConfigError, match="contact e-mail"):
        load_settings(environ={"SEC_USER_AGENT": "EquityDesk"})


def test_secret_not_in_repr_or_error() -> None:
    key = "sk-test-supersecretvalue123"
    s = load_settings(environ={"LLM_PROVIDER": "anthropic", "LLM_API_KEY": key, "LLM_MODEL": "m"})
    assert key not in repr(s)
    assert key not in str(s.model_dump())
    assert s.secret_values() == [key]
    with pytest.raises(ConfigError) as exc:
        load_settings(environ={"LLM_PROVIDER": "anthropic", "LLM_API_KEY": key})
    assert key not in str(exc.value)


def test_env_file_is_overridden_by_process_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nEQUITYDESK_LOG_LEVEL=DEBUG\nEQUITYDESK_DB_PATH='a.db'\n", encoding="utf-8"
    )
    s = load_settings(environ={"EQUITYDESK_DB_PATH": "b.db"}, env_file=env_file)
    assert s.log_level == "DEBUG"
    assert s.db_path == Path("b.db")


def test_env_example_parses_and_has_no_secret_values() -> None:
    from equitydesk.config import ENV_KEYS, parse_env_file

    values = parse_env_file(ROOT / ".env.example")
    load_settings(environ=values)
    for key in ("LLM_API_KEY", "ALERT_WEBHOOK_TOKEN"):
        assert values.get(key, "") == ""
    # every documented key is a known key
    assert set(values) <= set(ENV_KEYS)


SECRET_LITERAL = re.compile(r"(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})")


def test_no_secret_literals_in_source_tree() -> None:
    offenders = []
    for path in list((ROOT / "src").rglob("*.py")) + [ROOT / ".env.example"]:
        if SECRET_LITERAL.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path))
    assert offenders == []
