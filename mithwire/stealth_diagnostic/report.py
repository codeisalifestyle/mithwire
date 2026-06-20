"""Turn raw stealth-diagnostic probe output into a verdict + actionable findings.

The stealth diagnostic is exactly that: it reveals how a freshly-installed mithwire
browser looks to common detectors on *this* machine, so the operator can adjust
their own client (flags, headful vs headless, timezone, proxy, …) and re-run to
confirm. It does not auto-fix anything — it reports clearly and, where a signal
is a well-established tell, attaches a short factual hint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Verdict / severity levels, ordered.
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
_RANK = {PASS: 0, WARN: 1, FAIL: 2}

# deviceandbrowserinfo flags that indicate concrete automation (hard fail) vs
# soft heuristics (warn). Kept deliberately small; unknown true flags warn.
_DAB_HARD_FLAGS = {
    "isHeadlessChrome",
    "hasWebdriverTrue",
    "isAutomatedWithCDP",
    "isWebGLInconsistent",
    "hasInconsistentClientHints",
}


@dataclass
class Finding:
    site: str
    signal: str
    severity: str
    detail: str
    hint: str | None = None


@dataclass
class StealthDiagnosticReport:
    headless: bool
    verdict: str = PASS
    findings: list[Finding] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict != FAIL

    def _add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if _RANK[finding.severity] > _RANK[self.verdict]:
            self.verdict = finding.severity

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "headless": self.headless,
            "signals": self.signals,
            "findings": [vars(f) for f in self.findings],
        }


def _is_errorish(v: Any) -> bool:
    return isinstance(v, dict) and any(
        k in v for k in ("__timeout__", "__error__", "__unparsed__", "__eval_error__")
    )


def build_report(raw: dict[str, Any], *, headless: bool) -> StealthDiagnosticReport:
    """Flatten raw probe output into derived signals + a graded findings list."""
    rep = StealthDiagnosticReport(headless=headless, raw=raw)
    probes = raw.get("probes", {}) if isinstance(raw, dict) else {}
    sig = rep.signals

    # --- navigator core ---------------------------------------------------
    nav = probes.get("navigator") or {}
    if isinstance(nav, dict) and not _is_errorish(nav):
        wd_val = nav.get("webdriverValue")
        sig["webdriver"] = f"{nav.get('webdriverType')}={wd_val}"
        exposed = wd_val not in (None, "false", "undefined", "null", "")
        if exposed:
            rep._add(Finding(
                "navigator", "webdriver", FAIL,
                f"navigator.webdriver is exposed ({wd_val}).",
                "Launch the browser via mithwire.start() — it removes the "
                "webdriver flag. A plain Chrome/CDP launch will be flagged.",
            ))

        ua = nav.get("userAgent") or ""
        sig["userAgent"] = ua
        if "Headless" in ua:
            rep._add(Finding(
                "navigator", "user-agent", WARN,
                "User-Agent contains a 'HeadlessChrome' token.",
                "Run headful (under Xvfb on a headless server) so the UA and "
                "client-hints match a real browser.",
            ))

        sig["timezone"] = nav.get("timezone")
        wgl = nav.get("webgl")
        if isinstance(wgl, dict) and wgl.get("err"):
            sig["webgl"] = "unavailable"
            rep._add(Finding(
                "navigator", "webgl", WARN,
                "No WebGL renderer is available.",
                "Common in headless/no-GPU containers; some fingerprinters "
                "expect a renderer. Provide one (e.g. software rendering) if "
                "your targets check it.",
            ))
        elif isinstance(wgl, dict):
            sig["webgl"] = f"{wgl.get('vendor')} / {wgl.get('renderer')}"

        nts = nav.get("nativeToString") or {}
        if isinstance(nts, dict) and (nts.get("getParameter") is False or nts.get("fnToString") is False):
            rep._add(Finding(
                "navigator", "native-functions", WARN,
                "A patched function no longer reports '[native code]'.",
                "If you override native methods, mask their toString so the "
                "tamper isn't itself detectable.",
            ))

    # --- bot.sannysoft ----------------------------------------------------
    sanny = probes.get("sannysoft") or {}
    if isinstance(sanny, dict) and not _is_errorish(sanny) and sanny.get("total"):
        failed = sanny.get("failed") or []
        sig["sannysoft"] = f"{sanny.get('passed')}/{sanny.get('total')} passed"
        if failed:
            rep._add(Finding(
                "sannysoft", "checks", FAIL,
                f"Failed checks: {', '.join(failed)}.",
                "These are concrete automation tells; inspect each failing "
                "row's value to see what leaked.",
            ))

    # --- deviceandbrowserinfo --------------------------------------------
    dab = probes.get("deviceandbrowserinfo") or {}
    if isinstance(dab, dict) and dab.get("ready"):
        details = dab.get("details") or {}
        true_flags = sorted(k for k, v in details.items() if v is True)
        sig["dab_isBot"] = dab.get("isBot")
        sig["dab_flags"] = true_flags or "none"
        hard = [f for f in true_flags if f in _DAB_HARD_FLAGS]
        if hard:
            rep._add(Finding(
                "deviceandbrowserinfo", "flags", FAIL,
                f"Concrete automation flags set: {', '.join(hard)}.",
                "Each maps to a specific tell (headless UA, webdriver, CDP "
                "automation, inconsistent fingerprint). Fix the underlying leak.",
            ))
        elif dab.get("isBot"):
            rep._add(Finding(
                "deviceandbrowserinfo", "flags", WARN,
                f"isBot=true via soft heuristics: {', '.join(true_flags) or 'unknown'}.",
                "No hard automation flag tripped. Often environmental "
                "(datacenter IP, timezone/locale inconsistency); see the "
                "timezone finding if present.",
            ))

    # --- CreepJS ----------------------------------------------------------
    creep = probes.get("creepjs") or {}
    if isinstance(creep, dict) and creep.get("ready"):
        lies = creep.get("lieNodes") or 0
        sig["creep_lies"] = lies
        if lies:
            cats = creep.get("lieCategories") or []
            rep._add(Finding(
                "creepjs", "lies", WARN,
                f"{lies} spoofing inconsistency(ies): {', '.join(cats) or 'see report'}.",
                "CreepJS cross-checks the main thread against Worker scopes. A "
                "single headless Navigator lie is a known engine-only gap; "
                "more usually means a spoof disagrees across scopes.",
            ))

    # --- ip / timezone alignment -----------------------------------------
    ip = probes.get("ipapi") or {}
    if isinstance(ip, dict) and ip.get("ready"):
        sig["egress_ip"] = ip.get("ip")
        sig["egress_timezone"] = ip.get("timezone")
        flags = sorted(
            k.replace("is_", "")
            for k in ("is_proxy", "is_vpn", "is_datacenter", "is_tor", "is_abuser", "is_crawler")
            if ip.get(k) is True
        )
        sig["ip_flags"] = flags or "none"
        browser_tz = nav.get("timezone") if isinstance(nav, dict) else None
        egress_tz = ip.get("timezone")
        if browser_tz and egress_tz:
            if browser_tz == egress_tz:
                sig["tz_match"] = "MATCH"
            else:
                sig["tz_match"] = f"MISMATCH ({browser_tz} vs {egress_tz})"
                rep._add(Finding(
                    "ipapi", "timezone", WARN,
                    f"Browser timezone {browser_tz} != egress IP timezone {egress_tz}.",
                    "A browser-TZ vs IP-TZ gap is a classic tell. Align the "
                    "browser to the egress zone (e.g. CDP "
                    "Emulation.setTimezoneOverride) or run in that region; pair "
                    "geo spoofing with a same-region proxy.",
                ))
        if "datacenter" in flags:
            rep._add(Finding(
                "ipapi", "ip-reputation", WARN,
                "Egress IP is flagged as datacenter.",
                "Datacenter IPs are higher-suspicion for many detectors; use a "
                "residential/mobile proxy for sensitive targets.",
            ))

    # --- WebRTC leak ------------------------------------------------------
    wrtc = probes.get("webrtc") or {}
    if isinstance(wrtc, dict) and wrtc.get("ready"):
        egress = ip.get("ip") if isinstance(ip, dict) else None
        publics: list[str] = []
        for c in wrtc.get("candidates") or []:
            addr = (c.get("addr") or "") if isinstance(c, dict) else ""
            if _classify_addr(addr) == "public" and addr not in publics:
                publics.append(addr)
        leaks = [a for a in publics if a != egress]
        if leaks:
            sig["webrtc"] = f"REAL-IP-LEAK {leaks}"
            rep._add(Finding(
                "webrtc", "ice-candidates", FAIL,
                f"WebRTC exposed a public IP that isn't the egress: {leaks}.",
                "Behind a proxy this de-anonymizes you. Filter public ICE "
                "candidates / force --force-webrtc-ip-handling-policy, or "
                "disable RTCPeerConnection if WebRTC isn't needed.",
            ))
        elif publics:
            sig["webrtc"] = "egress-only (ok)"
        else:
            sig["webrtc"] = "no-public (ok)"

    return rep


def _classify_addr(addr: str) -> str:
    import re

    a = (addr or "").strip().lower()
    if not a:
        return "empty"
    if a.endswith(".local") or "mdns" in a:
        return "mdns"
    if ":" in a:
        return "private" if a.startswith(("fe80", "fc", "fd")) else "public"
    if re.match(r"^(10\.|127\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", a):
        return "private"
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", a):
        return "public"
    return "other"


_VERDICT_ICON = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL"}


def format_report(rep: StealthDiagnosticReport, *, color: bool = True) -> str:
    """Human-readable CLI rendering of a report."""
    def c(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if color else s

    vcolor = {PASS: "32", WARN: "33", FAIL: "31"}[rep.verdict]
    lines = []
    lines.append(c("1", "mithwire stealth diagnostic"))
    mode = "headless" if rep.headless else "headful"
    lines.append(f"  mode: {mode}")
    lines.append(f"  verdict: {c(vcolor, c('1', rep.verdict))}")
    lines.append("")
    lines.append(c("1", "Signals"))
    for k, v in rep.signals.items():
        lines.append(f"  {k:<18} {v}")
    if rep.findings:
        lines.append("")
        lines.append(c("1", "Findings"))
        for f in rep.findings:
            fc = {PASS: "32", WARN: "33", FAIL: "31"}[f.severity]
            lines.append(f"  {c(fc, f.severity)} [{f.site}/{f.signal}] {f.detail}")
            if f.hint:
                lines.append(f"       -> {f.hint}")
    else:
        lines.append("")
        lines.append(c("32", "  No issues detected — this browser looks clean to the bundled detectors."))
    return "\n".join(lines)
