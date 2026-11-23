"""Vortex mod rules (section "rules"): do the rules between mods match the loose files they really ship?

Vortex's mod rules ("load after", "load before") and a mod's file overrides decide only which mod's copy of a loose
file is deployed into Data; archives follow the plugin order. So this check works on the loose-file check's pairs
(two enabled mods shipping the same loose path) and needs:

  - the modlist export of the Vortex Modlist Backup extension (--vortex-profile, or --vortex-modlist next to
    --vortex-loadorder), which holds each mod's name, archive and, from extension 0.6.2 on, its rules and
    fileOverrides;
  - the loose-file check's results (the mods' staging folders).

Otherwise the section is skipped and says why.

An export with no rules (the extension before 0.6.2, or no mod has any): a note for each pair of mods shipping
different copies, naming the mod whose copies Vortex deployed (from its deployment record in Data, else from the
copy Data holds), and one note saying what can't be seen without the rules.

An export with rules: a "before"/"after" rule between two mods whose shared loose files are all identical, or who
share none, only orders identical copies (a note); a pair with different copies, no "before"/"after" rule either
way and no fileOverrides entry for those paths is a real clash with no rule (a caution naming the deployed copy).
A rule names the other mod by reference.id (its vortexId), else idHint, else fileExpression (a pattern) or
logicalFileName matched against the other mod's archive name and name. A rule that matches no enabled mod is a
note. "requires", "conflicts", "recommends" and "provides" aren't ordering rules and are left out. Nothing here
writes rules or changes Vortex.

The result is report.json's top-level "rules": {has_rules, pairs: [{mods, paths, same, winner, deployed, rule}],
unmatched, findings: {problem, caution, note}}.
"""
import fnmatch
import os

from . import log, vortex_deploy
from .model import Finding

SECTION = "rules"
SOURCE_WORDS = {"record": "Vortex's deployment record", "data": "the copies in Data"}
ORDER_TYPES = ("before", "after")
LIST = 10  # names shown in one grouped note

NO_RULES = ("This export has no mod rules (Vortex Modlist Backup 0.6.2 or later exports them), so rules that only "
            "order identical copies and clashes with no rule can't be told apart.")


def _key(s):
    return str(s).strip().lower() if isinstance(s, (str, int)) and not isinstance(s, bool) else ""


def _stem(archive):
    a = os.path.basename(str(archive or ""))
    low = a.lower()
    for ext in (".zip", ".7z", ".rar", ".fomod"):
        if low.endswith(ext):
            return a[:-len(ext)]
    return a


def _entries(modlist):
    """{mod id lower: modlist entry} for the enabled mods, by vortexId and by installationPath."""
    out = {}
    for m in modlist or []:
        if not isinstance(m, dict) or m.get("enabled") is not True:
            continue
        for k in (m.get("vortexId"), m.get("installationPath")):
            if _key(k):
                out.setdefault(_key(k), m)
    return out


def _mid(entry):
    return str(entry.get("vortexId") or entry.get("installationPath") or entry.get("name")).strip()


def has_rules(modlist):
    return any(isinstance(m, dict) and ("rules" in m or "fileOverrides" in m) for m in modlist or [])


def _resolve(ref, entries):
    """The enabled mod ids a rule's reference names (lower case), possibly none."""
    if not isinstance(ref, dict):
        return set()
    for k in ("id", "idHint"):
        if _key(ref.get(k)) in entries:
            return {_key(_mid(entries[_key(ref[k])]))}
    found = set()
    expr, logical = _key(ref.get("fileExpression")), _key(ref.get("logicalFileName"))
    for e in {id(e): e for e in entries.values()}.values():
        names = {_key(_stem(e.get("archive"))), _key(e.get("archive")), _key(e.get("vortexId"))} - {""}
        if expr and any(fnmatch.fnmatchcase(n, expr) for n in names):
            found.add(_key(_mid(e)))
        elif logical and logical in names | {_key(e.get("name"))}:
            found.add(_key(_mid(e)))
    return found


def ordering(modlist):
    """({frozenset(a, b): [{mod, type, other, wins}]}, [unmatched {mod, type, reference}]) for the before/after
    rules of the enabled mods. Ids are as the modlist writes them; `wins` is the mod whose copies the rule deploys."""
    entries = _entries(modlist)
    by_low = {_key(_mid(e)): _mid(e) for e in entries.values()}
    edges, unmatched = {}, []
    for e in {id(e): e for e in entries.values()}.values():
        me = _mid(e)
        rules = e.get("rules") if isinstance(e.get("rules"), list) else []
        for r in rules:
            if not isinstance(r, dict) or r.get("type") not in ORDER_TYPES:
                continue
            others = _resolve(r.get("reference"), entries) - {_key(me)}
            if not others:
                unmatched.append({"mod": me, "type": r["type"], "reference": r.get("reference")})
                continue
            for o in sorted(others):
                other = by_low[o]
                wins = me if r["type"] == "after" else other
                edges.setdefault(frozenset((_key(me), o)), []).append(
                    {"mod": me, "type": r["type"], "other": other, "wins": wins})
    return edges, unmatched


def _overrides(modlist):
    """{mod id lower: {path lower}} of each enabled mod's fileOverrides."""
    out = {}
    for low, e in _entries(modlist).items():
        fo = e.get("fileOverrides")
        if isinstance(fo, list):
            out.setdefault(low, set()).update(vortex_deploy.norm(p) for p in fo if isinstance(p, str) and p)
    return out


def _deployed(pr, differ_paths, dep, deployed_profile):
    """(winner or None, {mod: count}, source) for a pair: whose copies of the differing paths Vortex deployed. The
    deployment record decides (source "record"); without one, the copy Data holds (the loose check's in_data; source
    "data"). source is None when neither says."""
    if deployed_profile is False:
        return None, {}, None
    a, b = pr["mods"]
    counts, source = {}, "record"
    if dep is not None:
        for p in differ_paths:
            o = _key(vortex_deploy.owner(dep, p))
            for m in (a, b):
                if o and o == _key(m):
                    counts[m] = counts.get(m, 0) + 1
    if not counts:
        counts, source = {m: n for m, n in (pr.get("in_data") or {}).items() if n}, "data"
    if not counts:
        source = None
    if len(counts) == 1:
        return next(iter(counts)), counts, source
    return None, counts, source


def _deployed_words(winner, counts):
    if winner:
        return f"Vortex deployed {winner}'s copies"
    if counts:
        return "Vortex deployed some of each: " + ", ".join(f"{m} {n}" for m, n in sorted(counts.items()))
    return None


def analyse(lo, modlist, loose_result, deployed_profile=None):
    dep = getattr(lo, "_vortex_deployment", None)
    with_rules = has_rules(modlist)
    edges, unmatched = ordering(modlist) if with_rules else ({}, [])
    overrides = _overrides(modlist) if with_rules else {}
    pairs, seen = [], set()
    for pr in loose_result.get("pairs") or []:
        a, b = pr["mods"]
        k = frozenset((_key(a), _key(b)))
        seen.add(k)
        diff = [vortex_deploy.norm(p) for p in pr.get("not_same") or []]  # this pair's own two copies differ or can't be read
        winner, counts, source = _deployed(pr, diff, dep, deployed_profile)
        rule = edges.get(k)
        covered = bool(diff) and all(p in overrides.get(_key(a), set()) | overrides.get(_key(b), set())
                                     for p in diff)
        pairs.append({"mods": [a, b], "paths": pr.get("files", 0), "same": pr.get("advice") == "same",
                      "differ": pr.get("differ", 0), "unknown": pr.get("unknown", 0), "winner": winner,
                      "deployed": counts, "deployed_from": source, "rule": rule,
                      "overrides": covered, "sample": pr.get("sample") or []})
    folders = {_key(m.id or m.name) for m in lo.mods or [] if m.enabled and m.path and os.path.isdir(m.path)}
    no_shared = []
    for k, rs in sorted(edges.items(), key=lambda kv: sorted(kv[0])):
        if k not in seen and k <= folders:
            no_shared.append({"mods": sorted({r["mod"] for r in rs} | {r["other"] for r in rs}, key=str.lower),
                              "rule": rs})
    log.info(SECTION, f"{'rules found' if with_rules else 'no rules in the export'}: {len(pairs)} loose pair(s), "
                      f"{len(edges)} ordered pair(s), {len(no_shared)} with no shared loose file, "
                      f"{len(unmatched)} unmatched rule(s)")
    return {"has_rules": with_rules, "pairs": pairs, "no_shared": no_shared, "unmatched": unmatched}


def _differ_words(p):
    if p["differ"]:
        return f"ship {p['differ']} loose file(s) with different copies"
    return f"share {p.get('unknown') or p['paths']} loose file(s) that couldn't be compared"


def _rule_words(rs):
    return "; ".join(f"{r['mod']} loads {r['type']} {r['other']}" for r in rs)


def _findings(report, r, deployed_profile):
    out = []
    not_here = (" Data holds another profile, so which copies are deployed isn't known."
                if deployed_profile is False else "")
    if not r["has_rules"]:
        for p in r["pairs"]:
            if p["same"]:
                continue
            a, b = p["mods"]
            dw = _deployed_words(p["winner"], p["deployed"])
            out.append(Finding("note", SECTION,
                               f"Mods {a} and {b} {_differ_words(p)}. "
                               + (f"{dw} ({SOURCE_WORDS.get(p.get('deployed_from'), 'as far as can be told')})."
                                  if dw else
                                  "Which copies Vortex deployed isn't known." if not not_here else "")
                               + not_here, None, [], dict(p)))
        out.append(Finding("note", SECTION, NO_RULES,
                           "Install Vortex Modlist Backup 0.6.2 or later, let it export again, and run again.", [],
                           {"has_rules": False}))
        return out
    quiet = [p for p in r["pairs"] if p["same"] and p["rule"]]
    for p in r["pairs"]:
        if p["same"] or p["rule"] or p["overrides"]:
            continue
        a, b = p["mods"]
        dw = _deployed_words(p["winner"], p["deployed"])
        picked = f"Vortex picked {p['winner']}'s copy" if p["winner"] else (dw or "which copy Vortex picked isn't known")
        out.append(Finding("caution", SECTION,
                           f"{'Real' if p['differ'] else 'Possible'} clash with no rule: mods {a} and {b} "
                           f"{_differ_words(p)}, "
                           f"and neither has a load-before or load-after rule for the other; "
                           f"{picked}.{not_here}",
                           f"On Vortex's Mods page, open the conflict icon of {a} or {b} and set which one loads "
                           "after the other (its copies win), then deploy.", [], dict(p)))
    if quiet:
        names = [f"{p['mods'][0]} / {p['mods'][1]} ({_rule_words(p['rule'])})" for p in quiet]
        out.append(Finding("note", SECTION,
                           f"{len(quiet)} rule(s) only order identical copies, so they change nothing: "
                           + "; ".join(names[:LIST]) + (f"; and {len(names) - LIST} more" if len(names) > LIST
                                                        else "") + ".",
                           None, [], {"pairs": [{"mods": p["mods"], "rule": p["rule"]} for p in quiet]}))
    if r["no_shared"]:
        names = [f"{' / '.join(p['mods'])} ({_rule_words(p['rule'])})" for p in r["no_shared"]]
        out.append(Finding("note", SECTION,
                           f"{len(names)} rule(s) order two mods that share no loose file, so they change nothing "
                           "for loose files: " + "; ".join(names[:LIST])
                           + (f"; and {len(names) - LIST} more" if len(names) > LIST else "") + ".",
                           None, [], {"pairs": r["no_shared"]}))
    if r["unmatched"]:
        names = [f"{u['mod']} ({u['type']})" for u in r["unmatched"]]
        out.append(Finding("note", SECTION,
                           f"{len(names)} load-before or load-after rule(s) name a mod that isn't enabled in this "
                           "profile (or can't be matched to one): " + "; ".join(names[:LIST])
                           + (f"; and {len(names) - LIST} more" if len(names) > LIST else "") + ".",
                           None, [], {"rules": r["unmatched"]}))
    return out


def run(ctx):
    """Section "rules". Sets ctx.report.rules and ctx.results["rules"]."""
    lo = ctx.load_order
    if lo.source != "vortex":
        return {"status": "skipped", "why": "Vortex's mod rules apply only to a Vortex load order"}
    modlist = getattr(lo, "_vortex_modlist", None)
    if modlist is None:
        return {"status": "skipped", "why": "it needs the Modlist Backup extension's modlist export (--vortex-profile, "
                "or --vortex-modlist next to --vortex-loadorder)"}
    loose_result = ctx.results.get("loose")
    if loose_result is None:
        why = (ctx.report.sections.get("loose") or {}).get("why")
        return {"status": "skipped", "why": "the loose-file check didn't run" + (f" ({why})" if why else "")}
    vp = getattr(ctx.report, "vortex_profile", None) or {}
    deployed_profile = vp.get("deployed")
    r = analyse(lo, modlist, loose_result, deployed_profile)
    found = _findings(ctx.report, r, deployed_profile)
    ctx.report.findings += found
    counts = {lv: sum(1 for f in found if f.level == lv) for lv in ("problem", "caution", "note")}
    ctx.results[SECTION] = r
    ctx.report.rules = {"has_rules": r["has_rules"],
                        "pairs": [{k: p[k] for k in ("mods", "paths", "same", "winner", "deployed", "rule")}
                                  for p in r["pairs"]],
                        "no_shared": r["no_shared"], "unmatched": r["unmatched"], "findings": counts}
    return {"status": "done", "has_rules": r["has_rules"], "pairs": len(r["pairs"]),
            "differ": sum(1 for p in r["pairs"] if not p["same"])}
