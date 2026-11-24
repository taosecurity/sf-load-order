"""Read the load order to check, from one of three sources, into model.LoadOrder.

  read(args)      -> (LoadOrder, None) or (None, "what went wrong, naming the file, and the fix")
  findings(lo)    -> [model.Finding] for section "inputs": enabled plugins whose file is missing, names the game
                     drops, and what read() noticed on the way (a plugin listed twice, no Data folder, no staging)

Source choice (one source; --vortex-profile refuses any other):
  --vortex-profile NAME           the newest complete export set (modlist + loadorder, same stamp) of that profile
                                  from the Modlist Backup extension's export folder (--vortex-exports, default the
                                  folder in %APPDATA%\\Vortex\\modlist-backup-auto-export.json plus its old\\), or
                                  the set --vortex-stamp names; the loadorder file is read as --vortex-loadorder reads
                                  it, then deployed_check() says whether Data holds this profile
  --mo2 FOLDER [--profile NAME]   an MO2 instance: profiles\\<profile>\\plugins.txt, loadorder.txt and modlist.txt;
                                  the profile defaults to MO2's selected_profile in ModOrganizer.ini
  --vortex-loadorder FILE         a Vortex Load Order page export; mod folders from --vortex-staging (default
                                  %APPDATA%\\Vortex\\starfield\\mods), one per entry's modId
  --plugins-txt FILE              a Plugins.txt
  (none)                          the game's own %LOCALAPPDATA%\\Starfield\\Plugins.txt

Order rules (Starfield): official masters first in the game's fixed order (other SFBGS*.esm in Data follow by
name), then the listed plugins in file order, then the official BlueprintShips-*.esm last, oldest file first (by
modified time, then name; a listed BlueprintShips line is ignored, as the game ignores it). In Plugins.txt a line
starting with * is enabled, # starts a comment, any other line is a disabled plugin. Official masters are placed by
the game whether listed or not. A name ending in an official plugin's name, or starting with BlueprintShips-, is
removed from Plugins.txt by the game, so it never loads ("dropped").

Tests pass env (a dict like os.environ), drives (drive roots for the Game Pass search) and registry (a function
returning Steam's folder) to read(); the command line uses the real ones.
"""
import json
import os
import re

from . import log, paths, plugin_header, vortex_deploy
from .model import Finding, LoadOrder, Mod, Plugin

# Official masters in the order the game loads them. Other SFBGS*.esm files found in Data follow these by name.
# Constellation.esm and OldMars.esm exist only for Premium Edition owners.
OFFICIAL_ORDER = ["starfield.esm", "constellation.esm", "oldmars.esm", "shatteredspace.esm", "sfbgs003.esm",
                  "sfbgs004.esm", "sfbgs006.esm", "sfbgs007.esm", "sfbgs008.esm", "sfbgs00d.esm", "sfbgs047.esm",
                  "sfbgs050.esm"]
OFFICIAL_RE = re.compile(r"^(starfield|constellation|oldmars|shatteredspace|sfbgs[0-9a-z]{3})\.esm$", re.I)
BLUEPRINT_RE = re.compile(r"^blueprintships-(.+\.esm)$", re.I)
# The names sf-mod-check 0.12.0 counts official (src/ConflictLoadOrder.cs, tag v0.12.0): its OfficialOrder list
# (the four names below; its SFBGS names are hex, so the pattern covers them) and OfficialPattern: SFBGS plus
# 3 hex digits, sfta plus 2 digits, their BlueprintShips- files, and BlueprintShips-Starfield.esm. Other
# BlueprintShips- files (say BlueprintShips-Constellation.esm) are not on its lists. It leaves such plugins out of
# conflicts.json's mod list. is_official() doesn't use this: the sfta files are Creations that need a Plugins.txt
# line. Only the comparison with a --conflicts file uses it (records._differs).
MODCHECK_OFFICIAL_NAMES = frozenset({"starfield.esm", "constellation.esm", "oldmars.esm", "shatteredspace.esm"})
MODCHECK_OFFICIAL_RE = re.compile(r"^(blueprintships-)?(sfbgs[0-9a-f]{3}|sfta[0-9]{2})\.esm\Z|^blueprintships-starfield\.esm\Z", re.I)


# File names Starfield removes from Plugins.txt when it rewrites the file (tested in game, 2026-11-12).
DROPPED_PLUGIN_NAMES = [
    re.compile(r"-(starfield|constellation|oldmars|shatteredspace|sfbgs[0-9a-z]+|sfta0\d)\.es[mpl]$", re.I),
    re.compile(r"^blueprintships-", re.I),
]
PLUGIN_EXT = (".esm", ".esp", ".esl")
BAD_NAME_CHARS = ("\r", "\n", "\0", "/", "\\")
SECTION = "inputs"


class InputError(Exception):
    """A source can't be read. The text names the file and the fix."""


# ---------- shared rules ----------
def dropped_name(name):
    """True if Starfield would drop a plugin with this file name from Plugins.txt."""
    base = re.split(r"[\\/]", name)[-1]
    return any(rx.search(base) for rx in DROPPED_PLUGIN_NAMES)


def is_official(name):
    """An official master the game loads by itself. BlueprintShips-X.esm counts only when X is official. This is
    this tool's own rule (its SFBGS pattern is wider than sf-mod-check's); modcheck_official() is the narrower rule
    that only the --conflicts comparison uses."""
    n = name.lower()
    if OFFICIAL_RE.match(n):
        return True
    m = BLUEPRINT_RE.match(n)
    return bool(m and OFFICIAL_RE.match(m.group(1)))


def modcheck_official(name):
    """True if sf-mod-check 0.12.0 counts the plugin official (and so leaves it out of conflicts.json's mod list)."""
    n = (name or '').lower()
    return n in MODCHECK_OFFICIAL_NAMES or bool(MODCHECK_OFFICIAL_RE.match(n))


def bad_name(name):
    """True for a plugin name no plugin file can have, or one that would change meaning in Plugins.txt: a line break,
    NUL or other control character, a folder separator, a leading "*" (the enabled mark) or "#" (a comment), or
    leading or trailing spaces. Such a name is never used, so it can't add or alter lines in the plugins.txt and
    loadorder.txt this tool writes."""
    return (any(c in name for c in BAD_NAME_CHARS) or any(ord(c) < 32 for c in name) or not name
            or name[0] in "*#" or name != name.strip())


def drop_bad_names(entries, source):
    """(entries whose name (the first item) is a usable plugin name, [caution Finding for the ones left out])."""
    kept, bad = [], []
    for e in entries:
        (bad if bad_name(e[0]) else kept).append(e)
    if not bad:
        return kept, []
    shown = [repr(e[0]) for e in bad[:5]]
    log.info(SECTION, f"{source}: left out {len(bad)} name(s) that can't be plugin names: {', '.join(shown)}")
    return kept, [Finding("caution", SECTION, f"{source} lists {len(bad)} name(s) that can't be plugin file names "
                          "or would change meaning in a Plugins.txt (a line break, NUL or other control character, "
                          "a / or \\, a first character of * or #, or a space or other blank character at either end), so "
                          "they were left out: "
                          + ", ".join(shown) + (" and more." if len(bad) > 5 else "."),
                          fix=f"Remove those lines from {source}, or let the game or the mod manager write it again.",
                          data={"file": source, "names": [e[0] for e in bad]})]


def listing(folder):
    """{lower name: real name} of the plugin files in a folder ({} when it can't be read)."""
    try:
        return {n.lower(): n for n in os.listdir(folder) if n.lower().endswith(PLUGIN_EXT) and not bad_name(n)}
    except OSError as e:
        log.info(SECTION, f"can't list {folder}: {e.strerror}")
        return {}


def read_plugins_list(path):
    """[(name, enabled)] from a Starfield-style plugins file: * = enabled, # = comment, else disabled."""
    out = []
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("*"):
                    out.append((line[1:].strip(), True))
                else:
                    out.append((line, False))
    except OSError as e:
        raise InputError(f"can't read {path}: {e.strerror}. Fix: check the file exists and you may read it.")
    return out


def _age(path, name):
    """Sort key for BlueprintShips files: found files by modified time (oldest first), then files not found, by name."""
    try:
        return (0, os.path.getmtime(path), name) if path else (1, 0, name)
    except OSError:
        return (1, 0, name)


def build_order(listed, present, where):
    """Turn a listed order [(name, enabled)] into ([Plugin], [extra Finding]) with Starfield's rules.
    present: {lower name: real name} of plugin files on disk; where(lower name) -> full path or None."""
    extra, seen, plugins = [], set(), []
    listed_low = {n.lower() for n, _ in listed}
    officials = [n for n in present if is_official(n) and not BLUEPRINT_RE.match(n)]
    head = [n for n in OFFICIAL_ORDER if n in present] + sorted(n for n in officials if n not in OFFICIAL_ORDER)
    if OFFICIAL_ORDER[0] not in present:
        head.insert(0, OFFICIAL_ORDER[0])  # Starfield.esm always loads; findings() flags it when missing
    # Blueprint masters last: the game appends them after every other plugin, oldest file first (by the file's
    # modified time when it is found, then by name). This is the one place that rule lives; order.py reuses it.
    tail = sorted((n for n in present if is_official(n) and BLUEPRINT_RE.match(n)), key=lambda n: _age(where(n), n))
    for n in head:
        plugins.append(Plugin(present.get(n, "Starfield.esm" if n == OFFICIAL_ORDER[0] else n), True, official=True,
                              listed=n in listed_low, path=where(n)))
        seen.add(n)
    for name, on in listed:
        low = name.lower()
        if low in seen:
            if not is_official(low):
                extra.append(Finding("caution", SECTION, f"{name} is listed twice in the load order; the first "
                                     "entry counts.", fix="Remove the second line.", plugins=[name]))
            continue
        if is_official(low):
            continue  # placed by the game, listed or not (Blueprint masters go at the end)
        seen.add(low)
        plugins.append(Plugin(present.get(low, name), on, path=where(low), dropped=dropped_name(name)))
    for n in tail:
        plugins.append(Plugin(present[n], True, official=True, listed=n in listed_low, path=where(n)))
    log.info(SECTION, f"order: {len(head)} official first, {len(plugins) - len(head) - len(tail)} listed, "
                      f"{len(tail)} blueprint last")
    return plugins, extra


def _find_data(args, env, drives, registry, hint=None, hint_why=None):
    data, why, tried = paths.find_data(getattr(args, "data", None), hint, hint_why, env, drives, registry)
    if data is None and getattr(args, "data", None):
        raise InputError(f"{why}. Fix: give --data the game's Data folder (the one that holds Starfield.esm).")
    log.info(SECTION, f"data {data} ({why}); looked in {len(tried)} place(s)")
    for t in tried if data is None else []:
        log.info(SECTION, f"  no Data folder at {t}")
    extra = []
    if data is None:
        extra.append(Finding("caution", SECTION, f"The game's Data folder wasn't found (looked in {len(tried)} "
                             "Steam and Game Pass places), so missing files, plugin kinds and masters can't be "
                             "checked.", fix="Pass --data <the game's Data folder>, the one that holds "
                             "Starfield.esm.", data={"tried": tried}))
    return data, extra


def _in(folder, real):
    return os.path.join(folder, real)


# ---------- source 1: Plugins.txt ----------
def from_plugins_txt(path, args, env, drives, registry):
    if not os.path.isfile(path):
        raise InputError(f"no Plugins.txt at {path}. Fix: start the game once (it writes this file), or pass "
                         "--plugins-txt <file>, --mo2 <folder> or --vortex-loadorder <file>.")
    listed, bad = drop_bad_names(read_plugins_list(path), path)
    data, extra = _find_data(args, env, drives, registry)
    extra += bad
    log.info(SECTION, f"plugins.txt {path}: {len(listed)} lines")
    present = listing(data) if data else {}
    lo = LoadOrder("plugins.txt", files={"plugins_txt": path}, data=data,
                   mods_note="A plain Plugins.txt has no mod manager information, so mod folders and loose-file "
                             "owners are unknown.")
    lo.plugins, more = build_order(listed, present, lambda n: _in(data, present[n]) if n in present else None)
    return lo, extra + more


# ---------- source 2: Vortex Load Order export ----------
def _vortex_entries(path):
    bad = (f"{path} isn't a Vortex load order export. Fix: on Vortex's Load Order page, click Export and pass "
           "the file it saves to --vortex-loadorder.")
    if not os.path.isfile(path):
        raise InputError(f"no file at {path}. Fix: on Vortex's Load Order page, click Export and pass the file it "
                         "saves to --vortex-loadorder.")
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except OSError as e:
        raise InputError(f"can't read {path}: {e.strerror}. Fix: check you may read the file.")
    except (ValueError, UnicodeDecodeError):
        raise InputError(bad + " (It isn't valid JSON.)")
    if not isinstance(d, list) or not all(isinstance(e, dict) and (e.get("name") or e.get("id")) for e in d):
        raise InputError(bad + " (It should be a list of entries, each with a name.)")
    def mod_id(m):  # a string or a whole number; anything else (false, a list, an object, blanks) means no mod
        if isinstance(m, float) and m.is_integer():
            m = int(m)
        if isinstance(m, bool) or not isinstance(m, (str, int)) or (isinstance(m, str) and not m.strip()):
            return None
        # untrimmed: the id names the staging folder (paths.mod_folder tries exact, then Windows form, then trimmed);
        # the trimmed copy is only for the blank test above
        return str(m)
    return [(str(e.get("name") or e.get("id")), bool(e.get("enabled")), mod_id(e.get("modId"))) for e in d]


def _vortex_mod_key(staging, raw_id):
    """(the mod's id, its staging folder or None) for a raw Vortex modId. The id is always the trimmed modId, as in
    0.1.0, so ids that differ only by surrounding spaces are one mod and a mod keeps its id from run to run. Only the
    folder lookup uses the raw modId first (a folder name may start with a space, 0.1.1), then the name as Windows
    stores it (Windows only: trailing spaces and dots dropped), then the trimmed one (paths.mod_folder)."""
    if not raw_id:
        return None, None
    return raw_id.strip(), paths.mod_folder(staging, raw_id)


def from_vortex(path, args, env, drives, registry):
    entries, bad = drop_bad_names(_vortex_entries(path), path)
    data, extra = _find_data(args, env, drives, registry)
    extra += bad
    present = listing(data) if data else {}
    staging = getattr(args, "vortex_staging", None) or paths.default_vortex_staging(env)
    has_staging = bool(staging) and os.path.isdir(staging)  # no default off Windows (APPDATA isn't set)
    log.info(SECTION, f"vortex {path}: {len(entries)} entries; staging {staging} "
                      f"({'found' if has_staging else 'not found'})")
    lo = LoadOrder("vortex", files={"vortex_loadorder": path}, data=data)
    if has_staging:
        lo.files["vortex_staging"] = staging
    # Vortex writes mod plugin names in lower case: take the real case from Data, else from the mod's staging folder
    real, mods, order = {}, {}, []
    # First pass: each mod's folder, from whichever of its entries finds one, so the case lookups below don't
    # depend on the order of the export's entries
    folders, keyed = {}, []
    for name, on, raw_id in entries:
        mod_id, f = _vortex_mod_key(staging if has_staging else None, raw_id)
        keyed.append((name, on, mod_id, f))
        if not f:
            continue
        if mod_id not in folders:
            folders[mod_id] = f
        elif os.path.normcase(folders[mod_id]) != os.path.normcase(f):
            log.info(SECTION, f"mod {mod_id!r}: two staging folders ({folders[mod_id]}, {f}) differ only by "
                              "surrounding spaces, dots or case; the mod's path is the first, and each plugin's "
                              "case comes from its own entry's folder")
    for name, on, mod_id, own in keyed:
        low = name.lower()
        mfolder = folders.get(mod_id) if mod_id else None
        # the entry's own folder first, then the mod's (an entry whose id finds no folder of its own)
        case = (present.get(low) or (listing(own).get(low) if own else None)
                or (listing(mfolder).get(low) if mfolder and mfolder != own else None) or name)
        if case != name:
            log.info(SECTION, f"case of {name} -> {case}")
        real[low] = case
        order.append((case, on))
        if mod_id:
            m = mods.get(mod_id)
            if m is None:
                m = mods[mod_id] = Mod(mod_id, False, path=mfolder, plugins=[], id=mod_id)
            m.enabled = m.enabled or on
            m.plugins.append(case)
    lo.plugins, more = build_order(order, present, lambda n: _in(data, present[n]) if n in present else None)
    lo.mods = list(mods.values())
    # mods with no plugin aren't in the export: Vortex's deployment record in Data names them
    extra += vortex_deploy.enrich(lo, vortex_deploy.read(data))
    if "vortex_staging" not in lo.files:  # neither the option, Vortex's default nor the record's stagingPath
        where = staging or "(no default: APPDATA isn't set)"
        extra.append(Finding("note", SECTION, f"Vortex's staging folder {where} wasn't found, so mod folders are "
                             "unknown and loose-file checks are off.",
                             fix="Pass --vortex-staging <folder> (Vortex: Settings, Mods, Mod Staging Folder)."))
    return lo, extra + more


# ---------- source 2b: a Vortex profile's automatic export set ----------
STAMP_RE = re.compile(r"^\d{8}-\d{6}\Z")


def _export_re(profile):
    """The file names the Vortex extension "Modlist Backup" writes for one Starfield profile (its own pattern)."""
    return re.compile(r"^vortex-starfield-" + re.escape(profile) + r"-(modlist|loadorder|plugins)(?:-classic)?"
                      r"(?:-backup)?-(\d{8}-\d{6})\.(json|txt)\Z", re.I)


def find_vortex_set(profile, folders, stamp=None):
    """(stamp, {"modlist": path, "loadorder": path[, "plugins": path]}) for the newest complete set (a modlist and a
    loadorder file with the same stamp) of this profile, or the set with the given stamp. The first folder wins a
    file found twice. Raises InputError naming the folders looked in."""
    rx, sets = _export_re(profile), {}
    for folder in folders:
        try:
            names = sorted(os.listdir(folder))
        except OSError as e:
            log.info(SECTION, f"vortex exports: can't list {folder}: {e.strerror}")
            continue
        for n in names:
            m = rx.match(n)
            if m and os.path.isfile(os.path.join(folder, n)):
                sets.setdefault(m.group(2), {}).setdefault(m.group(1).lower(), os.path.join(folder, n))
    complete = sorted((s for s, v in sets.items() if "modlist" in v and "loadorder" in v), reverse=True)
    log.info(SECTION, f"vortex exports: profile {profile}: {len(complete)} complete set(s) in {len(folders)} folder(s)")
    where = ", ".join(folders) or "(no folder)"
    if stamp:
        if stamp not in complete:
            have = ", ".join(complete[:5]) or "none"
            raise InputError(f"no complete export set (a modlist and a loadorder file) with stamp {stamp} for the "
                             f"Vortex profile {profile} in {where} (newest stamps there: {have}). Fix: pass "
                             "--vortex-stamp with one of those stamps, or leave it out for the newest set.")
        return stamp, sets[stamp]
    if not complete:
        raise InputError(f"no complete export set (a modlist and a loadorder file with the same stamp) for the Vortex "
                         f"profile {profile} in {where}. Fix: check the profile name (as in the export file names), "
                         "pass --vortex-exports <the export folder>, or turn on Automatic Export in the Modlist "
                         "Backup extension and switch to the profile once.")
    return complete[0], sets[complete[0]]


def read_vortex_modlist(path, option="--vortex-modlist"):
    """[mod dict] from a Modlist Backup modlist export: a list of mods, each with a name or a vortexId."""
    bad = f"{path} isn't a Modlist Backup modlist export. Fix: pass {option} a vortex-starfield-<profile>-modlist file."
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except OSError as e:
        raise InputError(f"can't read {path}: {e.strerror}. Fix: check the file exists and you may read it.")
    except (ValueError, UnicodeDecodeError):
        raise InputError(bad + " (It isn't valid JSON.)")
    if not isinstance(d, list) or not all(isinstance(m, dict) and (m.get("name") or m.get("vortexId")) for m in d):
        raise InputError(bad + " (It should be a list of mods, each with a name.)")
    return d


def _enabled_mod_ids(modlist):
    return {str(m["vortexId"]).strip().lower() for m in modlist
            if m.get("enabled") is True and isinstance(m.get("vortexId"), (str, int)) and str(m["vortexId"]).strip()}


def _staging_copy(lo, low):
    """The plugin file in an enabled mod's staging folder, or None."""
    for m in lo.mods or []:
        if m.enabled and m.path and low in {p.lower() for p in m.plugins or []}:
            real = listing(m.path).get(low)
            if real:
                return os.path.join(m.path, real)
    return None


def _profile_mods(lo, modlist):
    """When Data holds another profile, make lo.mods this profile's mods: the deployment record marked the deployed
    profile's mods enabled (and added the ones with no plugin), and a staging folder found only through the record
    (stagingPath, e.g. a staging folder on another drive) left the export's own mods without folders. The modlist
    says which mods this profile enables; a mod it doesn't name stays enabled only when one of its plugins is."""
    staging = (lo.files or {}).get("vortex_staging")
    named, on = set(), {}
    for e in modlist:
        vid = e.get("vortexId")
        if not isinstance(vid, (str, int)) or isinstance(vid, bool) or not str(vid).strip():
            continue
        key = str(vid).strip().lower()
        named.add(key)
        if e.get("enabled") is True:
            on.setdefault(key, e)
    plugin_on = {p.name.lower() for p in lo.plugins if p.enabled}
    known = set()
    for m in lo.mods or []:
        key = (m.id or m.name).strip().lower()
        known.add(key)
        m.enabled = key in on if key in named else any(p.lower() in plugin_on for p in m.plugins or [])
        if m.path is None and m.id:
            m.path = paths.mod_folder(staging, m.id)
    added = 0
    for key, e in on.items():
        if key in known:
            continue
        raw = str(e["vortexId"])
        pl = [p for p in e.get("plugins") if isinstance(p, str)] if isinstance(e.get("plugins"), list) else []
        lo.mods.append(Mod(raw.strip(), True, path=paths.mod_folder(staging, raw), plugins=pl, id=raw.strip()))
        added += 1
    log.info(SECTION, f"vortex profile: mods now follow the modlist: {sum(1 for m in lo.mods if m.enabled)} enabled "
                      f"({added} added from the modlist), {sum(1 for m in lo.mods if m.enabled and m.path)} with a "
                      "staging folder")


def deployed_check(lo, modlist, plugins_export=None, live_plugins_txt=None):
    """{deployed, extra_plugins, extra_mods, missing_plugins[, plugins_txt_matches]} for a Vortex profile. The profile
    is the deployed one when Data holds no mod plugin it doesn't list and the deployment record names no mod it
    doesn't enable (Vortex removes other profiles' files on deploy). deployed is None when Data is unknown."""
    out = {"deployed": None, "extra_plugins": [], "extra_mods": [], "missing_plugins": []}
    if lo.data:
        listed = {p.name.lower() for p in lo.plugins}
        here = listing(lo.data)
        out["extra_plugins"] = sorted((real for low, real in here.items() if not is_official(low) and low not in listed),
                                      key=str.lower)
        dep = getattr(lo, "_vortex_deployment", None)
        if dep is not None:
            on = _enabled_mod_ids(modlist)
            out["extra_mods"] = sorted({mid.strip() for mid in dep.by_mod() if mid.strip().lower() not in on},
                                       key=str.lower)
        out["missing_plugins"] = [p.name for p in lo.plugins
                                  if p.enabled and not p.official and not p.dropped and p.path is None]
        out["deployed"] = not out["extra_plugins"] and not out["extra_mods"]
    if plugins_export and live_plugins_txt and os.path.isfile(live_plugins_txt):
        try:
            a = [(n.lower(), e) for n, e in read_plugins_list(plugins_export)]
            b = [(n.lower(), e) for n, e in read_plugins_list(live_plugins_txt)]
            out["plugins_txt_matches"] = a == b
        except InputError as e:
            log.info(SECTION, f"vortex profile: plugins file comparison skipped: {e}")
    return out


def from_vortex_profile(args, env, drives, registry):
    profile = args.vortex_profile
    stamp = getattr(args, "vortex_stamp", None)
    if stamp and not STAMP_RE.match(stamp):
        raise InputError(f"--vortex-stamp {stamp} isn't a stamp like 20261121-190400 (YYYYMMDD-HHMMSS, as in the "
                         "export file names). Fix: copy the stamp from an export file name.")
    if getattr(args, "vortex_exports", None):
        folders = list(args.vortex_exports)
        missing = [f for f in folders if not os.path.isdir(f)]
        if missing:
            raise InputError(f"--vortex-exports {missing[0]} is not a folder. Fix: give it the folder the Modlist "
                             "Backup extension exports to.")
    else:
        folders, why = paths.default_vortex_exports(env)
        if not folders:
            raise InputError(f"no Vortex export folder was found: {why}. Fix: pass --vortex-exports <the folder the "
                             "Modlist Backup extension exports to>, or turn on its Automatic Export.")
        log.info(SECTION, f"vortex exports: {', '.join(folders)} ({why})")
    stamp, files = find_vortex_set(profile, folders, stamp)
    modlist = read_vortex_modlist(files["modlist"], "--vortex-profile")
    lo, extra = from_vortex(files["loadorder"], args, env, drives, registry)
    lo.profile = profile
    lo.files["vortex_modlist"] = files["modlist"]
    if files.get("plugins"):
        lo.files["vortex_plugins_export"] = files["plugins"]
    lo._vortex_modlist = modlist  # not a dataclass field, so report.json never sees it
    live = paths.default_plugins_txt(env)
    check = deployed_check(lo, modlist, files.get("plugins"), live)
    vp = {"name": profile, "stamp": stamp, "files": {k: files.get(k) for k in ("modlist", "loadorder", "plugins")}}
    vp.update(check)
    lo._vortex_profile = vp
    log.info(SECTION, f"vortex profile {profile} {stamp}: deployed {vp['deployed']}, {len(vp['extra_plugins'])} extra "
                      f"plugin(s), {len(vp['extra_mods'])} extra mod(s), {len(vp['missing_plugins'])} missing")
    if vp["deployed"] is False:
        _profile_mods(lo, modlist)
        # the deployment record is the deployed profile's, so its "mods added from the record" note would name that
        # profile's mods as this one's; the caution below counts them as mods this profile doesn't enable
        rec = lo.files.get("vortex_deployment")
        extra = [f for f in extra if not (f.section == SECTION and "mods" in f.data and rec
                                          and f.data.get("record") == rec)]
        # point this profile's plugins at their staging folders (Data's copy, when there is one, is the deployed
        # profile's). That is a location only (the clashes check hashes it); no check reads these files, since
        # cli.NEEDS_DEPLOYED skips every check that would, and read() doesn't read their headers.
        moved = 0
        for p in lo.plugins:
            if not p.official:
                staged = _staging_copy(lo, p.name.lower())
                if staged:
                    p.path = staged
                    p.name = os.path.basename(staged)  # the export's lower case name, as the file is spelled
                    moved += 1
        log.info(SECTION, f"vortex profile: {moved} plugin(s) found in staging folders (not read)")
        n = len(vp["extra_plugins"]) + len(vp["extra_mods"])
        if getattr(args, "zips", None) is not None:  # candidate: it compares the build with the staging folders
            what = (". So the build is compared with this profile's mods in their staging folders, not with Data, "
                    "and the record check and the cleaning check (--depth heavy) don't run.")
        else:
            what = (". So only the plugin names and the known clashes were checked: the order, patch, archive, "
                    "loose-file, mod-rule, record and recommended-order checks were skipped, since they read the "
                    "profile's files and Data holds another profile's.")
        extra.insert(0, Finding(
            "caution", SECTION,
            f"Data holds another profile: {len(vp['extra_plugins'])} mod plugin(s) in Data aren't in the Vortex profile "
            f"{profile}" + (f", and Vortex's deployment record names {len(vp['extra_mods'])} mod(s) it doesn't enable"
                           if vp["extra_mods"] else "") + what + " Plugins missing from Data aren't listed as problems.",
            fix=f"Deploy the profile {profile} in Vortex and run again, or pass --data with a folder that holds this "
                "profile's files.",
            data={"extra_plugins": vp["extra_plugins"][:50], "extra_mods": vp["extra_mods"][:50], "count": n,
                  "missing_plugins": len(vp["missing_plugins"])}))
    if vp.get("plugins_txt_matches") is False and vp["deployed"] is not False:
        # (when Data holds another profile, the game's Plugins.txt is that profile's, so a difference says nothing)
        extra.append(Finding("note", SECTION, f"The export's plugins file ({os.path.basename(files['plugins'])}) "
                             f"differs from the game's Plugins.txt ({live}), so the export is older than the last "
                             "load order change.", fix="Switch to the profile in Vortex once so the extension exports "
                             "it again, or ignore this if you changed Plugins.txt on purpose."))
    return lo, extra


# ---------- source 3: MO2 ----------
def _mo2_ini(instance):
    """{key lower: value} from ModOrganizer.ini, with @ByteArray(...) unwrapped. The first value of a key wins."""
    out, path = {}, os.path.join(instance, "ModOrganizer.ini")
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                m = re.match(r"\s*([^=\[;#]+?)\s*=\s*(.*)$", line)
                if m:
                    v = m.group(2).strip()
                    b = re.match(r"@ByteArray\((.*)\)$", v)
                    v = (b.group(1) if b else v).replace("\\\\", "\\")
                    out.setdefault(m.group(1).lower(), v)
    except OSError as e:
        log.info(SECTION, f"no {path}: {e.strerror}")
    return out


def _mo2_dir(ini, key, instance, default):
    v = ini.get(key)
    if not v:
        return os.path.join(instance, default)
    base = ini.get("base_directory") or instance
    return os.path.normpath(v.replace("%BASE_DIR%", base))


def read_modlist(path):
    """[(name, enabled)] from MO2's modlist.txt, top line = highest priority. Separators and unmanaged entries
    (lines starting with *) are left out."""
    out = []
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line or line[0] not in "+-" or line[1:].endswith("_separator"):
                    continue
                out.append((line[1:], line[0] == "+"))
    except OSError as e:
        raise InputError(f"can't read {path}: {e.strerror}. Fix: check you may read the file.")
    return out


def from_mo2(instance, args, env, drives, registry):
    if not os.path.isdir(instance):
        raise InputError(f"no MO2 folder at {instance}. Fix: give --mo2 the MO2 instance folder, the one that holds "
                         "ModOrganizer.ini, profiles and mods.")
    ini = _mo2_ini(instance)
    ini_path = os.path.join(instance, "ModOrganizer.ini")
    profile = getattr(args, "profile", None) or ini.get("selected_profile")
    if not profile:
        raise InputError(f"no profile given and none selected in {ini_path}. Fix: pass --profile <name> (a folder "
                         f"in {os.path.join(instance, 'profiles')}).")
    profiles = _mo2_dir(ini, "profiles_directory", instance, "profiles")
    if not paths.safe_component(profile):
        raise InputError(f"the MO2 profile name {profile!r} isn't a single folder name (from "
                         f"{'--profile' if getattr(args, 'profile', None) else ini_path}). Fix: pass --profile with one "
                         f"of the folder names in {profiles}.")
    pdir = os.path.join(profiles, profile)
    ptxt = os.path.join(pdir, "plugins.txt")
    if not os.path.isdir(pdir):
        raise InputError(f"no MO2 profile folder {pdir}. Fix: pass --profile with one of the folder names in "
                         f"{profiles}.")
    if not os.path.isfile(ptxt):
        raise InputError(f"no plugins.txt in the MO2 profile folder {pdir}. Fix: open the profile in MO2 once so it "
                         "writes the file, or pass --profile with another profile.")
    mods_dir = _mo2_dir(ini, "mod_directory", instance, "mods")
    game = ini.get("gamepath")
    data, extra = _find_data(args, env, drives, registry, os.path.join(game, "Data") if game else None,
                             "MO2's gamePath")
    log.info(SECTION, f"mo2 {instance} profile {profile}; mods {mods_dir}")
    lo = LoadOrder("mo2", profile=profile, files={"plugins_txt": ptxt}, data=data)
    if os.path.isfile(ini_path):
        lo.files["modorganizer_ini"] = ini_path
    mlpath = os.path.join(pdir, "modlist.txt")
    if os.path.isfile(mlpath):
        lo.files["modlist"] = mlpath
        ml = read_modlist(mlpath)
        lo.mods = []
        for i, (name, on) in enumerate(ml):
            folder = os.path.join(mods_dir, name) if paths.safe_component(name) else None
            if folder and os.path.isdir(folder):
                lo.mods.append(Mod(name, on, path=folder, plugins=sorted(listing(folder).values()),
                                   priority=len(ml) - 1 - i))
            else:
                lo.mods.append(Mod(name, on, priority=len(ml) - 1 - i))
                if on and not paths.safe_component(name):
                    extra.append(Finding("caution", SECTION, f"MO2 mod {name!r} in modlist.txt isn't a single folder "
                                         "name, so its folder wasn't read.", fix="Remove that line from the profile's "
                                         "modlist.txt, or let MO2 write the file again."))
                elif on:
                    extra.append(Finding("caution", SECTION, f"MO2 mod {name} is enabled but has no folder in "
                                         f"{mods_dir}.", fix="Reinstall the mod in MO2, or disable it."))
        overwrite = _mo2_dir(ini, "overwrite_directory", instance, "overwrite")
        if os.path.isdir(overwrite):  # MO2's Overwrite folder wins over every mod
            lo.mods.append(Mod("Overwrite", True, path=overwrite, plugins=sorted(listing(overwrite).values()),
                               priority=len(ml)))
        lo.mods.sort(key=lambda m: m.priority)  # lowest first: the last one wins a loose-file conflict
    else:
        lo.mods_note = f"No modlist.txt in {pdir}, so mod folders and loose-file owners are unknown."
    # where each plugin file is: the highest-priority enabled mod folder that has it, else Data
    present = listing(data) if data else {}
    where = {n: _in(data, real) for n, real in present.items()}
    for m in lo.mods or []:
        if m.enabled and m.plugins:
            for p in m.plugins:
                present[p.lower()] = p
                where[p.lower()] = _in(m.path, p)
    listed, bad = drop_bad_names(read_plugins_list(ptxt), ptxt)
    extra += bad
    lopath = os.path.join(pdir, "loadorder.txt")
    if os.path.isfile(lopath):  # loadorder.txt holds the order (disabled plugins too); plugins.txt the flags
        lo.files["loadorder_txt"] = lopath
        on = {n.lower(): e for n, e in listed}
        lo_listed, bad = drop_bad_names(read_plugins_list(lopath), lopath)
        extra += bad
        order = [n for n, _ in lo_listed]
        known = {n.lower() for n in order}
        listed = [(n, on.get(n.lower(), False)) for n in order] + [(n, e) for n, e in listed if n.lower() not in known]
    lo.plugins, more = build_order(listed, present, where.get)
    return lo, extra + more


# ---------- the two entry points ----------
def _check_source_options(args):
    """Refuse option mixes that would silently ignore one of them."""
    g = lambda name: getattr(args, name, None)  # noqa: E731
    if g("vortex_profile"):
        other = [o for o, n in (("--plugins-txt", "plugins_txt"), ("--mo2", "mo2"),
                                ("--vortex-loadorder", "vortex_loadorder"), ("--vortex-modlist", "vortex_modlist"))
                 if g(n)]
        if other:
            raise InputError(f"--vortex-profile can't be combined with {other[0]}: give one load order source. Fix: "
                             f"leave out {other[0]} (the profile's export set holds the load order and the mod list).")
    else:
        for o, n in (("--vortex-exports", "vortex_exports"), ("--vortex-stamp", "vortex_stamp")):
            if g(n):
                raise InputError(f"{o} works only with --vortex-profile. Fix: add --vortex-profile <name>, or leave "
                                 f"out {o}.")
    if g("vortex_modlist") and not g("vortex_loadorder"):
        raise InputError("--vortex-modlist works only with --vortex-loadorder. Fix: add --vortex-loadorder <file>, "
                         "or use --vortex-profile <name>, which finds both files.")


def read(args, env=None, drives=None, registry=None):
    """(LoadOrder, None) or (None, error text). env, drives and registry are for tests (see paths.find_data)."""
    try:
        _check_source_options(args)
        if getattr(args, "vortex_profile", None):
            lo, extra = from_vortex_profile(args, env, drives, registry)
            why = "--vortex-profile"
        elif getattr(args, "mo2", None):
            lo, extra = from_mo2(args.mo2, args, env, drives, registry)
            why = "--mo2"
        elif getattr(args, "vortex_loadorder", None):
            lo, extra = from_vortex(args.vortex_loadorder, args, env, drives, registry)
            why = "--vortex-loadorder"
            if getattr(args, "vortex_modlist", None):
                lo._vortex_modlist = read_vortex_modlist(args.vortex_modlist)
                lo.files["vortex_modlist"] = args.vortex_modlist
        elif getattr(args, "plugins_txt", None):
            lo, extra = from_plugins_txt(args.plugins_txt, args, env, drives, registry)
            why = "--plugins-txt"
        else:
            default = paths.default_plugins_txt(env)
            if default is None:  # not Windows: the game's own file has no place here
                raise InputError("no load order was given, and the game's own Plugins.txt can't be found because "
                                 "LOCALAPPDATA isn't set (it is only on Windows). Fix: pass --plugins-txt <file>, "
                                 "--mo2 <folder> or --vortex-loadorder <file>, and --data <the game's Data folder>.")
            lo, extra = from_plugins_txt(default, args, env, drives, registry)
            why = "the game's own Plugins.txt (no source option given)"
    except InputError as e:
        log.info(SECTION, f"can't read the load order: {e}")
        return None, str(e)
    log.info(SECTION, f"source {lo.source} from {why}: {len(lo.plugins)} plugins, "
                      f"{len(lo.mods) if lo.mods is not None else 'no'} mods")
    vp = getattr(lo, "_vortex_profile", None) or {}
    # Data holds another profile: no check reads the mod plugins (cli.NEEDS_DEPLOYED), so their headers aren't read
    plugin_header.fill(lo, official_only=vp.get("deployed") is False)
    extra = list(extra) + test_files(lo, env)
    lo._inputs_extra = extra  # not a dataclass field, so report.json never sees it; findings() returns it
    return lo, None


def _ini_test_files(path):
    """The sTestFile1..10 values in the [General] section of an INI file, in order; [] when none or unreadable."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        log.info(SECTION, f"can't read {path}: {e}")
        return []
    found = {}
    section = None
    for line in lines:
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            section = t[1:-1].strip().lower()
        elif section == "general" and "=" in t and not t.startswith((";", "#")):
            key, _eq, val = t.partition("=")
            m = re.match(r"^stestfile(\d+)$", key.strip().lower())
            if m and val.strip():
                found[int(m.group(1))] = val.strip()
    return [found[k] for k in sorted(found) if 1 <= k <= 10]


def test_files(lo, env=None, documents=None):
    """One caution when StarfieldCustom.ini has sTestFile lines: the game (and MO2) load those files and skip
    Plugins.txt. The INI is the MO2 profile's own copy when the profile uses local INIs, else the one in
    Documents\\My Games\\Starfield: Windows's own Documents folder (documents; looked up in a real run, when env
    is None), then the one under the user profile and under OneDrive. [] when there is none."""
    if env is None:
        env = os.environ
        documents = paths.shell_documents() if documents is None else documents
    name = "StarfieldCustom.ini"
    candidates = []
    pt = lo.files.get("plugins_txt")
    if lo.source == "mo2" and pt:
        pdir = os.path.dirname(pt)
        local = False
        try:
            with open(os.path.join(pdir, "settings.ini"), encoding="utf-8-sig", errors="replace") as f:
                local = re.search(r"^\s*LocalSettings\s*=\s*true\s*$", f.read(), re.I | re.M) is not None
        except OSError:
            pass
        if local:
            candidates.append(os.path.join(pdir, name))
    if not candidates:
        docs = [documents] if documents else []
        docs += [os.path.join(env[v], "Documents") for v in ("USERPROFILE", "OneDrive") if env.get(v)]
        for d in docs:
            ini = os.path.join(d, "My Games", "Starfield", name)
            if os.path.normcase(ini) not in {os.path.normcase(c) for c in candidates}:
                candidates.append(ini)
    out = []
    for ini in candidates:
        if not os.path.isfile(ini):
            continue
        plugins = _ini_test_files(ini)
        if plugins:
            out.append(Finding(
                "caution", SECTION, f"{ini} has sTestFile lines ({', '.join(plugins)}), so the game (and MO2) load "
                "those files and skip Plugins.txt: the order this report checks may not be what the game loads.",
                fix=f"Remove the sTestFile lines from {ini} and use Plugins.txt (or your mod manager) for the load "
                "order.", plugins=plugins, data={"kind": "stestfile", "ini": ini, "plugins": plugins}))
            break
    return out


MO2_PINNED = {"starfield.esm", "constellation.esm", "oldmars.esm", "blueprintships-starfield.esm", "sfbgs007.esm",
              "sfbgs008.esm", "sfbgs006.esm", "sfbgs003.esm"}  # the official plugins MO2 2.5.2 pins


def mo2_unpinned_officials(lo):
    """For an MO2 source: the installed official plugins (names as on disk) that MO2 2.5.2 doesn't pin, so a plugins.txt
    that leaves them out can untick them. [] for any other source."""
    if lo.source != "mo2":
        return []
    return [p.name for p in lo.plugins if p.official and p.enabled and p.path and p.name.lower() not in MO2_PINNED]


def findings(lo):
    """[Finding] for section "inputs": names the game drops, enabled plugins whose file is missing (only when the
    Data folder is known), plus what read() noticed on the way."""
    out = []
    vp = getattr(lo, "_vortex_profile", None)
    other_deployed = bool(vp) and vp.get("deployed") is False  # one caution from read() covers missing files
    for p in lo.plugins:
        if p.dropped:
            # a PROBLEM only when it is meant to load; a disabled one is a note (it will be dropped if turned on)
            out.append(Finding("problem" if p.enabled else "note", SECTION,
                               f"{p.name}: Starfield removes this name from Plugins.txt (it ends in an official "
                               "plugin's name or starts with BlueprintShips-), so it never loads"
                               + ("." if p.enabled else ", even if you enable it."),
                               fix=f"Rename it so it doesn't, for example {_addon_name(p.name)}, and update any "
                               "plugin that has it as a master.", plugins=[p.name]))
        elif p.enabled and p.path is None and lo.data and not other_deployed:
            if lo.source == "vortex":
                fix = "Deploy in Vortex (it may be installed but not deployed), or reinstall the mod."
            elif lo.source == "mo2":
                fix = "Enable or reinstall the mod that holds it in MO2, or disable the plugin."
            else:
                fix = "Reinstall the mod that holds it, or remove the plugin from the load order."
            out.append(Finding("problem", SECTION, f"{p.name} is enabled but its file wasn't found"
                               + (" in any enabled mod folder or the Data folder." if lo.source == "mo2"
                                  else " in the Data folder."), fix=fix, plugins=[p.name]))
    return out + list(getattr(lo, "_inputs_extra", []))


def _addon_name(name):
    base, ext = os.path.splitext(name)
    if re.match(r"^blueprintships-", base, re.I):
        return base[len("BlueprintShips-"):] + "Ships" + ext
    return base + "Addon" + ext
