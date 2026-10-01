"""Step 6: tell the sales rep. One Slack message per lead, same facts as the tracker row."""

import httpx

from leadqual.models import Result, Route

TIMEOUT_SECONDS = 10
ROUTE_EMOJI = {
    Route.SALES_READY: "🟢",
    Route.CHECK_FIRST: "🟡",
    Route.DO_NOT_ENGAGE: "🔴",
}


class NotifyError(RuntimeError):
    """The notification was not delivered."""


def format_message(result: Result) -> str:
    contact, research, compliance, fit = (
        result.contact,
        result.research,
        result.compliance,
        result.fit,
    )
    route = result.decision.route
    title = f", {contact.job_title}" if contact.job_title else ""
    bonuses = f" · bonus: {', '.join(fit.bonuses)}" if fit.bonuses else ""
    lines = [
        f"{ROUTE_EMOJI[route]} *{route}* · Fit {fit.total}, {fit.priority} priority"
        f" ({fit.data} data)",
        f"*{escape(result.company.name)}* · {escape(result.company.domain or 'no website')}",
        f"*Why:* {escape(result.decision.reason)}",
        f"*Summary:* {escape(research.brief or 'not available')}",
        f"*Fit:* size {escape(fit.size_basis)} · cloud {escape(fit.cloud_basis)}{escape(bonuses)}",
        f"*Compliance:* {escape(compliance.flag)}. {escape(compliance.reasoning)}",
        f"*Contact:* {escape(contact.name + title)} · {escape(contact.email)}",
    ]
    if route is Route.DO_NOT_ENGAGE:
        lines.append("*Do not contact this lead.*")
    return "\n".join(lines)


def escape(text: str) -> str:
    """Escape Slack control characters so submitted text cannot ping a channel or fake a link."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send(result: Result, webhook_url: str, client: httpx.Client | None = None) -> None:
    payload = {"text": format_message(result)}
    try:
        if client is None:
            response = httpx.post(webhook_url, json=payload, timeout=TIMEOUT_SECONDS)
        else:
            response = client.post(webhook_url, json=payload)
    except httpx.HTTPError as error:
        raise NotifyError(f"Slack request failed: {type(error).__name__}") from error
    if response.status_code != 200:
        raise NotifyError(f"Slack answered HTTP {response.status_code}: {response.text[:200]}")
