"""Async client for the NextEnergy customer-portal market-price API.

The portal is built on OutSystems. The market-price screenservices accept
unauthenticated POSTs as long as the request carries a valid anonymous
session cookie set (osVisit / osVisitor / nr1Users_Customers /
nr2Users_Customers) plus an X-CSRFToken header lifted from the
nr2Users_Customers cookie's `crf=` field.

Lifecycle:
    1. GET the portal page once so the server sets cookies on the jar.
    2. Parse the CSRF token out of the nr2Users_Customers cookie.
    3. POST screen actions with that token; on 4xx, drop the token so the
       next call re-bootstraps.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any
from urllib.parse import unquote

import aiohttp
from yarl import URL

from .const import (
    BASE_URL,
    FORECAST_API_VERSION,
    FORECAST_URL,
    MODULE_VERSION,
    PORTAL_URL,
    PRICE_LEVEL_TOTAL,
    QUARTERLY_API_VERSION,
    QUARTERLY_URL,
)

_LOGGER = logging.getLogger(__name__)

_CSRF_RE = re.compile(r"crf=([^;]+)")


class NextEnergyError(Exception):
    """Generic transport / API error."""


class NextEnergyAuthError(NextEnergyError):
    """Session/CSRF was rejected; caller should treat as auth failure."""


class NextEnergyClient:
    """Anonymous OutSystems session client for the price endpoints."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._csrf_token: str | None = None
        self._lock = asyncio.Lock()

    async def fetch_market_prices(
        self,
        for_date: date,
        price_level: str = PRICE_LEVEL_TOTAL,
    ) -> dict[str, Any]:
        payload = _build_quarterly_payload(for_date, price_level)
        response = await self._post(QUARTERLY_URL, payload)
        data = response.get("data") or {}
        if not data:
            raise NextEnergyError("Empty data in market-prices response")
        return data

    async def fetch_forecast(self, for_date: date) -> dict[str, Any]:
        payload = _build_forecast_payload(for_date)
        response = await self._post(FORECAST_URL, payload)
        data = response.get("data") or {}
        if not data.get("IsSuccess", True):
            raise NextEnergyError(data.get("ErrorMessage") or "Forecast call failed")
        return data.get("Forecast_FileContent") or {}

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            await self._ensure_session()
            try:
                async with self._session.post(
                    url, json=payload, headers=self._headers()
                ) as resp:
                    if resp.status in (401, 403):
                        self._csrf_token = None
                        raise NextEnergyAuthError(
                            f"Portal returned {resp.status} for {url}"
                        )
                    if resp.status >= 400:
                        body = (await resp.text())[:200]
                        # 4xx often signals a moduleVersion / CSRF mismatch.
                        # Drop the session so the next attempt re-bootstraps.
                        self._csrf_token = None
                        raise NextEnergyError(
                            f"POST {url} returned {resp.status}: {body}"
                        )
                    return await resp.json()
            except aiohttp.ClientError as err:
                raise NextEnergyError(f"POST {url} failed: {err}") from err

    async def _ensure_session(self) -> None:
        if self._csrf_token is None:
            await self._bootstrap()

    async def _bootstrap(self) -> None:
        try:
            async with self._session.get(
                PORTAL_URL, allow_redirects=True
            ) as resp:
                resp.raise_for_status()
                # Drain so the connection is released; we only care about cookies.
                await resp.read()
        except aiohttp.ClientError as err:
            raise NextEnergyError(f"Bootstrap GET failed: {err}") from err

        token = self._extract_csrf()
        if not token:
            raise NextEnergyAuthError(
                "CSRF token missing from session cookies after bootstrap"
            )
        self._csrf_token = token
        _LOGGER.debug("NextEnergy session bootstrapped")

    def _extract_csrf(self) -> str | None:
        cookies = self._session.cookie_jar.filter_cookies(URL(BASE_URL))
        morsel = cookies.get("nr2Users_Customers")
        if not morsel:
            return None
        # Cookie value may be percent-encoded depending on how the jar stored it.
        raw = unquote(morsel.value)
        match = _CSRF_RE.search(raw)
        return match.group(1) if match else None

    def _headers(self) -> dict[str, str]:
        assert self._csrf_token is not None
        return {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=UTF-8",
            "Origin": BASE_URL,
            "Referer": PORTAL_URL,
            "OutSystems-locale": "en-US",
            "X-CSRFToken": self._csrf_token,
        }


def _build_quarterly_payload(for_date: date, price_level: str) -> dict[str, Any]:
    iso = for_date.isoformat()
    return {
        "versionInfo": {
            "moduleVersion": MODULE_VERSION,
            "apiVersion": QUARTERLY_API_VERSION,
        },
        "viewName": "MainFlow.MarketPrices",
        "screenData": {"variables": _quarterly_screen_variables()},
        "clientVariables": _client_variables(iso, price_level),
    }


def _build_forecast_payload(for_date: date) -> dict[str, Any]:
    iso = for_date.isoformat()
    return {
        "versionInfo": {
            "moduleVersion": MODULE_VERSION,
            "apiVersion": FORECAST_API_VERSION,
        },
        "viewName": "MainFlow.MarketPrices",
        "screenData": {
            "variables": {
                "Local_ExpectedFileType": ".json",
                "ScreenVariables": {
                    "EndDate_Formatted": "",
                    "ForecastExplanation": "",
                    "StartDate_Formatted": "",
                },
            }
        },
        "clientVariables": _client_variables(iso, PRICE_LEVEL_TOTAL),
    }


def _client_variables(iso_date: str, price_level: str) -> dict[str, Any]:
    return {
        "UsageDate": "1900-01-01",
        "UsageCostLevelId": "",
        "PriceCostLevel": price_level,
        "PriceDate": iso_date,
        "DistributionId": 0,
        "Chat_IsLoaded": False,
        "UsageUtilityTypeId": 0,
        "UsageYear": 0,
        "Chat_UnreadMessagesCounter": 0,
    }


def _quarterly_screen_variables() -> dict[str, Any]:
    return {
        "AuxContractProposition": _empty_proposition(),
        "BottomSheet_NPS": _empty_bottom_sheet(),
        "BottomSheet_OffPeak": _empty_bottom_sheet(),
        "BottomSheet_PriceExplanation": _empty_bottom_sheet(),
        "ContractAlert_IsVisible": False,
        "ContractInfo": {
            "ContractId": "0",
            "AccountId": "0",
            "StartDate": "1900-01-01",
            "EndDate": "1900-01-01",
            "ContractStatusId": 0,
        },
        "ContractProposition": _empty_proposition(),
        "ContractProposition_DaysLeft": 0,
        "CurrentHour": 0,
        "GasPrice": "0",
        "Graphsize": 235,
        "HighChartsJSON": "",
        "IsFixedPriceExplanation": False,
        "MarketPrice_StartDate": "2022-07-01",
        "OffPeakInterval": {
            "StartHour": 0,
            "EndHour": 0,
            "HasOffPeakIntervals": False,
        },
        "HasNonDynamicProposition": True,
        "DateTime": "1900-01-01T00:00:00",
        "_dateTimeInDataFetchStatus": 1,
        "ContractId": "0",
        "_contractIdInDataFetchStatus": 1,
    }


def _empty_proposition() -> dict[str, Any]:
    leg = {
        "PropositionTypeId": "",
        "Price": "0",
        "ValidFromDate": "1900-01-01",
        "ValidToDate": "1900-01-01",
        "StartDateTariff": "1900-01-01",
        "EndDateTariff": "1900-01-01",
        "StartDate": "1900-01-01",
        "EndDate": "1900-01-01",
    }
    return {
        "Electricity": dict(leg),
        "Gas": dict(leg),
        "HasFixedGas": False,
        "DateFrom": "1900-01-01",
        "DateTo": "1900-01-01",
    }


def _empty_bottom_sheet() -> dict[str, Any]:
    return {"IsRendered": False, "ToggleDateTime": "1900-01-01T00:00:00"}
