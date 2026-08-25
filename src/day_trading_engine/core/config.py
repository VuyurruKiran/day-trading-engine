from __future__ import annotations

import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectConfig(StrictModel):
    plan_version: str
    software_version: str
    timezone: str
    decision_time: str

    @model_validator(mode="after")
    def validate_timezone(self) -> ProjectConfig:
        """Validate the configured timezone and strict 24-hour decision time."""
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {self.timezone}") from exc
        if not _TIME_RE.fullmatch(self.decision_time):
            raise ValueError("decision_time must use strict HH:MM 24-hour format")
        return self


class ValidationConfig(StrictModel):
    starting_cash_usd: float = Field(gt=0)
    allow_capital_top_up: bool
    long_only: bool
    cash_only: bool
    leverage_allowed: bool
    max_active_positions: int = Field(ge=1)
    manual_execution_only: bool
    commissions_enabled: bool
    slippage_enabled: bool


class ResearchConfig(StrictModel):
    daily_candidate_count: int = Field(ge=1)
    final_candidate_min: int = Field(ge=0)
    final_candidate_max: int = Field(ge=1)
    primary_candidate_max: int = Field(ge=0)
    historical_bootstrap_months_min: int = Field(ge=1)
    historical_bootstrap_months_preferred: int = Field(ge=1)
    monthly_refinement_review: bool
    automatic_monthly_promotion: bool

    @model_validator(mode="after")
    def validate_counts(self) -> ResearchConfig:
        """Validate research cohort and historical-window relationships."""
        if self.final_candidate_min > self.final_candidate_max:
            raise ValueError("final_candidate_min cannot exceed final_candidate_max")
        if self.final_candidate_max > self.daily_candidate_count:
            raise ValueError("final candidates cannot exceed research candidates")
        if self.primary_candidate_max > 1:
            raise ValueError("V1 allows at most one PRIMARY candidate")
        if self.historical_bootstrap_months_preferred < self.historical_bootstrap_months_min:
            raise ValueError("preferred historical window cannot be smaller than minimum")
        return self


class MarketDataConfig(StrictModel):
    provider: str
    watchlist: tuple[str, ...] = Field(min_length=1, max_length=30)
    quote_batch_size: int = Field(ge=1, le=100)
    max_latency_ms: int = Field(ge=0, le=60_000)

    @model_validator(mode="after")
    def validate_market_data(self) -> MarketDataConfig:
        if self.provider.lower() != "questrade":
            raise ValueError("V1 market-data provider must be questrade")
        normalized = tuple(symbol.strip().upper() for symbol in self.watchlist)
        if any(not symbol for symbol in normalized):
            raise ValueError("market-data watchlist cannot contain blank symbols")
        if len(normalized) != len(set(normalized)):
            raise ValueError("market-data watchlist cannot contain duplicates")
        return self


class RuntimeConfig(StrictModel):
    ui: str
    metadata_store: str
    analytical_store: str
    analytics_engine: str
    ai_required_for_daily_run: bool


class AppConfig(StrictModel):
    project: ProjectConfig
    validation: ValidationConfig
    research: ResearchConfig
    market_data: MarketDataConfig
    runtime: RuntimeConfig

    @model_validator(mode="after")
    def enforce_v1_contract(self) -> AppConfig:
        """Reject configuration changes that violate the locked V1 contract."""
        v = self.validation
        r = self.research
        violations: list[str] = []
        if v.starting_cash_usd != 100.0:
            violations.append("starting_cash_usd must remain exactly 100.0 in V1")
        if v.allow_capital_top_up:
            violations.append("capital top-ups are forbidden in V1")
        if not (v.long_only and v.cash_only and v.manual_execution_only):
            violations.append("V1 must be long-only, cash-only, and manual-execution-only")
        if v.leverage_allowed:
            violations.append("leverage is forbidden in V1")
        if v.max_active_positions != 1:
            violations.append("V1 allows exactly one active position")
        if r.daily_candidate_count != 30:
            violations.append("V1 research cohort must contain exactly 30 candidates")
        if r.final_candidate_min != 2 or r.final_candidate_max != 5:
            violations.append("V1 requires 2-5 user-facing finalists")
        if r.primary_candidate_max != 1:
            violations.append("V1 allows at most 1 PRIMARY")
        if self.runtime.ai_required_for_daily_run:
            violations.append("AI cannot be mandatory for daily V1 operation")
        if violations:
            raise ValueError("; ".join(violations))
        return self


def load_config(path: str | Path) -> AppConfig:
    """Load and validate the application configuration from YAML."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppConfig.model_validate(data)
