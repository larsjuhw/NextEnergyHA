"""DataUpdateCoordinator combining the market-price and forecast endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .api import NextEnergyAuthError, NextEnergyClient, NextEnergyError
from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PRICE_LEVEL_MARKET,
    PRICE_LEVEL_TOTAL,
)

_LOGGER = logging.getLogger(__name__)


class NextEnergyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Pull both price flavours plus the forecast on every refresh."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self._client = NextEnergyClient(async_get_clientsession(hass))

    async def _async_update_data(self) -> dict[str, Any]:
        today = dt_util.now().date()
        try:
            all_in = await self._client.fetch_market_prices(today, PRICE_LEVEL_TOTAL)
            market = await self._client.fetch_market_prices(today, PRICE_LEVEL_MARKET)
        except NextEnergyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NextEnergyError as err:
            raise UpdateFailed(str(err)) from err

        # Forecast endpoint is supplementary — degrade gracefully if it fails.
        forecast: dict[str, Any] | None = None
        try:
            forecast = await self._client.fetch_forecast(today)
        except NextEnergyError as err:
            _LOGGER.debug("Forecast fetch failed (non-fatal): %s", err)

        now_utc = dt_util.utcnow()
        return {
            "all_in": _process_quarterly(all_in, now_utc),
            "market": _process_quarterly(market, now_utc),
            "forecast": _process_forecast(forecast),
            "fetched_at": now_utc,
        }


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

    return {
        "current": _safe_float(data.get("CurrentElectricityPrice")),
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
