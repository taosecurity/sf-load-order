"""sf-load-order: a Starfield load order advisor.

Reads a load order (the game's Plugins.txt, an MO2 profile, or a load order file exported from Vortex's Load Order
page), reports what is wrong with it, rates it, suggests a better order and writes files the player can apply.
It only reads the game, Vortex and MO2; it writes only to its own output folder.
"""
__version__ = "0.3.2"
AUTHOR = "Richard Bejtlich"
LICENSE = "GPL-3.0"
URL = "https://github.com/taosecurity/sf-load-order"
BYLINE = f"sf-load-order {__version__} by {AUTHOR} - {LICENSE} - {URL}"
CREDITS = ("Reads LOOT's Starfield masterlist (CC0 1.0) from your own LOOT install when it's there.\n"
           "Record conflicts come from sf-mod-check (https://github.com/taosecurity/sf-mod-check), when installed.\n"
           "This is free software: you may share and change it under the GPL-3.0. It comes with no warranty.")
