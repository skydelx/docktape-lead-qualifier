import pytest

from leadqual.intake import InvalidLead, accept, normalise_website
from leadqual.models import CloudProvider, SizeBand, SpendBand

BASE = {
    "name": "Ada Example",
    "email": "ada@acme.io",
    "company": "Acme Analytics",
    "website": "https://www.acme.io/about",
}


def test_minimal_lead_is_split_into_contact_and_company():
    contact, company = accept(BASE)

    assert contact.name == "Ada Example"
    assert contact.email == "ada@acme.io"
    assert company.name == "Acme Analytics"
    assert company.domain == "acme.io"
    assert company.email_domain == "acme.io"
    assert company.notes == []


def test_company_carries_no_personal_data():
    _, company = accept(BASE | {"job_title": "CTO"})

    dumped = company.model_dump_json()
    assert "Ada" not in dumped
    assert "ada@" not in dumped


def test_optional_fields_default_to_unknown():
    _, company = accept(BASE)

    assert company.declared_size is SizeBand.UNKNOWN
    assert company.declared_providers == []


def test_optional_fields_are_parsed():
    _, company = accept(BASE | {"company_size": "51-1000", "cloud_providers": ["aws", "gcp"]})

    assert company.declared_size is SizeBand.MID
    assert company.declared_providers == [CloudProvider.AWS, CloudProvider.GCP]


@pytest.mark.parametrize(
    "untouched",
    [
        {"company_size": ""},
        {"company_size": None},
        {"monthly_cloud_spend": "  "},
        {"cloud_providers": None},
        {"cloud_providers": ""},
        {"job_title": "", "country": "", "message": ""},
    ],
)
def test_a_field_the_form_posts_empty_does_not_refuse_the_lead(untouched):
    """Found in review: an untouched dropdown arrives as "" and rejected the whole lead."""
    _, company = accept(BASE | untouched)

    assert company.declared_size is SizeBand.UNKNOWN
    assert company.declared_spend is SpendBand.UNKNOWN
    assert company.declared_providers == []


def test_a_missing_website_value_is_the_same_as_no_website():
    _, company = accept(BASE | {"website": None})

    assert company.domain is None
    assert "no usable website submitted" in company.notes


def test_provider_names_are_accepted_in_any_case():
    _, company = accept(BASE | {"cloud_providers": ["AWS", " Oracle "]})

    assert company.declared_providers == [CloudProvider.AWS, CloudProvider.ORACLE]


@pytest.mark.parametrize(
    ("website", "domain"),
    [
        ("acme.io", "acme.io"),
        ("WWW.Acme.IO", "acme.io"),
        ("http://acme.io/", "acme.io"),
        ("https://app.acme.io/pricing?x=1", "app.acme.io"),
        ("bücher.example", "xn--bcher-kva.example"),
    ],
)
def test_website_is_normalised_to_a_bare_domain(website, domain):
    assert normalise_website(website)[1] == domain


@pytest.mark.parametrize(
    "website",
    [
        "",
        "   ",
        "n/a",
        "ftp://acme.io",
        "https://www.linkedin.com/company/acme",
        "http://[bad",
        "https://a.b*c",  # characters no host name can contain
        'https://acme.io"><script>',
        "https://acme..io",
    ],
)
def test_unusable_website_is_rejected_but_the_lead_is_kept(website):
    _, company = accept(BASE | {"website": website})

    assert company.domain is None
    assert company.website_url is None
    assert "no usable website submitted" in company.notes


def test_free_mail_address_is_not_treated_as_a_company_domain():
    _, company = accept(BASE | {"email": "ada@gmail.com"})

    assert company.email_domain is None
    assert "free-mail address" in company.notes


def test_mismatching_email_domain_is_noted():
    _, company = accept(BASE | {"email": "ada@other.example"})

    assert company.email_domain == "other.example"
    assert any("differs" in note for note in company.notes)


def test_subdomain_email_matches_the_website():
    _, company = accept(BASE | {"email": "ada@mail.acme.io"})

    assert company.notes == []


@pytest.mark.parametrize(
    "payload",
    [
        {**BASE, "email": "not-an-email"},
        {**BASE, "name": ""},
        {**BASE, "company_size": "huge"},
        {"email": "ada@acme.io"},
    ],
)
def test_broken_submission_is_refused(payload):
    with pytest.raises(InvalidLead):
        accept(payload)
