"""NextEnergy sensor entities."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import homeassistant.util.dt as dt_util

from .const import CONF_LANGUAGE, DEFAULT_LANGUAGE, DOMAIN
from .coordinator import NextEnergyCoordinator

PRICE_UNIT = "EUR/kWh"
GAS_UNIT = "EUR/m³"


@dataclass(frozen=True, kw_only=True)
class NextEnergyPriceDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None


def _curve_attrs(curve_key: str) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    def _fn(data: dict[str, Any]) -> dict[str, Any] | None:
        bucket = data.get(curve_key) or {}
        curve = bucket.get("curve")
        if not curve:
            return None
        return {
            "prices_today": curve,
            "average": bucket.get("average_precise") or bucket.get("average"),
        }
    return _fn


PRICE_DESCRIPTIONS: tuple[NextEnergyPriceDescription, ...] = (
    NextEnergyPriceDescription(
        key="current_market",
        name="Current market price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d: (d.get("market") or {}).get("current"),
        attrs_fn=_curve_attrs("market"),
    ),
    NextEnergyPriceDescription(
        key="next_market",
        name="Next-hour market price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d: (d.get("market") or {}).get("next"),
    ),
    NextEnergyPriceDescription(
        key="current_market_plus",
        name="Current all-in price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d: (d.get("all_in") or {}).get("current"),
        attrs_fn=_curve_attrs("all_in"),
    ),
    NextEnergyPriceDescription(
        key="next_market_plus",
        name="Next-hour all-in price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d: (d.get("all_in") or {}).get("next"),
    ),
    NextEnergyPriceDescription(
        key="average_market_plus",
        name="Average all-in price (today)",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda d: (d.get("all_in") or {}).get("average_precise"),
    ),
    NextEnergyPriceDescription(
        key="current_gas",
        name="Current gas price",
        native_unit_of_measurement=GAS_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: (d.get("all_in") or {}).get("current_gas"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NextEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[CoordinatorEntity] = [
        NextEnergyPriceSensor(coordinator, entry, desc) for desc in PRICE_DESCRIPTIONS
    ]
    entities.append(NextEnergyCheapestWindowSensor(coordinator, entry))
    entities.append(NextEnergyForecastInsightSensor(coordinator, entry))
    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="NextEnergy",
        manufacturer="NextEnergy",
        model="Market prices",
        configuration_url="https://mijn.nextenergy.nl/",
    )


class _BaseEntity(CoordinatorEntity[NextEnergyCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: NextEnergyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = _device_info(entry)


class NextEnergyPriceSensor(_BaseEntity):
    entity_description: NextEnergyPriceDescription

    def __init__(
        self,
        coordinator: NextEnergyCoordinator,
        entry: ConfigEntry,
        description: NextEnergyPriceDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None or self.coordinator.data is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class NextEnergyCheapestWindowSensor(_BaseEntity):
    _attr_name = "Cheapest window start"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: NextEnergyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cheapest_window"

    @property
    def native_value(self) -> datetime | None:
        forecast = (self.coordinator.data or {}).get("forecast")
        if not forecast or not forecast.get("cheapest_start"):
            return None
        return dt_util.parse_datetime(forecast["cheapest_start"])

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        forecast = (self.coordinator.data or {}).get("forecast")
        if not forecast:
            return None
        return {
            "duration_hours": forecast.get("cheapest_duration_hours"),
            "forecast_window_start": forecast.get("forecast_start"),
            "forecast_window_duration_hours": forecast.get("forecast_duration_hours"),
            "next_forecast_update": forecast.get("next_execution_time"),
        }


class NextEnergyForecastInsightSensor(_BaseEntity):
    _attr_name = "Forecast insight"

    def __init__(self, coordinator: NextEnergyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_forecast_insight"

    def _language(self) -> str:
        return self._entry.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)

    def _sentences(self) -> dict[str, list[str]]:
        forecast = (self.coordinator.data or {}).get("forecast") or {}
        ai = forecast.get("ai_sentences") or {}
        lang = self._language()
        out: dict[str, list[str]] = {}
        for key, payload in ai.items():
            translations = payload.get(lang) if isinstance(payload, dict) else None
            if isinstance(translations, dict):
                lst = translations.get("List")
                if isinstance(lst, list):
                    out[key.lower()] = lst
        return out

    @property
    def native_value(self) -> str | None:
        sentences = self._sentences()
        insight = sentences.get("insight")
        return insight[0] if insight else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"sentences": self._sentences()}
