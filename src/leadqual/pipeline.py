"""The six steps of the exercise brief, in order."""

from datetime import datetime
from typing import Any

from leadqual import compliance, intake, research, scoring
from leadqual.config import Settings
from leadqual.llm import Llm
from leadqual.models import Result
from leadqual.research import Fetcher
from leadqual.web import fetch_page


def qualify(
    payload: dict[str, Any], *, settings: Settings, llm: Llm, fetch: Fetcher = fetch_page
) -> Result:
    """Steps 1-4: turn one form submission into a decision. No side effects."""
    contact, company = intake.accept(payload)
    findings = research.research(company, llm, fetch)
    screening = compliance.screen(company, findings, llm, settings, fetch)
    fit = scoring.score(company, contact, findings, settings.scoring)
    decision = scoring.decide(screening, findings, fit, settings.scoring)
    return Result(
        contact=contact,
        company=company,
        research=findings,
        compliance=screening,
        fit=fit,
        decision=decision,
        processed_at=datetime.now(),
    )
