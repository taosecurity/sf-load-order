"""Loose files: files mods ship outside archives (section "loose").

A loose file in the game's Data folder beats every archive, whatever the plugin order. This check needs each mod's
own folder (MO2's mods folder, or Vortex's staging folder), so it is skipped for a plain Plugins.txt. It lists:

  - Mods shipping the same loose path. Identical copies (same CRC32 and size) need nothing. Different copies: with
    MO2 the mod with the higher priority wins and its copy is the one the game sees; with Vortex its own conflict
    rules decide, and when the Data folder holds the file and it matches one mod's copy, that copy is named.
  - Loose files that hide a file inside a loaded archive (a mod's or the base game's), with whether the content is
    the same (SHA-1, texture pixel data for .dds as archives.py compares them).
  - Stray files: loose files in Data that no enabled mod ships, counted by folder and named by source rules. Only
    when the mod list is complete: MO2 (it never copies mods into Data), or Vortex when Data matches the load order.

Hashes are cached per file (full path + size + modification time) in <state>\\cache\\loose.json; --no-state keeps
them in memory only. For Vortex, the deployment record (vortex_deploy.py) names each deployed file's mod; download archives are never read.
"""
import os
import re
from collections import Counter, defaultdict

from . import archives, log, vortex_deploy
from .model import Finding

SECTION = "loose"
SAMPLES = 6
PLUGIN_EXT = (".esm", ".esp", ".esl")
SKIP_EXT = PLUGIN_EXT + (".ba2",)
DOC = re.compile(r"(^|\\)(fomod\\.*|readme[^\\]*|changelog[^\\]*|license[^\\]*|[^\\]*\.(txt|md|pdf|url|jpg|png|psd))$",
                 re.I)
MARKER = "__folder_managed_by_vortex"
VORTEX_RECORD = "vortex.deployment.starfield-data-folder.json"

# Folders a Starfield Data path can start with (seen in Data and the game's archives).
ROOTS = {"textures", "meshes", "materials", "geometries", "scripts", "sound", "interface", "music", "video",
         "terrain", "particles", "planetdata", "shadersfx", "misc", "lodsettings", "strings", "sfse", "facegen",
         "facecustomization", "vis", "lighting", "space", "distantlod", "seq", "dialogueviews", "animationdata",
         "animationdatasinglefile", "actors", "effects", "nativeterrain", "lod", "meshesextra", "editorfiles",
         "f4se", "replacements", "lights", "naf", "weathers", "aiss", "dataviews"}


# ---------------------------------------------------------------- listing files

def mod_files(folder):
    """(all, loose) for one mod folder: all = every file's relative path (lower case, backslashes), the files the
    mod owns once installed; loose = {path: full path} of the files that can clash, i.e. inside a subfolder and not a
    plugin, archive, doc, installer file or Vortex marker. MO2's meta.ini sits at the top and is skipped."""
    allp, loose = set(), {}
    for root, dirs, files in os.walk(folder):
        rel = os.path.relpath(root, folder)
        if rel == ".":
            dirs[:] = [x for x in dirs if x.lower() not in ("fomod", ".git")]
            for fn in files:
                if fn.lower() != "meta.ini":
                    allp.add(fn.lower())
            continue
        for fn in files:
            p = (rel.replace("/", "\\") + "\\" + fn).lower()
            allp.add(p)
            if p.endswith(SKIP_EXT) or DOC.search(p) or fn.lower().startswith(MARKER):
                continue
            loose[p] = os.path.join(root, fn)
    return allp, loose


def data_files(data):
    """(subfolder files, top-level files) of Data, relative lower-case paths. Top-level plugins and archives are
    left out (the order and archive checks cover them)."""
    sub, top = {}, {}
    for entry in os.scandir(data):
        if entry.is_file():
            if not entry.name.lower().endswith(SKIP_EXT):
                top[entry.name.lower()] = entry.path
            continue
        if not entry.is_dir():
            continue
        for root, _dirs, files in os.walk(entry.path):
            rel = os.path.relpath(root, data).replace("/", "\\").lower()
            for fn in files:
                sub[rel + "\\" + fn.lower()] = os.path.join(root, fn)
    return sub, top


# ---------------------------------------------------------------- where a stray file came from

TS = r"\d{4}-\d{2}-\d{2}[_ -]\d{2}-\d{2}-\d{2}(\.\d+)?"
XEDIT_TS = r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}"
NOT_GAME_ROOTS = {"aiss", "naf", "f4se", "sfse"}
BACKUP_SUFFIX = re.compile(r"\.(bak\d*|orig|old|tmp)(\.\d+)?$")
HINTS = {
    "vortex-marker": "Vortex's marker for a folder it created; harmless, and Vortex removes it when it purges.",
    "vortex-record": "Vortex's deployment record (which mod deployed which file); leave it, Vortex needs it.",
    "sfse-dll": "An SFSE plugin DLL no enabled mod ships (copied in by hand or by another tool); SFSE still loads "
                "it, so remove it only if you no longer want that plugin.",
    "sfse-dll-settings": "Settings or data of the SFSE plugin {owner}; it was made or changed after install, so keep "
                         "it while you use that plugin.",
    "sfse-log": "A log written by an SFSE plugin{owner_of}; safe to delete.",
    "sfse-report": "A timestamped report or crash log an SFSE plugin writes each run; old ones are safe to delete.",
    "sfse-data": "A file under Data\\SFSE, which only SFSE and its plugins read: an SFSE plugin's own data or "
                 "run-time output.",
    "xedit-backup": "An xEdit backup of a plugin it saved (in the {folder} folder); the game ignores it, safe to "
                    "remove once you're happy with the edit.",
    "xedit-cache": "xEdit's reference cache (it rebuilds it when needed); the game ignores it, safe to delete.",
    "xedit-save-leftover": "A plugin xEdit was saving when it stopped (it renames these when a save finishes); the "
                           "game ignores it, check the real plugin before deleting it.",
    "ck-autosave": "A Creation Kit autosave or plugin backup in Data\\Backup; the game ignores it, safe to delete if "
                   "you don't need to roll back a CK edit.",
    "ck-install": "Part of the Creation Kit install (editor-only data); the game doesn't load it, leave it while the "
                  "CK is installed.",
    "psc-with-pex": "Papyrus script source next to a matching compiled script ({pex}); the game runs only the .pex, "
                    "so the source is safe to remove from Data.",
    "psc-no-pex": "Papyrus script source with no compiled .pex among the loose files (its script may be inside a "
                  ".ba2, or it's the Creation Kit's unpacked source); the game never reads .psc files.",
    "psc-backup": "A backup copy of a Papyrus source file made by an editor; the game ignores it, safe to delete.",
    "photo-mode": "A photo (or its thumbnail) the game saved in photo mode; keep it if you want the picture.",
    "motd-image": "The main menu news image the game downloads and keeps; harmless.",
    "video-bk2": "Starfield ships its videos as loose .bk2 files in Data\\Video; a replacement you copied in by hand "
                 "would look the same, so check its date if you replaced one.",
    "backup-suffix": "A backup or temporary copy made by an editor or tool; the game ignores it, safe to delete.",
    "mod-folder": "Sits in a folder only the mod {owner} ships files into: most likely that mod's run-time output, "
                  "settings or a leftover from an older version.",
    "sfse-companion-folder": "Data\\{folder} isn't a folder the game reads, and Data\\{sfse} has the same name: the "
                             "data or run-time output of that SFSE plugin.",
    "tool-folder": "Data\\{folder} isn't a folder the game reads: a mod or tool put it there (its own data, logs or "
                   "docs), and the game ignores it.",
    "unknown": "No rule names its source; check it by hand before removing it.",
}


def _result(path, source, rule, confidence="certain", owner=None, **fmt):
    hint = HINTS[rule]
    if "{owner_of}" in hint:
        fmt["owner_of"] = f" ({owner}.dll)" if owner else " (no installed DLL matches its name, so the plugin may be gone)"
    if "{owner}" in hint:
        fmt["owner"] = owner
    return {"path": path, "source": source, "rule": rule, "confidence": confidence, "owner": owner,
            "hint": hint.format(**fmt) if fmt else hint}


class _Known:
    def __init__(self, known, folder_mods):
        self.known = known
        self.folder_mods = folder_mods
        self.dlls = sorted({os.path.splitext(p.split("\\")[-1])[0] for p in known
                            if p.startswith("sfse\\plugins\\") and p.count("\\") == 2 and p.endswith(".dll")},
                           key=len, reverse=True)

    def dll_for(self, stem):
        s = stem.lower()
        if s in self.dlls:
            return s
        return next((d for d in self.dlls if len(d) >= 4 and s.startswith(d)), None)


def classify_one(p, k):
    """Name the source of one stray file (lower case, backslashes) by rules; "unknown" when no rule matches."""
    parts = p.split("\\")
    name = parts[-1]
    stem, ext = os.path.splitext(name)
    top = parts[0] if len(parts) > 1 else ""
    if name == MARKER:
        return _result(p, "vortex", "vortex-marker")
    if len(parts) == 1 and name == VORTEX_RECORD:
        return _result(p, "vortex", "vortex-record")
    if re.fullmatch(r"\w+edit backups", top):
        return _result(p, "xedit-output", "xedit-backup", folder=parts[0])
    if re.fullmatch(r"\w+edit cache", top) and ext == ".refcache":
        return _result(p, "xedit-output", "xedit-cache")
    if len(parts) == 1 and re.search(r"\.es[mpl]\.save\." + XEDIT_TS + r"(_\d+)?$", name):
        return _result(p, "xedit-output", "xedit-save-leftover")
    if re.search(r"\.es[mpl]\.backup\." + XEDIT_TS + r"(_\d+)?$", name):
        return _result(p, "xedit-output", "xedit-backup", folder=parts[0] if len(parts) > 1 else "Data")
    if top == "backup" and len(parts) == 2 and (re.fullmatch(r"autosave\w*\.bak(\.\d+\.bak)*", name)
                                                 or re.fullmatch(r".+\.es[mpl]\.\d+\.bak", name)):
        return _result(p, "ck-output", "ck-autosave")
    if top in ("editorfiles", "dataviews") or p.startswith(("source\\tgatextures\\", "textures\\brushalphas\\")):
        return _result(p, "ck-install", "ck-install")
    if ext == ".psc" or re.search(r"\.psc\.(bak\d*|orig|old)$", name):
        if ext != ".psc":
            return _result(p, "script-source", "psc-backup")
        rest = None
        for pre in ("scripts\\source\\", "source\\scripts\\", "scripts\\"):
            if p.startswith(pre):
                rest = p[len(pre):]
                break
        if rest is not None and "scripts\\" + rest[:-4] + ".pex" in k.known:
            return _result(p, "script-source", "psc-with-pex", pex="Data\\scripts\\" + rest[:-4] + ".pex")
        return _result(p, "script-source", "psc-no-pex")
    if top == "sfse":
        if len(parts) == 3 and parts[1] == "plugins" and ext == ".dll":
            return _result(p, "sfse-plugin", "sfse-dll", owner=stem)
        if re.search(TS, name) and ext in (".txt", ".log"):
            return _result(p, "sfse-plugin", "sfse-report")
        if len(parts) >= 3 and parts[1] == "plugins" and ext == ".log":
            return _result(p, "sfse-plugin", "sfse-log", owner=k.dll_for(stem))
        if len(parts) >= 3 and parts[1] == "plugins":
            own = k.dll_for(stem) if len(parts) == 3 else k.dll_for(parts[2])
            if own and not BACKUP_SUFFIX.search(name):
                return _result(p, "sfse-plugin", "sfse-dll-settings", owner=own + ".dll")
        if BACKUP_SUFFIX.search(name):
            return _result(p, "backup-copy", "backup-suffix")
        return _result(p, "sfse-plugin", "sfse-data")
    if p.startswith("textures\\photos\\") and re.fullmatch(
            r"photo_\d{4}-\d{2}-\d{2}-\d{6}(_[\w' -]+?)?(-thumbnail)?\.png", name):
        return _result(p, "game-output", "photo-mode")
    if p.startswith("textures\\motd_media\\"):
        return _result(p, "game-output", "motd-image")
    if top == "video" and len(parts) == 2 and ext == ".bk2":
        return _result(p, "game", "video-bk2", confidence="likely")
    if BACKUP_SUFFIX.search(name):
        return _result(p, "backup-copy", "backup-suffix")
    for i in range(len(parts) - 1, 0, -1):  # nearest folder any mod ships into; a bare game root names no one
        folder = "\\".join(parts[:i])
        mods = k.folder_mods.get(folder)
        if mods:
            if i == 1 and folder in ROOTS:
                break
            if len(mods) == 1:
                return _result(p, "mod-folder", "mod-folder", confidence="likely", owner=next(iter(mods)))
            break
    if len(parts) > 1 and (top not in ROOTS or top in NOT_GAME_ROOTS):
        fam = next((f for f in (f"sfse\\{top}\\", f"sfse\\plugins\\{top}\\")
                    if any(x.startswith(f) for x in k.known)), None)
        if fam:
            return _result(p, "sfse-plugin", "sfse-companion-folder", folder=top, sfse=fam.rstrip("\\"))
        return _result(p, "tool", "tool-folder", folder=top)
    return _result(p, "unknown", "unknown", confidence="unknown")


def classify(stray, known, folder_mods):
    """One result per stray path: path, source, rule, confidence (certain, likely, unknown), hint, owner."""
    k = _Known(set(known), folder_mods)
    return [classify_one(p, k) for p in stray]


def group_of(path):
    return path.rsplit("\\", 1)[0] if "\\" in path else "(Data)"


# ---------------------------------------------------------------- the check

def _mods_in_priority(lo):
    """Enabled mods with a folder, lowest priority first. MO2: Mod.priority (higher wins); else the listed order."""
    mods = [(i, m) for i, m in enumerate(lo.mods or []) if m.enabled and m.path and os.path.isdir(m.path)]
    mods.sort(key=lambda im: (im[1].priority if im[1].priority is not None else im[0], im[0]))
    return [m for _i, m in mods]


def analyse(lo, cache, arc_cache, arc_result):
    mods = _mods_in_priority(lo)
    mo2 = lo.source == "mo2"
    dep = None if mo2 else getattr(lo, "_vortex_deployment", None)
    rank = {m.name: i for i, m in enumerate(mods)}
    data = lo.data if lo.data and os.path.isdir(lo.data) else None
    # (a Vortex profile that Data doesn't hold never gets here: cli.NEEDS_DEPLOYED skips the section)
    trusted = data is not None and (mo2 or archives.data_matches(lo))
    # stray files need to know every mod's files: MO2's folders, or Vortex's deployment record
    stray_ok = data is not None and (mo2 or dep is not None)

    ships = defaultdict(list)  # path -> [(mod name, full path)], lowest priority first
    owned, folder_mods = set(), defaultdict(set)
    for m in mods:
        allp, lf = mod_files(m.path)
        m.files = sorted(allp)
        owned |= allp
        for p in allp:
            parts = p.split("\\")
            for i in range(1, len(parts)):
                folder_mods["\\".join(parts[:i])].add(m.name)
        for p, full in lf.items():
            ships[p].append((m.name, full))
        log.info(SECTION, f"{m.name}: {len(allp)} files, {len(lf)} loose")
    for m in lo.mods or []:  # Vortex mods known only from the deployment record (no staging folder to read)
        if m.enabled and m.files and m not in mods:
            owned |= {f.lower() for f in m.files}

    def info(full):
        try:
            return cache.file(full)
        except OSError:
            return None

    dsub, dtop = data_files(data) if data else ({}, {})
    out_paths, pairs = [], {}
    for p, who in sorted(ships.items()):
        if len(who) < 2:
            continue
        facts = [(mn, info(full)) for mn, full in who]
        sig = {mn: (f["crc"], f["size"]) if f else None for mn, f in facts}
        here = None
        if not mo2 and p in dsub:
            d = info(dsub[p])
            here = (d["crc"], d["size"]) if d else None
        if mo2:
            winner = who[-1][0]
        else:
            match = [mn for mn, s in sig.items() if s is not None and s == here]
            winner = match[0] if match else None
        known = [s for s in sig.values() if s is not None]
        identical = len(known) == len(who) and len(set(known)) == 1
        out_paths.append({"path": p, "mods": [mn for mn, _ in who], "winner": winner, "identical": identical,
                          "in_data": p in dsub, "data_matches_none": here is not None and here not in known})
        for i in range(len(who)):
            for j in range(i + 1, len(who)):
                a, b = who[i][0], who[j][0]
                key = (a, b) if rank[a] < rank[b] else (b, a)
                pr = pairs.setdefault(key, {"mods": list(key), "files": 0, "same": 0, "differ": 0, "unknown": 0,
                                            "sample": [], "in_data": defaultdict(int), "nomatch": 0,
                                            "not_same": []})
                pr["files"] += 1
                sa, sb = sig[a], sig[b]
                how = "unknown" if sa is None or sb is None else ("same" if sa == sb else "differ")
                pr[how] += 1
                if how != "same":
                    pr["not_same"].append(p)  # this pair's own differing or unreadable paths (rules reads them)
                    if len(pr["sample"]) < SAMPLES:
                        pr["sample"].append(p)
                    if here is not None:
                        if here == sa:
                            pr["in_data"][a] += 1
                        elif here == sb:
                            pr["in_data"][b] += 1
                        else:
                            pr["nomatch"] += 1
    for pr in pairs.values():
        pr["in_data"] = dict(pr["in_data"])
        pr["advice"] = "same" if pr["same"] == pr["files"] else "differ"
        if mo2:
            pr["winner"] = pr["mods"][1]  # the higher priority of the two
    pair_list = sorted(pairs.values(), key=lambda x: (x["advice"] != "differ", -x["differ"], x["mods"]))

    # the loose copy the game sees for each path
    view = {}  # path -> (owner or None for Data, full path)
    if trusted:
        for p, full in dsub.items():
            view[p] = (None, full)
    for p, who in ships.items():
        if mo2:
            view[p] = who[-1]
        elif p in dsub:  # Vortex deployed it: name the mod whose copy Data holds
            d = info(dsub[p])
            mt = None
            if d:
                for mn, full in who:
                    f = info(full)
                    if f and (f["crc"], f["size"]) == (d["crc"], d["size"]):
                        mt = mn
                        break
            view[p] = (mt or vortex_deploy.owner(dep, p) or "(Data)", dsub[p])
        else:
            view[p] = who[0] if len(who) == 1 else ("(Vortex's rules)", who[-1][1])

    # stray files: in Data, shipped by no enabled mod
    stray = None
    if stray_ok:
        names = sorted(p for p in list(dsub) + list(dtop)
                       if p not in owned and (dep is None or vortex_deploy.owner(dep, p) is None))
        res = classify(names, set(dsub) | set(dtop) | owned, folder_mods)
        tops = Counter(group_of(p).split("\\", 1)[0] for p in names)
        stray = {"count": len(names), "folders": dict(tops.most_common()),
                 "by_source": dict(Counter(r["source"] for r in res).most_common()), "files": res}
        for p in names:
            if p in dsub:
                view[p] = ("(no mod)", dsub[p])

    # loose files hiding archived ones
    src = archives.archived_sources(lo, arc_result, arc_cache)
    modof = {}
    for m in (lo.mods or []):
        for pn in (m.plugins or []):
            modof.setdefault(pn.lower(), m.name)
    hides = defaultdict(lambda: {"files": 0, "same": 0, "differ": 0, "unknown": 0, "sample": []})
    for p in sorted(set(view) & set(src)):
        owner, full = view[p]
        aowner, afull = src[p]
        how = archives.compare_loose(cache, full, arc_cache.digest(afull, p))
        is_mod_arc = aowner.lower().endswith(PLUGIN_EXT)
        if is_mod_arc and owner and modof.get(aowner.lower()) == owner:
            continue  # a mod's loose file over its own archive: the author's choice
        h = hides[(owner or "(Data)", aowner if is_mod_arc else "(base game)")]
        h["files"] += 1
        h[how] += 1
        if how != "same" and len(h["sample"]) < SAMPLES:
            h["sample"].append(p)
    hide_list = [dict(v, loose=k[0], archive=k[1]) for k, v in sorted(hides.items())]
    log.info(SECTION, f"{len(ships)} loose paths from {len(mods)} mods, {len(pair_list)} pair(s), "
                      f"{sum(1 for x in pair_list if x['advice'] == 'differ')} with different copies, "
                      f"{len(hide_list)} hide group(s), stray "
                      + (str(stray["count"]) if stray else "not checked (Data doesn't match this load order)"))
    return {"pairs": pair_list, "paths": out_paths, "hides": hide_list, "stray": stray, "data_checked": stray_ok,
            "mods": len(mods)}


def _findings(report, lo, r):
    mo2 = lo.source == "mo2"
    quiet = []
    for pr in r["pairs"]:
        a, b = pr["mods"]
        if pr["advice"] == "same":
            quiet.append(pr)
            continue
        what = f"{pr['differ']} differ" + (f", {pr['unknown']} can't be read" if pr["unknown"] else "")
        if mo2:
            text = (f"Mods {a} and {b} both ship {pr['files']} loose file(s) ({what}). {b} has the higher priority "
                    f"in MO2, so its copies are the ones the game sees.")
            fix = (f"If you want {a}'s versions, give {a} a higher priority than {b} (lower in MO2's left pane); "
                   f"otherwise leave it.")
        else:
            seen = ", ".join(f"{n}'s copy of {c}" for n, c in sorted(pr["in_data"].items()))
            text = (f"Mods {a} and {b} both ship {pr['files']} loose file(s) ({what}). Vortex's conflict rules decide "
                    f"which copy is deployed" + (f"; Data holds {seen}" if seen else "") + ".")
            if pr["nomatch"]:
                text += f" {pr['nomatch']} file(s) in Data match neither mod's copy (edited or left over)."
            fix = ("Check the conflict icon on Vortex's Mods page and set which mod loads after the other, then "
                   "deploy.")
        report.add(Finding("caution", SECTION, text, fix, [], {k: pr[k] for k in pr if k not in ("advice", "not_same")}))
    if quiet:
        names = [f"{pr['mods'][0]} / {pr['mods'][1]} ({pr['files']})" for pr in quiet]
        report.add(Finding("note", SECTION, f"{len(quiet)} pair(s) of mods ship the same loose files with identical "
                           f"copies, so it doesn't matter which wins: " + "; ".join(names[:10])
                           + (f"; and {len(names) - 10} more" if len(names) > 10 else "") + ".", None, [],
                           {"pairs": [pr["mods"] for pr in quiet]}))
    base_same, base_n = 0, 0
    for h in r["hides"]:
        who = {"(Data)": "Loose files in Data", "(no mod)": "Loose files no mod ships",
               "(Vortex's rules)": "Loose files (deployed copy decided by Vortex's rules)"}.get(
            h["loose"], f"Loose files from {h['loose']}")
        if h["archive"] == "(base game)":
            base_n += h["files"]
            base_same += h["same"]
            continue
        if h["same"] == h["files"]:
            report.add(Finding("note", SECTION, f"{who} cover {h['files']} file(s) in {h['archive']}'s archives with "
                               f"identical copies; nothing changes.", None, [h["archive"]], dict(h)))
            continue
        report.add(Finding(
            "caution", SECTION,
            f"{who} replace {h['files']} file(s) inside {h['archive']}'s archives ({h['differ']} differ, "
            f"{h['unknown']} can't be compared). A loose file always beats an archive, so plugin order can't "
            f"change this.", f"If you want {h['archive']}'s versions, remove those loose files.", [h["archive"]],
            dict(h)))
    if base_n:
        report.add(Finding("note", SECTION, f"{base_n} loose file(s) replace files in the base game's archives "
                           f"({base_same} identical); that is what replacer mods do.", None, [],
                           {"groups": [h for h in r["hides"] if h["archive"] == "(base game)"]}))
    s = r["stray"]
    if s and s["count"]:
        folders = ", ".join(f"{f} {n}" for f, n in list(s["folders"].items())[:8])
        srcs = ", ".join(f"{k} {v}" for k, v in s["by_source"].items())
        report.add(Finding("note", SECTION, f"{s['count']} loose file(s) in Data come from no enabled mod (by folder: "
                           f"{folders}; by source: {srcs}).",
                           "Check the 'unknown' ones by hand; the report's data lists each file with a hint.", [],
                           {"folders": s["folders"], "by_source": s["by_source"], "files": s["files"][:200]}))


def run(ctx):
    """Section "loose". Stores in ctx.results["loose"]: pairs [{mods (lower priority first), files, same, differ,
    unknown, sample, in_data, nomatch, advice[, winner for MO2]}], paths, hides [{loose, archive, files, same,
    differ, unknown, sample}], stray ({count, folders, by_source, files} or None), data_checked, mods."""
    lo = ctx.load_order
    if not lo.mods:
        return {"status": "skipped", "why": lo.mods_note or "loose files need each mod's folder (MO2's mods folder "
                "or Vortex's staging folder), and a Plugins.txt alone doesn't list mods"}
    if not any(m.enabled and (m.files or (m.path and os.path.isdir(m.path))) for m in lo.mods):
        return {"status": "skipped", "why": "no enabled mod has a folder that can be read (check --mo2 or "
                "--vortex-staging)"}
    cache = archives.Cache(archives.cache_path(ctx.args, "loose.json"))
    arc_cache = archives.Cache(archives.cache_path(ctx.args, "archives.json"))
    arc_result = ctx.results.get("archives")
    if arc_result is None:  # the archive check was skipped or failed: work out which archives load
        arc_result = archives.analyse(lo, arc_cache)
    try:
        r = analyse(lo, cache, arc_cache, arc_result)
    finally:
        cache.save()
        arc_cache.save()
    ctx.results[SECTION] = r
    _findings(ctx.report, lo, r)
    status = {"status": "done", "mods": r["mods"], "pairs": len(r["pairs"]),
              "stray": r["stray"]["count"] if r["stray"] else None}
    if not r["data_checked"]:
        status["note"] = ("Stray files in Data weren't checked: " + ("no Data folder." if lo.data is None else "Vortex's deployment record wasn't found in Data, so the mods' own files aren't known."))
    return status
