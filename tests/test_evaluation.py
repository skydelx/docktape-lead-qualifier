from pathlib import Path

from fakes import FakeLlm
from leadqual.compliance import Verdict
from leadqual.config import load_settings
from leadqual.evaluation import Case, load_cases, report, run_all, run_case
from leadqual.llm import LlmError
from leadqual.models import CompetitorVerdict, MatchType, SanctionsVerdict

ROOT = Path(__file__).parent.parent
SETTINGS = load_settings(ROOT / "config.toml")
COMPETITOR_CASE = Case(
    id="X01", kind="competitor", expected="clear", company="Right Size Shoes", page="Shoes."
)
HQ_CASE = Case(
    id="X02", kind="headquarters", expected="blocked", company="Pars Data", page="HQ: Tehran."
)


def verdict(competitor: CompetitorVerdict, sanctions: SanctionsVerdict) -> Verdict:
    return Verdict(
        competitor=competitor,
        matched_entry=None,
        match_type=MatchType.NONE,
        sanctions=sanctions,
        hq_country=None,
        reasoning="because",
        evidence=[],
    )


def scripted(agent_says: Verdict) -> FakeLlm:
    """Research fails (the script's first entry), so only the screening verdict matters."""
    return FakeLlm([LlmError("research not under test"), agent_says])


def test_shipped_cases_are_valid_and_cover_both_kinds():
    cases = load_cases(ROOT / "eval" / "compliance_cases.json")

    assert len(cases) == 24
    assert {case.kind for case in cases} == {"competitor", "headquarters"}
    assert len({case.id for case in cases}) == 24


def test_competitor_case_passes_only_on_the_exact_verdict():
    clear = verdict(CompetitorVerdict.CLEAR, SanctionsVerdict.CLEAR)
    possible = verdict(CompetitorVerdict.POSSIBLE_MATCH, SanctionsVerdict.CLEAR)

    assert run_case(COMPETITOR_CASE, scripted(clear), SETTINGS).passed
    outcome = run_case(COMPETITOR_CASE, scripted(possible), SETTINGS)
    assert not outcome.passed
    assert outcome.actual == "possible_match"


def test_headquarters_case_only_asks_whether_the_lead_was_blocked():
    blocked = verdict(CompetitorVerdict.CLEAR, SanctionsVerdict.BLOCKED)
    review = verdict(CompetitorVerdict.CLEAR, SanctionsVerdict.REVIEW)

    assert run_case(HQ_CASE, scripted(blocked), SETTINGS).passed
    assert run_case(HQ_CASE, scripted(review), SETTINGS).actual == "not_blocked"


def test_the_case_page_is_the_only_page_of_the_fake_website():
    llm = scripted(verdict(CompetitorVerdict.CLEAR, SanctionsVerdict.CLEAR))

    run_case(COMPETITOR_CASE, llm, SETTINGS)

    research_call = llm.calls[0]
    assert '<website url="https://lead-x01.test/">\nShoes.\n</website>' in research_call.prompt
    assert research_call.web_search is False


def test_report_counts_per_kind_and_lists_failures():
    clear = verdict(CompetitorVerdict.CLEAR, SanctionsVerdict.CLEAR)
    scripts = iter([scripted(clear), scripted(clear)])

    outcomes = run_all([COMPETITOR_CASE, HQ_CASE], lambda: next(scripts), SETTINGS)

    assert report(outcomes).splitlines() == [
        "competitor: 1/1",
        "headquarters: 0/1",
        "total: 1/2",
        "FAIL X02 'Pars Data': expected blocked, got not_blocked - because",
    ]
