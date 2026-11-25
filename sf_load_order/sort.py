"""The `sort` command's section (DESIGN-0.3.0.md 1.1 to 1.10): build a whole, valid load order from every rule source.

`sf-load-order sort` runs the main pipeline with this section in place of "recommend" (cli.SORT_PIPELINE), so every
other check runs too. This section:

  1. builds the hard graph (sortgraph.build) from the game's rules, the mods' own plugin order, --picks, the user
     rules file, the patch targets (--clashes fixed_by and patch-sources.json), the community rules file and LOOT's
     userlist and masterlist (groups only with --loot-groups);
  2. runs the soft climb (recommend.plan) over the record-conflict pairs with the graph's edges, leaving out the
     pairs a hard edge already decides, and keeps the near-tie rule;
  3. applies the Vortex mod rules as a tie-break: a rule's plugins follow the rule only when the record score stays
     the same, and each rule the order still breaks gets a note;
  4. reports the rules that weren't used (rejected), master loops, rules naming a plugin that isn't enabled
     (inactive, counted), requires and incompatible checks, and stale rules;
  5. writes plugins.txt, loadorder.txt (MO2), the Vortex import file, HOW-TO-APPLY.txt (writers.write) and
     rules-proposed.json (one proposal per record-conflict choice the climb made).

A master loop (only a broken mod has one) leaves no valid order: no order files are written, only
rules-proposed.json (empty) and the report.

ctx.results["sort"] and the report's top-level "sort" key (report.json; keys only added):
  {"tie_break", "order", "moved", "moves": [{plugin, after, reason, source}], "score_current", "score_sorted",
   "estimate", "rated", "same_as_current", "sources": {source: accepted edges}, "rejected": [{edge, source, entry,
   file, why, path, level, text}], "inactive_rules", "requires_missing", "incompatible", "proposed", "stale",
   "vortex_broken", "loot_files", "decided", "loop", "files"}
"""
import heapq
import os

from . import log, loot_rules, recommend, rulesfile, sortgraph, writers
from .model import Finding

SECTION = "sort"
LABEL_SORTED = "sorted"


def _add(ctx, level, text, fix=None, plugins=(), **data):
    ctx.report.add(Finding(level, SECTION, text, fix, list(plugins), data))


# ---------- the sources ----------
def patch_targets(ctx, lo):
    """Rank 5 edges: every plugin a patch fixes loads before it. From --clashes entries with fixed_by (the clashes
    section's results) and from each patch-sources.json (records[].editors[].plugin before patch.name)."""
    from . import merged
    out, seen = [], set()

    def take(patch, target, file, entry, text):
        if patch.lower() == target.lower() or (patch.lower(), target.lower()) in seen:
            return
        seen.add((patch.lower(), target.lower()))
        out.append({"patch": patch, "target": target, "file": file, "entry": entry, "text": text})

    for k in (ctx.results.get("clashes") or {}).get("known") or []:
        fb = k.get("fixed_by")
        if not fb:
            continue
        for p in k.get("plugins") or []:
            take(fb, p, getattr(ctx.args, "clashes", None), k.get("entry"),
                 f"your --clashes file (entry {k.get('entry')}) says {fb} fixes {p}, so {fb} loads after it")
    for path, _given in merged.find_manifests(lo, getattr(ctx.args, "patch_sources", None)):
        d = merged._load(path)
        if not merged._looks_like_manifest(d):
            continue
        name = (d.get("patch") or {}).get("name")
        if not isinstance(name, str) or not rulesfile.is_name(name):
            continue
        for k, r in enumerate(d.get("records") or []):
            for ed in (r.get("editors") if isinstance(r, dict) else None) or []:
                p = ed.get("plugin") if isinstance(ed, dict) else None
                if isinstance(p, str) and rulesfile.is_name(p):
                    take(name, p, path, k + 1, f"the patch {name} was built from {p} ({os.path.basename(path)}), "
                                               "so it loads after it")
    return out


def vortex_prefs(lo, movable):
    """The Vortex mod rules as soft plugin edges: [{"before", "after", "rule": text, "key"}]. A rule "W loads after
    L" (W's loose files win) keeps L's plugins before W's, so their archives win the same way."""
    from . import rules as vrules
    modlist = getattr(lo, "_vortex_modlist", None)
    if not modlist or not lo.mods:
        return []
    mov = {n.lower(): n for n in movable}
    plugins_of = {}
    for m in lo.mods:
        mine = [mov[x.lower()] for x in m.plugins or [] if x.lower() in mov]
        for k in (m.id, m.name, os.path.basename(m.path or "")):
            if vrules._key(k):
                plugins_of.setdefault(vrules._key(k), mine)
    edges, _unmatched = vrules.ordering(modlist)
    out = []
    for pair in sorted(edges, key=lambda s: sorted(s)):
        for r in edges[pair]:
            w = vrules._key(r["wins"])
            lose = vrules._key(r["other"] if r["wins"] == r["mod"] else r["mod"])
            text = f"Vortex's rule: {r['mod']} loads {r['type']} {r['other']}"
            for a in plugins_of.get(lose) or []:
                for b in plugins_of.get(w) or []:
                    out.append({"before": a, "after": b, "rule": text, "key": (r["mod"], r["type"], r["other"])})
    return out


# ---------- ordering on the graph ----------
def _topo(g, extra, key=None):
    """The graph's plugins in order with extra edges [(a, b)] (names) added; None when they close a cycle. key:
    {node id: sort key} for plugins (default the graph's tie-break key)."""
    succ = {n: set(s) for n, s in g.succ.items()}
    indeg = {n: len(p) for n, p in g.pred.items()}
    for a, b in extra:
        a, b = a.lower(), b.lower()
        if a in succ and b in succ and a != b and b not in succ[a]:
            succ[a].add(b)
            indeg[b] += 1
    def kf(n):
        return g.key[n] if key is None else key.get(n, g.key[n])
    heap = [(kf(n), n) for n, d in indeg.items() if d == 0]
    heapq.heapify(heap)
    out, done = [], 0
    while heap:
        _, n = heapq.heappop(heap)
        done += 1
        if not n.startswith("<"):
            out.append(g.names[n])
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(heap, (kf(m), m))
    return out if done == len(succ) else None


def _valid(g, extra, order):
    """True when order (plugins) meets every graph edge and the extra edges."""
    key = {n.lower(): (0, i, "") for i, n in enumerate(order)}
    return _topo(g, extra, key) == order


def arranger(g):
    """recommend.plan's arranger for the graph: the graph's order with the climb's edges, then each pick's plugin
    placed right after its `after` plugin where every edge allows (the adjacency step of recommend.arrange)."""
    def arr(_base, edges, picks):
        order = _topo(g, edges)
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
            if _valid(g, edges, trial):
                order = trial
        return order
    return arr


def _reaches(g, a, b):
    a, b = a.lower(), b.lower()
    if a not in g.succ or b not in g.succ:
        return False
    seen, stack = {a}, [a]
    while stack:
        n = stack.pop()
        for m in g.succ[n]:
            if m == b:
                return True
            if m not in seen:
                seen.add(m)
                stack.append(m)
    return False


# ---------- the soft part (shared with `rules explain` and `rules propose`) ----------
def soft(ctx, b, rec=None):
    """The soft climb and the Vortex tie-break on a built graph (b.order not None). Returns {"order" (movable),
    "full", "pl" (recommend.plan's result), "flips": [pair info], "decided": n, "vortex": {"kept", "broken"},
    "applied": picks applied, "proposals": [rules-proposed entries], "soft_edges": [(a, b)]}."""
    lo, g = ctx.load_order, b.graph
    rated = bool(rec and rec.get("lost") is not None)
    applied = [p for p in (ctx.results.get("picks") or []) if g.has(p["plugin"]) and g.has(p["after"])
               and not any(r["edge"].source == "picks" and r["edge"].entry == p.get("entry") for r in b.rejected)]
    decided = []

    def skip(_k, p):
        if _reaches(g, p["winner"], p["loser"]) or _reaches(g, p["loser"], p["winner"]):
            decided.append(p)
            return True
        return False
    arr = arranger(g)
    pl = recommend.plan(b.current, b.movable, b.fixed, [], applied, rec if rated else None, skip=skip,
                        blocked_why="it would break a rule the sort must keep (a master, a mod's own plugin order, "
                                    "a pick or a rules-file rule)", arranger=arr)
    order = pl["order"]
    pairs = (rec or {}).get("pairs") or []
    newly = [k for k in pl["flipped"] if k not in pl["flipped0"]]
    soft_edges = [(pairs[k]["winner"], pairs[k]["loser"]) for k in newly]
    score = recommend.scorer(rec) if rated else None
    best = pl["score_recommended"]

    # the Vortex tie-break: keep a rule's plugin order when the record score stays the same
    kept, broken = [], []
    prefs = vortex_prefs(lo, b.movable)
    for v in prefs:
        pos = {n.lower(): i for i, n in enumerate(order)}
        if pos.get(v["before"].lower(), -1) < pos.get(v["after"].lower(), -1):
            kept.append(v)
            continue
        trial = arr(None, soft_edges + [(x["before"], x["after"]) for x in kept] + [(v["before"], v["after"])],
                    applied)
        if trial is not None and (score is None or score(recommend._full(b.current, trial, b.fixed))[0] <= best):
            order = trial
            kept.append(v)
        else:
            broken.append(v)
    # a rule kept earlier can't be broken by a later one (each trial keeps every kept edge), so `broken` is final
    full = recommend._full(b.current, order, b.fixed)
    if score is not None:
        pl["score_recommended"], flipped = score(full)
        newly = [k for k in flipped if k not in pl["flipped0"]]
    flips = []
    for k in newly:
        p = pairs[k]
        flips.append({"k": k, "loser": p["loser"], "winner": p["winner"], "records": len(p["records"]),
                      "what": recommend._pair_text(p, rec["lost"]), "cost_complete": bool(p.get("cost_complete")),
                      "fks": [rec["lost"][i]["fk"] for i in p["records"] if rec["lost"][i].get("fk")]})
    proposals = [choice_proposal(lo, f) for f in flips]
    return {"order": order, "full": full, "pl": pl, "flips": flips, "decided": len(decided),
            "vortex": {"kept": kept, "broken": broken}, "applied": applied, "proposals": proposals}


def choice_proposal(lo, f):
    from .rules_cmd import proposal
    est = " (estimated)" if not f["cost_complete"] else ""
    text = (f"So its changes to {f['records']} record(s) ({f['what']}) win over {f['winner']}{est}: "
            f"{f['loser']} loads after {f['winner']}.")
    return proposal(lo, f["loser"], [f["winner"]], "choice", text, records=f["fks"])


def flip_reason(f):
    return (f"so its changes to {f['records']} record(s) ({f['what']}) win over {f['winner']}"
            + (" (estimated)" if not f["cost_complete"] else ""))


# ---------- the moves and their reasons ----------
def _virtual_preds(g, nid):
    """Plugins that reach the virtual node nid through virtual nodes only."""
    out, seen, stack = [], {nid}, [nid]
    while stack:
        n = stack.pop()
        for m in g.pred[n]:
            if m in seen:
                continue
            seen.add(m)
            (stack if m.startswith("<") else out).append(m)
    return out


def _virtual_succs(g, nid):
    out, seen, stack = [], {nid}, [nid]
    while stack:
        n = stack.pop()
        for m in g.succ[n]:
            if m in seen:
                continue
            seen.add(m)
            (stack if m.startswith("<") else out).append(m)
    return out


def _edge_reason(b, name, cur):
    """(text, source) of the hard edge that moved a plugin: an edge into it from a plugin that was below it, or out
    of it to a plugin that was above it (through virtual nodes too). None when none did."""
    g, p = b.graph, name.lower()
    if not g.has(p):
        return None
    me = cur.get(p, 0)
    best = None
    for a in g.pred[p]:
        e = g.edges[(a, p)]
        below = [a] if not a.startswith("<") else _virtual_preds(g, a)
        if any(cur.get(x, -1) > me for x in below) and (best is None or e.rank < best.rank):
            best = e
    for c in g.succ[p]:
        e = g.edges[(p, c)]
        above = [c] if not c.startswith("<") else _virtual_succs(g, c)
        if any(cur.get(x, 1 << 30) < me for x in above) and (best is None or e.rank < best.rank):
            best = e
    if best is None:
        return None
    return best.text, sortgraph.REPORT_SOURCE.get(best.source, best.source)


def moves(b, sf):
    """[{plugin, after, reason, source}] for each plugin the sort really moves."""
    full, order = sf["full"], sf["order"]
    cur = {n.lower(): i for i, n in enumerate(b.current)}
    mv = recommend.moved(order, b.movable)
    full_low = [n.lower() for n in full]
    pick_of = {p["plugin"].lower(): p for p in sf["applied"]}
    vort = {}
    for v in sf["vortex"]["kept"]:
        vort.setdefault(v["before"].lower(), v)
        vort.setdefault(v["after"].lower(), v)
    out = []
    for n in mv:
        low = n.lower()
        i = full_low.index(low)
        after = full[i - 1] if i else None
        f = next((x for x in sf["flips"] if x["loser"].lower() == low), None)
        w = next((x for x in sf["flips"] if x["winner"].lower() == low), None)
        er = _edge_reason(b, n, cur)
        if low in pick_of:
            pk = pick_of[low]
            reason, source = f"your pick: load after {pk['after']}" + (f" ({pk['why']})" if pk.get("why") else ""), \
                "picks"
        elif er:
            reason, source = er
        elif f:
            reason, source = flip_reason(f), "records"
        elif w:
            reason, source = (f"so {w['loser']}'s changes to {w['records']} record(s) ({w['what']}) win over it"
                              + (" (estimated)" if not w["cost_complete"] else "")), "records"
        elif low in vort:
            reason, source = f"so the plugins keep the order of {vort[low]['rule']}", "vortex"
        elif b.graph.tie_break == "name":
            reason, source = "--tie-break name puts the plugins in file-name order wherever the rules allow", "order"
        else:
            reason, source = "it moves along with the moves above", "order"
        out.append({"plugin": n, "after": after, "reason": reason, "source": source})
    return out


# ---------- the findings ----------
FIX = {"user": "Remove or change that entry in your rules file (sf-load-order rules check shows every problem).",
       "picks": "Change or remove the pick in your --picks file.",
       "type": "Tell the plugin's author: a master plugin shouldn't need a plugin that isn't one as its master. Until "
               "it is fixed, the game's master order is kept.",
       "patch": None, "community": None, "loot_userlist": None, "loot_masterlist": None, "loot_groups": None,
       "groups": None, "mod": None}


def _findings(ctx, b, res, files, stale_rows, req, inc, vbroken):
    for r in b.rejected:
        e = r["edge"]
        _add(ctx, r["level"], sortgraph.rejected_text(r), FIX.get(e.source), [x for x in (e.before, e.after)
                                                                             if not x.startswith("<")],
             kind="rule-not-used", source=sortgraph.REPORT_SOURCE.get(e.source, e.source), entry=e.entry,
             file=e.file)
    for x in req:
        _add(ctx, "problem", f"{x['plugin']} needs {x['needs']}, which isn't enabled ({_src(x)}).",
             f"Enable {x['needs']}, or disable {x['plugin']}. The sort never enables or disables a plugin.",
             [x["plugin"], x["needs"]], kind="requires", entry=x["entry"], file=x["file"])
    for x in inc:
        _add(ctx, "problem", f"{x['plugin']} and {x['with']} are both enabled, but they don't work together "
                             f"({_src(x)}).",
             f"Disable {x['plugin']} or {x['with']}. The sort never enables or disables a plugin.",
             [x["plugin"], x["with"]], kind="incompatible", entry=x["entry"], file=x["file"])
    if stale_rows:
        parts = "; ".join(f"{s['file_label']} {s['label']} about {s['plugin']} ({', '.join(s['changed'])} changed)"
                          for s in stale_rows[:10])
        more = f"; and {len(stale_rows) - 10} more" if len(stale_rows) > 10 else ""
        _add(ctx, "caution", f"{len(stale_rows)} rule(s) are stale: a plugin they name changed since the rule was "
                             f"pinned, so the conflict may be different now. They are still used: {parts}{more}.",
             "Run `sf-load-order sort` again, look at the new proposals, and accept or delete the old rule.",
             sorted({s["plugin"] for s in stale_rows}), kind="stale")
    for v in vbroken:
        _add(ctx, "note", f"The sorted order doesn't keep {v['rule']} for the plugins {v['before']} and "
                          f"{v['after']}: another rule, or the record conflicts, put {v['after']} first. Vortex's "
                          "rules decide only loose files; the plugin order (and so the archives) goes the other way.",
             None, [v["before"], v["after"]], kind="vortex-rule")
    if b.inactive:
        log.info(SECTION, f"{len(b.inactive)} rule(s) inactive (a plugin they name isn't enabled)")


def _src(x):
    return ("your rules file" if x["source"] == "user" else "the community rules file") + \
        f", {os.path.basename(x['file'])} entry {x['entry']}"


def _stale(files, lo):
    rows = []
    for source, label in (("user", "your rules file"), ("community", "the community rules file")):
        rf = files.get(source)
        if rf is None:
            continue
        for s in rulesfile.stale(rf, lo):
            rows.append(dict(s, source=source, file=rf.path, file_label=label,
                             label=f"entry {s['entry']}" + (f" (id {s['id']})" if s.get("id") else "")))
    return rows


# ---------- run ----------
def run(ctx, env=None):
    lo, args = ctx.load_order, ctx.args
    env = os.environ if env is None else env
    files = ctx.results.get("sort_files") or {"user": None, "community": None, "user_path": None}
    rec = ctx.results.get("records")
    rated = bool(rec and rec.get("lost") is not None)
    current = recommend._current(ctx)
    s = sortgraph.Sources(picks=ctx.results.get("picks") or [], user=files.get("user"),
                          community=files.get("community"))
    s.loot = loot_rules.read(ctx, env, groups=getattr(args, "loot_groups", False), lo=lo)
    s.patches = patch_targets(ctx, lo)
    b = sortgraph.build(lo, current, s, getattr(args, "tie_break", None) or "current")
    req, inc = sortgraph.rule_checks(lo, [("user", files.get("user")), ("community", files.get("community"))])
    stale_rows = _stale(files, lo)
    res = {"tie_break": b.graph.tie_break, "order": None, "moved": [], "moves": [], "score_current": None,
           "score_sorted": None, "estimate": False, "rated": rated, "same_as_current": None,
           "sources": dict(b.counts), "rejected": [], "inactive_rules": len(b.inactive),
           "requires_missing": req, "incompatible": inc, "proposed": 0,
           "stale": [{k: x[k] for k in ("source", "file", "entry", "id", "plugin", "changed")} for x in stale_rows],
           "vortex_broken": [], "loot_files": dict((s.loot or {}).get("files") or {}), "decided": 0, "loop": None,
           "files": {}}
    for r in b.rejected:
        e = r["edge"]
        res["rejected"].append({"edge": [e.before, e.after], "source": sortgraph.REPORT_SOURCE.get(e.source, e.source),
                                "entry": e.entry, "file": e.file, "why": r["why"], "level": r["level"],
                                "path": [x.text for x in r["path"]], "text": sortgraph.rejected_text(r)})
    ctx.results[SECTION] = res
    ctx.report.sort = res
    proposals, vbroken = [], []
    if b.order is None:
        loop = b.graph.loops[0]
        res["loop"] = [x.text for x in loop]
        _add(ctx, "problem", sortgraph.loop_text(loop) + " No sorted order was written.",
             "Tell the authors of these plugins: a plugin can't need itself as a master, even through other "
             "plugins. Until it is fixed, disable one of them.",
             sorted({n for x in loop for n in (x.before, x.after)}), kind="master-loop")
    else:
        sf = soft(ctx, b, rec)
        pl = sf["pl"]
        vbroken = sf["vortex"]["broken"]
        proposals = sf["proposals"]
        full = sf["full"]
        mv = moves(b, sf)
        res.update(order=full, moved=[m["plugin"] for m in mv], moves=mv, score_current=pl["score_current"],
                   score_sorted=pl["score_recommended"], estimate=pl["estimate"],
                   same_as_current=[n.lower() for n in full] == [n.lower() for n in current],
                   decided=sf["decided"], proposed=len(proposals))
        res["sources"]["vortex"] = len(sf["vortex"]["kept"])
        res["vortex_broken"] = [{k: v[k] for k in ("before", "after", "rule")} for v in vbroken]
    _findings(ctx, b, res, files, stale_rows, req, inc, vbroken)
    if res["order"] is not None:
        if res["same_as_current"]:
            _add(ctx, "note", "Sorting keeps your current order: every rule is already met"
                              + (f" and no move the record data supports makes it lose less (loss score "
                                 f"{res['score_current']})" if rated else "") + ". The files written equal it.")
        else:
            est = f" The sorted score is {recommend.ESTIMATE_WHY}." if res["estimate"] else ""
            score = (f" Loss score (lower is better): current {res['score_current']}, sorted {res['score_sorted']}."
                     if rated else "")
            _add(ctx, "note", f"Sorting moves {len(res['moves'])} plugin(s).{score}{est}",
                 "Apply the files in the output folder (HOW-TO-APPLY.txt), after backing up your current order.",
                 res["moved"], estimate=res["estimate"])
        if b.graph.tie_break == "name":
            _add(ctx, "note", "--tie-break name orders the plugins by file name wherever the rules allow, so the "
                              "same plugins and rules give the same order on any PC. It moves many plugins.")
        ctx.report.recommended = res["order"]
        files_w = writers.write(ctx, res=dict(res, same_as_current=res["same_as_current"]), section=SECTION,
                                intro=[f"Sorting moves {len(res['moves'])} plugin(s). Back up your current order "
                                       "first (the steps below say how).", ""])
        res["files"] = files_w
        ctx.report.outputs.update(files_w)
    try:
        path = _write_proposed(args.out, proposals)
        res["files"]["rules_proposed"] = path
        ctx.report.outputs["rules_proposed"] = path
    except OSError as e:
        _add(ctx, "caution", f"rules-proposed.json couldn't be written to {args.out} ({e.strerror or e}).",
             f"Check {args.out} can be written, then run sf-load-order sort again.")
    log.info(SECTION, f"{len(res['moves'])} move(s), {len(res['rejected'])} rule(s) not used, {res['proposed']} "
                      f"proposal(s), score {res['score_current']} -> {res['score_sorted']}")
    return {"status": "done", "moves": len(res["moves"]), "rejected": len(res["rejected"]),
            "proposed": res["proposed"], "loop": res["loop"] is not None}


def _write_proposed(folder, rules):
    from .rules_cmd import write_proposed
    return write_proposed(folder, rules)
