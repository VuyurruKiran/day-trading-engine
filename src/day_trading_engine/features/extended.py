from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from statistics import fmean

import pandas as pd

from day_trading_engine.core.config import ExtendedGateThresholds

EXTENDED_FEATURE_VERSION = "extended-v1"


@dataclass(frozen=True)
class ExtendedPhaseMetrics:
    active_minutes: int
    dollar_volume: float
    return_pct: float
    gap_pct: float | None
    range_pct: float
    volatility: float
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    expected_minutes: int | None = None
    first_observed_at: str | None = None
    last_observed_at: str | None = None


@dataclass(frozen=True)
class ExtendedSessionFeatures:
    symbol: str
    premarket_session: str
    prior_postmarket_session: str
    premarket: ExtendedPhaseMetrics | None
    prior_postmarket: ExtendedPhaseMetrics | None
    premarket_unavailable_reason: str | None
    postmarket_unavailable_reason: str | None
    premarket_provider: str
    postmarket_provider: str
    premarket_feed: str
    postmarket_feed: str
    schedule_source: str
    postmarket_schedule_source: str = "canonical_us_equities_v1"
    feature_version: str = EXTENDED_FEATURE_VERSION

    def __post_init__(self) -> None:
        if date.fromisoformat(self.prior_postmarket_session) >= date.fromisoformat(
            self.premarket_session
        ):
            raise ValueError("post-market evidence must precede the decision session")
        if (self.premarket is None) != (self.premarket_unavailable_reason is not None):
            raise ValueError("pre-market availability and reason disagree")
        if self.premarket is not None and self.premarket.gap_pct is None:
            raise ValueError("pre-market gap is required")
        if (self.prior_postmarket is None) != (
            self.postmarket_unavailable_reason is not None
        ):
            raise ValueError("post-market availability and reason disagree")

    def evidence(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "premarket_session": self.premarket_session,
            "prior_postmarket_session": self.prior_postmarket_session,
            "premarket": None if self.premarket is None else asdict(self.premarket),
            "prior_postmarket": (
                None if self.prior_postmarket is None else asdict(self.prior_postmarket)
            ),
            "premarket_unavailable_reason": self.premarket_unavailable_reason,
            "postmarket_unavailable_reason": self.postmarket_unavailable_reason,
            "premarket_provider": self.premarket_provider,
            "postmarket_provider": self.postmarket_provider,
            "premarket_feed": self.premarket_feed,
            "postmarket_feed": self.postmarket_feed,
            "schedule_source": self.schedule_source,
            "premarket_schedule_source": self.schedule_source,
            "postmarket_schedule_source": self.postmarket_schedule_source,
            "feature_version": self.feature_version,
        }


def phase_metrics(
    frame: pd.DataFrame,
    *,
    previous_close: float,
    include_gap: bool,
    expected_minutes: int | None = None,
) -> ExtendedPhaseMetrics:
    required = {"start", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing extended candle columns: {', '.join(sorted(missing))}")
    if not isfinite(previous_close) or previous_close <= 0:
        raise ValueError("previous_close must be finite and positive")
    if frame.empty:
        raise ValueError("extended phase requires at least one candle")
    prepared = frame.copy()
    prepared["start"] = pd.to_datetime(prepared["start"], utc=True, errors="raise")
    prepared = prepared.sort_values("start", kind="stable")
    if prepared["start"].duplicated().any():
        raise ValueError("extended candles contain duplicate timestamps")
    numeric = prepared[["open", "high", "low", "close", "volume"]].astype(float)
    invalid_price = (numeric[["open", "high", "low", "close"]] <= 0).any().any()
    if not numeric.map(isfinite).all().all() or invalid_price:
        raise ValueError("extended candles contain invalid values")
    if (numeric["volume"] < 0).any():
        raise ValueError("extended volume must be non-negative")
    first = float(numeric.iloc[0]["open"])
    last = float(numeric.iloc[-1]["close"])
    returns = numeric["close"].pct_change().dropna()
    high = float(numeric["high"].max())
    low = float(numeric["low"].min())
    return ExtendedPhaseMetrics(
        active_minutes=len(numeric),
        dollar_volume=float((numeric["close"] * numeric["volume"]).sum()),
        return_pct=last / first - 1,
        gap_pct=last / previous_close - 1 if include_gap else None,
        range_pct=high / low - 1,
        volatility=float(returns.std(ddof=0)) if len(returns) else 0.0,
        high=high,
        low=low,
        volume=int(numeric["volume"].sum()),
        expected_minutes=expected_minutes,
        first_observed_at=prepared.iloc[0]["start"].isoformat(),
        last_observed_at=prepared.iloc[-1]["start"].isoformat(),
    )


def normalize_extended_scores(
    features: dict[str, ExtendedSessionFeatures],
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    symbols = sorted(features)

    def ranks(values: dict[str, float], *, higher_is_better: bool) -> dict[str, float]:
        unique = sorted(set(values.values()))
        if len(unique) == 1:
            return {symbol: 0.5 for symbol in values}
        denominator = len(unique) - 1
        positions = {value: index / denominator for index, value in enumerate(unique)}
        ranked = {symbol: positions[value] for symbol, value in values.items()}
        return (
            ranked
            if higher_is_better
            else {symbol: 1 - value for symbol, value in ranked.items()}
        )

    phase_scores: dict[str, dict[str, float]] = {symbol: {} for symbol in symbols}
    for phase_name, attribute in (("premarket", "premarket"), ("postmarket", "prior_postmarket")):
        available = {
            symbol: getattr(features[symbol], attribute)
            for symbol in symbols
            if getattr(features[symbol], attribute) is not None
        }
        if not available:
            for symbol in symbols:
                phase_scores[symbol][phase_name] = 0.5
            continue
        metric_scores: dict[str, list[float]] = {symbol: [] for symbol in available}
        metric_names = ["active_minutes", "dollar_volume", "return_pct", "range_pct", "volatility"]
        if phase_name == "premarket":
            metric_names.append("gap_pct")
        for metric in metric_names:
            values = {symbol: float(getattr(value, metric)) for symbol, value in available.items()}
            ranked = ranks(values, higher_is_better=metric not in {"range_pct", "volatility"})
            for symbol, value in ranked.items():
                metric_scores[symbol].append(value)
        for symbol in symbols:
            phase_scores[symbol][phase_name] = (
                fmean(metric_scores[symbol]) if symbol in metric_scores else 0.5
            )
    scores = {
        symbol: fmean((values["premarket"], values["postmarket"]))
        for symbol, values in phase_scores.items()
    }
    return scores, phase_scores


def extended_gate_reasons(
    features: ExtendedSessionFeatures, thresholds: ExtendedGateThresholds
) -> tuple[str, ...]:
    if features.premarket is None:
        return ("required pre-market evidence unavailable",)
    pre = features.premarket
    reasons: list[str] = []
    if pre.active_minutes < thresholds.min_pre_active_minutes:
        reasons.append("pre-market active minutes below limit")
    if pre.dollar_volume < thresholds.min_pre_dollar_volume:
        reasons.append("pre-market dollar volume below limit")
    if abs(pre.return_pct) > thresholds.max_pre_abs_return:
        reasons.append("pre-market absolute return above limit")
    if pre.gap_pct is not None and abs(pre.gap_pct) > thresholds.max_pre_abs_gap:
        reasons.append("pre-market absolute gap above limit")
    if pre.range_pct > thresholds.max_pre_range or pre.volatility > thresholds.max_pre_volatility:
        reasons.append("pre-market instability above limit")
    post = features.prior_postmarket
    if post is not None:
        if post.active_minutes < thresholds.min_post_active_minutes:
            reasons.append("prior post-market active minutes below limit")
        if post.dollar_volume < thresholds.min_post_dollar_volume:
            reasons.append("prior post-market dollar volume below limit")
        if abs(post.return_pct) > thresholds.max_post_abs_return:
            reasons.append("prior post-market absolute return above limit")
        if (
            post.range_pct > thresholds.max_post_range
            or post.volatility > thresholds.max_post_volatility
        ):
            reasons.append("prior post-market instability above limit")
    return tuple(reasons)
