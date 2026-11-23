"""Record conflicts, section "records": what sf-mod-check's `conflict` command finds, as ratings, pairs and findings.

run(ctx) gets the data one of two ways:
  --conflicts FILE   read a conflicts.json from an earlier `sf-mod-check conflict` run (any version that writes the
                     keys below; nothing is run)
  otherwise          find sf-mod-check (sfmodcheck.find), check it is 0.8.0 or later, write the load order as a
                     Plugins.txt in ctx.work and run `conflict` into ctx.work\\conflict
With no sf-mod-check the section is skipped and says where to get it.

What conflicts.json holds (sf-mod-check src\\ConflictReport.cs, 0.8.0) and what that means here:
  lost       one entry per (record, loser, winner): a plugin's change to a record that the game throws away because a
             later plugin, which doesn't have it as a master (directly or through its masters), supplies the field.
             Each field carries WinnerChangedToo and Differences / LoserChanges ({Text, Minor}). The field labels are
             the ones sf-mod-check's report shows, worked out the same way (ConflictImpact.IsMinor):
               No effect    the loser's changes, or the differences, are all Minor (list order, letter case,
                            undocumented fields)
               Choice       the winner changed that field on purpose too
               Forwardable  the winner only carries an older value, so a patch can copy the lost value forward
  itms, typeChanges, loadOrderProblems, Errors / UnreadableFields (records not compared), missingAssets,
  archiveLimitWarnings, loadOrder (plugins sf-mod-check loaded, with Official and Masters).

What it can't tell: the file lists only losses. A plugin's own edits that nobody undoes, and edits two mods make
the same way, aren't listed, so a plugin with no entry is "neutral" in the sense "nothing of it is lost and it
undoes nothing", not proven to add only new records (that would take every override, which the file doesn't list).
It also has no cost of the other order (sf-mod-check works that out, ReorderCost, but doesn't write it), so a
pair's flip cost below is only the part the Choice fields show.

Ratings per enabled mod plugin (labels in RATING_LABEL):
  neutral       no lost-change entry names it and no ITM or type change of its is listed
  neutral-here  it is in entries, but none that the order can change (built on it, same mod, same edit, it undoes
                an official change, which no order fixes), or it has ITMs / type changes
  matters       it is the loser or winner of a "patch candidate" or "pick a winner" pair between two mods
  unchecked     sf-mod-check didn't load it (unreadable, a master missing, not in its load order)

Pair advice (key, label): built "no action: built on it", samemod "no action: same mod", same "no action: same
edit", merge "patch candidate", conflict "pick a winner", navi "no action: navmesh info map", blueprint "no action:
BlueprintShips file wins" (a merge or conflict pair whose winner is an official BlueprintShips file: nothing loads
after one, so neither an order nor a patch helps; "blueprint": True on the pair). "built" can't come from
sf-mod-check itself (it leaves out pairs where the winner has the loser as a master); it is kept for files made another
way. The navmesh info map (NAVI) never counts as a lost change: each plugin's copy lists only its own navmeshes, so its
entries (outcome "navi") go in a pair's "navi" list, not its "records", and get a note of their own; a pair with only
NAVI entries gets the advice "navi", and NAVI under an official loser isn't counted in official_undone.

ctx.results["records"] (for recommend.py and the report):
  {"source": "run"|"file", "file": conflicts.json path, "program": Program.to_dict() or None,
   "exit": int|None, "result_line": str|None, "generated": str,
   "load_order": [names sf-mod-check loaded, official included, in its order],
   "plugins": {lower name: {"name", "rating", "label", "why", "pairs": [pair index], "lost_records": int,
               "won_records": int, "undoes_official": int, "itms": int, "type_changes": int}},
   "lost": [{"fk", "type", "edid", "loser", "winner", "loser_official", "winner_official", "outcome",
             "fields": [{"field", "label", "weight", "winner_changed_too", "loser_value", "winner_value",
                         "differences": [str]}]}],
   "pairs": [{"loser", "winner", "winner_official", "advice", "label", "mod", "records": [index into lost],
              "navi": [index into lost: the pair's navmesh info map entries, left out of everything below],
              "n": {"same", "merge", "conflict"}, "fields": {"Forwardable": n, "Choice": n, "No effect": n},
              "gain_if_flipped": weighted sum of the loser's lost Forwardable + Choice fields,
              "known_cost_if_flipped": weighted sum of the Choice fields (the winner's own changes there),
              "cost_complete": False}],
   "official_undone": [index into lost],   loser official, winner a mod, not the navmesh info map
   "itms": [{"plugin", "fk", "type", "edid"}],
   "type_changes": [{"fk", "edid", "plugin", "plugin_type", "first", "first_type", "text"}],
   "problems": [{"text", "plugin", "kind", "seen_by"}],  kind: missing-file / master / master-order / unreadable /
                                                         other; seen_by: "inputs" / "order" / "records"
   "not_compared": {"errors": [str], "unreadable_fields": {str: n}},
   "assets": {"checked", "missing": [...], "archive_counts", "archive_limit_warnings"},
   "differs": None or {"missing": [...], "extra": [...], "order": bool}   vs the load order being checked
   "heavy": None or {lower name: sfmodcheck.run_check() dict}}
Field weights are recommend.py's: 0 bookkeeping or No effect, 1 cosmetic (lighting, music, weather, names...),
3 functional (everything else).
"""
import json
import os

from . import inputs, log, sfmodcheck
from .model import Finding

SECTION = "records"
NO_PROGRAM = ("sf-mod-check wasn't found. Get it from https://github.com/taosecurity/sf-mod-check/releases (called "
              "sfmodcheck before 0.8.0; this tool needs 0.8.0 or newer) or pass --sf-mod-check <exe>.")
RATING_LABEL = {"neutral": "neutral", "neutral-here": "neutral here", "matters": "order matters",
                "unchecked": "not checked"}
ADVICE_LABEL = {"built": "no action: built on it", "samemod": "no action: same mod", "same": "no action: same edit",
                "merge": "patch candidate", "conflict": "pick a winner", "navi": "no action: navmesh info map",
                "blueprint": "no action: BlueprintShips file wins"}
ACTION = {"merge", "conflict"}
FORWARDABLE, CHOICE, NO_EFFECT = "Forwardable", "Choice", "No effect"
# field weights: bookkeeping 0, cosmetic 1, everything else 3
NOISE = {"NumTraversalFluffBytes", "PersistentTimestamp", "Timestamp"}
COSMETIC = {"Lighting", "LightingTemplate", "ImageSpace", "AcousticSpace", "Music", "AmbienceSet", "EnvironmentMap",
            "Climate", "LodWater", "LodWaterHeight", "Water", "DistantLodMultiplier", "CellWaterHeightLocations",
            "Name", "MapData", "WorldMapOffset", "FNAM", "GNAM", "HNAM", "SkyLighting", "WaterHeight",
            "WaterEnvironmentMap", "LandDefaults", "Description", "Components", "XILS", "WaterHeights",
            "ActorFadeMult"}
# record types whose edits are lists of entries: two mods adding entries can always be merged
LIST_TYPES = {"LeveledItem", "LeveledNpc", "LeveledPackIn", "LeveledSpaceshipBaseList", "LeveledBaseForm",
              "FormList", "Container", "Outfit"}
# the navmesh info map (NAVI): each plugin's copy lists only its own navmeshes, so it isn't a record one plugin wins
# whole; it is never patched and isn't a real loss (sf-mod-check ConflictImpact.IsNavi, NaviBlueprintNote)
NAVI = "NavigationMeshInfoMap"
DELETED_RISKY = {"REFR", "ACHR", "NAVM"}
REQUIRED_KEYS = ("loadOrder", "lost")


# ---------- reading ----------
def load(path):
    """(dict, None) or (None, error text naming the file and the fix)."""
    fix = "Fix: give --conflicts the conflicts.json that sf-mod-check conflict wrote in its --out folder."
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except OSError as e:
        return None, f"can't read {path}: {e.strerror or e}. {fix}"
    except ValueError as e:
        return None, f"{path} isn't valid JSON ({e}). {fix}"
    if not isinstance(d, dict) or any(not isinstance(d.get(k), list) for k in REQUIRED_KEYS):
        return None, f"{path} doesn't look like sf-mod-check's conflicts.json (it needs a loadOrder list and a lost list). {fix}"
    return d, None


def label(f):
    """sf-mod-check's label for one lost field (ConflictImpact.IsMinor, then WinnerChangedToo)."""
    lc = [x for x in (f.get("LoserChanges") or []) if isinstance(x, dict)]  # an item that isn't an object says nothing
    df = [x for x in (f.get("Differences") or []) if isinstance(x, dict)]
    if (lc and all(x.get("Minor") for x in lc)) or (df and all(x.get("Minor") for x in df)):
        return NO_EFFECT
    return CHOICE if f.get("WinnerChangedToo") else FORWARDABLE


def weight(field, lab=None):
    if lab == NO_EFFECT or field in NOISE:
        return 0
    return 1 if field in COSMETIC else 3


def is_navi(rtype):
    """The navmesh info map (record type NavigationMeshInfoMap, NAVI)."""
    return (rtype or "").lower() == NAVI.lower()


def _outcome(rtype, fields):
    if is_navi(rtype):
        return "navi"
    real = [f for f in fields if f["label"] != NO_EFFECT]
    if not real:
        return "same"
    if rtype in LIST_TYPES or all(f["label"] == FORWARDABLE for f in real):
        return "merge"
    return "conflict"


def _text(v):
    """A plugin name from conflicts.json, trimmed the same way as the loadOrder FileName (non-text gives "")."""
    return v.strip() if isinstance(v, str) else ""


def _load_entries(cj):
    """conflicts.json's loadOrder entries that are objects with a usable file name (a string with text in it), each
    with FileName trimmed. Anything else (not an object, no or empty or blank or non-text FileName) names no plugin
    and is skipped, so it can't skew the load order shown, the ratings or the --conflicts comparison."""
    out, seen = [], {}
    for e in (cj.get("loadOrder") or []):
        if not isinstance(e, dict) or not isinstance(e.get("FileName"), str) or not e["FileName"].strip():
            continue
        e = dict(e, FileName=e["FileName"].strip())
        first = seen.get(e["FileName"].lower())
        if first is None:
            seen[e["FileName"].lower()] = e
            out.append(e)
            continue
        # a plugin listed twice (any case) is one plugin: it keeps its first place, and the entries' masters and
        # Official flag are merged into it
        ms = [m for x in (first, e) for m in (x.get("Masters") if isinstance(x.get("Masters"), list) else [])]
        first["Masters"] = ms
        first["Official"] = bool(first.get("Official") or e.get("Official"))
    return out


def _closure(load_order):
    direct = {}
    for e in load_order:
        ms = e.get("Masters")
        ms = ms if isinstance(ms, list) else []  # a text or number Masters value names no masters
        # master names are trimmed and blank ones dropped; entries naming the same plugin (any case) share one set
        direct.setdefault(e["FileName"].lower(), set()).update(_text(m).lower() for m in ms if _text(m))
    out = {}

    def walk(n, seen):
        for m in direct.get(n, ()):
            if m not in seen:
                seen.add(m)
                walk(m, seen)
        return seen
    for n in direct:
        out[n] = walk(n, set())
    return out


def _problem(text):
    """(plugin, kind, seen_by) for one loadOrderProblems line (texts from sf-mod-check ConflictLoadOrder.cs)."""
    plugin = text.split(":", 1)[0].strip() if ":" in text else None
    t = text.lower()
    if "listed but not found" in t:
        return plugin, "missing-file", "inputs"
    if "listed before its master" in t:
        return plugin, "master-order", "order"
    if " master " in t and ("missing or inactive" in t or "unloadable" in t):
        return plugin, "master", "order"
    if "cannot be read" in t or "cannot be opened" in t:
        return plugin, "unreadable", "records"
    return plugin, "other", "records"


def analyse(cj, lo):
    """Turn conflicts.json (dict) into ctx.results["records"] (see the module text). lo: the LoadOrder checked."""
    entries = _load_entries(cj)
    order = [e["FileName"] for e in entries]
    official = {e["FileName"].lower() for e in entries if e.get("Official")}
    need = _closure(entries)
    modof = {}
    for m in (getattr(lo, "mods", None) or []):
        for p in (m.plugins or []):
            modof[p.lower()] = m.name

    lost = []
    raw = cj.get("lost")
    # a non-object item names no record; a lost value that isn't a list holds none (load() already refuses it)
    lost_in = [e for e in (raw if isinstance(raw, list) else []) if isinstance(e, dict)]
    for e in lost_in:
        fields = []
        for f in [f for f in (e.get("Fields") or []) if isinstance(f, dict)]:
            lab = label(f)
            name = _text(f.get("Field")) or "?"
            fields.append({"field": name, "label": lab, "weight": weight(name, lab),
                           "winner_changed_too": bool(f.get("WinnerChangedToo")),
                           "loser_value": f.get("LoserValue"), "winner_value": f.get("WinnerValue"),
                           "differences": [_text(x.get("Text")) for x in (f.get("Differences") or [])
                                           if isinstance(x, dict)]})  # non-object items are skipped, as in label()
        loser, winner = _text(e.get("Loser")), _text(e.get("Winner"))
        lost.append({"fk": _text(e.get("formKey")), "type": _text(e.get("RecordType")), "edid": e.get("EditorID"),
                     "loser": loser, "winner": winner,
                     "loser_official": bool(e.get("LoserOfficial")) or loser.lower() in official,
                     "winner_official": winner.lower() in official,
                     "outcome": _outcome(_text(e.get("RecordType")), fields), "fields": fields})

    # sf-mod-check's WinnerOfficial on a lost entry (like the Official flag in loadOrder) makes the pair a BlueprintShips
    # pair, per winner name so any entry counts. That changes its wording and keeps it out of "order matters" and the
    # move candidates. winner_official, the ratings and official_undone keep the load order's own flags.
    marked = {_text(e.get("Winner")).lower() for e in lost_in if e.get("WinnerOfficial")}
    pairs, by_pair = [], {}
    for i, x in enumerate(lost):
        if x["loser_official"]:
            continue
        key = (x["loser"].lower(), x["winner"].lower())
        if key not in by_pair:
            by_pair[key] = len(pairs)
            pairs.append({"loser": x["loser"], "winner": x["winner"], "winner_official": x["winner_official"],
                          "blueprint": is_blueprint(x["winner"], x["winner_official"]
                                                    or x["winner"].lower() in marked),
                          "records": [], "navi": [], "mod": None})
        # the navmesh info map stays out of the pair's records, counts, gain and advice (it gets a note of its own)
        pairs[by_pair[key]]["navi" if x["outcome"] == "navi" else "records"].append(i)
    for p in pairs:
        recs = [lost[i] for i in p["records"]]
        outs = [r["outcome"] for r in recs]
        p["n"] = {k: outs.count(k) for k in ("same", "merge", "conflict")}
        allf = [f for r in recs for f in r["fields"]]
        p["fields"] = {k: sum(1 for f in allf if f["label"] == k) for k in (FORWARDABLE, CHOICE, NO_EFFECT)}
        p["gain_if_flipped"] = sum(f["weight"] for f in allf if f["label"] != NO_EFFECT)
        p["known_cost_if_flipped"] = sum(f["weight"] for f in allf if f["label"] == CHOICE)
        p["cost_complete"] = False
        lw, ll = p["winner"].lower(), p["loser"].lower()
        if ll in need.get(lw, ()):
            p["advice"] = "built"
        elif modof.get(ll) and modof.get(ll) == modof.get(lw):
            p["advice"], p["mod"] = "samemod", modof[ll]
        elif not recs:
            p["advice"] = "navi"
        elif p["blueprint"] and ("conflict" in outs or "merge" in outs):
            p["advice"] = "blueprint"
        elif "conflict" in outs:
            p["advice"] = "conflict"
        elif "merge" in outs:
            p["advice"] = "merge"
        else:
            p["advice"] = "same"
        p["label"] = ADVICE_LABEL[p["advice"]]

    # a mod's copy of the navmesh info map over an official one undoes nothing: each copy lists its own navmeshes
    official_undone = [i for i, x in enumerate(lost) if x["loser_official"] and not x["winner_official"]
                       and x["outcome"] != "navi"]
    for x in lost:
        if x["loser_official"] and not x["winner_official"] and x["outcome"] == "navi":
            log.info(SECTION, f"{x['winner']} over {x['loser']} on the navmesh info map {x['fk']}: not counted as "
                              "undoing an official change (each plugin's copy lists only its own navmeshes)")
    itms = [{"plugin": (x.get("Plugin") or ""), "fk": (x.get("formKey") or ""), "type": (x.get("RecordType") or ""),
             "edid": x.get("EditorID")} for x in cj.get("itms", []) or []]
    tcs = [{"fk": (x.get("formKey") or ""), "edid": x.get("EditorID"), "plugin": (x.get("Plugin") or ""),
            "plugin_type": x.get("PluginType"), "first": x.get("First"), "first_type": x.get("FirstType"),
            "text": x.get("finding")} for x in cj.get("typeChanges", []) or []]
    problems = []
    for t in cj.get("loadOrderProblems", []) or []:
        plugin, kind, seen_by = _problem(str(t))
        problems.append({"text": str(t), "plugin": plugin, "kind": kind, "seen_by": seen_by})

    res = {"load_order": order, "generated": cj.get("generated"), "lost": lost, "pairs": pairs,
           "official_undone": official_undone, "itms": itms, "type_changes": tcs, "problems": problems,
           "not_compared": {"errors": list(cj.get("Errors") or []),
                            "unreadable_fields": dict(cj.get("UnreadableFields") or {})},
           "assets": {"checked": cj.get("assetsChecked"), "missing": list(cj.get("missingAssets") or []),
                      "archive_counts": cj.get("archiveCounts"),
                      "archive_limit_warnings": list(cj.get("archiveLimitWarnings") or [])},
           "heavy": None}
    res["plugins"] = _ratings(lo, res, {n.lower() for n in order})
    res["differs"] = _differs(lo, cj)
    return res


def is_blueprint(name, official=False):
    """An official BlueprintShips-*.esm. The game loads these after every other plugin, so no plugin can load after
    one and no load order or patch brings back what it overrides (sf-mod-check 0.12.0, BlueprintAdvice). official:
    conflicts.json marks the file Official (sf-mod-check also counts names this tool doesn't, such as sfta01's)."""
    return bool(inputs.BLUEPRINT_RE.match(name or "")) and (bool(official) or inputs.is_official(name))


def acts_on(p):
    """A pair a load order or a patch can change: "patch candidate" or "pick a winner" (a pair an official
    BlueprintShips file wins has the advice "blueprint" instead)."""
    return p.get("advice") in ACTION and not (p.get("blueprint") or is_blueprint(p["winner"], p.get("winner_official")))


def _mod_plugins(lo):
    return [p for p in lo.plugins if p.enabled and not p.official and not p.dropped]


def _ratings(lo, res, loaded):
    skipped = {}
    for pr in res["problems"]:
        # sf-mod-check skips a plugin it can't read, one with a master missing, and a listed file that isn't there
        if pr["plugin"] and pr["kind"] in ("unreadable", "master", "missing-file"):
            skipped.setdefault(pr["plugin"].lower(), pr["text"])
    out = {}
    for p in _mod_plugins(lo):
        n = p.name.lower()
        # a pair with only navmesh info map entries (whatever its advice) isn't a lost change, so it doesn't make a
        # plugin "neutral here"
        mine = [i for i, x in enumerate(res["pairs"]) if n in (x["loser"].lower(), x["winner"].lower())
                and x["records"]]
        lost_r = sum(len(res["pairs"][i]["records"]) for i in mine if res["pairs"][i]["loser"].lower() == n)
        won_r = sum(len(res["pairs"][i]["records"]) for i in mine if res["pairs"][i]["winner"].lower() == n)
        undo = sum(1 for i in res["official_undone"] if res["lost"][i]["winner"].lower() == n)
        itms = sum(1 for x in res["itms"] if x["plugin"].lower() == n)
        tcs = sum(1 for x in res["type_changes"] if x["plugin"].lower() == n)
        movable = [i for i in mine if acts_on(res["pairs"][i]) and not res["pairs"][i]["winner_official"]]
        if n in skipped or n not in loaded:
            rating = "unchecked"
            why = skipped.get(n) or "sf-mod-check didn't load it (it isn't in conflicts.json's load order)"
        elif movable:
            rating = "matters"
            kinds = sorted({res["pairs"][i]["label"] for i in movable})
            why = f"{len(movable)} overlap(s) with another mod: {', '.join(kinds)}"
        elif mine or undo or itms or tcs:
            rating = "neutral-here"
            bits = []
            if mine:
                bits.append(f"{len(mine)} overlap(s) the order doesn't change ("
                            + ", ".join(sorted({res['pairs'][i]['label'] for i in mine})) + ")")
            if undo:
                bits.append(f"undoes {undo} official change(s) (no order fixes that)")
            if itms:
                bits.append(f"{itms} ITM record(s)")
            if tcs:
                bits.append(f"{tcs} record type change(s)")
            why = "; ".join(bits)
        else:
            rating = "neutral"
            why = ("no change of it is lost and it undoes no one's change (conflicts.json lists only losses, so this "
                   "doesn't say whether it edits existing records)")
        out[n] = {"name": p.name, "rating": rating, "label": RATING_LABEL[rating], "why": why, "pairs": mine,
                  "lost_records": lost_r, "won_records": won_r, "undoes_official": undo, "itms": itms,
                  "type_changes": tcs}
    return out


def _differs(lo, cj):
    """Does the --conflicts file's load order differ from ours? Plugins conflicts.json lists as Official, and names
    sf-mod-check counts official (sfta01 to sfta06 and the like), aren't mods, so they aren't compared."""
    entries = [(e, e["FileName"].lower()) for e in _load_entries(cj)]
    flagged = {n for e, n in entries if e.get("Official")}
    left = []
    seen = set()
    ours = []
    for p in _mod_plugins(lo):
        n = p.name.lower()
        if n in flagged or inputs.modcheck_official(n):
            if n not in seen:
                seen.add(n)
                left.append(p.name)
        else:
            ours.append(n)
    theirs = []
    for e, n in entries:
        if e.get("Official") or inputs.modcheck_official(n):
            if n not in seen:
                seen.add(n)
                left.append(n)
        else:
            theirs.append(n)
    if left:
        log.info(SECTION, "left out of the load order comparison (sf-mod-check counts them official, so they are not "
                          "in its mod list): " + ", ".join(left))
    if ours == theirs:
        return None
    so, st = set(ours), set(theirs)
    common_o = [n for n in ours if n in st]
    common_t = [n for n in theirs if n in so]
    return {"missing": [n for n in ours if n not in st], "extra": [n for n in theirs if n not in so],
            "order": common_o != common_t}


# ---------- findings ----------
def _names(fields, n=6):
    """Field names in plain words with the raw name after it, e.g. "script settings (VirtualMachineAdapter)"."""
    from .recommend import plain_field  # imported here: recommend imports this module
    out = []
    for f in fields:
        raw = f["field"]
        word = plain_field(raw)
        label = raw if word.replace(" ", "").lower() == raw.lower() else f"{word} ({raw})"
        if label not in out:
            out.append(label)
    return ", ".join(out[:n]) + ("..." if len(out) > n else "")


def navi_note(p, lost):
    """The note for a pair's navmesh info map entries (sf-mod-check ConflictImpact.NaviBlueprintNote, worded for any
    winner): not a patch candidate, not a pick-a-winner choice, not a real loss. A note with no fix, so it sorts after
    the notes that have one."""
    L, W = p["loser"], p["winner"]
    n = len(p["navi"])
    where = ("loads after every mod, so its copy of the navmesh info map loads last" if p.get("blueprint") else
             f"loads after {L}, so its copy of the navmesh info map loads later")
    text = (f"{W} and {L} both change the navmesh info map ({n} record(s)). {W} {where}. Each plugin's copy lists "
            "only its own navmeshes, so this isn't a record one plugin wins whole: it isn't patched, and don't copy "
            "it by hand either (the Creation Kit rebuilds it when a navmesh is finalized).")
    log.info(SECTION, f"{L} vs {W}: {n} navmesh info map record(s) get a note, not patch or pick-a-winner advice, and "
                      "aren't counted as lost changes")
    return Finding("note", SECTION, text, plugins=[L, W],
                   data={"advice": "navi", "records": [lost[i]["fk"] for i in p["navi"]][:50]})


def add_findings(ctx, res, source):
    add = ctx.report.add
    lost = res["lost"]
    for p in res["pairs"]:
        if p.get("navi") and p["advice"] not in ("built", "samemod"):
            add(navi_note(p, lost))
        if p["advice"] not in ACTION and p["advice"] != "blueprint":
            continue
        recs = [lost[i] for i in p["records"]]
        real = [f for r in recs for f in r["fields"] if f["label"] != NO_EFFECT]
        level = "caution" if any(f["weight"] >= 3 for f in real) else "note"
        L, W = p["loser"], p["winner"]
        head = (f"{W} wins over {L} on {len(recs)} record(s), so changes {L} made are lost there "
                f"(fields: {_names(real)}).")
        if p["advice"] == "merge":
            text = head + f" {W} didn't change those fields itself, so a patch can keep both (patch candidate)."
            fix = (f"Make a small patch that loads after both and carries {L}'s values forward (sf-mod-check's "
                   f"report.html has a prompt for it), or load {L} after {W} if you want its changes more.")
        else:
            text = head + f" Both changed the same fields, so one has to win (pick a winner)."
            fix = (f"Decide which version you want: the plugin that loads later wins. Keep {W} after {L}, or move "
                   f"{L} below {W}; a patch can combine the two.")
        if p["advice"] == "blueprint":
            # nothing loads after a BlueprintShips file, so neither "patch candidate" nor "pick a winner" applies
            n = len(recs)
            ships = all(r["type"] == "GenericBaseForm" for r in recs)
            what = ("ship" if n == 1 else "ships") if ships else ("record" if n == 1 else "records")
            text = (f"{W} wins over {L} on {n} record(s) (fields: {_names(real)}). {W} loads after every mod, so no "
                    f"load order or patch brings this change back; the edit {L} makes to "
                    f"{f'this {what}' if n == 1 else f'these {n} {what}'} doesn't reach the game.")
            fix = (f"Nothing to change in the load order. If you need {L}'s version, ask its author for an update "
                   f"made for {W}.")
            log.info(SECTION, f"{L} vs {W}: {W} is an official BlueprintShips file, which the game loads after every "
                              "mod, so the pair gets no patch or pick-a-winner advice")
        elif p["winner_official"]:
            fix = (f"{W} is an official file that loads after every mod, so no order fixes this; a patch that loads "
                   f"after it can.")
        add(Finding(level, SECTION, text, fix=fix, plugins=[L, W],
                    data={"advice": p["advice"], "records": [lost[i]["fk"] for i in p["records"]][:50],
                          "fields": p["fields"]}))

    by_winner = {}
    for i in res["official_undone"]:
        by_winner.setdefault(lost[i]["winner"], []).append(lost[i])
    for w, xs in by_winner.items():
        real = [f for x in xs for f in x["fields"] if f["label"] != NO_EFFECT]
        if not real:
            continue
        offs = sorted({x["loser"] for x in xs})
        recs = sorted({x["fk"] for x in xs})
        add(Finding("caution" if any(f["weight"] >= 3 for f in real) else "note", SECTION,
                    f"{w} undoes official changes from {', '.join(offs)} on {len(recs)} record(s) (fields: "
                    f"{_names(real)}): it wasn't built with them as masters.",
                    fix=(f"Look for an update of {w} made for the current game and DLCs, or a patch that carries the "
                         "official changes forward. Moving it won't help: official files always load first."),
                    plugins=[w] + offs, data={"records": recs[:50]}))

    for t in res["type_changes"]:
        add(Finding("problem", SECTION,
                    t["text"] or f"{t['plugin']} gives {t['fk']} a different record type than {t['first']}.",
                    fix=("The game keeps whichever record loads first and drops the other. Tell the mod's author: "
                         "the record needs its own FormID (xEdit: right-click > Change FormID)."),
                    plugins=[x for x in (t["plugin"], t["first"]) if x], data={"fk": t["fk"]}))

    itm_by = {}
    for x in res["itms"]:
        itm_by.setdefault(x["plugin"], []).append(x["fk"])
    for plugin, fks in itm_by.items():
        add(Finding("note", SECTION, f"{plugin} has {len(fks)} record(s) identical to the version it overrides "
                    "(ITM); they change nothing but can undo other mods' edits to the same records.",
                    fix="Clean it with xEdit's QuickAutoClean (Remove \"Identical to Master\" records), or ask the "
                        "author for a cleaned version.", plugins=[plugin], data={"records": fks[:50]}))

    for pr in res["problems"]:
        if pr["seen_by"] != "records":
            log.info(SECTION, f"load-order problem left to the {pr['seen_by']} checks: {pr['text']}")
            continue
        add(Finding("caution", SECTION, f"sf-mod-check: {pr['text']}.",
                    fix=f"Run sf-mod-check check on {pr['plugin'] or 'that plugin'} to see why; reinstall it if "
                        "the file is damaged.", plugins=[pr["plugin"]] if pr["plugin"] else []))

    nc = res["not_compared"]
    if nc["errors"] or nc["unreadable_fields"]:
        add(Finding("note", SECTION,
                    f"sf-mod-check couldn't compare everything: {len(nc['errors'])} error line(s) and "
                    f"{sum(nc['unreadable_fields'].values())} unreadable field(s), so conflicts there aren't listed.",
                    fix="See \"Tool limits hit\" in sf-mod-check's report.html; sf-mod-check check on the named "
                        "plugin shows the record.", data={"errors": nc["errors"][:20]}))

    miss = {}
    for m in res["assets"]["missing"]:
        miss.setdefault((m.get("plugin") or ""), []).append(m)
    for plugin, ms in miss.items():
        scripts = [m for m in ms if (m.get("kind") or "").lower().startswith("script")]
        models = len(ms) - len(scripts)
        add(Finding("caution" if scripts else "note", SECTION,
                    f"{plugin}: {len(scripts)} script(s) and {models} model(s) its records use aren't installed "
                    "(not loose and not in an archive the game loads).",
                    fix="Reinstall the mod, or install the mod those files come from.", plugins=[plugin],
                    data={"paths": [m.get("path") for m in ms][:50]}))
    for w in res["assets"]["archive_limit_warnings"]:
        add(Finding("caution", SECTION, f"sf-mod-check: {w}",
                    fix="Remove or merge mods that bring archives, or use a BA2 limit fix."))

    d = res["differs"]
    if d and source == "file":
        bits = []
        if d["missing"]:
            bits.append(f"{len(d['missing'])} enabled plugin(s) aren't in it")
        if d["extra"]:
            bits.append(f"it has {len(d['extra'])} plugin(s) that aren't enabled now")
        if d["order"]:
            bits.append("the order differs")
        add(Finding("caution", SECTION, f"The --conflicts file was made for another load order ({'; '.join(bits)}), "
                    "so its record results may be out of date.",
                    fix="Run sf-mod-check conflict again on this load order, or leave out --conflicts.",
                    data=d))


# ---------- heavy ----------
def heavy(ctx, prog, res):
    """--depth heavy: sf-mod-check check on each enabled mod plugin, for deleted records and broken plugins (the ITM
    count from check is kept in the data only: conflict already lists ITMs)."""
    lo = ctx.load_order
    out = {}
    failed = []
    for p in _mod_plugins(lo):
        path = os.path.join(lo.data, p.name)
        if not os.path.isfile(path):
            failed.append(f"{p.name} (not in the Data folder)")
            continue
        r = sfmodcheck.run_check(prog, path, lo.data)
        out[p.name.lower()] = r
        if r["why"]:
            failed.append(f"{p.name} ({r['why']})")
            continue
        risky = {k: v for k, v in r["deleted"].items() if k in DELETED_RISKY}
        other = {k: v for k, v in r["deleted"].items() if k not in DELETED_RISKY}
        if risky:
            what = ", ".join(f"{v} {k}" for k, v in sorted(risky.items()))
            ctx.report.add(Finding("caution", SECTION,
                                   f"{p.name} deletes records ({what}); another mod or the game still using a deleted "
                                   "placed reference or navmesh can crash the game.",
                                   fix="Ask the author for a cleaned version, or in xEdit use Undelete and Disable "
                                       "References on the placed references.", plugins=[p.name], data=r["deleted"]))
        elif other:
            what = ", ".join(f"{v} {k}" for k, v in sorted(other.items()))
            ctx.report.add(Finding("note", SECTION, f"{p.name} deletes records ({what}).",
                                   fix="Usually meant; check it only if something from the game goes missing.",
                                   plugins=[p.name], data=r["deleted"]))
        if r["errors"]:
            ctx.report.add(Finding("caution", SECTION, f"sf-mod-check check found {r['errors']} error(s) in {p.name}.",
                                   fix=f"Run sf-mod-check check \"{p.name}\" to see them, and tell the mod's author.",
                                   plugins=[p.name]))
    if failed:
        ctx.report.add(Finding("note", SECTION, f"sf-mod-check check couldn't check {len(failed)} plugin(s): "
                               + "; ".join(failed[:10]) + ("..." if len(failed) > 10 else ""),
                               plugins=[f.split(" (")[0] for f in failed]))
    res["heavy"] = out
    return len(out), len(failed)


# ---------- run ----------
def _skip(why, **kw):
    log.info(SECTION, f"skipped: {why}")
    return dict({"status": "skipped", "why": why}, **kw)


def _exe_note(ctx, prog):
    """A note naming the .exe chosen beside a .cmd/.bat wrapper (sfmodcheck.unwrap_info), once per run."""
    if prog.note and not any(f.section == SECTION and f.data.get("exe") == prog.path for f in ctx.report.findings):
        log.info(SECTION, prog.note)
        ctx.report.add(Finding("note", SECTION, prog.note, fix="If that isn't the sf-mod-check you meant, give "
                               "--sf-mod-check the full path of the right sf-mod-check.exe.", data={"exe": prog.path}))


def _not_visible(lo):
    return [p.name for p in _mod_plugins(lo) if not os.path.isfile(os.path.join(lo.data, p.name))]


def run(ctx):
    args, lo = ctx.args, ctx.load_order
    depth = getattr(args, "depth", None) or "medium"
    given = getattr(args, "conflicts", None)
    prog, result_line, exit_code = None, None, None
    if given:
        cj, err = load(given)
        if err:
            ctx.report.add(Finding("caution", SECTION, f"The --conflicts file couldn't be used: {err}"))
            return _skip(err)
        source, path = "file", os.path.abspath(given)
        log.info(SECTION, f"read {path}: {len(cj['lost'])} lost entries")
    else:
        prog, err = sfmodcheck.find(args)
        if err:
            return _skip(err)
        if prog is None:
            return _skip(NO_PROGRAM)
        _exe_note(ctx, prog)
        err = sfmodcheck.version(prog)
        if err:
            return _skip(err, program=prog.to_dict())
        err = sfmodcheck.too_old(prog)
        if err:
            return _skip(err, program=prog.to_dict())
        if not lo.data:
            return _skip("no Data folder was found, so sf-mod-check has nothing to read. Fix: pass --data.")
        hidden = _not_visible(lo)
        if hidden and lo.source == "mo2":
            why = (f"{len(hidden)} enabled plugin(s) aren't in {lo.data} (MO2 keeps mods in their own folders, and "
                   "only programs started from MO2 see them in Data). Fix: start sf-load-order from MO2 (add it as "
                   "an executable), or run sf-mod-check conflict from MO2 with --plugins set to the profile's "
                   "plugins.txt and pass its conflicts.json with --conflicts.")
            ctx.report.add(Finding("note", SECTION, "Record conflicts weren't checked: " + why.split(" Fix: ")[0] + ".",
                                   fix=why.split(" Fix: ")[1], plugins=hidden[:20]))
            return _skip(why, program=prog.to_dict())
        os.makedirs(ctx.work, exist_ok=True)
        ptxt = sfmodcheck.write_plugins_txt(os.path.join(ctx.work, "sf-mod-check-plugins.txt"), lo)
        r = sfmodcheck.run_conflict(prog, ptxt, lo.data, os.path.join(ctx.work, "conflict"),
                                    getattr(args, "max_memory", None),
                                    timeout=getattr(args, "sf_mod_check_timeout", None) or sfmodcheck.CONFLICT_TIMEOUT)
        result_line, exit_code = r.result, r.exit
        failed = None
        if getattr(r, "refused", False):
            failed = (f"sf-mod-check wasn't run: {r.start_error}.",
                      "Give --sf-mod-check the sf-mod-check.exe itself (not the .cmd), or use folders whose names "
                      "hold none of & | < > ^ % ! \" for the output (--out) and the game.")
        elif r.start_error:
            failed = (f"sf-mod-check couldn't be started ({r.start_error}).",
                      "Check the --sf-mod-check path, or reinstall sf-mod-check.")
        elif r.timed_out:
            failed = (f"sf-mod-check conflict didn't finish within {int(r.seconds)} seconds and was stopped.",
                      "Run sf-mod-check conflict yourself (it can take a while on a big load order) and pass its "
                      "conflicts.json with --conflicts.")
        elif r.exit == 3:
            failed = (f"sf-mod-check conflict stopped at the memory limit ({r.result or 'RESULT: STOPPED'}).",
                      "Close other programs and run again with a higher --max-memory (or none).")
        elif r.exit == 2:
            failed = (f"sf-mod-check conflict couldn't run ({r.result or 'no RESULT line'}).",
                      "Run it with --log to see why: sf-mod-check conflict --plugins <file> --data <folder> --log "
                      "sf-mod-check-log.txt.")
        elif r.exit != 0:
            failed = (f"sf-mod-check conflict ended with exit code {r.exit} ({r.result or 'no RESULT line'}).",
                      "Run it yourself with --log to see why.")
        elif not r.conflicts:
            failed = ("sf-mod-check conflict said it finished but wrote no conflicts.json.",
                      "Run it yourself with --log to see why.")
        if failed:
            ctx.report.add(Finding("caution", SECTION, "Record conflicts weren't checked: " + failed[0], fix=failed[1]))
            return _skip(failed[0], program=prog.to_dict(), exit=r.exit, result_line=r.result)
        cj, err = load(r.conflicts)
        if err:
            ctx.report.add(Finding("caution", SECTION, "Record conflicts weren't checked: " + err))
            return _skip(err, program=prog.to_dict(), exit=r.exit, result_line=r.result)
        source, path = "run", r.conflicts

    res = analyse(cj, lo)
    res.update({"source": source, "file": path, "program": prog.to_dict() if prog else None,
                "exit": exit_code, "result_line": result_line})
    ctx.results[SECTION] = res
    add_findings(ctx, res, source)
    status = {"status": "done", "source": source, "file": path,
              # the navmesh info map entries aren't lost changes (each copy lists its own navmeshes): counted apart
              "lost": sum(1 for x in res["lost"] if x["outcome"] != "navi"),
              "navi": sum(1 for x in res["lost"] if x["outcome"] == "navi"), "pairs": len(res["pairs"]),
              "action_pairs": sum(1 for p in res["pairs"] if acts_on(p)),
              "official_undone": len(res["official_undone"]), "itms": len(res["itms"]),
              "type_changes": len(res["type_changes"])}
    if result_line:
        status["result_line"] = result_line
    if depth == "heavy":
        if prog is None:
            prog, err = sfmodcheck.find(args)
            if not err and prog is not None:
                err = sfmodcheck.version(prog) or sfmodcheck.too_old(prog)
            if err or prog is None:
                status["heavy"] = f"skipped: {err or NO_PROGRAM}"
                prog = None
            else:
                _exe_note(ctx, prog)
        if prog is not None:
            if not lo.data:
                status["heavy"] = "skipped: no Data folder (pass --data)"
            elif _not_visible(lo) and lo.source == "mo2":
                status["heavy"] = "skipped: MO2's mods aren't in the Data folder (start sf-load-order from MO2)"
            else:
                done, bad = heavy(ctx, prog, res)
                status["heavy"] = f"sf-mod-check check on {done} plugin(s), {bad} not checked"
        log.info(SECTION, f"heavy: {status['heavy']}")
    log.info(SECTION, f"{len(res['lost'])} lost entries, {len(res['pairs'])} pairs, ratings "
                      + str({k: sum(1 for v in res['plugins'].values() if v['rating'] == k) for k in RATING_LABEL}))
    return status
