# Copyright 2024 by UltrafunkAmsterdam (https://github.com/UltrafunkAmsterdam)
# All rights reserved.
# This file is part of the mithwire package, a fork of nodriver.
# Modified 2026 by Elendil Technologies as part of the mithwire fork.
# Released under the "GNU AFFERO GENERAL PUBLIC LICENSE" (AGPL-3.0).
# Please see the LICENSE.txt and NOTICE files included as part of this package.

from mithwire import cdp
from mithwire.core import util
from mithwire.core._contradict import ContraDict  # noqa
from mithwire.core._contradict import cdict
from mithwire.core.browser import Browser, BrowserContext
from mithwire.core.config import Config
from mithwire.core.connection import Connection, ProtocolException
from mithwire.core.element import Element
from mithwire.core.tab import Tab
from mithwire.core.util import loop, start
from mithwire.stealth import FingerprintConfig, Stealth, compute_launch_args
from mithwire.stealth_diagnostic import run_stealth_diagnostic, stealth_diagnostic

__all__ = [
    "loop",
    "Browser",
    "Tab",
    "cdp",
    "Config",
    "start",
    "util",
    "Element",
    "ContraDict",
    "ProtocolException",
    "FingerprintConfig",
    "Stealth",
    "compute_launch_args",
    "stealth_diagnostic",
    "run_stealth_diagnostic",
]

__version__ = "0.50.6"