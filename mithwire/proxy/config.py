"""First-class proxy configuration for the browser MCP.

Accepts a handful of common spellings and normalizes them into a single
:class:`ProxyConfig`. The only proxy "modes" we support are the ones Chromium
itself supports:

* ``http`` / ``https`` upstream proxies, with optional username/password.
  Authentication is satisfied by a local authenticating relay (see
  :mod:`.local_proxy`): Chromium is pointed at ``127.0.0.1`` with no auth and
  the relay injects ``Proxy-Authorization`` upstream. This avoids per-request
  CDP ``Fetch`` interception, which floods the event loop and stalls heavy
  page loads.
* ``socks5`` / ``socks4`` upstream proxies, **without** authentication.

Chromium's ``--proxy-server`` flag (how this MCP wires every proxy) cannot
authenticate SOCKS proxies. mithwire *can* via per-context
``browser.create_context(proxy_server="socks5://user:pass@host:port")``, but that
path is not wired into this launch flow yet, so a SOCKS spec carrying credentials
is rejected up front with an actionable message rather than failing at nav time.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

# Maps the user-facing scheme spellings to the canonical Chromium scheme used
# in ``--proxy-server``.
_SCHEME_ALIASES: dict[str, str] = {
    "http": "http",
    "https": "https",
    "socks": "socks5",
    "socks5": "socks5",
    "socks5h": "socks5",
    "socks4": "socks4",
    "socks4a": "socks4",
}

_SOCKS_SCHEMES = {"socks4", "socks5"}

# Rotation URLs almost always live on plain HTTP(S) endpoints exposed by the
# provider (e.g. ``https://api.provider.com/rotate?token=...``). We refuse any
# other scheme up front so a typo never silently becomes a no-op at rotate
# time.
_VALID_ROTATION_SCHEMES = {"http", "https"}


def _normalize_rotation_url(raw: object) -> str | None:
    """Validate and canonicalize a rotation URL.

    Returns ``None`` for unset/blank input. Raises ``ValueError`` for anything
    that isn't a syntactically valid ``http(s)://...`` URL — we'd rather fail
    at config time than at rotate time.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in _VALID_ROTATION_SCHEMES:
        raise ValueError(
            f"rotation_url must be an http(s) URL, got {text!r}."
        )
    if not parsed.hostname:
        raise ValueError(
            f"rotation_url is missing a host: {text!r}."
        )
    return text


# Path segments shorter than this are treated as routing verbs (``rotate``,
# ``refresh``, ``v1``) and kept in the redacted output. Anything longer is
# almost always an opaque token (``rt_acbc...``, base64, UUID) and gets masked.
# This is heuristic but errs aggressively on the side of redaction — false
# positives just make logs less specific; false negatives leak secrets.
_REDACT_PATH_SEGMENT_THRESHOLD = 16


def _redact_path_segments(path: str) -> str:
    """Mask path segments that look like opaque session tokens.

    Provider routing paths are short, lowercase verbs (``/rotate``, ``/v1``,
    ``/refresh``); session tokens are long alphanumerics (``rt_acbc3a4651…``,
    UUIDs, base64-ish blobs). Anything ≥16 chars becomes ``***``.
    """
    segments = path.split("/")
    redacted = [
        ("***" if len(seg) >= _REDACT_PATH_SEGMENT_THRESHOLD else seg)
        for seg in segments
    ]
    return "/".join(redacted)


def _redact_rotation_url(raw: str) -> str:
    """Strip secrets from a rotation URL for safe surfacing in metadata/logs.

    Rotation endpoints embed the provider session token in any of three
    places: the query string (``?token=…``), the userinfo
    (``user:token@host``), or a tail path segment (``/rotate/rt_…``, used by
    e.g. falconproxy). All three are scrubbed; routing verbs and short
    segments stay so the redacted URL is still recognizable.
    """
    parsed = urlsplit(raw)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    path = _redact_path_segments(parsed.path or "")
    suffix = "?***" if parsed.query else ""
    return f"{parsed.scheme}://{host}{port}{path}{suffix}"


@dataclass(frozen=True)
class ProxyConfig:
    """Normalized upstream proxy description."""

    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    # Optional provider endpoint that rotates the upstream exit IP when hit.
    # Stored verbatim (often carries a secret token in the query string); use
    # ``to_metadata`` / ``_redact_rotation_url`` when surfacing it externally.
    rotation_url: str | None = None

    @property
    def has_auth(self) -> bool:
        return bool(self.username) or bool(self.password)

    @property
    def is_socks(self) -> bool:
        return self.scheme in _SOCKS_SCHEMES

    @property
    def has_rotation(self) -> bool:
        return bool(self.rotation_url)

    @property
    def server_url(self) -> str:
        """Value chrome expects after ``--proxy-server=`` (no credentials)."""
        return f"{self.scheme}://{self.host}:{self.port}"

    def proxy_server_arg(self) -> str:
        return f"--proxy-server={self.server_url}"

    def redacted(self) -> str:
        """Safe-to-log/persist representation that never leaks the password."""
        if self.username:
            return f"{self.scheme}://{self.username}:***@{self.host}:{self.port}"
        return self.server_url

    def to_metadata(self) -> dict[str, object]:
        data: dict[str, object] = {
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "has_auth": self.has_auth,
            "has_rotation": self.has_rotation,
            "redacted": self.redacted(),
        }
        if self.rotation_url:
            data["rotation_url"] = _redact_rotation_url(self.rotation_url)
        return data


def _normalize_scheme(raw: str | None) -> str:
    scheme = (raw or "http").strip().lower()
    if scheme not in _SCHEME_ALIASES:
        raise ValueError(
            f"Unsupported proxy scheme '{raw}'. "
            f"Expected one of: {', '.join(sorted(_SCHEME_ALIASES))}."
        )
    return _SCHEME_ALIASES[scheme]


def _coerce_port(raw: object) -> int:
    try:
        port = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Proxy port must be an integer, got {raw!r}.") from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"Proxy port out of range (1-65535): {port}.")
    return port


def _build(
    *,
    scheme: str | None,
    host: str | None,
    port: object,
    username: str | None,
    password: str | None,
    rotation_url: object = None,
) -> ProxyConfig:
    normalized_scheme = _normalize_scheme(scheme)
    clean_host = (host or "").strip()
    if not clean_host:
        raise ValueError("Proxy host is required.")
    config = ProxyConfig(
        scheme=normalized_scheme,
        host=clean_host,
        port=_coerce_port(port),
        username=(username or None),
        password=(password or None),
        rotation_url=_normalize_rotation_url(rotation_url),
    )
    if config.is_socks and config.has_auth:
        raise ValueError(
            "Authenticated SOCKS proxies are not wired into this launch flow yet "
            "(Chromium's --proxy-server cannot authenticate SOCKS; mithwire supports "
            "it via create_context). Use the provider's HTTP/HTTPS endpoint with the "
            "same credentials, or an IP-whitelisted SOCKS endpoint without a password."
        )
    return config


def _parse_url_form(spec: str, *, rotation_url: object = None) -> ProxyConfig:
    parsed = urlsplit(spec)
    return _build(
        scheme=parsed.scheme,
        host=parsed.hostname,
        port=parsed.port if parsed.port is not None else "",
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        rotation_url=rotation_url,
    )


def _parse_colon_form(spec: str, *, rotation_url: object = None) -> ProxyConfig:
    """Parse ``scheme:host:port[:user:pass]`` (and the no-scheme variant)."""
    parts = spec.split(":")
    scheme: str | None = None
    if parts and parts[0].strip().lower() in _SCHEME_ALIASES:
        scheme = parts.pop(0)
    if len(parts) < 2:
        raise ValueError(
            f"Could not parse proxy {spec!r}. "
            "Expected 'scheme:host:port' or 'scheme:host:port:user:pass'."
        )
    host = parts[0]
    port = parts[1]
    username: str | None = None
    password: str | None = None
    if len(parts) >= 3:
        username = parts[2] or None
    if len(parts) >= 4:
        # Re-join any trailing fragments so passwords containing ':' survive.
        password = ":".join(parts[3:]) or None
    return _build(
        scheme=scheme,
        host=host,
        port=port,
        username=username,
        password=password,
        rotation_url=rotation_url,
    )


def parse_proxy(spec: object) -> ProxyConfig | None:
    """Normalize a proxy spec into a :class:`ProxyConfig` (or ``None``).

    Accepts:

    * ``None`` / empty string -> ``None`` (no proxy).
    * an existing :class:`ProxyConfig` (returned unchanged).
    * a mapping with ``scheme``/``host``/``port``/``username``/``password``
      and an optional ``rotation_url`` (or a single ``server`` URL string
      under the ``server`` key, again with optional ``rotation_url``).
    * a URL string: ``http://user:pass@host:port``, ``socks5://host:port``.
    * a colon string: ``http:host:port:user:pass`` (the provider format).

    The string forms cannot carry a ``rotation_url`` because there's no
    unambiguous slot for it; use the dict form (or wrap the string under
    ``{"server": "...", "rotation_url": "..."}``) to attach one.
    """
    if spec is None:
        return None
    if isinstance(spec, ProxyConfig):
        return spec
    if isinstance(spec, dict):
        rotation_url = spec.get("rotation_url") or spec.get("rotation")
        server = spec.get("server")
        if server and not spec.get("host"):
            base = parse_proxy(str(server))
            if base is None:
                return None
            return _build(
                scheme=spec.get("scheme") or base.scheme,
                host=base.host,
                port=base.port,
                username=spec.get("username", base.username),
                password=spec.get("password", base.password),
                rotation_url=rotation_url if rotation_url is not None else base.rotation_url,
            )
        return _build(
            scheme=spec.get("scheme"),
            host=spec.get("host"),
            port=spec.get("port", ""),
            username=spec.get("username"),
            password=spec.get("password"),
            rotation_url=rotation_url,
        )
    text = str(spec).strip()
    if not text:
        return None
    if "://" in text:
        return _parse_url_form(text)
    return _parse_colon_form(text)
