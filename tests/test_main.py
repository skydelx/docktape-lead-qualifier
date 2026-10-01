import logging

import pytest

from leadqual.__main__ import configure_logging


@pytest.mark.parametrize("http_library", ["httpx", "httpx2"])
def test_request_urls_are_not_logged_because_the_webhook_url_is_a_secret(http_library):
    """Found in the first live run: the HTTP client printed the Slack webhook URL at INFO."""
    configure_logging()

    assert not logging.getLogger(http_library).isEnabledFor(logging.INFO)
