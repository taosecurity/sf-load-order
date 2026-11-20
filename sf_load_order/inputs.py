"""Read the load order to check, from one of three sources, into model.LoadOrder.

  read(args)      -> (LoadOrder, None) or (None, "what went wrong, naming the file, and the fix")
  findings(lo)    -> [model.Finding] for section "inputs": enabled plugins whose file is missing, names the game
                     drops, and what read() noticed on the way (a plugin listed twice, no Data folder, no staging)

Source choice (the first one given wins):
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
    """An official master the game loads by itself. BlueprintShips-X.esm counts only when X is official."""
    n = name.lower()
    if OFFICIAL_RE.match(n):
        return True
    m = BLUEPRINT_RE.match(n)
    return bool(m and OFFICIAL_RE.match(m.group(1)))


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
                          "a / or \\, a first character of * or #, or a space at either end), so they were left out: "
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
    return [(str(e.get("name") or e.get("id")), bool(e.get("enabled")),
             str(e["modId"]) if e.get("modId") is not None else None) for e in d]


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
    for name, on, mod_id in entries:
        low = name.lower()
        mfolder = paths.child_folder(staging, mod_id) if has_staging and mod_id else None
        case = present.get(low) or (listing(mfolder).get(low) if mfolder else None) or name
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
def read(args, env=None, drives=None, registry=None):
    """(LoadOrder, None) or (None, error text). env, drives and registry are for tests (see paths.find_data)."""
    try:
        if getattr(args, "mo2", None):
            lo, extra = from_mo2(args.mo2, args, env, drives, registry)
            why = "--mo2"
        elif getattr(args, "vortex_loadorder", None):
            lo, extra = from_vortex(args.vortex_loadorder, args, env, drives, registry)
            why = "--vortex-loadorder"
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
    plugin_header.fill(lo)
    lo._inputs_extra = extra  # not a dataclass field, so report.json never sees it; findings() returns it
    return lo, None


def findings(lo):
    """[Finding] for section "inputs": names the game drops, enabled plugins whose file is missing (only when the
    Data folder is known), plus what read() noticed on the way."""
    out = []
    for p in lo.plugins:
        if p.dropped:
            # a PROBLEM only when it is meant to load; a disabled one is a note (it will be dropped if turned on)
            out.append(Finding("problem" if p.enabled else "note", SECTION,
                               f"{p.name}: Starfield removes this name from Plugins.txt (it ends in an official "
                               "plugin's name or starts with BlueprintShips-), so it never loads"
                               + ("." if p.enabled else ", even if you enable it."),
                               fix=f"Rename it so it doesn't, for example {_addon_name(p.name)}, and update any "
                               "plugin that has it as a master.", plugins=[p.name]))
        elif p.enabled and p.path is None and lo.data:
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
