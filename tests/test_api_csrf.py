"""CSRF-extraction tests. Skipped unless Home Assistant is installed, since
importing the api module pulls in the integration package (aiohttp + HA)."""
import pytest

pytest.importorskip("homeassistant")

from custom_components.nextenergy.api import (  # noqa: E402
    _CSRF_RE,
    _extract_csrf_from_html,
)


def test_csrf_re_pulls_token_from_cookie_value():
    match = _CSRF_RE.search("ses=1; crf=ABC123def; other=2")
    assert match is not None
    assert match.group(1) == "ABC123def"


@pytest.mark.parametrize(
    "html, expected",
    [
        ('window._csrfToken = "TOK1";', "TOK1"),
        ("var csrfToken: 'TOK2'", "TOK2"),
        ('<input name="_csrfToken" value="TOK3">', "TOK3"),
        ('<meta name="csrf-token" content="TOK4">', "TOK4"),
    ],
)
def test_extract_csrf_from_html(html, expected):
    assert _extract_csrf_from_html(html) == expected


def test_extract_csrf_from_html_returns_none_when_absent():
    assert _extract_csrf_from_html("<html>nothing here</html>") is None
