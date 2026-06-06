MITHWIRE
=======================

### A maintained fork of [nodriver](https://github.com/ultrafunkamsterdam/nodriver) with modern anti-bot bypass improvements.

> **Origin & license.** mithwire is a fork of [nodriver](https://github.com/ultrafunkamsterdam/nodriver) by UltrafunkAmsterdam, which is itself the successor of undetected-chromedriver. It is distributed under the **GNU AGPL-3.0**, the same license as the upstream project. The original copyright and license are preserved in `LICENSE.txt`; see `NOTICE` for attribution details. mithwire is not affiliated with or endorsed by the original author.

This fork addresses real-world limitations encountered when using it against modern anti-bot systems. Fixes are applied as they are discovered during active usage across projects.

#### Improvements over upstream

- **Dark mode Cloudflare Turnstile support** — bundled templates for both light and dark mode widgets
- **HiDPI / Retina display support** — `verify_cf()` and `template_location()` now detect `devicePixelRatio` and scale templates accordingly, returning correct CSS pixel coordinates
- **Multi-template matching** — all bundled templates are tried automatically; best match wins
- **Retry logic in `verify_cf()`** — configurable `max_retries`, `timeout`, and `retry_interval` with human-like timing jitter
- **Proper temp file handling** — screenshots written to system temp dir instead of cwd

#### Installation

```bash
pip install git+https://github.com/codeisalifestyle/mithwire.git
```

---

*Original README follows:*

### mithwire provides next level async webscraping and browser automation library for python with an easy interface which Just Makes Sense ™


* **This is the official successor of the** [Undetected-Chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver/) **python package.**
* **No more webdriver, no more selenium**

### for docs click [here](https://ultrafunkamsterdam.github.io/nodriver)

Direct communication provides even better resistance against web applicatinon firewalls (WAF’s), while
performance gets a massive boost.
This module is, contrary to undetected-chromedriver, fully asynchronous.

What makes this package different from other known packages,
is the optimization to stay undetected for most anti-bot solutions.

Another focus point is usability and quick prototyping, so expect a lot to work `-as is-` ,
with most method parameters having `best practice` defaults.
Using 1 or 2 lines, this is up and running, providing best practice config
by default. It cleans up created files (profile) afterwards.

known to work with 
- chromium
- chrome
- edge
- brave 


While usability and convenience is important. It’s also easy
to fully customizable everything using the entire array of
[CDP](https://chromedevtools.github.io/devtools-protocol/) domains, methods and events available.

### Some features
* No chromedriver binary or Selenium dependency
* Up and running in 1 line of code\*
* uses fresh profile on each run, cleans up on exit
* save and load cookies to file to not repeat tedious login steps
* ```tab.find("sometext")```

  ```tab.find_all("sometext")```

  ```tab.select("a[class*=something]")```

  ```tab.select_all("a[href] > div > img")```

  smart and performant element lookup, by selector or text, including iframe content.
  this could also be used as wait condition for a element to appear, since it will retry
  for the duration of <timeout> until found. so an ```await tab.select('body')``` could be used
  as an indicator whether a page is loaded.
  the find method searches by text, but will not naively return the first 
  matching element, but will match candidates by closest matching text length (shortest wins),
  this makes lookups like tab.find('accept all') return the actual cookie button instead of
  a script in the headers

* can connect to a running chrome debug session
* descriptive \_\_repr_\_ for elements, which represent the element as html
* utility function to convert a running undetected_chromedriver.Chrome instance
  to a mithwire.Browser instance and contintue from there
* packed with helpers and utility methods for most used and important operations

what is new
--------------------

### ```tab.xpath(selector, timeout=2.5)```
find nodes using xpath selector!
see [tab xpath in the api docs](https://ultrafunkamsterdam.github.io/nodriver/nodriver/classes/tab.html#nodriver.Tab.xpath)


### ```tab.cf_verify()```
finds the checkbox and click it successfully
this only works when NOT in expert mode.
currently built-in english only
requires opencv-python package to be installed

<video autoplay loop muted playsInline src="https://github.com/user-attachments/assets/288c5e01-39c5-4453-9e64-2b40c3a8548d"></video>


### ```tab.bypass_insecure_connection_warning()```
convenience method, for insecure page warning.
for example when a certificate is invalid.

### ```tab.open_external_debugger()```
lets you inspect the tab without breaking your connection

### ```tab.get_local_storage()```
get localstorage content


### ```tab.set_local_storage(dict)```
set localstorage content

### ```tab.add_handler(someEvent, callback)```
callback may accept a single argument (event), or 2 arguments (event, tab).


### ```start(expert=True)```
does some hacking for more experienced users. It disables 
web security and origin-trials, as well as ensures 
shadow-roots are always open. This makes you more detectable though!


Installation
=================

**you need chrome (or some chromium based browser) installed
 preferably in the default location
 on the machine where you use this package.**

when running on a headless machine, like AWS or any other environment where
no display is present, it's best to use some **Xvfb** tool, to emulate a **screen**. 
alternatively this package can be used in headless mode.



#### you can use pip to install mithwire

```default
pip install mithwire
```

To update
---------

```default
pip install -U mithwire
```


<a id="getting-started-commands"></a>

usage examples
=======================

The aim of this project (just like undetected-chromedriver, somewhere long ago)
is to keep it short and simple, so you can quickly open an editor or interactive session,
type or paste a few lines and off you go.

simple 
--------------

```python
import mithwire as uc

async def main():

    browser = await uc.start()
    page = await browser.get('https://www.nowsecure.nl')

    ... further code ...

if __name__ == '__main__':
    # since asyncio.run never worked (for me)
    uc.loop().run_until_complete(main())
```


Custom starting options
---------------------
I’ll leave out the async boilerplate here

```python
from mithwire import *

browser = await start(
    headless=False,
    user_data_dir="/path/to/existing/profile",  # by specifying it, it won't be automatically cleaned up when finished
    browser_executable_path="/path/to/some/other/browser",
    browser_args=['--some-browser-arg=true', '--some-other-option'],
    lang="en-US"   # this could set iso-language-code in navigator, not recommended to change
)
tab = await browser.get('https://somewebsite.com')
```

Custom options using the Config object
---------------------------

I’ll leave out the async boilerplate here

```python 
from mithwire import *

config = Config()
config.headless = False
config.user_data_dir="/path/to/existing/profile",  # by specifying it, it won't be automatically cleaned up when finished
config.browser_executable_path="/path/to/some/other/browser",
config.browser_args=['--some-browser-arg=true', '--some-other-option'],
config.lang="en-US"   # this could set iso-language-code in navigator, not recommended to change
)
```

some impression
----
```python 
import mithwire

async def main():

    browser = await mithwire.start()
    page = await browser.get('https://www.nowsecure.nl')

    await page.save_screenshot()
    await page.get_content()
    await page.scroll_down(150)
    elems = await page.select_all('*[src]')

    for elem in elems:
        await elem.flash()

    page2 = await browser.get('https://twitter.com', new_tab=True)
    page3 = await browser.get('https://github.com/ultrafunkamsterdam/nodriver', new_window=True)

    for p in (page, page2, page3):
       await p.bring_to_front()
       await p.scroll_down(200)
       await p   # wait for events to be processed
       await p.reload()
       if p != page3:
           await p.close()

if __name__ == '__main__':

    # since asyncio.run never worked (for me)
    uc.loop().run_until_complete(main())
```



complete code example
-----------------------------
automating Twitter/X account creation

```python

A more concrete example, which can be found in the ./example/ folder,
shows a script to create a twitter account

```python
import random
import string
import logging

logging.basicConfig(level=30)

import mithwire as uc

months = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


async def main():
    driver = await uc.start()

    tab = await driver.get("https://twitter.com")

    # wait for text to appear instead of a static number of seconds to wait
    # this does not always work as expected, due to speed.
    print('finding the "create account" button')
    create_account = await tab.find("create account", best_match=True)

    print('"create account" => click')
    await create_account.click()

    print("finding the email input field")
    email = await tab.select("input[type=email]")

    # sometimes, email field is not shown, because phone is being asked instead
    # when this occurs, find the small text which says "use email instead"
    if not email:
        use_mail_instead = await tab.find("use email instead")
        # and click it
        await use_mail_instead.click()

        # now find the email field again
        email = await tab.select("input[type=email]")

    randstr = lambda k: "".join(random.choices(string.ascii_letters, k=k))

    # send keys to email field
    print('filling in the "email" input field')
    await email.send_keys("".join([randstr(8), "@", randstr(8), ".com"]))

    # find the name input field
    print("finding the name input field")
    name = await tab.select("input[type=text]")

    # again, send random text
    print('filling in the "name" input field')
    await name.send_keys(randstr(8))

    # since there are 3 select fields on the tab, we can use unpacking
    # to assign each field
    print('finding the "month" , "day" and "year" fields in 1 go')
    sel_month, sel_day, sel_year = await tab.select_all("select")

    # await sel_month.focus()
    print('filling in the "month" input field')
    await sel_month.send_keys(months[random.randint(0, 11)].title())

    # await sel_day.focus()
    # i don't want to bother with month-lengths and leap years
    print('filling in the "day" input field')
    await sel_day.send_keys(str(random.randint(0, 28)))

    # await sel_year.focus()
    # i don't want to bother with age restrictions
    print('filling in the "year" input field')
    await sel_year.send_keys(str(random.randint(1980, 2005)))

    await tab

    # let's handle the cookie nag as well
    cookie_bar_accept = await tab.find("accept all", best_match=True)
    if cookie_bar_accept:
        await cookie_bar_accept.click()

    await tab.sleep(1)

    next_btn = await tab.find(text="next", best_match=True)
    # for btn in reversed(next_btns):
    await next_btn.mouse_click()

    print("sleeping 2 seconds")
    await tab.sleep(2)  # visually see what part we're actually in

    print('finding "next" button')
    next_btn = await tab.find(text="next", best_match=True)
    print('clicking "next" button')
    await next_btn.mouse_click()

    # just wait for some button, before we continue
    await tab.select("[role=button]")

    print('finding "sign up"  button')
    sign_up_btn = await tab.find("Sign up", best_match=True)
    # we need the second one
    print('clicking "sign up"  button')
    await sign_up_btn.click()

    print('the rest of the "implementation" is out of scope')
    # further implementation outside of scope
    await tab.sleep(10)
    await driver.close()

    # verification code per mail


if __name__ == "__main__":
    # since asyncio.run never worked (for me)
    # i use
    uc.loop().run_until_complete(main())

```

### more examples in the ./example/ folder