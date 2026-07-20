"""Automatic Xvfb (virtual display) management for headed browser mode.

On a headless Linux server, Chrome cannot run in headed mode without a display.
Xvfb provides a virtual X11 framebuffer that makes Chrome behave exactly like a
real desktop browser — natural toolbar height, proper window chrome, and correct
storage/permission API behaviour — while consuming minimal resources.

Usage::

    display = ensure_virtual_display()
    # display is ":99" and DISPLAY env var is set

The module is idempotent: calling ``ensure_virtual_display`` multiple times
reuses the existing Xvfb process.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

_xvfb_process: subprocess.Popen | None = None
_display: str | None = None

XVFB_DISPLAY = ":99"
XVFB_SCREEN = "1920x1080x24"


def _is_display_available() -> bool:
    """Return True if a usable DISPLAY is already set."""
    display = os.environ.get("DISPLAY")
    return bool(display and display.strip())


def _xvfb_available() -> bool:
    """Return True if the Xvfb binary is on PATH."""
    return shutil.which("Xvfb") is not None


def _cleanup() -> None:
    """Kill the managed Xvfb process on interpreter exit."""
    global _xvfb_process
    if _xvfb_process is not None:
        try:
            _xvfb_process.terminate()
            _xvfb_process.wait(timeout=3)
        except Exception:
            try:
                _xvfb_process.kill()
            except Exception:
                pass
        _xvfb_process = None


def ensure_virtual_display(
    *,
    display: str = XVFB_DISPLAY,
    screen: str = XVFB_SCREEN,
) -> str | None:
    """Start Xvfb if needed and return the DISPLAY string.

    Returns None on non-Linux platforms, when a display already exists,
    or when Xvfb is not installed.
    """
    global _xvfb_process, _display

    if not sys.platform.startswith("linux"):
        return os.environ.get("DISPLAY")

    if _is_display_available():
        return os.environ["DISPLAY"]

    if _display is not None and _xvfb_process is not None:
        if _xvfb_process.poll() is None:
            return _display

    if not _xvfb_available():
        logger.warning(
            "Xvfb not found — headed browser sessions on a displayless server "
            "will fail. Install with: apt-get install xvfb"
        )
        return None

    try:
        _xvfb_process = subprocess.Popen(
            [
                "Xvfb", display,
                "-screen", "0", screen,
                "-ac",
                "+extension", "GLX",
                "-noreset",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)

        if _xvfb_process.poll() is not None:
            logger.warning("Xvfb exited immediately (display %s may be in use)", display)
            _xvfb_process = None
            return None

        os.environ["DISPLAY"] = display
        _display = display
        atexit.register(_cleanup)
        logger.info("Xvfb started on %s (%s)", display, screen)
        return display

    except Exception as exc:
        logger.warning("Failed to start Xvfb: %s", exc)
        return None


def stop_virtual_display() -> None:
    """Stop the managed Xvfb process."""
    _cleanup()
