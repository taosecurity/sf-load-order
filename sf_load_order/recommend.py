"""Recommended load order (section "recommend"): rate the order, look for a better one, write the files to apply.

How the recommendation is made:
  1. Start from the current enabled order (the order the game loads; LOOT's, if the player just sorted).
  2. Apply the player's --picks first (ctx.results["picks"]): plugin X loads right after plugin Y. Plugins that need
     X as a master move with it. A pick that would put a plugin above its own master is not applied.
  3. Rate an order by the changes it throws away, from sf-mod-check's conflicts.json (ctx.results["records"]): each
     lost field counts its weight (3 functional, 1 cosmetic, 0 bookkeeping or "No effect"), and official changes a
     mod undoes count too. Lower is better.
  4. For each pair whose advice is "patch candidate" or "pick a winner", try letting the losing plugin load after the
     winner. Keep the move only if the score of the WHOLE order drops. Climb until a pass finds nothing better, then
     restart the climb from its own result until a restart finds nothing better.
Constraints, the strongest first (as in sortgraph, ranks 1 and 2): masters stay above the plugins that need them,
master plugins (master flag, or a .esm/.esl name) stay above the plugins that aren't (the game loads them first
whatever the list says), and a mod's plugins keep their current relative order where that doesn't contradict the
first two (a mod that lists a patch above the master it needs gets its master order fixed). Official plugins and
BlueprintShips files stay where the game puts them.

What the data can't tell: conflicts.json lists only the losses of the order it was run
on. When a pair is flipped, the new loser's loss is known only in part: the "Choice" fields, where both plugins
changed the same field. Fields only the old winner changed aren't written, so a flipped pair's cost is a lower
bound (pair["cost_complete"] False) and the score of the recommended order is an ESTIMATE. With exact costs
(cost_complete True), known_cost_if_flipped is the whole cost and a move can be rejected for raising the score.

With no records data (--depth light, sf-mod-check missing, or the section skipped) the order can't be rated beyond
masters and archives: only master order and picks are applied, and no record-based move is suggested.

ctx.results["recommend"]:
  {"order": [names in load order, official included], "moves": [{"plugin", "after", "reason"}],
   "score_current", "score_recommended" (None without records data), "estimate": bool, "same_as_current": bool,
   "rated": bool, "label": "your current order (LOOT's if you just sorted)", "flipped": [pair summaries],
   "rejected": [{"loser", "winner", "why"}], "picks": {"applied": [...], "not_applied": [{"pick", "why"}]},
   "archive_flips": [{"path", "before", "after"}], "remaining": [lost-change summaries for a merged patch],
   "vs_loot": None or {"file", "moved", "moved_count", "score", "missing", "extra"} or {"file", "error"},
   "files": {role: path}}
"""
import bisect
import heapq
import os

from . import archives, inputs, log, records, writers
from . import order as order_check
from .graph import Edge, Graph, is_virtual
from .model import Finding

SECTION = "recommend"
LABEL_CURRENT = "your current order (LOOT's if you just sorted)"
ACTION = {"merge", "conflict"}
ESTIMATE_WHY = ("an estimate: sf-mod-check's conflicts.json lists only the losses of the order it checked, so the "
                "cost of a flipped pair is known only for fields both plugins changed")

# field -> plain words for the report
QUEST = "quest objects and NPCs added to the location"
PLAIN = [
    (lambda f: f.startswith("Added") and f.endswith(("References", "Npcs")), QUEST),
    (lambda f: f in ("AmbienceSet", "Music", "AcousticSpace"), "music and ambient sound"),
    (lambda f: f == "Climate", "weather"),
    (lambda f: f == "Biome", "biome (plants, creatures, resources)"),
    (lambda f: f in ("Lighting", "LightingTemplate", "ImageSpace", "EnvironmentMap", "XILS", "SkyLighting"),
     "lighting"),
    (lambda f: "Water" in f, "water"),
    (lambda f: f in ("Location", "ParentLocation", "WorldLocationRadius"), "which location it belongs to"),
    (lambda f: f in ("Flags", "MajorFlags"), "record flags"),
    (lambda f: f == "Keywords", "keywords"),
    (lambda f: f in ("Owner", "Ownership"), "ownership"),
    (lambda f: f.startswith("Properties") or f == "VirtualMachineAdapter", "script settings"),
    (lambda f: f in ("Name", "Description"), "name"),
    (lambda f: f in ("MapData", "WorldMapOffset", "FNAM", "GNAM", "HNAM", "DistantLodMultiplier", "LandDefaults"),
     "map and distance display"),
    (lambda f: f in ("RequiredFaction", "ActorReactions"), "companion reactions"),
    (lambda f: f == "MapInfos", "navmesh map"),
    (lambda f: f == "Components", "attached components"),
    (lambda f: f == "ActorFadeMult", "NPC fade distance"),
]
MAX_PASSES, MAX_RESTARTS = 50, 20
NEAR_MIN, NEAR_SHARE = 10, 0.03  # near-tie rule: a saving under max(10, 3%) keeps the order


def plain_field(f):
    return next((t for test, t in PLAIN if test(f)), f)


def plain(fields):
    """Plain words for a list of field names (or records.py field dicts), weight-0 fields left out, no repeats."""
    out = []
    for f in fields:
        if isinstance(f, dict):
            if not f.get("weight"):
                continue
            f = f["field"]
        elif records.weight(f) == 0:
            continue
        p = plain_field(f)
        if p not in out:
            out.append(p)
    return out


# ---------- ordering ----------
def topo(current, edges):
    """Stable topological sort: keep the current order wherever the edges (a before b) allow. None on a cycle.
    An edge may name a virtual node (graph.is_virtual, such as MASTERS_END): it pops as soon as it is free and
    isn't in the output. Edges naming a plugin that isn't in current are ignored."""
    idx = {n.lower(): (0, i) for i, n in enumerate(current)}
    succ = {n: set() for n in idx}
    indeg = dict.fromkeys(idx, 0)
    for a, b in edges:
        a, b = a.lower(), b.lower()
        for v in (a, b):
            if v not in succ and is_virtual(v):
                idx[v], succ[v], indeg[v] = (-1, 0), set(), 0
        if a in succ and b in succ and a != b and b not in succ[a]:
            succ[a].add(b)
            indeg[b] += 1
    heap = [(idx[n], n) for n, d in indeg.items() if d == 0]
    heapq.heapify(heap)
    names = {n.lower(): n for n in current}
    out, done = [], 0
    while heap:
        _, n = heapq.heappop(heap)
        done += 1
        if n in names:
            out.append(names[n])
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(heap, (idx[m], m))
    return out if done == len(succ) else None


def _ok(order, edges):
    """True when order meets every edge. A virtual node (MASTERS_END, the only one _edges adds) is met when every
    plugin before it sits above every plugin after it. A position check, not a sort: arrange calls this per pick."""
    pos = {n.lower(): i for i, n in enumerate(order)}
    lo_after, hi_before = {}, {}
    for a, b in edges:
        a, b = a.lower(), b.lower()
        if is_virtual(b):
            if a in pos:
                hi_before[b] = max(hi_before.get(b, -1), pos[a])
        elif is_virtual(a):
            if b in pos:
                lo_after[a] = min(lo_after.get(a, len(pos)), pos[b])
        elif a in pos and b in pos and pos[a] >= pos[b]:
            return False
    return all(hi < lo_after.get(v, len(pos)) for v, hi in hi_before.items())


def arrange(base, edges, picks):
    """topo(base, edges), then each pick's plugin placed right after its `after` plugin where the edges allow (a
    plugin whose master or mod sibling sits in between stays below; it is still after `after`)."""
    order = topo(base, edges)
    if order is None:
        return None
    for p in picks:
        low = [n.lower() for n in order]
        x, y = p["plugin"].lower(), p["after"].lower()
        if x not in low or y not in low or low.index(x) == low.index(y) + 1:
            continue
        name = order[low.index(x)]
        trial = [n for n in order if n.lower() != x]
        trial.insert([n.lower() for n in trial].index(y) + 1, name)
        if _ok(trial, edges):
            order = trial
    return order


def moved(order, current):
    """Plugins that really move: those outside the longest run kept in the current relative order (plugins that only
    slide one place because another plugin moved past them don't count). Names missing from current are moved."""
    cur = {n.lower(): i for i, n in enumerate(current)}
    items = [(i, cur[n.lower()]) for i, n in enumerate(order) if n.lower() in cur]
    tails, tail_v, prev = [], [], {}
    for k, (i, v) in enumerate(items):
        j = bisect.bisect_left(tail_v, v)
        if j == len(tails):
            tails.append(k)
            tail_v.append(v)
        else:
            tails[j], tail_v[j] = k, v
        prev[k] = tails[j - 1] if j else -1
    keep, k = set(), tails[-1] if tails else -1
    while k != -1:
        keep.add(items[k][0])
        k = prev[k]
    return [n for i, n in enumerate(order) if i not in keep]


# ---------- scoring ----------
def _entry_weight(x):
    return sum(f.get("weight") or 0 for f in x.get("fields") or [])


def scorer(rec, rules=None):
    """score(order) -> (score, flipped pair indexes). Pairs between two mods count their gain_if_flipped (what the
    loser loses now) while the loser loads first, and known_cost_if_flipped once it loads after the winner. Official
    changes a mod undoes (official_undone) count always: no order of mods brings them back. rules (order.load_rules):
    "loads first" is judged in the order the game loads the list (master plugins first), not the list's own order."""
    pairs = rec.get("pairs") or []
    fixed = sum(_entry_weight(rec["lost"][i]) for i in rec.get("official_undone") or [])

    def score(order):
        pos = order_check.load_keys(order, rules) if rules is not None else {n.lower(): i for i, n in
                                                                             enumerate(order)}
        total, flipped = fixed, []
        for k, p in enumerate(pairs):
            lw, ll = p["winner"].lower(), p["loser"].lower()
            if ll in pos and lw in pos and pos[ll] > pos[lw]:
                total += p.get("known_cost_if_flipped") or 0
                flipped.append(k)
            else:
                total += p.get("gain_if_flipped") or 0
        return total, flipped
    return score


# ---------- the pieces of run ----------
def _add(ctx, level, text, fix=None, plugins=(), **data):
    ctx.report.add(Finding(level, SECTION, text, fix, list(plugins), data))


def _current(ctx):
    eff = (ctx.results.get("order") or {}).get("effective")
    return list(eff) if eff else ctx.load_order.enabled()


def _fixed(lo, name):
    return inputs.is_official(name) or name.lower().startswith("blueprintships-") or any(
        p.official for p in lo.plugins if p.name.lower() == name.lower())


def game_rules(lo):
    """order.load_rules for the plugins that load: what decides the game's order besides the list."""
    return order_check.load_rules(order_check.effective_order(lo))


def _edges(lo, movable):
    """Constraint edges among the movable plugins, ranked as sortgraph ranks them: (master, plugin) edges (forced),
    the type rule (every master plugin before MASTERS_END, MASTERS_END before every plugin that isn't one; a type
    edge that contradicts a master edge is left out), then (earlier, later) within each mod in the current order,
    each left out when it contradicts the edges above (a mod that lists Patch.esm above its master Base.esm).
    Returns (edges, why): why[plugin lower] = ("master", [movable masters]), ("mod", mod name) or ("type", bool
    is master), the first that applies."""
    low = {n.lower(): n for n in movable}
    pos = {n.lower(): i for i, n in enumerate(movable)}
    rules = game_rules(lo)
    block, up = order_check.master_block(rules), order_check.plain_hoisted(rules)
    g = Graph(movable)
    edges, why, masters_of, is_master = [], {}, {}, {}
    for p in lo.plugins:
        if p.name.lower() not in low:
            continue
        # a plain plugin a master plugin needs loads with the master plugins (order.load_rules), so it counts as one
        is_master[low[p.name.lower()]] = p.name.lower() in block
        for m in p.masters or []:
            if m.lower() in low:
                # every master edge stays in the list, even one that closes a master loop (topo then gives None)
                edges.append((low[m.lower()], p.name))
                masters_of.setdefault(p.name, []).append(low[m.lower()])
                w = why.setdefault(p.name.lower(), ("master", []))
                if w[0] == "master":
                    w[1].append(low[m.lower()])  # every movable master; the move reason names one that moved
    g.add_masters(masters_of)
    names = {p.name.lower(): p.name for p in lo.plugins}
    g.add_type_rule(is_master, {low[n]: names.get(m, m) for n, m in up.items() if n in low})
    edges += [(e.before, e.after) for e in g.edges.values() if e.source == "type"]
    for m in lo.mods or []:
        mine = sorted({x.lower() for x in (m.plugins or []) if x.lower() in low}, key=pos.get)
        neighbours = list(zip(mine, mine[1:]))
        dropped = [p for p in neighbours if not _mod_edge(g, low, m.name, p[0], p[1], edges, why)]
        if dropped:
            # a dropped neighbour pair (a, b) leaves a and the plugin after b unordered: keep every other pair of
            # the mod's order that the game's rules allow
            for i in range(len(mine)):
                for j in range(i + 2, len(mine)):
                    _mod_edge(g, low, m.name, mine[i], mine[j], edges, why)
    if any(is_master.values()) and not all(is_master.values()):
        for n, m in is_master.items():
            why.setdefault(n.lower(), ("type", m))
    return edges, why


def _mod_edge(g, low, mod, a, b, edges, why):
    """Add the mod-order edge a -> b (lower-case names) to g, edges and why. False when the game's rules reject it."""
    if g.add(Edge(low[a], low[b], "mod", 2, f"{low[a]} and {low[b]} are both in the mod {mod}")) == "rejected":
        log.info(SECTION, f"{mod} lists {low[a]} above {low[b]}, which the game's rules (masters, master plugins "
                          "first) put the other way; that pair of its plugin order isn't kept")
        return False
    edges.append((low[a], low[b]))
    why.setdefault(b, ("mod", mod))
    return True


def _type_only(low, before, after, is_mp):
    """True when the move of plugin `low` from order `before` to `after` crosses only plugins of the other type
    (master plugin or not), in the game's direction (a master plugin moving up past plain ones, a plain one moving
    down past master plugins): the game already loads it there, so the move only lists it where it loads."""
    pb = {n.lower(): i for i, n in enumerate(before)}
    pa = {n.lower(): i for i, n in enumerate(after)}
    crossed = [x for x in pb if x != low and x in pa and (pb[x] < pb[low]) != (pa[x] < pa[low])]
    up = bool(is_mp.get(low))  # a master plugin may only move up past plain ones: the crossed were above it
    return bool(crossed) and all(is_mp.get(x) != is_mp.get(low) and (pb[x] < pb[low]) == up for x in crossed)


def _picks(ctx, movable, edges):
    applied, not_applied = [], []
    low = {n.lower() for n in movable}
    for p in ctx.results.get("picks") or []:
        a, b = p["plugin"], p["after"]
        if a.lower() not in low or b.lower() not in low:
            why = "one of them isn't enabled (or is an official or BlueprintShips file the game places itself)"
        elif topo(movable, edges + [(b, a)]) is None:
            why = ("it would put a plugin above its own master, a plugin that isn't a master plugin above a master "
                   "plugin (.esm, .esl or the master flag: the game loads those first), or break a mod's own plugin "
                   "order")
        else:
            edges.append((b, a))
            applied.append(p)
            continue
        not_applied.append({"pick": p, "why": why})
        _add(ctx, "caution", f"Your pick {a} after {b} wasn't applied: {why}.",
             "Change or remove the pick in your --picks file.", [a, b], entry=p.get("entry"))
    return applied, not_applied


def _full(current, movable_order, fixed):
    """Put the movable plugins back into the slots between the fixed ones (official and BlueprintShips files keep
    their positions)."""
    it = iter(movable_order)
    return [n if n.lower() in fixed else next(it) for n in current]


def _pair_text(p, lost):
    fields = [f for i in p["records"] for f in lost[i]["fields"]]
    words = plain(fields)
    return ", ".join(words[:5]) + (f" and {len(words) - 5} more" if len(words) > 5 else "") if words else "fields"


def _read_loot(ctx, path):
    try:
        listed = inputs.read_plugins_list(path)
    except inputs.InputError as e:
        _add(ctx, "caution", f"The --loot-order file couldn't be read: {e}", file=path)
        return None, str(e)
    return [n for n, on in listed if on], None


BLOCKED_WHY = ("it would put a plugin above its own master, put a master plugin below a plugin that isn't one (the "
               "game loads master plugins first, whatever the list says), break a mod's own plugin order or undo one "
               "of your picks")


def plan(current, movable, fixed, edges, applied, rec, skip=None, blocked_why=BLOCKED_WHY, arranger=None, rules=None):
    """Arrange the movable plugins under the hard edges and picks, then run the score climb over the record-conflict
    pairs (the module docstring, steps 2 to 4, and the near-tie rule). The main run and `sort` share it.

    current: the whole current enabled order; movable: its movable plugins in current order; fixed: lower-case names
    that keep their slots; edges: [(before, after)] hard edges among movable plugins (the climb adds to a copy);
    applied: the picks applied; rec: ctx.results["records"] when the order can be rated, else None.
    skip(k, pair) -> True leaves a pair out of the climb (sort: a pair a hard edge already decides).
    blocked_why: the reason given for a pair whose move breaks a hard edge.
    arranger(base, edges, picks) -> order or None replaces arrange() (sort passes its graph's order).
    rules: order.load_rules (game_rules), so each order is scored as the game loads it.

    Returns {"order": movable order, "score_current", "score_recommended" (None unrated), "flipped": [pair index],
    "flipped0": {pair index flipped in the current order}, "rejected": [{"loser", "winner", "why"}],
    "estimate": bool, "candidates": [pair index], "flipped_start": {pair index already flipped by the arranged
    order before the climb (masters, picks, rules edges)}}."""
    arranger = arranger or arrange
    order = arranger(movable, edges, applied)
    if order is None:  # a master loop in the plugins themselves: leave the order alone
        order = list(movable)
        log.info(SECTION, "master loop among the plugins; order left as is")

    score_cur = score_rec = None
    flipped, flipped0, rejected, cands, flipped_start = [], set(), [], [], set()
    estimate = False
    if rec is not None:
        pairs = rec.get("pairs") or []
        score = scorer(rec, rules)
        score_cur, f0 = score(_full(current, movable, fixed))
        flipped0 = set(f0)
        best = score(_full(current, order, fixed))[0]
        pos0 = {n.lower(): i for i, n in enumerate(movable)}
        # records.acts_on is the one "acts on" rule (advice merge/conflict, not a BlueprintShips winner); an
        # ordinary official winner is never moved either, which acts_on doesn't cover
        cands = [k for k, p in enumerate(pairs) if records.acts_on(p) and not p.get("winner_official")
                 and p["loser"].lower() in pos0 and p["winner"].lower() in pos0 and not (skip and skip(k, p))]
        tried = {}

        def climb(base, best):
            ed, cur_order = list(edges), arranger(base, edges, applied)
            for _ in range(MAX_PASSES):
                changed = False
                for k in cands:
                    p = pairs[k]
                    e = (p["winner"], p["loser"])
                    if e in ed:
                        continue
                    o = arranger(base, ed + [e], applied)
                    if o is None:
                        tried[k] = blocked_why
                        continue
                    s = score(_full(current, o, fixed))[0]
                    if s < best:
                        ed.append(e)
                        cur_order, best, changed = o, s, True
                        tried.pop(k, None)
                    else:
                        tried.setdefault(k, f"the whole order would lose as much or more (score {s} vs {best})")
                if not changed:
                    break
            return cur_order, best

        start = list(order)  # the current order with picks and master fixes only
        flipped_start = set(score(_full(current, start, fixed))[1])
        for _ in range(MAX_RESTARTS):
            o, s = climb(order, best)
            if s >= best:
                break
            order, best = o, s
        # Not worth another import: when the record moves save
        # less than 3% (at least 10 points), keep the start order. The search is local, so near-ties shift with it.
        start_score = score(_full(current, start, fixed))[0]
        if order != start and start_score - best < max(NEAR_MIN, NEAR_SHARE * start_score):
            log.info(SECTION, f"near tie: moves would save {start_score - best} of {start_score}; keeping the order")
            dropped = set(score(_full(current, order, fixed))[1]) - set(score(_full(current, start, fixed))[1])
            for k in dropped:
                tried[k] = f"the saving is too small to be worth re-importing (score {best} vs {start_score})"
            order = start
        score_rec, flipped = score(_full(current, order, fixed))
        newly = [k for k in flipped if k not in flipped0]
        estimate = any(not pairs[k].get("cost_complete") for k in newly)
        for k in cands:
            if k in flipped:
                continue
            p = pairs[k]
            rejected.append({"loser": p["loser"], "winner": p["winner"], "why": tried.get(k, "not tried")})
            log.info(SECTION, f"kept {p['loser']} above {p['winner']}: {tried.get(k)}")
    return {"order": order, "score_current": score_cur, "score_recommended": score_rec, "flipped": flipped,
            "flipped0": flipped0, "rejected": rejected, "estimate": estimate, "candidates": cands,
            "flipped_start": flipped_start}


def mo2_officials_caution(ctx, lo, add):
    """The MO2 official-plugins caution (shared with sort.run); add is the section's own _add."""
    off = inputs.mo2_unpinned_officials(lo)
    if off:  # A5: a suspicion from MO2's source (2.5.2 pins eight plugins), so a caution, not extra lines
        add(ctx, "caution", f"MO2 2.5.2 pins only some official plugins, so copying in a plugins.txt this tool writes (it "
            f"leaves official plugins out) can leave {', '.join(off)} unticked in MO2.",
            f"After you copy the files, check in MO2's Plugins tab that {', '.join(off)} "
            f"{'is' if len(off) == 1 else 'are'} still ticked, and tick {'it' if len(off) == 1 else 'them'} if not "
            "(the 2.5.3 betas pin them at the top).", off)
        ctx.report.findings[-1].data = {"kind": "mo2-officials", "plugins": off}


def run(ctx):
    lo = ctx.load_order
    rec = ctx.results.get("records")
    rated = bool(rec and rec.get("lost") is not None)
    current = _current(ctx)
    fixed = {n.lower() for n in current if _fixed(lo, n)}
    movable = [n for n in current if n.lower() not in fixed]
    edges, edge_why = _edges(lo, movable)
    applied, not_applied = _picks(ctx, movable, edges)
    log.info(SECTION, f"{len(movable)} movable plugins, {len(edges)} constraint edges, {len(applied)} pick(s) applied, "
                      f"records data {'yes' if rated else 'no'}")
    mo2_officials_caution(ctx, lo, _add)
    rules = game_rules(lo)
    pl = plan(current, movable, fixed, edges, applied, rec if rated else None, rules=rules)
    order, score_cur, score_rec = pl["order"], pl["score_current"], pl["score_recommended"]
    flipped, flipped0, rejected, estimate = pl["flipped"], pl["flipped0"], pl["rejected"], pl["estimate"]
    full = _full(current, order, fixed)
    same = [n.lower() for n in full] == [n.lower() for n in current]

    # explain the moves
    flips_info = []
    if rated:
        pairs = rec["pairs"]
        for k in flipped:
            if k in flipped0:
                continue
            p = pairs[k]
            flips_info.append({"loser": p["loser"], "winner": p["winner"], "records": len(p["records"]),
                               "what": _pair_text(p, rec["lost"]),"gain": p.get("gain_if_flipped"),
                               "cost": p.get("known_cost_if_flipped"), "cost_complete": bool(p.get("cost_complete"))})
    pick_of = {p["plugin"].lower(): p for p in applied}
    moves = []
    mv = moved(order, movable)
    mv_low = {x.lower() for x in mv}
    is_mp = dict.fromkeys(order_check.master_block(rules), True)
    up = order_check.plain_hoisted(rules)
    names_of = {p.name.lower(): p.name for p in lo.plugins}
    cur_keys = order_check.load_keys(current, rules)

    def met_in_game(low, ms):
        """The game already loads every master in ms before low in the current order, and none of them moved."""
        return all(m.lower() not in mv_low and m.lower() in cur_keys and low in cur_keys
                   and cur_keys[m.lower()] < cur_keys[low] for m in ms)
    for n in mv:
        i = [x.lower() for x in full].index(n.lower())
        after = full[i - 1] if i else None
        low = n.lower()
        f = next((x for x in flips_info if x["loser"].lower() == low), None)
        g = next((x for x in flips_info if x["winner"].lower() == low), None)
        if low in pick_of:
            pk = pick_of[low]
            reason = f"your pick: load after {pk['after']}" + (f" ({pk['why']})" if pk.get("why") else "")
        elif f:
            reason = (f"so its changes to {f['records']} record(s) ({f['what']}) win over {f['winner']}"
                      + (" (estimated)" if not f["cost_complete"] else ""))
        elif g:
            reason = (f"so {g['loser']}'s changes to {g['records']} record(s) ({g['what']}) win over it"
                      + (" (estimated)" if not g["cost_complete"] else ""))
        elif low in edge_why and edge_why[low][0] == "master" and not met_in_game(low, edge_why[low][1]):
            ms = edge_why[low][1]
            hit = next((m for m in ms if m.lower() in mv_low), None)
            reason = (f"it needs {hit} as a master, which moved" if hit else
                      f"it must load after its master{'s' if len(ms) > 1 else ''} {', '.join(ms)}")
        elif low in edge_why and edge_why[low][0] == "mod":
            reason = f"it keeps its mod's own plugin order ({edge_why[low][1]})"
        elif low in edge_why and edge_why[low][0] in ("type", "master") and _type_only(low, movable, order, is_mp):
            # a master edge the game already meets: the move only lists the plugin where the game loads it
            reason = (f"it isn't a master plugin, but the master plugin {names_of.get(up[low], up[low])} needs it, "
                      "so the game loads it with the master plugins whatever the list says; this only lists it "
                      "where the game loads it" if low in up else
                      "the game loads master plugins (.esm, .esl or the master flag) before the others whatever the "
                      "list says, so this only lists it where the game loads it")
        else:
            reason = "its master order needed fixing" if not rated else "it moves along with the moves above"
        moves.append({"plugin": n, "after": after, "reason": reason})

    # archive winners that change, in the order the game loads each list (a type-only move changes no winner)
    def as_loaded(names):
        keys = order_check.load_keys(names, rules)
        return sorted(names, key=lambda n: keys[n.lower()])
    aflips = archives.flips(as_loaded(current), as_loaded(full), ctx.results.get("archives")) if not same else []
    if aflips:
        by = {}
        for x in aflips:
            by.setdefault((x["before"], x["after"]), []).append(x["path"])
        parts = "; ".join(f"{b} -> {a}: {len(ps)} file(s), for example {ps[0]}" for (b, a), ps in sorted(by.items()))
        _add(ctx, "caution", f"The recommended order also changes which mod's archived file the game uses for "
                             f"{len(aflips)} file(s) (today's winner -> new winner: {parts}). The score counts "
                             "records only.",
             "After applying the order, check those textures, meshes or sounds look or sound right; if not, keep "
             "your current order for those plugins.",
             sorted({n for (b, a) in by for n in (b, a)}), flips=aflips[:200], count=len(aflips))

    # what no order can keep: one merged-patch note
    remaining = []
    if rated:
        pos = {n.lower(): i for i, n in enumerate(full)}
        for k, p in enumerate(rec["pairs"]):
            # the game loads BlueprintShips files after every plugin, so a merged patch can't load after one
            # (records.analyse gives such a pair the advice "blueprint"; a hand-made pair may still say merge/conflict)
            if p.get("advice") == "blueprint" or (p.get("advice") in ACTION and (
                    p.get("blueprint") or records.is_blueprint(p["winner"], p.get("winner_official")))):
                log.info(SECTION, f"{p['loser']} vs {p['winner']}: left out of the merged-patch note (nothing "
                                  "loads after a BlueprintShips file)")
                continue
            if p.get("advice") not in ACTION:
                continue
            flip = k in flipped
            loser, winner = (p["winner"], p["loser"]) if flip else (p["loser"], p["winner"])
            if loser.lower() not in pos or winner.lower() not in pos:
                continue
            for i in p["records"]:
                x = rec["lost"][i]
                # flipped: the old winner now loses its own changes where both changed a field (Choice), as far
                # as the data shows
                fs = [f for f in x["fields"] if f.get("weight") and (not flip or f["label"] == "Choice")]
                if fs:
                    remaining.append({"fk": x["fk"], "edid": x["edid"], "type": x["type"], "loser": loser,
                                      "winner": winner, "weight": sum(f["weight"] for f in fs),
                                      "fields": plain(fs), "official": False})
        for i in rec.get("official_undone") or []:
            x = rec["lost"][i]
            fs = [f for f in x["fields"] if f.get("weight")]
            if fs:
                remaining.append({"fk": x["fk"], "edid": x["edid"], "type": x["type"], "loser": x["loser"],
                                  "winner": x["winner"], "weight": sum(f["weight"] for f in fs),
                                  "fields": plain(fs), "official": True})
        remaining.sort(key=lambda r: (-r["weight"], r["edid"] or r["fk"]))
    if remaining:
        top = remaining[:8]
        names = "; ".join(f"{r['type']} {r['edid'] or r['fk']} ({', '.join(r['fields'][:4])}: "
                          f"{r['loser']} loses to {r['winner']})" for r in top)
        quest = [r for r in remaining if QUEST in r["fields"]]
        imp = (f" The important part: {len(quest)} location(s) where one mod's {QUEST} are dropped, which can stop a "
               "quest or an NPC from appearing there.") if quest else ""
        _add(ctx, "note", f"No load order keeps every change: {len(remaining)} record(s) still lose changes under the "
                          f"recommended order. A merged patch would keep both sides' changes.{imp} The most important: "
                          f"{names}" + (f"; and {len(remaining) - 8} more." if len(remaining) > 8 else "."),
             "Make one merged patch for these records (in xEdit or another patch tool), load it after every plugin "
             "it covers, and check it in game.",
             sorted({n for r in remaining for n in (r["loser"], r["winner"])}),
             records=[{k: r[k] for k in ("fk", "edid", "type", "loser", "winner", "weight", "fields", "official")}
                      for r in remaining[:100]], count=len(remaining))

    # compare with LOOT's order
    vs_loot = None
    lpath = getattr(ctx.args, "loot_order", None)
    if lpath:
        loot, err = _read_loot(ctx, lpath)
        if err:
            vs_loot = {"file": lpath, "error": err}
        else:
            seen_l = set()  # a name listed twice (in any case): the first line counts, as in the game's Plugins.txt
            loot = [n for n in loot if not (n.lower() in seen_l or seen_l.add(n.lower()))]
            lset = {n.lower() for n in loot}
            ours = [n for n in full if n.lower() not in fixed]
            common = [n for n in ours if n.lower() in lset]
            loot_c = [n for n in loot if n.lower() in {x.lower() for x in common}]
            mvl = moved(common, loot_c)
            vs_loot = {"file": lpath, "moved": mvl, "moved_count": len(mvl),
                       "missing": [n for n in ours if n.lower() not in lset],
                       "extra": [n for n in loot if n.lower() not in {x.lower() for x in ours}
                                 and not _fixed(lo, n)], "score": None}
            if rated:  # LOOT's order of the plugins it names, the rest after them, fixed files in their slots
                loot_full = _full(current, loot_c + [n for n in ours if n.lower() not in lset], fixed)
                vs_loot["score"] = scorer(rec, rules)(loot_full)[0]
            log.info(SECTION, f"vs LOOT {lpath}: {len(mvl)} plugin(s) placed differently")

    # the summary finding
    if not rated:
        text = ("The order can't be rated beyond masters and archives: there is no record-conflict data (run at "
                "--depth medium with sf-mod-check 0.8.0 or later, or pass --conflicts), so no record-based move is "
                "suggested.")
        if not same:
            text += f" {len(moves)} plugin(s) move only to fix master order or apply your picks."
        _add(ctx, "note", text, None if same else "Apply the files in the output folder (HOW-TO-APPLY.txt).",
             [m["plugin"] for m in moves])
    elif same:
        _add(ctx, "note", f"Keep {LABEL_CURRENT}: no move the data supports makes it lose less (loss score "
                          f"{score_cur}, lower is better). The files written equal the current order.")
    else:
        est = f" The new score is {ESTIMATE_WHY}." if estimate else ""
        _add(ctx, "note", f"The recommended order moves {len(moves)} plugin(s). Loss score (lower is better): "
                          f"{LABEL_CURRENT} {score_cur}, recommended {score_rec}.{est}",
             "Apply the files in the output folder (HOW-TO-APPLY.txt), after backing up your current order.",
             [m["plugin"] for m in moves], estimate=estimate)
    if rated and rec.get("differs"):
        _add(ctx, "caution", "The record-conflict data was made for a different load order than this one, so the "
                             "scores may be off.", "Run again without --conflicts so sf-mod-check checks this order.")

    res ={"order": full, "moves": moves, "score_current": score_cur, "score_recommended": score_rec,
           "estimate": estimate, "same_as_current": same, "rated": rated, "label": LABEL_CURRENT,
           "flipped": flips_info, "rejected": rejected,
           "picks": {"applied": applied, "not_applied": not_applied},
           "archive_flips": aflips, "remaining": remaining, "vs_loot": vs_loot}
    ctx.results[SECTION] = res
    ctx.report.recommended = full
    files = writers.write(ctx)
    res["files"] = files
    ctx.report.outputs.update(files)
    log.info(SECTION, f"{len(moves)} move(s), score {score_cur} -> {score_rec}{' (estimate)' if estimate else ''}, "
                      f"files {sorted(files)}")
    return {"status": "done", "moves": len(moves), "score_current": score_cur, "score_recommended": score_rec,
            "estimate": estimate, "same_as_current": same, "rated": rated}
