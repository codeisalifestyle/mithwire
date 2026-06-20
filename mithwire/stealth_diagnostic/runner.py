"""Drive a real mithwire browser across the detection sites and grade it.

This is the engine-owned equivalent of the mithwire-mcp baseline harness's
``mithwire`` column: a bare ``mithwire.start(...)`` browser (the engine's
always-on stealth, no MCP layers) run against the bundled detectors so an
installer can see exactly how their machine looks and adjust their client.
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable

from mithwire.core.util import loop as _loop
from mithwire.core.util import start as _start

from .probes import NAV_PROBE, SITES, WEBRTC_PROBE, parse, wrap
from .report import StealthDiagnosticReport, build_report

__all__ = ["run_stealth_diagnostic", "stealth_diagnostic"]


async def _guard(coro, timeout: float, name: str) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return {"__timeout__": name, "after_s": timeout}
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


async def _eval(tab: Any, probe: str, timeout: float, name: str) -> Any:
    async def _run() -> Any:
        result = await tab.evaluate(wrap(probe), await_promise=True, return_by_value=True)
        if not isinstance(result, str):
            # Non-string -> an ExceptionDetails / RemoteObject, i.e. the probe
            # threw or didn't serialize. Surface it rather than crash the run.
            return {"__eval_error__": str(result)[:300]}
        return parse(result)

    return await _guard(_run(), timeout, name)


async def run_stealth_diagnostic(
    *,
    headless: bool = False,
    sites: Iterable[str] | None = None,
    proxy: str | None = None,
    browser_args: list[str] | None = None,
    browser_executable_path: str | None = None,
    sandbox: bool = True,
    include_webrtc: bool = True,
) -> StealthDiagnosticReport:
    """Launch a mithwire browser, probe the detection sites, return a report.

    :param headless: run headless (leaks a HeadlessChrome UA — see the report's
        warning); default is headful, the recommended stealth mode.
    :param sites: optional subset of site keys (see ``probes.SITES``); default
        runs them all.
    :param proxy: optional ``host:port`` (or scheme://host:port) proxy. The bare
        engine sets ``--proxy-server`` only; an authenticated proxy will
        407-challenge (proxy auth is an mithwire-mcp layer).
    :param browser_args: extra Chromium flags to launch with (this is exactly
        where a user applies a fix and re-runs to verify it).
    :param browser_executable_path: override Chrome/Chromium autodetection.
    """
    selected = list(SITES)
    if sites is not None:
        wanted = set(sites)
        selected = [s for s in SITES if s[0] in wanted]
        if not selected:
            raise ValueError(f"no known sites in {sorted(wanted)}; valid: {[s[0] for s in SITES]}")

    args = list(browser_args or [])
    if proxy:
        scheme_host = proxy if "://" in proxy else f"http://{proxy}"
        args.append(f"--proxy-server={scheme_host}")

    raw: dict[str, Any] = {"headless": headless, "proxy": bool(proxy), "probes": {}}

    browser = await _start(
        headless=headless,
        browser_args=args or None,
        browser_executable_path=browser_executable_path,
        sandbox=sandbox,
    )
    try:
        tab = browser.main_tab
        for i, (key, url, wait, probe, probe_to) in enumerate(selected):
            await _guard(tab.get(url), 40, f"nav {key}")
            if wait:
                await asyncio.sleep(wait)
            if i == 0:
                raw["probes"]["navigator"] = await _eval(tab, NAV_PROBE, 15, "navigator")
            raw["probes"][key] = await _eval(tab, probe, probe_to, key)
        # WebRTC leak check on the current secure (https) page after ICE
        # gathering completes — independent of any site's own snapshot.
        if include_webrtc:
            raw["probes"]["webrtc"] = await _eval(tab, WEBRTC_PROBE, 15, "webrtc")
    finally:
        await _guard(browser.stop(), 20, "stop")

    return build_report(raw, headless=headless)


def stealth_diagnostic(**kwargs: Any) -> StealthDiagnosticReport:
    """Synchronous wrapper around :func:`run_stealth_diagnostic` (engine loop)."""
    return _loop().run_until_complete(run_stealth_diagnostic(**kwargs))
