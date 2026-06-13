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


def compute_launch_args(
    browser_args: list[str],
    *,
    fingerprint: "FingerprintConfig | None" = None,
    headless: bool = False,
) -> list[str]:
    """Return the stealth launch flags to append, given the existing args.

    These flags MUST be applied at launch (they cannot be retrofitted via CDP
    on an already-spawned process without leaking):

    * ``--force-webrtc-ip-handling-policy`` — pinned per proxy presence. A proxy
      (detected by the presence of ``--proxy-server=`` in ``browser_args``)
      forces ``disable_non_proxied_udp`` so WebRTC can never reveal the real
      egress IP behind the proxy; a direct connection uses
      ``default_public_interface_only`` (its public IP is the legitimate one,
      but private/LAN IPs stay hidden).
    * ``--lang`` — Chromium applies it itself, so it propagates to
      ``navigator.language(s)``, the ``Accept-Language`` header, and Web Workers
      consistently. A runtime CDP override cannot rewrite ``navigator.languages``
      in already-spawned workers, so the launch flag is the only leak-free way.
    * ``--window-size`` — headless Chrome otherwise reports a default-ish screen
      that, combined with device-metric overrides, can produce impossible
      viewport/screen combinations.

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

    if headless and not any(arg.startswith("--window-size=") for arg in existing):
        extra.append("--window-size=1920,1080")

    return extra
