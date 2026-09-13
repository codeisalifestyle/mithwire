"""CloakBrowser adapter — binary resolution and fingerprint flag translation.

CloakBrowser is a Chromium fork with C++ source-level fingerprint patches.
When ``engine=stealth`` is requested, this adapter:

1. Resolves the CloakBrowser binary (auto-downloads on first use).
2. Builds the ``--fingerprint=<seed>`` and supporting CLI flags that the
   binary consumes natively — CloakBrowser generates a *complete*, internally
   consistent fingerprint (canvas, WebGL, audio, fonts, GPU, screen, TLS,
   etc.) from a single integer seed.
3. Maps high-level identity properties (platform, timezone, locale) to the
   corresponding CloakBrowser flags.

The binary is proprietary (free to use, not redistributable) and downloaded
from official CloakHQ channels by the MIT-licensed ``cloakbrowser`` wrapper.
See https://github.com/CloakHQ/CloakBrowser/blob/main/BINARY-LICENSE.md
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import random
import sys

from .fingerprint import FingerprintConfig
from ..proxy import ProxyConfig

logger = logging.getLogger(__name__)

_LEGACY_PLATFORM_TO_CB: dict[str, str] = {
    "MacIntel": "macos",
    "Win32": "windows",
    "Win64": "windows",
    "Linux x86_64": "linux",
    "Linux armv81": "linux",
    "macos": "macos",
    "mac": "macos",
    "darwin": "macos",
    "windows": "windows",
    "win": "windows",
    "linux": "linux",
}


class CloakBrowserUnavailable(RuntimeError):
    """Raised when the cloakbrowser package is not installed."""


def is_platform_supported() -> bool:
    """Return True if the current OS supports the CloakBrowser binary."""
    return (
        sys.platform.startswith("linux")
        or sys.platform == "darwin"
        or sys.platform == "win32"
    )


def require_platform() -> None:
    """Raise if the current platform cannot run CloakBrowser stealth mode."""
    if not is_platform_supported():
        os_name = platform.system()
        raise ValueError(
            f"engine='stealth' requires Linux, macOS, or Windows (current: {os_name}). "
            "Use engine='cdp' (default) on this platform, which applies "
            "Mithwire's CDP/JS stealth patches."
        )


def resolve_binary(*, license_key: str | None = None) -> str:
    """Ensure the CloakBrowser binary is downloaded and return its path.

    Delegates to the ``cloakbrowser`` package which handles platform detection,
    download, checksum verification, and caching (~/.cloakbrowser/).
    """
    if license_key is None:
        license_key = os.environ.get("CLOAKBROWSER_LICENSE_KEY")

    try:
        from cloakbrowser import ensure_binary  # type: ignore[import-untyped]
    except ImportError as exc:
        raise CloakBrowserUnavailable(
            "engine='stealth' requires the cloakbrowser package. "
            "Install it with: pip install mithwire[stealth]"
        ) from exc

    try:
        result = ensure_binary(license_key=license_key)
    except Exception as exc:
        raise RuntimeError(
            f"CloakBrowser binary download/resolution failed: {exc}. "
            "Check network connectivity and disk space."
        ) from exc

    if isinstance(result, str):
        path = result
    elif isinstance(result, dict):
        path = result.get("executable_path") or result.get("path", "")
    else:
        path = str(result)

    if not path:
        raise RuntimeError(
            "CloakBrowser ensure_binary() returned no executable path. "
            "Try clearing the cache: python -c "
            "'from cloakbrowser import clear_cache; clear_cache()'"
        )

    logger.info("CloakBrowser binary resolved: %s", path)
    return path


def _profile_seed(profile_name: str) -> int:
    """Derive a stable 5-digit fingerprint seed from a profile name.

    CloakBrowser generates a complete, internally consistent fingerprint from
    a single integer seed. By deriving the seed from the profile name, the
    same profile always gets the same canvas hash, WebGL renderer, audio
    context, etc. -- critical for long-lived identity consistency.
    """
    digest = hashlib.sha256(profile_name.encode()).hexdigest()
    return 10000 + int(digest[:8], 16) % 90000


def fingerprint_to_flags(
    fp: FingerprintConfig,
    *,
    proxy: ProxyConfig | None = None,
    profile_name: str | None = None,
    headless: bool = True,
    portable_cookies: bool = True,
    transparent_proxy: bool = True,
    webrtc_ip: str | None = "auto",
) -> list[str]:
    """Translate a FingerprintConfig and proxy settings into CloakBrowser CLI flags.

    CloakBrowser generates a complete fingerprint from ``--fingerprint=<seed>``
    at the C++ level. Individual properties (canvas, WebGL, audio, fonts, GPU,
    screen) cannot be overridden separately -- they are all derived from the
    seed for internal consistency. Only platform, timezone, and locale can be
    set independently.

    ``--no-sandbox`` is added only on Linux: CloakBrowser's custom Chromium
    build does not ship the SUID sandbox helper (``chrome-sandbox``) that
    stock Chromium packages install, so namespace sandboxing always fails on
    Linux regardless of user. On macOS and Windows the native sandbox mechanism
    works without a helper binary, so the flag is omitted to avoid the detectable
    "unsupported command-line flag" infobar.
    """
    flags: list[str] = []
    if sys.platform.startswith("linux"):
        flags.append("--no-sandbox")

    if profile_name:
        seed = _profile_seed(profile_name)
    else:
        seed = random.randint(10000, 99999)
    flags.append(f"--fingerprint={seed}")

    # Disable canvas/audio noise injection. CloakBrowser's noise adds
    # detectable entropy that OVP and FingerprintJS flag as tampering
    # (hasCanvasNoise=true, browser tampering smart signal). Deterministic
    # rendering from the seed is sufficient for cross-session uniqueness
    # without triggering noise-detection heuristics.
    flags.append("--fingerprint-noise=false")

    host_cb = "linux" if sys.platform.startswith("linux") else (
        "macos" if sys.platform == "darwin" else "windows"
    )
    if fp.platform:
        cb_platform = _LEGACY_PLATFORM_TO_CB.get(fp.platform, host_cb)
        flags.append(f"--fingerprint-platform={cb_platform}")
    else:
        cb_platform = host_cb
        flags.append(f"--fingerprint-platform={host_cb}")

    # When spoofing Windows on Linux, align font metrics to avoid detection by
    # CreepJS and FingerprintJS font enumeration checks.
    if sys.platform.startswith("linux") and cb_platform == "windows":
        flags.append("--fingerprint-windows-font-metrics")

    if fp.timezone_id:
        flags.append(f"--fingerprint-timezone={fp.timezone_id}")

    if fp.languages:
        lang_csv = ",".join(fp.languages)
        flags.append(f"--lang={lang_csv}")
        flags.append(f"--fingerprint-locale={fp.primary_language}")
    elif fp.primary_language:
        flags.append(f"--lang={fp.primary_language}")
        flags.append(f"--fingerprint-locale={fp.primary_language}")

    # CloakBrowser 151+: Encrypt cookies with machine-independent key so
    # profiles can be copied across machines/containers without losing logins.
    if portable_cookies:
        flags.append("--fingerprint-portable-cookies")

    # CloakBrowser 151+: Advanced proxy connection and WebRTC IP leak protection
    if proxy is not None:
        if transparent_proxy:
            flags.append("--fingerprint-transparent-proxy")
        if webrtc_ip:
            flags.append(f"--fingerprint-webrtc-ip={webrtc_ip}")

    if not headless:
        flags.append("--ignore-gpu-blocklist")

    return flags


def build_launch_config(
    fp: FingerprintConfig,
    *,
    proxy: ProxyConfig | None = None,
    profile_name: str | None = None,
    headless: bool = True,
    license_key: str | None = None,
    portable_cookies: bool = True,
    transparent_proxy: bool = True,
    webrtc_ip: str | None = "auto",
    extra_args: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Return ``(binary_path, args_list)`` ready for MithwireBrowser.

    This is the main entry point: resolves the binary, translates the
    fingerprint, and assembles the full argument list.
    """
    require_platform()
    binary_path = resolve_binary(license_key=license_key)

    # Detect proxy presence from either proxy object or extra_args
    has_proxy_arg = bool(extra_args and any(arg.startswith("--proxy-server=") for arg in extra_args))
    effective_proxy = proxy
    if effective_proxy is None and has_proxy_arg:
        # Proxy is set via CLI flags
        effective_proxy = True  # type: ignore[assignment]

    args: list[str] = []
    translated = fingerprint_to_flags(
        fp,
        proxy=proxy,
        profile_name=profile_name,
        headless=headless,
        portable_cookies=portable_cookies,
        transparent_proxy=transparent_proxy,
        webrtc_ip=webrtc_ip,
    )

    # If proxy was only in extra_args, ensure transparent-proxy and webrtc-ip are applied
    if proxy is None and has_proxy_arg:
        if transparent_proxy and "--fingerprint-transparent-proxy" not in translated:
            translated.append("--fingerprint-transparent-proxy")
        if webrtc_ip and not any(a.startswith("--fingerprint-webrtc-ip=") for a in translated):
            translated.append(f"--fingerprint-webrtc-ip={webrtc_ip}")

    # Combine flags avoiding duplicate switches
    for flag in translated:
        prefix = flag.split("=")[0] if "=" in flag else flag
        if extra_args and any(arg == flag or (arg.startswith(prefix + "=") and "=" in flag) for arg in extra_args):
            continue
        args.append(flag)

    if extra_args:
        args.extend(extra_args)

    return binary_path, args
