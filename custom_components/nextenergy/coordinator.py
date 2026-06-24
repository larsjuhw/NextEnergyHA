"""DataUpdateCoordinator combining the market-price and forecast endpoints."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .api import NextEnergyClient, NextEnergyError
from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EVENT_REFRESH_FAILED,
    EVENT_REFRESHED,
    PRICE_LEVEL_MARKET,
    PRICE_LEVEL_TOTAL,
)
from .processing import _process_forecast, _process_quarterly

_LOGGER = logging.getLogger(__name__)


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Shared device descriptor for all NextEnergy entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="NextEnergy",
        manufacturer="NextEnergy",
        model="Market prices",
        configuration_url="https://mijn.nextenergy.nl/",
    )


class NextEnergyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Pull both price flavours plus the forecast on every refresh."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: aiohttp.ClientSession,
    ) -> None:
        interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self._client = NextEnergyClient(session)
        self._entry_id = entry.entry_id

    async def async_close(self) -> None:
        """Close the underlying HTTP session on unload."""
        await self._client.aclose()

    async def _async_update_data(self) -> dict[str, Any]:
        today = dt_util.now().date()
        try:
            all_in = await self._client.fetch_market_prices(today, PRICE_LEVEL_TOTAL)
            market = await self._client.fetch_market_prices(today, PRICE_LEVEL_MARKET)
        except NextEnergyError as err:
            # No credentials to re-enter — both bootstrap failures and transient
            # API errors are treated as UpdateFailed. The client invalidates its
            # session on 4xx, so the next refresh will re-bootstrap automatically.
            self.hass.bus.async_fire(
                EVENT_REFRESH_FAILED,
                {"entry_id": self._entry_id, "error": str(err)},
            )
            raise UpdateFailed(str(err)) from err

        # Forecast endpoint is supplementary — degrade gracefully if it fails.
        forecast: dict[str, Any] | None = None
        forecast_error: str | None = None
        try:
            forecast = await self._client.fetch_forecast(today)
        except NextEnergyError as err:
            forecast_error = str(err)
            _LOGGER.debug("Forecast fetch failed (non-fatal): %s", err)

        now_utc = dt_util.utcnow()
        self.hass.bus.async_fire(
            EVENT_REFRESHED,
            {
                "entry_id": self._entry_id,
                "fetched_at": now_utc.isoformat(),
                "forecast_ok": forecast is not None,
                "forecast_error": forecast_error,
            },
        )
        return {
            "all_in": _process_quarterly(all_in, now_utc),
            "market": _process_quarterly(market, now_utc),
            "forecast": _process_forecast(forecast),
            "fetched_at": now_utc,
        }
