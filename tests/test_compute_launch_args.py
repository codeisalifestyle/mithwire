import sys
import unittest
from unittest import mock

from mithwire.stealth import FingerprintConfig, compute_launch_args


class ComputeLaunchArgsTests(unittest.TestCase):
    def test_headless_adds_default_window_size(self) -> None:
        args = compute_launch_args([], headless=True)
        self.assertIn("--window-size=1920,1080", args)

    def test_headless_window_size_matches_fingerprint(self) -> None:
        fp = FingerprintConfig.from_dict(
            {"screen_width": 2560, "screen_height": 1440}
        )
        args = compute_launch_args([], headless=True, fingerprint=fp)
        self.assertIn("--window-size=2560,1440", args)
        self.assertNotIn("--window-size=1920,1080", args)

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

    @mock.patch(
        "mithwire.stealth._detect_chrome_major", return_value="150"
    )
    @mock.patch.object(sys, "platform", "linux")
    def test_headless_adds_user_agent_flag(self, mock_detect: mock.MagicMock) -> None:
        args = compute_launch_args(
            [], headless=True, browser_executable_path="/usr/bin/chrome"
        )
        ua_args = [a for a in args if a.startswith("--user-agent=")]
        self.assertEqual(len(ua_args), 1)
        self.assertIn("Chrome/150.0.0.0", ua_args[0])
        self.assertNotIn("HeadlessChrome", ua_args[0])

    def test_headless_adds_font_render_hinting(self) -> None:
        args = compute_launch_args([], headless=True)
        self.assertIn("--font-render-hinting=medium", args)

    def test_headed_skips_headless_flags(self) -> None:
        args = compute_launch_args([], headless=False)
        self.assertFalse(any(a.startswith("--window-size=") for a in args))
        self.assertFalse(any(a.startswith("--user-agent=") for a in args))
        self.assertFalse(any("font-render-hinting" in a for a in args))


if __name__ == "__main__":
    unittest.main()
