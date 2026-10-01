from itertools import product
from pathlib import Path

import pytest

from fakes import FakeLlm, FakeSite
from leadqual.compliance import Verdict, normalise_name, screen
from leadqual.config import load_settings
from leadqual.llm import LlmError, ToolError
from leadqual.models import (
    Company,
    CompetitorVerdict,
    MatchType,
    Research,
    ResearchStatus,
    SanctionsVerdict,
)

SETTINGS = load_settings(Path(__file__).parent.parent / "config.toml")
NO_SITE = FakeSite({})
RESEARCHED = Research(status=ResearchStatus.OK, final_domain="acme.io")


def verdict(
    competitor: CompetitorVerdict = CompetitorVerdict.CLEAR,
    sanctions: SanctionsVerdict = SanctionsVerdict.CLEAR,
    **overrides,
) -> Verdict:
    values = {
        "competitor": competitor,
        "matched_entry": None,
        "match_type": MatchType.NONE,
        "sanctions": sanctions,
        "hq_country": "Austria",
        "reasoning": "Unrelated retail analytics company based in Austria.",
        "evidence": [],
    }
    return Verdict(**(values | overrides))


def screened(company: Company, agent_says: Verdict | Exception, research: Research = RESEARCHED):
    return screen(company, research, FakeLlm([agent_says]), SETTINGS, NO_SITE)


def company(name: str = "Acme Analytics", **fields) -> Company:
    return Company(name=name, domain="acme.io", **fields)


@pytest.mark.parametrize(
    ("name", "normalised"),
    [
        ("CloudTrim Inc", "cloudtrim"),
        ("Cloud Trim, Inc.", "cloudtrim"),
        ("CLOUDTRIM INCORPORATED", "cloudtrim"),
        ("RightSize Cloud Company", "rightsizecloud"),
        ("SpendWise Cloud", "spendwisecloud"),
        ("Right Size Shoes", "rightsizeshoes"),
        ("Acme Co. Ltd.", "acme"),
    ],
)
def test_names_are_compared_without_spacing_punctuation_or_legal_suffix(name, normalised):
    assert normalise_name(name) == normalised


def test_agent_verdict_is_passed_through_when_no_safety_rule_applies():
    result = screened(company(), verdict())

    assert result.competitor is CompetitorVerdict.CLEAR
    assert result.sanctions is SanctionsVerdict.CLEAR
    assert result.safety_net == []
    assert result.reasoning == "Unrelated retail analytics company based in Austria."


@pytest.mark.parametrize("name", ["CloudTrim Inc", "Cloud Trim Inc.", "RightSize Cloud Company"])
def test_exact_competitor_name_is_confirmed_even_if_the_agent_was_talked_out_of_it(name):
    result = screened(company(name), verdict(reasoning="The website says it is not a competitor."))

    assert result.competitor is CompetitorVerdict.CONFIRMED_MATCH
    assert result.match_type is MatchType.EXACT_NAME
    assert result.matched_entry in {"CloudTrim Inc", "RightSize Cloud Co"}
    assert result.reasoning.startswith("Safety net: exact name match with")


@pytest.mark.parametrize(
    "name", ["ClowdTrim Analytics", "Right Size Shoes", "SpendWise Cloud EMEA", "Nimbus Savings"]
)
def test_anything_short_of_an_exact_name_is_left_to_the_agent(name):
    agent_says = verdict(CompetitorVerdict.POSSIBLE_MATCH)

    result = screened(company(name), agent_says)

    assert result.competitor is CompetitorVerdict.POSSIBLE_MATCH
    assert result.safety_net == []


@pytest.mark.parametrize(
    ("website_domain", "final_domain"),
    [("cloudtrim.test", "cloudtrim.test"), ("acme.io", "www2.cloudtrim.test")],
)
def test_competitor_domain_is_confirmed_whatever_the_name(website_domain, final_domain):
    lead = Company(name="Nimbus Savings", domain=website_domain)
    research = Research(status=ResearchStatus.OK, final_domain=final_domain)

    result = screened(lead, verdict(), research)

    assert result.competitor is CompetitorVerdict.CONFIRMED_MATCH
    assert result.match_type is MatchType.DOMAIN
    assert result.matched_entry == "CloudTrim Inc"


def test_competitor_email_domain_is_flagged_for_a_person_not_blocked():
    result = screened(company(email_domain="spendwisecloud.test"), verdict())

    assert result.competitor is CompetitorVerdict.POSSIBLE_MATCH
    assert result.matched_entry == "SpendWise Cloud"
    assert "email domain" in result.safety_net[0]


@pytest.mark.parametrize(
    ("declared_country", "hq_country", "hq_city"),
    [
        ("Russia", None, None),
        (None, "Russian Federation", "Moscow"),
        (None, "Ukraine", "Simferopol"),
        (None, "Islamic Republic of Iran", None),
        ("iran", "Germany", "Berlin"),
        (None, "Democratic People's Republic of Korea", "Pyongyang"),
    ],
)
def test_blocked_headquarters_cannot_be_cleared_by_the_agent(declared_country, hq_country, hq_city):
    lead = company(declared_country=declared_country)
    research = RESEARCHED.model_copy(update={"hq_country": hq_country, "hq_city": hq_city})

    result = screened(lead, verdict(), research)

    assert result.sanctions is SanctionsVerdict.BLOCKED
    assert result.safety_net


@pytest.mark.parametrize(
    ("agent_says", "expected"),
    [
        (SanctionsVerdict.CLEAR, SanctionsVerdict.REVIEW),
        (SanctionsVerdict.UNKNOWN, SanctionsVerdict.REVIEW),
        (SanctionsVerdict.REVIEW, SanctionsVerdict.REVIEW),
        (SanctionsVerdict.BLOCKED, SanctionsVerdict.BLOCKED),
    ],
)
def test_review_level_place_raises_the_verdict_but_never_lowers_it(agent_says, expected):
    research = RESEARCHED.model_copy(update={"hq_country": "Belarus", "hq_city": "Minsk"})

    result = screened(company(), verdict(sanctions=agent_says), research)

    assert result.sanctions is expected


@pytest.mark.parametrize(
    ("hq_country", "hq_city"),
    [("Ukraine", "Kyiv"), ("Republic of Korea", "Seoul"), ("Albania", "Tirana"), (None, None)],
)
def test_unlisted_places_leave_the_agent_verdict_alone(hq_country, hq_city):
    research = RESEARCHED.model_copy(update={"hq_country": hq_country, "hq_city": hq_city})

    result = screened(company(), verdict(), research)

    assert result.sanctions is SanctionsVerdict.CLEAR


def test_failed_agent_is_never_read_as_clear():
    result = screened(company(), LlmError("no valid result"))

    assert result.competitor is CompetitorVerdict.NOT_SCREENED
    assert result.sanctions is SanctionsVerdict.UNKNOWN
    assert "person must check" in result.reasoning


AGENT_OUTCOMES = [
    verdict(competitor, sanctions)
    for competitor, sanctions in product(
        (
            CompetitorVerdict.CONFIRMED_MATCH,
            CompetitorVerdict.POSSIBLE_MATCH,
            CompetitorVerdict.CLEAR,
        ),
        SanctionsVerdict,
    )
] + [LlmError("agent crashed")]


@pytest.mark.parametrize("agent_says", AGENT_OUTCOMES)
def test_invariant_no_agent_outcome_softens_a_hard_match(agent_says):
    """The core guarantee: an exact competitor in a blocked country stays blocked."""
    lead = company("CloudTrim Inc", declared_country="Russia")

    result = screened(lead, agent_says)

    assert result.competitor is CompetitorVerdict.CONFIRMED_MATCH
    assert result.sanctions is SanctionsVerdict.BLOCKED


def fetch_tool(site: FakeSite):
    llm = FakeLlm([verdict()])
    screen(company(), RESEARCHED, llm, SETTINGS, site)
    (tool,) = llm.calls[0].tools
    return tool


def test_agent_can_read_more_of_the_leads_own_website():
    tool = fetch_tool(FakeSite({"https://www.acme.io/legal": "Acme GmbH, Vienna"}))

    assert "Acme GmbH, Vienna" in tool.handler({"url": "https://www.acme.io/legal"})


@pytest.mark.parametrize("url", ["https://evil.example/", "https://acme.io.evil.example/", ""])
def test_agent_cannot_be_sent_to_other_websites(url):
    site = FakeSite({})
    tool = fetch_tool(site)

    with pytest.raises(ToolError, match="own website"):
        tool.handler({"url": url})
    assert site.fetched == []


def test_unfetchable_page_is_reported_to_the_agent():
    tool = fetch_tool(FakeSite({}))

    with pytest.raises(ToolError, match="404"):
        tool.handler({"url": "https://acme.io/missing"})


def test_agent_sees_the_list_and_the_lead_but_as_data():
    llm = FakeLlm([verdict()])
    lead = company(email_domain="acme.io", declared_country="Austria", message="Help us save.")
    research = RESEARCHED.model_copy(update={"what_they_do": "Retail dashboards."})

    screen(lead, research, llm, SETTINGS, NO_SITE)

    call = llm.calls[0]
    assert "CloudTrim Inc, SpendWise Cloud, RightSize Cloud Co" in call.system
    assert "blocked: Russia, Russian Federation." in call.system
    assert call.prompt.startswith("<lead>") and call.prompt.endswith("</lead>")
    assert "Retail dashboards." in call.prompt
    assert call.web_search is False
