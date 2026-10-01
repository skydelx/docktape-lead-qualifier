"""Test doubles and builders shared by the test modules."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from leadqual.llm import Tool
from leadqual.models import (
    Company,
    CompetitorVerdict,
    Compliance,
    Contact,
    Decision,
    Fit,
    Priority,
    Research,
    ResearchStatus,
    Result,
    Route,
    SanctionsVerdict,
)
from leadqual.web import FetchError, Page


def make_result(
    *,
    company: str = "Acme Analytics",
    domain: str | None = "acme.io",
    route: Route = Route.SALES_READY,
    reason: str = "Compliance clear; high priority (fit 85)",
    fit: int = 85,
    summary: str = "Acme builds retail dashboards on AWS.",
    compliance: Compliance | None = None,
    job_title: str | None = "CTO",
) -> Result:
    """A finished pipeline result, for testing the tracker and the notification."""
    return Result(
        contact=Contact(name="Ada Example", email="ada@acme.io", job_title=job_title),
        company=Company(name=company, domain=domain),
        research=Research(status=ResearchStatus.OK, summary=summary, hq_country="Austria"),
        compliance=compliance
        or Compliance(
            competitor=CompetitorVerdict.CLEAR,
            sanctions=SanctionsVerdict.CLEAR,
            hq_country="Austria",
            reasoning="Unrelated retail analytics company based in Austria.",
        ),
        fit=Fit(
            size_points=50,
            cloud_points=25,
            bonus_points=10,
            total=fit,
            priority=Priority.HIGH if fit >= 65 else Priority.MEDIUM if fit >= 40 else Priority.LOW,
            size_basis="51-1000 (website)",
            cloud_basis="medium workload, inferred (website)",
            size_known=True,
            cloud_known=True,
            bonuses=["runs on aws", "decision maker (CTO)"],
        ),
        decision=Decision(route=route, reason=reason),
        processed_at=datetime(2026, 10, 1, 14, 30),
    )


@dataclass
class LlmCall:
    system: str
    prompt: str
    tools: list[Tool]
    web_search: bool


@dataclass
class FakeLlm:
    """Returns scripted results in order; an exception in the script is raised instead."""

    script: list[BaseModel | Exception]
    calls: list[LlmCall] = field(default_factory=list)

    def run(
        self,
        *,
        system: str,
        prompt: str,
        result_type: type[BaseModel],
        tools: Sequence[Tool] = (),
        web_search: bool = False,
    ) -> BaseModel:
        self.calls.append(LlmCall(system, prompt, list(tools), web_search))
        result = self.script.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeSite:
    """A fetcher over an in-memory website: {url: page text}; links are every other page."""

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages
        self.fetched: list[str] = []

    def __call__(self, url: str) -> Page:
        self.fetched.append(url)
        if url not in self._pages:
            raise FetchError(f"HTTP 404 from {url}")
        links = tuple(other for other in self._pages if other != url)
        return Page(url=url, text=self._pages[url], links=links)
