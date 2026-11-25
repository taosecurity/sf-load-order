"""LOOT's ordering rules for `sort` and `rules` (DESIGN-0.3.0.md 1.4 ranks 7 and 8, and 2.6).

Reads the player's own LOOT masterlist.yaml and userlist.yaml (the files the clashes section reads; this tool never
ships or downloads either) for the three things that order plugins:

  - "after" on a plugin entry: the entry's plugin loads after each named plugin (an item is a name, or
    {name, display, condition}; an item whose condition is false or can't be evaluated is left out);
  - "group" on a plugin entry: the plugin is in that group;
  - the top-level "groups" list: each group {name, after: [group names]} loads after the groups it names.

Groups are read only when asked (--loot-groups; the user's choice, 2026-11-24). An entry name that is a regular
expression goes through the regex guard (clashes.safe_regex) and is matched against the load order's plugin names;
a refused pattern is skipped and counted, never guessed. LOOT's own req, inc, msg and dirty stay in the clashes
section. Nothing here adds findings or writes anything: callers decide how to report what it returns.
"""
import os

from . import log
from .clashes import (Env, _file_item, _is_regex, evaluate, load_loot_file, loot_entries, loot_paths,
                      regex_refusal, rx_match, safe_regex)

SECTION = "rules"
SOURCES = (("loot_userlist", "userlist", 7), ("loot_masterlist", "masterlist", 8))


def _empty():
    return {"files": {}, "after": [], "members": [], "groups": [], "unsafe": 0, "over_limit": 0, "errors": [],
            "conditions_unknown": 0}


def read(ctx, env=None, groups=False, lo=None):
    """LOOT's ordering rules: {"files": {source: path}, "after": [{plugin, after, source, rank, file, entry}],
    "members": [{plugin, group, ...}], "groups": [{name, after, ...}] (both only with groups), "unsafe": n,
    "over_limit": n (regex entries skipped past clashes.MAX_REGEX_ENTRIES),
    "errors": [text], "conditions_unknown": n}. source is "loot_userlist" (rank 7) or "loot_masterlist" (rank 8).

    lo: the load order (default ctx.load_order). With one, a regex entry name gives one rule per plugin it matches
    and conditions are evaluated; with None, entries are listed as written (for `rules list` without a source) and
    items with a condition are left out."""
    env = os.environ if env is None else env
    lo = lo if lo is not None else getattr(ctx, "load_order", None)
    out = _empty()
    if getattr(ctx.args, "no_loot", False):
        return out
    ml, ul = loot_paths(ctx, env)
    cenv = Env(lo) if lo is not None else None
    names = [p.name for p in lo.plugins] if lo is not None else []
    for source, what, rank in SOURCES:
        path = ul if what == "userlist" else ml
        if not path or not os.path.isfile(path):
            continue
        d, err = load_loot_file(path, what)
        if err:
            out["errors"].append(f"{path}: {err[0]}")
            continue
        out["files"][source] = path
        entries, unsafe, over = loot_entries(d, path)
        out["unsafe"] += unsafe
        out["over_limit"] += over
        pos = {id(e): k + 1 for k, e in enumerate(d.get("plugins") or [])}
        for match, e, _src in entries:
            k = pos[id(e)]
            # the load order's plugins the entry names (a regex may match several); none (or no load order): the
            # name as written, so `rules list` still shows the rule (inactive) and the graph leaves it out
            plugins = [n for n in names if match(n)] or [e["name"]]
            after = _after_names(e, names, lo, cenv, out)  # once per entry, so each item is counted once
            for p in plugins:
                for a in after:
                    if a.lower() != p.lower():
                        out["after"].append({"plugin": p, "after": a, "source": source, "rank": rank,
                                             "file": path, "entry": k})
                if groups and isinstance(e.get("group"), str) and e["group"].strip():
                    out["members"].append({"plugin": p, "group": e["group"], "source": source, "rank": rank,
                                           "file": path, "entry": k})
        if groups:
            glist = d.get("groups") if isinstance(d.get("groups"), list) else []
            for k, g in enumerate(glist):
                if not isinstance(g, dict) or not isinstance(g.get("name"), str):
                    continue
                items = g.get("after") if isinstance(g.get("after"), list) else []
                after = [a if isinstance(a, str) else a["name"] for a in items
                         if isinstance(a, str) or (isinstance(a, dict) and isinstance(a.get("name"), str))]
                out["groups"].append({"name": g["name"], "after": after, "source": source, "rank": rank,
                                      "file": path, "entry": k + 1})
    if cenv is not None:
        out["unsafe"] += cenv.unsafe
    log.info(SECTION, f"loot rules: {len(out['after'])} after, {len(out['members'])} group member(s), "
                      f"{len(out['groups'])} group(s) from {', '.join(out['files'].values()) or 'no LOOT file'}"
                      + (f"; {out['unsafe']} LOOT entries skipped: unsafe pattern" if out["unsafe"] else "")
                      + (f"; {out['over_limit']} LOOT regex entries skipped: over the limit" if out["over_limit"]
                         else ""))
    return out


def _after_names(e, names, lo, cenv, out):
    """The plugin names an entry's "after" items name: items whose condition is false or unknown (or, with no load
    order, any condition) are left out, and a regex item is expanded against the load order."""
    got = []
    for x in e.get("after") if isinstance(e.get("after"), list) else []:
        name, _disp, cond = _file_item(x)
        if not name:
            continue
        if cond:
            if cenv is None:
                continue
            v, _why = evaluate(cond, cenv)
            if v is None:
                out["conditions_unknown"] += 1
            if v is not True:
                continue
        got += _expand(name, names, lo, out)
    return got


def _expand(name, names, lo, out):
    """An "after" item's plugin names: the name itself, or with a regex, the load order's plugins it matches. A name
    starting with "<" isn't a plugin name and gives none."""
    if name.startswith("<"):
        log.info(SECTION, f"loot rules: after item {name[:80]!r} skipped: a name starting with '<' isn't a plugin name")
        return []
    if not _is_regex(name) or lo is None:
        return [name]
    rx, why = safe_regex(name)
    if rx is None:
        out["unsafe"] += 1 if regex_refusal(name) else 0
        log.info(SECTION, f"loot rules: after item {name[:80]!r} skipped: {why}")
        return []
    return [n for n in names if rx_match(rx, n)]
