"""Data contracts shared by every pipeline step."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
    UNREACHABLE = "unreachable"
    EMPTY_SITE = "empty-site"  # parked, for sale, placeholder
    FAILED = "failed"


class CompetitorVerdict(StrEnum):
    CONFIRMED_MATCH = "confirmed_match"
    POSSIBLE_MATCH = "possible_match"
    CLEAR = "clear"
    NOT_SCREENED = "not_screened"  # the screening agent failed; never treated as clear


class MatchType(StrEnum):
    EXACT_NAME = "exact_name"
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
    DO_NOT_ENGAGE = "DO-NOT-ENGAGE"
    REVIEW = "REVIEW"
    SALES_READY = "SALES-READY"
    LOW_PRIORITY = "LOW-PRIORITY"


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
    sells_cloud_cost_optimization: bool = False
    stated_pain: bool = False
    evidence: dict[str, Evidence] = {}  # keyed by the field name it supports
    pages_fetched: list[str] = []


class Compliance(BaseModel):
    competitor: CompetitorVerdict
    matched_entry: str | None = None
    match_type: MatchType = MatchType.NONE
    sanctions: SanctionsVerdict
    hq_country: str | None = None
    reasoning: str
    evidence: list[str] = []
    safety_net: list[str] = []  # code rules that overrode the agent, if any


class Fit(BaseModel):
    size_points: int
    cloud_points: int
    bonus_points: int
    total: int
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
