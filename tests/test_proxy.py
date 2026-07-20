"""Tests for the engine-level proxy module."""

import unittest

from mithwire.proxy import ProxyConfig, parse_proxy


class ParseProxyTest(unittest.TestCase):
    def test_none_and_empty(self) -> None:
        self.assertIsNone(parse_proxy(None))
        self.assertIsNone(parse_proxy(""))
        self.assertIsNone(parse_proxy("   "))

    def test_passthrough_config(self) -> None:
        cfg = ProxyConfig(scheme="http", host="h", port=1)
        self.assertIs(parse_proxy(cfg), cfg)

    def test_colon_form_with_auth(self) -> None:
        cfg = parse_proxy("http:proxy.example.com:8081:user123:pass456")
        assert cfg is not None
        self.assertEqual(cfg.scheme, "http")
        self.assertEqual(cfg.host, "proxy.example.com")
        self.assertEqual(cfg.port, 8081)
        self.assertEqual(cfg.username, "user123")
        self.assertEqual(cfg.password, "pass456")
        self.assertTrue(cfg.has_auth)

    def test_colon_form_no_auth(self) -> None:
        cfg = parse_proxy("http:1.2.3.4:8080")
        assert cfg is not None
        self.assertFalse(cfg.has_auth)

    def test_url_form_with_auth(self) -> None:
        cfg = parse_proxy("http://user:pass@1.2.3.4:8080")
        assert cfg is not None
        self.assertEqual(cfg.username, "user")
        self.assertEqual(cfg.password, "pass")

    def test_url_form_percent_encoded(self) -> None:
        cfg = parse_proxy("http://us%40er:p%3Aass@host:3128")
        assert cfg is not None
        self.assertEqual(cfg.username, "us@er")
        self.assertEqual(cfg.password, "p:ass")

    def test_password_with_colons(self) -> None:
        cfg = parse_proxy("http:host:8080:user:a:b:c")
        assert cfg is not None
        self.assertEqual(cfg.password, "a:b:c")

    def test_scheme_aliases(self) -> None:
        self.assertEqual(parse_proxy("socks:h:1").scheme, "socks5")  # type: ignore[union-attr]
        self.assertEqual(parse_proxy("socks5h://h:1").scheme, "socks5")  # type: ignore[union-attr]

    def test_socks_with_auth_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_proxy("socks:host:1081:user:pass")

    def test_dict_form(self) -> None:
        cfg = parse_proxy({"scheme": "http", "host": "h", "port": 9000})
        assert cfg is not None
        self.assertEqual(cfg.port, 9000)

    def test_dict_server_url(self) -> None:
        cfg = parse_proxy({"server": "http://h:8080", "username": "u"})
        assert cfg is not None
        self.assertEqual(cfg.host, "h")
        self.assertEqual(cfg.username, "u")

    def test_invalid_scheme(self) -> None:
        with self.assertRaises(ValueError):
            parse_proxy("ftp://h:21")

    def test_invalid_port(self) -> None:
        with self.assertRaises(ValueError):
            parse_proxy("http:h:0")

    def test_redacted_hides_password(self) -> None:
        cfg = parse_proxy("http://user:supersecret@h:8080")
        assert cfg is not None
        self.assertNotIn("supersecret", cfg.redacted())
        self.assertIn("***", cfg.redacted())

    def test_rotation_url_dict(self) -> None:
        cfg = parse_proxy({
            "server": "http://1.2.3.4:8080",
            "rotation_url": "https://api.example.com/rotate?token=abc",
        })
        assert cfg is not None
        self.assertTrue(cfg.has_rotation)

    def test_metadata_redacts_rotation_query(self) -> None:
        cfg = parse_proxy({
            "server": "http://1.2.3.4:8080",
            "rotation_url": "https://api.example.com/rotate?token=secret",
        })
        assert cfg is not None
        meta = cfg.to_metadata()
        self.assertNotIn("secret", str(meta))


class LocalProxyRelayTest(unittest.TestCase):
    def test_socks_rejected(self) -> None:
        from mithwire.proxy import LocalProxyRelay
        with self.assertRaises(ValueError):
            LocalProxyRelay(ProxyConfig(scheme="socks5", host="h", port=1))


class VirtualDisplayTest(unittest.TestCase):
    def test_non_linux_returns_display(self) -> None:
        import sys
        from mithwire.core.virtual_display import ensure_virtual_display
        if not sys.platform.startswith("linux"):
            result = ensure_virtual_display()
            # On non-Linux, returns DISPLAY env var or None
            self.assertIsNone(result) if not __import__("os").environ.get("DISPLAY") else None


class CloakBrowserAdapterTest(unittest.TestCase):
    def test_platform_check(self) -> None:
        import sys
        from mithwire.stealth.cloakbrowser import is_platform_supported
        if sys.platform in ("darwin", "linux"):
            self.assertTrue(is_platform_supported())

    def test_fingerprint_to_flags(self) -> None:
        from mithwire.stealth import FingerprintConfig
        from mithwire.stealth.cloakbrowser import fingerprint_to_flags
        fp = FingerprintConfig(timezone_id="Europe/London", languages=["en-GB"])
        flags = fingerprint_to_flags(fp, profile_name="test")
        flag_str = " ".join(flags)
        self.assertIn("--fingerprint=", flag_str)
        self.assertIn("--fingerprint-timezone=Europe/London", flag_str)
        self.assertIn("--lang=en-GB", flag_str)

    def test_profile_seed_deterministic(self) -> None:
        from mithwire.stealth.cloakbrowser import _profile_seed
        self.assertEqual(_profile_seed("myprofile"), _profile_seed("myprofile"))
        self.assertNotEqual(_profile_seed("a"), _profile_seed("b"))


class FingerprintGenTest(unittest.TestCase):
    def test_is_available(self) -> None:
        from mithwire.fingerprint_gen import is_available
        # Just verify it doesn't crash; availability depends on install
        isinstance(is_available(), bool)


class EgressSummaryTest(unittest.TestCase):
    def test_empty_returns_none(self) -> None:
        from mithwire.proxy import egress_summary
        self.assertIsNone(egress_summary(None))
        self.assertIsNone(egress_summary({}))

    def test_picks_fields(self) -> None:
        from mithwire.proxy import egress_summary
        data = {
            "ip": "1.2.3.4",
            "location": {"timezone": "Europe/London", "country": "UK", "city": "London", "country_code": "GB"},
        }
        result = egress_summary(data)
        assert result is not None
        self.assertEqual(result["exit_ip"], "1.2.3.4")
        self.assertEqual(result["timezone"], "Europe/London")


if __name__ == "__main__":
    unittest.main()
