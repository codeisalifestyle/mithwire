"""A failed or cancelled launch must not leave a browser behind (ELE-174).

``Browser.start()`` spawns Chrome and *then* connects, attaches and applies
stealth. If anything after the spawn raised -- or the task was cancelled, e.g.
an MCP client timing out and sending ``notifications/cancelled`` -- the browser
process (often with an empty window) was orphaned for good: nothing held a
reference to the half-started instance, so nothing could ever stop it, and its
ephemeral profile (~100 MB each) was never deleted.

These tests use a real subprocess as the "browser" (a script that answers
``--version`` and otherwise just sleeps, never opening a DevTools port), so they
verify actual process death rather than mock calls.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mithwire.core import _exit_guard, util
from mithwire.core.browser import Browser
from mithwire.core.config import Config

FAKE_BROWSER = """#!/bin/sh
case "$1" in
  --version) echo "Google Chrome 130.0.6723.58"; exit 0 ;;
esac
exec "{python}" -c "import time; time.sleep(300)" "$@"
"""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.05) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


@unittest.skipUnless(os.name == "posix", "uses a POSIX shell script as the fake browser")
class LaunchAbortTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.exe = Path(self._tmp.name) / "fake-chrome"
        self.exe.write_text(FAKE_BROWSER.format(python=sys.executable))
        self.exe.chmod(0o755)

        # Record every browser process the engine spawns.
        self.spawned: list = []
        real_spawn = asyncio.create_subprocess_exec

        async def spy(*args, **kwargs):
            proc = await real_spawn(*args, **kwargs)
            self.spawned.append(proc)
            return proc

        spawn_patch = patch("asyncio.create_subprocess_exec", spy)
        spawn_patch.start()
        self.addCleanup(spawn_patch.stop)
        self.addCleanup(self._kill_strays)

        # Don't wait the production 10 s for a DevTools port that never opens.
        timeout_patch = patch.object(Browser, "_connect_timeout", 0.4)
        timeout_patch.start()
        self.addCleanup(timeout_patch.stop)

    def _kill_strays(self) -> None:
        for proc in self.spawned:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    def _config(self, **kwargs) -> Config:
        return Config(browser_executable_path=str(self.exe), headless=True, **kwargs)

    async def _assert_browser_gone(self, proc) -> None:
        await asyncio.wait_for(proc.wait(), 10)
        self.assertFalse(_pid_alive(proc.pid), "the browser process must be dead")

    # ------------------------------------------------------------------ #
    async def test_failed_connect_terminates_the_spawned_process(self) -> None:
        config = self._config()
        profile = Path(config.user_data_dir)
        self.assertTrue(profile.is_dir())

        with self.assertRaisesRegex(Exception, "Failed to connect"):
            await Browser.create(config)

        self.assertEqual(len(self.spawned), 1)
        await self._assert_browser_gone(self.spawned[0])
        self.assertFalse(profile.exists(), "the ephemeral profile must be deleted")

    async def test_cancelled_launch_terminates_the_spawned_process(self) -> None:
        config = self._config()
        profile = Path(config.user_data_dir)

        with patch.object(Browser, "_connect_timeout", 60.0):
            task = asyncio.ensure_future(Browser.create(config))
            self.assertTrue(await _wait_until(lambda: self.spawned))
            await asyncio.sleep(0.2)  # now inside the connect retry loop
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        await self._assert_browser_gone(self.spawned[0])
        self.assertFalse(profile.exists())

    async def test_launch_cleanup_survives_level_triggered_cancellation(self) -> None:
        """anyio (used by the MCP SDK) re-delivers cancellation at *every*
        checkpoint, not just once. The browser must still be stopped."""
        config = self._config()
        profile = Path(config.user_data_dir)

        with patch.object(Browser, "_connect_timeout", 60.0):
            task = asyncio.ensure_future(Browser.create(config))
            self.assertTrue(await _wait_until(lambda: self.spawned))
            await asyncio.sleep(0.2)

            async def hammer() -> None:
                while not task.done():
                    task.cancel()
                    await asyncio.sleep(0)

            hammer_task = asyncio.ensure_future(hammer())
            with self.assertRaises(asyncio.CancelledError):
                await task
            await hammer_task

        await self._assert_browser_gone(self.spawned[0])
        # The tidy-up runs in its own task, so it finishes even though the
        # launching task was being cancelled underneath it.
        self.assertTrue(await _wait_until(lambda: not profile.exists()))

    async def test_a_closed_launch_coroutine_still_stops_the_browser(self) -> None:
        """A coroutine that is *closed* while suspended (``GeneratorExit``, e.g. a
        pending task collected at loop shutdown) cannot await anything. The
        synchronous half must still terminate the browser, and the exit guard
        -- which does not need the loop -- must still remove the profile."""
        config = self._config()
        profile = Path(config.user_data_dir)

        with patch.object(Browser, "_connect_timeout", 60.0):
            coro = Browser.create(config)
            # Drive it by hand (as a Task would) to its first suspension after
            # the spawn, i.e. somewhere inside the connect retry loop.
            step = coro.send(None)
            while not self.spawned:
                if step is None:  # a bare ``await asyncio.sleep(0)``
                    await asyncio.sleep(0)
                else:
                    step._asyncio_future_blocking = False  # what Task.__step does
                    await step
                step = coro.send(None)
            coro.close()  # raises GeneratorExit at the suspension point

        await self._assert_browser_gone(self.spawned[0])
        self.assertTrue(await _wait_until(lambda: not profile.exists()))

    async def test_aborted_launch_is_not_kept_registered(self) -> None:
        before = set(util.get_registered_instances())
        with self.assertRaises(Exception):
            await Browser.create(self._config())
        self.assertEqual(set(util.get_registered_instances()), before)

    async def test_a_user_supplied_profile_is_never_deleted(self) -> None:
        custom = Path(self._tmp.name) / "my-profile"
        custom.mkdir()
        (custom / "Cookies").write_text("precious")

        with self.assertRaises(Exception):
            await Browser.create(self._config(user_data_dir=str(custom)))

        await self._assert_browser_gone(self.spawned[0])
        self.assertEqual((custom / "Cookies").read_text(), "precious")

    async def test_attaching_to_an_existing_browser_never_spawns_or_kills(self) -> None:
        # host+port => "connect to a browser somebody else started".
        config = self._config(host="127.0.0.1", port=util.free_port())
        with self.assertRaises(Exception):
            await Browser.create(config)
        self.assertEqual(self.spawned, [])

    async def test_exit_guard_is_armed_after_the_spawn_and_released_on_failure(self) -> None:
        guards: list = []
        real_arm = _exit_guard.arm

        def spy_arm(*args, **kwargs):
            guard = real_arm(*args, **kwargs)
            guards.append(guard)
            return guard

        with patch.object(_exit_guard, "arm", spy_arm), self.assertRaises(Exception):
            await Browser.create(self._config())

        self.assertEqual(len(guards), 1)
        guard = guards[0]
        self.assertIsNotNone(guard, "the guard should be available on POSIX")
        self.assertFalse(guard.armed, "a failed launch must stand the guard down")
        self.assertEqual(guard._process.wait(timeout=10), 0, "the sidecar must exit")


class AstopProfileTest(unittest.IsolatedAsyncioTestCase):
    """``astop()`` deletes the ephemeral profile it owns -- and nothing else."""

    def _bare_browser(self, profile: Path, *, owns: bool) -> Browser:
        with patch.object(Browser, "__init__", lambda self, *a, **kw: None):
            browser = Browser.__new__(Browser)
        browser._process = None
        browser._process_pid = None
        browser.config = SimpleNamespace(user_data_dir=str(profile), uses_custom_data_dir=not owns)
        browser._owns_temp_profile = owns
        browser.aclose = AsyncMock()
        return browser

    async def test_astop_removes_an_owned_ephemeral_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "uc_abc12345"
            (profile / "Default").mkdir(parents=True)
            (profile / "Default" / "Cookies").write_text("x")
            browser = self._bare_browser(profile, owns=True)

            await browser.astop()

            self.assertFalse(profile.exists())
            await browser.astop()  # idempotent

    async def test_astop_leaves_a_profile_it_does_not_own(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "my-profile"
            profile.mkdir()
            browser = self._bare_browser(profile, owns=False)

            await browser.astop()

            self.assertTrue(profile.is_dir())

    async def test_astop_completes_with_real_asyncio_pipes(self) -> None:
        """Regression: stdout/stderr are StreamReaders, which have no close().

        ``_close_pipes`` used to call ``.close()`` on all three and raised
        AttributeError, so everything after it in ``astop()`` -- resetting the
        process handle, deleting the profile -- silently never ran. Fakes whose
        pipes all had close() hid it; this uses a real subprocess.
        """
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "uc_realpipe"
            profile.mkdir()
            browser = self._bare_browser(profile, owns=True)
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time; time.sleep(300)",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.addAsyncCleanup(self._reap, proc)
            browser._process = proc
            browser._process_pid = proc.pid

            await browser.astop()

            self.assertIsNotNone(proc.returncode, "the process must have been stopped")
            self.assertIsNone(browser._process)
            self.assertIsNone(browser._process_pid)
            self.assertTrue(proc.stdin.is_closing())
            self.assertFalse(profile.exists())

    async def test_close_pipes_accepts_real_stream_readers(self) -> None:
        """``_close_pipes`` itself must cope with real StreamReader stdout/stderr."""
        browser = self._bare_browser(Path(tempfile.gettempdir()) / "unused", owns=False)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "pass",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        browser._process = proc

        browser._close_pipes()  # used to raise AttributeError on the StreamReaders

        self.assertTrue(proc.stdin.is_closing())
        transport = getattr(proc, "_transport", None)
        if transport is not None:  # releases the read ends deterministically
            self.assertTrue(transport.is_closing())

    async def test_close_pipes_does_not_kill_a_live_process(self) -> None:
        """Closing the transport of a running child would kill it -- never do that."""
        browser = self._bare_browser(Path(tempfile.gettempdir()) / "unused", owns=False)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(300)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.addAsyncCleanup(self._reap, proc)
        browser._process = proc

        browser._close_pipes()
        await asyncio.sleep(0.2)

        self.assertIsNone(proc.returncode, "a live process must be left alone")

    @staticmethod
    async def _reap(proc: asyncio.subprocess.Process) -> None:
        """Kill, wait for and fully release a test subprocess (transport included)."""
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
