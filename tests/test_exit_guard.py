"""The exit guard keeps a browser from outliving its owner (ELE-174).

Nothing stops a plain child process when its parent is SIGKILLed or crashes, so
before the guard a hard-killed host left Chrome running forever. The sidecar
watches a lifeline pipe (closed by the kernel when the owner dies) and reaps the
browser -- but only ever the process it was told about, and only ever an
ephemeral ``uc_*`` profile.
"""
from __future__ import annotations

import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from mithwire.core import _exit_guard

POSIX = os.name == "posix"

# Starts a stand-in "browser", arms a guard for it, reports both PIDs, then just
# sits there until the test SIGKILLs it.
OWNER_SCRIPT = r"""
import importlib.util, subprocess, sys, time
spec = importlib.util.spec_from_file_location("exit_guard", sys.argv[1])
exit_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exit_guard)
needle, profile = sys.argv[2], sys.argv[3]
browser = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)", needle])
guard = exit_guard.arm(browser.pid, needle, profile or None, poll=0.05, grace=1.0)
print(browser.pid, guard.pid, flush=True)
time.sleep(300)
"""


def _alive(pid: int) -> bool:
    """Like ``kill(pid, 0)`` but a zombie counts as dead."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # Linux: no ``ps`` needed (minimal containers do not ship it)
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        pass
    try:  # macOS / BSD
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return True
    return bool(out) and not out.startswith("Z")


def _wait_until(predicate, timeout: float = 15.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


@unittest.skipUnless(POSIX, "the exit guard is POSIX-only")
class ExitGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.needle = "--user-data-dir=/mw-guard-test/%s" % uuid.uuid4().hex
        self._procs: list = []
        self.addCleanup(self._kill_procs)

    def _kill_procs(self) -> None:
        for proc in self._procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def _browser(self) -> subprocess.Popen:
        """A stand-in browser whose command line contains ``self.needle``."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)", self.needle]
        )
        self._procs.append(proc)
        return proc

    def _arm(self, browser: subprocess.Popen, cleanup_dir=None, needle=None):
        guard = _exit_guard.arm(
            browser.pid, needle or self.needle, cleanup_dir, poll=0.05, grace=1.0
        )
        self.assertIsNotNone(guard, "the guard should be available on POSIX")
        self.addCleanup(guard.release)
        return guard

    # ------------------------------------------------------------------ #
    def test_browser_is_reaped_when_the_owner_is_sigkilled(self) -> None:
        profile = self.tmp / ("uc_" + uuid.uuid4().hex[:8])
        (profile / "Default").mkdir(parents=True)
        (profile / "Default" / "Cookies").write_text("x")

        owner = subprocess.Popen(
            [sys.executable, "-c", OWNER_SCRIPT, _exit_guard.__file__, self.needle, str(profile)],
            stdout=subprocess.PIPE,
            text=True,
        )
        self._procs.append(owner)
        browser_pid, guard_pid = (int(x) for x in owner.stdout.readline().split())
        self.addCleanup(lambda: _alive(browser_pid) and os.kill(browser_pid, 9))
        self.assertTrue(_alive(browser_pid))

        owner.kill()  # no atexit, no finally, no signal handler: a hard kill
        owner.wait()

        self.assertTrue(_wait_until(lambda: not _alive(browser_pid)), "browser outlived its owner")
        self.assertTrue(_wait_until(lambda: not _alive(guard_pid)), "the sidecar must exit too")
        self.assertFalse(profile.exists(), "the ephemeral profile must be removed")

    def test_clean_release_after_a_stop_exits_without_touching_anything(self) -> None:
        browser = self._browser()
        guard = self._arm(browser)

        browser.terminate()  # the owner's normal stop...
        browser.wait()
        guard.release()  # ...and only then stands the guard down

        self.assertEqual(guard._process.wait(timeout=10), 0)
        self.assertFalse(guard.armed)
        guard.release()  # idempotent

    def test_sidecar_exits_by_itself_when_the_browser_dies_first(self) -> None:
        browser = self._browser()
        guard = self._arm(browser)

        browser.kill()
        browser.wait()

        # Owner is still alive and never released: the sidecar must not linger.
        self.assertEqual(guard._process.wait(timeout=10), 0)

    def test_a_process_that_is_not_the_browser_is_never_signalled(self) -> None:
        # A recycled PID: alive, but its command line does not contain the needle.
        victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        self._procs.append(victim)
        guard = self._arm(victim)

        guard.release()  # == "the owner died"
        self.assertEqual(guard._process.wait(timeout=10), 0)

        self.assertIsNone(victim.poll(), "the guard killed a process that is not its browser")

    def test_only_ephemeral_profiles_are_deleted(self) -> None:
        precious = self.tmp / "important-profile"
        precious.mkdir()
        browser = self._browser()
        guard = self._arm(browser, cleanup_dir=str(precious))

        browser.kill()
        browser.wait()
        guard.release()

        self.assertEqual(guard._process.wait(timeout=10), 0)
        self.assertTrue(precious.is_dir(), "a directory without the uc_ prefix must be kept")

    def test_an_ephemeral_profile_is_deleted_once_the_browser_is_gone(self) -> None:
        profile = self.tmp / "uc_deadbeef"
        profile.mkdir()
        (profile / "junk").write_text("x")
        browser = self._browser()
        guard = self._arm(browser, cleanup_dir=str(profile))

        guard.release()  # owner "died" with the browser still running

        self.assertEqual(guard._process.wait(timeout=10), 0)
        self.assertTrue(_wait_until(lambda: browser.poll() is not None), "browser must be stopped")
        self.assertFalse(profile.exists())

    def test_the_sidecar_does_not_inherit_the_owners_descriptors(self) -> None:
        # An MCP server's stdio pipes are exactly this: inheritable descriptors
        # the owner must be able to close. If the sidecar held one open, the
        # server's death would be invisible to its client.
        read_end, write_end = os.pipe()
        os.set_inheritable(write_end, True)
        self.addCleanup(os.close, read_end)
        browser = self._browser()
        self._arm(browser)

        os.close(write_end)
        ready, _, _ = select.select([read_end], [], [], 5)

        self.assertTrue(ready, "the sidecar inherited the owner's pipe and kept it open")

    def test_disabled_by_environment(self) -> None:
        with patch.dict(os.environ, {_exit_guard.DISABLE_ENV: "1"}):
            self.assertIsNone(_exit_guard.arm(os.getpid(), self.needle))

    def test_never_raises_when_the_sidecar_cannot_start(self) -> None:
        with patch.object(_exit_guard.subprocess, "Popen", side_effect=OSError("no fork")):
            self.assertIsNone(_exit_guard.arm(os.getpid(), self.needle))


class ExitGuardPortabilityTest(unittest.TestCase):
    def test_arm_is_a_noop_off_posix(self) -> None:
        with patch.object(_exit_guard.os, "name", "nt"):
            self.assertIsNone(_exit_guard.arm(os.getpid(), "x"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
