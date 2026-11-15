from unittest.mock import Mock

import pytest

from vesterian import bot


@pytest.mark.parametrize(
    "existing,expected", [(None, "fixture-token"), ("environment-token", "environment-token")]
)
def test_startup_loads_project_env_without_overriding_environment(tmp_path, monkeypatch, existing, expected):
    (tmp_path / ".env").write_text('DISCORD_TOKEN="fixture-token"\n', encoding="utf-8")
    monkeypatch.setattr(bot, "__file__", str(tmp_path / "vesterian" / "bot.py"))
    monkeypatch.setattr("sys.argv", ["vesterian"])
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    if existing:
        monkeypatch.setenv("DISCORD_TOKEN", existing)
    client = Mock()
    monkeypatch.setattr(bot, "Bot", client)
    bot.main()
    client.return_value.run.assert_called_once_with(expected, log_handler=None)
