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


def is_master_plugin(p):
    """Plugin p loads with the master plugins: its master flag when the header was read, else a .esm or .esl name
    (plugin_header.is_master's rule)."""
    return p.master if p.master is not None else p.name.lower().endswith((".esm", ".esl"))


def load_rules(plugins):
    """What the game's load order depends on besides the list, read once from plugins (effective_order):
    {lower name: (rank, needed_by)}. rank 0 is a master plugin, 1 a plain one, 2 a BlueprintShips file. needed_by
    lists (lower names) the master plugins that have a plain plugin as a master: the game loads that plain plugin with
    the master plugins, just before the first one in the list that needs it (libloadorder's hoist_masters, which LOOT
    and Vortex use for Starfield)."""
    rules = {p.name.lower(): (2 if is_blueprint(p.name) else 0 if is_master_plugin(p) else 1, []) for p in plugins}
    for p in plugins:
        if rules[p.name.lower()][0] == 0:
            for m in p.masters or []:
                r = rules.get(m.lower())
                if r is not None and r[0] == 1 and p.name.lower() not in r[1]:
                    r[1].append(p.name.lower())
    return rules


def master_block(rules):
    """The lower names that load with the master plugins (load_rules): the master plugins and the plain plugins
    a master plugin needs."""
    return {n for n, (rank, by) in rules.items() if rank == 0 or (rank == 1 and by)}


def load_keys(names, rules):
    """{lower name: key} for names in list order: sorting by key gives the order the game loads them (load_rules).
    A name load_rules doesn't know is ranked by its extension."""
    pos = {n.lower(): i for i, n in enumerate(names)}
    keys = {}
    for n, i in pos.items():
        rank, by = rules.get(n) or (2 if is_blueprint(n) else 0 if n.endswith((".esm", ".esl")) else 1, [])
        deps = [pos[d] for d in by if d in pos]
        keys[n] = (0, min(deps), 0, i) if deps else (rank, i, 1, 0)
    return keys


def game_order(plugins):
    """plugins (effective_order) as the game really loads them: the master plugins first, then the others, each part
    in list order; a plain plugin that a master plugin needs loads just before the first master plugin that needs
    it; the BlueprintShips files stay at the end (the game appends them after every plugin)."""
    keys = load_keys([p.name for p in plugins], load_rules(plugins))
    return sorted(plugins, key=lambda p: keys[p.name.lower()])


def _add(ctx, level, text, fix=None, plugins=(), **data):
    ctx.report.add(Finding(level, SECTION, text, fix, list(plugins), data))


def run(ctx):
    lo = ctx.load_order
    if any(p.path and p.kind is None for p in lo.plugins):
        plugin_header.fill(lo)
    order = effective_order(lo)
    # "loads before its master" is judged where the game loads each plugin: master plugins before the others, and
    # a plain plugin a master plugin needs just before that master plugin
    game = game_order(order)
    gpos = {p.name.lower(): i for i, p in enumerate(game)}
    for n, (rank, by) in load_rules(order).items():
        if rank == 1 and by:
            log.info(SECTION, f"{n} isn't a master plugin, but the master plugin {by[0]} needs it, so the game loads "
                              f"it with the master plugins, just before {by[0]}")
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
                if gpos[ml] > gpos[p.name.lower()]:
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
