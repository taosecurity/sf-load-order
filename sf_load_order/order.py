"""Order check (section "order"): masters, unreadable plugins, slot counts and BlueprintShips placement.

What it checks, for the plugins that load (enabled, and not a name the game drops):
  - each mod plugin's masters: missing, disabled, dropped by the game, or loading after the plugin;
  - plugins whose header can't be read (not a plugin file);
  - slot counts per kind, base game included: full up to 253, medium up to 256, light up to 4096. A CAUTION comes
    within 5% of a limit (full 241+, medium 244+, light 3892+), a PROBLEM past it;
  - BlueprintShips-*.esm: the game appends these after every other plugin, oldest file first, whatever the list says
    (inputs.build_order places them so). A mod mastering one isn't told to move: no order fixes that.
Official masters (base game, DLC and Bethesda's free Creations) load in the game's own order and aren't checked.
A missing enabled plugin file is reported by the inputs section; here it only matters as somebody's master.

ctx.results["order"] gets the effective order (what the game loads: lo.plugins without the disabled and dropped ones) and each plugin's kind.
"""
import math

from . import log, plugin_header
from .model import Finding

SECTION = "order"
LIMITS = {"F": 253, "M": 256, "L": 4096}
KIND = {"F": "full", "M": "medium", "L": "light", "?": "unreadable"}
NEAR = 0.95  # caution from 95% of a limit


def is_blueprint(name):
    return name.lower().startswith("blueprintships-")


def near_limit(kind):
    return math.ceil(LIMITS[kind] * NEAR)


def effective_order(load_order):
    """[Plugin] in the order the game loads them: the plugins that load, in lo.plugins' order. inputs.build_order
    already puts the official BlueprintShips-*.esm after all others, oldest file first (the one place that rule
    lives), and any other BlueprintShips- name is dropped by the game, so it never loads."""
    return [p for p in load_order.plugins if p.enabled and not p.dropped]


def _add(ctx, level, text, fix=None, plugins=(), **data):
    ctx.report.add(Finding(level, SECTION, text, fix, list(plugins), data))


def run(ctx):
    lo = ctx.load_order
    if any(p.path and p.kind is None for p in lo.plugins):
        plugin_header.fill(lo)
    order = effective_order(lo)
    pos = {p.name.lower(): i for i, p in enumerate(order)}
    by_name = {p.name.lower(): p for p in lo.plugins}
    counts = {"F": 0, "M": 0, "L": 0, "?": 0}

    for i, p in enumerate(order):
        if p.kind in counts:
            counts[p.kind] += 1
        if p.kind == "?":
            _add(ctx, "caution", f"{p.name} couldn't be read: it isn't a plugin file (no plugin header).",
                 f"Reinstall the mod that provides {p.name}, or disable it.", [p.name])
            continue
        if p.official or p.masters is None:
            continue  # official: the game's own order; masters None: file missing (the inputs section reports it)
        for m in p.masters:
            ml = m.lower()
            if ml in pos:
                q = order[pos[ml]]
                if pos[ml] > i:
                    if is_blueprint(m):
                        log.info(SECTION, f"{p.name} masters {m}, which the game always loads last; no order fixes it")
                        continue
                    _add(ctx, "problem", f"{p.name} loads before its master {q.name}.",
                         f"Move {p.name} below {q.name} in the load order.", [p.name, q.name], master=q.name)
                elif q.path is None:
                    _add(ctx, "problem", f"{p.name} needs {q.name}, which is enabled but its file isn't in the Data "
                                         "folder.", f"Reinstall the mod that provides {q.name}, or disable {p.name}.",
                         [p.name, q.name], master=q.name)
                continue
            q = by_name.get(ml)
            if q is not None and q.dropped:
                _add(ctx, "problem", f"{p.name} needs {q.name}, but the game removes that name from Plugins.txt, so "
                                     "it never loads.",
                     f"Ask the author of {q.name} for a release under another name, or disable {p.name}.",
                     [p.name, q.name], master=q.name)
            elif q is not None and q.enabled is False:
                _add(ctx, "problem", f"{p.name} needs {q.name}, which is disabled.",
                     f"Enable {q.name}, or disable {p.name}.", [p.name, q.name], master=q.name)
            else:
                _add(ctx, "problem", f"{p.name} needs {m}, which isn't installed or isn't enabled.",
                     f"Install and enable {m}, or disable {p.name}.", [p.name, m], master=m)

    for k in "FML":
        if counts[k] > LIMITS[k]:
            _add(ctx, "problem", f"{counts[k]} {KIND[k]} plugins load, over the game's limit of {LIMITS[k]} "
                                 "(base game included).",
                 f"Disable {counts[k] - LIMITS[k]} or more {KIND[k]} plugins.", [], kind=k, count=counts[k],
                 limit=LIMITS[k])
        elif counts[k] >= near_limit(k):
            _add(ctx, "caution", f"{counts[k]} {KIND[k]} plugins load, close to the game's limit of {LIMITS[k]} "
                                 "(base game included).",
                 f"Leave room before adding more {KIND[k]} plugins.", [], kind=k, count=counts[k], limit=LIMITS[k])
    log.info(SECTION, f"{len(order)} plugins load: {counts}")
    ctx.results[SECTION] = {"effective": [p.name for p in order], "kinds": {p.name: p.kind for p in lo.plugins}}
    return {"status": "done", "counts": counts, "limits": dict(LIMITS)}
