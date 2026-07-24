"""Pre-launch proxy health check.

A session that is *meant* to use a proxy must not silently fall back to the
host's direct connection — that defeats the entire identity model (real IP
leaks into login flows, the timezone/language we then auto-align to is the
WRONG country, and any persistent profile gets cross-contaminated). So when
``start_session`` is given a proxy, we probe it *before* the browser is
spawned and refuse to launch on failure.

The probe issues a plain-HTTP ``GET`` to ``ip-api.com/json/`` **through the
proxy**, with credentials when present. The response is the canonical source
used to derive the default identity (timezone, language, geo) — so the probe
doubles as the egress-info lookup. Doing it pre-launch means we never need to
do it again, and a bad proxy fails fast with no half-launched browser to
clean up.

Notes on the implementation:

* The probe uses a **raw TCP socket** to the proxy, sending an absolute-form
  ``GET http://ip-api.com/json/ HTTP/1.1`` request. This avoids two classes of
  failure that plagued urllib-based probes:
  (a) ``api.ipapi.is`` 301-redirects to HTTPS, requiring a CONNECT tunnel that
      many residential/mobile gateways don't support;
  (b) even with a plain-HTTP target, urllib's ``Connection: close`` causes
      keep-alive-only proxy gateways to hang until timeout.
  The raw-socket approach uses ``Proxy-Connection: Keep-Alive`` and reads
  exactly ``Content-Length`` bytes, sidestepping both issues.
* The synchronous socket I/O runs in a worker thread; the surrounding
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
import base64
import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Any

from .config import ProxyConfig

logger = logging.getLogger(__name__)

_PROBE_URL = "http://ip-api.com/json/"
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
            parsed = {"raw": text.strip()[:500]}

    return {"status": status, "response": parsed}


async def probe_proxy(
    proxy: ProxyConfig,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Verify the proxy is usable and (for HTTP/HTTPS) return egress identity.

    Returns parsed egress JSON (normalized to the ipapi.is schema) for
    HTTP/HTTPS upstreams; an empty dict for SOCKS upstreams (where only TCP
    reachability is checked). Raises :class:`ProxyHealthError` with a
    redacted, actionable message on any failure — bad host/port, refused TCP,
    HTTP 407 (wrong credentials), or an unparseable response.

    **The browser never starts on failure.**
    """
    timeout = max(1.0, float(timeout_seconds))
    if proxy.is_socks:
        await _check_tcp(proxy, timeout=timeout)
        return {}
    return await _http_egress_probe(proxy, timeout=timeout)


async def _check_tcp(proxy: ProxyConfig, *, timeout: float) -> None:
    """SOCKS liveness: just open and immediately close a TCP socket."""
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


def _normalize_ip_api_response(data: dict[str, Any]) -> dict[str, Any]:
    """Map an ip-api.com response to the ipapi.is schema the stack expects.

    ip-api.com returns a flat object::

        {"query": "1.2.3.4", "country": "UK", "countryCode": "GB",
         "city": "London", "timezone": "Europe/London",
         "lat": 51.5, "lon": -0.12, ...}

    The rest of the codebase (``FingerprintConfig.from_ipapi``,
    ``egress_summary``) expects the ipapi.is layout::

        {"ip": "1.2.3.4", "location": {"country": "UK",
         "country_code": "GB", "city": "London",
         "timezone": "Europe/London",
         "latitude": 51.5, "longitude": -0.12}}
    """
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
) -> dict[str, Any]:
    """Validate the raw HTTP response and return normalized egress identity."""
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

    data = _normalize_ip_api_response(data)

    ip_field = data.get("ip")
    if not (isinstance(ip_field, str) and ip_field.strip()):
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} responded but the egress probe could "
            "not determine an exit IP (response did not match the expected "
            "schema)."
        )

    return data


async def _http_egress_probe(
    proxy: ProxyConfig, *, timeout: float
) -> dict[str, Any]:
    """Probe the proxy via a plain-HTTP GET to ip-api.com using raw sockets.

    Uses an absolute-form ``GET`` (no CONNECT tunnel) so it works with every
    class of HTTP proxy — datacenter, residential, mobile. Reads exactly
    ``Content-Length`` bytes to avoid hanging on keep-alive-only gateways.
    """

    def _fetch() -> tuple[int, bytes]:
        auth_header = ""
        if proxy.has_auth:
            token = base64.b64encode(
                f"{proxy.username}:{proxy.password}".encode()
            ).decode("ascii")
            auth_header = f"Proxy-Authorization: Basic {token}\r\n"

        request = (
            f"GET {_PROBE_URL} HTTP/1.1\r\n"
            f"Host: ip-api.com\r\n"
            f"{auth_header}"
            "User-Agent: mithwire/proxy-probe\r\n"
            "Accept: application/json\r\n"
            "Proxy-Connection: Keep-Alive\r\n"
            "\r\n"
        )

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((proxy.host, proxy.port))
            sock.sendall(request.encode("ascii"))

            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ProxyHealthError(
                        f"Proxy {proxy.redacted()} closed connection before "
                        "sending response headers."
                    )
                buf += chunk

            header_end = buf.index(b"\r\n\r\n")
            headers_raw = buf[:header_end].decode("ascii", errors="replace")
            body_so_far = buf[header_end + 4:]

            status_code = 0
            content_length = -1
            for line in headers_raw.split("\r\n"):
                if line.startswith("HTTP/"):
                    status_code = int(line.split()[1])
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":", 1)[1].strip())

            if content_length >= 0:
                while len(body_so_far) < content_length:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    body_so_far += chunk
            else:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    body_so_far += chunk

            return status_code, body_so_far
        finally:
            sock.close()

    try:
        status, body = await asyncio.wait_for(
            asyncio.to_thread(_fetch),
            timeout=timeout + 5.0,
        )
    except asyncio.TimeoutError as exc:
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} did not complete the egress probe "
            f"within {timeout:.1f}s."
        ) from exc
    except ProxyHealthError:
        raise
    except (ConnectionError, OSError) as exc:
        raise ProxyHealthError(
            f"Proxy {proxy.redacted()} failed during egress probe: {exc}"
        ) from exc

    return _parse_egress_response(proxy, status, body)


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
