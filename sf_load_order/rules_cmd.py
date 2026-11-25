"""The `rules` command (DESIGN-0.3.0.md 2.7): list, explain, check, accept and propose PMT ordering rules.

    sf-load-order rules list    [--rules FILE | --no-rules] [--community-rules FILE] [LOOT options] [source] [--derived]
                                [sort's options: --picks, --tie-break, --conflicts, --clashes, --patch-sources]
    sf-load-order rules explain PLUGIN [the same as list]
    sf-load-order rules check   [--rules FILE] [--community-rules FILE] [source options]
    sf-load-order rules accept  PROPOSED.json (--id ID ... | --all) [--rules FILE]
    sf-load-order rules propose [the same as explain] [--kind choice|patch|all] [--out FOLDER]

Everything prints plain text; --json FILE writes the same as JSON (for agents). `list` and `check` read a load order
only when a source option is given; `explain` and `propose` always do (the game's own Plugins.txt by default).
`accept` is the only command that writes outside --out: it writes the user rules file and its .bak, nothing else.

Exit codes: 0 done (check: nothing to fix); 1 check found something to fix; 2 couldn't run (a file can't be read,
bad options, the plugin isn't in the load order, accept refused).
"""
import argparse
import datetime
import json
import os
import sys

from . import BYLINE, __version__, log, rulesfile, sortgraph
from .model import Report

SOURCE_OPTIONS = ("plugins_txt", "mo2", "vortex_loadorder", "vortex_profile")
COMMANDS = ("list", "explain", "check", "accept", "propose")
KINDS = ("choice", "patch", "all")


class Stop(Exception):
    """Stop with exit 2 and this text (a file that can't be read, a refusal)."""


# ---------- options ----------
def parser():
    from .cli import _source_options
    p = argparse.ArgumentParser(
        prog="sf-load-order rules",
        description="List, explain, check, accept and propose PMT ordering rules (sf-load-order-rules.json). Only "
                    "`rules accept` writes a file outside --out: your rules file and its .bak.",
        allow_abbrev=False)
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    def common(sp, loot=True, source=True):
        _rules_files(sp)
        if loot:
            _loot_options(sp)
        if source:
            _source_options(sp)
        out = sp.add_argument_group("output")
        out.add_argument("--json", metavar="FILE", help="also write the result as JSON to this file")
        out.add_argument("--verbose", action="store_true", help="write the troubleshooting log to the screen (stderr)")
        out.add_argument("--log", metavar="FILE", help="write the troubleshooting log to this file")

    sp = sub.add_parser("list", help="every rule, and whether it is active in this load order (with a source)",
                        description="Print every rule from your rules file, the community file and LOOT's files. "
                                    "With a source option, each rule says whether it is active in that load order, "
                                    "and an active rule that sort leaves out (a stronger rule contradicts it) says "
                                    "why.",
                        allow_abbrev=False)
    sp.add_argument("--derived", action="store_true",
                    help="also list the game's rules read from the plugins (masters, master plugins first, a mod's "
                         "own plugin order); needs a load order")
    _sort_options(sp)
    common(sp)
    sp = sub.add_parser("explain", help="why a plugin is where it is in the sorted order",
                        description="Sort the load order quietly (nothing is written but the log) and say why PLUGIN "
                                    "is where it is: every rule into and out of it, the nearest plugin above it that "
                                    "didn't move, and any rule about it that wasn't used and why.",
                        allow_abbrev=False)
    sp.add_argument("plugin", metavar="PLUGIN", help="the plugin's file name")
    _sort_options(sp)
    common(sp)
    sp = sub.add_parser("check", help="check the rules files (and, with a source, against the load order)",
                        description="Check the rules files: schema, fields, names, limits and loops among a file's "
                                    "own rules. With a source option, also rules the game's own rules contradict, "
                                    "and stale rules (a plugin changed since the rule was pinned). Exit 0 clean, 1 "
                                    "something to fix, 2 a file couldn't be read.",
                        allow_abbrev=False)
    common(sp, loot=False)
    sp = sub.add_parser("accept", help="copy proposed rules into your rules file",
                        description="Copy proposals from a rules-proposed.json (written by `sort` or `rules "
                                    "propose`) into your rules file. Writes <rules file>.bak first, then the new "
                                    "file in one step; nothing is written when it is refused.",
                        allow_abbrev=False)
    sp.add_argument("proposed", metavar="PROPOSED.json", help="the rules-proposed.json to take proposals from")
    pick = sp.add_mutually_exclusive_group(required=True)
    pick.add_argument("--id", metavar="ID", action="append", help="a proposal's id (may be given more than once)")
    pick.add_argument("--all", action="store_true", help="every proposal in the file")
    common(sp, loot=False, source=False)
    sp = sub.add_parser("propose", help="write rules-proposed.json without the order files",
                        description="Write the proposed rules (rules-proposed.json) into --out, as `sort` does, "
                                    "without the order files.",
                        allow_abbrev=False)
    sp.add_argument("--kind", choices=KINDS, default="choice",
                    help="choice: the record-conflict choices the sort makes (default; needs --conflicts, since "
                         "propose doesn't run sf-mod-check itself); patch: the plugins each patch "
                         "in --clashes fixes (fixed_by), for sharing in a community file; all: both")
    sp.add_argument("--out", metavar="FOLDER", default="sf-load-order-report",
                    help="where rules-proposed.json goes (default: .\\sf-load-order-report)")
    _sort_options(sp)
    common(sp)
    return p


def _rules_files(sp):
    g = sp.add_argument_group("rules files")
    g.add_argument("--rules", metavar="FILE", help="your rules file (default: sf-load-order-rules.json in the state "
                                                   "folder, %%LOCALAPPDATA%%\\sf-load-order)")
    g.add_argument("--no-rules", action="store_true", help="don't read your rules file")
    g.add_argument("--community-rules", metavar="FILE", help="a community rules file (a local file; nothing is "
                                                             "downloaded)")
    g.add_argument("--state", metavar="FOLDER", help="the state folder that holds your rules file by default")


def _loot_options(sp):
    g = sp.add_argument_group("LOOT")
    g.add_argument("--no-loot", action="store_true", help="don't read LOOT's masterlist or userlist")
    g.add_argument("--loot-masterlist", metavar="FILE",
                   help="LOOT's Starfield masterlist.yaml (default: %%LOCALAPPDATA%%\\LOOT\\games\\Starfield\\"
                        "masterlist.yaml)")
    g.add_argument("--loot-userlist", metavar="FILE", help="LOOT's userlist.yaml (default: next to the masterlist)")
    g.add_argument("--loot-groups", action="store_true", help="also use LOOT's groups (off by default)")


def _sort_options(sp):
    g = sp.add_argument_group("sorting")
    g.add_argument("--picks", metavar="FILE", help="your own order picks: plugin X loads right after plugin Y")
    g.add_argument("--tie-break", choices=("current", "name"), default="current",
                   help="current: keep the current order wherever the rules allow (default); name: order by file "
                        "name, the same on any PC")
    g.add_argument("--conflicts", metavar="FILE",
                   help="a conflicts.json from sf-mod-check conflict, for the record-conflict choices the sort makes")
    g.add_argument("--clashes", metavar="FILE", help="your own known-clashes file (its fixed_by entries), as in sort")
    g.add_argument("--patch-sources", metavar="FILE|FOLDER", action="append",
                   help="a patch-sources.json (or a folder of them) besides those found next to the enabled plugins, "
                        "as in sort (may be given more than once)")


# ---------- reading ----------
def _source_given(args):
    return any(getattr(args, k, None) for k in SOURCE_OPTIONS)


def user_path(args, env=None):
    """(path, given): the user rules file, and whether --rules named it."""
    if getattr(args, "rules", None):
        return args.rules, True
    return rulesfile.default_path(getattr(args, "state", None), env), False


def read_files(args, env=None):
    """{"user": RulesFile|None, "user_path", "community": RulesFile|None}. A missing default user file is no file;
    any other file that can't be read raises Stop."""
    out = {"user": None, "user_path": None, "community": None}
    if not getattr(args, "no_rules", False):
        path, given = user_path(args, env)
        out["user_path"] = path
        if given or os.path.isfile(path):
            try:
                out["user"] = rulesfile.read(path, "user")
            except rulesfile.RulesError as e:
                raise Stop(str(e))
        else:
            log.info("rules", f"no user rules file at {path} (fine: no rules of your own)")
    if getattr(args, "community_rules", None):
        try:
            out["community"] = rulesfile.read(args.community_rules, "community")
        except rulesfile.RulesError as e:
            raise Stop(str(e))
    return out


def _context(args, lo):
    from .cli import Context
    if not hasattr(args, "out"):
        args.out = "."
    for k in ("clashes", "accept", "picks"):
        if not hasattr(args, k):
            setattr(args, k, None)
    return Context(args, lo, Report(tool_version=__version__))


def read_lo(args):
    from .cli import read_load_order
    lo, err = read_load_order(args)
    if lo is None:
        raise Stop(f"can't read the load order: {err}")
    vp = getattr(lo, "_vortex_profile", None)
    if vp and vp.get("deployed") is False:  # Data's plugins are another profile's: no masters to read (as sort)
        raise Stop(f"Data holds another profile, not {vp['name']} (deploy {vp['name']} in Vortex and run again)")
    return lo


def current(lo):
    """The order the game loads (enabled, not dropped), the order sort starts from."""
    from .order import effective_order
    return [p.name for p in effective_order(lo)]


def sources(args, lo, files, ctx=None, env=None):
    """sortgraph.Sources for this run, and the context (picks read into it)."""
    from . import clashes, loot_rules
    ctx = ctx or _context(args, lo)
    s = sortgraph.Sources(user=files["user"], community=files["community"])
    if getattr(args, "picks", None):
        s.picks, st = clashes.read_picks(ctx, args.picks)
        if st.get("status") == "failed":
            raise Stop(st["why"])
    s.loot = loot_rules.read(ctx, env, groups=getattr(args, "loot_groups", False), lo=lo)
    ctx.results["picks"] = s.picks
    if lo is not None:  # the patch edges, as sort reads them (--clashes fixed_by and each patch-sources.json)
        from . import sort
        if getattr(args, "clashes", None) and "clashes" not in ctx.results:
            known, st = clashes.read_clashes(ctx, args.clashes, [], {})
            if st.get("status") == "failed":
                raise Stop(st["why"])
            ctx.results["clashes"] = {"known": known}
        s.patches = sort.patch_targets(ctx, lo)
    return s, ctx


def build(args, lo, files, env=None):
    s, ctx = sources(args, lo, files, env=env)
    eff = (ctx.results.get("order") or {}).get("effective")
    return sortgraph.build(lo, list(eff) if eff else current(lo), s, getattr(args, "tie_break", "current")), s, ctx


# ---------- list ----------
def _active(r, on):
    if r["plugin"].lower() not in on:
        return False
    order = r["after"] + r["before"]
    return not order or any(x.lower() in on for x in order)


def _left_out(args, lo, files, env):
    """{key: [why, ...]} for each rule the sort leaves out in this load order (the same graph as sort's).
    Keys: (source, entry) for a rules file's rule, (source, entry, plugin, after) for a LOOT after rule."""
    b, _s, _ctx = build(args, lo, files, env)
    left = {}
    for r in b.rejected:
        e = r["edge"]
        if e.before.startswith("<group:") and e.after.startswith("<group:"):
            continue  # a group entry's edge (group after group), not a rule entry's: they share entry numbers
        if e.source in ("user", "community"):
            left.setdefault((e.source, str(e.entry)), []).append(r["why"])
        elif e.source in ("loot_userlist", "loot_masterlist"):
            left.setdefault((e.source, str(e.entry), e.after.lower(), e.before.lower()), []).append(r["why"])
    return left


def cmd_list(args, env=None):
    lo = read_lo(args) if (_source_given(args) or args.derived) else None
    on = {n.lower() for n in current(lo)} if lo else None
    files = read_files(args, env)
    left = _left_out(args, lo, files, env) if lo else {}
    lines, rows = [], []

    def head(label, rf, path, why_none):
        if rf is None:
            lines.append(f"{label}: none ({why_none})")
            return
        lines.append(f"{label}: {rf.path} (kind {rf.kind}, {len(rf.rules)} rule(s), {len(rf.groups)} group(s)"
                     + (f", {len(rf.problems)} entr{'y' if len(rf.problems) == 1 else 'ies'} skipped" if rf.problems
                        else "") + ")")
        for w in rf.warnings:
            lines.append(f"  Caution: {w}")
        for g in rf.groups:
            text = f"the group {g['name']} loads after {', '.join(g['after']) or 'no other group'}"
            lines.append(f"  group entry {g['entry']}: {text}")
            rows.append({"source": label_key[label], "file": rf.path, "entry": g["entry"], "group": g["name"],
                         "text": text})
        for r in rf.rules:
            act = None if on is None else _active(r, on)
            why = left.get((label_key[label], str(r["entry"])), []) if act else []
            lines.append(f"  {rf.label(r)}: {rulesfile.describe(r)}" + _flag(act, why))
            rows.append({"source": label_key[label], "file": rf.path, "entry": r["entry"], "id": r.get("id"),
                         "plugin": r["plugin"], "text": rulesfile.describe(r), "active": act, "not_used": why})

    label_key = {"Your rules": "user", "Community rules": "community"}
    why_user = "--no-rules" if args.no_rules else f"no file at {files['user_path']}"
    head("Your rules", files["user"], files["user_path"], why_user)
    head("Community rules", files["community"], None, "none given with --community-rules")
    if not args.no_loot:
        ctx = _context(args, lo)
        from . import loot_rules
        loot = loot_rules.read(ctx, env, groups=args.loot_groups, lo=lo)
        for source, what in (("loot_userlist", "LOOT userlist"), ("loot_masterlist", "LOOT masterlist")):
            path = loot["files"].get(source)
            if not path:
                lines.append(f"{what}: none found")
                continue
            mine = [a for a in loot["after"] if a["source"] == source]
            mem = [m for m in loot["members"] if m["source"] == source]
            grp = [g for g in loot["groups"] if g["source"] == source]
            lines.append(f"{what}: {path} ({len(mine)} after rule(s)"
                         + (f", {len(mem)} group assignment(s), {len(grp)} group(s))" if args.loot_groups
                            else "; groups are used only with --loot-groups)"))
            for g in grp:
                text = f"the group {g['name']} loads after {', '.join(g['after']) or 'no other group'}"
                lines.append(f"  entry {g['entry']} (groups): {text}")
                rows.append({"source": source, "file": path, "entry": g["entry"], "group": g["name"], "text": text})
            for a in mine:
                act = None if on is None else (a["plugin"].lower() in on and a["after"].lower() in on)
                why = left.get((source, str(a["entry"]), a["plugin"].lower(), a["after"].lower()), []) if act else []
                text = f"{a['plugin']} loads after {a['after']}"
                lines.append(f"  entry {a['entry']}: {text}" + _flag(act, why))
                rows.append({"source": source, "file": path, "entry": a["entry"], "plugin": a["plugin"],
                             "text": text, "active": act, "not_used": why})
            for m in mem:
                act = None if on is None else m["plugin"].lower() in on
                text = f"{m['plugin']} is in the group {m['group']}"
                lines.append(f"  entry {m['entry']}: {text}" + _flag(act))
                rows.append({"source": source, "file": path, "entry": m["entry"], "plugin": m["plugin"],
                             "text": text, "active": act})
        for e in loot["errors"]:
            lines.append(f"  Caution: {e}")
        if loot["unsafe"]:
            lines.append(f"  {loot['unsafe']} LOOT entries skipped: unsafe pattern")
    if args.derived:
        b = sortgraph.build(lo, current(lo), sortgraph.Sources())
        game = [e for e in b.graph.edges.values() if e.source in ("masters", "type", "mod")
                and not (e.before.startswith("<") and e.after.startswith("<"))]
        lines.append(f"The game's rules (read from the plugins): {len(game)}")
        for e in sorted(game, key=lambda e: (e.rank, e.source, e.after.lower(), e.before.lower())):
            lines.append(f"  {e.text}")
            rows.append({"source": sortgraph.REPORT_SOURCE[e.source], "text": e.text})
    if on is None and not args.derived:
        lines.append("(Give a source option, such as --plugins-txt, to see which rules are active in a load order.)")
    return 0, lines, {"command": "list", "rules": rows}


def _flag(act, why=()):
    if act and why:
        return f" [active, but the sort doesn't use it: {'; '.join(why)}]"
    return "" if act is None else " [active]" if act else " [inactive: a plugin it names isn't enabled]"


# ---------- explain ----------
def cmd_explain(args, env=None):
    lo = read_lo(args)
    files = read_files(args, env)
    b, _s, _ctx = build(args, lo, files, env)
    # the order sort writes: the graph's order with the picks' right-after placement, the record-conflict climb
    # (with --conflicts) and the Vortex tie-break, so explain's position and anchor match sort's files
    from . import sort
    rec = _records(_ctx, lo)
    sf = sort.soft(_ctx, b, rec) if b.order is not None else None
    x = sortgraph.explain(b, args.plugin, full=sf["full"] if sf else None, order=sf["order"] if sf else None)
    if not x["found"]:
        raise Stop(f"{args.plugin} isn't enabled in this load order (rules explain looks only at enabled plugins).")
    lines = []
    p = x["plugin"]
    if b.order is None:
        lines.append(sortgraph.loop_text(b.graph.loops[0]))
    if x["fixed"]:
        lines.append(f"{p} is a plugin the game places itself (an official plugin, or a BlueprintShips file), so "
                     f"the sort never moves it. It is number {x['was']} in the order.")
    elif x["position"] is not None:
        lines.append(f"{p} is number {x['position']} in the sorted order (number {x['was']} now): "
                     + ("the sort moves it." if x["moved"] else "the sort doesn't move it."))
    for key, label in (("after", "It loads after something because:"), ("before", "Something loads after it "
                                                                                    "because:")):
        if x[key]:
            lines.append(label)
            lines += [f"  - {t}" for t in x[key]]
    if not x["after"] and not x["before"] and not x["fixed"]:
        lines.append("No rule places it: it keeps its place in the current order.")
    if x["anchor"]:
        lines.append(f"The nearest plugin above it that the sort doesn't move: {x['anchor']}.")
    if x["rejected"]:
        lines.append("Rules about it that weren't used:")
        lines += [f"  - {t}" for t in x["rejected"]]
    if x["inactive"]:
        lines.append("Rules about it that don't apply here (a plugin they name isn't enabled):")
        lines += [f"  - {t}" for t in x["inactive"]]
    x["soft"] = None
    if sf and rec is not None and not x["fixed"]:
        low = x["plugin"].lower()
        f = next((f for f in sf["flips"] if f["loser"].lower() == low), None)
        w = next((f for f in sf["flips"] if f["winner"].lower() == low), None)
        if f:
            x["soft"] = sort.flip_reason(f)
            lines.append(f"A record-conflict choice moves it after {f['winner']}: {x['soft']}.")
        elif w:
            x["soft"] = f"{w['loser']} loads after it, so {w['loser']}'s changes win"
            lines.append(f"A record-conflict choice puts {w['loser']} after it, so {w['loser']}'s changes to "
                         f"{w['records']} record(s) win over it.")
        else:
            lines.append("No record-conflict choice moves it.")
    return 0, lines, dict({"command": "explain"}, **x)


# ---------- check ----------
def _loops_in(rf, source):
    """Rules of one file that can't all be met together (a loop, or an official plugin it would move): [text]."""
    return [f"{r['edge'].text[0].upper() + r['edge'].text[1:]}, but "
            + (f"{sortgraph.describe(r['path'])} (the file's own rules form a loop)." if r["path"] else f"{r['why']}.")
            for r in _own_graph(rf, source)]


def cmd_check(args, env=None):
    lines, out, code = [], {"command": "check", "files": []}, 0
    try:
        files = read_files(args, env)
    except Stop as e:
        return 2, [f"Couldn't read a rules file: {e}"], dict(out, error=str(e))
    lo = read_lo(args) if _source_given(args) else None
    checked = [(s, files[s]) for s in ("user", "community") if files[s] is not None]
    if not checked:
        lines.append("No rules file to check" + (f" (no file at {files['user_path']})." if files["user_path"]
                                                 else "."))
    for source, rf in checked:
        item = {"file": rf.path, "kind": rf.kind, "rules": len(rf.rules), "problems": [], "loops": [],
                "contradictions": [], "stale": [], "warnings": list(rf.warnings)}
        lines.append(f"{rf.path} (kind {rf.kind}): {len(rf.rules)} usable rule(s) of {rf.entries}, "
                     f"{len(rf.groups)} group(s)")
        for w in rf.warnings:
            lines.append(f"  Caution: {w}")
        for pr in rf.problems:
            t = f"{pr['what']} entry {pr['entry']}: {pr['why']}. The entry is skipped."
            item["problems"].append(t)
            lines.append(f"  Fix: {t}")
        for t in _loops_in(rf, source):
            item["loops"].append(t)
            lines.append(f"  Fix: {t}")
        if lo is not None:
            b = sortgraph.build(lo, current(lo), sortgraph.Sources(**{source: rf}))
            for r in b.rejected:
                if r["edge"].source == source and r["edge"].file == rf.path:
                    t = sortgraph.rejected_text(r)
                    item["contradictions"].append(t)
                    lines.append(f"  Fix: {t}")
            for s in rulesfile.stale(rf, lo):
                t = (f"entry {s['entry']}" + (f" (id {s['id']})" if s.get("id") else "") + f" about {s['plugin']} is "
                     f"stale: {', '.join(s['changed'])} changed since the rule was pinned. It is still used. Run "
                     "`sf-load-order sort` again, look at the new proposals, and accept or delete the old rule.")
                item["stale"].append(dict(s, text=t))
                lines.append(f"  Caution: {t}")
        if item["problems"] or item["loops"] or item["contradictions"] or item["stale"] or item["warnings"]:
            code = 1
        else:
            lines.append("  OK: nothing to fix.")
        out["files"].append(item)
    if lo is None and checked:
        lines.append("(Give a source option, such as --plugins-txt, to also check the rules against a load order.)")
    return code, lines, out


# ---------- accept ----------
def _same(a, b):
    return a["plugin"].lower() == b["plugin"].lower() and {x.lower() for x in a["after"]} == \
        {x.lower() for x in b["after"]} and {x.lower() for x in a["before"]} == {x.lower() for x in b["before"]}


def _clean(r):
    """A RulesFile rule back as a file entry (no reader bookkeeping, no empty lists)."""
    d = {k: r[k] for k in ("id", "plugin") if r.get(k)}
    for k in rulesfile.LISTS:
        if r.get(k):
            d[k] = list(r[k])
    for k in ("group", "note", "evidence"):
        if r.get(k):
            d[k] = r[k]
    return d


def cmd_accept(args, env=None):
    path, _given = user_path(args, env)
    try:
        prop = rulesfile.read(args.proposed, "user")
    except rulesfile.RulesError as e:
        raise Stop(f"can't read the proposals: {e}")
    if prop.problems:
        raise Stop(f"{args.proposed}: {len(prop.problems)} entr{'y is' if len(prop.problems) == 1 else 'ies are'} "
                   f"not valid ({prop.problems[0]['why']}); nothing was written. Fix: run `sf-load-order sort` again "
                   "for a new rules-proposed.json.")
    by_id = {(r.get("id") or "").lower(): r for r in prop.rules}
    if args.all:
        chosen = list(prop.rules)
    else:
        missing = [i for i in args.id if i.lower() not in by_id]
        if missing:
            raise Stop(f"{args.proposed} has no proposal with the id {', '.join(missing)}; nothing was written.")
        chosen = [by_id[i.lower()] for i in dict.fromkeys(args.id)]
    if os.path.isfile(path):
        try:
            raw = rulesfile.load_json(path)
            old = rulesfile.read(path, "user")
        except rulesfile.RulesError as e:
            raise Stop(f"can't read your rules file, so nothing was written: {e}")
    else:
        raw, old = None, rulesfile.RulesFile(path)
    lines, added, skipped = [], [], []
    used = {int(r["id"][2:]) for r in old.rules if (r.get("id") or "").lower().startswith("u-")
            and r["id"][2:].isdigit()}
    nxt = max(used, default=0) + 1
    for r in chosen:
        if any(_same(r, o) for o in old.rules + added):
            skipped.append(r.get("id"))
            lines.append(f"Already in your rules: {rulesfile.describe(r)} (proposal {r.get('id')}); left alone.")
            continue
        new = _clean(r)
        while nxt in used:
            nxt += 1
        new["id"] = f"u-{nxt:04d}"
        used.add(nxt)
        ev = dict(new.get("evidence") or {})
        if r.get("id"):
            ev["proposal"] = r["id"]
        new["evidence"] = ev
        added.append(dict(r, id=new["id"], _entry=new))
    if not added:
        lines.append(f"Nothing new to add; {path} was not changed.")
        return 0, lines, {"command": "accept", "file": path, "added": [], "already": skipped}
    entries = (list(raw.get("rules") or []) if isinstance(raw, dict) else []) + [a["_entry"] for a in added]
    doc = dict(raw) if isinstance(raw, dict) else rulesfile.to_json("user", [])
    doc["rules"] = entries
    doc.setdefault("schema", rulesfile.SCHEMA)
    # check the result with the same reader, and refuse a rule that contradicts the file's own rules
    trial = rulesfile.RulesFile(path, old.kind, old.about, old.groups,
                                old.rules + [dict(a, entry=f"new {a['id']}") for a in added], entries=len(entries))
    over = rulesfile._limits(doc)
    if over or len(entries) > rulesfile.MAX_RULES:
        raise Stop(f"adding them would put {path} past a limit ({over or f'over {rulesfile.MAX_RULES} rules'}); "
                   "nothing was written.")
    clash = _new_loops(trial)
    if clash:
        raise Stop("these proposals contradict rules already in your file, so nothing was written: "
                   + " ".join(clash) + " Fix: delete or change the old rule first, or accept only the other ids.")
    try:
        bak = rulesfile.write_atomic(path, doc)
    except OSError as e:
        raise Stop(f"can't write {path} ({e.strerror or e}); nothing was changed. Fix: check that you can write "
                   "to that folder.")
    for a in added:
        lines.append(f"Added {a['id']}: {rulesfile.describe(a)} (proposal {a['_entry']['evidence'].get('proposal')}).")
    lines.append(f"Wrote {path}" + (f" (the old file is in {bak})." if bak else "."))
    return 0, lines, {"command": "accept", "file": path, "backup": bak, "added": [a["id"] for a in added],
                      "already": skipped}


def _own_graph(rf, source):
    """A file's rules alone in a graph (every plugin it names enabled, no game rules): its rejected edges are the
    rules the file's earlier rules make impossible."""
    from .inputs import OFFICIAL_ORDER
    from .model import LoadOrder, Plugin

    def game_first(n):  # official plugins in the game's own order (code review round 4), the rest by name
        low = n.lower()
        return (0, OFFICIAL_ORDER.index(low), low) if low in OFFICIAL_ORDER else (1, 0, low)
    names = sorted({n for r in rf.rules for n in rulesfile.names_in(r)}, key=game_first)
    lo = LoadOrder(source="plugins.txt", plugins=[Plugin(n, True, masters=[]) for n in names])
    bl = sortgraph._Builder(lo, names, "name")
    bl.rules(rf, source, 4 if source == "user" else 6)
    return bl.g.rejected + bl.fixed_rejected


def _new_loops(rf):
    """Texts for the new rules (entry "new <id>") that the file's own rules make impossible."""
    return [f"{r['edge'].text[0].upper() + r['edge'].text[1:]}, but {sortgraph.describe(r['path']) or r['why']}."
            for r in _own_graph(rf, "user") if str(r["edge"].entry).startswith("new ")]


# ---------- propose ----------
def patch_proposals(ctx, lo):
    """Proposals from --clashes fixed_by: the patch loads after every plugin it fixes (evidence kind "patch")."""
    from . import clashes
    if not getattr(ctx.args, "clashes", None):
        return []
    known, st = clashes.read_clashes(ctx, ctx.args.clashes, [], {})
    if st.get("status") == "failed":
        raise Stop(st["why"])
    on = {n.lower() for n in current(lo)}
    out = []
    for k in known:
        fb = k.get("fixed_by")
        if not fb or fb.lower() not in on:
            continue
        after = [p for p in k["plugins"] if p.lower() != fb.lower()]
        if not after:
            continue
        out.append(proposal(lo, fb, after, "patch", f"{fb} is the patch for {', '.join(after)} (your --clashes "
                                                    f"file, entry {k['entry']}), so it loads after them."))
    return out


def proposal(lo, plugin, after, kind, text, records=None):
    """One proposed rule (rules-proposed.json entry)."""
    ev = {"kind": kind, "text": text, "files": rulesfile.file_hashes(lo, [plugin] + list(after)),
          "tool": f"sf-load-order {__version__}", "date": datetime.date.today().isoformat()}
    if records:
        ev["records"] = list(records)[:rulesfile.MAX_RECORDS]
    return {"id": rulesfile.proposal_id(plugin, after, kind), "plugin": plugin, "after": list(after),
            "evidence": ev}


def write_proposed(folder, rules):
    """rules-proposed.json in folder (written even when empty). Returns its path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "rules-proposed.json")
    seen, uniq = set(), []
    for r in rules:
        if r["id"] not in seen:
            seen.add(r["id"])
            uniq.append(r)
    rulesfile.write_atomic(path, rulesfile.to_json("user", uniq), backup=False)
    return path


def cmd_propose(args, env=None):
    lo = read_lo(args)
    files = read_files(args, env)
    b, _s, ctx = build(args, lo, files, env)
    rules = []
    if args.kind in ("patch", "all"):
        rules += patch_proposals(ctx, lo)
    # choice proposals (the record-conflict pairs the soft climb flips) come from the sort's climb
    rules += choice_proposals(ctx, lo, b) if args.kind in ("choice", "all") else []
    try:
        path = write_proposed(args.out, rules)
    except OSError as e:
        raise Stop(f"can't write rules-proposed.json in {args.out} ({e.strerror or e}). Fix: give --out a folder "
                   "you can write to.")
    lines = [f"{len(rules)} proposal(s) written to {path}."]
    if args.kind in ("choice", "all") and not getattr(args, "conflicts", None):
        lines.append("Note: record-conflict choices need record data. Give --conflicts <conflicts.json> (from "
                     "sf-mod-check conflict), or run sort, which gets it itself.")
    lines += [f"  {r['id']}: {rulesfile.describe(dict(r, before=[], requires=[], incompatible=[]))}" for r in rules]
    if rules:
        lines.append(f"To keep them: sf-load-order rules accept \"{path}\" --all (or --id <id> for one).")
    return 0, lines, {"command": "propose", "file": path, "proposals": rules}


def _records(ctx, lo):
    """ctx.results["records"] from --conflicts (a conflicts.json from sf-mod-check conflict), else None."""
    path = getattr(ctx.args, "conflicts", None)
    if not path:
        return None
    if "records" not in ctx.results:
        from . import records
        cj, err = records.load(path)
        if err:
            raise Stop(f"can't read --conflicts {path}: {err}")
        ctx.results["records"] = records.analyse(cj, lo)
    return ctx.results["records"]


def soft(ctx, lo, built):
    """sort.soft() on this graph with --conflicts (None without a valid order or record data)."""
    from . import sort
    rec = _records(ctx, lo)
    if built.order is None or rec is None:
        return None
    return sort.soft(ctx, built, rec)


def choice_proposals(ctx, lo, built):
    """The soft climb's choices as proposals (the same code as `sort`'s rules-proposed.json): each record-conflict
    pair the climb flips, loser after winner. Without --conflicts there is nothing to propose."""
    sf = soft(ctx, lo, built)
    return sf["proposals"] if sf else []


# ---------- main ----------
RUN = {"list": cmd_list, "explain": cmd_explain, "check": cmd_check, "accept": cmd_accept, "propose": cmd_propose}


def main(argv, env=None):
    p = parser()
    if not argv or argv[0] not in COMMANDS:
        if argv and argv[0] in ("-h", "--help"):
            p.print_help()
            return 0
        print("sf-load-order rules needs a command: " + ", ".join(COMMANDS) + ". Example: sf-load-order rules list. "
              "sf-load-order rules --help shows them all.")
        return 2
    args = p.parse_args(argv)
    err = log.setup(args.verbose, args.log)
    if err:
        print(f"RESULT: CANNOT CHECK: {err}")
        return 2
    try:
        log.info("start", f"{BYLINE}; rules {args.cmd}; args {vars(args)}")
        try:
            code, lines, data = RUN[args.cmd](args, env)
        except Stop as e:
            log.info("rules", f"stopped: {e}")
            print(f"RESULT: CANNOT CHECK: {e}")
            return 2
        for ln in lines:
            print(ln)
        if args.json:
            try:
                with open(args.json, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                    f.write("\n")
            except OSError as e:
                print(f"RESULT: CANNOT CHECK: can't write {args.json} ({e.strerror or e}).")
                return 2
        sys.stdout.flush()
        return code
    finally:
        log.close()
