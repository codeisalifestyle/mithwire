<p align="center">
  <img src="assets/mithwire-banner.png" alt="mithwire" width="760">
</p>

<p align="center">
  <b>🥷 An anti-detect browser for Python.</b><br>
  Drive real Chrome straight over the DevTools Protocol — <b>no WebDriver, no Selenium, no chromedriver</b> — with stealth and Cloudflare Turnstile solving built in.
</p>

<p align="center">
  <a href="https://pypi.org/project/mithwire/"><img src="https://img.shields.io/pypi/v/mithwire?style=for-the-badge&color=d62839&label=pip%20install%20mithwire" alt="PyPI"></a>
  <a href="https://pypi.org/project/mithwire/"><img src="https://img.shields.io/pypi/pyversions/mithwire?style=for-the-badge&color=3776ab&logo=python&logoColor=white" alt="Python versions"></a>
  <a href="LICENSE.txt"><img src="https://img.shields.io/badge/license-AGPL--3.0-2ea44f?style=for-the-badge" alt="License"></a>
  <a href="https://github.com/codeisalifestyle/mithwire-mcp"><img src="https://img.shields.io/badge/🤖_agents-mithwire--mcp-d62839?style=for-the-badge" alt="mithwire-mcp"></a>
</p>

---

## 🤔 What is mithwire?

**mithwire is an anti-detect browser automation framework.** It launches a normal
Chromium-based browser (Chrome, Brave, Edge…) and controls it by talking directly
to the [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) (CDP).

There's no automation driver bolted on the side — so the tell-tale signals
anti-bot systems look for to spot WebDriver/Selenium simply **aren't there**. You
get a browser that behaves like a real one, a short and pleasant async API, and
the practical stealth fixes you actually need against modern anti-bot stacks.

## ✨ Features

🥷 **Anti-detect by design** — speaks raw CDP, so there's **no `navigator.webdriver`, no chromedriver binary, and no Selenium surface** to fingerprint

🌐 **Real Chromium browsers** — works with Chrome, Chromium, Brave, and Edge

👁️ **Headful or headless** — run with a visible window, or invisibly on a server (pair with `Xvfb` when you need a real display)

🎭 **Identity & fingerprint control** — full CDP `Emulation` access to shape what the browser presents: timezone, locale, geolocation, user agent, device metrics, and more

🌍 **Proxy support** — route traffic through upstream HTTP/SOCKS proxies

🛡️ **Cloudflare Turnstile bypass** — one call (`tab.verify_cf()`) solves the checkbox challenge, light *and* dark mode

🔎 **Smart DOM access** — find elements by **text, CSS selector, or XPath**, including inside iframes — lookups double as wait conditions

📡 **Full network & event access** — the entire CDP surface is yours: requests, responses, events, interception

🍪 **Stateful when you want it** — cookies, localStorage, multi-tab & multi-window, screenshots, and external-debugger attach

⚡ **Tiny, async API** — up and running in ~2 lines, with best-practice defaults and automatic profile cleanup

---

## 🤖 mithwire-mcp — give your AI agents a browser fleet

<p align="center">
  <a href="https://github.com/codeisalifestyle/mithwire-mcp">
    <img src="assets/mithwire-mcp-banner.jpg" alt="mithwire-mcp" width="640">
  </a>
</p>

One mithwire browser is great for a script. But what about an **AI agent that
needs to run dozens of them**? That's [**mithwire-mcp**](https://github.com/codeisalifestyle/mithwire-mcp) —
a [Model Context Protocol](https://modelcontextprotocol.io) server that turns
mithwire into a tool your LLM can drive directly.

- 🧠 **Agents launch & control their own browsers** — `session_start`, navigate, click, type, screenshot, evaluate — all as MCP tools
- 🚀 **Fleet management** — spin up many isolated sessions at once, each its own browser process, with full lifecycle control
- 👤 **Durable identities** — persistent profiles with their own cookies, proxies, and fingerprints that survive across runs
- 🕵️ **Consistent stealth at scale** — proxy-aligned timezone/locale/geo, fingerprint spoofing, and WebRTC leak protection kept in sync per worker

➡️ **[Get started with mithwire-mcp →](https://github.com/codeisalifestyle/mithwire-mcp)**

---

## 📦 Install

```bash
pip install mithwire
```

You'll need a Chromium-based browser (Chrome/Brave/Edge) installed, ideally in the
default location. On a headless server, run under [`Xvfb`](https://en.wikipedia.org/wiki/Xvfb)
or use headless mode. To upgrade:

```bash
pip install -U mithwire
```

## 🚀 Quick start

```python
import mithwire as uc

async def main():
    browser = await uc.start()
    page = await browser.get("https://www.nowsecure.nl")
    await page.save_screenshot()

uc.loop().run_until_complete(main())
```

> 💡 Use `uc.loop().run_until_complete(...)` instead of `asyncio.run(...)` for
> reliable event-loop handling.

## 🧭 Usage

### ⚙️ Custom launch options

```python
from mithwire import start

browser = await start(
    headless=False,
    user_data_dir="/path/to/profile",   # pass one and it won't be auto-cleaned on exit
    browser_executable_path="/path/to/some/other/browser",
    browser_args=["--some-flag=true"],
    lang="en-US",
)
tab = await browser.get("https://example.com")
```

Or configure via a `Config` object:

```python
from mithwire import Config

config = Config()
config.headless = False
config.user_data_dir = "/path/to/profile"
config.browser_args = ["--some-flag=true"]
```

### 🔎 Finding things on the page

```python
# by text — returns the closest match by text length, not the first hit
accept = await tab.find("accept all", best_match=True)
await accept.click()

# by CSS selector (retries until found or <timeout>, so it doubles as a wait)
email = await tab.select("input[type=email]")
imgs = await tab.select_all("a[href] > div > img")

# by XPath
node = await tab.xpath("//button[contains(., 'Next')]", timeout=2.5)
```

### 🛡️ Solving Cloudflare Turnstile

```python
page = await browser.get("https://site-behind-turnstile.example")
await page.verify_cf(max_retries=3, timeout=20, retry_interval=2)
```

Bundled with light- and dark-mode widget templates and HiDPI-correct coordinates.
Requires `opencv-python` (`pip install opencv-python`).

### 🧰 Handy tab helpers

| Method | Does |
| --- | --- |
| `tab.get_content()` | current page HTML |
| `tab.save_screenshot()` | screenshot to a temp file |
| `tab.scroll_down(n)` / `tab.scroll_up(n)` | scroll the page |
| `tab.get_local_storage()` / `tab.set_local_storage(dict)` | read/write localStorage |
| `tab.add_handler(event, cb)` | subscribe to CDP events (`cb(event)` or `cb(event, tab)`) |
| `tab.bypass_insecure_connection_warning()` | click through invalid-cert warnings |
| `tab.open_external_debugger()` | inspect a tab without breaking your connection |

A fuller, runnable script (automating account creation end to end) lives in the
[`example/`](example/) folder. 📂

## ⚖️ How it compares

Most "stealth" tools either drive the browser through WebDriver/Selenium (which
leaks an obvious automation surface) or patch over it with injected JavaScript
(which is itself detectable). mithwire skips both: it speaks the browser's own
debug protocol directly, so there's **no driver to fingerprint and no injected
shim to catch** — while still giving you the full power of CDP for low-level control.

## 🙏 Credits & license

mithwire is a maintained fork of [**nodriver**](https://github.com/UltrafunkAmsterdam/nodriver)
by UltrafunkAmsterdam, itself the successor to undetected-chromedriver. It's
distributed under the **GNU AGPL-3.0**, the same license as upstream. Original
copyright and license are preserved in [`LICENSE.txt`](LICENSE.txt); attribution
details are in [`NOTICE`](NOTICE). mithwire is not affiliated with or endorsed by
the original author.

This fork addresses real-world limitations hit when running against modern
anti-bot systems; fixes land as they're discovered during active use.

> ⚠️ **Use responsibly.** Only automate sites and accounts you're authorized to use,
> respect Terms of Service and local law, and avoid abusive request rates.
