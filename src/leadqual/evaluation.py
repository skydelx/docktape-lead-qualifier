"""Measures the compliance check on labelled, fictional leads, using the real model.

Each case is a lead whose website is one in-memory page, so the run exercises the
same research and screening code as a real lead without touching the internet.
"""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from leadqual.compliance import screen
from leadqual.config import Settings
from leadqual.llm import Llm
from leadqual.models import Company, Compliance, SanctionsVerdict
from leadqual.research import research
from leadqual.web import FetchError, Page

DEFAULT_CASES_PATH = Path("eval/compliance_cases.json")
WORKERS = 3


class Case(BaseModel):
    id: str
    kind: Literal["competitor", "headquarters"]
    expected: str
    company: str
    page: str


class Outcome(BaseModel):
    case: Case
    actual: str
    passed: bool
    reasoning: str


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[Case]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Case.model_validate(case) for case in data["cases"]]


def run_case(case: Case, llm: Llm, settings: Settings) -> Outcome:
    url = f"https://lead-{case.id.lower()}.test/"

    def fetch(requested: str) -> Page:
        if requested != url:
            raise FetchError(f"HTTP 404 from {requested}")
        return Page(url=url, text=case.page, links=())

    company = Company(name=case.company, website_url=url, domain=f"lead-{case.id.lower()}.test")
    findings = research(company, llm, fetch, web_search=False)
    compliance = screen(company, findings, llm, settings, fetch)
    actual = _actual(case, compliance)
    return Outcome(
        case=case, actual=actual, passed=actual == case.expected, reasoning=compliance.reasoning
    )


def _actual(case: Case, compliance: Compliance) -> str:
    if case.kind == "competitor":
        return compliance.competitor.value
    return "blocked" if compliance.sanctions is SanctionsVerdict.BLOCKED else "not_blocked"


def run_all(cases: list[Case], make_llm: Callable[[], Llm], settings: Settings) -> list[Outcome]:
    """Run the cases a few at a time; each gets its own model client."""
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(lambda case: run_case(case, make_llm(), settings), cases))


def report(outcomes: list[Outcome]) -> str:
    lines = []
    for kind in ("competitor", "headquarters"):
        of_kind = [outcome for outcome in outcomes if outcome.case.kind == kind]
        passed = sum(outcome.passed for outcome in of_kind)
        lines.append(f"{kind}: {passed}/{len(of_kind)}")
    lines.append(f"total: {sum(outcome.passed for outcome in outcomes)}/{len(outcomes)}")
    for outcome in outcomes:
        if not outcome.passed:
            case = outcome.case
            lines.append(
                f"FAIL {case.id} {case.company!r}: expected {case.expected}, got {outcome.actual}"
                f" - {outcome.reasoning}"
            )
    return "\n".join(lines)
