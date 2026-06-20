"""Detection-site probes for the mithwire stealth diagnostic.

Each probe is a self-contained JS expression that runs in the page and returns
a plain (JSON-serializable) value. Probes are **readiness-gated**: they poll the
page for their authoritative result to exist (and stabilize) rather than relying
on a fixed sleep, so they behave correctly on slow links and never sample a
half-rendered page. See ``SITE_PARSING`` notes inline for why each parse is the
robust one.

This module is the single source of truth for the shared probes: the engine
stealth diagnostic (:mod:`mithwire.stealth_diagnostic`) and the mithwire-mcp
baseline harness both import these definitions so the JS never drifts.
"""
from __future__ import annotations

import json
from typing import Any

__all__ = [
    "NAV_PROBE",
    "SANNYSOFT_PROBE",
    "DEVICEANDBROWSER_PROBE",
    "CREEPJS_PROBE",
    "IPAPI_PROBE",
    "WEBRTC_PROBE",
    "SITES",
    "wrap",
    "parse",
]

# --- navigator / core fingerprint -----------------------------------------
# Captured on the first secure-context site so userAgentData & deviceMemory are
# present. These are the values most automation tells key on.
NAV_PROBE = r"""
(() => {
  const r = {};
  const safe = (f, d=null) => { try { return f(); } catch(e) { return d; } };
  r.userAgent = safe(() => navigator.userAgent);
  r.webdriverType = typeof navigator.webdriver;
  r.webdriverValue = safe(() => String(navigator.webdriver));
  r.languages = safe(() => navigator.languages);
  r.platform = safe(() => navigator.platform);
  r.vendor = safe(() => navigator.vendor);
  r.hardwareConcurrency = safe(() => navigator.hardwareConcurrency);
  r.deviceMemory = safe(() => navigator.deviceMemory);
  r.timezone = safe(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  r.screen = safe(() => ({ w: screen.width, h: screen.height, depth: screen.colorDepth }));
  r.dpr = safe(() => devicePixelRatio);
  r.hasChrome = safe(() => !!window.chrome);
  r.hasChromeRuntime = safe(() => !!(window.chrome && window.chrome.runtime));
  r.uaData = safe(() => navigator.userAgentData ? {
    mobile: navigator.userAgentData.mobile,
    platform: navigator.userAgentData.platform,
    brands: (navigator.userAgentData.brands || []).map(b => b.brand + ' ' + b.version)
  } : null);
  r.webgl = safe(() => {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    return { vendor: gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL), renderer: gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) };
  }, { err: true });
  // `Function.prototype.toString` / patched-native tells: a spoof that replaces
  // a native fn with a JS one (without masking) shows up here as non-native.
  r.nativeToString = safe(() => ({
    getParameter: ('' + WebGLRenderingContext.prototype.getParameter).includes('[native code]'),
    permissionsQuery: ('' + navigator.permissions.query).includes('[native code]'),
    fnToString: ('' + Function.prototype.toString).includes('[native code]')
  }));
  return r;
})()
"""

# deviceandbrowserinfo computes its verdict SERVER-SIDE and renders the returned
# JSON into a <pre><code class="language-json"> block. Anchor on that element
# (textContent flattens Prism's spans to clean JSON); self-poll until it parses.
DEVICEANDBROWSER_PROBE = r"""
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const read = () => {
    const el = document.querySelector('code.language-json')
      || document.querySelector('pre code');
    if (el && el.textContent && el.textContent.trim().charAt(0) === '{') {
      try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }
    return null;
  };
  const deadline = Date.now() + 25000;
  let v = read();
  while (!v && Date.now() < deadline) { await sleep(250); v = read(); }
  if (!v) return { ready: false, error: 'no-verdict' };
  return { ready: true, isBot: v.isBot, details: v.details || {} };
})()
"""

# sannysoft's real verdicts are the 8 `td.result` cells (each with a stable id).
# Plain `.passed` cells are the fp2 data rows (always green) -- NOT tests -- so
# key strictly off `td.result`. Readiness = every cell has text AND the
# (id,verdict) signature held stable for one extra cycle (the page hard-codes
# `failed` at parse time and swaps to `passed` async, so polling on 'unknown'
# false-passes the red state).
SANNYSOFT_PROBE = r"""
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const verdict = (cls) => /failed/.test(cls) ? 'failed'
    : /warn/.test(cls) ? 'warn' : /passed/.test(cls) ? 'passed' : 'unknown';
  const name = (td) => {
    const tr = td.closest('tr');
    return tr && tr.cells[0]
      ? tr.cells[0].innerText.replace(/\s+/g, ' ').trim() : (td.id || '');
  };
  const collect = () => [...document.querySelectorAll('td.result')].map((td) => ({
    id: td.id, name: name(td), verdict: verdict(td.className),
    value: (td.innerText || '').trim(),
  }));
  const allFilled = (rows) => rows.length > 0 && rows.every((r) => r.value.length > 0);
  const signature = (rows) => rows.map((r) => r.id + ':' + r.verdict).join(',');
  const deadline = Date.now() + 12000;
  let rows = collect(), lastSig = '', stableSince = -1;
  while (Date.now() < deadline) {
    if (allFilled(rows)) {
      const sig = signature(rows);
      if (sig === lastSig) {
        if (stableSince < 0) stableSince = Date.now();
        if (Date.now() - stableSince >= 400) break;
      } else { lastSig = sig; stableSince = -1; }
    }
    await sleep(200);
    rows = collect();
  }
  return {
    total: rows.length,
    passed: rows.filter((r) => r.verdict === 'passed').length,
    failed: rows.filter((r) => r.verdict === 'failed').map((r) => r.id || r.name),
    warn: rows.filter((r) => r.verdict === 'warn').map((r) => r.id || r.name),
    rows: rows.map((r) => ({ ...r, value: r.value.slice(0, 40) })),
  };
})()
"""

# CreepJS renders progressively; there is NO plain-text trust score in this
# build. Stable signal = the `.lies` count (spoofing inconsistencies it caught;
# 0 on a clean browser). Gate readiness on the fuzzy hash being populated with a
# non-zero hex char (it renders a 16-zero placeholder before computing).
CREEPJS_PROBE = r"""
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const safe = (f, d=null) => { try { return f(); } catch (e) { return d; } };
  const fuzzyHex = () => safe(() => {
    const e = document.querySelector('.fuzzy-fp');
    return e ? (e.innerText || '').replace(/fuzzy:?/i, '').replace(/[^0-9a-f]/gi, '') : '';
  }, '') || '';
  const fuzzyReady = () => { const h = fuzzyHex(); return h.length >= 16 && /[1-9a-f]/.test(h); };
  const deadline = Date.now() + 24000;
  while (Date.now() < deadline && !fuzzyReady()) { await sleep(300); }
  const txt = safe(() => document.body.innerText, '') || '';
  const lies = safe(() => [...document.querySelectorAll('.lies')], []) || [];
  const categories = lies.map((e) => {
    const row = e.closest('div');
    return (row ? (row.innerText || '') : (e.textContent || '')).replace(/\s+/g, ' ').trim().slice(0, 40);
  });
  const fpId = safe(() => { const m = txt.match(/FP ID:\s*([0-9a-f]{16,})/i); return m ? m[1] : null; });
  return {
    ready: fuzzyReady(),
    lieNodes: lies.length,
    lieCategories: categories,
    fpId: fpId,
    fuzzyHash: fuzzyHex().slice(0, 16) || null,
  };
})()
"""

# IP / geo ground truth. The body is raw JSON; parse directly. Used to compare
# the egress IP's timezone against the browser's Intl timezone (a mismatch is a
# classic tell) and to surface datacenter/proxy/vpn flags on the exit IP.
IPAPI_PROBE = r"""
(() => {
  const safe = (f, d=null) => { try { return f(); } catch (e) { return d; } };
  const raw = safe(() => document.body.innerText, '') || '';
  let j = null;
  try { j = JSON.parse(raw); } catch (e) { return { ready: false, error: String(e) }; }
  const loc = j.location || {};
  return {
    ready: true,
    ip: j.ip, country: loc.country, timezone: loc.timezone,
    is_proxy: j.is_proxy, is_vpn: j.is_vpn, is_datacenter: j.is_datacenter,
    is_tor: j.is_tor, is_abuser: j.is_abuser, is_crawler: j.is_crawler, is_mobile: j.is_mobile,
    asn: (j.asn || {}).descr || (j.asn || {}).org || null,
  };
})()
"""

# Deterministic WebRTC leak probe: drive our own RTCPeerConnection against a
# public STUN server and WAIT for ICE gathering to complete (9s cap) before
# reporting every candidate. A public srflx address that isn't the egress IP is
# a real-IP leak. Runs on a secure (https) page.
WEBRTC_PROBE = r"""
(async () => {
  if (typeof RTCPeerConnection === 'undefined') return { ready: false, error: 'no-rtc' };
  const cands = [];
  const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
  try {
    pc.createDataChannel('probe');
    await pc.setLocalDescription(await pc.createOffer());
  } catch (e) {
    try { pc.close(); } catch (_) {}
    return { ready: false, error: 'offer-failed:' + String(e) };
  }
  let complete = false;
  await new Promise((resolve) => {
    pc.onicegatheringstatechange = () => {
      if (pc.iceGatheringState === 'complete') { complete = true; resolve(); }
    };
    pc.onicecandidate = (e) => {
      if (!e.candidate) { complete = true; resolve(); return; }
      const c = e.candidate.candidate || '';
      const parts = c.split(' ');
      cands.push({ addr: parts[4] || '', typ: (c.match(/ typ (\S+)/) || [])[1] || '' });
    };
    setTimeout(resolve, 9000);
  });
  try { pc.close(); } catch (_) {}
  return { ready: true, gatheringComplete: complete, candidates: cands };
})()
"""

# (key, url, nav_wait_s, probe_js, probe_timeout_s). Self-polling probes gate on
# readiness internally; probe_timeout MUST exceed the in-JS deadline so a timely
# -but-late page isn't cut off by the outer guard. The first entry must be a
# secure-context site (the navigator probe runs there).
SITES: list[tuple[str, str, float, str, float]] = [
    ("deviceandbrowserinfo", "https://deviceandbrowserinfo.com/are_you_a_bot", 2.0, DEVICEANDBROWSER_PROBE, 32.0),
    ("sannysoft", "https://bot.sannysoft.com/", 1.5, SANNYSOFT_PROBE, 18.0),
    ("creepjs", "https://abrahamjuliot.github.io/creepjs/", 2.0, CREEPJS_PROBE, 32.0),
    ("ipapi", "https://api.ipapi.is/", 1.5, IPAPI_PROBE, 12.0),
]


def wrap(expr: str) -> str:
    """Force a probe to resolve to a JSON string.

    ``Tab.evaluate(return_by_value=True)`` hands back a RemoteObject for nested
    objects; serializing to a string in-page and ``json.loads``-ing it in Python
    yields a uniform plain value across sync and async probes. ``Promise.resolve``
    lets a probe be a plain IIFE *or* an async (readiness-polling) one.
    """
    return f"Promise.resolve(({expr})).then((v) => JSON.stringify(v))"


def parse(value: Any) -> Any:
    """Decode the JSON string a wrapped probe returns (tolerant of failures)."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return {"__unparsed__": value[:500]}
    return value
