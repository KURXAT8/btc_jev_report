"""Validated, versioned contracts shared by the reporting pipeline.

Times are aware UTC and prices remain Decimal (strings in JSON). Validation
does not establish source freshness, bar coverage or provider authenticity;
those policies belong to the source/quality/Jev adapters.
"""

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict,
    Field, PlainSerializer, SecretStr, field_validator, model_validator,
)


def valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("invalid timezone") from None
    return value


HORIZON_LIMITS = {1: Decimal(1), 3: Decimal(2), 7: Decimal(4)}
HORIZONS = tuple(HORIZON_LIMITS)


def strict_horizon(value: Any) -> int:
    if type(value) is not int or value not in HORIZONS:
        raise ValueError("horizon must be integer 1, 3 or 7")
    return value


UtcDateTime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(timezone.utc))]
ZoneName = Annotated[str, AfterValidator(valid_timezone)]
Price = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
NonnegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, strict=True)]
Horizon = Annotated[Literal[1, 3, 7], BeforeValidator(strict_horizon)]
Direction = Literal["bullish", "sideways", "bearish"]
Probability = Annotated[float, Field(ge=0, le=1, strict=True, allow_inf_nan=False)]
DIRECTIONS = frozenset(("bullish", "sideways", "bearish"))


def validated_probabilities(values: Mapping[str, float]) -> Mapping[str, float]:
    if set(values) != DIRECTIONS:
        raise ValueError("probabilities must contain exactly three market directions")
    if abs(math.fsum(values.values()) - 1) > 1e-4:
        raise ValueError("probabilities must sum to one within 1e-4")
    # Freeze a copy, not a view of a caller-owned mutable dictionary.
    return MappingProxyType(dict(values))


Probabilities = Annotated[
    Mapping[str, Probability], AfterValidator(validated_probabilities),
    PlainSerializer(lambda values: dict(values), return_type=dict[str, float]),
]


def validate_finite_tree(value: Any) -> Any:
    """Reject hidden NaN/Infinity in extensible feature/answer dictionaries."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite numeric value")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("nonfinite numeric value")
    if isinstance(value, Mapping):
        for nested in value.values():
            validate_finite_tree(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            validate_finite_tree(nested)
    return value


class DomainModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True,
        validate_default=True, ser_json_bytes="base64", val_json_bytes="base64",
    )
    schema_version: Literal[1] = 1

    @field_validator("*", mode="after")
    @classmethod
    def finite_values(cls, value: Any) -> Any:
        return validate_finite_tree(value)


class Settings(DomainModel):
    data_dir: Path
    timezone: ZoneName = "Asia/Taipei"
    jev_model: str = "jev-1.13.0"
    calibration_mode: Literal["auto", "shadow", "off"] = "auto"
    typesafe_api_key: SecretStr = SecretStr("")
    binance_api_key: SecretStr = SecretStr("")
    binance_api_secret: SecretStr = SecretStr("")
    gmail_address: SecretStr = SecretStr("")
    gmail_app_password: SecretStr = SecretStr("")
    recipient_email: SecretStr = SecretStr("")

    @field_validator("jev_model")
    @classmethod
    def pinned_model(cls, value: str) -> str:
        if not re.fullmatch(r"jev-\d+\.\d+\.\d+", value):
            raise ValueError("a numbered Jev release is required")
        return value


class Candle(DomainModel):
    open_at: UtcDateTime
    close_at: UtcDateTime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: NonnegativeDecimal

    @model_validator(mode="after")
    def coherent_bar(self) -> Self:
        if self.open_at > self.close_at:
            raise ValueError("candle closes before opening")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("inconsistent candle OHLC ordering")
        return self


class Article(DomainModel):
    id: str
    source: str
    url: str
    title: str
    summary: str
    content_hash: str
    published_at: UtcDateTime
    first_seen_at: UtcDateTime
    fetched_at: UtcDateTime
    truncated: bool

    @model_validator(mode="after")
    def version_order(self) -> Self:
        if self.first_seen_at > self.fetched_at:
            raise ValueError("first_seen_at must not follow fetched_at")
        return self


class SourceHealth(DomainModel):
    status: Literal["ok", "error"]
    last_success_at: UtcDateTime | None
    last_attempt_at: UtcDateTime
    reason: str | None
    item_count: Count

    @model_validator(mode="after")
    def success_order(self) -> Self:
        if self.status == "ok" and self.last_success_at is None:
            raise ValueError("successful source requires a success timestamp")
        if self.last_success_at is not None and self.last_success_at > self.last_attempt_at:
            raise ValueError("success must not follow latest completed attempt")
        return self


class QualityReport(DomainModel):
    status: Literal["complete", "degraded", "unavailable"]
    reasons: list[str]
    rules_version: str
    crosscheck_deviation_pct: NonnegativeDecimal | None
    source_ages_seconds: dict[str, Count | None]
    news_counts: dict[str, Count]


class CalendarEvent(DomainModel):
    id: str
    source: str
    url: str
    title: str
    content_hash: str
    scheduled_start: UtcDateTime
    scheduled_end: UtcDateTime
    known_at: UtcDateTime
    time_precision: Literal["exact", "date", "date_range"]
    source_timezone: ZoneName

    @model_validator(mode="after")
    def precision_window(self) -> Self:
        if self.time_precision == "exact":
            if self.scheduled_start != self.scheduled_end:
                raise ValueError("exact event must be a single point")
        elif self.scheduled_start >= self.scheduled_end:
            raise ValueError("date event requires a nonempty half-open interval")
        return self


class ContextSummary(DomainModel):
    percentiles: dict[str, dict[str, Any]]
    trend: Literal["up", "down", "mixed", "unknown"]
    volatility: Literal["low", "medium", "high", "unknown"]
    rules_version: str
    regime: Literal["low_volatility", "trend_up", "trend_down", "high_volatility", "mixed", "unknown"] = "unknown"


class Snapshot(DomainModel):
    as_of: UtcDateTime
    fetched_at: UtcDateTime
    reference_price: Price
    daily: list[Candle]
    four_hour: list[Candle]
    hourly: list[Candle]
    funding: list[dict[str, Any]]
    open_interest: list[dict[str, Any]]
    articles: list[Article]
    events: list[CalendarEvent]
    calendar_health: dict[str, SourceHealth]
    context: ContextSummary
    source_health: dict[str, SourceHealth]
    quality: QualityReport

    @model_validator(mode="after")
    def acquisition_order(self) -> Self:
        if self.fetched_at < self.as_of:
            raise ValueError("snapshot fetched_at must not precede as_of")
        return self


class Distribution(DomainModel):
    probabilities: Probabilities
    choice: Direction
    confidence: Probability

    @model_validator(mode="after")
    def matching_winner(self) -> Self:
        if self.probabilities[self.choice] != max(self.probabilities.values()):
            raise ValueError("choice must be a maximum probability option")
        return self


class Prediction(DomainModel):
    id: str
    as_of: UtcDateTime
    created_at: UtcDateTime
    due_at: UtcDateTime
    horizon_days: Horizon
    reference_price: Price
    distribution: Distribution
    model: str
    rules_version: str
    label_version: str
    config_hash: str
    snapshot_id: str

    @model_validator(mode="after")
    def exact_horizon(self) -> Self:
        if self.due_at != self.as_of + timedelta(days=self.horizon_days):
            raise ValueError("due_at must equal as_of plus the exact horizon")
        if self.created_at < self.as_of:
            raise ValueError("prediction cannot precede its input cutoff")
        return self


class BaselineForecast(DomainModel):
    id: str
    name: str
    version: str
    label_version: str
    as_of: UtcDateTime
    due_at: UtcDateTime
    horizon_days: Horizon
    reference_price: Price
    probabilities: Probabilities | None
    choice: Direction | None
    history_count: Count
    input_ids: list[str]
    status: Literal["ready", "uniform_fallback", "unavailable"]

    @model_validator(mode="after")
    def consistent_forecast(self) -> Self:
        if self.due_at != self.as_of + timedelta(days=self.horizon_days):
            raise ValueError("baseline due_at does not match horizon")
        if self.status == "unavailable":
            if self.probabilities is not None or self.choice is not None:
                raise ValueError("unavailable baseline has no probabilities or choice")
        elif self.probabilities is None:
            raise ValueError("available baseline requires probabilities")
        if self.status == "uniform_fallback" and self.probabilities is not None and len(set(self.probabilities.values())) != 1:
            raise ValueError("uniform fallback requires equal probabilities")
        if self.status == "uniform_fallback" and self.choice is not None:
            raise ValueError("uniform fallback has no direction")
        if self.choice is not None and self.probabilities is not None:
            if self.probabilities[self.choice] != max(self.probabilities.values()):
                raise ValueError("baseline choice is not a maximum")
        return self


class Settlement(DomainModel):
    prediction_id: str
    target_price: Price
    return_pct: Decimal
    actual: Direction
    settled_at: UtcDateTime
    result_version: Annotated[int, Field(gt=0, strict=True)]


class RenderedMail(DomainModel):
    subject: str
    text: str
    html: str


class DeliveryResult(DomainModel):
    status: Literal["sent", "retryable", "failed", "unknown"]
    reason: str


class WeeklyReport(DomainModel):
    week_start: UtcDateTime
    week_end: UtcDateTime
    cutoff: UtcDateTime
    groups: list[dict[str, Any]]
    corrections: list[dict[str, Any]]
    snapshot_id: str

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        if self.week_start >= self.week_end or self.week_end > self.cutoff:
            raise ValueError("weekly window must end by cutoff")
        return self


class StageResult(DomainModel):
    day: date
    stage: Literal["news-1", "news-2", "trend", "trend-shadow-3d", "trend-shadow-7d",
                   "market_regime", "event_risk", "adversarial_review",
                   "forecast-v2:news-1", "forecast-v2:news-2", "forecast-v2:trend"]
    request_hash: str
    model: str
    completed_at: UtcDateTime
    answers: dict[str, Any]
    usage: dict[str, Count]


class Job(DomainModel):
    id: str
    day: date
    state: Literal["preparing", "ready", "sending", "retryable", "sent", "failed", "unknown", "missed"]
    lease_owner: str | None
    lease_until: UtcDateTime | None
    used_slots: list[Annotated[int, Field(strict=True)]]
    mime_bytes: bytes | None
    message_id: str
    created_at: UtcDateTime
    payload_hash: str | None
    weekly_snapshot_id: str | None

    @model_validator(mode="after")
    def coherent_lease_and_slots(self) -> Self:
        if len(self.used_slots) != len(set(self.used_slots)) or not set(self.used_slots) <= {480, 485, 495, 510}:
            raise ValueError("used_slots must contain unique scheduled delivery slots")
        if (self.lease_owner is None) != (self.lease_until is None):
            raise ValueError("lease owner and expiry must appear together")
        if self.state == "sending" and not self.lease_owner:
            raise ValueError("sending requires a lease")
        return self
