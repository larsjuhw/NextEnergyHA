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
from datetime import date
import logging
import re
from typing import Any
from urllib.parse import unquote
import uuid

import aiohttp
from yarl import URL

from .const import (
    BASE_URL,
    BOOTSTRAP_URLS,
    FORECAST_API_VERSION,
    FORECAST_URL,
    MODULE_VERSION,
    MODULEINFO_URL,
    PORTAL_URL,
    PRICE_LEVEL_TOTAL,
    QUARTERLY_API_VERSION,
    QUARTERLY_URL,
)

_LOGGER = logging.getLogger(__name__)

_CSRF_RE = re.compile(r"crf=([^;]+)")

# OutSystems embeds the CSRF token in the HTML shell (the browser pulls it
# from the DOM rather than from a Set-Cookie). The exact JS variable name
# varies between Reactive / Mobile / Traditional generations, so try a few
# common patterns in order. First capture group = token.
_HTML_CSRF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"_csrfToken\s*[=:]\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"csrfToken\s*[=:]\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"CsrfToken\s*[=:]\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"name=['\"]_csrfToken['\"][^>]*value=['\"]([^'\"]+)['\"]"),
    re.compile(r"value=['\"]([^'\"]+)['\"][^>]*name=['\"]_csrfToken['\"]"),
    re.compile(r"<meta\s+name=['\"]csrf[_-]token['\"]\s+content=['\"]([^'\"]+)['\"]"),
)

# OutSystems portals occasionally filter on User-Agent. Send a browser-ish
# string so the bootstrap GET behaves the same way it would in Firefox.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
    "Gecko/20100101 Firefox/150.0"
)


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

    async def aclose(self) -> None:
        await self._session.close()

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
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        # Pre-seed osVisit / osVisitor cookies the way the OutSystems
        # client-side analytics SDK does (via document.cookie in JavaScript).
        self._session.cookie_jar.update_cookies(
            {"osVisit": uuid.uuid4().hex, "osVisitor": uuid.uuid4().hex},
            response_url=URL(BASE_URL),
        )

        attempt_log: list[str] = []
        last_html_excerpt: str = ""
        for url in BOOTSTRAP_URLS:
            try:
                async with self._session.get(
                    url, headers=headers, allow_redirects=True
                ) as resp:
                    set_cookie_headers = resp.headers.getall("Set-Cookie", [])
                    cookie_names = [
                        h.split(";", 1)[0].split("=", 1)[0].strip()
                        for h in set_cookie_headers
                    ]
                    content_type = resp.headers.get("Content-Type", "?")
                    body = await resp.text(errors="replace")
                    attempt_log.append(
                        f"GET {url} -> {resp.status} (final {resp.url}); "
                        f"Set-Cookie: {cookie_names or 'none'}; "
                        f"ct={content_type}; body_len={len(body)}"
                    )
            except aiohttp.ClientError as err:
                attempt_log.append(f"GET {url} failed: {err}")
                continue

            # First try the cookie path (still might work on some servers).
            token = self._extract_csrf()
            source = "cookie"
            if not token:
                token = _extract_csrf_from_html(body)
                source = "html"
            if token:
                self._csrf_token = token
                # Also synthesize the nr2Users_Customers cookie the portal
                # expects on subsequent POSTs, in case it inspects the cookie
                # in addition to the X-CSRFToken header.
                self._session.cookie_jar.update_cookies(
                    {"nr2Users_Customers": f"crf={token};uid=0;unm="},
                    response_url=URL(BASE_URL),
                )
                _LOGGER.debug(
                    "NextEnergy CSRF token acquired from %s via %s",
                    source, url,
                )
                return

            # Remember the largest body we've seen so we can dump it for
            # diagnosis if every URL fails.
            if len(body) > len(last_html_excerpt):
                last_html_excerpt = body

        # The HTML shell didn't yield a token. Try the actual screenservices
        # endpoint without a CSRF header — in OutSystems Mobile, the very
        # first POST without auth is the canonical session-bootstrap call:
        # the server replies with Set-Cookie nr2Users_Customers (containing
        # the CSRF token) regardless of whether the request itself succeeds.
        probe_log: list[str] = []

        # First, fetch moduleinfo's full body — the manifest sometimes carries
        # auth-related metadata that we may need.
        try:
            async with self._session.get(
                MODULEINFO_URL,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json,*/*",
                    "Referer": PORTAL_URL,
                },
            ) as resp:
                set_cookie_headers = resp.headers.getall("Set-Cookie", [])
                body = await resp.text(errors="replace")
                cookie_names = [
                    h.split(";", 1)[0].split("=", 1)[0].strip()
                    for h in set_cookie_headers
                ]
                probe_log.append(
                    f"GET {MODULEINFO_URL} -> {resp.status}; "
                    f"Set-Cookie: {cookie_names or 'none'}; "
                    f"body_len={len(body)}; body[:600]={body[:600]!r}"
                )
                token = self._extract_csrf() or _extract_csrf_from_json_text(body)
                if token:
                    self._csrf_token = token
                    self._session.cookie_jar.update_cookies(
                        {"nr2Users_Customers": f"crf={token};uid=0;unm="},
                        response_url=URL(BASE_URL),
                    )
                    return
        except aiohttp.ClientError as err:
            probe_log.append(f"GET {MODULEINFO_URL} failed: {err}")

        # Now the canonical bootstrap: POST to the actual screenservices
        # endpoint with the real payload but no X-CSRFToken header. Logs the
        # response so we can see exactly what the server returns when an
        # unauthenticated client first hits it.
        from .const import PRICE_LEVEL_TOTAL  # local import avoids cycle
        try:
            async with self._session.post(
                QUARTERLY_URL,
                json=_build_quarterly_payload(date.today(), PRICE_LEVEL_TOTAL),
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json; charset=UTF-8",
                    "Origin": BASE_URL,
                    "Referer": PORTAL_URL,
                    "OutSystems-locale": "en-US",
                    # Deliberately no X-CSRFToken — we want the server to
                    # treat this as a first-contact anonymous call.
                },
            ) as resp:
                set_cookie_headers = resp.headers.getall("Set-Cookie", [])
                cookie_names = [
                    h.split(";", 1)[0].split("=", 1)[0].strip()
                    for h in set_cookie_headers
                ]
                body = await resp.text(errors="replace")
                probe_log.append(
                    f"POST {QUARTERLY_URL} (no-csrf) -> {resp.status}; "
                    f"Set-Cookie: {cookie_names or 'none'}; "
                    f"body[:400]={body[:400]!r}"
                )
                # Did the server set the cookie?
                token = self._extract_csrf() or _extract_csrf_from_json_text(body)
                if token:
                    self._csrf_token = token
                    self._session.cookie_jar.update_cookies(
                        {"nr2Users_Customers": f"crf={token};uid=0;unm="},
                        response_url=URL(BASE_URL),
                    )
                    _LOGGER.debug(
                        "NextEnergy CSRF token acquired from screenservices "
                        "first-contact response"
                    )
                    return
        except aiohttp.ClientError as err:
            probe_log.append(f"POST {QUARTERLY_URL} (no-csrf) failed: {err}")

        attempt_log.extend(probe_log)

        jar_names = sorted({m.key for m in self._session.cookie_jar})
        # The CSRF wasn't in the HTML shell — log the entire body so we can
        # see what scripts/endpoints it references. Bodies are ~5KB so this
        # is a one-off setup-failure dump, not log spam.
        _LOGGER.warning(
            "NextEnergy bootstrap failed. Attempts:\n  %s\n"
            "Cookie jar: %s\n----- FULL HTML BODY -----\n%s\n----- END BODY -----",
            "\n  ".join(attempt_log),
            jar_names or "empty",
            last_html_excerpt or "(no body captured)",
        )
        raise NextEnergyAuthError(
            "CSRF token missing after bootstrap "
            f"(cookies in jar: {jar_names or 'none'}). "
            "See HA log for the full request trace."
        )

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
        if self._csrf_token is None:
            raise NextEnergyError("CSRF token missing; session not bootstrapped")
        return {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=UTF-8",
            "Origin": BASE_URL,
            "Referer": PORTAL_URL,
            "OutSystems-locale": "en-US",
            "X-CSRFToken": self._csrf_token,
        }


def _extract_csrf_from_html(html: str) -> str | None:
    for pattern in _HTML_CSRF_PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group(1)
    return None


def _extract_csrf_from_json_text(text: str) -> str | None:
    """Look for csrf-token-shaped fields in a JSON-ish body."""
    if not text:
        return None
    for pattern in (
        re.compile(r'"csrfToken"\s*:\s*"([^"]+)"'),
        re.compile(r'"_csrfToken"\s*:\s*"([^"]+)"'),
        re.compile(r'"crf"\s*:\s*"([^"]+)"'),
    ):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


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
