"""The whole decision path, with a scripted model and an in-memory website."""

from pathlib import Path

from fakes import FakeLlm, FakeSite
from leadqual.compliance import Verdict
from leadqual.config import load_settings
from leadqual.llm import LlmError
from leadqual.models import (
    CloudProvider,
    CompetitorVerdict,
    MatchType,
    ResearchStatus,
    Route,
    SanctionsVerdict,
    SizeBand,
    Workload,
)
from leadqual.pipeline import qualify
from leadqual.research import Cited, Findings

SETTINGS = load_settings(Path(__file__).parent.parent / "config.toml")
HOME = "https://acme.io"
ABOUT = "https://acme.io/about"
SITE = FakeSite(
    {
        HOME: "Acme Analytics builds data dashboards for retailers.",
        ABOUT: "Our team of 60 is based in Vienna, Austria. We run entirely on AWS.",
    }
)
LEAD = {
    "name": "Ada Example",
    "email": "ada.example@acme.io",
    "company": "Acme Analytics",
    "website": "acme.io",
    "job_title": "CTO",
}
FINDINGS = Findings(
    site_is_real=True,
    what_they_do="Data dashboards for retailers.",
    summary="Acme builds retail dashboards on AWS.",
    hq_country="Austria",
    hq_city="Vienna",
    hq_source=Cited(quote="based in Vienna, Austria", url=ABOUT),
    size=SizeBand.MID,
    size_source=Cited(quote="Our team of 60", url=ABOUT),
    workload=Workload.MEDIUM,
    workload_source=Cited(quote="builds data dashboards for retailers", url=HOME),
    cloud_providers=[CloudProvider.AWS],
    providers_source=Cited(quote="We run entirely on AWS", url=ABOUT),
    other_names=[],
    names_source=Cited(),
    sells_cloud_cost_optimization=False,
    stated_pain=False,
)
CLEAR = Verdict(
    competitor=CompetitorVerdict.CLEAR,
    matched_entry=None,
    match_type=MatchType.NONE,
    sanctions=SanctionsVerdict.CLEAR,
    hq_country="Austria",
    reasoning="Unrelated retail analytics company based in Austria.",
    evidence=["based in Vienna, Austria"],
)


def test_a_good_lead_becomes_sales_ready():
    result = qualify(LEAD, settings=SETTINGS, llm=FakeLlm([FINDINGS, CLEAR]), fetch=SITE)

    assert result.decision.route is Route.SALES_READY
    assert result.fit.total == 85  # 50 size + 25 workload + 10 bonus (AWS, CTO)
    assert result.compliance.flag == "clear"
    assert result.contact.email == "ada.example@acme.io"


def test_a_competitor_is_blocked_even_when_its_site_is_down_and_the_agent_says_clear():
    lead = LEAD | {"company": "Cloud Trim Inc.", "website": "cloudtrim-labs.test"}

    result = qualify(lead, settings=SETTINGS, llm=FakeLlm([CLEAR]), fetch=FakeSite({}))

    assert result.research.status is ResearchStatus.UNREACHABLE
    assert result.decision.route is Route.DO_NOT_ENGAGE
    assert result.decision.reason == "Competitor: CloudTrim Inc"


def test_when_the_model_is_down_every_lead_goes_to_a_person():
    broken = FakeLlm([LlmError("down"), LlmError("down")])

    result = qualify(LEAD, settings=SETTINGS, llm=broken, fetch=SITE)

    assert result.research.status is ResearchStatus.FAILED
    assert result.decision.route is Route.CHECK_FIRST
    assert result.decision.reason == "Compliance screening did not complete"


def test_the_contacts_name_and_email_never_reach_the_model():
    llm = FakeLlm([FINDINGS, CLEAR])

    qualify(LEAD, settings=SETTINGS, llm=llm, fetch=SITE)

    assert len(llm.calls) == 2
    sent_to_model = " ".join(call.system + call.prompt for call in llm.calls)
    assert "Ada" not in sent_to_model
    assert "ada.example" not in sent_to_model
    assert "CTO" not in sent_to_model
