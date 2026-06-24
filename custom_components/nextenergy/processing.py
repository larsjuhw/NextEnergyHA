"""Pure helpers for shaping the portal's raw responses into coordinator data.

Kept free of Home Assistant imports so the logic can be unit-tested with only
the standard library.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _process_quarterly(data: dict[str, Any], now_utc: datetime) -> dict[str, Any]:
    """Reduce one quarterly response to current/next/curve."""
    raw_points = ((data.get("DataPoints") or {}).get("List")) or []
    parsed: list[tuple[int, float]] = []
    for point in raw_points:
        try:
            parsed.append((int(point["Label"]), float(point["Value"])))
        except (KeyError, TypeError, ValueError):
            continue

    # The list is chronological; Labels are UTC clock-hours ("0".."23").
    # Anchor on the entry whose Label matches the current UTC hour so we can
    # reconstruct an absolute timestamp for every point in the curve.
    current_utc_hour = now_utc.hour
    anchor_idx: int | None = next(
        (i for i, (h, _) in enumerate(parsed) if h == current_utc_hour),
        None,
    )

    curve: list[dict[str, Any]] = []
    if anchor_idx is not None:
        anchor_ts = now_utc.replace(minute=0, second=0, microsecond=0)
        for i, (_, price) in enumerate(parsed):
            ts = anchor_ts + timedelta(hours=(i - anchor_idx))
            curve.append({"start": ts.isoformat(), "price": price})
    else:
        for label, price in parsed:
            curve.append({"start": None, "label_utc_hour": label, "price": price})

    next_price: float | None = None
    if anchor_idx is not None and anchor_idx + 1 < len(parsed):
        next_price = parsed[anchor_idx + 1][1]

    # Prefer the curve's value for the current hour. The portal's
    # CurrentElectricityPrice field is sometimes cached server-side and goes
    # stale for hours, while the curve advances correctly each hour (it's the
    # same source `next` reads from). Fall back to the field only when the
    # hour anchor can't be located in the curve.
    if anchor_idx is not None:
        current_price = parsed[anchor_idx][1]
    else:
        current_price = _safe_float(data.get("CurrentElectricityPrice"))

    return {
        "current": current_price,
        "next": next_price,
        "average": _safe_float(data.get("AvgElectricityPrice")),
        "average_precise": _safe_float(data.get("PriceKwhAvg")),
        "current_gas": _safe_float(data.get("CurrentGasPrice")),
        "curve": curve,
    }


def _process_forecast(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    forecast_window = data.get("Forecast_Window") or {}
    cheapest = data.get("Cheapest_Window") or {}
    return {
        "forecast_start": forecast_window.get("Start_time"),
        "forecast_duration_hours": _safe_int(forecast_window.get("Duration_hours")),
        "cheapest_start": cheapest.get("Start_time"),
        "cheapest_duration_hours": _safe_int(cheapest.get("Duration_hours")),
        "next_execution_time": data.get("Next_Execution_Time"),
        "ai_sentences": data.get("AI_Sentences"),
    }


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
