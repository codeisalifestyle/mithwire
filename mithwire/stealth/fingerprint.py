"""Browser identity / anti-detect fingerprint configuration.

A :class:`FingerprintConfig` is a declarative description of the identity a
session should present: timezone, locale, language, geolocation, device
metrics, hardware hints, user agent, and (optionally) GPU strings.

Design rule — prefer engine-level CDP ``Emulation.*`` overrides over JavaScript
injection. CDP overrides are applied inside Chromium itself, so they propagate
to *Web Workers* and to HTTP request headers. JS patches injected via
``Page.addScriptToEvaluateOnNewDocument`` only run on the main document, so a
worker reading the unpatched value produces an inconsistency that lie-detectors
(e.g. CreepJS) flag. We therefore use CDP for everything Chromium supports and
fall back to JS only for the handful of properties with no CDP override
(``navigator.deviceMemory`` and, when explicitly requested, the WebGL vendor /
renderer strings).

The whole point of this module is *internal consistency*: every signal a site
can read should agree with every other signal and with the proxy egress IP. A
mismatched override is worse than no override at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# country -> language mapping
# --------------------------------------------------------------------------
# ipapi.is returns a country_code but no language, so we derive a plausible
# Accept-Language / navigator.languages set from the country. Values are the
# locally dominant locale(s); English is appended as a near-universal fallback
# the way real browsers in these regions are commonly configured.
_COUNTRY_LANGUAGES: dict[str, list[str]] = {
    "US": ["en-US", "en"],
    "GB": ["en-GB", "en"],
    "CA": ["en-CA", "fr-CA", "en"],
    "AU": ["en-AU", "en"],
    "IE": ["en-IE", "en"],
    "NZ": ["en-NZ", "en"],
    "DE": ["de-DE", "de", "en"],
    "AT": ["de-AT", "de", "en"],
    "CH": ["de-CH", "de", "fr", "en"],
    "FR": ["fr-FR", "fr", "en"],
    "BE": ["nl-BE", "fr-BE", "en"],
    "NL": ["nl-NL", "nl", "en"],
    "ES": ["es-ES", "es", "en"],
    "MX": ["es-MX", "es", "en"],
    "AR": ["es-AR", "es", "en"],
    "CO": ["es-CO", "es", "en"],
    "CL": ["es-CL", "es", "en"],
    "IT": ["it-IT", "it", "en"],
    "PT": ["pt-PT", "pt", "en"],
    "BR": ["pt-BR", "pt", "en"],
    "PL": ["pl-PL", "pl", "en"],
    "RU": ["ru-RU", "ru", "en"],
    "UA": ["uk-UA", "uk", "ru", "en"],
    "SE": ["sv-SE", "sv", "en"],
    "NO": ["nb-NO", "no", "en"],
    "DK": ["da-DK", "da", "en"],
    "FI": ["fi-FI", "fi", "sv", "en"],
    "CZ": ["cs-CZ", "cs", "en"],
    "GR": ["el-GR", "el", "en"],
    "TR": ["tr-TR", "tr", "en"],
    "RO": ["ro-RO", "ro", "en"],
    "HU": ["hu-HU", "hu", "en"],
    "JP": ["ja-JP", "ja", "en"],
    "KR": ["ko-KR", "ko", "en"],
    "CN": ["zh-CN", "zh", "en"],
    "TW": ["zh-TW", "zh", "en"],
    "HK": ["zh-HK", "zh", "en"],
    "IN": ["en-IN", "hi-IN", "hi", "en"],
    "ID": ["id-ID", "id", "en"],
    "TH": ["th-TH", "th", "en"],
    "VN": ["vi-VN", "vi", "en"],
    "PH": ["en-PH", "fil-PH", "en"],
    "MY": ["ms-MY", "en-MY", "en"],
    "SG": ["en-SG", "zh-SG", "en"],
    "ZA": ["en-ZA", "af-ZA", "en"],
    "AE": ["ar-AE", "ar", "en"],
    "SA": ["ar-SA", "ar", "en"],
    "IL": ["he-IL", "he", "en"],
}

_DEFAULT_LANGUAGES = ["en-US", "en"]


def languages_for_country(country_code: str | None) -> list[str]:
    if not country_code:
        return list(_DEFAULT_LANGUAGES)
    return list(_COUNTRY_LANGUAGES.get(country_code.strip().upper(), _DEFAULT_LANGUAGES))


def strip_q_values(value: str) -> str:
    """Drop ``;q=...`` weights from an Accept-Language-like string.

    Chromium's CDP ``acceptLanguage`` override expects a *plain* comma list and
    re-derives the q-weights itself when it builds the header. If we pass a
    pre-weighted string Chromium doubles the weights (``de;q=0.9;q=0.9``) and
    leaks the literal ``;q=`` tokens into ``navigator.languages`` — a glaring
    inconsistency. So we always normalise to a clean ``"de-DE,de,en"`` form.
    """
    parts: list[str] = []
    for token in value.split(","):
        lang = token.split(";")[0].strip()
        if lang:
            parts.append(lang)
    return ",".join(parts)


def accept_language_csv(languages: list[str]) -> str:
    """Clean comma list for CDP ``acceptLanguage`` (Chromium adds q-weights)."""
    if not languages:
        languages = list(_DEFAULT_LANGUAGES)
    return ",".join(languages)


def _locale_from_languages(languages: list[str]) -> str | None:
    return languages[0] if languages else None


@dataclass
class FingerprintConfig:
    """Declarative identity description. All fields optional; unset = untouched."""

    timezone_id: str | None = None
    locale: str | None = None                 # BCP-47, e.g. "de-DE"
    languages: list[str] | None = None        # navigator.languages
    accept_language: str | None = None        # explicit header override

    latitude: float | None = None
    longitude: float | None = None
    geo_accuracy: float | None = None

    user_agent: str | None = None
    platform: str | None = None               # navigator.platform
    hardware_concurrency: int | None = None
    device_memory: int | None = None          # GB: 0.25/0.5/1/2/4/8

    screen_width: int | None = None
    screen_height: int | None = None
    device_scale_factor: float | None = None
    mobile: bool | None = None
    max_touch_points: int | None = None

    webgl_vendor: str | None = None
    webgl_renderer: str | None = None

    # Free-form provenance (e.g. proxy egress country/city) for diagnostics.
    source: dict[str, Any] = field(default_factory=dict)

    @property
    def has_device_metrics(self) -> bool:
        return (
            self.screen_width is not None
            and self.screen_height is not None
        )

    @property
    def is_empty(self) -> bool:
        return not any(
            value is not None
            for value in (
                self.timezone_id,
                self.locale,
                self.languages,
                self.accept_language,
                self.latitude,
                self.longitude,
                self.user_agent,
                self.platform,
                self.hardware_concurrency,
                self.device_memory,
                self.screen_width,
                self.screen_height,
                self.device_scale_factor,
                self.mobile,
                self.max_touch_points,
                self.webgl_vendor,
                self.webgl_renderer,
            )
        )

    @property
    def primary_language(self) -> str | None:
        if self.languages:
            return self.languages[0]
        if self.locale:
            return self.locale
        return None

    @property
    def effective_accept_language(self) -> str | None:
        """Clean comma list to feed CDP ``acceptLanguage`` (no q-weights)."""
        if self.accept_language:
            return strip_q_values(self.accept_language)
        if self.languages:
            return accept_language_csv(self.languages)
        return None

    def to_metadata(self) -> dict[str, Any]:
        """Compact, JSON-safe view for session metadata / diagnostics."""
        data = {
            "timezone_id": self.timezone_id,
            "locale": self.locale,
            "languages": self.languages,
            "accept_language": self.effective_accept_language,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "user_agent": self.user_agent,
            "platform": self.platform,
            "hardware_concurrency": self.hardware_concurrency,
            "device_memory": self.device_memory,
            "screen": (
                {
                    "width": self.screen_width,
                    "height": self.screen_height,
                    "device_scale_factor": self.device_scale_factor,
                    "mobile": self.mobile,
                    "max_touch_points": self.max_touch_points,
                }
                if self.has_device_metrics or self.max_touch_points is not None
                else None
            ),
            "webgl_vendor": self.webgl_vendor,
            "webgl_renderer": self.webgl_renderer,
            "source": self.source or None,
        }
        return {key: value for key, value in data.items() if value is not None}

    @classmethod
    def from_dict(cls, raw: Any) -> "FingerprintConfig":
        if raw is None:
            return cls()
        if isinstance(raw, FingerprintConfig):
            return raw
        if not isinstance(raw, dict):
            raise ValueError("fingerprint must be an object/dict.")

        def _f(*keys: str) -> Any:
            for key in keys:
                if key in raw and raw[key] is not None:
                    return raw[key]
            return None

        languages = _f("languages")
        if isinstance(languages, str):
            languages = [part.strip() for part in languages.split(",") if part.strip()]
        elif isinstance(languages, list):
            languages = [str(part).strip() for part in languages if str(part).strip()]
        elif languages is not None:
            raise ValueError("fingerprint.languages must be a list or comma string.")

        locale = _f("locale")
        if locale is None and languages:
            locale = _locale_from_languages(languages)

        def _num(value: Any) -> float | None:
            if value is None:
                return None
            return float(value)

        def _int(value: Any) -> int | None:
            if value is None:
                return None
            return int(value)

        screen = _f("screen") or {}
        if not isinstance(screen, dict):
            screen = {}

        return cls(
            timezone_id=_f("timezone_id", "timezone", "tz"),
            locale=locale,
            languages=languages,
            accept_language=_f("accept_language"),
            latitude=_num(_f("latitude", "lat")),
            longitude=_num(_f("longitude", "lon", "lng")),
            geo_accuracy=_num(_f("geo_accuracy", "accuracy")),
            user_agent=_f("user_agent", "ua"),
            platform=_f("platform"),
            hardware_concurrency=_int(_f("hardware_concurrency", "cores")),
            device_memory=_num(_f("device_memory", "ram")),
            screen_width=_int(_f("screen_width") or screen.get("width")),
            screen_height=_int(_f("screen_height") or screen.get("height")),
            device_scale_factor=_num(
                _f("device_scale_factor", "dpr") or screen.get("device_scale_factor")
            ),
            mobile=_f("mobile") if _f("mobile") is not None else screen.get("mobile"),
            max_touch_points=_int(_f("max_touch_points") or screen.get("max_touch_points")),
            webgl_vendor=_f("webgl_vendor"),
            webgl_renderer=_f("webgl_renderer"),
            source=_f("source") or {},
        )

    @classmethod
    def from_ipapi(cls, data: dict[str, Any]) -> "FingerprintConfig":
        """Build an identity from an ``api.ipapi.is`` response.

        Aligns timezone, locale/language, and geolocation to the (proxy) egress
        IP so the presented identity is internally consistent with the network
        path the traffic actually takes.
        """
        location = data.get("location") or {}
        country_code = location.get("country_code")
        languages = languages_for_country(country_code)
        config = cls(
            timezone_id=location.get("timezone"),
            locale=_locale_from_languages(languages),
            languages=languages,
            accept_language=accept_language_csv(languages),
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            geo_accuracy=50.0,
            source={
                "exit_ip": data.get("ip"),
                "country": location.get("country"),
                "country_code": country_code,
                "city": location.get("city"),
                "timezone": location.get("timezone"),
            },
        )
        return config

    def merged_with(self, override: "FingerprintConfig") -> "FingerprintConfig":
        """Return a copy where any set field of ``override`` wins."""
        base = self
        out = FingerprintConfig()
        for f_name in (
            "timezone_id", "locale", "languages", "accept_language",
            "latitude", "longitude", "geo_accuracy", "user_agent", "platform",
            "hardware_concurrency", "device_memory", "screen_width",
            "screen_height", "device_scale_factor", "mobile", "max_touch_points",
            "webgl_vendor", "webgl_renderer",
        ):
            ov = getattr(override, f_name)
            setattr(out, f_name, ov if ov is not None else getattr(base, f_name))
        merged_source = dict(base.source or {})
        merged_source.update(override.source or {})
        out.source = merged_source
        return out
