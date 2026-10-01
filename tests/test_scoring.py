from pathlib import Path

import pytest

from leadqual.config import load_settings
from leadqual.models import (
    CloudProvider,
    Company,
    CompetitorVerdict,
    Compliance,
    Contact,
    Evidence,
    Fit,
    MatchType,
    Priority,
    Research,
    ResearchStatus,
    Route,
    SanctionsVerdict,
    SizeBand,
    Source,
    SpendBand,
    Workload,
)
from leadqual.scoring import RULES, decide, score

SETTINGS = load_settings(Path(__file__).parent.parent / "config.toml").scoring
PAGE = Evidence(source=Source.PAGE, quote="a quote from the site", url="https://acme.io/about")
SEARCH = Evidence(source=Source.SEARCH, url="https://www.linkedin.com/company/acme")


def lead(
    *,
    declared_size: SizeBand = SizeBand.UNKNOWN,
    spend: SpendBand = SpendBand.UNKNOWN,
    providers: tuple[CloudProvider, ...] = (),
    job_title: str | None = None,
    found_size: SizeBand = SizeBand.UNKNOWN,
    workload: Workload = Workload.UNKNOWN,
    found_providers: tuple[CloudProvider, ...] = (),
    pain: bool = False,
    size_evidence: Evidence = PAGE,
) -> Fit:
    company = Company(
        name="Acme",
        declared_size=declared_size,
        declared_spend=spend,
        declared_providers=list(providers),
    )
    contact = Contact(name="Ada Example", email="ada@acme.io", job_title=job_title)
    research = Research(
        status=ResearchStatus.OK,
        size=found_size,
        workload=workload,
        cloud_providers=list(found_providers),
        stated_pain=pain,
        evidence={"size": size_evidence, "workload": PAGE},
    )
    return score(company, contact, research, SETTINGS)


def test_size_and_declared_spend_are_the_two_axes():
    fit = lead(found_size=SizeBand.MID, spend=SpendBand.OVER_20K)

    assert (fit.size_points, fit.cloud_points, fit.bonus_points, fit.total) == (50, 50, 0, 100)
    assert fit.size_basis == "51-1000 (website)"
    assert fit.cloud_basis == ">20k/month (self-reported, unverified)"
    assert fit.data == "full"


def test_inferred_workload_is_used_when_no_bill_was_declared_and_counts_for_less():
    inferred = lead(found_size=SizeBand.MID, workload=Workload.HIGH)
    declared = lead(found_size=SizeBand.MID, spend=SpendBand.OVER_20K)

    assert inferred.cloud_points == 40 < declared.cloud_points
    assert inferred.cloud_basis == "high workload, inferred (website)"


def test_declared_bill_wins_over_inferred_workload():
    fit = lead(spend=SpendBand.UNDER_5K, workload=Workload.HIGH)

    assert fit.cloud_points == 10


def test_researched_size_wins_over_the_form_and_names_its_source():
    fit = lead(declared_size=SizeBand.MID, found_size=SizeBand.SMALL, size_evidence=SEARCH)

    assert fit.size_points == 40
    assert fit.size_basis == "11-50 (web search)"


def test_self_reported_size_counts_but_is_labelled_unverified():
    fit = lead(declared_size=SizeBand.MID)

    assert fit.size_points == 50
    assert fit.size_basis == "51-1000 (self-reported, unverified)"


def test_unknown_axis_is_neutral_not_small():
    fit = lead(spend=SpendBand.OVER_20K)

    assert fit.size_points == 25 > SETTINGS.size_points[SizeBand.MICRO]
    assert fit.size_basis == "unknown"
    assert fit.data == "partial"


def test_nothing_known_scores_the_neutral_middle_and_says_so():
    fit = lead()

    assert fit.total == 50
    assert fit.data == "none"


@pytest.mark.parametrize(
    "job_title",
    ["CTO", "Co-Founder & CEO", "VP Engineering", "Head of Platform", "Chief Financial Officer"],
)
def test_decision_makers_earn_a_bonus(job_title):
    assert lead(job_title=job_title).bonuses == [f"decision maker ({job_title})"]


@pytest.mark.parametrize("job_title", [None, "", "Software Engineer", "Doctoral student", "Intern"])
def test_other_titles_do_not(job_title):
    assert lead(job_title=job_title).bonuses == []


def test_preferred_provider_bonus_from_the_form_or_from_research():
    declared = lead(providers=(CloudProvider.AWS, CloudProvider.GCP))
    found = lead(found_providers=(CloudProvider.ORACLE,))

    assert declared.bonuses == ["runs on aws"]
    assert found.bonuses == ["runs on oracle"]


def test_other_providers_cost_nothing():
    on_azure = lead(found_size=SizeBand.MID, providers=(CloudProvider.AZURE,))
    undeclared = lead(found_size=SizeBand.MID)

    assert on_azure.total == undeclared.total


def test_bonuses_are_capped():
    fit = lead(providers=(CloudProvider.AWS,), job_title="CTO", pain=True)

    assert len(fit.bonuses) == 3
    assert fit.bonus_points == SETTINGS.max_bonus == 10


def test_total_never_exceeds_100():
    fit = lead(
        found_size=SizeBand.MID,
        spend=SpendBand.OVER_20K,
        providers=(CloudProvider.AWS,),
        job_title="CTO",
    )

    assert fit.total == 100


@pytest.mark.parametrize(
    ("declared", "found", "conflict"),
    [
        (SizeBand.MID, SizeBand.SMALL, False),  # neighbouring bands: a rounding difference
        (SizeBand.ENTERPRISE, SizeBand.MICRO, True),
        (SizeBand.MICRO, SizeBand.MID, True),
        (SizeBand.MID, SizeBand.UNKNOWN, False),
    ],
)
def test_size_contradiction_needs_bands_far_apart(declared, found, conflict):
    assert bool(lead(declared_size=declared, found_size=found).conflicts) is conflict


def test_big_declared_bill_with_a_small_workload_is_a_contradiction():
    fit = lead(spend=SpendBand.OVER_20K, workload=Workload.LOW)

    assert fit.conflicts == ["form says a >20k monthly bill, research found a low cloud workload"]


def test_sanity_ranking_of_example_companies():
    """Would a sales lead agree with this order? Checked by eye, pinned here."""
    scale_up = lead(
        found_size=SizeBand.MID,
        spend=SpendBand.OVER_20K,
        providers=(CloudProvider.AWS,),
        job_title="CTO",
    )
    funded_startup = lead(
        found_size=SizeBand.SMALL, spend=SpendBand.FROM_5K_TO_20K, providers=(CloudProvider.AWS,)
    )
    saas_no_bill_given = lead(found_size=SizeBand.MID, workload=Workload.MEDIUM)
    data_heavy_enterprise = lead(found_size=SizeBand.LARGE, workload=Workload.HIGH)
    tiny_startup_in_pain = lead(
        found_size=SizeBand.MICRO,
        spend=SpendBand.FROM_5K_TO_20K,
        providers=(CloudProvider.AWS,),
        job_title="Founder",
        pain=True,
    )
    small_firm_small_cloud = lead(found_size=SizeBand.SMALL, workload=Workload.LOW)
    local_agency = lead(found_size=SizeBand.MICRO, workload=Workload.LOW)

    ranking = [
        scale_up.total,
        funded_startup.total,
        saas_no_bill_given.total,
        tiny_startup_in_pain.total,
        small_firm_small_cloud.total,
        local_agency.total,
    ]
    assert ranking == [100, 80, 75, 60, 45, 20]
    assert data_heavy_enterprise.total == saas_no_bill_given.total == 75


def test_priority_labels_follow_the_configured_thresholds():
    assert lead(found_size=SizeBand.MID, spend=SpendBand.OVER_20K).priority is Priority.HIGH
    assert lead(found_size=SizeBand.SMALL, workload=Workload.LOW).priority is Priority.MEDIUM
    assert lead(found_size=SizeBand.MICRO, workload=Workload.LOW).priority is Priority.LOW


def fit_of(total: int, **overrides) -> Fit:
    values = {
        "size_points": 0,
        "cloud_points": 0,
        "bonus_points": 0,
        "total": total,
        "priority": Priority.HIGH
        if total >= 65
        else Priority.MEDIUM
        if total >= 40
        else Priority.LOW,
        "size_basis": "",
        "cloud_basis": "",
        "size_known": True,
        "cloud_known": True,
    }
    return Fit(**(values | overrides))


def compliance(
    competitor: CompetitorVerdict = CompetitorVerdict.CLEAR,
    sanctions: SanctionsVerdict = SanctionsVerdict.CLEAR,
    matched_entry: str | None = None,
    hq_country: str | None = "Austria",
) -> Compliance:
    return Compliance(
        competitor=competitor,
        matched_entry=matched_entry,
        match_type=MatchType.NONE,
        sanctions=sanctions,
        hq_country=hq_country,
        reasoning="",
    )


OK = Research(status=ResearchStatus.OK)
CLEAR = compliance()


@pytest.mark.parametrize(
    ("total", "reason"),
    [
        (100, "Compliance clear; high priority (fit 100)"),
        (65, "Compliance clear; high priority (fit 65)"),
        (64, "Compliance clear; medium priority (fit 64)"),
        (40, "Compliance clear; medium priority (fit 40)"),
        (39, "Compliance clear; low priority (fit 39)"),
        (0, "Compliance clear; low priority (fit 0)"),
    ],
)
def test_a_clean_lead_is_always_callable_and_the_score_only_orders_the_list(total, reason):
    decision = decide(CLEAR, OK, fit_of(total))

    assert (decision.route, decision.reason) == (Route.SALES_READY, reason)


@pytest.mark.parametrize(
    ("screening", "route", "reason"),
    [
        (
            compliance(CompetitorVerdict.CONFIRMED_MATCH, matched_entry="CloudTrim Inc"),
            Route.DO_NOT_ENGAGE,
            "Competitor: CloudTrim Inc",
        ),
        (
            compliance(sanctions=SanctionsVerdict.BLOCKED, hq_country="Iran"),
            Route.DO_NOT_ENGAGE,
            "Headquartered in a sanctioned place: Iran",
        ),
        (
            compliance(CompetitorVerdict.NOT_SCREENED, SanctionsVerdict.UNKNOWN),
            Route.CHECK_FIRST,
            "Compliance screening did not complete",
        ),
        (
            compliance(CompetitorVerdict.POSSIBLE_MATCH, matched_entry="SpendWise Cloud"),
            Route.CHECK_FIRST,
            "Possible competitor: SpendWise Cloud",
        ),
        (
            compliance(CompetitorVerdict.POSSIBLE_MATCH),
            Route.CHECK_FIRST,
            "Possible competitor: sells the same service",
        ),
        (
            compliance(sanctions=SanctionsVerdict.REVIEW, hq_country="Belarus"),
            Route.CHECK_FIRST,
            "Headquarters needs a sanctions check: Belarus",
        ),
        (
            compliance(sanctions=SanctionsVerdict.UNKNOWN, hq_country=None),
            Route.CHECK_FIRST,
            "Headquarters country not found, so sanctions could not be ruled out",
        ),
    ],
)
def test_compliance_findings_stop_even_a_perfect_fit(screening, route, reason):
    decision = decide(screening, OK, fit_of(100))

    assert (decision.route, decision.reason) == (route, reason)


@pytest.mark.parametrize(
    "status",
    [status for status in ResearchStatus if status is not ResearchStatus.OK],
)
def test_a_company_we_could_not_research_goes_to_a_person(status):
    decision = decide(CLEAR, Research(status=status), fit_of(100))

    assert decision.route is Route.CHECK_FIRST
    assert decision.reason == f"Could not research the company ({status.value})"


def test_contradictions_go_to_a_person():
    fit = fit_of(100, conflicts=["form says 5000+ employees, research found 1-10"])

    decision = decide(CLEAR, OK, fit)

    assert decision.route is Route.CHECK_FIRST
    assert "5000+" in decision.reason


def test_a_lead_with_no_fit_data_is_ranked_in_the_middle_not_sent_to_a_person():
    fit = fit_of(50, size_known=False, cloud_known=False)

    assert decide(CLEAR, OK, fit).route is Route.SALES_READY
    assert fit.data == "none"


def test_a_competitor_in_a_sanctioned_country_is_reported_as_a_competitor_first():
    both = compliance(
        CompetitorVerdict.CONFIRMED_MATCH, SanctionsVerdict.BLOCKED, matched_entry="CloudTrim Inc"
    )

    assert decide(both, OK, fit_of(100)).reason == "Competitor: CloudTrim Inc"


def test_blocked_beats_every_softer_outcome():
    """A sanctioned lead that also looks like a near-match must be blocked, not reviewed."""
    blocked_near_match = compliance(CompetitorVerdict.POSSIBLE_MATCH, SanctionsVerdict.BLOCKED)
    unresearched = Research(status=ResearchStatus.UNREACHABLE)

    decision = decide(blocked_near_match, unresearched, fit_of(0))

    assert decision.route is Route.DO_NOT_ENGAGE


def test_the_rule_table_ends_with_a_catch_all():
    assert RULES[-1].route is Route.SALES_READY
