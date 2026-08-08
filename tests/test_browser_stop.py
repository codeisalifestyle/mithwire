"""Tests for Browser.stop() / Browser.astop() FD-leak fix (ELE-81).

Verifies that:
- astop() properly closes the CDP websocket (Connection.aclose)
- astop() properly closes subprocess pipes (stdin/stdout/stderr)
- astop() waits for the process to exit
- stop() still works for sync callers (backward compatibility)
- Edge cases: stop/astop when browser already stopped or process dead
- FD count doesn't grow when starting/stopping in a loop
"""
from __future__ import annotations

import asyncio
import unittest
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import mithwire.core.browser as browser_mod


class _FakeStreamWriter:
    """Mimics an asyncio.StreamWriter (subprocess pipe) for testing."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    """Mimics asyncio.subprocess.Process for stop/astop tests."""

    def __init__(self, *, already_dead: bool = False) -> None:
        self.pid = 12345
        self.returncode = 0 if already_dead else None
        self.stdin = _FakeStreamWriter()
        self.stdout = _FakeStreamWriter()
        self.stderr = _FakeStreamWriter()
        self._terminated = False
        self._killed = False

    def terminate(self) -> None:
        self._terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self._killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


class _HangingProcess(_FakeProcess):
    """Process whose wait() hangs until killed."""

    def __init__(self) -> None:
        super().__init__()
        self._killed_event = asyncio.Event()

    def kill(self) -> None:
        super().kill()
        self._killed_event.set()

    async def wait(self) -> int:
        if self.returncode is None or self.returncode == -15:
            # Simulate a process that doesn't exit on terminate
            await self._killed_event.wait()
        return self.returncode or 0


def _make_browser_instance() -> browser_mod.Browser:
    """Create a Browser without running the real __init__ or start()."""
    # Bypass __init__'s event loop check by patching
    with patch.object(browser_mod.Browser, "__init__", lambda self, *a, **kw: None):
        b = browser_mod.Browser.__new__(browser_mod.Browser)
    # Set minimal required attributes
    b._process = _FakeProcess()
    b._process_pid = b._process.pid
    b.connection = None
    b.socket = None
    b._targets = []
    b._listener_task = None
    b._mapper = {}
    b.websocket_url = "ws://127.0.0.1:9999/devtools/browser/fake"
    return b


class AstopTest(unittest.IsolatedAsyncioTestCase):
    """Tests for the new async astop() method."""

    async def test_astop_closes_websocket(self) -> None:
        b = _make_browser_instance()
        b.aclose = AsyncMock()

        await b.astop()

        b.aclose.assert_awaited_once()

    async def test_astop_terminates_process(self) -> None:
        b = _make_browser_instance()
        b.aclose = AsyncMock()

        proc = b._process
        await b.astop()

        self.assertTrue(proc._terminated)
        self.assertIsNone(b._process)
        self.assertIsNone(b._process_pid)

    async def test_astop_closes_pipes(self) -> None:
        b = _make_browser_instance()
        b.aclose = AsyncMock()

        proc = b._process
        await b.astop()

        self.assertTrue(proc.stdin.closed)
        self.assertTrue(proc.stdout.closed)
        self.assertTrue(proc.stderr.closed)

    async def test_astop_kills_on_timeout(self) -> None:
        b = _make_browser_instance()
        b._process = _HangingProcess()
        b._stop_timeout = 0.1  # short timeout for test speed
        b.aclose = AsyncMock()

        proc = b._process
        await b.astop()

        self.assertTrue(proc._killed)

    async def test_astop_already_stopped(self) -> None:
        """astop() on already-stopped browser doesn't raise."""
        b = _make_browser_instance()
        b._process = _FakeProcess(already_dead=True)
        b.aclose = AsyncMock()

        # Should not raise
        await b.astop()
        # Pipes still closed even if process is dead
        self.assertTrue(b._process is None)

    async def test_astop_no_process(self) -> None:
        """astop() with _process=None doesn't raise."""
        b = _make_browser_instance()
        b._process = None
        b.aclose = AsyncMock()

        await b.astop()  # should not raise

    async def test_astop_aclose_exception_doesnt_prevent_cleanup(self) -> None:
        """If aclose() raises, pipes and process are still cleaned up."""
        b = _make_browser_instance()
        b.aclose = AsyncMock(side_effect=RuntimeError("websocket boom"))

        proc = b._process
        await b.astop()

        self.assertTrue(proc._terminated)
        self.assertTrue(proc.stdin.closed)
        self.assertTrue(proc.stdout.closed)
        self.assertTrue(proc.stderr.closed)


class SyncStopTest(unittest.TestCase):
    """Tests for backward-compatible sync stop()."""

    def test_stop_cleans_up_process(self) -> None:
        """stop() in a sync context closes pipes and terminates."""
        b = _make_browser_instance()
        b.aclose = AsyncMock()

        b.stop()

        # asyncio.run(astop()) path should have cleaned up
        self.assertIsNone(b._process)
        self.assertIsNone(b._process_pid)

    def test_stop_already_stopped(self) -> None:
        """stop() when process is already dead doesn't raise."""
        b = _make_browser_instance()
        b._process = _FakeProcess(already_dead=True)
        b.aclose = AsyncMock()

        b.stop()  # should not raise


class StopInLoopTest(unittest.IsolatedAsyncioTestCase):
    """Simulate repeated start/stop to verify no FD accumulation."""

    async def test_repeated_astop_no_leak(self) -> None:
        """Calling astop() many times doesn't accumulate unclosed resources."""
        pipes_closed = 0

        for _ in range(50):
            b = _make_browser_instance()
            b.aclose = AsyncMock()
            proc = b._process

            await b.astop()

            if proc.stdin.closed:
                pipes_closed += 1

        # All 50 iterations should have closed pipes
        self.assertEqual(pipes_closed, 50)


class ClosePipesTest(unittest.TestCase):
    """Unit tests for _close_pipes helper."""

    def test_close_pipes_with_none_process(self) -> None:
        b = _make_browser_instance()
        b._process = None
        b._close_pipes()  # should not raise

    def test_close_pipes_with_none_pipe(self) -> None:
        b = _make_browser_instance()
        b._process.stdin = None
        b._close_pipes()  # should not raise

    def test_close_pipes_oserror_suppressed(self) -> None:
        b = _make_browser_instance()
        b._process.stdin.close = MagicMock(side_effect=OSError("already closed"))
        b._close_pipes()  # should not raise


if __name__ == "__main__":
    unittest.main()
