import sys
import unittest
from unittest import mock

from mithwire.stealth import FingerprintConfig, compute_launch_args


class ComputeLaunchArgsTests(unittest.TestCase):
    def test_headless_adds_window_size(self) -> None:
        args = compute_launch_args([], headless=True)
        self.assertIn("--window-size=1920,1080", args)

    def test_headless_skips_duplicate_window_size(self) -> None:
        existing = ["--window-size=1280,720"]
        args = compute_launch_args(existing, headless=True)
        self.assertNotIn("--window-size=1920,1080", args)

    @mock.patch.object(sys, "platform", "win32")
    def test_headless_windows_adds_offscreen_position(self) -> None:
        args = compute_launch_args([], headless=True)
        self.assertIn("--window-position=-2400,-2400", args)

    @mock.patch.object(sys, "platform", "darwin")
    def test_headless_non_windows_skips_window_position(self) -> None:
        args = compute_launch_args([], headless=True)
        self.assertNotIn("--window-position=-2400,-2400", args)

    @mock.patch.object(sys, "platform", "win32")
    def test_headless_windows_skips_duplicate_window_position(self) -> None:
        existing = ["--window-position=0,0"]
        args = compute_launch_args(existing, headless=True)
        self.assertNotIn("--window-position=-2400,-2400", args)

    def test_proxied_adds_webrtc_policy(self) -> None:
        args = compute_launch_args(["--proxy-server=http://127.0.0.1:8080"])
        self.assertIn(
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", args
        )

    def test_direct_connection_webrtc_policy(self) -> None:
        args = compute_launch_args([])
        self.assertIn(
            "--force-webrtc-ip-handling-policy=default_public_interface_only",
            args,
        )

    def test_fingerprint_adds_lang(self) -> None:
        fp = FingerprintConfig.from_dict(
            {"languages": ["de-DE", "de"], "timezone": "Europe/Berlin"}
        )
        args = compute_launch_args([], fingerprint=fp)
        self.assertIn("--lang=de-DE", args)


if __name__ == "__main__":
    unittest.main()
