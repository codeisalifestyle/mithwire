"""``python -m mithwire`` / ``mithwire`` command-line entry point."""
from __future__ import annotations

import argparse
import json
import sys

from mithwire.stealth_diagnostic import format_report, run_stealth_diagnostic
from mithwire.core.util import loop as _loop
from mithwire.stealth_diagnostic.probes import SITES


def _cmd_stealth_diagnostic(args: argparse.Namespace) -> int:
    sites = args.sites.split(",") if args.sites else None
    report = _loop().run_until_complete(
        run_stealth_diagnostic(
            headless=args.headless,
            sites=sites,
            proxy=args.proxy,
            browser_args=(args.browser_arg or None),
            browser_executable_path=args.browser_path,
            sandbox=not args.no_sandbox,
            include_webrtc=not args.no_webrtc,
        )
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report(report, color=not args.no_color))
    # Exit non-zero only on a hard FAIL, so the diagnostic is CI-friendly.
    return 1 if not report.ok else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mithwire", description="mithwire CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    st = sub.add_parser(
        "stealth-diagnostic",
        help="run the stealth diagnostic against public bot-detection sites",
    )
    st.add_argument("--headless", action="store_true", help="run headless (leaks a HeadlessChrome UA; headful is recommended)")
    st.add_argument("--sites", default=None, help=f"comma-separated subset of: {','.join(s[0] for s in SITES)}")
    st.add_argument("--proxy", default=None, help="proxy as host:port or scheme://host:port (no auth in the bare engine)")
    st.add_argument("--browser-arg", action="append", default=[], help="extra Chromium flag (repeatable) — apply a fix and re-run to verify")
    st.add_argument("--browser-path", default=None, help="path to the Chrome/Chromium executable")
    st.add_argument("--no-sandbox", action="store_true", help="launch with the sandbox disabled (needed as root / in containers)")
    st.add_argument("--no-webrtc", action="store_true", help="skip the WebRTC leak probe")
    st.add_argument("--json", action="store_true", help="emit the full report as JSON")
    st.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    st.set_defaults(func=_cmd_stealth_diagnostic)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
