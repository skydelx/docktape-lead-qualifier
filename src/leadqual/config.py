"""Typed settings loaded from config.toml and validated at start-up."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from leadqual.models import CloudProvider, SizeBand, SpendBand, Workload

DEFAULT_CONFIG_PATH = Path("config.toml")


class LlmSettings(BaseModel):
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    max_agent_steps: int
    max_web_searches: int


class Competitor(BaseModel):
    name: str
    domains: list[str] = []


class SanctionedPlace(BaseModel):
    place: str
    aliases: list[str] = []
    level: Literal["blocked", "review"]
    reason: str

    @property
    def names(self) -> list[str]:
        return [self.place, *self.aliases]


class ScoringSettings(BaseModel):
    size_points: dict[SizeBand, int]
    spend_points: dict[SpendBand, int]
    workload_points: dict[Workload, int]
    unknown_axis_points: int
    bonus_points: int
    max_bonus: int
    high_priority_min: int
    medium_priority_min: int
    preferred_providers: list[CloudProvider]
    decision_maker_keywords: list[str]


class Settings(BaseModel):
    tracker_path: Path
    llm: LlmSettings
    competitors: list[Competitor]
    sanctions: list[SanctionedPlace]
    scoring: ScoringSettings


def load_settings(path: Path = DEFAULT_CONFIG_PATH) -> Settings:
    with path.open("rb") as file:
        return Settings.model_validate(tomllib.load(file))
