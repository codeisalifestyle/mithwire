"""Proxy infrastructure for the mithwire engine.

Provides proxy configuration parsing, an authenticating local relay for
credential-bearing HTTP proxies, and pre-launch health checking with egress
identity discovery.
"""

from .config import ProxyConfig, parse_proxy
from .health import (
    ProxyHealthError,
    ProxyRotationError,
    egress_summary,
    probe_proxy,
    trigger_rotation,
)
from .relay import LocalProxyRelay

__all__ = [
    "LocalProxyRelay",
    "ProxyConfig",
    "ProxyHealthError",
    "ProxyRotationError",
    "egress_summary",
    "parse_proxy",
    "probe_proxy",
    "trigger_rotation",
]
