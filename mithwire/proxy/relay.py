"""Local authenticating proxy relay.

Chromium's ``--proxy-server`` flag cannot carry credentials, and answering the
upstream's HTTP 407 challenge over CDP ``Fetch`` requires intercepting *every*
request, which floods the event-dispatch loop and stalls heavy page loads.

This module sidesteps that entirely. It runs a tiny asyncio TCP server on
``127.0.0.1`` that Chromium uses as an *unauthenticated* HTTP proxy. For each
client connection the relay opens a socket to the real upstream proxy and
injects a ``Proxy-Authorization: Basic ...`` header, then pipes bytes in both
directions. Chromium never sees a 407, so no CDP interception is needed and
pages load at native speed.

Supported client traffic:

* ``CONNECT host:port`` (HTTPS tunnels) -- one challenge per tunnel, injected
  into the forwarded ``CONNECT`` line.
* Absolute-form HTTP requests (``GET http://host/path``) -- the auth header is
  injected into the (first) request head; the connection is then piped raw.

The relay only adds credentials; it does not parse or modify payloads.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass

from .config import ProxyConfig

logger = logging.getLogger(__name__)

_HEADER_TERMINATOR = b"\r\n\r\n"
_MAX_HEAD_BYTES = 64 * 1024


@dataclass
class _RelayState:
    host: str
    port: int


class LocalProxyRelay:
    """An authenticating forward-proxy bound to localhost.

    Start it, point Chromium at :attr:`server_url`, and it transparently
    authenticates to the configured upstream proxy.
    """

    def __init__(self, upstream: ProxyConfig, *, host: str = "127.0.0.1") -> None:
        if upstream.is_socks:
            raise ValueError("LocalProxyRelay only supports HTTP(S) upstream proxies.")
        self._upstream = upstream
        self._host = host
        self._server: asyncio.AbstractServer | None = None
        self._state: _RelayState | None = None
        self._auth_header = self._build_auth_header(upstream)
        self._clients: set[asyncio.Task[None]] = set()

    @staticmethod
    def _build_auth_header(upstream: ProxyConfig) -> bytes:
        raw = f"{upstream.username or ''}:{upstream.password or ''}".encode()
        token = base64.b64encode(raw).decode("ascii")
        return f"Proxy-Authorization: Basic {token}\r\n".encode("ascii")

    @property
    def bound(self) -> bool:
        return self._state is not None

    @property
    def server_url(self) -> str:
        if self._state is None:
            raise RuntimeError("LocalProxyRelay is not started.")
        return f"http://{self._state.host}:{self._state.port}"

    def proxy_server_arg(self) -> str:
        return f"--proxy-server={self.server_url}"

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client, self._host, 0
        )
        sock = self._server.sockets[0]
        bound_host, bound_port = sock.getsockname()[:2]
        self._state = _RelayState(host=bound_host, port=int(bound_port))
        logger.info(
            "LocalProxyRelay listening on %s -> upstream %s",
            self.server_url,
            self._upstream.redacted(),
        )

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        for task in list(self._clients):
            task.cancel()
        self._clients.clear()
        self._server = None
        self._state = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        upstream_reader: asyncio.StreamReader | None = None
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            head = await self._read_head(reader)
            if not head:
                return

            upstream_reader, upstream_writer = await asyncio.open_connection(
                self._upstream.host, self._upstream.port
            )
            forwarded = self._inject_auth(head)
            upstream_writer.write(forwarded)
            await upstream_writer.drain()

            await asyncio.gather(
                self._pipe(reader, upstream_writer),
                self._pipe(upstream_reader, writer),
            )
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("relay client error: %s", exc)
        finally:
            for w in (writer, upstream_writer):
                if w is not None:
                    try:
                        w.close()
                    except Exception:  # noqa: BLE001
                        pass
            if task is not None:
                self._clients.discard(task)

    async def _read_head(self, reader: asyncio.StreamReader) -> bytes:
        """Read up to (and including) the first header terminator."""
        buf = bytearray()
        while _HEADER_TERMINATOR not in buf:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > _MAX_HEAD_BYTES:
                break
        return bytes(buf)

    def _inject_auth(self, head: bytes) -> bytes:
        """Insert the Proxy-Authorization header after the request line."""
        idx = head.find(b"\r\n")
        if idx == -1:
            # No header break found; forward unchanged.
            return head
        request_line = head[: idx + 2]
        rest = head[idx + 2 :]
        # Drop any pre-existing (empty) proxy-auth header from the client.
        return request_line + self._auth_header + rest

    @staticmethod
    async def _pipe(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                if writer.can_write_eof():
                    writer.write_eof()
            except Exception:  # noqa: BLE001
                pass
