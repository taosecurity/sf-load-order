"""The ordering graph for `sort` (DESIGN-0.3.0.md sections 1.4, 1.6 and 1.7).

Nodes are the movable plugins plus virtual nodes (the end of the master plugins, the start and end of each group).
Every edge "A loads before B" carries its provenance (Edge): its source, its rank (1 = the game, highest), the file
and entry it came from and one plain sentence. Edges are added source by source in rank order. An edge that would
close a cycle is rejected and the path that blocks it is kept as the reason, so a lower source loses to a higher
one in plain words. Rank 1 master edges are forced: a cycle among them (a master loop) means no valid order exists,
and order() returns None.

The cycle check keeps a topological order of the nodes as edges arrive (the Pearce-Kelly method): an edge that
already agrees with that order needs no search, and one that doesn't searches only the nodes between its two ends.
So a large graph (1,000 plugins, 20,000 edges) builds in well under a second.

order() is a stable topological sort: it pops the free node with the lowest key, so with the "current" tie-break
every plugin keeps its current relative order wherever the edges allow, and a plugin that must move goes only as far
as its constraints need. Virtual nodes have the lowest key, so they never hold a plugin back, and they never appear
in the output.
"""
import heapq
from dataclasses import dataclass, field

MASTERS_END = "<masters-end>"  # "<" can't be in a Windows file name, so a virtual node never meets a plugin
MAX_PATH_STEPS = 4             # a longer blocking path is shown as its first two and last two steps


@dataclass
class Edge:
    before: str               # plugin name or virtual node, as given (case kept)
    after: str
    source: str               # "masters", "type", "mod", "picks", "rules", "patches", "community", "userlist", ...
    rank: int                 # 1 = the game (highest) ... 8 = LOOT's masterlist
    text: str                 # one plain sentence, e.g. "B.esm has A.esm as a master"
    file: str = None          # the file the edge came from, None for game facts
    entry: object = None      # the entry in that file (a number or an id)
    data: dict = field(default_factory=dict)


def group_start(name):
    return f"<group:{name}:start>"


def group_end(name):
    return f"<group:{name}:end>"


def is_virtual(name):
    return name.startswith("<")


class Graph:
    """Build with Graph(plugins, tie_break), then add edges in rank order with add() or add_all()."""

    def __init__(self, plugins, tie_break="current"):
        if tie_break not in ("current", "name"):
            raise ValueError(f"bad tie-break {tie_break!r}")
        self.tie_break = tie_break
        self.names = {}       # node id (lower case) -> name as given
        self.key = {}         # node id -> heap key
        self.ord = {}         # node id -> its place in the topological order kept as edges arrive
        self.succ, self.pred = {}, {}
        self.edges = {}       # (a, b) ids -> Edge (the first edge accepted for that pair)
        self.rejected = []    # [{"edge": Edge, "path": [Edge, ...]}]: path runs from edge.after to edge.before
        self.loops = []       # [[Edge, ...]]: rank 1 cycles (master loops); with any, order() is None
        self.inactive = []    # [Edge]: edges naming a plugin that isn't in the graph
        for i, n in enumerate(plugins):
            self.add_node(n, (0, i, "") if tie_break == "current" else (0, 0, n.lower()))

    # ---------- nodes ----------
    def add_node(self, name, key=None):
        nid = name.lower()
        if nid in self.names:
            return nid
        if key is None:  # virtual nodes pop as soon as they are free
            key = (-1, len(self.names), nid)
        self.names[nid], self.key[nid], self.ord[nid] = name, key, len(self.ord)
        self.succ[nid], self.pred[nid] = set(), set()
        return nid

    def has(self, name):
        return name.lower() in self.names

    def plugins(self):
        return [n for nid, n in self.names.items() if not is_virtual(nid)]

    # ---------- edges ----------
    def add(self, edge, force=False):
        """Add one edge. Returns "added", "exists" (the pair is already ordered this way), "inactive" (a node isn't
        in the graph), "rejected" (it would close a cycle; the path is kept) or "loop" (a forced edge closed a
        cycle; kept in self.loops). force is for rank 1 master edges, which are never rejected."""
        a, b = edge.before.lower(), edge.after.lower()
        if a not in self.names or b not in self.names:
            self.inactive.append(edge)
            return "inactive"
        if a == b:
            return "exists"
        if (a, b) in self.edges:
            return "exists"
        path = self._insert(a, b)
        if path is not None:
            blocking = [self.edges[(x, y)] for x, y in zip(path, path[1:])]
            if force:
                self.loops.append(blocking + [edge])
                return "loop"
            self.rejected.append({"edge": edge, "path": blocking})
            return "rejected"
        self.edges[(a, b)] = edge
        self.succ[a].add(b)
        self.pred[b].add(a)
        return "added"

    def add_all(self, edges):
        """Add a set of edges that stand or fall together (a group membership: start before p and p before end).
        When one is rejected, the ones this call added are taken out again. Returns the add() status of the first
        edge that wasn't added or exists ("added" when all were)."""
        done = []
        for e in edges:
            st = self.add(e)
            if st == "added":
                done.append(e)
            elif st != "exists":
                for d in done:
                    self.remove(d)
                return st
        return "added"

    def remove(self, edge):
        """Take an edge out (removing an edge never breaks the kept topological order)."""
        a, b = edge.before.lower(), edge.after.lower()
        if self.edges.get((a, b)) is edge:
            del self.edges[(a, b)]
            self.succ[a].discard(b)
            self.pred[b].discard(a)

    def _insert(self, a, b):
        """Make room for a before b in the kept order. Returns None when it fits (the order is updated), or the
        node path b ... a that would close a cycle."""
        lo, hi = self.ord[b], self.ord[a]
        if lo > hi:
            return None  # already in this order: no cycle is possible
        # forward from b, only through nodes not after a
        parent, stack, fwd = {b: None}, [b], []
        while stack:
            n = stack.pop()
            fwd.append(n)
            for m in self.succ[n]:
                if m == a:
                    parent[m] = n
                    path, x = [], a
                    while x is not None:
                        path.append(x)
                        x = parent[x]
                    return path[::-1]
                if m not in parent and self.ord[m] < hi:
                    parent[m] = n
                    stack.append(m)
        # backward from a, only through nodes not before b
        seen, stack, back = {a}, [a], []
        while stack:
            n = stack.pop()
            back.append(n)
            for m in self.pred[n]:
                if m not in seen and self.ord[m] > lo:
                    seen.add(m)
                    stack.append(m)
        back.sort(key=self.ord.get)
        fwd.sort(key=self.ord.get)
        slots = sorted(self.ord[n] for n in back + fwd)
        for n, s in zip(back + fwd, slots):
            self.ord[n] = s
        return None

    # ---------- the order ----------
    def order(self):
        """The plugins in load order (virtual nodes left out), or None when a master loop leaves no valid order."""
        if self.loops:
            return None
        indeg = {n: len(p) for n, p in self.pred.items()}
        heap = [(self.key[n], n) for n, d in indeg.items() if d == 0]
        heapq.heapify(heap)
        out = []
        while heap:
            _, n = heapq.heappop(heap)
            if not is_virtual(n):
                out.append(self.names[n])
            for m in self.succ[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    heapq.heappush(heap, (self.key[m], m))
        return out

    def reason(self, plugin):
        """The accepted edges that put plugin after something (the ones a move can name), highest rank first."""
        p = plugin.lower()
        return sorted((self.edges[(a, p)] for a in self.pred.get(p, ())), key=lambda e: (e.rank, e.file or "",
                                                                                         str(e.entry)))

    # ---------- the game's rules as edges ----------
    def add_masters(self, masters_of, text=None):
        """Rank 1: each master before the plugin that lists it. masters_of: {plugin: [master names]}. Forced."""
        for p, ms in masters_of.items():
            for m in ms or []:
                if self.has(m) and self.has(p):
                    self.add(Edge(m, p, "masters", 1, (text or "{p} has {m} as a master").format(p=p, m=m)),
                             force=True)

    def add_type_rule(self, is_master, hoisted_by=None):
        """Rank 1: every master plugin before every plugin that isn't one, through the virtual node MASTERS_END
        (2n edges, not n squared). is_master: {plugin: bool}. hoisted_by: {plugin: master plugin} for a plain plugin
        counted as a master plugin because that master plugin needs it (order.load_rules). Add it after
        add_masters: a master plugin that lists a non-master as its master can't meet both, and the master edge
        wins (the type edge is rejected and kept in self.rejected for a problem finding)."""
        if not any(is_master.values()) or all(is_master.values()):
            return
        self.add_node(MASTERS_END)
        hoisted_by = hoisted_by or {}
        for p, m in is_master.items():
            if not self.has(p):
                continue
            if m and p in hoisted_by:
                self.add(Edge(p, MASTERS_END, "type", 1, f"{p} isn't a master plugin, but the master plugin "
                                                          f"{hoisted_by[p]} needs it, so the game loads it with the "
                                                          "master plugins, before every plugin that isn't one"))
            elif m:
                self.add(Edge(p, MASTERS_END, "type", 1, f"{p} is a master plugin, and the game loads master plugins "
                                                          "before every plugin that isn't one"))
            else:
                self.add(Edge(MASTERS_END, p, "type", 1, f"{p} isn't a master plugin (no master flag and not a .esm "
                                                          "or .esl file), so the game loads it after every master "
                                                          "plugin"))

    # ---------- groups ----------
    def add_group(self, name, rank, source, file=None):
        """The virtual nodes of group `name` (start before end)."""
        self.add_node(group_start(name))
        self.add_node(group_end(name))
        self.add(Edge(group_start(name), group_end(name), source, rank, f"group {name}", file))

    def add_member(self, plugin, group, rank, source, text, file=None, entry=None):
        """plugin in group: start before plugin before end, both or neither. Returns the add_all() status."""
        self.add_group(group, rank, source, file)
        return self.add_all([Edge(group_start(group), plugin, source, rank, text, file, entry),
                             Edge(plugin, group_end(group), source, rank, text, file, entry)])

    def add_group_after(self, group, after_group, rank, source, text, file=None, entry=None):
        """group loads after after_group: after_group's end before group's start."""
        self.add_group(group, rank, source, file)
        self.add_group(after_group, rank, source, file)
        return self.add(Edge(group_end(after_group), group_start(group), source, rank, text, file, entry))


def describe(path):
    """Plain words for a blocking path ([Edge]): each step's sentence, a long path shortened to its first two and
    last two steps."""
    texts = [e.text for e in path]
    if len(texts) > MAX_PATH_STEPS:
        texts = texts[:2] + [f"and {len(texts) - 4} more steps"] + texts[-2:]
    return "; ".join(texts)
