import os

import pytest

from agenticcore.config import load_env


@pytest.fixture
def isolated_env(monkeypatch):
    """Give load_env a throwaway environ.

    load_env writes straight into os.environ by design, so without this the
    keys a test loads leak into every test that runs after it.
    """

    fake: dict[str, str] = {}
    monkeypatch.setattr(os, "environ", fake)
    return fake


def test_loads_keys_and_skips_comments_and_blanks(tmp_path, isolated_env):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "ANTHROPIC_API_KEY=sk-test-123\n"
        "AYRSHARE_API_KEY=   \n"          # blank placeholder, as in .env.example
        "export TELEGRAM_BOT_TOKEN=bot:abc\n"
        'TELEGRAM_CHAT_ID="-100 99"\n'    # quoted value keeps its space
        "AGENTICCORE_MODEL=claude-sonnet-5  # trailing note\n"
    )

    loaded = load_env(env)

    assert loaded["ANTHROPIC_API_KEY"] == "sk-test-123"
    assert loaded["TELEGRAM_BOT_TOKEN"] == "bot:abc"
    assert loaded["TELEGRAM_CHAT_ID"] == "-100 99"
    assert loaded["AGENTICCORE_MODEL"] == "claude-sonnet-5"
    assert "AYRSHARE_API_KEY" not in loaded  # blank placeholder is not a setting
    assert isolated_env["ANTHROPIC_API_KEY"] == "sk-test-123"


def test_real_environment_wins_unless_override(tmp_path, isolated_env):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-file\n")
    isolated_env["ANTHROPIC_API_KEY"] = "from-shell"

    load_env(env)
    assert isolated_env["ANTHROPIC_API_KEY"] == "from-shell"

    load_env(env, override=True)
    assert isolated_env["ANTHROPIC_API_KEY"] == "from-file"


def test_missing_file_is_not_an_error(tmp_path, isolated_env):
    assert load_env(tmp_path / "nope.env") == {}
