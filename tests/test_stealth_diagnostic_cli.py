"""Chrome-free smoke tests for the stealth-diagnostic public surface.

The browser-driving runner can't run in the fast CI lane (no Chrome), and the
grading logic is covered by ``test_stealth_diagnostic_report``. This file guards
the two remaining no-browser surfaces that a refactor can silently break:

* the public API exports on the ``mithwire`` package, and
* the CLI wiring (argparse subcommand + flag-to-kwarg mapping + entry point).

All assertions are deterministic and require neither Chrome nor the network.
"""
from __future__ import annotations

import types
import unittest

import mithwire
from mithwire.__main__ import build_parser
from mithwire.stealth_diagnostic.probes import SITES, parse, wrap


class PublicApiTests(unittest.TestCase):
    def test_package_exports_are_callable(self):
        for name in ("diagnose_stealth", "run_stealth_diagnostic"):
            self.assertIn(name, mithwire.__all__)
            self.assertTrue(callable(getattr(mithwire, name)))

    def test_old_selftest_name_is_gone(self):
        # The rename should not leave a dangling alias.
        self.assertFalse(hasattr(mithwire, "selftest"))
        self.assertFalse(hasattr(mithwire, "run_selftest"))

    def test_subpackage_attribute_is_the_module_not_a_function(self):
        # Regression: the sync wrapper used to be exported as ``stealth_diagnostic``,
        # which shadowed the ``mithwire.stealth_diagnostic`` subpackage on the
        # ``mithwire`` namespace and broke dotted submodule imports below.
        self.assertIsInstance(mithwire.stealth_diagnostic, types.ModuleType)

    def test_dotted_submodule_import_resolves(self):
        # ``import mithwire.stealth_diagnostic.probes as p`` compiles to IMPORT_FROM,
        # which walks the (formerly shadowed) ``stealth_diagnostic`` attribute. This
        # is the exact form that regressed; assert it resolves to the real module.
        import mithwire.stealth_diagnostic.probes as p

        self.assertIs(p, mithwire.stealth_diagnostic.probes)
        self.assertTrue(p.SITES)


class CliParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def test_subcommand_defaults_map_to_runner_kwargs(self):
        ns = self.parser.parse_args(["stealth-diagnostic"])
        self.assertEqual(ns.command, "stealth-diagnostic")
        self.assertTrue(callable(ns.func))
        self.assertFalse(ns.headless)
        self.assertIsNone(ns.sites)
        self.assertEqual(ns.browser_arg, [])
        self.assertFalse(ns.no_sandbox)
        self.assertFalse(ns.no_webrtc)
        self.assertFalse(ns.json)

    def test_flags_parse(self):
        ns = self.parser.parse_args([
            "stealth-diagnostic",
            "--headless",
            "--sites", "sannysoft,ipapi",
            "--no-sandbox",
            "--no-webrtc",
            # value starts with '-', so the '=' form is required
            "--browser-arg=--disable-gpu",
            "--browser-arg=--mute-audio",
            "--json",
        ])
        self.assertTrue(ns.headless)
        self.assertEqual(ns.sites, "sannysoft,ipapi")
        self.assertTrue(ns.no_sandbox)
        self.assertTrue(ns.no_webrtc)
        self.assertEqual(ns.browser_arg, ["--disable-gpu", "--mute-audio"])
        self.assertTrue(ns.json)

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as cm:
            self.parser.parse_args(["stealth-diagnostic", "--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_missing_subcommand_errors(self):
        # add_subparsers(required=True) -> argparse exits non-zero with no cmd.
        with self.assertRaises(SystemExit) as cm:
            self.parser.parse_args([])
        self.assertNotEqual(cm.exception.code, 0)


class ProbeStructureTests(unittest.TestCase):
    def test_sites_well_formed(self):
        self.assertTrue(SITES)
        for key, url, nav_wait, probe_js, probe_to in SITES:
            self.assertIsInstance(key, str)
            self.assertTrue(url.startswith("https://"), f"{key} must be https")
            self.assertGreater(probe_to, 0)
            self.assertIn("=>", probe_js)

    def test_first_site_is_secure_context(self):
        # The navigator probe (and userAgentData) need a secure context, so the
        # first site the runner visits must be https.
        self.assertTrue(SITES[0][1].startswith("https://"))

    def test_parse_round_trips_and_tolerates_garbage(self):
        self.assertEqual(parse('{"a": 1, "b": [2, 3]}'), {"a": 1, "b": [2, 3]})
        self.assertEqual(parse(123), 123)  # non-str passthrough
        self.assertIn("__unparsed__", parse("not json"))

    def test_wrap_serializes_to_json_string(self):
        wrapped = wrap("({x: 1})")
        self.assertIn("JSON.stringify", wrapped)
        self.assertIn("Promise.resolve", wrapped)


if __name__ == "__main__":
    unittest.main()
