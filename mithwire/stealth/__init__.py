"""Engine-owned anti-detect stealth.

The mithwire engine owns every browser-altering anti-detect capability so that
*any* client (the mithwire-mcp server, or a custom script) gets identical
stealth simply by describing the identity it wants. A client never reimplements
patching; it passes a :class:`FingerprintConfig` and a WebRTC mode to the engine.

Public surface:

* :class:`FingerprintConfig` -- declarative identity description.
* :class:`Stealth` -- applies the patches to a live :class:`~mithwire.Browser`.
* :func:`compute_launch_args` -- the stealth-relevant Chromium command-line
  flags (``--lang``, ``--force-webrtc-ip-handling-policy``, headless window
  size) that must be set at launch, before the process starts.
"""

from __future__ import annotations

import logging
import subprocess
import sys

from .controller import Stealth
from .fingerprint import (
    FingerprintConfig,
    accept_language_csv,
    languages_for_country,
    strip_q_values,
)

__all__ = [
    "FingerprintConfig",
    "Stealth",
    "compute_launch_args",
    "languages_for_country",
    "accept_language_csv",
    "strip_q_values",
]

logger = logging.getLogger(__name__)

_PLATFORM_UA_TOKENS: dict[str, str] = {
    "MacIntel": "Macintosh; Intel Mac OS X 10_15_7",
    "Win32": "Windows NT 10.0; Win64; x64",
    "Win64": "Windows NT 10.0; Win64; x64",
    "Linux x86_64": "X11; Linux x86_64",
    "Linux armv81": "Linux; Android 10; K",
}


def _detect_chrome_major(executable_path: str) -> str | None:
    """Run the browser binary with --product-version and return the major."""
    try:
        result = subprocess.run(
            [executable_path, "--product-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version = result.stdout.strip()
        if version:
            return version.split(".")[0]
    except Exception:
        pass
    return None


def _build_clean_ua(
    *,
    major: str,
    fingerprint: "FingerprintConfig | None" = None,
) -> str:
    """Build a standard Chrome UA string without HeadlessChrome."""
    if fingerprint and fingerprint.platform:
        token = _PLATFORM_UA_TOKENS.get(fingerprint.platform)
    else:
        token = None
    if not token:
        if sys.platform == "darwin":
            token = "Macintosh; Intel Mac OS X 10_15_7"
        elif sys.platform.startswith("linux"):
            token = "X11; Linux x86_64"
        else:
            token = "Windows NT 10.0; Win64; x64"
    return (
        f"Mozilla/5.0 ({token}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


def compute_launch_args(
    browser_args: list[str],
    *,
    fingerprint: "FingerprintConfig | None" = None,
    headless: bool = False,
    browser_executable_path: str | None = None,
) -> list[str]:
    """Return the stealth launch flags to append, given the existing args.

    These flags MUST be applied at launch (they cannot be retrofitted via CDP
    on an already-spawned process without leaking):

    * ``--force-webrtc-ip-handling-policy`` — pinned per proxy presence.
    * ``--lang`` — propagates to workers (CDP cannot).
    * ``--window-size`` — matched to the fingerprint's screen dimensions so
      ``innerWidth/Height`` never exceeds ``outerWidth/Height``.
    * ``--user-agent`` — in headless mode, replaces the default
      ``HeadlessChrome/...`` UA at the binary level. CDP
      ``Emulation.setUserAgentOverride`` only reaches the main thread;
      Workers inherit the launch-time UA and would otherwise expose
      ``HeadlessChrome`` to any cross-scope consistency check (CreepJS).
    * ``--font-render-hinting=medium`` — headless Chrome defaults to
      ``HINTING_FULL`` which produces different glyph metrics from headed
      mode (``HINTING_MEDIUM``), reducing CSS-based font detection to a
      fraction of headed-mode results.
    * ``--force-effective-connection-type=4G`` — headless Chrome and
      server environments often report ``navigator.connection.rtt === 0``
      because the Network Quality Estimator cannot measure real latency.
      Forcing 4G yields plausible values (rtt ~50-150 ms, downlink ~1.5 Mbps).
    * ``--window-position`` — on Windows, headless Chrome 129+ can still
      spawn a blank overlay window; off-screen placement hides it.

    Existing flags are never duplicated.
    """
    existing = list(browser_args or [])
    extra: list[str] = []

    proxied = any(arg.startswith("--proxy-server=") for arg in existing)
    if not any("webrtc-ip-handling-policy" in arg for arg in existing):
        policy = "disable_non_proxied_udp" if proxied else "default_public_interface_only"
        extra.append(f"--force-webrtc-ip-handling-policy={policy}")

    if fingerprint is not None:
        lang = fingerprint.primary_language
        if lang and not any(arg.startswith("--lang=") for arg in existing):
            extra.append(f"--lang={lang}")

    if headless:
        if not any(arg.startswith("--window-size=") for arg in existing):
            w = int(fingerprint.screen_width) if fingerprint and fingerprint.screen_width else 1920
            h = int(fingerprint.screen_height) if fingerprint and fingerprint.screen_height else 1080
            extra.append(f"--window-size={w},{h}")

        if sys.platform == "win32" and not any(
            arg.startswith("--window-position=") for arg in existing
        ):
            extra.append("--window-position=-2400,-2400")

        if not any(arg.startswith("--user-agent=") for arg in existing):
            major = (
                _detect_chrome_major(browser_executable_path)
                if browser_executable_path
                else None
            )
            if major:
                ua = _build_clean_ua(major=major, fingerprint=fingerprint)
                extra.append(f"--user-agent={ua}")
                logger.info(
                    "Headless UA launch flag: Chrome/%s (propagates to Workers)", major
                )

        if not any("font-render-hinting" in arg for arg in existing):
            extra.append("--font-render-hinting=medium")

        if not any("force-effective-connection-type" in arg for arg in existing):
            extra.append("--force-effective-connection-type=4G")

    if not any("use-fake-device" in arg for arg in existing):
        extra.append("--use-fake-device-for-media-stream")

    return extra
