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

    assert len(cases) == 26
    assert {case.kind for case in cases} == {"competitor", "headquarters"}
    assert len({case.id for case in cases}) == 26


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


def test_a_failed_agent_call_never_counts_as_a_pass():
    not_blocked_case = HQ_CASE.model_copy(update={"expected": "not_blocked"})
    broken = FakeLlm([LlmError("research down"), LlmError("agent down")])

    outcome = run_case(not_blocked_case, broken, SETTINGS)

    assert outcome.actual == "not_screened"
    assert not outcome.passed


def test_the_case_page_is_the_only_page_of_the_fake_website():
    llm = scripted(verdict(CompetitorVerdict.CLEAR, SanctionsVerdict.CLEAR))

    run_case(COMPETITOR_CASE, llm, SETTINGS)

    research_call = llm.calls[0]
    assert '<website url="https://lead-x01.test/">\nShoes.\n</website>' in research_call.prompt
    assert research_call.web_search is False


def test_report_counts_per_kind_lists_failures_and_says_what_the_code_decided():
    clear = verdict(CompetitorVerdict.CLEAR, SanctionsVerdict.CLEAR)
    exact_name_case = COMPETITOR_CASE.model_copy(
        update={"id": "X03", "company": "CloudTrim Inc", "expected": "confirmed_match"}
    )
    scripts = iter([scripted(clear), scripted(clear), scripted(clear)])

    outcomes = run_all([COMPETITOR_CASE, HQ_CASE, exact_name_case], lambda: next(scripts), SETTINGS)

    lines = report(outcomes).splitlines()
    assert lines[:4] == [
        "competitor: 2/2",
        "headquarters: 0/1",
        "total: 2/3",
        "of the passes, decided by the code safety net: 1 ['X03']",
    ]
    assert lines[4].startswith("FAIL X02 'Pars Data': expected blocked, got not_blocked - ")
