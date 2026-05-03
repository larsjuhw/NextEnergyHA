"""Constants for the NextEnergy integration."""
from __future__ import annotations

DOMAIN = "nextenergy"
PLATFORMS: list[str] = ["sensor"]

BASE_URL = "https://mijn.nextenergy.nl"
PORTAL_URL = f"{BASE_URL}/Mobile_EnergyNext/MarketPrices"

QUARTERLY_URL = (
    f"{BASE_URL}/Mobile_EnergyNext/screenservices/Mobile_EnergyNext"
    "/CW_WidgetFlow/MarketPrices_Quarterly_v2/DataActionGetPriceDataPoints"
)
FORECAST_URL = (
    f"{BASE_URL}/Mobile_EnergyNext/screenservices/Mobile_EnergyNext"
    "/CW_WidgetFlow/PriceForecast/DataActionGetForecastFileContent"
)

# Captured from the OutSystems frontend bundle. These rotate when NextEnergy
# redeploys the portal; on a 4xx the client invalidates the session and a
# re-bootstrap will pull whatever the portal currently advertises.
MODULE_VERSION = "oZwL4ffkzErR2QdpI8eKcQ"
QUARTERLY_API_VERSION = "CojBpHbLdUTYB8YFvXr0TA"
FORECAST_API_VERSION = "I5h+1ChToy0af84Jm_BF8w"

DEFAULT_UPDATE_INTERVAL = 3600
MIN_UPDATE_INTERVAL = 900
MAX_UPDATE_INTERVAL = 21600

CONF_UPDATE_INTERVAL = "update_interval"
CONF_LANGUAGE = "language"

DEFAULT_LANGUAGE = "EN"
SUPPORTED_LANGUAGES = ["EN", "NL"]

PRICE_LEVEL_TOTAL = "TotalPrice"
# TODO: confirm the exact enum value the portal uses for the raw exchange price.
# Captured HARs only contained TotalPrice. "MarketPrice" is a guess based on the
# field name `PriceCostLevel` and the fact that the portal exposes both views.
PRICE_LEVEL_MARKET = "MarketPrice"
