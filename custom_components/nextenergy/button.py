"""Diagnostic button to force a coordinator refresh."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NextEnergyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NextEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([NextEnergyRefreshButton(coordinator, entry)])


class NextEnergyRefreshButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Refresh"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: NextEnergyCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="NextEnergy",
            manufacturer="NextEnergy",
            model="Market prices",
            configuration_url="https://mijn.nextenergy.nl/",
        )

    async def async_press(self) -> None:
        await self._coordinator.async_request_refresh()
