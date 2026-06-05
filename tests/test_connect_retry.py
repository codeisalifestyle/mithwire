"""Regression tests for ``Browser.start``'s DevTools-port connect loop.

This is the first test in the engine repo after the monorepo split. It
covers a reforged-only fix to the upstream connect path:

The historical loop budgeted only ~2.75s (5 attempts x 0.5s sleep). Real
cold Chrome starts cluster around 1.9-2.1s, leaving <1s of headroom that
any system hiccup (Spotlight scan, antivirus scan-on-execute, contended
host, Chrome auto-update) easily eats -- surfacing as a spurious "Failed
to connect to browser" even though Chrome is fine and would have come up
a moment later. The loop now uses exponential backoff against a ~10s
wall-clock deadline. These tests pin that behavior without launching a
real browser.
"""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

import nodriver as uc
import nodriver.core.browser as browser_mod


class _FakeProc:
    """Stands in for the asyncio.subprocess.Process Chrome would normally be."""

    def __init__(self) -> None:
        self.pid = 99999
        self.returncode = None

    def terminate(self) -> None:  # pragma: no cover - never escalated in tests
        self.returncode = 0

    def kill(self) -> None:  # pragma: no cover
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _http_api_failing_n_times(n: int):
    """Build an HTTPApi-shaped class that ConnRefuses N times, then returns info."""

    class _FailNThenOk(browser_mod.HTTPApi):
        calls = 0

        async def get(self, endpoint: str):  # type: ignore[override]
            type(self).calls += 1
            if type(self).calls <= n:
                raise ConnectionRefusedError("simulated: port not yet bound")
            # Minimum dict shape that Browser.start consumes.
            return {
                "webSocketDebuggerUrl": (
                    "ws://127.0.0.1:9999/devtools/browser/fake"
                ),
                "Browser": "Chrome/0.0.0.0",
                "Protocol-Version": "1.3",
                "User-Agent": "test",
            }

    return _FailNThenOk


class _AlwaysFailHTTPApi(browser_mod.HTTPApi):
    async def get(self, endpoint: str):  # type: ignore[override]
        raise ConnectionRefusedError("simulated: port never binds")


def _patches():
    """Patches that neutralize every side effect after the connect loop."""
    return (
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_FakeProc()),
        ),
        # `attach()` opens a CDP websocket; `update_targets()` issues CDP calls.
        # Neither is the unit under test; stub both to no-ops.
        patch.object(browser_mod.Browser, "attach", AsyncMock()),
        patch.object(browser_mod.Browser, "update_targets", AsyncMock()),
    )


class ConnectRetryTest(unittest.IsolatedAsyncioTestCase):
    """The new connect loop tolerates a slow port-bind without raising."""

    async def test_succeeds_after_slow_port_bind(self) -> None:
        # Chrome's port binds on the 6th probe -- well past the old 5-attempt
        # ceiling (which would have raised) but inside the new ~10s deadline.
        stub = _http_api_failing_n_times(5)
        config = uc.Config(headless=True, sandbox=True)

        sub_patch, attach_patch, update_patch = _patches()
        with sub_patch, attach_patch, update_patch, \
             patch.object(browser_mod, "HTTPApi", stub):
            t0 = time.monotonic()
            browser = await browser_mod.Browser.create(config)
            elapsed = time.monotonic() - t0

        self.assertIsNotNone(
            browser.info,
            "info must be set when the port eventually responds",
        )
        # 6 probes with 50/100/200/400/800/<get> backoff => ~1.55s ideal.
        # Allow generous slack for scheduler jitter / CI noise.
        self.assertLess(
            elapsed,
            5.0,
            f"connect loop took {elapsed:.2f}s; expected <5s for 6 probes",
        )

    async def test_persistent_failure_raises_within_budget(self) -> None:
        # Port never binds -- loop must give up and raise the same wrapped
        # "Failed to connect to browser" the engine has always raised, and do
        # so within a bounded (~10-12s) wall-clock budget.
        config = uc.Config(headless=True, sandbox=True)

        sub_patch, attach_patch, update_patch = _patches()
        with sub_patch, attach_patch, update_patch, \
             patch.object(browser_mod, "HTTPApi", _AlwaysFailHTTPApi):
            t0 = time.monotonic()
            with self.assertRaises(Exception) as ctx:
                await browser_mod.Browser.create(config)
            elapsed = time.monotonic() - t0

        self.assertIn("Failed to connect to browser", str(ctx.exception))
        # Deadline is 10s; allow 3s slack for scheduler/teardown overhead.
        self.assertLess(
            elapsed,
            13.0,
            f"connect loop ran for {elapsed:.2f}s; deadline was ~10s",
        )

    async def test_immediate_success_is_fast(self) -> None:
        # Warm path: first probe wins. Must not be artificially delayed.
        stub = _http_api_failing_n_times(0)
        config = uc.Config(headless=True, sandbox=True)

        sub_patch, attach_patch, update_patch = _patches()
        with sub_patch, attach_patch, update_patch, \
             patch.object(browser_mod, "HTTPApi", stub):
            t0 = time.monotonic()
            browser = await browser_mod.Browser.create(config)
            elapsed = time.monotonic() - t0

        self.assertIsNotNone(browser.info)
        # No initial fixed sleep, no retry sleeps -- should be <250ms even on
        # a contended CI box.
        self.assertLess(
            elapsed,
            0.5,
            f"warm connect took {elapsed:.2f}s; should be near-instant",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
