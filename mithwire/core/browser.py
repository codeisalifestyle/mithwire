# Copyright 2024 by UltrafunkAmsterdam (https://github.com/UltrafunkAmsterdam)
# All rights reserved.
# This file is part of the mithwire package.
# and is released under the "GNU AFFERO GENERAL PUBLIC LICENSE".
# Please see the LICENSE.txt file that should have been included as part of this package.

from __future__ import annotations

import asyncio
import atexit
import contextlib
import http.cookiejar
import json
import logging
import os
import pathlib
import pickle
import shutil
import urllib.parse
import urllib.request
import warnings
from collections import defaultdict
from typing import List, Optional, Tuple, Union

from .. import cdp
from . import _exit_guard, tab, util
from ._contradict import ContraDict
from .config import Config, PathLike, is_posix
from .connection import Connection

logger = logging.getLogger(__name__)

# Strong references to fire-and-forget cleanup tasks so they cannot be
# garbage-collected (and silently dropped) before they finish.
_background_tasks: set = set()


def _kill_if_running(process: asyncio.subprocess.Process) -> None:
    """SIGKILL ``process`` unless it already exited. Safe to call from a loop callback."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()


class Browser(Connection):
    """
    The Browser object is the "root" of the hierarchy and contains a reference
    to the browser parent process.
    there should usually be only 1 instance of this.

    All opened tabs, extra browser screens and resources will not cause a new Browser process,
    but rather create additional :class:`mithwire.Tab` objects.

    So, besides starting your instance and first/additional tabs, you don't actively use it a lot under normal conditions.

    Tab objects will represent and control
     - tabs (as you know them)
     - browser windows (new window)
     - iframe
     - background processes

    note:
    the Browser object is not instantiated by __init__ but using the asynchronous :meth:`mithwire.Browser.create` method.

    note:
    in Chromium based browsers, there is a parent process which keeps running all the time, even if
    there are no visible browser windows. sometimes it's stubborn to close it, so make sure after using
    this library, the browser is correctly and fully closed/exited/killed.

    """

    _process: asyncio.subprocess.Process
    _process_pid: int
    _http: HTTPApi = None
    _cookies: CookieJar = None
    _stop_timeout: float = 5.0
    #: How long ``start()`` keeps retrying to reach the DevTools endpoint.
    _connect_timeout: float = 10.0
    #: After a failed/cancelled launch: SIGTERM first, SIGKILL after this long.
    _abort_kill_after: float = 3.0
    #: Sidecar that kills the browser if this process dies uncleanly.
    _guard: Optional[_exit_guard.ExitGuard] = None
    #: True while this instance owns an ephemeral profile directory it created.
    _owns_temp_profile: bool = False

    config: Config

    @classmethod
    async def create(
        cls,
        config: Config = None,
        *,
        user_data_dir: PathLike = None,
        headless: bool = False,
        browser_executable_path: PathLike = None,
        browser_args: List[str] = None,
        sandbox: bool = True,
        host: str = None,
        port: int = None,
        **kwargs,
    ) -> Browser:
        """
        entry point for creating an instance
        """
        if not config:
            config = Config(
                user_data_dir=user_data_dir,
                headless=headless,
                browser_executable_path=browser_executable_path,
                browser_args=browser_args or [],
                sandbox=sandbox,
                host=host,
                port=port,
                **kwargs,
            )
        instance = cls(config)
        await instance.start()
        return instance

    def __init__(self, config: Config, **kwargs):
        """
        constructor. to create a instance, use :py:meth:`Browser.create(...)`

        :param config:
        """

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError(
                "{0} objects of this class are created using await {0}.create()".format(
                    self.__class__.__name__
                )
            )
        # weakref.finalize(self, self._quit, self)
        self.config = config

        """current targets (all types"""
        self.info = None
        self._target = None
        self._process = None
        self._process_pid = None
        self._keep_user_data_dir = None
        self._is_updating = asyncio.Event()
        self.connection: Connection = None
        self.stealth = None
        super().__init__("", auto_attach=False)
        logger.debug("Session object initialized: %s" % vars(self))

    @property
    def main_tab(self) -> tab.Tab:
        """returns the target which was launched with the browser"""
        return next(filter(lambda x: x.target.type_ == "page", self.targets))

    @property
    def targets(self) -> List[Connection]:
        return self._targets

    @property
    def tabs(self) -> List[tab.Connection]:
        return [x for x in self._targets if x.target.type_ == "page"]

    # @property
    # def tabs(self) -> List[tab.Tab]:
    #     """returns the current targets which are of type "page"
    #     :return:
    #     """
    #     tabs = filter(lambda item: item.type_ == "page", self.targets)
    #     return [tab.Tab(self, x) for x in tabs]
    #     # return list(tabs)

    @property
    def cookies(self) -> CookieJar:
        if not self._cookies:
            self._cookies = CookieJar(self)
        return self._cookies

    @property
    def stopped(self):
        if self._process and self._process.returncode is None:
            return False
        return True
        # return (self._process and self._process.returncode) or False

    async def wait(self, time: Union[float, int] = 0.1):
        """wait for <time> seconds. important to use, especially in between page navigation

        :param time:
        :return:
        """
        try:
            await asyncio.wait(
                [
                    asyncio.create_task(self.update_targets()),
                    asyncio.create_task(asyncio.sleep(time)),
                ],
                return_when=asyncio.ALL_COMPLETED,
            )
        except asyncio.TimeoutError:
            pass

    sleep = wait
    """alias for wait"""

    async def get(
        self, url="chrome://welcome", new_tab: bool = False, new_window: bool = False
    ) -> tab.Tab:
        """top level get. utilizes the first tab to retrieve given url.

        convenience function known from selenium.
        this function handles waits/sleeps and detects when DOM events fired, so it's the safest
        way of navigating.

        :param url: the url to navigate to
        :param new_tab: open new tab
        :param new_window:  open new window
        :return: Page
        """
        if new_tab or new_window:
            # creat new target using the browser session
            target_id = await self.send(
                cdp.target.create_target(
                    url, new_window=new_window, enable_begin_frame_control=True
                )
            )

            # connection = tab.Tab(target=target_id, parent=self, auto_attach=False)
            # await connection.attach()
            # self._targets.append(connection)
            await self.update_targets()
            connection = next(
                filter(lambda x: x.target.target_id == target_id, self.targets)
            )
            await connection.attach()

        else:
            # first tab from browser.tabs
            connection: tab.Tab = next(
                filter(lambda item: item.target.type_ == "page", self.targets)
            )

            frame_id, loader_id, *_ = await connection.send(cdp.page.navigate(url))
            await connection.attach()
            await self.update_targets()
        # await self
        return connection

    async def create_context(
        self,
        url: str = "chrome://welcome",
        new_tab: bool = False,
        new_window: bool = True,
        dispose_on_detach: bool = True,
        proxy_server: str = None,
        proxy_bypass_list: List[str] = None,
        origins_with_universal_network_access: List[str] = None,
        proxy_ssl_context=None,
    ) -> tab.Tab:
        """
        creates a new browser context - mostly useful if you want to use proxies for different browser instances
        since chrome usually can only use 1 proxy per browser.
        socks5 with authentication is supported by using a forwarder proxy, the
        correct string to use socks proxy with username/password auth is socks://USERNAME:PASSWORD@SERVER:PORT
        http/https proxies with authentication are also supported: http://USERNAME:PASSWORD@SERVER:PORT

        dispose_on_detach – (EXPERIMENTAL) (Optional) If specified, disposes this context when debugging session disconnects.
        proxy_server – (EXPERIMENTAL) (Optional) Proxy server, similar to the one passed to –proxy-server
        proxy_bypass_list – (EXPERIMENTAL) (Optional) Proxy bypass list, similar to the one passed to –proxy-bypass-list
        origins_with_universal_network_access – (EXPERIMENTAL) (Optional) An optional list of origins to grant unlimited cross-origin access to. Parts of the URL other than those constituting origin are ignored.
        proxy_ssl_context – (Optional) Custom SSL context for HTTPS proxy connections. If None, a default context is used.

        :param new_window:
        :type new_window:
        :param new_tab:
        :type new_tab:
        :param url:
        :type url:
        :param dispose_on_detach:
        :type dispose_on_detach:
        :param proxy_server:
        :type proxy_server:
        :param proxy_bypass_list:
        :type proxy_bypass_list:
        :param origins_with_universal_network_access:
        :type origins_with_universal_network_access:
        :param proxy_ssl_context:
        :type proxy_ssl_context: ssl.SSLContext
        :return:
        :rtype:
        """

        if proxy_server:
            fw = util.ProxyForwarder(
                proxy_server=proxy_server, ssl_context=proxy_ssl_context
            )
            proxy_server = fw.proxy_server

        ctx: cdp.browser.BrowserContextID = await self.send(
            cdp.target.create_browser_context(
                dispose_on_detach=dispose_on_detach,
                proxy_server=proxy_server,
                proxy_bypass_list=proxy_bypass_list,
                origins_with_universal_network_access=origins_with_universal_network_access,
            )
        )
        target_id: cdp.target.TargetID = await self.send(
            cdp.target.create_target(
                url, browser_context_id=ctx, new_window=new_window, for_tab=new_tab
            )
        )
        await self.sleep(0.5)
        connection: tab.Tab = next(
            filter(
                lambda item: item.target.type_ == "page"
                and item.target.target_id == target_id,
                self.targets,
            )
        )
        return connection

    async def start(self=None) -> Browser:
        """launches the actual browser"""

        if not self:
            raise RuntimeError(
                "use ``await Browser.create()`` to create a new instance"
            )

        if self._process or self._process_pid:
            if self._process.returncode is not None:
                return await self.create(config=self.config)
            raise RuntimeError("ignored! this call has no effect when already running.")

        # self.config.update(kwargs)
        connect_existing = False
        if self.config.host is not None and self.config.port is not None:
            connect_existing = True
        else:
            self.config.host = "127.0.0.1"
            self.config.port = util.free_port()

        if not connect_existing:
            logger.debug(
                "BROWSER EXECUTABLE PATH: %s", self.config.browser_executable_path
            )
            if not pathlib.Path(self.config.browser_executable_path).exists():
                raise FileNotFoundError(
                    (
                        """
                    ---------------------
                    Could not determine browser executable.
                    ---------------------
                    Make sure your browser is installed in the default location (path).
                    If you are sure about the browser executable, you can specify it using
                    the `browser_executable_path='{}` parameter."""
                    ).format(
                        "/path/to/browser/executable"
                        if is_posix
                        else "c:/path/to/your/browser.exe"
                    )
                )

        if getattr(self.config, "_extensions", None):  # noqa
            self.config.add_argument(
                "--load-extension=%s"
                % ",".join(str(_) for _ in self.config._extensions)
            )  # noqa

        exe = self.config.browser_executable_path
        params = self.config()

        logger.info(
            "starting\n\texecutable :%s\n\narguments:\n%s", exe, "\n\t".join(params)
        )
        if not connect_existing:
            self._process: asyncio.subprocess.Process = (
                await asyncio.create_subprocess_exec(
                    # self.config.browser_executable_path,
                    # *cmdparams,
                    exe,
                    *params,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    close_fds=is_posix,
                )
            )
            self._process_pid = self._process.pid
            self._owns_temp_profile = not self.config.uses_custom_data_dir
            # Armed before the first await after the spawn, so from here on the
            # browser cannot outlive this process however it ends (SIGKILL, a
            # crash, a wedged shutdown that is finally force-exited, ...).
            # Best-effort; a no-op where unsupported. See ``_exit_guard``.
            self._guard = _exit_guard.arm(
                self._process_pid,
                "--user-data-dir=%s" % self.config.user_data_dir,
                None
                if self.config.uses_custom_data_dir
                else str(self.config.user_data_dir),
            )

        self._http = HTTPApi((self.config.host, self.config.port))
        util.get_registered_instances().add(self)
        try:
            await self._connect()
        except GeneratorExit:
            # The coroutine is being closed (e.g. a pending task collected at
            # loop shutdown): nothing may be awaited any more, so only do the
            # synchronous half. The exit guard finishes the job.
            self._abort_now()
            raise
        except BaseException:
            # Failure *and* cancellation (e.g. an MCP client timing out and
            # cancelling the request) both land here. The browser is already
            # running and nobody else holds a reference to this half-started
            # instance, so unless it is torn down now it leaks: a live Chrome
            # (often showing an empty window) that nothing can ever stop.
            await self._abort_launch()
            raise

    async def _connect(self) -> None:
        """Connect to the freshly-spawned browser and apply the stealth baseline."""
        # Connect to the freshly-spawned Chrome's DevTools endpoint. The old
        # loop budgeted only ~2.75s (5 attempts x 0.5s), which is tight: a
        # cold Chrome typically binds its DevTools port in ~1-2s, leaving
        # <1s of headroom. Any system hiccup (Spotlight scan, antivirus
        # scan-on-execute, contended host, Chrome auto-update install) eats
        # the margin and surfaces as a spurious "Failed to connect to
        # browser" -- even though Chrome is fine and would have come up a
        # moment later.
        #
        # Strategy: exponential backoff (50ms doubling, capped at 1s)
        # against a wall-clock deadline of ~10s. Warm starts converge in a
        # few probes; cold/contended starts get a real chance to finish;
        # genuinely broken launches still fail in bounded time.
        deadline = asyncio.get_running_loop().time() + self._connect_timeout
        delay = 0.05
        last_exc: BaseException | None = None
        while True:
            try:
                self.info = ContraDict(await self._http.get("version"), silent=True)
                break
            except Exception as exc:
                last_exc = exc
                if asyncio.get_running_loop().time() + delay >= deadline:
                    logger.debug("could not start", exc_info=True)
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2, 1.0)

        if not self.info:
            raise Exception(
                (
                    """
                ---------------------
                Failed to connect to browser
                ---------------------
                One of the causes could be when you are running as root.
                In that case you need to pass no_sandbox=True 
                """
                )
            )

        self.websocket_url = self.info.webSocketDebuggerUrl
        await self.attach()
        await self.update_targets()
        await self._apply_stealth()
        # await self

    async def _abort_launch(self) -> None:
        """Tear down a browser whose launch failed or was cancelled.

        Called from an ``except BaseException`` handler, so it has to work while
        the surrounding task is being cancelled. Cancellation can interrupt any
        ``await`` -- and frameworks with level-triggered cancellation (anyio, as
        used by the MCP SDK) re-deliver it at *every* checkpoint -- so the part
        that actually stops the browser is synchronous and cannot be
        interrupted. Only the tidy-up (closing the CDP socket, reaping the
        process, deleting the profile) is asynchronous, and it runs in a task of
        its own.
        """
        self._abort_now()

        cleanup = asyncio.ensure_future(self._finish_abort())
        _background_tasks.add(cleanup)
        cleanup.add_done_callback(_background_tasks.discard)
        # If the caller is cancelled (again) while waiting, that cancellation
        # propagates -- it is never ours to swallow -- and the cleanup task
        # carries on without us.
        await asyncio.shield(cleanup)

    def _abort_now(self) -> None:
        """The synchronous, uninterruptible half of aborting a launch."""
        proc = self._process
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.terminate()
            # Escalate from a loop callback: it fires even when every task
            # involved has been cancelled.
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().call_later(
                    self._abort_kill_after, _kill_if_running, proc
                )
        # Also hand the job to the sidecar, which does not depend on this loop
        # (SIGTERM, then SIGKILL after a grace period, then the profile).
        self._release_guard()

    async def _finish_abort(self) -> None:
        try:
            await self.astop()
        except Exception:
            logger.debug("astop raised while aborting a launch", exc_info=True)
        util.get_registered_instances().discard(self)

    def _release_guard(self) -> None:
        """Stand the exit guard down (stop the browser first, see ``_exit_guard``)."""
        guard, self._guard = self._guard, None
        if guard is not None:
            guard.release()

    async def _remove_temp_profile(self) -> None:
        """Delete the ephemeral profile this instance created.

        Never touches a user-supplied ``user_data_dir``. Anything that cannot be
        removed now is retried by ``util.deconstruct_browser`` at interpreter
        exit, and by the exit guard if this process dies first.
        """
        if not self._owns_temp_profile:
            return
        self._owns_temp_profile = False
        path = getattr(getattr(self, "config", None), "user_data_dir", None)
        if not path:
            return
        for _ in range(5):
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                return
            except OSError:
                await asyncio.sleep(0.2)  # helper processes may still be flushing
            else:
                logger.info("removed temp profile %s", path)
                return
        logger.debug("could not remove temp profile %s", path)

    async def _apply_stealth(self) -> None:
        """Apply the engine-owned anti-detect stealth to the live browser.

        The engine owns every browser-altering anti-detect capability, so this
        runs on every launch. With no configured identity it still applies the
        always-on baseline (window.chrome shim, headless UA cleanup when
        headless, WebRTC leak protection when proxied). The resulting
        :class:`~mithwire.stealth.Stealth` is stored on ``self.stealth`` so a
        client can re-apply an identity later (e.g. once a proxy egress geo is
        resolved).
        """
        from ..stealth import Stealth

        config = self.config
        # The engine is agnostic of any client's proxy abstraction: proxy
        # presence is inferred purely from the launch flags.
        proxied = any(
            str(arg).startswith("--proxy-server=")
            for arg in (getattr(config, "_browser_args", None) or [])
        )
        stealth = Stealth(
            self,
            fingerprint=getattr(config, "fingerprint", None),
            webrtc_leak_protection=getattr(config, "webrtc_leak_protection", "auto"),
            headless=bool(getattr(config, "headless", False)),
            proxied=proxied,
            engine=getattr(config, "engine", "cdp"),
        )
        # A freshly attached tab needs a brief moment before CDP overrides and
        # new-document scripts reliably register on the about:blank target.
        await asyncio.sleep(1.2)
        try:
            await stealth.apply_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Anti-detect stealth application failed: %s", exc)
        self.stealth = stealth

    async def grant_all_permissions(self):
        """
        grant permissions for:
            accessibilityEvents
            audioCapture
            backgroundSync
            backgroundFetch
            clipboardReadWrite
            clipboardSanitizedWrite
            displayCapture
            durableStorage
            geolocation
            idleDetection
            localFonts
            midi
            midiSysex
            nfc
            notifications
            paymentHandler
            periodicBackgroundSync
            protectedMediaIdentifier
            sensors
            storageAccess
            topLevelStorageAccess
            videoCapture
            videoCapturePanTiltZoom
            wakeLockScreen
            wakeLockSystem
            windowManagement
        """
        permissions = list(cdp.browser.PermissionType)
        permissions.remove(cdp.browser.PermissionType.FLASH)
        permissions.remove(cdp.browser.PermissionType.CAPTURED_SURFACE_CONTROL)
        await self.send(cdp.browser.grant_permissions(permissions))

    async def tile_windows(self, windows=None, max_columns: int = 0):
        import math

        import mss

        m = mss.mss()
        screen, screen_width, screen_height = 3 * (None,)
        if m.monitors and len(m.monitors) >= 1:
            screen = m.monitors[0]
            screen_width = screen["width"]
            screen_height = screen["height"]
        if not screen or not screen_width or not screen_height:
            warnings.warn("no monitors detected")
            return
        await self
        distinct_windows = defaultdict(list)

        if windows:
            tabs = windows
        else:
            tabs = self.tabs
        for tab in tabs:
            window_id, bounds = await tab.get_window()
            distinct_windows[window_id].append(tab)

        num_windows = len(distinct_windows)
        req_cols = max_columns or int(num_windows * (19 / 6))
        req_rows = int(num_windows / req_cols)

        while req_cols * req_rows < num_windows:
            req_rows += 1

        box_w = math.floor((screen_width / req_cols) - 1)
        box_h = math.floor(screen_height / req_rows)

        distinct_windows_iter = iter(distinct_windows.values())
        grid = []
        for x in range(req_cols):
            for y in range(req_rows):
                num = x + y
                try:
                    tabs = next(distinct_windows_iter)
                except StopIteration:
                    continue
                if not tabs:
                    continue
                tab = tabs[0]

                try:
                    pos = [x * box_w, y * box_h, box_w, box_h]
                    grid.append(pos)
                    await tab.set_window_size(*pos)
                except Exception:
                    logger.info(
                        "could not set window size. exception => ", exc_info=True
                    )
                    continue
        return grid

    async def _get_targets(self) -> List[cdp.target.TargetInfo]:
        info = await self.send(cdp.target.get_targets())

        return info

    async def update_targets(self):

        targets = await self.send(cdp.target.get_targets())
        #
        # current_tabs_targets = [t.target for t in self.children]
        #
        for t in targets:
            for ctab in self._targets:
                if ctab.target.target_id == t.target_id:
                    ctab.target = t
                    break
            else:
                _t = tab.Tab(target=t, parent=self, auto_attach=False)
                self._targets.append(_t)

        for ctab in self._targets.copy():
            if ctab.target not in targets:
                self._targets.remove(ctab)

        await asyncio.sleep(0)

    def __iter__(self):
        self._i = self.tabs.index(self.main_tab)
        return self

    def __getitem__(
        self, item: Union[str, int, slice]
    ) -> Union[tab.Tab, List[tab.Tab], None]:
        """
        allows to get py:obj:`tab.Tab` instances by using browser[0], browser[1], etc.
        a string is also allowed. it will then return the first tab where the py:obj:`cdp.target.TargetInfo` object
        (as json string) contains the given key, or the first tab in case no matches are found. eg:
        `browser["google"]` gives the first tab which has "google" in it's serialized target object.

        :param item:
        :type item:
        :return:
        :rtype: tab.Tab
        """
        if isinstance(item, int):
            return self.tabs[item]
        elif isinstance(item, slice):
            tabs: List[tab.Tab] = []
            sta, sto, ste = item.start, item.stop, item.step
            if not ste:
                ste = 1
            if not sto:
                sto = len(self.tabs) - 1
            if not sta:
                sta = 0
            for x in range(sta, sto, ste):
                try:
                    tabs.append(self.tabs[x])
                except IndexError:
                    pass
            return tabs
        elif isinstance(item, tuple):
            r = range(*item)
            tabs: List[tab.Tab] = []
            for i in r:
                try:
                    tabs.append(self.tabs[i])
                except IndexError:
                    pass
            return tabs
        elif isinstance(item, str):
            for t in self.tabs:
                if item.lower() in str(t.target.to_json()).lower():
                    return t
            else:
                return self.tabs[0]

    def __reversed__(self):
        return reversed(list(self.tabs))

    def __next__(self) -> tab.Tab | tab.Connection | None:
        try:
            return self.tabs[self._i]
        except IndexError:
            del self._i
            raise StopIteration
        except AttributeError:
            del self._i
            raise StopIteration
        finally:
            if hasattr(self, "_i"):
                if self._i != len(self.tabs):
                    self._i += 1
                else:
                    del self._i

    async def astop(self) -> None:
        """Gracefully stop the browser, closing the CDP websocket and subprocess pipes.

        This is the preferred shutdown path in async contexts. It ensures no
        file descriptors are leaked by:
        1. Closing the CDP websocket via Connection.aclose()
        2. Terminating the browser process (with kill fallback on timeout)
        3. Closing subprocess pipes (stdin/stdout/stderr)
        4. Awaiting process exit to reap the zombie
        5. Deleting the ephemeral profile directory this instance created
           (a user-supplied ``user_data_dir`` is never touched)
        6. Unregistering the instance from the global exit-time registry
        """
        # 1. Close the CDP websocket connection
        try:
            await self.aclose()
        except Exception:
            logger.debug("aclose raised during astop", exc_info=True)

        # 2. Terminate (or kill) the browser process
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                logger.info(
                    "terminated browser with pid %d" % self._process.pid
                )
            except (ProcessLookupError, OSError):
                logger.debug("process already gone during terminate")

            # 3. Wait for exit with timeout; kill if stuck
            try:
                await asyncio.wait_for(self._process.wait(), timeout=self._stop_timeout)
            except asyncio.TimeoutError:
                try:
                    self._process.kill()
                    logger.info(
                        "killed browser with pid %d after timeout" % self._process.pid
                    )
                    await self._process.wait()
                except (ProcessLookupError, OSError):
                    pass

        # 4. Close subprocess pipes to release FDs. A failure here must never
        #    skip the steps below.
        try:
            self._close_pipes()
        except Exception:  # noqa: BLE001
            logger.debug("closing the subprocess pipes failed", exc_info=True)

        self._process = None
        self._process_pid = None

        # 5. The browser is gone: delete its ephemeral profile now instead of at
        #    interpreter exit (a long-lived process would otherwise pile up
        #    hundreds of MB per launch), then stand the exit guard down.
        await self._remove_temp_profile()
        self._release_guard()

        # 6. Done: stop pinning this instance. A long-lived host (an MCP server
        #    that launches hundreds of browsers) would otherwise keep every
        #    closed Browser, and its connection state, alive forever. Only
        #    reached when the steps above completed, so an interrupted stop
        #    stays registered for the exit-time sweep to retry.
        util.get_registered_instances().discard(self)

    def _close_pipes(self) -> None:
        """Close stdin/stdout/stderr pipes on the subprocess if open."""
        if self._process is None:
            return
        for attr in ("stdin", "stdout", "stderr"):
            pipe = getattr(self._process, attr, None)
            # stdin is a StreamWriter (has close()); stdout/stderr are
            # StreamReaders, which cannot be closed directly.
            close = getattr(pipe, "close", None)
            if close is not None:
                try:
                    close()
                except OSError:
                    pass
        # Release the read ends deterministically. Without this they only go
        # away once *every* process holding the write end (Chrome's helper
        # processes inherit it) has exited. Closing the transport of an
        # already-exited process is safe and idempotent.
        transport = getattr(self._process, "_transport", None)
        if transport is not None and self._process.returncode is not None:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                logger.debug("closing the subprocess transport failed", exc_info=True)

    def stop(self) -> None:
        """Stop the browser (sync API, backward-compatible).

        Prefers to delegate to astop() via the running event loop. Falls back
        to best-effort sync cleanup when no loop is available.
        """
        # Try to run the full async shutdown if a loop is running
        try:
            loop = asyncio.get_running_loop()
            future = asyncio.ensure_future(self.astop(), loop=loop)
            # If we're not inside a coroutine already awaiting, we can't block,
            # but at least the task is scheduled on the loop.
            logger.debug("scheduled astop() on running loop")
            # Give the task a chance to run if we're in a sync context that
            # will return to the loop (e.g. atexit handler during shutdown).
            return
        except RuntimeError:
            pass

        # No running loop — try asyncio.run() for a full clean shutdown
        try:
            asyncio.run(self.astop())
            logger.debug("closed the connection using asyncio.run()")
            return
        except RuntimeError:
            logger.debug("asyncio.run() failed, falling back to sync cleanup")

        # Last resort: best-effort sync cleanup
        self._close_pipes()
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                logger.info(
                    "terminated browser with pid %d (sync fallback)"
                    % self._process.pid
                )
            except (ProcessLookupError, OSError):
                pass
            try:
                self._process.kill()
            except (ProcessLookupError, OSError):
                pass
        self._process = None
        self._process_pid = None
        self._release_guard()

    def __await__(self):
        # return ( asyncio.sleep(0)).__await__()
        return self.update_targets().__await__()

    def __del__(self):
        pass


class CookieJar:
    def __init__(self, browser: Browser):
        self._browser = browser
        # self._connection = connection

    async def get_all(
        self, requests_cookie_format: bool = False
    ) -> List[Union[cdp.network.Cookie, "http.cookiejar.Cookie"]]:
        """
        get all cookies

        :param requests_cookie_format: when True, returns python http.cookiejar.Cookie objects, compatible  with requests library and many others.
        :type requests_cookie_format: bool
        :return:
        :rtype:

        """
        connection = None
        for tab in self._browser:
            if tab.target:
                connection = tab
                break

        else:
            connection = self._browser
        cookies = await connection.send(cdp.storage.get_cookies())
        if requests_cookie_format:
            import requests.cookies

            return [
                requests.cookies.create_cookie(
                    name=c.name,
                    value=c.value,
                    domain=c.domain,
                    path=c.path,
                    expires=c.expires,
                    secure=c.secure,
                )
                for c in cookies
            ]
        return cookies

    async def set_all(self, cookies: List[cdp.network.CookieParam]):
        """
        set cookies

        :param cookies: list of cookies
        :type cookies:
        :return:
        :rtype:
        """
        connection = None
        for tab in self._browser:
            if tab.target:
                connection = tab
                break

        else:
            connection = self._browser

        await connection.send(cdp.storage.set_cookies(cookies))

    async def save(self, file: PathLike = ".session.dat", pattern: str = ".*"):
        """
        save all cookies (or a subset, controlled by `pattern`) to a file to be restored later

        :param file:
        :type file:
        :param pattern: regex style pattern string.
               any cookie that has a  domain, key or value field which matches the pattern will be included.
               default (param not specified) = ".*"  (all)

               eg: the pattern "(cf|.com|nowsecure)" will include those cookies which:
                    - have a string "cf" (cloudflare)
                    - have ".com" in them, in either domain, key or value field.
                    - contain "nowsecure"
        :type pattern: str
        :return:
        :rtype:
        """
        import re

        pattern = re.compile(pattern)
        save_path = pathlib.Path(file).resolve()
        connection = None
        for tab in self._browser:
            if tab.target:
                connection = tab
                break
        else:
            connection = self._browser

        cookies = await self.get_all(requests_cookie_format=False)
        included_cookies = []
        for cookie in cookies:
            for match in pattern.finditer(str(cookie.__dict__)):
                logger.debug(
                    "saved cookie for matching pattern '%s' => (%s: %s)",
                    pattern.pattern,
                    cookie.name,
                    cookie.value,
                )
                included_cookies.append(cookie)
                break
        pickle.dump(cookies, save_path.open("w+b"))

    async def load(self, file: PathLike = ".session.dat", pattern: str = ".*"):
        """
        load all cookies (or a subset, controlled by `pattern`) from a file created by :py:meth:`~save_cookies`.

        :param file:
        :type file:
        :param pattern: regex style pattern string.
               any cookie that has a  domain, key or value field which matches the pattern will be included.
               default (param not specified)  = ".*"  (all)

               eg: the pattern "(cf|.com|nowsecure)" will include those cookies which:
                    - have a string "cf" (cloudflare)
                    - have ".com" in them, in either domain, key or value field.
                    - contain "nowsecure"
        :type pattern: str
        :return:
        :rtype:
        """
        import re

        pattern = re.compile(pattern)
        save_path = pathlib.Path(file).resolve()
        cookies = pickle.load(save_path.open("r+b"))
        included_cookies = []
        connection = None
        for tab in self._browser:
            if tab.target:
                connection = tab
                break

        else:
            connection = self._browser
        for cookie in cookies:
            for match in pattern.finditer(str(cookie.__dict__)):
                included_cookies.append(cookie)
                logger.debug(
                    "loaded cookie for matching pattern '%s' => (%s: %s)",
                    pattern.pattern,
                    cookie.name,
                    cookie.value,
                )
                break
        await connection.send(cdp.storage.set_cookies(included_cookies))

    async def clear(self):
        """
        clear current cookies

        note: this includes all open tabs/windows for this browser

        :return:
        :rtype:
        """
        connection = None
        for tab in self._browser:
            if tab.target:
                #     continue
                connection = tab
                break

        else:
            connection = self._browser

        await connection.send(cdp.storage.clear_cookies())


class HTTPApi:
    def __init__(self, addr: Tuple[str, int]):
        self.host, self.port = addr
        self.api = "http://%s:%d" % (self.host, self.port)

    @classmethod
    def from_target(cls, target: "Target"):
        ws_url = urllib.parse.urlparse(target.websocket_url)
        inst = cls((ws_url.hostname, ws_url.port))
        return inst

    async def get(self, endpoint: str):
        return await self._request(endpoint)

    async def post(self, endpoint, data):
        return await self._request(endpoint, data)

    async def _request(self, endpoint, method: str = "get", data: dict = None):
        url = urllib.parse.urljoin(
            self.api, f"json/{endpoint}" if endpoint else "/json"
        )
        if data and method.lower() == "get":
            raise ValueError("get requests cannot contain data")
        if not url:
            url = self.api + endpoint
        request = urllib.request.Request(url)
        request.method = method
        request.data = None
        if data:
            request.data = json.dumps(data).encode("utf-8")

        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: urllib.request.urlopen(request, timeout=10)
        )
        return json.loads(response.read())


class BrowserContext:
    def __init__(
        self,
        config: Config = None,
        *,
        user_data_dir: PathLike = None,
        headless: bool = False,
        browser_executable_path: PathLike = None,
        browser_args: List[str] = None,
        sandbox: bool = True,
        host: str = None,
        port: int = None,
        keep_open: bool = False,
        **kwargs,
    ):
        self._config = config
        self._user_data_dir = user_data_dir
        self._headless = headless
        self._browser_executable_path = browser_executable_path
        self._browser_args = browser_args
        self._sandbox = sandbox
        self._host = host
        self._port = port
        self._kwargs = kwargs
        self._instance: Browser = None
        self._keep_open = keep_open

    async def __aenter__(self):
        if not self._instance:
            self._instance = await Browser.create(
                self._config,
                user_data_dir=self._user_data_dir,
                headless=self._headless,
                browser_executable_path=self._browser_executable_path,
                browser_args=self._browser_args,
                sandbox=self._sandbox,
                host=self._host,
                port=self._port,
                **self._kwargs,
            )
        return self._instance

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not self._keep_open:
            await util.deconstruct_browser(self._instance)


atexit.register(util.deconstruct_browser)
