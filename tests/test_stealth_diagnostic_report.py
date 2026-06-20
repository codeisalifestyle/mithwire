"""Browser-free tests for the stealth-diagnostic grading logic.

These pin the verdict/finding rules (:mod:`mithwire.stealth_diagnostic.report`)
without launching Chrome, by feeding synthetic probe payloads shaped exactly
like the real probes emit. The browser-driving runner is covered by the live
``mithwire stealth-diagnostic`` command, not here.
"""
from __future__ import annotations

import unittest

from mithwire.stealth_diagnostic.report import FAIL, PASS, WARN, build_report


def _finding(rep, site, signal):
    return next((f for f in rep.findings if f.site == site and f.signal == signal), None)


CLEAN_NAV = {
    "userAgent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0 Safari/537.36",
    "webdriverType": "boolean",
    "webdriverValue": "false",
    "timezone": "Europe/London",
    "webgl": {"vendor": "Google Inc.", "renderer": "ANGLE (SwiftShader)"},
    "nativeToString": {"getParameter": True, "permissionsQuery": True, "fnToString": True},
}


def _clean_raw():
    return {
        "headless": False,
        "probes": {
            "navigator": dict(CLEAN_NAV),
            "sannysoft": {"total": 8, "passed": 8, "failed": [], "warn": [], "rows": []},
            "deviceandbrowserinfo": {"ready": True, "isBot": False, "details": {}},
            "creepjs": {"ready": True, "lieNodes": 0, "lieCategories": []},
            "ipapi": {"ready": True, "ip": "1.2.3.4", "timezone": "Europe/London"},
            "webrtc": {"ready": True, "candidates": [{"addr": "1.2.3.4", "typ": "srflx"}]},
        },
    }


class CleanBrowserTests(unittest.TestCase):
    def test_clean_browser_passes_with_no_findings(self):
        rep = build_report(_clean_raw(), headless=False)
        self.assertEqual(rep.verdict, PASS)
        self.assertEqual(rep.findings, [])
        self.assertEqual(rep.signals["tz_match"], "MATCH")
        self.assertEqual(rep.signals["webrtc"], "egress-only (ok)")


class HardFailTests(unittest.TestCase):
    def test_exposed_webdriver_is_fail(self):
        raw = _clean_raw()
        raw["probes"]["navigator"]["webdriverValue"] = "true"
        rep = build_report(raw, headless=False)
        self.assertEqual(rep.verdict, FAIL)
        self.assertIsNotNone(_finding(rep, "navigator", "webdriver"))

    def test_sannysoft_failure_is_fail(self):
        raw = _clean_raw()
        raw["probes"]["sannysoft"] = {"total": 8, "passed": 7, "failed": ["webdriver"], "warn": [], "rows": []}
        rep = build_report(raw, headless=False)
        self.assertEqual(rep.verdict, FAIL)

    def test_dab_hard_flag_is_fail_soft_is_warn(self):
        hard = _clean_raw()
        hard["probes"]["deviceandbrowserinfo"] = {
            "ready": True, "isBot": True, "details": {"isHeadlessChrome": True},
        }
        self.assertEqual(build_report(hard, headless=False).verdict, FAIL)

        soft = _clean_raw()
        soft["probes"]["deviceandbrowserinfo"] = {
            "ready": True, "isBot": True, "details": {"hasSuspiciousWeakSignals": True},
        }
        rep = build_report(soft, headless=False)
        self.assertEqual(rep.verdict, WARN)
        self.assertIsNotNone(_finding(rep, "deviceandbrowserinfo", "flags"))

    def test_webrtc_real_ip_leak_is_fail(self):
        raw = _clean_raw()
        raw["probes"]["webrtc"] = {
            "ready": True,
            "candidates": [
                {"addr": "1.2.3.4", "typ": "srflx"},
                {"addr": "9.9.9.9", "typ": "srflx"},  # not the egress IP -> leak
            ],
        }
        rep = build_report(raw, headless=False)
        self.assertEqual(rep.verdict, FAIL)
        leak = _finding(rep, "webrtc", "ice-candidates")
        self.assertIsNotNone(leak)
        self.assertIn("9.9.9.9", leak.detail)


class SoftWarnTests(unittest.TestCase):
    def test_timezone_mismatch_warns_with_hint(self):
        raw = _clean_raw()
        raw["probes"]["ipapi"]["timezone"] = "America/New_York"
        rep = build_report(raw, headless=False)
        self.assertEqual(rep.verdict, WARN)
        tz = _finding(rep, "ipapi", "timezone")
        self.assertIsNotNone(tz)
        self.assertIn("Emulation.setTimezoneOverride", tz.hint)

    def test_headless_ua_warns(self):
        raw = _clean_raw()
        raw["probes"]["navigator"]["userAgent"] = "Mozilla/5.0 HeadlessChrome/120.0.0.0"
        rep = build_report(raw, headless=True)
        self.assertEqual(rep.verdict, WARN)
        self.assertIsNotNone(_finding(rep, "navigator", "user-agent"))

    def test_creepjs_lies_warn(self):
        raw = _clean_raw()
        raw["probes"]["creepjs"] = {"ready": True, "lieNodes": 2, "lieCategories": ["navigator", "worker"]}
        rep = build_report(raw, headless=False)
        self.assertEqual(rep.verdict, WARN)

    def test_datacenter_ip_warns(self):
        raw = _clean_raw()
        raw["probes"]["ipapi"]["is_datacenter"] = True
        rep = build_report(raw, headless=False)
        self.assertEqual(rep.verdict, WARN)
        self.assertEqual(rep.signals["ip_flags"], ["datacenter"])


class ResilienceTests(unittest.TestCase):
    def test_errorish_and_missing_probes_do_not_crash(self):
        raw = {
            "headless": True,
            "probes": {
                "navigator": {"__timeout__": "navigator"},
                "sannysoft": {"__error__": "boom"},
                # other probes absent entirely
            },
        }
        rep = build_report(raw, headless=True)
        # Nothing assertable tripped, so it stays PASS rather than raising.
        self.assertEqual(rep.verdict, PASS)


if __name__ == "__main__":
    unittest.main()
