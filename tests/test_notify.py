import json

import httpx
import pytest

from fakes import make_result
from leadqual.models import CompetitorVerdict, Compliance, MatchType, Route, SanctionsVerdict
from leadqual.notify import NotifyError, format_message, send

WEBHOOK = "https://hooks.slack.test/services/T000/B000/XXXX"


def test_message_carries_the_same_facts_as_the_tracker_row():
    assert format_message(make_result()).splitlines() == [
        "🟢 *SALES-READY* · Fit 85 (full data)",
        "*Acme Analytics* · acme.io",
        "*Why:* Fit 85, compliance clear",
        "*Summary:* Acme builds retail dashboards on AWS.",
        "*Fit:* size 51-1000 (website) · cloud medium workload, inferred (website)"
        " · bonus: runs on aws, decision maker (CTO)",
        "*Compliance:* clear. Unrelated retail analytics company based in Austria.",
        "*Contact:* Ada Example, CTO · ada@acme.io",
    ]


def test_flagged_lead_says_why_and_tells_the_rep_to_stay_away():
    flagged = Compliance(
        competitor=CompetitorVerdict.CONFIRMED_MATCH,
        matched_entry="CloudTrim Inc",
        match_type=MatchType.RENAMED,
        sanctions=SanctionsVerdict.CLEAR,
        reasoning='The site says "formerly CloudTrim": same company under a new name.',
    )
    result = make_result(
        route=Route.DO_NOT_ENGAGE, reason="Competitor: CloudTrim Inc", compliance=flagged
    )

    message = format_message(result)

    assert message.startswith("🔴 *DO-NOT-ENGAGE*")
    assert "*Compliance:* FLAGGED: competitor (CloudTrim Inc). The site says" in message
    assert message.endswith("*Do not contact this lead.*")


def test_missing_job_title_and_website_are_handled():
    message = format_message(make_result(domain=None, job_title=None))

    assert "*Acme Analytics* · no website" in message
    assert "*Contact:* Ada Example · ada@acme.io" in message


def test_submitted_text_cannot_ping_the_channel_or_fake_a_link():
    hostile = "<!channel> click <https://evil.example|here> & win"

    message = format_message(make_result(company=hostile, summary=hostile))

    assert "<" not in message
    assert "&lt;!channel&gt; click &lt;https://evil.example|here&gt; &amp; win" in message


def test_send_posts_the_message_to_the_webhook():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok")

    result = make_result()
    send(result, WEBHOOK, httpx.Client(transport=httpx.MockTransport(handler)))

    (request,) = requests
    assert str(request.url) == WEBHOOK
    assert json.loads(request.content) == {"text": format_message(result)}


def test_slack_rejecting_the_message_is_an_error():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(404, text="no_service"))
    )

    with pytest.raises(NotifyError, match="HTTP 404: no_service"):
        send(make_result(), WEBHOOK, client)


def test_network_failure_is_an_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    with pytest.raises(NotifyError, match="ConnectError"):
        send(make_result(), WEBHOOK, httpx.Client(transport=httpx.MockTransport(handler)))
