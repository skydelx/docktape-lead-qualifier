import logging
import sys

import anthropic
import httpx
import pytest

from leadqual import __main__ as cli
from leadqual.__main__ import configure_logging


@pytest.mark.parametrize("http_library", ["httpx", "httpx2"])
def test_request_urls_are_not_logged_because_the_webhook_url_is_a_secret(http_library):
    """Found in the first live run: the HTTP client printed the Slack webhook URL at INFO."""
    configure_logging()

    assert not logging.getLogger(http_library).isEnabledFor(logging.INFO)


@pytest.fixture
def no_dotenv(monkeypatch):
    """Never read the developer's real .env in a test."""
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)


def _main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["leadqual", *args])
    return cli.main()


def test_a_missing_api_key_gives_one_clear_line_not_a_traceback(monkeypatch, capsys, no_dotenv):
    """Found in the final review: a stranger without a key got a 40-line traceback."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert _main(monkeypatch, "run", "leads/03_flagged_competitor.json", "--no-notify") == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_a_rejected_api_key_gives_one_clear_line(monkeypatch, capsys, no_dotenv):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    response = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com"))

    def rejected(*_args, **_kwargs):
        raise anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)

    monkeypatch.setattr(cli, "_run", rejected)

    assert _main(monkeypatch, "run", "leads/03_flagged_competitor.json", "--no-notify") == 2
    assert "the API key was rejected" in capsys.readouterr().err


def test_a_missing_lead_file_gives_one_clear_line(monkeypatch, capsys, no_dotenv):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")

    assert _main(monkeypatch, "run", "leads/no_such_lead.json", "--no-notify") == 2
    assert "Cannot read the lead file" in capsys.readouterr().err
