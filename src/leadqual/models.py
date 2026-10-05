"""Data contracts shared by every pipeline step."""

import re
import unicodedata
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

LEGAL_SUFFIXES = frozenset(
    {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company"}
    | {"gmbh", "ag", "plc", "sa", "bv", "nv", "kft", "zrt", "pty", "srl", "sas", "oy", "ab"}
)


def normalise_name(name: str) -> str:
    """'Cloud Trim, Inc.', 'CloudTrim L.L.C.', 'CloudTrím' and 'CLOUDTRIM' become 'cloudtrim'."""
    decomposed = unicodedata.normalize("NFKD", name)
    # Accents are dropped, not the letters under them: 'Rába' becomes 'raba', not 'rba'.
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    text = unaccented.casefold().replace(".", "")
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return "".join(words)


class SizeBand(StrEnum):
    MICRO = "1-10"
    SMALL = "11-50"
    MID = "51-1000"
    LARGE = "1001-5000"
    ENTERPRISE = "5000+"
    UNKNOWN = "unknown"


class SpendBand(StrEnum):
    """Monthly cloud bill in USD."""

    UNDER_5K = "<5k"
    FROM_5K_TO_20K = "5k-20k"
    OVER_20K = ">20k"
    UNKNOWN = "unknown"


class CloudProvider(StrEnum):
    AWS = "aws"
    ORACLE = "oracle"
    AZURE = "azure"
    GCP = "gcp"
    OTHER = "other"


class Workload(StrEnum):
    """How much cloud infrastructure the company likely runs, inferred from research."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class Source(StrEnum):
    PAGE = "page"  # verbatim quote found on a page we fetched
    SEARCH = "search"  # web search result, URL kept
    SELF_REPORTED = "self-reported"  # typed into the form, not verified


class ResearchStatus(StrEnum):
    OK = "ok"
    NO_WEBSITE = "no-website"
    SEARCH_ONLY = "search-only"  # the site exists but refused us; findings come from web search
    UNREACHABLE = "unreachable"  # the site does not exist
    EMPTY_SITE = "empty-site"  # parked, for sale, placeholder
    FAILED = "failed"


class CompetitorVerdict(StrEnum):
    CONFIRMED_MATCH = "confirmed_match"
    POSSIBLE_MATCH = "possible_match"
    CLEAR = "clear"
    NOT_SCREENED = "not_screened"  # the screening agent failed; never treated as clear


class MatchType(StrEnum):
    EXACT_NAME = "exact_name"
    PARTIAL_NAME = "partial_name"
    SPELLING_VARIANT = "spelling_variant"
    RENAMED = "renamed"
    SUBSIDIARY_OR_BRAND = "subsidiary_or_brand"
    DOMAIN = "domain"
    SAME_BUSINESS = "same_business"
    NONE = "none"


class SanctionsVerdict(StrEnum):
    BLOCKED = "blocked"
    REVIEW = "review"
    CLEAR = "clear"
    UNKNOWN = "unknown"


class Route(StrEnum):
    """Whether sales may call. How soon is the fit score's job, not the route's."""

    DO_NOT_ENGAGE = "DO-NOT-ENGAGE"
    CHECK_FIRST = "CHECK-FIRST"  # one concrete question for a person before anyone calls
    SALES_READY = "SALES-READY"


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Lead(BaseModel):
    """A form submission as received. Only the first four fields exist on the current form."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    company: str = Field(min_length=1)
    website: str = ""
    job_title: str | None = None
    country: str | None = None
    company_size: SizeBand = SizeBand.UNKNOWN
    monthly_cloud_spend: SpendBand = SpendBand.UNKNOWN
    cloud_providers: list[CloudProvider] = []
    message: str | None = None

    @field_validator("website", "company_size", "monthly_cloud_spend", mode="before")
    @classmethod
    def _untouched_field_is_not_answered(cls, value: object, info: ValidationInfo) -> object:
        """A web form posts "" or null for a field left alone; that must not refuse the lead."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return cls.model_fields[info.field_name].default
        if isinstance(value, str) and info.field_name != "website":
            return value.strip().lower()  # ">20K " is the same answer as ">20k"
        return value

    @field_validator("cloud_providers", mode="before")
    @classmethod
    def _providers_as_a_form_sends_them(cls, value: object) -> object:
        """Nothing ticked arrives as "" or null; one tick may arrive as a bare string;
        'AWS' is the same answer as 'aws'; a provider we do not list is 'other'."""
        if value is None:
            return []
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list):
            return value
        known = {provider.value for provider in CloudProvider}
        names = [item.strip().lower() if isinstance(item, str) else item for item in items]
        return [name if name in known else "other" for name in names if name != ""]


class Contact(BaseModel):
    """Personal data: written to the tracker and the notification, never sent to the LLM."""

    name: str
    email: str
    job_title: str | None = None


class Company(BaseModel):
    """What the lead told us about the company. Safe to send to the LLM."""

    name: str
    website_url: str | None = None
    domain: str | None = None
    email_domain: str | None = None  # None for free-mail addresses
    declared_country: str | None = None
    declared_size: SizeBand = SizeBand.UNKNOWN
    declared_spend: SpendBand = SpendBand.UNKNOWN
    declared_providers: list[CloudProvider] = []
    message: str | None = None
    notes: list[str] = []


class Evidence(BaseModel):
    source: Source
    quote: str | None = None
    url: str | None = None


class Research(BaseModel):
    status: ResearchStatus
    final_domain: str | None = None
    what_they_do: str = ""
    summary: str = ""
    hq_country: str | None = None
    hq_city: str | None = None
    size: SizeBand = SizeBand.UNKNOWN
    workload: Workload = Workload.UNKNOWN
    cloud_providers: list[CloudProvider] = []
    other_names: list[str] = []  # former names, parent company, group or brand the site states
    sells_cloud_cost_optimization: bool = False
    stated_pain: bool = False
    evidence: dict[str, Evidence] = {}  # keyed by the field name it supports
    pages_fetched: list[str] = []
    other_links: list[str] = []  # links seen on the site but not read

    @property
    def brief(self) -> str:
        """The summary as the rep reads it, saying so when the website itself was never read."""
        if self.status is ResearchStatus.SEARCH_ONLY:
            return f"Website could not be read; from web search only. {self.summary}".strip()
        return self.summary


class Compliance(BaseModel):
    competitor: CompetitorVerdict
    matched_entry: str | None = None
    match_type: MatchType = MatchType.NONE
    sanctions: SanctionsVerdict
    hq_country: str | None = None
    reasoning: str
    evidence: list[str] = []
    safety_net: list[str] = []  # code rules that overrode the agent, if any
    overridden: list[str] = []  # which verdicts they changed: "competitor", "sanctions"

    @property
    def flag(self) -> str:
        """The one-line compliance flag shown in the tracker and the notification."""
        if self.competitor is CompetitorVerdict.CONFIRMED_MATCH:
            return f"FLAGGED: competitor ({self.matched_entry})"
        if self.sanctions is SanctionsVerdict.BLOCKED:
            return f"FLAGGED: sanctions ({self.hq_country})"
        if self.competitor is CompetitorVerdict.NOT_SCREENED:
            return "NOT SCREENED"
        if self.competitor is CompetitorVerdict.POSSIBLE_MATCH:
            return f"CHECK: possible competitor ({self.matched_entry or 'same service'})"
        if self.sanctions is SanctionsVerdict.REVIEW:
            return f"CHECK: sanctions ({self.hq_country})"
        if self.sanctions is SanctionsVerdict.UNKNOWN:
            return "CHECK: headquarters unknown"
        return "clear"


class Fit(BaseModel):
    size_points: int
    cloud_points: int
    bonus_points: int
    total: int
    priority: Priority
    size_basis: str  # e.g. "51-1000 (website)"
    cloud_basis: str
    size_known: bool  # False when the axis fell back to the neutral value
    cloud_known: bool
    bonuses: list[str] = []
    conflicts: list[str] = []  # form answers that research contradicts

    @property
    def data(self) -> str:
        known = self.size_known + self.cloud_known
        return ("none", "partial", "full")[known]


class Decision(BaseModel):
    route: Route
    reason: str


class Result(BaseModel):
    """Everything one pipeline run produced for one lead."""

    contact: Contact
    company: Company
    research: Research
    compliance: Compliance
    fit: Fit
    decision: Decision
    processed_at: datetime
