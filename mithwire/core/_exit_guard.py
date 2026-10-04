"""Exit guard: a browser must never outlive the process that launched it.

Chrome is launched as a plain child process. If its owner is killed without a
chance to clean up (``kill -9``, a crash, an OOM kill, a shutdown that wedged
and was finally force-exited) nothing stops the browser: macOS and Windows have
no parent-death signal, so it keeps running -- window and all -- until reboot,
and its ephemeral profile directory is never deleted.

The guard closes that gap with a tiny stdlib-only sidecar process:

* the owner keeps the write end of a *lifeline* pipe, the sidecar blocks on the
  read end. The kernel closes the write end when the owner dies for **any**
  reason, which the sidecar sees as EOF immediately -- no polling of the owner
  and therefore no PID-reuse race;
* on EOF the sidecar terminates the browser (SIGTERM, then SIGKILL after a
  grace period) -- but only after verifying that the PID still belongs to *that*
  browser -- and deletes the browser's ephemeral profile directory;
* a clean stop simply closes the lifeline *after* stopping the browser, so the
  sidecar finds nothing left to do and exits.

POSIX only. Elsewhere, and when ``MITHWIRE_NO_EXIT_GUARD`` is set, :func:`arm`
is a no-op and behaviour is exactly what it was before the guard existed.

This file doubles as the sidecar's entry point (``python -I -S _exit_guard.py``)
so it must keep importing nothing but the standard library.
"""

from __future__ import annotations

import contextlib
import logging
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)

DISABLE_ENV = "MITHWIRE_NO_EXIT_GUARD"

#: Only directories with this prefix (mithwire's ephemeral profiles, see
#: ``config.temp_profile_dir``) are ever deleted by the sidecar.
TEMP_PROFILE_PREFIX = "uc_"

#: How often the sidecar notices the browser exiting by itself.
POLL_SECONDS = 1.0
#: How long the browser gets to exit after SIGTERM before SIGKILL.
TERM_GRACE_SECONDS = 4.0


# --------------------------------------------------------------------------- #
# Owner side
# --------------------------------------------------------------------------- #
class ExitGuard:
    """Handle the owner keeps for a running sidecar."""

    def __init__(self, process: "subprocess.Popen[bytes]", lifeline: int) -> None:
        self._process = process
        self._lifeline: Optional[int] = lifeline

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def armed(self) -> bool:
        return self._lifeline is not None

    def release(self) -> None:
        """Stand the guard down by closing the lifeline.

        Stop the browser *first*: if it is still running when the lifeline
        closes, the sidecar assumes the owner died and terminates it. That makes
        ``release()`` a safe last resort after a stop that may have failed.
        Idempotent.
        """
        lifeline, self._lifeline = self._lifeline, None
        if lifeline is not None:
            with contextlib.suppress(OSError):
                os.close(lifeline)
        # Reap the sidecar if it has already exited (avoids a zombie); if it is
        # still winding down, subprocess reaps it on the next Popen creation.
        with contextlib.suppress(Exception):
            self._process.poll()


def _disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def arm(
    target_pid: int,
    needle: str,
    cleanup_dir: Optional[str] = None,
    *,
    poll: float = POLL_SECONDS,
    grace: float = TERM_GRACE_SECONDS,
) -> Optional[ExitGuard]:
    """Start a sidecar that kills ``target_pid`` if this process disappears.

    :param target_pid: the browser's PID (must be a child of this process).
    :param needle: a string that appears in the browser's command line (its
        ``--user-data-dir=...``); the sidecar refuses to signal a PID whose
        command line does not contain it, so a recycled PID is never harmed.
    :param cleanup_dir: an ephemeral profile directory to delete once the
        browser is gone. Never pass a user-owned directory.
    :returns: the owner-side handle, or ``None`` when the guard is unavailable
        or disabled (never raises).
    """
    if os.name != "posix" or _disabled() or not needle or not sys.executable:
        return None
    try:
        read_fd, write_fd = os.pipe()  # non-inheritable by default (PEP 446)
    except OSError:
        logger.debug("exit guard: no pipe available", exc_info=True)
        return None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                os.path.abspath(__file__),
                str(read_fd),
                str(int(target_pid)),
                needle,
                cleanup_dir or "",
                repr(float(poll)),
                repr(float(grace)),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            # Only the read end is handed down. The sidecar must never inherit
            # the owner's other descriptors (e.g. an MCP server's stdio pipes:
            # holding them open would hide the owner's death from its client).
            pass_fds=(read_fd,),
            # Its own session: terminal hang-ups and group signals aimed at the
            # owner must not take the sidecar down with it.
            start_new_session=True,
        )
    except Exception:  # noqa: BLE001 - the guard is strictly best-effort
        logger.debug("exit guard: could not start the sidecar", exc_info=True)
        with contextlib.suppress(OSError):
            os.close(write_fd)
        return None
    finally:
        with contextlib.suppress(OSError):
            os.close(read_fd)
    return ExitGuard(process, write_fd)


# --------------------------------------------------------------------------- #
# Sidecar side (runs as a separate process; stdlib only)
# --------------------------------------------------------------------------- #
def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _command_line(pid: int) -> str:
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as handle:
            return handle.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout
    except Exception:  # noqa: BLE001
        return ""


def _is_target(pid: int, needle: str) -> bool:
    """True only if ``pid`` is alive and its command line mentions ``needle``."""
    return bool(needle) and needle in _command_line(pid)


def _signal(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, sig)


def _reap(target: int, needle: str, grace: float) -> None:
    """Terminate ``target`` if (and only if) it is still the guarded browser."""
    if not (_alive(target) and _is_target(target, needle)):
        return
    _signal(target, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _alive(target):
            return
        time.sleep(0.1)
    if _alive(target) and _is_target(target, needle):
        _signal(target, signal.SIGKILL)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _alive(target):
            time.sleep(0.1)


def _remove_profile(path: str) -> None:
    if not path:
        return
    if not os.path.basename(path.rstrip("/")).startswith(TEMP_PROFILE_PREFIX):
        return  # never delete anything that is not an ephemeral mithwire profile
    for _ in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.3)  # helper processes may still be flushing files


def run(
    lifeline: int,
    target: int,
    needle: str,
    cleanup_dir: str = "",
    poll: float = POLL_SECONDS,
    grace: float = TERM_GRACE_SECONDS,
) -> int:
    """Block until the owner dies or the browser exits, then clean up."""
    # The sidecar must outlive its owner: ignore terminal hang-ups / Ctrl-C.
    for name in ("SIGHUP", "SIGINT", "SIGPIPE"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(sig, signal.SIG_IGN)

    while True:
        ready, _, _ = select.select([lifeline], [], [], poll)
        if ready:
            try:
                data = os.read(lifeline, 4096)
            except OSError:
                data = b""
            if not data:
                break  # EOF: the owner exited (or released the guard)
            continue
        if not _alive(target):
            break  # the browser exited by itself

    _reap(target, needle, grace)
    _remove_profile(cleanup_dir)
    return 0


def _main(argv: list) -> int:
    if len(argv) < 3:
        return 2
    return run(
        int(argv[0]),
        int(argv[1]),
        argv[2],
        argv[3] if len(argv) > 3 else "",
        float(argv[4]) if len(argv) > 4 else POLL_SECONDS,
        float(argv[5]) if len(argv) > 5 else TERM_GRACE_SECONDS,
    )


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
