"""Constants for the NextEnergy integration."""
from __future__ import annotations

DOMAIN = "nextenergy"
PLATFORMS: list[str] = ["sensor"]

BASE_URL = "https://mijn.nextenergy.nl"
PORTAL_URL = f"{BASE_URL}/Mobile_EnergyNext/MarketPrices"

# Bootstrap endpoints we try in order — the first one to set the
# nr2Users_Customers cookie wins. The OutSystems module root is the canonical
# entry point and usually issues cookies; the SPA route sometimes doesn't.
BOOTSTRAP_URLS = (
    f"{BASE_URL}/Mobile_EnergyNext/",
    f"{BASE_URL}/",
    PORTAL_URL,
)

# OutSystems Mobile (Cordova) runtime endpoints. Most moduleservices paths
# return 404 on this build — moduleinfo is the only one that exists, and
# even that one doesn't issue session cookies. The real session bootstrap
# happens on the first screenservices POST: the server responds with
# Set-Cookie: nr2Users_Customers regardless of whether the call itself
# succeeds (likely 200) or fails (e.g. 412 / 401).
MODULEINFO_URL = f"{BASE_URL}/Mobile_EnergyNext/moduleservices/moduleinfo"

QUARTERLY_URL = (
    f"{BASE_URL}/Mobile_EnergyNext/screenservices/Mobile_EnergyNext_CW"
    "/WidgetFlow/MarketPrices_Quarterly_v2/DataActionGetPriceDataPoints"
)
FORECAST_URL = (
    f"{BASE_URL}/Mobile_EnergyNext/screenservices/Mobile_EnergyNext_CW"
    "/WidgetFlow/PriceForecast/DataActionGetForecastFileContent"
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
