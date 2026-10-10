from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from tests.factories import prediction, settlement


UTC = timezone.utc
NOW = datetime(2026, 9, 20, 23, 30, tzinfo=UTC)


def candle_values(**overrides):
    values = dict(open_at=NOW - timedelta(minutes=1), close_at=NOW - timedelta(milliseconds=1),
                  open="100", high="102", low="99", close="101", volume="2")
    values.update(overrides)
    return values


def test_prediction_normalizes_to_utc_and_roundtrips_decimal_json():
    from btc_jev.domain import Prediction
    result = prediction()
    assert result.as_of == NOW
    assert result.as_of.tzinfo == UTC
    assert result.due_at == datetime(2026, 9, 23, 23, 30, tzinfo=UTC)
    assert result.reference_price == Decimal("100")
    data = json.loads(result.model_dump_json())
    assert data["reference_price"] == "100"
    assert data["schema_version"] == 1
    assert Prediction.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("field", ["as_of", "created_at", "due_at"])
def test_prediction_rejects_naive_time(field):
    with pytest.raises(ValueError):
        prediction(**{field: datetime(2026, 9, 21)})


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "-Infinity"])
def test_prediction_rejects_bad_reference_prices(value):
    with pytest.raises(ValueError):
        prediction(reference_price=value)


@pytest.mark.parametrize("value", [0, 2, 4, 72, True, 1.0, "1", 3.0, "3"])
def test_prediction_horizon_requires_supported_integer(value):
    # Pass due_at explicitly so the fixture does not calculate invalid dates.
    with pytest.raises(ValueError):
        prediction(horizon_days=value, due_at=NOW + timedelta(days=3))


@pytest.mark.parametrize("horizon", [1, 3, 7])
def test_prediction_requires_exact_due_time(horizon):
    valid = prediction(horizon_days=horizon)
    assert valid.due_at - valid.as_of == timedelta(days=horizon)
    with pytest.raises(ValueError):
        prediction(horizon_days=horizon, due_at=valid.due_at + timedelta(milliseconds=1))


def test_prediction_cannot_be_created_before_its_cutoff():
    with pytest.raises(ValueError):
        prediction(created_at=NOW - timedelta(seconds=1))


@pytest.mark.parametrize("changes", [
    {"probabilities": {"bullish": .7, "sideways": .4, "bearish": .2}},
    {"probabilities": {"bullish": .8, "sideways": .2}},
    {"probabilities": {"bullish": .7, "sideways": .2, "bearish": .1, "other": 0}},
    {"probabilities": {"bullish": float("nan"), "sideways": .2, "bearish": .1}},
    {"probabilities": {"bullish": float("inf"), "sideways": .2, "bearish": .1}},
    {"probabilities": {"bullish": 1.1, "sideways": 0, "bearish": -.1}},
    {"probabilities": {"bullish": True, "sideways": 0, "bearish": 0}},
    {"choice": "bearish"}, {"choice": "unknown"},
    {"confidence": -.01}, {"confidence": 1.01}, {"confidence": float("nan")},
    {"confidence": True},
])
def test_distribution_rejects_malformed_or_mismatched_answers(changes):
    from btc_jev.domain import Distribution
    values = dict(probabilities={"bullish": .7, "sideways": .2, "bearish": .1},
                  choice="bullish", confidence=.7)
    values.update(changes)
    with pytest.raises(ValueError):
        Distribution(**values)


@pytest.mark.parametrize("choice", ["bullish", "sideways"])
def test_distribution_preserves_provider_choice_on_tie(choice):
    from btc_jev.domain import Distribution
    result = Distribution(probabilities={"bullish": .5, "sideways": .5, "bearish": 0},
                          choice=choice, confidence=0)
    assert result.choice == choice


def test_probability_tolerance_does_not_renormalize():
    from btc_jev.domain import Distribution
    values = {"bullish": .7, "sideways": .2, "bearish": .10009}
    result = Distribution(probabilities=values, choice="bullish", confidence=.8)
    assert dict(result.probabilities) == values


def test_prediction_and_nested_probabilities_are_immutable():
    result = prediction()
    original = result.model_dump_json()
    with pytest.raises((ValueError, TypeError)):
        result.reference_price = Decimal(9)
    with pytest.raises((ValueError, TypeError)):
        result.distribution.choice = "bearish"
    with pytest.raises((ValueError, TypeError)):
        result.distribution.probabilities["bullish"] = .1
    detached = result.model_dump()
    detached["distribution"]["probabilities"]["bullish"] = .1
    assert result.model_dump_json() == original


def test_input_mutation_does_not_change_distribution():
    from btc_jev.domain import Distribution
    values = {"bullish": .7, "sideways": .2, "bearish": .1}
    result = Distribution(probabilities=values, choice="bullish", confidence=.7)
    values["bullish"] = 0
    assert result.probabilities["bullish"] == .7


@pytest.mark.parametrize("changes", [
    {"open": "0"}, {"close": "NaN"}, {"high": "Infinity"}, {"volume": "-1"},
    {"volume": "NaN"}, {"high": "100"}, {"low": "102"},
    {"close_at": NOW - timedelta(minutes=2)}, {"open_at": datetime(2026, 9, 21)},
])
def test_candle_rejects_invalid_numbers_order_or_time(changes):
    from btc_jev.domain import Candle
    with pytest.raises(ValueError):
        Candle(**candle_values(**changes))


def test_candle_preserves_exchange_milliseconds_and_zero_volume():
    from btc_jev.domain import Candle
    result = Candle(**candle_values(volume="0"))
    assert result.close_at == NOW - timedelta(milliseconds=1)
    assert json.loads(result.model_dump_json())["volume"] == "0"


def test_source_health_can_keep_fresh_success_after_a_failed_attempt():
    from btc_jev.domain import SourceHealth
    value = SourceHealth(status="error", last_success_at=NOW - timedelta(minutes=5),
                         last_attempt_at=NOW, reason="timeout", item_count=0)
    assert value.last_success_at == NOW - timedelta(minutes=5)
    assert value.item_count == 0


@pytest.mark.parametrize("changes", [
    {"status": "maybe"}, {"item_count": -1},
    {"last_success_at": NOW + timedelta(seconds=1)},
    {"last_attempt_at": datetime(2026, 9, 21)},
    {"status": "ok", "last_success_at": None},
])
def test_source_health_rejects_impossible_metadata(changes):
    from btc_jev.domain import SourceHealth
    values = dict(status="ok", last_success_at=NOW, last_attempt_at=NOW, reason=None, item_count=0)
    values.update(changes)
    with pytest.raises(ValueError):
        SourceHealth(**values)


def test_article_revision_metadata_is_aware_and_ordered():
    from btc_jev.domain import Article
    values = dict(id="a", source="coindesk", url="https://example.org/a", title="Synthetic",
                  summary="", content_hash="hash", published_at=NOW - timedelta(hours=1),
                  first_seen_at=NOW, fetched_at=NOW, truncated=False)
    assert Article(**values).published_at.tzinfo == UTC
    with pytest.raises(ValueError):
        Article(**(values | {"first_seen_at": NOW + timedelta(seconds=1)}))


def test_calendar_event_precision_does_not_invent_an_exact_time():
    from btc_jev.domain import CalendarEvent
    values = dict(id="fomc", source="fed", url="https://example.org", title="Synthetic",
                  content_hash="hash", scheduled_start=NOW, scheduled_end=NOW,
                  known_at=NOW - timedelta(days=1), time_precision="exact",
                  source_timezone="America/New_York")
    assert CalendarEvent(**values).scheduled_start == NOW
    with pytest.raises(ValueError):
        CalendarEvent(**(values | {"time_precision": "date"}))
    with pytest.raises(ValueError):
        CalendarEvent(**(values | {"scheduled_end": NOW + timedelta(hours=1)}))
    with pytest.raises(ValueError):
        CalendarEvent(**(values | {"source_timezone": "invalid/zone"}))


def test_quality_missing_is_not_zero_and_invalid_counts_rejected():
    from btc_jev.domain import QualityReport
    values = dict(status="degraded", reasons=["missing_okx"], rules_version="v1",
                  crosscheck_deviation_pct=None, source_ages_seconds={"okx": None}, news_counts={"kept": 0})
    result = QualityReport(**values)
    assert result.crosscheck_deviation_pct is None
    assert result.source_ages_seconds["okx"] is None
    for override in [{"crosscheck_deviation_pct": "-1"}, {"news_counts": {"kept": -1}},
                     {"source_ages_seconds": {"okx": -1}}]:
        with pytest.raises(ValueError):
            QualityReport(**(values | override))


def test_context_rejects_nonfinite_nested_json():
    from btc_jev.domain import ContextSummary
    with pytest.raises(ValueError):
        ContextSummary(percentiles={"volume": {"value": float("nan")}},
                       trend="unknown", volatility="unknown", rules_version="v1")


def test_snapshot_roundtrip_includes_all_shared_contracts():
    from btc_jev.domain import Snapshot
    values = dict(as_of=NOW, fetched_at=NOW + timedelta(minutes=1), reference_price="100",
                  daily=[candle_values()], four_hour=[], hourly=[], funding=[], open_interest=[],
                  articles=[], events=[], calendar_health={}, source_health={},
                  context=dict(percentiles={}, trend="unknown", volatility="unknown", rules_version="v1"),
                  quality=dict(status="unavailable", reasons=["insufficient_bars"], rules_version="v1",
                               crosscheck_deviation_pct=None, source_ages_seconds={}, news_counts={}))
    result = Snapshot(**values)
    assert Snapshot.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValueError):
        Snapshot(**(values | {"reference_price": "0"}))
    with pytest.raises(ValueError):
        Snapshot(**(values | {"fetched_at": NOW - timedelta(seconds=1)}))


def baseline_values(**overrides):
    values = dict(id="b", name="uniform", version="v1", label_version="labels-v1",
                  as_of=NOW, due_at=NOW + timedelta(days=3), horizon_days=3, reference_price="100",
                  probabilities=dict.fromkeys(["bullish", "sideways", "bearish"], 1/3),
                  choice=None, history_count=0, input_ids=[], status="ready")
    return values | overrides


def test_uniform_baseline_has_no_forced_direction_and_roundtrips():
    from btc_jev.domain import BaselineForecast
    result = BaselineForecast(**baseline_values())
    assert result.choice is None
    assert BaselineForecast.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("changes", [
    {"probabilities": None}, {"status": "unavailable"},
    {"status": "uniform_fallback", "choice": "bullish"}, {"history_count": -1},
    {"probabilities": {"bullish": .6, "sideways": .4, "bearish": 0}, "choice": "bearish"},
    {"due_at": NOW},
])
def test_baseline_rejects_inconsistent_probabilities_status_or_horizon(changes):
    from btc_jev.domain import BaselineForecast
    with pytest.raises(ValueError):
        BaselineForecast(**baseline_values(**changes))


def test_unavailable_baseline_keeps_missing_values():
    from btc_jev.domain import BaselineForecast
    result = BaselineForecast(**baseline_values(status="unavailable", probabilities=None))
    assert result.probabilities is None and result.choice is None


def test_stage_result_validates_stage_and_usage_with_schema_version():
    from btc_jev.domain import StageResult
    values = dict(day=date(2026, 9, 21), stage="trend", request_hash="hash", model="jev-1.13.0",
                  completed_at=NOW, answers={"score": .7}, usage={"tokens": 10})
    result = StageResult(**values)
    assert StageResult.model_validate_json(result.model_dump_json()) == result
    for override in [{"stage": "anything"}, {"usage": {"tokens": -1}},
                     {"usage": {"tokens": True}}, {"answers": {"score": float("inf")}}]:
        with pytest.raises(ValueError):
            StageResult(**(values | override))


def job_values(**overrides):
    return dict(id="job", day=date(2026, 9, 21), state="ready", lease_owner=None, lease_until=None,
                used_slots=[480], mime_bytes=b"Subject: BTC\r\n\r\n\xff\x00", message_id="synthetic-id",
                created_at=NOW, payload_hash="hash", weekly_snapshot_id=None) | overrides


def test_job_mime_bytes_roundtrip_losslessly():
    from btc_jev.domain import Job
    result = Job(**job_values())
    restored = Job.model_validate_json(result.model_dump_json())
    assert restored.mime_bytes == b"Subject: BTC\r\n\r\n\xff\x00"


@pytest.mark.parametrize("changes", [
    {"state": "made-up"}, {"used_slots": [481]}, {"used_slots": [480, 480]},
    {"lease_owner": "owner"}, {"state": "sending"},
])
def test_job_rejects_invalid_state_slot_or_lease(changes):
    from btc_jev.domain import Job
    with pytest.raises(ValueError):
        Job(**job_values(**changes))


@pytest.mark.parametrize("horizon,price,label", [
    (1, "101", "sideways"), (1, "99", "sideways"), (1, "101.0001", "bullish"),
    (1, "98.9999", "bearish"),
    (3, "102", "sideways"), (3, "98", "sideways"), (3, "102.0001", "bullish"),
    (3, "97.9999", "bearish"), (7, "104", "sideways"), (7, "96", "sideways"),
    (7, "104.0001", "bullish"), (7, "95.9999", "bearish"),
])
def test_factories_follow_thresholds_independently(horizon, price, label):
    forecast = prediction(horizon_days=horizon)
    result = settlement(forecast, target_price=price)
    assert result.actual == label
    assert result.return_pct == Decimal(price) - 100
    assert result.prediction_id == forecast.id
    assert result.settled_at == forecast.due_at


def test_settlement_rejects_nonfinite_price_and_invalid_label():
    from btc_jev.domain import Settlement
    values = dict(prediction_id="p", target_price="100", return_pct="0", actual="sideways",
                  settled_at=NOW, result_version=1)
    for override in [{"target_price": "0"}, {"return_pct": "NaN"}, {"actual": "unclear"},
                     {"result_version": 0}]:
        with pytest.raises(ValueError):
            Settlement(**(values | override))


def test_delivery_and_weekly_contracts_reject_unknown_status_and_reversed_window():
    from btc_jev.domain import DeliveryResult, RenderedMail, WeeklyReport
    assert RenderedMail(subject="日報", text="狀態", html="<p>狀態</p>").text == "狀態"
    with pytest.raises(ValueError):
        DeliveryResult(status="probably-sent", reason="uncertain")
    values = dict(week_start=NOW - timedelta(days=7), week_end=NOW, cutoff=NOW,
                  groups=[], corrections=[], snapshot_id="weekly")
    assert WeeklyReport(**values).groups == []
    with pytest.raises(ValueError):
        WeeklyReport(**(values | {"week_start": NOW + timedelta(days=1)}))


def test_unknown_schema_and_extra_fields_are_rejected():
    with pytest.raises(ValueError):
        prediction(schema_version=2)
    with pytest.raises(ValueError):
        prediction(gemini_api_key="forbidden")
