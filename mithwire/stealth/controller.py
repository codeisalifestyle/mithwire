"""Anti-detect stealth controller for the mithwire engine.

The engine owns every browser-altering anti-detect capability: fingerprint
application, headless user-agent cleanup, WebRTC leak protection, the
new-document stealth shim, and the timezone override. A client (the
``mithwire-mcp`` server, or any custom script) merely *describes* the identity
it wants via :class:`~mithwire.stealth.fingerprint.FingerprintConfig` and a
WebRTC mode; the engine implements all of it.

Design rule — prefer engine-level CDP ``Emulation.*`` overrides over JavaScript
injection. CDP overrides are applied inside Chromium itself, so they propagate
to Web Workers and to HTTP request headers. JS patches injected via
``Page.addScriptToEvaluateOnNewDocument`` only run on the main document, so a
worker reading the unpatched value produces an inconsistency that lie-detectors
(e.g. CreepJS) flag. We therefore use CDP for everything Chromium supports and
fall back to JS only for the handful of properties with no CDP override
(``navigator.deviceMemory`` and, when explicitly requested, the WebGL vendor /
renderer strings).

This module was extracted verbatim (behaviour-preserving) from the historical
``mithwire_mcp.browser.BridgeBrowser`` so that ownership of the anti-detect
implementation lives with the browser engine, not a single client.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, ClassVar

from ..cdp import browser as cdp_browser
from ..cdp import emulation as cdp_emulation
from ..cdp import network as cdp_network
from ..cdp import page as cdp_page
from .fingerprint import FingerprintConfig

logger = logging.getLogger(__name__)


class Stealth:
    """Applies anti-detect patches to a live engine :class:`Browser`.

    Constructed against an already-started browser. ``apply_all`` runs the
    standard launch-time sequence; individual methods (``apply_fingerprint``,
    ``apply_timezone_override``) are also public so a client can re-apply an
    identity later (e.g. once a proxy egress geo is resolved).
    """

    def __init__(
        self,
        browser: Any,
        *,
        fingerprint: FingerprintConfig | None = None,
        webrtc_leak_protection: str = "auto",
        headless: bool = False,
        proxied: bool = False,
    ) -> None:
        self.browser = browser
        self.fingerprint = fingerprint or FingerprintConfig()
        # WebRTC leak protection mode. An HTTP/SOCKS proxy cannot carry STUN/UDP,
        # so WebRTC queries STUN over the physical NIC and the server-reflexive
        # (srflx) candidate betrays the real public IP -- the #1 proxy leak, and
        # one no Chromium flag reliably closes. Modes: "auto" (filter only when
        # proxied), "filter" (always), "disable" (remove RTCPeerConnection), "off".
        self.webrtc_leak_protection = (webrtc_leak_protection or "auto").strip().lower()
        self.headless = headless
        self.proxied = proxied
        self.timezone_id: str | None = None
        self.tab: Any = getattr(browser, "main_tab", None)
        self._page_domain_tab: Any | None = None

    async def apply_all(self) -> None:
        """Run the launch-time stealth sequence on the active tab."""
        self.tab = getattr(self.browser, "main_tab", None)
        await self._inject_stealth_script()
        await self._inject_webrtc_protection()
        if self.headless:
            await self._apply_headless_user_agent()
        if not self.fingerprint.is_empty:
            await self.apply_fingerprint(self.fingerprint)

    async def _ensure_page_domain(self) -> None:
        """Enable the CDP Page domain once on the active tab.

        ``Page.addScriptToEvaluateOnNewDocument`` only actually injects when the
        Page domain is enabled on that target's session (mithwire does the same
        in ``_prepare_expert``). Without this, registered scripts silently never
        run on subsequent documents.
        """
        if self.tab is None:
            return
        if getattr(self, "_page_domain_tab", None) is self.tab:
            return
        try:
            await self.tab.send(cdp_page.enable())
            self._page_domain_tab = self.tab
        except Exception as exc:  # noqa: BLE001
            logger.debug("Page.enable() failed: %s", exc)

    async def add_script_on_new_document(self, source: str) -> None:
        await self._ensure_page_domain()
        await self.tab.send(cdp_page.add_script_to_evaluate_on_new_document(source=source))

    async def _inject_stealth_script(self) -> None:
        await self._ensure_page_domain()
        # Intentionally do NOT override navigator.webdriver here. Chromium
        # already exposes it as a NATIVE getter on Navigator.prototype that
        # returns `false` (it only flips to `true` under --enable-automation,
        # which this launcher never sets). Re-defining it with
        # Object.defineProperty(navigator, 'webdriver', ...) installs a
        # non-native getter as an OWN property on the instance, which shadows
        # the prototype getter and is itself a detectable tell (e.g. sannysoft
        # "WebDriver (New)" flags the tampered descriptor even when the value is
        # false). Verified against clean-Chrome and HEAD baselines: leaving the
        # native getter untouched passes where the override fails.
        #
        # The chrome object shim is kept (no-op when window.chrome already
        # exists, e.g. headful) to avoid an empty/missing window.chrome in some
        # headless contexts.
        script = """
            window.chrome = window.chrome || { runtime: {} };
        """
        await self.tab.send(cdp_page.add_script_to_evaluate_on_new_document(source=script))

    def _resolve_webrtc_action(self) -> str | None:
        """Decide the effective WebRTC action for this session ('filter'/'disable'/None)."""
        mode = self.webrtc_leak_protection
        if mode == "off":
            return None
        if mode == "disable":
            return "disable"
        if mode == "filter":
            return "filter"
        # "auto" (and any unknown value): protect only when proxied, since a
        # direct connection's public WebRTC candidate is the legitimate IP.
        return "filter" if self.proxied else None

    async def _inject_webrtc_protection(self) -> None:
        """Inject the WebRTC leak guard as an all-frames new-document script.

        Runs before page scripts on every navigation/frame. Self-contained: it
        bundles its own native-toString mask so the patched accessors/methods
        stringify as native even when no fingerprint document JS is injected.
        """
        action = self._resolve_webrtc_action()
        if action is None:
            return
        script = self._webrtc_protection_js(action)
        try:
            await self.tab.send(
                cdp_page.add_script_to_evaluate_on_new_document(source=script)
            )
            logger.info("Injected WebRTC leak protection (mode=%s).", action)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not inject WebRTC leak protection: %s", exc)

    def _webrtc_protection_js(self, action: str) -> str:
        if action == "disable":
            # Remove the constructors outright. WebRTC absence is a mild tell but
            # cannot leak. Both the standard and webkit-prefixed names are cleared.
            return """
            (function () {
              const drop = (name) => {
                try { Object.defineProperty(window, name, { value: undefined, configurable: true }); }
                catch (e) { try { delete window[name]; } catch (e2) {} }
              };
              drop('RTCPeerConnection');
              drop('webkitRTCPeerConnection');
              drop('mozRTCPeerConnection');
              drop('RTCDataChannel');
            })();
            """
        # action == "filter": drop public, non-mDNS ICE candidates so the real
        # IP never reaches the page. We patch only RTCPeerConnection.prototype
        # members that are NORMALLY own properties of that prototype (the
        # onicecandidate accessor, the localDescription accessors, and
        # createOffer/createAnswer), so no own-property tell is introduced.
        #
        # We deliberately do NOT use the global Function.prototype.toString mask
        # (_NATIVE_MASK_PREAMBLE) here: this guard is ALWAYS-ON (no-spoof path),
        # and globally reassigning Function.prototype.toString is itself a strong
        # CreepJS tell that cascades into ~9 component "lies" (Timezone, WebGL,
        # Canvas, Audio, Math, ...). Instead each replacement gets a light,
        # local own-`toString` so `fn.toString()`/`fn + ''` read native, without
        # touching the global. (Advanced Function.prototype.toString.call probing
        # of these specific WebRTC members is an accepted depth-layer gap -- far
        # cheaper than re-leaking the real IP or tripping 9 lies.)
        return (
            r"""
            (function () {
              const RTC = window.RTCPeerConnection || window.webkitRTCPeerConnection;
              if (!RTC || !RTC.prototype || RTC.prototype.__nrRtcGuard) return;
              const proto = RTC.prototype;
              const __nrMask = (fn, name) => {
                try {
                  Object.defineProperty(fn, 'toString', {
                    value: function toString() { return 'function ' + name + '() { [native code] }'; },
                    configurable: true, writable: true,
                  });
                } catch (e) {}
                return fn;
              };
              const isPublic = (addr) => {
                if (!addr) return false;
                addr = ('' + addr).toLowerCase();
                if (addr.indexOf('.local') >= 0 || addr.indexOf('mdns') >= 0) return false;
                if (addr.indexOf(':') >= 0) {
                  return !(addr.indexOf('fe80') === 0 || addr.indexOf('fc') === 0 || addr.indexOf('fd') === 0);
                }
                if (/^(10\.|127\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(addr)) return false;
                return /^\d{1,3}(\.\d{1,3}){3}$/.test(addr);
              };
              const candAddr = (s) => { const p = ('' + s).split(' '); return p[4] || ''; };
              const candBlocked = (cand) => {
                try {
                  const s = cand && (cand.candidate !== undefined ? cand.candidate : cand);
                  return s ? isPublic(candAddr(s)) : false;
                } catch (e) { return false; }
              };
              const scrubSdp = (sdp) => {
                if (!sdp) return sdp;
                return ('' + sdp).split('\r\n').filter((line) => {
                  const i = line.indexOf('candidate:');
                  if (i < 0) return true;
                  return !isPublic(candAddr(line.slice(i + 'candidate:'.length)));
                }).join('\r\n');
              };
              const wrapCb = (cb) => function (ev) {
                try { if (ev && ev.candidate && candBlocked(ev.candidate)) return undefined; } catch (e) {}
                return cb.apply(this, arguments);
              };
              // 1) onicecandidate accessor (own accessor on the prototype): wrap
              //    the page's handler so srflx/public candidates are dropped.
              try {
                const od = Object.getOwnPropertyDescriptor(proto, 'onicecandidate');
                if (od && typeof od.set === 'function') {
                  const getter = function () { return od.get ? od.get.call(this) : null; };
                  const setter = function (cb) {
                    return od.set.call(this, typeof cb === 'function' ? wrapCb(cb) : cb);
                  };
                  __nrMask(getter, 'onicecandidate');
                  __nrMask(setter, 'onicecandidate');
                  Object.defineProperty(proto, 'onicecandidate', {
                    configurable: true, enumerable: od.enumerable, get: getter, set: setter,
                  });
                }
              } catch (e) {}
              // 2) localDescription family: scrub candidate lines from any SDP a
              //    page reads back after gathering.
              ['localDescription', 'currentLocalDescription', 'pendingLocalDescription'].forEach((prop) => {
                try {
                  const d = Object.getOwnPropertyDescriptor(proto, prop);
                  if (d && typeof d.get === 'function') {
                    const getter = function () {
                      const desc = d.get.call(this);
                      if (!desc || !desc.sdp) return desc;
                      try { return new RTCSessionDescription({ type: desc.type, sdp: scrubSdp(desc.sdp) }); }
                      catch (e) { return desc; }
                    };
                    __nrMask(getter, prop);
                    Object.defineProperty(proto, prop, {
                      configurable: true, enumerable: d.enumerable, get: getter,
                    });
                  }
                } catch (e) {}
              });
              // 3) createOffer/createAnswer (own methods): scrub the promise's SDP
              //    so non-trickle offers carry no public candidate.
              ['createOffer', 'createAnswer'].forEach((m) => {
                try {
                  const orig = proto[m];
                  if (typeof orig !== 'function') return;
                  const wrapped = function () {
                    const r = orig.apply(this, arguments);
                    if (r && typeof r.then === 'function') {
                      return r.then((desc) => {
                        try { if (desc && desc.sdp) return { type: desc.type, sdp: scrubSdp(desc.sdp) }; }
                        catch (e) {}
                        return desc;
                      });
                    }
                    return r;
                  };
                  __nrMask(wrapped, m);
                  proto[m] = wrapped;
                } catch (e) {}
              });
              try { Object.defineProperty(proto, '__nrRtcGuard', { value: true }); } catch (e) {}
            })();
            """
        )

    async def _apply_headless_user_agent(self) -> None:
        """Strip ``HeadlessChrome`` while keeping main-thread UA-CH populated.

        Headless Chrome leaks the automation in ``navigator.userAgent`` (it
        carries ``HeadlessChrome``), which DAB/sannysoft flag. Stripping it with a
        CDP user-agent override is the fix -- but a UA-only override (no
        ``userAgentMetadata``) BLANKS ``navigator.userAgentData`` (empty brands +
        platform), and an empty brand list is itself a tell since a real Chrome
        always exposes one. The earlier code hit exactly that trap whenever the
        live high-entropy hints were unreadable (``getHighEntropyValues`` rejects
        on ``about:blank`` right after launch), shipping a clean UA with blank
        UA-CH.

        So we ALWAYS push the override WITH metadata: ``_build_ua_metadata``
        synthesizes the brand list and infers the host fields from the UA when
        the live hints are blank. The UA string itself is only rewritten when the
        legacy token is present.

        SCOPE: this covers the MAIN thread only -- the surface virtually all
        detectors read. The override does NOT propagate to worker scopes, so a
        Worker/ServiceWorker still exposes the raw ``HeadlessChrome`` UA and the
        host's real high-entropy hints. Tools that cross-check window-vs-worker
        navigator (e.g. CreepJS) therefore still see one inconsistency. Closing
        that is a deliberate non-goal here: worker-scope UA spoofing needs CDP
        target auto-attach and is a fragile depth layer most sites never probe.
        """
        try:
            current_ua = await self.tab.evaluate("navigator.userAgent")
        except Exception as exc:
            logger.warning("Could not read headless user-agent: %s", exc)
            return
        if not isinstance(current_ua, str) or not current_ua:
            return
        clean_ua = current_ua.replace("HeadlessChrome", "Chrome")
        ua_changed = clean_ua != current_ua

        # Build metadata even when the live hints are unreadable. Right after
        # launch ``navigator.userAgentData.getHighEntropyValues`` can reject (UA-CH
        # not ready yet) and ``_read_client_hints`` returns None; passing ``{}``
        # lets ``_build_ua_metadata`` synthesize the brand list and infer the host
        # fields purely from the UA string. Critically, headless leaves UA-CH
        # blank regardless, so we must NEVER fall back to a UA-only override --
        # that BLANKS ``navigator.userAgentData.brands`` (the very tell we fix).
        metadata = None
        hints = await self._read_client_hints()
        try:
            metadata = self._build_ua_metadata(hints or {}, ua_string=clean_ua)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not build client-hints metadata: %s", exc)

        if metadata is None:
            # Only reachable if metadata synthesis itself failed; at that point a
            # UA-only override would blank UA-CH, so apply it solely to strip a
            # legacy headless token and otherwise leave UA-CH untouched.
            if not ua_changed:
                return
            try:
                await self.tab.send(
                    cdp_network.set_user_agent_override(user_agent=clean_ua)
                )
            except Exception as exc:
                logger.warning("Could not override headless user-agent: %s", exc)
            return

        try:
            await self.tab.send(
                cdp_network.set_user_agent_override(
                    user_agent=clean_ua, user_agent_metadata=metadata
                )
            )
        except Exception as exc:
            logger.warning("Could not override headless user-agent: %s", exc)
            return

        if ua_changed:
            try:
                await self.add_script_on_new_document(
                    "Object.defineProperty(navigator, 'userAgent', "
                    f"{{get: () => {json.dumps(clean_ua)}, configurable: true}});"
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not inject UA new-document script: %s", exc)
        logger.info(
            "Applied headless UA-CH metadata (brands populated; UA %s).",
            "rewritten" if ua_changed else "unchanged",
        )

    async def _read_client_hints(self) -> dict[str, Any] | None:
        """Read the browser's own high-entropy User-Agent Client Hints."""
        script = """
        (async () => {
          const uad = navigator.userAgentData;
          if (!uad) return null;
          let high = {};
          try {
            high = await uad.getHighEntropyValues([
              "platform", "platformVersion", "architecture",
              "bitness", "model", "fullVersionList"
            ]);
          } catch (e) {}
          return {
            brands: (uad.brands || []).map(b => ({brand: b.brand, version: b.version})),
            mobile: !!uad.mobile,
            platform: high.platform || uad.platform || "",
            platformVersion: high.platformVersion || "",
            architecture: high.architecture || "",
            bitness: high.bitness || "",
            model: high.model || "",
            fullVersionList: (high.fullVersionList || []).map(b => ({brand: b.brand, version: b.version})),
          };
        })()
        """
        try:
            data = await self.tab.evaluate(script, await_promise=True, return_by_value=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not read client hints: %s", exc)
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _chrome_versions(ua_string: str | None) -> tuple[str, str] | None:
        """Extract ``(major, full)`` Chrome version from a UA string, if present."""
        if not ua_string:
            return None
        match = re.search(r"Chrome/(\d+)(?:\.[\d.]+)?", ua_string)
        if not match:
            return None
        full_match = re.search(r"Chrome/([\d.]+)", ua_string)
        full = full_match.group(1) if full_match else f"{match.group(1)}.0.0.0"
        return match.group(1), full

    @staticmethod
    def _infer_platform_hints(ua_string: str | None) -> tuple[str, str, str, str]:
        """Infer ``(platform, platformVersion, architecture, bitness)`` from a UA.

        Used only when the live browser's real high-entropy hints are
        unreadable (e.g. a custom UA set at launch while on ``about:blank``).
        Getting ``platform`` right is what keeps ``Sec-CH-UA-Platform`` consistent
        with ``navigator.userAgent``; the higher-entropy fields are best-effort.
        """
        ua = ua_string or ""
        if "Windows" in ua:
            return ("Windows", "15.0.0", "x86", "64")
        if "Macintosh" in ua or "Mac OS X" in ua:
            return ("macOS", "15.0.0", "x86" if "Intel" in ua else "arm", "64")
        if "Android" in ua:
            match = re.search(r"Android (\d+)", ua)
            return ("Android", f"{match.group(1)}.0.0" if match else "14.0.0", "arm", "64")
        if "CrOS" in ua:
            return ("Chrome OS", "", "x86", "64")
        if "Linux" in ua or "X11" in ua:
            return ("Linux", "", "x86", "64")
        return ("", "", "", "")

    def _synthesize_brands(self, major: str, full: str) -> tuple[list[Any], list[Any]]:
        """Build a plausible Chromium brand set when real hints are unavailable.

        Reusing the live browser's own hints is always preferred (it carries the
        exact, version-correct GREASE brand); this is only a last-resort fallback
        so a custom UA never ships with empty ``userAgentData.brands``.
        """
        emu = cdp_emulation
        grease = 'Not;A=Brand'
        brands = [
            emu.UserAgentBrandVersion(brand=grease, version="99"),
            emu.UserAgentBrandVersion(brand="Chromium", version=major),
            emu.UserAgentBrandVersion(brand="Google Chrome", version=major),
        ]
        full_list = [
            emu.UserAgentBrandVersion(brand=grease, version="99.0.0.0"),
            emu.UserAgentBrandVersion(brand="Chromium", version=full),
            emu.UserAgentBrandVersion(brand="Google Chrome", version=full),
        ]
        return brands, full_list

    # navigator.platform uses frozen legacy tokens ("MacIntel", "Win32") while
    # navigator.userAgentData.platform / Sec-CH-UA-Platform uses brand names
    # ("macOS", "Windows").  Profiles store the legacy token; this table maps
    # it to the UA-CH form for _build_ua_metadata.
    _LEGACY_TO_UACH_PLATFORM: ClassVar[dict[str, str]] = {
        "MacIntel": "macOS",
        "Win32": "Windows",
        "Win64": "Windows",
        "Linux x86_64": "Linux",
        "Linux armv81": "Linux",
        "iPhone": "iOS",
        "iPad": "iOS",
    }

    # Chrome's reduced UA string uses frozen OS tokens regardless of actual OS
    # version (see chromium.org/updates/ua-reduction).  When a profile sets
    # ``platform`` without an explicit ``user_agent``, we rewrite the host's own
    # UA to carry the token matching the target platform so
    # ``navigator.userAgent`` and ``navigator.platform`` stay consistent.
    _PLATFORM_TO_UA_OS_TOKEN: ClassVar[dict[str, str]] = {
        "MacIntel": "Macintosh; Intel Mac OS X 10_15_7",
        "Win32": "Windows NT 10.0; Win64; x64",
        "Win64": "Windows NT 10.0; Win64; x64",
        "Linux x86_64": "X11; Linux x86_64",
        "Linux armv81": "Linux; Android 10; K",
    }

    @classmethod
    def _align_ua_to_platform(cls, ua: str, platform: str) -> str:
        """Rewrite the OS token in a Chrome UA string to match *platform*.

        Chrome UA strings follow ``Mozilla/5.0 (<os-token>) AppleWebKit/…``.
        This replaces the first parenthesised section with the frozen token
        that Chrome uses for the given ``navigator.platform`` value, keeping
        the Chrome version and everything else intact.
        """
        token = cls._PLATFORM_TO_UA_OS_TOKEN.get(platform)
        if not token:
            return ua
        return re.sub(r"\([^)]+\)", f"({token})", ua, count=1)

    def _build_ua_metadata(
        self,
        hints: dict[str, Any],
        *,
        platform_override: str | None = None,
        ua_string: str | None = None,
    ) -> Any:
        """Build a CDP ``UserAgentMetadata`` consistent with the active UA.

        ``platform_override`` is the legacy ``navigator.platform`` token (e.g.
        ``"MacIntel"``); it is automatically mapped to the corresponding UA-CH
        brand name (``"macOS"``) for ``navigator.userAgentData.platform`` /
        ``Sec-CH-UA-Platform``. ``ua_string`` lets us re-version the Chromium /
        Google Chrome brands to match a custom user-agent (so the low-entropy
        brands and the full-version list agree with ``navigator.userAgent``).
        """
        emu = cdp_emulation
        versions = self._chrome_versions(ua_string)

        def _is_chromium(brand: str) -> bool:
            low = brand.lower()
            return "chrom" in low  # Chromium + Google Chrome, never the GREASE brand

        def _brand_list(raw: Any, *, full: bool) -> list[Any]:
            out: list[Any] = []
            for item in raw or []:
                brand = str(item.get("brand", "") or "")
                if not brand:
                    continue
                brand = brand.replace("HeadlessChrome", "Google Chrome")
                version = str(item.get("version", "") or "")
                if versions and _is_chromium(brand):
                    version = versions[1] if full else versions[0]
                out.append(emu.UserAgentBrandVersion(brand=brand, version=version))
            return out

        brands = _brand_list(hints.get("brands"), full=False)
        full_version_list = _brand_list(hints.get("fullVersionList"), full=True)
        # Fall back to a synthesized set so a custom UA never blanks UA-CH.
        if not brands and versions:
            brands, full_version_list = self._synthesize_brands(versions[0], versions[1])
        # Infer host fields when the live hints are unavailable (about:blank).
        inferred = (
            self._infer_platform_hints(ua_string) if not hints.get("platform") else None
        )

        def _field(key: str, idx: int) -> str:
            real = str(hints.get(key, "") or "")
            if real:
                return real
            return inferred[idx] if inferred else ""

        if platform_override:
            platform_value = self._LEGACY_TO_UACH_PLATFORM.get(
                platform_override, platform_override
            )
        else:
            platform_value = _field("platform", 0)
        return emu.UserAgentMetadata(
            platform=platform_value,
            platform_version=_field("platformVersion", 1),
            architecture=_field("architecture", 2),
            model=str(hints.get("model", "") or ""),
            mobile=bool(hints.get("mobile", False)),
            brands=brands or None,
            full_version_list=full_version_list or None,
            bitness=_field("bitness", 3),
        )

    async def apply_fingerprint(self, fp: FingerprintConfig) -> dict[str, Any]:
        """Apply an identity to the live session, engine-level where possible.

        Order and mechanism are chosen for *consistency*: CDP ``Emulation.*``
        overrides (timezone, locale, UA/Accept-Language/platform, geolocation,
        hardware concurrency, device metrics, touch) are applied inside Chromium
        so they reach Web Workers and HTTP headers. Only ``deviceMemory`` and the
        optional WebGL strings — which have no CDP override — are injected as
        new-document JS (and eval'd once on the current document for immediate
        effect).
        """
        if self.tab is None:
            raise RuntimeError("Browser not started")
        emu = cdp_emulation
        applied: dict[str, Any] = {}

        if fp.timezone_id:
            try:
                await self.tab.send(emu.set_timezone_override(timezone_id=fp.timezone_id))
                self.timezone_id = fp.timezone_id
                applied["timezone_id"] = fp.timezone_id
            except Exception as exc:  # noqa: BLE001
                logger.warning("setTimezoneOverride(%s) failed: %s", fp.timezone_id, exc)

        if fp.locale:
            try:
                await self.tab.send(emu.set_locale_override(locale=fp.locale))
                applied["locale"] = fp.locale
            except Exception as exc:  # noqa: BLE001
                logger.warning("setLocaleOverride(%s) failed: %s", fp.locale, exc)

        # User-Agent / Accept-Language / platform share one CDP call. We only
        # issue it when at least one of those is requested, and we always pass a
        # user_agent (the current one if unchanged) because the param is required.
        accept_language = fp.effective_accept_language
        effective_ua: str | None = None
        if fp.user_agent or fp.platform or accept_language:
            try:
                current_ua = await self.tab.evaluate("navigator.userAgent")
            except Exception:  # noqa: BLE001
                current_ua = None
            ua_string = fp.user_agent or (current_ua if isinstance(current_ua, str) else None)
            # When the profile sets platform but not user_agent, the host's
            # own UA may carry the wrong OS token (e.g. Linux host spoofing a
            # Mac identity).  Rewrite the parenthesised OS section to match.
            if ua_string and fp.platform and not fp.user_agent:
                ua_string = self._align_ua_to_platform(ua_string, fp.platform)
            if ua_string:
                metadata = None
                if fp.user_agent or fp.platform:
                    effective_ua = ua_string
                    hints = await self._read_client_hints()
                    try:
                        # Build even when live hints are empty: a custom UA
                        # falls back to a synthesized brand set so UA-CH is never
                        # blanked (which is itself a strong bot signal).
                        metadata = self._build_ua_metadata(
                            hints or {},
                            platform_override=fp.platform,
                            ua_string=ua_string,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("UA metadata build failed: %s", exc)
                kwargs: dict[str, Any] = {"user_agent": ua_string}
                if accept_language:
                    kwargs["accept_language"] = accept_language
                if fp.platform:
                    kwargs["platform"] = fp.platform
                if metadata is not None:
                    kwargs["user_agent_metadata"] = metadata
                try:
                    await self.tab.send(emu.set_user_agent_override(**kwargs))
                    if accept_language:
                        applied["accept_language"] = accept_language
                    if effective_ua:
                        applied["user_agent"] = effective_ua
                    if fp.platform:
                        applied["platform"] = fp.platform
                except Exception as exc:  # noqa: BLE001
                    logger.warning("setUserAgentOverride failed: %s", exc)

        if fp.latitude is not None and fp.longitude is not None:
            try:
                await self.tab.send(
                    emu.set_geolocation_override(
                        latitude=fp.latitude,
                        longitude=fp.longitude,
                        accuracy=fp.geo_accuracy if fp.geo_accuracy is not None else 50.0,
                    )
                )
                # The override only supplies coordinates; the page still needs
                # the geolocation permission or getCurrentPosition() times out.
                # Granting it browser-wide mirrors a user who allowed location.
                await self._grant_geolocation_permission()
                applied["geolocation"] = {"latitude": fp.latitude, "longitude": fp.longitude}
            except Exception as exc:  # noqa: BLE001
                logger.warning("setGeolocationOverride failed: %s", exc)

        if fp.hardware_concurrency is not None:
            try:
                await self.tab.send(
                    emu.set_hardware_concurrency_override(
                        hardware_concurrency=int(fp.hardware_concurrency)
                    )
                )
                applied["hardware_concurrency"] = int(fp.hardware_concurrency)
            except Exception as exc:  # noqa: BLE001
                logger.warning("setHardwareConcurrencyOverride failed: %s", exc)

        if fp.has_device_metrics:
            try:
                await self.tab.send(
                    emu.set_device_metrics_override(
                        width=int(fp.screen_width),
                        height=int(fp.screen_height),
                        device_scale_factor=float(fp.device_scale_factor or 1.0),
                        mobile=bool(fp.mobile),
                        # Without screen_width/height, only the viewport
                        # (innerWidth/innerHeight) changes while screen.width/
                        # height keep the host values -> innerWidth can exceed
                        # screen.width, an impossible, easily-flagged state.
                        screen_width=int(fp.screen_width),
                        screen_height=int(fp.screen_height),
                    )
                )
                applied["device_metrics"] = {
                    "width": int(fp.screen_width),
                    "height": int(fp.screen_height),
                    "device_scale_factor": float(fp.device_scale_factor or 1.0),
                    "mobile": bool(fp.mobile),
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("setDeviceMetricsOverride failed: %s", exc)

        if fp.max_touch_points is not None:
            try:
                await self.tab.send(
                    emu.set_touch_emulation_enabled(
                        enabled=int(fp.max_touch_points) > 0,
                        max_touch_points=int(fp.max_touch_points) or 1,
                    )
                )
                applied["max_touch_points"] = int(fp.max_touch_points)
            except Exception as exc:  # noqa: BLE001
                logger.warning("setTouchEmulationEnabled failed: %s", exc)

        # JS-only overrides (no CDP equivalent): deviceMemory, WebGL strings,
        # and belt-and-suspenders userAgent/platform overrides.
        document_js = self._fingerprint_document_js(fp, effective_ua=effective_ua)
        if document_js:
            try:
                await self.add_script_on_new_document(document_js)
            except Exception as exc:  # noqa: BLE001
                logger.debug("fingerprint new-document script failed: %s", exc)
            try:
                await self.tab.evaluate(document_js)
            except Exception as exc:  # noqa: BLE001
                logger.debug("fingerprint immediate eval failed: %s", exc)
            if fp.device_memory is not None:
                applied["device_memory"] = fp.device_memory
            if fp.webgl_vendor or fp.webgl_renderer:
                applied["webgl"] = {
                    "vendor": fp.webgl_vendor,
                    "renderer": fp.webgl_renderer,
                }

        self.fingerprint = self.fingerprint.merged_with(fp)
        logger.info("Applied fingerprint overrides: %s", sorted(applied))
        return applied

    async def _grant_geolocation_permission(self) -> None:
        """Grant geolocation permission for the active tab's browser context.

        Sent over the browser-level connection (Browser-domain command) and
        scoped to the tab's ``browserContextId`` so the grant actually applies
        to the context the page lives in — otherwise the page keeps prompting.
        """
        if self.browser is None:
            return
        connection = getattr(self.browser, "connection", None)
        if connection is None:
            return
        context_id = None
        target = getattr(self.tab, "target", None)
        if target is not None:
            context_id = getattr(target, "browser_context_id", None)
        try:
            await connection.send(
                cdp_browser.grant_permissions(
                    permissions=[cdp_browser.PermissionType.GEOLOCATION],
                    browser_context_id=context_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("grantPermissions(geolocation) failed: %s", exc)

    def _worker_bootstrap_js(
        self, fp: FingerprintConfig, *, effective_ua: str | None = None,
    ) -> str:
        """JS run *inside* each worker to re-assert JS-only navigator props.

        CDP timezone/locale/hardwareConcurrency overrides already reach workers,
        but navigator.language(s) (ignored by --lang on macOS) and
        navigator.deviceMemory (no CDP override at all) do not, so a worker would
        otherwise read host values and trip a main-vs-worker mismatch.
        """
        lines: list[str] = []
        if fp.languages:
            lines.append(
                "Object.defineProperty(p,'languages',{get:function(){return %s;},configurable:true});"
                % json.dumps(fp.languages)
            )
            lines.append(
                "Object.defineProperty(p,'language',{get:function(){return %s;},configurable:true});"
                % json.dumps(fp.primary_language or fp.languages[0])
            )
        if fp.device_memory is not None:
            lines.append(
                "Object.defineProperty(p,'deviceMemory',{get:function(){return %s;},configurable:true});"
                % json.dumps(fp.device_memory)
            )
        # hardwareConcurrency: CDP setHardwareConcurrencyOverride covers the main
        # thread but NOT workers, so re-assert it here for worker consistency.
        if fp.hardware_concurrency is not None:
            lines.append(
                "Object.defineProperty(p,'hardwareConcurrency',{get:function(){return %s;},configurable:true});"
                % json.dumps(int(fp.hardware_concurrency))
            )
        # platform: CDP Emulation.setUserAgentOverride(platform=...) only
        # reaches Navigator::platform() (main document); WorkerNavigator
        # inherits NavigatorBase::platform() which reads the real host OS.
        if fp.platform:
            lines.append(
                "Object.defineProperty(p,'platform',{get:function(){return %s;},configurable:true});"
                % json.dumps(fp.platform)
            )
        # userAgent: when the UA was aligned to match the target platform, the
        # worker must agree with the main thread or CreepJS flags the mismatch.
        if effective_ua:
            lines.append(
                "Object.defineProperty(p,'userAgent',{get:function(){return %s;},configurable:true});"
                % json.dumps(effective_ua)
            )
        nav_block = (
            "try{var p=Object.getPrototypeOf(navigator);" + "".join(lines) + "}catch(e){}"
            if lines
            else ""
        )
        # OffscreenCanvas WebGL lives in the worker too; without the same
        # getParameter patch a worker reports the real GPU while the main thread
        # reports the spoofed one -> CreepJS flags the mismatch. The WebGL patch
        # depends on __nrMask, so pull in the native-toString shim here as well.
        webgl_block = self._webgl_patch_js(fp)
        parts: list[str] = []
        if webgl_block:
            parts.append(self._NATIVE_MASK_PREAMBLE)
        if nav_block:
            parts.append(nav_block)
        if webgl_block:
            parts.append(webgl_block)
        return "".join(parts)

    def _webgl_patch_js(self, fp: FingerprintConfig) -> str:
        """getParameter override for UNMASKED vendor/renderer (assumes __nrMask).

        Uses ``self.*`` so the same source works in a document and in a worker
        (OffscreenCanvas) global scope.
        """
        if not (fp.webgl_vendor or fp.webgl_renderer):
            return ""
        vendor = json.dumps(fp.webgl_vendor or "")
        renderer = json.dumps(fp.webgl_renderer or "")
        return (
            """
            try {
              const V = %s, R = %s;
              const patch = (proto) => {
                if (!proto || !proto.getParameter) return;
                const orig = proto.getParameter;
                const wrapped = function getParameter(p) {
                  if (V && p === 37445) return V;   // UNMASKED_VENDOR_WEBGL
                  if (R && p === 37446) return R;   // UNMASKED_RENDERER_WEBGL
                  return orig.apply(this, arguments);
                };
                __nrMask(wrapped, "getParameter");
                proto.getParameter = wrapped;
              };
              patch(self.WebGLRenderingContext && self.WebGLRenderingContext.prototype);
              patch(self.WebGL2RenderingContext && self.WebGL2RenderingContext.prototype);
            } catch (e) {}
            """
            % (vendor, renderer)
        )

    # Shared preamble: makes any function we patch report
    # `function <name>() { [native code] }` via a LOCAL own-`toString` per fn.
    #
    # History: this used to install a GLOBAL `Function.prototype.toString` shim
    # (backed by a WeakMap) so even `Function.prototype.toString.call(fn)` read
    # native. That defeats the strongest probe, BUT globally reassigning
    # `Function.prototype.toString` is ITSELF a strong CreepJS tell -- it
    # cascaded into ~9 component "lies" (Timezone/WebGL/Canvas/Audio/Math/...),
    # taking a spoofed session from 1 lie to 10 (measured). A local own-toString
    # leaves the global pristine: `fn.toString()` / `fn + ''` read native (the
    # common checks) while only the rarer `Function.prototype.toString.call(fn)`
    # of a specific patched member can still reveal it -- an accepted depth-layer
    # gap, far cheaper than tripping 9 lies. Call sites are unchanged
    # (`__nrMask(fn, name)`).
    _NATIVE_MASK_PREAMBLE = """
        const __nrMask = (fn, name) => {
          try {
            const ts = function toString() { return "function " + name + "() { [native code] }"; };
            Object.defineProperty(fn, "toString", { value: ts, configurable: true, writable: true });
          } catch (e) {}
          return fn;
        };
    """

    def _fingerprint_document_js(
        self, fp: FingerprintConfig, *, effective_ua: str | None = None,
    ) -> str | None:
        """Build the JS for properties Chromium has no CDP override for."""
        blocks: list[str] = []
        worker_boot = self._worker_bootstrap_js(fp, effective_ua=effective_ua)
        wants_webgl = bool(fp.webgl_vendor or fp.webgl_renderer)
        # The native-toString mask must be defined before any patched function.
        if worker_boot or wants_webgl:
            blocks.append(self._NATIVE_MASK_PREAMBLE)
        # Wrap the classic Worker constructor so every worker first re-asserts
        # the JS-only navigator props (language(s), deviceMemory) before running
        # its real script (loaded transparently via importScripts).
        if worker_boot:
            blocks.append(
                """
                try {
                  const BOOT = %s;
                  const NativeWorker = self.Worker;
                  if (NativeWorker && !NativeWorker.__nrPatched) {
                    const Wrapped = function Worker(url, options) {
                      try {
                        if (!options || options.type !== 'module') {
                          const abs = new URL(url, self.location.href).href;
                          const src = BOOT + ";importScripts(" + JSON.stringify(abs) + ");";
                          const burl = URL.createObjectURL(
                            new Blob([src], { type: 'text/javascript' })
                          );
                          return new NativeWorker(burl, options);
                        }
                      } catch (e) {}
                      return new NativeWorker(url, options);
                    };
                    Wrapped.prototype = NativeWorker.prototype;
                    Wrapped.__nrPatched = true;
                    __nrMask(Wrapped, "Worker");
                    self.Worker = Wrapped;
                  }
                } catch (e) {}
                """
                % json.dumps(worker_boot)
            )
        if fp.device_memory is not None:
            blocks.append(
                "try{Object.defineProperty(navigator,'deviceMemory',"
                f"{{get:()=>{json.dumps(fp.device_memory)},configurable:true}});}}catch(e){{}}"
            )
        # Belt-and-suspenders for navigator.platform: the CDP platform param
        # on Emulation.setUserAgentOverride handles most environments, but on
        # some Chromium builds (e.g. snap on Ubuntu) it silently fails while
        # the userAgentMetadata path still works.  A prototype-level JS
        # override catches that gap and is harmless when CDP already succeeded
        # (the value is identical).
        if fp.platform:
            blocks.append(
                "try{Object.defineProperty(Object.getPrototypeOf(navigator),'platform',"
                f"{{get:()=>{json.dumps(fp.platform)},configurable:true}});}}catch(e){{}}"
            )
        # Belt-and-suspenders for navigator.userAgent: the CDP user_agent param
        # handles most reads, but _clean_headless_ua may have installed an
        # earlier instance-level JS override (HeadlessChrome cleanup) that
        # shadows the CDP value.  We must use instance-level here too (not
        # prototype-level) so we overwrite that earlier configurable property.
        if effective_ua:
            blocks.append(
                "try{Object.defineProperty(navigator,'userAgent',"
                f"{{get:()=>{json.dumps(effective_ua)},configurable:true}});}}catch(e){{}}"
            )
        if wants_webgl:
            blocks.append(self._webgl_patch_js(fp))
        if not blocks:
            return None
        return "(()=>{" + "".join(blocks) + "})();"

    async def apply_timezone_override(self, timezone_id: str) -> None:
        """Pin the JS timezone via CDP ``Emulation.setTimezoneOverride``."""
        if not timezone_id or self.tab is None:
            return
        try:
            await self.tab.send(cdp_emulation.set_timezone_override(timezone_id=timezone_id))
            self.timezone_id = timezone_id
            logger.info("Applied timezone override: %s", timezone_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not set timezone override (%s): %s", timezone_id, exc)
