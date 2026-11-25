"""The hard ordering graph from every rule source (DESIGN-0.3.0.md 1.3, 1.4, 1.6 and 1.8).

build() puts the movable plugins of the current order into a graph.Graph and adds each source's edges in rank order
(highest first), so an edge from a lower source that contradicts the ones already accepted is rejected with the path
that blocks it:

  rank 1  the game: masters (forced) and the plugin type rule (master plugins before the others)
  rank 2  a mod's own plugins keep their current order
  rank 3  --picks ("X right after Y" gives the edge Y before X)
  rank 4  the user rules file (after, before, groups)
  rank 5  patch targets (Sources.patches, filled by `sort`)
  rank 6  the community rules file
  rank 7  LOOT's userlist (after; group with --loot-groups)
  rank 8  LOOT's masterlist (after; group and groups only with --loot-groups)
  rank 9  every movable plugin with no group is in "default", when any source uses groups (as in LOOT)

Official plugins (top, in the game's order) and BlueprintShips files (at the end) are fixed: they aren't graph nodes,
so an edge naming one is decided here (already true, or a rule that can't be met). A rule naming a plugin that isn't
enabled is inactive. rule_checks() is 1.8 (requires and incompatible from the rules files). explain() answers "why
is X there?". Nothing here adds findings or writes anything.
"""
import os
from dataclasses import dataclass, field

from . import log, recommend
from .graph import MASTERS_END, Edge, Graph, describe, group_end, group_start, is_virtual

SECTION = "sort"
DEFAULT_GROUP = "default"
REPORT_SOURCE = {"masters": "game", "type": "game", "mod": "mod", "picks": "picks", "user": "user",
                 "patch": "patch", "community": "community", "loot_userlist": "loot_userlist",
                 "loot_masterlist": "loot_masterlist", "loot_groups": "loot_groups", "groups": "default_group"}
SOURCE_KEYS = ("game", "mod", "picks", "user", "patch", "community", "loot_userlist", "loot_masterlist",
               "loot_groups", "vortex")
ENTRY_SOURCES = {"picks", "user", "community", "loot_userlist", "loot_masterlist", "loot_groups"}  # counted by entry
OWN = {"user", "picks"}  # the player's own: a rule of theirs that isn't used is a caution; others are notes


@dataclass
class Sources:
    picks: list = field(default_factory=list)       # clashes.read_picks() entries
    user: object = None                             # rulesfile.RulesFile
    community: object = None                        # rulesfile.RulesFile
    loot: dict = None                               # loot_rules.read()
    patches: list = field(default_factory=list)     # [{"patch", "target", "file", "entry", "text"}]


@dataclass
class Built:
    graph: Graph
    current: list                                   # the whole current enabled order
    movable: list                                   # its movable plugins, in current order
    fixed: set                                      # lower-case names of the fixed plugins
    order: list = None                              # movable plugins sorted (None: a master loop)
    full: list = None                               # the whole sorted order (fixed plugins in their slots)
    rejected: list = field(default_factory=list)    # [{"edge": Edge, "path": [Edge], "why": text, "level"}]
    met_fixed: int = 0                              # edges naming a fixed plugin that are already true
    group_of: dict = field(default_factory=dict)    # plugin (lower) -> (group, Edge) it was placed in
    counts: dict = field(default_factory=dict)      # report source key -> accepted edges

    @property
    def inactive(self):
        return self.graph.inactive


def _who(source, rf, r):
    """How a rules-file entry is named: "your rule (file.json, entry 4)" or "community rule (Name, entry 4)"."""
    if source == "user":
        return f"your rule ({os.path.basename(rf.path)}, {rf.label(r)})"
    name = rf.about.get("name") or os.path.basename(rf.path)
    return f"community rule ({name}, {rf.label(r)})"


def _group_who(source, rf, g):
    if source == "user":
        return f"your rules file ({os.path.basename(rf.path)}, group entry {g['entry']})"
    return f"the community rules file ({rf.about.get('name') or os.path.basename(rf.path)}, group entry {g['entry']})"


class _Builder:
    def __init__(self, lo, current, tie_break):
        self.lo = lo
        fixed = {n.lower() for n in current if recommend._fixed(lo, n)}
        movable = [n for n in current if n.lower() not in fixed]
        self.pos = {n.lower(): i for i, n in enumerate(current)}
        self.end = {n for n in fixed if n.startswith("blueprintships-")}  # inputs.build_order puts these last
        self.g = Graph(movable, tie_break)
        self.b = Built(self.g, list(current), movable, fixed)
        self.fixed_rejected = []

    # where a node sits when it isn't in the graph: 0 official (top), 2 BlueprintShips (end); movable/virtual 1
    def _place(self, nid):
        if nid in self.b.fixed:
            return (2 if nid in self.end else 0, self.pos[nid])
        return (1, 0)

    def add(self, e):
        a, b = e.before.lower(), e.after.lower()
        ina, inb = self.g.has(a), self.g.has(b)
        if ina and inb:
            return self.g.add(e)
        if (not ina and a not in self.b.fixed) or (not inb and b not in self.b.fixed):
            self.g.inactive.append(e)
            return "inactive"
        if a == b:
            return "exists"
        pa, pb = self._place(a), self._place(b)
        if pa < pb:
            self.b.met_fixed += 1
            return "exists"
        name_a, name_b = e.before, e.after
        if b in self.b.fixed and b not in self.end:
            why = f"{name_b} is an official plugin, which the game loads first, before every other plugin"
        elif a in self.end:
            why = f"{name_a} is a BlueprintShips file, which the game loads last, after every other plugin"
        else:
            why = f"the game places both {name_a} and {name_b} itself, {name_b} first"
        self.fixed_rejected.append({"edge": e, "path": [], "why": why})
        return "rejected"

    def member(self, plugin, group, rank, source, text, file=None, entry=None):
        """plugin in group: both membership edges or neither. A plugin already in a group (from a higher source)
        keeps it."""
        p = plugin.lower()
        e1 = Edge(group_start(group), plugin, source, rank, text, file, entry, {"group": group})
        if p in self.b.fixed:  # an official or BlueprintShips plugin: the game places it, so its group is moot
            return "exists"
        if not self.g.has(p):
            self.g.inactive.append(e1)
            return "inactive"
        if p in self.b.group_of:
            return "exists"
        self.g.add_group(group, rank, source, file)
        st = self.g.add_all([e1, Edge(plugin, group_end(group), source, rank, text, file, entry, {"group": group})])
        if st == "added":
            self.b.group_of[p] = (group, e1)
        return st

    def group_after(self, group, after, rank, source, text, file=None, entry=None):
        return self.g.add_group_after(group, after, rank, source, text, file, entry)

    # ---------- the sources ----------
    def game(self):
        lo, movable_low = self.lo, {n.lower() for n in self.b.movable}
        masters_of = {p.name: p.masters for p in lo.plugins if p.name.lower() in movable_low and p.masters}
        self.g.add_masters(masters_of)
        is_master = {}
        for p in lo.plugins:
            if p.name.lower() in movable_low:
                is_master[p.name] = p.master if p.master is not None else p.name.lower().endswith((".esm", ".esl"))
        self.g.add_type_rule(is_master)

    def mods(self):
        low = {n.lower(): n for n in self.b.movable}
        for m in self.lo.mods or []:
            mine = sorted({x.lower() for x in (m.plugins or []) if x.lower() in low}, key=self.pos.get)
            for a, b in zip(mine, mine[1:]):
                self.add(Edge(low[a], low[b], "mod", 2, f"{low[a]} and {low[b]} are both in the mod {m.name}, which "
                              "keeps its plugins in this order"))

    def picks(self, picks):
        for p in picks:
            self.add(Edge(p["after"], p["plugin"], "picks", 3, f"your pick (entry {p['entry']}) says {p['plugin']} "
                          f"loads right after {p['after']}", p.get("file"), p["entry"]))

    def rules(self, rf, source, rank):
        for g in rf.groups:
            for a in g["after"]:
                self.group_after(g["name"], a, rank, source, f"{_group_who(source, rf, g)} says the group "
                                 f"{g['name']} loads after the group {a}", rf.path, g["entry"])
        for r in rf.rules:
            who = _who(source, rf, r)
            for a in r["after"]:
                self.add(Edge(a, r["plugin"], source, rank, f"{who} says {r['plugin']} loads after {a}", rf.path,
                              r["entry"], {"id": r.get("id")}))
            for b in r["before"]:
                self.add(Edge(r["plugin"], b, source, rank, f"{who} says {r['plugin']} loads before {b}", rf.path,
                              r["entry"], {"id": r.get("id")}))
            if r.get("group"):
                self.member(r["plugin"], r["group"], rank, source, f"{who} puts {r['plugin']} in the group "
                            f"{r['group']}", rf.path, r["entry"])

    def patches(self, patches):
        for p in patches:
            self.add(Edge(p["target"], p["patch"], "patch", 5, p.get("text") or f"the patch {p['patch']} changes "
                          f"records of {p['target']}, so it loads after it", p.get("file"), p.get("entry")))

    def loot(self, loot, rank):
        what = "userlist" if rank == 7 else "masterlist"
        source = "loot_userlist" if rank == 7 else "loot_masterlist"
        for a in loot["after"]:
            if a["rank"] == rank:
                self.add(Edge(a["after"], a["plugin"], source, rank, f"LOOT's {what} says {a['plugin']} loads after "
                              f"{a['after']}", a["file"], a["entry"]))
        for g in loot["groups"]:
            if g["rank"] == rank:
                for x in g["after"]:
                    self.group_after(g["name"], x, rank, "loot_groups", f"LOOT's {what} says the group {g['name']} "
                                     f"loads after the group {x}", g["file"], g["entry"])
        for m in loot["members"]:
            if m["rank"] == rank:
                self.member(m["plugin"], m["group"], rank, "loot_groups", f"LOOT's {what} puts {m['plugin']} in the "
                            f"group {m['group']}", m["file"], m["entry"])

    def default_group(self):
        uses = any(is_virtual(n) and n.startswith("<group:") for n in self.g.names)
        if not uses:
            return
        for n in self.b.movable:
            if n.lower() not in self.b.group_of:
                self.member(n, DEFAULT_GROUP, 9, "groups", f"{n} has no group, so it is in the group {DEFAULT_GROUP}")


def build(lo, current, sources=None, tie_break="current"):
    """The hard graph and the sorted order (Built). current: the whole current enabled order."""
    s = sources or Sources()
    bl = _Builder(lo, current, tie_break)
    bl.game()
    bl.mods()
    bl.picks(s.picks or [])
    if s.user is not None:
        bl.rules(s.user, "user", 4)
    bl.patches(s.patches or [])
    if s.community is not None:
        bl.rules(s.community, "community", 6)
    if s.loot:
        bl.loot(s.loot, 7)
        bl.loot(s.loot, 8)
    bl.default_group()
    b, g = bl.b, bl.g
    b.order = g.order()
    if b.order is not None:
        b.full = recommend._full(current, b.order, b.fixed)
    rej = [{"edge": r["edge"], "path": r["path"], "why": describe(r["path"])} for r in g.rejected] + bl.fixed_rejected
    # the implied default group can't put a plugin on the wrong side of the master plugins (LOOT sorts masters and
    # the others apart too): those are dropped with one log line, not one note per plugin
    keep, cross = [], []
    for r in rej:
        (cross if r["edge"].source == "groups" and any(x.source == "type" for x in r["path"]) else keep).append(r)
    if cross:
        log.info(SECTION, f"{len(cross)} default-group placement(s) skipped: they cross the master plugins")
    rej = keep
    for r in rej:
        e = r["edge"]
        r["level"] = "problem" if e.source == "type" else "caution" if e.source in OWN else "note"
    b.rejected = sorted(rej, key=lambda r: (r["edge"].rank, r["edge"].file or "", str(r["edge"].entry)))
    # rules, not edges: the edges of one written entry (each `after` name, a group's two membership edges) count
    # once; the game's rules, a mod's order and the patch targets count one per pair
    rules = set()
    for e in g.edges.values():
        if e.source == "groups" or (e.before.endswith(":start>") and e.after.endswith(":end>")):
            continue  # the default group (implied, not a rule anyone wrote) and a group's own start-before-end edge
        src = REPORT_SOURCE.get(e.source, e.source)
        if e.source in ENTRY_SOURCES and e.entry is not None:
            group_edge = e.before.startswith("<group:") and e.after.startswith("<group:")  # own entry numbers
            rules.add((src, e.file, str(e.entry), group_edge))
        else:
            rules.add((src, e.before.lower(), e.after.lower(), None))
    b.counts = dict.fromkeys(SOURCE_KEYS, 0)
    for r in rules:
        b.counts[r[0]] = b.counts.get(r[0], 0) + 1
    log.info(SECTION, f"graph: {len(b.movable)} movable, {len(g.edges)} edges, {len(b.rejected)} rejected, "
                      f"{len(g.inactive)} inactive, {len(g.loops)} master loop(s)")
    return b


def rejected_text(r):
    """A rule that wasn't used, in plain words (DESIGN 1.6)."""
    e = r["edge"]
    t = e.text[0].upper() + e.text[1:]
    if e.data.get("group"):
        return f"{t}, but {r['why']}. It was taken out of the group {e.data['group']}."
    return f"{t}, but {r['why']}. The rule wasn't used."


def loop_text(loop):
    return "The plugins' own masters form a loop, so no load order can meet them: " + describe(loop) + "."


# ---------- 1.8: requires and incompatible ----------
def rule_checks(lo, files):
    """files: [(source, RulesFile)]. Returns (requires_missing, incompatible): [{plugin, needs|with, source, file,
    entry, id}] for rules whose plugin is enabled."""
    on = {n.lower() for n in lo.enabled()}
    missing, inc, seen = [], [], set()
    for source, rf in files:
        if rf is None:
            continue
        for r in rf.rules:
            if r["plugin"].lower() not in on:
                continue
            base = {"plugin": r["plugin"], "source": source, "file": rf.path, "entry": r["entry"], "id": r.get("id")}
            for x in r["requires"]:
                if x.lower() not in on:
                    missing.append(dict(base, needs=x))
            for x in r["incompatible"]:
                pair = frozenset((r["plugin"].lower(), x.lower()))
                if x.lower() in on and pair not in seen:
                    seen.add(pair)
                    inc.append(dict(base, **{"with": x}))
    return missing, inc


# ---------- explain ----------
def explain(b, plugin, full=None, order=None):
    """Why plugin is where it is: {"plugin", "found", "fixed", "position", "was", "moved", "after": [Edge texts],
    "before": [Edge texts], "anchor": the nearest plugin above it that didn't move, "rejected": [texts],
    "inactive": [texts]}. full, order: the order sort writes (whole, movable part); default the graph's own."""
    if full is None:
        full, order = b.full, b.order
    p = plugin.lower()
    cur_low = [n.lower() for n in b.current]
    out = {"plugin": plugin, "found": p in cur_low, "fixed": p in b.fixed, "position": None, "was": None,
           "moved": False, "after": [], "before": [], "anchor": None, "rejected": [], "inactive": []}
    if not out["found"]:
        return out
    real = b.current[cur_low.index(p)]
    out["plugin"] = real
    out["was"] = cur_low.index(p) + 1
    if full is not None:
        full_low = [n.lower() for n in full]
        out["position"] = full_low.index(p) + 1
        mv = {n.lower() for n in recommend.moved(order, b.movable)} if order is not None else set()
        out["moved"] = p in mv
        for n in reversed(full[:full_low.index(p)]):
            if n.lower() not in mv:
                out["anchor"] = n
                break
    g = b.graph
    if g.has(p):
        for a in sorted(g.pred[p], key=lambda x: (g.edges[(x, p)].rank, x)):
            out["after"].append(g.edges[(a, p)].text)
        for c in sorted(g.succ[p], key=lambda x: (g.edges[(p, x)].rank, x)):
            out["before"].append(g.edges[(p, c)].text)
    for r in b.rejected:
        if p in (r["edge"].before.lower(), r["edge"].after.lower()):
            out["rejected"].append(rejected_text(r))
    for e in b.inactive:
        if p in (e.before.lower(), e.after.lower()):
            out["inactive"].append(e.text)
    return out
