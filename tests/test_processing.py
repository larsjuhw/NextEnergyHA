"""Unit tests for the pure response-shaping helpers."""
from datetime import datetime, timezone

from processing import (
    _process_forecast,
    _process_quarterly,
    _safe_float,
    _safe_int,
)

NOW = datetime(2026, 6, 24, 10, 30, tzinfo=timezone.utc)


def _quarterly(current_field="9.99", hours=range(24)):
    """Build a quarterly payload whose curve value for hour H is H/10."""
    points = [{"Label": str(h), "Value": str(h / 10)} for h in hours]
    return {
        "DataPoints": {"List": points},
        "CurrentElectricityPrice": current_field,
        "AvgElectricityPrice": "0.5",
        "PriceKwhAvg": "0.55",
        "CurrentGasPrice": "1.2",
    }


def test_current_reads_from_curve_not_stale_field():
    # Regression test: the portal's CurrentElectricityPrice field can go stale,
    # so `current` must come from the curve at the current hour (10 -> 1.0),
    # matching the same source `next` uses (11 -> 1.1).
    result = _process_quarterly(_quarterly(current_field="9.99"), NOW)
    assert result["current"] == 1.0
    assert result["next"] == 1.1


def test_current_falls_back_to_field_when_anchor_missing():
    # Current hour (10) is absent from the curve -> fall back to the field.
    result = _process_quarterly(_quarterly(current_field="9.99", hours=range(5)), NOW)
    assert result["current"] == 9.99
    assert result["next"] is None


def test_curve_has_reconstructed_timestamps():
    result = _process_quarterly(_quarterly(), NOW)
    curve = result["curve"]
    assert len(curve) == 24
    # Hour 0 sits 10 hours before the 10:00 anchor.
    assert curve[0]["start"] == "2026-06-24T00:00:00+00:00"
    assert curve[10]["start"] == "2026-06-24T10:00:00+00:00"
    assert curve[10]["price"] == 1.0


def test_quarterly_skips_malformed_points():
    data = _quarterly()
    data["DataPoints"]["List"].append({"Label": "oops", "Value": "x"})
    result = _process_quarterly(data, NOW)
    assert len(result["curve"]) == 24  # malformed point dropped


def test_process_forecast_none():
    assert _process_forecast(None) is None
    assert _process_forecast({}) is None


def test_process_forecast_maps_fields():
    result = _process_forecast(
        {
            "Forecast_Window": {
                "Start_time": "2026-06-24T12:00:00",
                "Duration_hours": "6",
            },
            "Cheapest_Window": {
                "Start_time": "2026-06-24T14:00:00",
                "Duration_hours": "2",
            },
            "Next_Execution_Time": "2026-06-24T18:00:00",
            "AI_Sentences": {"insight": {"EN": {"List": ["hello"]}}},
        }
    )
    assert result["forecast_start"] == "2026-06-24T12:00:00"
    assert result["forecast_duration_hours"] == 6
    assert result["cheapest_start"] == "2026-06-24T14:00:00"
    assert result["cheapest_duration_hours"] == 2
    assert result["ai_sentences"] == {"insight": {"EN": {"List": ["hello"]}}}


def test_safe_float():
    assert _safe_float("3.5") == 3.5
    assert _safe_float(None) is None
    assert _safe_float("") is None
    assert _safe_float("abc") is None


def test_safe_int():
    assert _safe_int("4") == 4
    assert _safe_int(None) is None
    assert _safe_int("") is None
    assert _safe_int("nope") is None
