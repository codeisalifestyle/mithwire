"""Generate realistic fingerprints via BrowserForge's Bayesian network.

BrowserForge samples from joint probability distributions trained on real
browser populations, ensuring that screen resolutions, hardware specs, and
UA strings appear together at plausible frequencies.  This module wraps
BrowserForge and maps its output to Mithwire's :class:`FingerprintConfig`.

Install with: ``pip install mithwire[fingerprints]``
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from .stealth import FingerprintConfig

logger = logging.getLogger(__name__)

_BROWSERFORGE_AVAILABLE: bool | None = None


def _check_available() -> bool:
    global _BROWSERFORGE_AVAILABLE
    if _BROWSERFORGE_AVAILABLE is None:
        try:
            from browserforge.fingerprints import FingerprintGenerator  # noqa: F401

            _BROWSERFORGE_AVAILABLE = True
        except ImportError:
            _BROWSERFORGE_AVAILABLE = False
    return _BROWSERFORGE_AVAILABLE


def is_available() -> bool:
    """Return True if browserforge is installed."""
    return _check_available()


_OS_MAP = {
    "Windows": "windows",
    "macOS": "macos",
    "Linux": "linux",
    "linux": "linux",
    "windows": "windows",
    "macos": "macos",
    "darwin": "macos",
}

_HOST_OS = "macos" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else "windows"


def generate(
    *,
    os: str | None = None,
    browser: str = "chrome",
    min_version: int = 140,
    locale: str | None = None,
    screen_min_width: int = 1280,
    screen_max_width: int = 2560,
    screen_min_height: int = 720,
    screen_max_height: int = 1440,
    include_ua: bool = False,
) -> FingerprintConfig:
    """Generate a statistically plausible fingerprint.

    Parameters
    ----------
    os : str, optional
        Target OS (``"windows"``, ``"linux"``, ``"macos"``).
        Defaults to the host OS to respect the same-OS-family rule.
    browser : str
        Browser engine (``"chrome"`` recommended).
    min_version : int
        Reject Chrome versions below this (avoids stale Bayesian samples).
    locale : str, optional
        Primary locale (e.g. ``"en-US"``). Passed through to BrowserForge.
    include_ua : bool
        Whether to include a generated user-agent string. Defaults to False
        because in CDP mode the actual Chrome binary's version-specific
        behaviours would contradict a BrowserForge-generated UA (e.g. Chrome
        147 claimed vs Chrome 150 actual). Set True only when the caller
        controls the binary version (e.g. stealth/CloakBrowser mode).
    screen_min_width, screen_max_width, screen_min_height, screen_max_height
        Screen size bounds for realistic display distributions.

    Returns
    -------
    FingerprintConfig
        A config ready to be passed to ``session_start(fingerprint=...)``
        or merged with proxy-derived geo defaults.
    """
    if not _check_available():
        logger.debug("browserforge not installed; returning empty FingerprintConfig")
        return FingerprintConfig()

    from browserforge.fingerprints import FingerprintGenerator, Screen
    from browserforge.headers import Browser

    target_os = _OS_MAP.get(os or "", _HOST_OS)

    browsers = [Browser(name=browser, min_version=min_version)]
    screen = Screen(
        min_width=screen_min_width,
        max_width=screen_max_width,
        min_height=screen_min_height,
        max_height=screen_max_height,
    )

    fg = FingerprintGenerator(screen=screen)

    locale_args: dict[str, Any] = {}
    if locale:
        locale_args["locale"] = locale

    fp = fg.generate(browser=browsers, os=target_os, **locale_args)

    nav = fp.navigator
    scr = fp.screen

    languages: list[str] | None = None
    if isinstance(getattr(nav, "languages", None), (list, tuple)):
        languages = list(nav.languages)
    elif isinstance(getattr(nav, "language", None), str) and nav.language:
        languages = [nav.language]

    return FingerprintConfig(
        user_agent=getattr(nav, "userAgent", None) if include_ua else None,
        platform=getattr(nav, "platform", None),
        hardware_concurrency=getattr(nav, "hardwareConcurrency", None),
        device_memory=getattr(nav, "deviceMemory", None),
        screen_width=getattr(scr, "width", None),
        screen_height=getattr(scr, "height", None),
        device_scale_factor=getattr(scr, "devicePixelRatio", None),
        max_touch_points=getattr(nav, "maxTouchPoints", None),
        languages=languages,
        locale=languages[0] if languages else None,
        source={"generator": "browserforge", "os": target_os},
    )


def generate_for_proxy(
    *,
    proxy_egress: dict[str, Any],
    os: str | None = None,
    browser: str = "chrome",
    min_version: int = 140,
) -> FingerprintConfig:
    """Generate a fingerprint aligned with proxy egress geo.

    Merges BrowserForge's hardware/screen with the proxy-derived timezone,
    locale, and languages so the identity is internally consistent.
    """
    base = generate(os=os, browser=browser, min_version=min_version)

    location = proxy_egress.get("location", {})
    country_code = location.get("country_code", "").upper()

    from .stealth import languages_for_country
    geo_languages = languages_for_country(country_code) if country_code else None

    if geo_languages and not base.languages:
        base.languages = geo_languages
        base.locale = geo_languages[0]

    base.timezone_id = location.get("timezone")
    base.latitude = location.get("latitude")
    base.longitude = location.get("longitude")

    return base
