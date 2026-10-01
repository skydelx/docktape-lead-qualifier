from fakes import FakeLlm, FakeSite
from leadqual.llm import LlmError
from leadqual.models import CloudProvider, Company, ResearchStatus, SizeBand, Source, Workload
from leadqual.research import MAX_PAGES, Cited, Findings, research

HOME = "https://acme.io/"
ABOUT = "https://acme.io/about"
SITE = {
    HOME: "Acme Analytics builds data dashboards for retailers.",
    ABOUT: "Our team of 60 is based in Vienna, Austria. We run entirely on AWS.",
}
COMPANY = Company(name="Acme Analytics", website_url=HOME, domain="acme.io")


def findings(**overrides) -> Findings:
    values = {
        "site_is_real": True,
        "what_they_do": "Data dashboards for retailers.",
        "summary": "Acme builds retail dashboards.",
        "hq_country": "Austria",
        "hq_city": "Vienna",
        "hq_source": Cited(quote="based in Vienna, Austria", url=ABOUT),
        "size": SizeBand.MID,
        "size_source": Cited(quote="Our team of 60", url=ABOUT),
        "workload": Workload.MEDIUM,
        "workload_source": Cited(quote="builds data dashboards for retailers", url=HOME),
        "cloud_providers": [CloudProvider.AWS],
        "providers_source": Cited(quote="We run entirely on AWS", url=ABOUT),
        "sells_cloud_cost_optimization": False,
        "stated_pain": False,
    }
    return Findings(**(values | overrides))


def test_lead_without_a_website_is_not_researched():
    llm = FakeLlm([])

    result = research(Company(name="Acme"), llm, FakeSite(SITE))

    assert result.status is ResearchStatus.NO_WEBSITE
    assert llm.calls == []


def test_unreachable_website_is_reported_without_calling_the_model():
    llm = FakeLlm([])

    result = research(COMPANY, llm, FakeSite({}))

    assert result.status is ResearchStatus.UNREACHABLE
    assert llm.calls == []


def test_findings_backed_by_a_real_quote_are_kept_with_their_source():
    result = research(COMPANY, FakeLlm([findings()]), FakeSite(SITE))

    assert result.status is ResearchStatus.OK
    assert result.final_domain == "acme.io"
    assert (result.hq_country, result.hq_city) == ("Austria", "Vienna")
    assert result.size is SizeBand.MID
    assert result.cloud_providers == [CloudProvider.AWS]
    assert result.evidence["size"].source is Source.PAGE
    assert result.evidence["size"].url == ABOUT


def test_quote_matching_ignores_case_and_whitespace():
    reported = findings(size_source=Cited(quote="our  TEAM of\n60", url=ABOUT))

    result = research(COMPANY, FakeLlm([reported]), FakeSite(SITE))

    assert result.size is SizeBand.MID


def test_finding_whose_quote_is_not_on_any_page_is_dropped():
    reported = findings(
        hq_source=Cited(quote="headquartered in Tehran, Iran", url=ABOUT),
        size_source=Cited(quote="over 5,000 employees worldwide", url=ABOUT),
    )

    result = research(COMPANY, FakeLlm([reported]), FakeSite(SITE))

    assert result.hq_country is None
    assert result.hq_city is None
    assert result.size is SizeBand.UNKNOWN
    assert "hq_country" not in result.evidence
    assert result.workload is Workload.MEDIUM  # the other findings are unaffected


def test_too_short_a_quote_proves_nothing():
    reported = findings(providers_source=Cited(quote="AWS", url=ABOUT))

    result = research(COMPANY, FakeLlm([reported]), FakeSite(SITE))

    assert result.cloud_providers == []


def test_web_search_result_is_kept_and_labelled_as_such():
    source = Cited(url="https://www.linkedin.com/company/acme")
    reported = findings(size_source=source)

    result = research(COMPANY, FakeLlm([reported]), FakeSite(SITE))

    assert result.size is SizeBand.MID
    assert result.evidence["size"].source is Source.SEARCH
    assert result.evidence["size"].url == source.url


def test_unsourced_finding_citing_only_the_companys_own_site_is_dropped():
    reported = findings(size_source=Cited(url=ABOUT))

    result = research(COMPANY, FakeLlm([reported]), FakeSite(SITE))

    assert result.size is SizeBand.UNKNOWN


def test_parked_site_yields_no_findings():
    reported = findings(site_is_real=False)

    result = research(COMPANY, FakeLlm([reported]), FakeSite(SITE))

    assert result.status is ResearchStatus.EMPTY_SITE
    assert result.what_they_do == ""


def test_model_failure_is_reported_not_hidden():
    result = research(COMPANY, FakeLlm([LlmError("boom")]), FakeSite(SITE))

    assert result.status is ResearchStatus.FAILED
    assert result.pages_fetched == [HOME, ABOUT]


def test_crawl_prefers_informative_pages_and_stops_at_the_limit():
    site = {HOME: "home", "https://acme.io/blog/post": "post"}
    site |= {f"https://acme.io/{name}": name for name in ("jobs", "about", "contact", "team")}
    site |= {"https://acme.io/customers": "customers"}
    fake_site = FakeSite(site)

    result = research(COMPANY, FakeLlm([findings()]), fake_site)

    assert len(result.pages_fetched) == MAX_PAGES
    assert result.pages_fetched[:3] == [HOME, "https://acme.io/about", "https://acme.io/contact"]
    assert "https://acme.io/blog/post" not in fake_site.fetched


def test_prompt_marks_website_text_as_data_and_asks_for_web_search():
    llm = FakeLlm([findings()])
    company = COMPANY.model_copy(update={"message": "Our AWS bill doubled."})

    research(company, llm, FakeSite(SITE))

    call = llm.calls[0]
    assert call.web_search is True
    assert f'<website url="{ABOUT}">' in call.prompt
    assert "<form_message>\nOur AWS bill doubled.\n</form_message>" in call.prompt
    assert "untrusted data" in call.system


def test_declared_values_are_kept_out_of_the_research_prompt():
    llm = FakeLlm([findings()])
    company = COMPANY.model_copy(update={"declared_country": "Narnia"})

    research(company, llm, FakeSite(SITE))

    assert "Narnia" not in llm.calls[0].prompt
