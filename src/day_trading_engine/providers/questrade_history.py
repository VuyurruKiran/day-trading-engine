from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .questrade import QuestradeClient, ResponseMeta

HISTORICAL_INTERVALS = frozenset(
    {
        "OneMinute",
        "TwoMinutes",
        "ThreeMinutes",
        "FourMinutes",
        "FiveMinutes",
        "TenMinutes",
        "FifteenMinutes",
        "TwentyMinutes",
        "HalfHour",
        "OneHour",
        "TwoHours",
        "FourHours",
        "OneDay",
        "OneWeek",
        "OneMonth",
        "OneYear",
    }
)


class HistoricalCandle(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    start: datetime
    end: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_ohlc(self) -> HistoricalCandle:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("candle timestamps must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("candle end must be after start")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle OHLC values are inconsistent")
        if self.low > self.high:
            raise ValueError("candle low cannot exceed high")
        return self


class HistoricalCandleBatch(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    candles: tuple[HistoricalCandle, ...]
    meta: ResponseMeta


class QuestradeHistoryClient(QuestradeClient):
    def get_candles(
        self,
        symbol_id: int,
        *,
        start: datetime,
        end: datetime,
        interval: str = "OneMinute",
    ) -> HistoricalCandleBatch:
        if symbol_id <= 0:
            raise ValueError("symbol_id must be positive")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("historical candle range must be timezone-aware")
        if end <= start:
            raise ValueError("historical candle end must be after start")
        if interval not in HISTORICAL_INTERVALS:
            raise ValueError(f"unsupported historical interval: {interval}")

        payload, meta = self._get_json(
            f"markets/candles/{symbol_id}",
            {
                "startTime": start.isoformat(),
                "endTime": end.isoformat(),
                "interval": interval,
            },
        )
        candles = tuple(
            HistoricalCandle.model_validate(item) for item in payload.get("candles", [])
        )
        if len(candles) > 2000:
            raise ValueError("Questrade candle response exceeded documented 2,000-row limit")
        return HistoricalCandleBatch(candles=candles, meta=meta)
