"""Step 4: the fit score and the routing decision.

Pure code, no LLM: every number comes from config.toml, so a sales lead can read
and change how leads are ranked without touching a prompt.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from leadqual.config import ScoringSettings
from leadqual.models import (
    Company,
    CompetitorVerdict,
    Compliance,
    Contact,
    Decision,
    Fit,
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

SOURCE_LABELS = {
    Source.PAGE: "website",
    Source.SEARCH: "web search",
    Source.SELF_REPORTED: "self-reported, unverified",
}
SIZE_ORDER = [band for band in SizeBand if band is not SizeBand.UNKNOWN]
# Bands this far apart are a contradiction, not a rounding difference.
SIZE_CONFLICT_DISTANCE = 2


def score(company: Company, contact: Contact, research: Research, settings: ScoringSettings) -> Fit:
    size_points, size_basis, size_known = _size_axis(company, research, settings)
    cloud_points, cloud_basis, cloud_known = _cloud_axis(company, research, settings)
    bonuses = _bonuses(company, contact, research, settings)
    bonus_points = min(len(bonuses) * settings.bonus_points, settings.max_bonus)
    total = min(size_points + cloud_points + bonus_points, 100)
    return Fit(
        size_points=size_points,
        cloud_points=cloud_points,
        bonus_points=bonus_points,
        total=total,
        priority=_priority(total, settings),
        size_basis=size_basis,
        cloud_basis=cloud_basis,
        size_known=size_known,
        cloud_known=cloud_known,
        bonuses=bonuses,
        conflicts=_conflicts(company, research),
    )


def _priority(total: int, settings: ScoringSettings) -> Priority:
    if total >= settings.high_priority_min:
        return Priority.HIGH
    if total >= settings.medium_priority_min:
        return Priority.MEDIUM
    return Priority.LOW


def _size_axis(
    company: Company, research: Research, settings: ScoringSettings
) -> tuple[int, str, bool]:
    """Researched size wins over the form; an unknown size is neutral, not small."""
    if research.size is not SizeBand.UNKNOWN:
        basis = _basis(research.size, research.evidence["size"].source)
        return settings.size_points[research.size], basis, True
    if company.declared_size is not SizeBand.UNKNOWN:
        band = company.declared_size
        return settings.size_points[band], _basis(band, Source.SELF_REPORTED), True
    return settings.unknown_axis_points, "unknown", False


def _cloud_axis(
    company: Company, research: Research, settings: ScoringSettings
) -> tuple[int, str, bool]:
    """Only the lead knows its bill, so a declared bill wins over an inferred workload."""
    if company.declared_spend is not SpendBand.UNKNOWN:
        band = company.declared_spend
        return settings.spend_points[band], _basis(f"{band}/month", Source.SELF_REPORTED), True
    if research.workload is not Workload.UNKNOWN:
        source = research.evidence["workload"].source
        basis = _basis(f"{research.workload} workload, inferred", source)
        return settings.workload_points[research.workload], basis, True
    return settings.unknown_axis_points, "unknown", False


def _basis(value: str, source: Source) -> str:
    return f"{value} ({SOURCE_LABELS[source]})"


def _bonuses(
    company: Company, contact: Contact, research: Research, settings: ScoringSettings
) -> list[str]:
    bonuses = []
    providers = {*company.declared_providers, *research.cloud_providers}
    preferred = sorted(providers.intersection(settings.preferred_providers))
    if preferred:
        bonuses.append(f"runs on {', '.join(preferred)}")
    if _is_decision_maker(contact.job_title, settings.decision_maker_keywords):
        bonuses.append(f"decision maker ({contact.job_title})")
    if research.stated_pain:
        bonuses.append("stated a cloud cost problem")
    return bonuses


def _is_decision_maker(job_title: str | None, keywords: list[str]) -> bool:
    if not job_title:
        return False
    return any(re.search(rf"\b{re.escape(word)}\b", job_title, re.IGNORECASE) for word in keywords)


def _conflicts(company: Company, research: Research) -> list[str]:
    conflicts = []
    declared, found = company.declared_size, research.size
    if SizeBand.UNKNOWN not in (declared, found):
        distance = abs(SIZE_ORDER.index(declared) - SIZE_ORDER.index(found))
        if distance >= SIZE_CONFLICT_DISTANCE:
            conflicts.append(f"form says {declared} employees, research found {found}")
    if company.declared_spend is SpendBand.OVER_20K and research.workload is Workload.LOW:
        conflicts.append("form says a >20k monthly bill, research found a low cloud workload")
    return conflicts


@dataclass(frozen=True)
class Facts:
    compliance: Compliance
    research: Research
    fit: Fit


@dataclass(frozen=True)
class Rule:
    route: Route
    applies: Callable[[Facts], bool]
    reason: Callable[[Facts], str]


# Read top to bottom: the first rule that applies decides. The route only answers
# "may sales call?": blocked, one question for a person first, or yes. A lead becomes
# sales-ready after every reason to stop has been ruled out; the fit score then orders
# the call list, so a mediocre score never sends a lead to a person for a decision.
RULES: tuple[Rule, ...] = (
    Rule(
        Route.DO_NOT_ENGAGE,
        lambda f: f.compliance.competitor is CompetitorVerdict.CONFIRMED_MATCH,
        lambda f: f"Competitor: {f.compliance.matched_entry}",
    ),
    Rule(
        Route.DO_NOT_ENGAGE,
        lambda f: f.compliance.sanctions is SanctionsVerdict.BLOCKED,
        lambda f: f"Headquartered in a sanctioned place: {f.compliance.hq_country}",
    ),
    Rule(
        Route.CHECK_FIRST,
        lambda f: f.compliance.competitor is CompetitorVerdict.NOT_SCREENED,
        lambda f: "Compliance screening did not complete",
    ),
    Rule(
        Route.CHECK_FIRST,
        lambda f: f.compliance.competitor is CompetitorVerdict.POSSIBLE_MATCH,
        lambda f: f"Possible competitor: {f.compliance.matched_entry or 'sells the same service'}",
    ),
    Rule(
        Route.CHECK_FIRST,
        lambda f: f.compliance.sanctions is SanctionsVerdict.REVIEW,
        lambda f: f"Headquarters needs a sanctions check: {f.compliance.hq_country}",
    ),
    Rule(
        Route.CHECK_FIRST,
        lambda f: f.research.status not in (ResearchStatus.OK, ResearchStatus.SEARCH_ONLY),
        lambda f: f"Could not research the company ({f.research.status})",
    ),
    Rule(
        Route.CHECK_FIRST,
        lambda f: f.compliance.sanctions is SanctionsVerdict.UNKNOWN,
        lambda f: "Headquarters country not found, so sanctions could not be ruled out",
    ),
    Rule(
        Route.CHECK_FIRST,
        lambda f: bool(f.fit.conflicts),
        lambda f: f"Form contradicts research: {'; '.join(f.fit.conflicts)}",
    ),
    Rule(
        Route.SALES_READY,
        lambda f: True,
        lambda f: f"Compliance clear; {f.fit.priority} priority (fit {f.fit.total})",
    ),
)


def decide(compliance: Compliance, research: Research, fit: Fit) -> Decision:
    facts = Facts(compliance, research, fit)
    rule = next(rule for rule in RULES if rule.applies(facts))
    return Decision(route=rule.route, reason=rule.reason(facts))
