"""Pre-launch proxy health check.

A session that is *meant* to use a proxy must not silently fall back to the
host's direct connection — that defeats the entire identity model (real IP
leaks into login flows, the timezone/language we then auto-align to is the
WRONG country, and any persistent profile gets cross-contaminated). So when
``start_session`` is given a proxy, we probe it *before* the browser is
spawned and refuse to launch on failure.

The probe issues a ``GET`` to ``api.ipapi.is`` **through the proxy**, with
credentials when present. The same response is the canonical source we use to
derive the default identity (timezone, language, geo) — so the probe doubles
as the egress-info lookup that ``MithwireBrowser`` used to do *after* launch
via ``align_timezone_to_proxy``. Doing it pre-launch means we never need to
do it again, and a bad proxy fails fast with no half-launched browser to
clean up.

Notes on the implementation:

* The probe drives a ``urllib.request`` GET through a ``ProxyHandler``. That
  handler does the right thing for *both* possible upstreams: an absolute-form
  ``GET`` over HTTP for ``http://``-shaped targets, and a ``CONNECT`` tunnel
  with TLS for ``https://`` targets. ipapi.is now serves ``https`` only (the
  ``http`` endpoint 301-redirects to it), so we point at the ``http`` URL and
  let urllib follow the redirect through the proxy automatically — that
  exercises the proxy's CONNECT + TLS path the same way the real browser will.
* The synchronous ``urlopen`` runs in a worker thread; the surrounding
  ``asyncio.wait_for`` enforces an async-side deadline regardless of any
  socket-level timer.
* SOCKS proxies get a TCP-only liveness check. We don't support authenticated
  SOCKS in this launch flow, and a full SOCKS5 negotiation just to read JSON
  is more code than it's worth for the small minority of SOCKS users.
  Identity auto-derivation falls back to ``align_timezone_to_proxy`` on the
  live browser for SOCKS sessions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import ProxyConfig

logger = logging.getLogger(__name__)

# Primary probe target.  ipapi.is now 301-redirects http → https.  When urllib
# follows that redirect it attempts an HTTP CONNECT tunnel for the TLS leg,
# which many residential/mobile proxies don't support on their HTTP port —
# causing a silent hang.  We disable auto-redirect and, if the primary hangs,
# fall back to ip-api.com which still serves plain HTTP without redirecting.
_PROBE_TARGET_URL = "http://api.ipapi.is/"
_FALLBACK_PROBE_URL = "http://ip-api.com/json/"

_DEFAULT_TIMEOUT_SECONDS = 8.0


class ProxyHealthError(RuntimeError):
    """Raised when the configured proxy fails its pre-launch health check."""


class ProxyRotationError(RuntimeError):
    """Raised when hitting the provider's rotation endpoint fails."""


async def trigger_rotation(
    rotation_url: str,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Hit the provider's rotation endpoint and return the parsed reply.

    Almost all rotation endpoints are simple authenticated GETs over HTTPS —
    not something we route through the proxy itself (we are talking to the
    provider's *control plane*, not the network). The request runs in a
    worker thread because ``urllib.request.urlopen`` is sync; the surrounding
    ``wait_for`` gives us a hard deadline regardless of socket-level timers.

    A non-2xx response is treated as a failure and surfaces a redacted message
    (the URL almost always carries a session token in its query string; only
    status + a short body snippet are echoed). The JSON body, when parseable,
    is returned alongside the status so the caller can log provider-specific
    fields like ``new_ip`` or ``status``.
    """
    if not rotation_url:
        raise ProxyRotationError("Proxy has no rotation_url configured.")
    timeout = max(1.0, float(timeout_seconds))

    def _fetch() -> tuple[int, bytes]:
        request = urllib.request.Request(
            rotation_url,
            headers={
                "User-Agent": "mithwire-mcp/rotate",
                "Accept": "application/json, */*",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            # Capture the server's response body too — providers often explain
            # the failure (rate limit, expired token, etc.) in the JSON payload.
            try:
                body = exc.read()
            except Exception:  # noqa: BLE001
                body = b""
            return int(exc.code), body

    try:
        status, body = await asyncio.wait_for(
            asyncio.to_thread(_fetch),
            timeout=timeout + 5.0,
        )
    except asyncio.TimeoutError as exc:
        raise ProxyRotationError(
            f"Rotation endpoint did not respond within {timeout:.1f}s."
        ) from exc
    except urllib.error.URLError as exc:
        raise ProxyRotationError(
            f"Could not reach rotation endpoint: {exc.reason}"
        ) from exc
    except OSError as exc:
        raise ProxyRotationError(
            f"Could not reach rotation endpoint: {exc}"
        ) from exc

    text = body.decode("utf-8", errors="replace") if body else ""
    if not (200 <= status < 400):
        snippet = text[:200].replace("\n", " ")
        raise ProxyRotationError(
            f"Rotation endpoint returned HTTP {status}: {snippet!r}"
        )

    parsed: Any = None
    if text.strip():
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Providers occasionally reply with plain text ("ok"); surface it
            # but don't fail — the HTTP 2xx is the authoritative success.
            parsed = {"raw": text.strip()[:500]}

    return {"status": status, "response": parsed}


async def probe_proxy(
    proxy: ProxyConfig,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Verify the proxy is usable and (for HTTP/HTTPS) return egress identity.

    Returns the parsed ipapi.is JSON for HTTP/HTTPS upstreams; an empty dict
    for SOCKS upstreams (where only TCP reachability is checked). Raises
    :class:`ProxyHealthError` with a redacted, actionable message on any
    failure — bad host/port, refused TCP, HTTP 407 (wrong credentials), or an
    unparseable response. The browser never starts on failure.
    """
    timeout = max(1.0, float(timeout_seconds))
    if proxy.is_socks:
        await _check_tcp(proxy, timeout=timeout)
        return {}
    return await _http_egress_probe(proxy, timeout=timeout)


async def _check_tcp(proxy: ProxyConfig, *, timeout: float) -> None:
    """SOCKS / fallback liveness: just open and immediately close a TCP socket.

    We don't drive a SOCKS5 negotiation here because the launch flow doesn't
    support authenticated SOCKS anyway, so a successful TCP connect already
    tells us as much as we can verify cheaply.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(proxy.host, proxy.port),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        raise ProxyHealthError(
            f"Could not reach proxy {proxy.redacted()} within {timeout:.1f}s "
            "(connect timeout)."
        ) from exc
    except OSError as exc:
        raise ProxyHealthError(
            f"Could not reach proxy {proxy.redacted()}: {exc}"
        ) from exc
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass


def _proxy_url_for_urllib(proxy: ProxyConfig) -> str:
    """Build the proxy URL that ``urllib.request.ProxyHandler`` expects.

    Userinfo gets percent-quoted — tokens often contain ``@`` / ``:`` / ``/``
    which would otherwise corrupt the URL parser. We always advertise the
    proxy with the ``http://`` scheme (urllib's HTTPS-through-proxy code path
    uses HTTP for the proxy hop itself; the proxy scheme is unrelated to the
    target scheme).
    """
    netloc = f"{proxy.host}:{proxy.port}"
    if proxy.has_auth:
        user = urllib.parse.quote(proxy.username or "", safe="")
        pw = urllib.parse.quote(proxy.password or "", safe="")
        return f"http://{user}:{pw}@{netloc}"
    return f"http://{netloc}"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from following redirects (avoids CONNECT tunneling)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _normalize_ip_api_response(data: dict[str, Any]) -> dict[str, Any]:
    """Map an ip-api.com response to the ipapi.is schema."""
    return {
        "ip": data.get("query") or data.get("ip", ""),
        "location": {
            "country": data.get("country"),
            "country_code": data.get("countryCode") or data.get("country_code"),
            "city": data.get("city"),
            "timezone": data.get("timezone"),
            "latitude": data.get("lat") or data.get("latitude"),
            "longitude": data.get("lon") or data.get("longitude"),
        },
    }


def _parse_egress_response(
    proxy: ProxyConfig,
    status: int,
    body: bytes,
    *,
    schema: str = "ipapi",
) -> dict[str, Any]:
    """Validate and parse a successful egress probe response.

    ``schema`` selects the normalisation: ``"ipapi"`` (api.ipapi.is, the
    default) passes through as-is; ``"ip-api"`` maps ``http://ip-api.com``
    fields to the canonical schema.
    """
    if status == 407:
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} rejected the supplied credentials "
            "(HTTP 407 Proxy Authentication Required). Double-check username "
            "and password."
        )
    if not (200 <= status < 300):
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} returned HTTP {status} for the egress "
            f"probe."
        )
    if not body:
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} returned a 2xx response with an empty "
            "body — egress probe could not read identity."
        )

    text = body.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:200].replace("\n", " ")
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} returned non-JSON for the egress probe "
            f"(first 200 chars: {snippet!r})."
        ) from exc

    if not isinstance(data, dict):
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} returned a JSON value that is not an "
            "object."
        )

    if schema == "ip-api":
        data = _normalize_ip_api_response(data)

    ip_field = data.get("ip")
    if not (isinstance(ip_field, str) and ip_field.strip()):
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} responded but the egress probe could "
            "not determine an exit IP (response did not match the expected "
            "schema)."
        )

    return data


async def _http_egress_probe(proxy: ProxyConfig, *, timeout: float) -> dict[str, Any]:
    """Drive a GET to an IP-info service through the proxy and return egress JSON.

    Tries ``api.ipapi.is`` first (with redirects disabled to avoid CONNECT
    tunneling through proxies that only support absolute-form HTTP). Falls back
    to ``ip-api.com`` (plain HTTP, no redirect) when the primary times out or
    fails — a common scenario with residential/mobile HTTP proxies.
    """
    proxy_url = _proxy_url_for_urllib(proxy)

    def _fetch(url: str, *, follow_redirects: bool = False) -> tuple[int, bytes]:
        handlers: list = [urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})]
        if not follow_redirects:
            handlers.append(_NoRedirectHandler)
        opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "mithwire/proxy-probe",
                "Accept": "application/json, */*",
                "Connection": "close",
            },
            method="GET",
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
            except Exception:  # noqa: BLE001
                body = b""
            return int(exc.code), body

    primary_exc: Exception | None = None

    # --- Attempt 1: api.ipapi.is (redirects disabled) ---
    try:
        status, body = await asyncio.wait_for(
            asyncio.to_thread(_fetch, _PROBE_TARGET_URL, follow_redirects=False),
            timeout=timeout + 2.0,
        )
        if 300 <= status < 400:
            logger.debug(
                "Primary probe returned %d redirect — falling back to ip-api.com",
                status,
            )
            raise ProxyHealthError("primary probe redirected")
        return _parse_egress_response(proxy, status, body)
    except (ProxyHealthError, asyncio.TimeoutError, urllib.error.URLError,
            ConnectionError, OSError) as exc:
        primary_exc = exc
        logger.debug("Primary egress probe failed (%s), trying fallback", exc)

    # --- Attempt 2: ip-api.com (plain HTTP, no redirect) ---
    try:
        status, body = await asyncio.wait_for(
            asyncio.to_thread(_fetch, _FALLBACK_PROBE_URL, follow_redirects=True),
            timeout=timeout + 2.0,
        )
        return _parse_egress_response(proxy, status, body, schema="ip-api")
    except asyncio.TimeoutError as exc:
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} did not complete the egress probe "
            f"within {timeout:.1f}s (both primary and fallback timed out)."
        ) from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            raise ProxyHealthError(
                f"Proxy {proxy.redacted()} timed out during the egress probe "
                f"(socket timeout on fallback)."
            ) from exc
        raise ProxyHealthError(
            f"Could not reach proxy {proxy.redacted()}: {reason}"
        ) from exc
    except (ConnectionError, OSError) as exc:
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} failed mid-probe: {exc}"
        ) from exc


def egress_summary(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact view of the probe result suitable for session metadata."""
    if not isinstance(data, dict) or not data:
        return None
    location = data.get("location") or {}
    summary = {
        "exit_ip": data.get("ip"),
        "timezone": location.get("timezone"),
        "city": location.get("city"),
        "country": location.get("country"),
        "country_code": location.get("country_code"),
    }
    return {k: v for k, v in summary.items() if v}
