"""Step 1: validate a submission and split it into personal data and company data."""

import re
from urllib.parse import urlsplit

from pydantic import ValidationError

from leadqual.models import Company, Contact, Lead

FREE_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "aol.com",
    }
)
# A social profile is not a company website we can research.
SOCIAL_HOSTS = frozenset({"linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com"})

_EMAIL = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$")


class InvalidLead(ValueError):
    """The submission cannot be processed at all."""


def accept(payload: dict) -> tuple[Contact, Company]:
    """Validate a raw form payload and separate the contact from the company."""
    try:
        lead = Lead.model_validate(payload)
    except ValidationError as error:
        raise InvalidLead(str(error)) from error

    email_match = _EMAIL.match(lead.email)
    if email_match is None:
        raise InvalidLead(f"not an email address: {lead.email!r}")
    email_domain = email_match.group(1).lower()

    website_url, domain = normalise_website(lead.website)
    notes = []
    if domain is None:
        notes.append("no usable website submitted")
    if email_domain in FREE_MAIL_DOMAINS:
        notes.append("free-mail address")
        email_domain = None
    elif domain is not None and not _same_site(email_domain, domain):
        notes.append(f"email domain {email_domain} differs from website {domain}")

    contact = Contact(name=lead.name, email=lead.email, job_title=lead.job_title)
    company = Company(
        name=lead.company,
        website_url=website_url,
        domain=domain,
        email_domain=email_domain,
        declared_country=lead.country,
        declared_size=lead.company_size,
        declared_spend=lead.monthly_cloud_spend,
        declared_providers=lead.cloud_providers,
        message=lead.message,
        notes=notes,
    )
    return contact, company


def normalise_website(website: str) -> tuple[str | None, str | None]:
    """Return (url, bare domain) for a submitted website, or (None, None) if unusable."""
    text = website.strip()
    if not text:
        return None, None
    if "://" not in text:
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        return None, None
    if parts.scheme not in ("http", "https") or "." not in host:
        return None, None
    domain = host.removeprefix("www.")
    if domain in SOCIAL_HOSTS:
        return None, None
    return f"{parts.scheme}://{host}{parts.path}", domain


def _same_site(email_domain: str, website_domain: str) -> bool:
    return (
        email_domain == website_domain
        or email_domain.endswith(f".{website_domain}")
        or website_domain.endswith(f".{email_domain}")
    )
