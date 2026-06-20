"""mithwire stealth diagnostic.

A diagnostic that runs a bare ``mithwire.start()`` browser against a set of
public bot-detection sites and reports how it looks, so anyone who installs
mithwire can check their machine, adjust their client, and re-run to verify.

    from mithwire import stealth_diagnostic
    report = stealth_diagnostic()      # headful, all sites
    print(report.verdict)              # PASS / WARN / FAIL

Or from the shell::

    mithwire stealth-diagnostic
"""
from __future__ import annotations

from .report import (
    FAIL,
    PASS,
    WARN,
    Finding,
    StealthDiagnosticReport,
    build_report,
    format_report,
)
from .runner import run_stealth_diagnostic, stealth_diagnostic

__all__ = [
    "stealth_diagnostic",
    "run_stealth_diagnostic",
    "build_report",
    "format_report",
    "StealthDiagnosticReport",
    "Finding",
    "PASS",
    "WARN",
    "FAIL",
]
