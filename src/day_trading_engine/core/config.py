from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ProjectConfig(StrictModel):
    plan_version: str
    software_version: str
    timezone: str
    decision_time: str

    @model_validator(mode="after")
    def validate_timezone(self) -> ProjectConfig:
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
    core_candidate_count: int = Field(ge=0)
    boundary_candidate_count: int = Field(ge=0)
    diversity_candidate_count: int = Field(ge=0)
    final_candidate_min: int = Field(ge=1, le=5)
    final_candidate_max: int = Field(ge=1, le=5)
    primary_candidate_max: int = Field(ge=0)
    historical_bootstrap_months_min: int = Field(ge=1)
    historical_bootstrap_months_preferred: int = Field(ge=1)
    monthly_refinement_review: bool
    automatic_monthly_promotion: bool

    @model_validator(mode="after")
    def validate_counts(self) -> ResearchConfig:
        if self.daily_candidate_count != 30:
            raise ValueError("V1 research cohort must contain exactly 30 candidates")
        bucket_total = (
            self.core_candidate_count
            + self.boundary_candidate_count
            + self.diversity_candidate_count
        )
        if bucket_total != self.daily_candidate_count:
            raise ValueError("research cohort bucket counts must equal daily_candidate_count")
        if self.final_candidate_min > self.final_candidate_max:
            raise ValueError("final_candidate_min cannot exceed final_candidate_max")
        if self.final_candidate_max > self.daily_candidate_count:
            raise ValueError("final candidates cannot exceed research candidates")
        if self.primary_candidate_max > 1:
            raise ValueError("V1 allows at most one PRIMARY candidate")
        if self.historical_bootstrap_months_preferred < self.historical_bootstrap_months_min:
            raise ValueError("preferred historical window cannot be smaller than minimum")
        return self


class ResearchUniverseConfig(StrictModel):
    target: int = Field(default=200, ge=30)
    refresh: str = "monthly"
    ipo_seasoning_sessions: int = Field(default=20, ge=20, le=30)
    max_spread_pct: float = Field(default=0.02, gt=0, le=1)
    min_coverage_ratio: float = Field(default=0.90, ge=0, le=1)
    max_sector_fraction: float = Field(default=0.25, gt=0, le=1)
    selector_version: str = "universe-v1"
    benchmark_symbols: tuple[str, ...] = ("SPY", "QQQ")

    @field_validator("benchmark_symbols", mode="before")
    @classmethod
    def normalize_benchmarks(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(
                symbol.strip().upper() if isinstance(symbol, str) else symbol
                for symbol in value
            )
        return value

    @model_validator(mode="after")
    def validate_universe(self) -> ResearchUniverseConfig:
        if self.refresh != "monthly":
            raise ValueError("v3.2 research universe refresh must be monthly")
        if not self.selector_version.strip():
            raise ValueError("universe selector_version is required")
        if not self.benchmark_symbols or any(not symbol for symbol in self.benchmark_symbols):
            raise ValueError("benchmark_symbols must be non-empty")
        if len(self.benchmark_symbols) != len(set(self.benchmark_symbols)):
            raise ValueError("benchmark_symbols cannot contain duplicates")
        return self


class HistoryConfig(StrictModel):
    provider: str = "alpaca"
    minimum_months: int = Field(default=12, ge=1)
    preferred_months: int = Field(default=24, ge=1)
    interval: str = "1m"

    @model_validator(mode="after")
    def validate_history(self) -> HistoryConfig:
        if self.provider.lower() != "alpaca":
            raise ValueError("v3.2 US historical provider must be alpaca")
        if self.interval != "1m":
            raise ValueError("v3.2 historical bootstrap interval must be 1m")
        if self.preferred_months < self.minimum_months:
            raise ValueError("preferred historical window cannot be smaller than minimum")
        return self


class RankingConfig(StrictModel):
    technical: float = Field(default=0.50, ge=0, le=1)
    market: float = Field(default=0.20, ge=0, le=1)
    news: float = Field(default=0.20, ge=0, le=1)
    reddit: float = Field(default=0.05, ge=0, le=1)
    fundamentals: float = Field(default=0.05, ge=0, le=1)
    missing_optional_weight_to: str = "technical"
    minimum_final_score: float = Field(default=0.50, ge=0, le=1)
    normalization_version: str = "normalized-v2"

    @model_validator(mode="after")
    def validate_ranking(self) -> RankingConfig:
        total = self.technical + self.market + self.news + self.reddit + self.fundamentals
        if abs(total - 1.0) > 1e-9:
            raise ValueError("ranking weights must sum to 1")
        if self.missing_optional_weight_to != "technical":
            raise ValueError("missing optional context weight must move to technical")
        if not self.normalization_version.strip():
            raise ValueError("normalization_version is required")
        return self


class MarketDataConfig(StrictModel):
    provider: str
    watchlist: tuple[str, ...] = Field(min_length=1, max_length=30)
    quote_batch_size: int = Field(ge=1, le=100)
    max_latency_ms: int = Field(ge=0, le=60_000)

    @field_validator("watchlist", mode="before")
    @classmethod
    def normalize_watchlist(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(
                symbol.strip().upper() if isinstance(symbol, str) else symbol
                for symbol in value
            )
        return value

    @model_validator(mode="after")
    def validate_market_data(self) -> MarketDataConfig:
        if self.provider.lower() != "questrade":
            raise ValueError("V1 live market-data provider must be questrade")
        if any(not symbol for symbol in self.watchlist):
            raise ValueError("market-data watchlist cannot contain blank symbols")
        if len(self.watchlist) != len(set(self.watchlist)):
            raise ValueError("market-data watchlist cannot contain duplicates")
        return self


class RiskConfig(StrictModel):
    max_spread_pct: float = Field(gt=0)
    max_volatility: float = Field(gt=0)
    min_rvol: float = Field(gt=0)
    min_volume: int = Field(ge=0)


class StrategyConfig(StrictModel):
    family: str
    entry_buffer_pct: float = Field(ge=0)
    stop_buffer_pct: float = Field(ge=0)
    reward_to_risk: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_family(self) -> StrategyConfig:
        if self.family != "opening_range_vwap_continuation":
            raise ValueError("V1 strategy family must be opening_range_vwap_continuation")
        return self


class ExtendedGateThresholds(StrictModel):
    min_pre_active_minutes: int = Field(ge=0)
    min_pre_dollar_volume: float = Field(ge=0)
    max_pre_abs_return: float = Field(ge=0)
    max_pre_abs_gap: float = Field(ge=0)
    max_pre_range: float = Field(ge=0)
    max_pre_volatility: float = Field(ge=0)
    min_post_active_minutes: int = Field(ge=0)
    min_post_dollar_volume: float = Field(ge=0)
    max_post_abs_return: float = Field(ge=0)
    max_post_range: float = Field(ge=0)
    max_post_volatility: float = Field(ge=0)


class ExtendedGateArtifact(StrictModel):
    version: str
    approved: bool
    complete_sessions: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    deterministic_replay: bool
    holdout_consumed: bool
    forward_confirmed: bool
    no_expectancy_regression: bool
    no_drawdown_regression: bool
    no_hard_risk_regression: bool
    thresholds: ExtendedGateThresholds

    @property
    def activation_ready(self) -> bool:
        return (
            bool(self.version.strip())
            and self.approved
            and self.complete_sessions >= 15
            and self.coverage_ratio >= 0.90
            and all((
                self.deterministic_replay,
                self.holdout_consumed,
                self.forward_confirmed,
                self.no_expectancy_regression,
                self.no_drawdown_regression,
                self.no_hard_risk_regression,
            ))
        )


class ExtendedHoursConfig(StrictModel):
    historical_start: str = "04:00"
    historical_end: str = "20:00"
    technical_score_share: float = Field(default=0.20, ge=0, le=1)
    feature_version: str = "extended-v1"
    gate_mode: Literal["shadow", "active"] = "shadow"
    gate_artifact: ExtendedGateArtifact | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> ExtendedHoursConfig:
        if (self.historical_start, self.historical_end) != ("04:00", "20:00"):
            raise ValueError("v3.2 extended history must use 04:00-20:00 ET")
        if self.technical_score_share != 0.20:
            raise ValueError("v3.2 extended evidence must use 20% of the technical score")
        if not self.feature_version.strip():
            raise ValueError("extended feature_version is required")
        if self.gate_mode == "active" and (
            self.gate_artifact is None or not self.gate_artifact.activation_ready
        ):
            raise ValueError("extended gates require a manually approved validation artifact")
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
    risk: RiskConfig
    strategy: StrategyConfig
    runtime: RuntimeConfig
    research_universe: ResearchUniverseConfig = Field(default_factory=ResearchUniverseConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    extended_hours: ExtendedHoursConfig = Field(default_factory=ExtendedHoursConfig)

    @model_validator(mode="after")
    def enforce_v1_contract(self) -> AppConfig:
        v = self.validation
        r = self.research
        violations: list[str] = []
        if self.project.plan_version != "3.2":
            violations.append("V1 must use implementation plan 3.2")
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
        if (
            r.core_candidate_count,
            r.boundary_candidate_count,
            r.diversity_candidate_count,
        ) != (20, 5, 5):
            violations.append("V1 research cohort must use the frozen 20/5/5 policy")
        if r.final_candidate_min != 1 or r.final_candidate_max != 5:
            violations.append("V1 requires 1-5 user-facing finalists")
        if r.primary_candidate_max != 1:
            violations.append("V1 allows at most 1 PRIMARY")
        if self.research_universe.target != 200:
            violations.append("v3.2 active US research universe target must remain 200")
        if (self.history.minimum_months, self.history.preferred_months) != (12, 24):
            violations.append(
                "v3.2 history target must remain 12-month minimum / 24-month preferred"
            )
        if (
            self.ranking.technical,
            self.ranking.market,
            self.ranking.news,
            self.ranking.reddit,
            self.ranking.fundamentals,
        ) != (0.50, 0.20, 0.20, 0.05, 0.05):
            violations.append("v3.2 ranking weights must remain 50/20/20/5/5")
        if set(self.market_data.watchlist) & set(self.research_universe.benchmark_symbols):
            violations.append("benchmark symbols must remain separate from research candidates")
        if self.runtime.ui != "custom-local":
            violations.append("v3.2 runtime UI must remain custom-local")
        if self.runtime.ai_required_for_daily_run:
            violations.append("AI cannot be mandatory for daily V1 operation")
        if violations:
            raise ValueError("; ".join(violations))
        return self


def load_config(path: str | Path) -> AppConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppConfig.model_validate(data)
