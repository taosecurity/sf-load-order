"""`sf-load-order candidate <zip>...`: check a new build against the load order it will join (section "candidate").

Before installing a mod, this compares the files its zip (or zips: the parts of one build, one plugin each) would
install with every enabled mod's files, loose and archived:

  - a script (.pex) path another mod also ships, identical or not, is a problem: whichever copy wins replaces the
    script for both mods;
  - any other shared path whose content differs is a problem: the load order (or, for loose files, the mod
    manager) decides which copy the game uses, and a loose copy beats every archive;
  - a path two parts of the build itself ship: a problem for scripts, a caution for the rest (each part becomes its
    own mod, so a stale part replaces the file for every part);
  - shared paths that couldn't be compared, and differences from a mod the build is built on (one of its plugins is
    a master of the build), are cautions;
  - identical copies and a master's files the build overrides are notes.

The installed copy of the build itself (a mod shipping a plugin with the same name as one of the build's plugins,
or a mod whose name is a build zip's name with another version, such as a loose-files part) is left out, since the
build replaces it. A zip packed with its mod's own folder (MyMod\\MyMod.esm) is read as if that folder were Data.

Depth: light compares names only (every shared path is a caution "not compared"); medium (default) compares
contents (the SHA-1 of each file's content; a .dds by its pixel data, as the archives check does); heavy adds the
cleaning check: sf-mod-check check on each build plugin (its masters must be installed in Data), where deleted
placed references and navmeshes are problems and ITM records a caution.

Reads only. The zip is never unpacked to its own member paths: loose files are hashed straight from the zip, each
.ba2 is copied to <out>\\work\\candidate\\<n>.ba2 and each plugin to <out>\\work\\candidate\\plugins\\<n>\\<name>
(a numbered folder, and a name checked to be a plain file name).
"""
import os
import re
import shutil
import struct
import zipfile
import zlib
from collections import defaultdict

from . import archives, log, loose, paths, sfmodcheck
from .model import Finding
from .plugin_header import header

SECTION = "candidate"
SAMPLES = 5
PLUGIN_EXT = (".esm", ".esp", ".esl")
NOT_ZIP = (".7z", ".rar")
DELETED_RISKY = {"REFR", "ACHR", "NAVM"}


class CandidateError(Exception):
    """A zip that can't be checked at all: the run stops with exit 2."""


def _raw_path(name):
    p = name.replace("/", "\\").strip("\\")
    while p.startswith(".\\"):
        p = p[2:]
    return p


def wrapper_folder(names):
    """The one top folder every member sits under when it isn't a game folder, Data or fomod (a zip packed with
    its mod's own folder, such as MyMod\\MyMod.esm), else None."""
    tops = set()
    for n in names:
        p = _raw_path(n)
        if not p:
            continue
        if "\\" not in p:
            return None
        tops.add(p.split("\\", 1)[0].lower())
        if len(tops) > 1:
            return None
    if len(tops) != 1:
        return None
    top = next(iter(tops))
    return None if top in loose.ROOTS or top in ("data", "fomod") else top


def _member_path(name, wrapper=None):
    """A zip member's game path: lower case, backslashes, a wrapper folder (see wrapper_folder) and then a leading
    Data\\ folder stripped; None for a folder."""
    p = _raw_path(name)
    if wrapper and p.lower().startswith(wrapper + "\\"):
        p = p[len(wrapper) + 1:]
    if p.lower() == "data" or p.lower().startswith("data\\"):
        p = p[5:]
    return p.lower() or None


def _hash_member(zf, info, path):
    """bytes_digest of one zip member (big non-DDS files are hashed in pieces)."""
    if archives.CONTENT_NAMED.match(path):
        return "name:" + path
    if path.endswith(".dds"):
        return archives.bytes_digest(path, zf.read(info))
    import hashlib
    h = hashlib.sha1()
    with zf.open(info) as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_zips(zips, work, deep):
    """What the build ships. Returns a dict:
    zips: [{path, files, loose, archives, plugins, fomod}]
    loose: {path: [(zip name, digest or None)]}
    archives: [{zip, name, copy}]   (each .ba2 copied to work\\<n>.ba2)
    plugins: [{zip, name, copy, masters}]
    Raises CandidateError for a missing file, a .7z/.rar, or a file that isn't a readable zip."""
    out = {"zips": [], "loose": defaultdict(list), "archives": [], "plugins": []}
    os.makedirs(work, exist_ok=True)
    for z in zips:
        if not os.path.isfile(z):
            raise CandidateError(f"{z} doesn't exist. Fix: give the path of the mod's .zip file.")
        if z.lower().endswith(NOT_ZIP):
            raise CandidateError(f"{os.path.basename(z)} is a {os.path.splitext(z)[1]} file, and candidate reads "
                                 ".zip files only. Fix: unpack it and pack the files again as a .zip.")
        if not zipfile.is_zipfile(z):
            raise CandidateError(f"{z} isn't a readable .zip file. Fix: download it again, or pack the mod's files "
                                 "as a .zip.")
        zname = os.path.basename(z)
        info_out = {"path": os.path.abspath(z), "files": 0, "loose": 0, "archives": [], "plugins": [],
                    "fomod": False}
        try:
            with zipfile.ZipFile(z) as zf:
                wrapper = wrapper_folder([i.filename for i in zf.infolist() if not i.is_dir()])
                if wrapper:
                    log.info(SECTION, f"{zname}: every file sits in the folder {wrapper}, read as the Data folder")
                for i in zf.infolist():
                    if i.is_dir():
                        continue
                    p = _member_path(i.filename, wrapper)
                    if not p:
                        continue
                    info_out["files"] += 1
                    base = p.rsplit("\\", 1)[-1]
                    if p == "fomod\\moduleconfig.xml":
                        info_out["fomod"] = True
                    if "\\" not in p and p.endswith(".ba2"):
                        n = len(out["archives"])
                        copy = os.path.join(work, f"{n}.ba2")
                        with zf.open(i) as src, open(copy, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
                        real = i.filename.replace("\\", "/").rsplit("/", 1)[-1]
                        out["archives"].append({"zip": zname, "name": real, "copy": copy})
                        info_out["archives"].append(real)
                        continue
                    if "\\" not in p and p.endswith(PLUGIN_EXT):
                        real = i.filename.replace("\\", "/").rsplit("/", 1)[-1]
                        n = len(out["plugins"])
                        copy = None
                        if paths.safe_component(real):
                            folder = os.path.join(work, "plugins", str(n))
                            os.makedirs(folder, exist_ok=True)
                            copy = os.path.join(folder, real)
                            with zf.open(i) as src, open(copy, "wb") as dst:
                                shutil.copyfileobj(src, dst, 1 << 20)
                        _kind, masters = header(copy) if copy else (None, None)
                        out["plugins"].append({"zip": zname, "name": real, "copy": copy, "masters": masters or []})
                        info_out["plugins"].append(real)
                        continue
                    if "\\" not in p or p.endswith(loose.SKIP_EXT) or loose.DOC.search(p) or \
                            base.startswith(loose.MARKER):
                        continue
                    info_out["loose"] += 1
                    out["loose"][p].append((zname, _hash_member(zf, i, p) if deep else None))
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as e:
            raise CandidateError(f"{z} couldn't be read ({type(e).__name__}: {e}). Fix: download it again, or "
                                 "pack the mod's files as a .zip again.")
        log.info(SECTION, f"{zname}: {info_out['files']} files, {info_out['loose']} loose, "
                          f"{len(info_out['archives'])} archive(s), {len(info_out['plugins'])} plugin(s)")
        out["zips"].append(info_out)
    return out


class _Named:
    def __init__(self, name):
        self.name = name


def build_archive_files(build, cache, deep):
    """({path: [(part, archive copy, digest or None)]}, [archives no build plugin loads]). An archive's part is the
    build plugin that loads it by the game's exact-name rule."""
    view = {a["name"].lower(): a["copy"] for a in build["archives"]}
    owned, taken = archives.assign([_Named(p["name"]) for p in build["plugins"]], lambda p: view)
    files = defaultdict(list)
    for plug, alist in owned.items():
        for full in alist:
            try:
                names = cache.names(full)
            except (OSError, ValueError, struct.error) as e:
                log.info(SECTION, f"can't read {full}: {e}")
                continue
            for n in names:
                files[n].append((plug, full, cache.digest(full, n) if deep else None))
    idle = [a["name"] for a in build["archives"] if a["copy"].lower() not in taken]
    return files, idle


VERSION_TAIL = re.compile(r"[-_ ]v?\d+(?:[.\-_]\d+)*$", re.I)


def mod_stem(name):
    """A zip or mod name without its folder, .zip and trailing version: ShipMod-Loose-8.8.40.zip and the Vortex
    mod ShipMod-Loose-8.8.40-1700000000 both give shipmod-loose."""
    n = os.path.basename(str(name or "").replace("\\", "/")).lower()
    if n.endswith(".zip"):
        n = n[:-4]
    return VERSION_TAIL.sub("", n)


def installed_files(lo, build_names, build_stems=()):
    """({path: [(owner, owner plugins, "loose"|"archive", full path)]}, replaced mod names, mods checked,
    mods with no folder). Mod managers: each enabled mod's folder (loose files, and the archives its enabled
    plugins load); a plain Plugins.txt: the Data folder's loose files and each enabled plugin's archives.
    A mod is the installed copy of the build (left out) when it ships one of the build's plugins, or when its name,
    id or folder has a build zip's name without the version (a part with no plugin)."""
    enabled = {n.lower() for n in lo.enabled()}
    stems = {s for s in build_stems if s}
    out, replaced, no_folder, checked = defaultdict(list), [], [], 0
    if lo.mods is not None:
        for m in lo.mods:
            if not m.enabled:
                continue
            plugs = list(m.plugins or [])
            if {p.lower() for p in plugs} & build_names or \
                    stems & {mod_stem(m.name), mod_stem(m.id), mod_stem(m.path)}:
                replaced.append(m.name)
                continue
            if not m.path or not os.path.isdir(m.path):
                no_folder.append(m.name)
                continue
            checked += 1
            _all, lf = loose.mod_files(m.path)
            for p, full in lf.items():
                out[p].append((m.name, plugs, "loose", full))
            on = [_Named(p) for p in plugs if p.lower() in enabled]
            view = archives.ba2s_in(m.path)
            owned, _taken = archives.assign(on, lambda p: view)
            for alist in owned.values():
                for full in alist:
                    out[("archive", full)] = (m.name, plugs)
        return out, replaced, checked, no_folder
    data = lo.data if lo.data and os.path.isdir(lo.data) else None
    if data:
        sub, _top = loose.data_files(data)
        for p, full in sub.items():
            if not (p.endswith(loose.SKIP_EXT) or loose.DOC.search(p)):
                out[p].append(("the Data folder (loose)", [], "loose", full))
    mine = [p for p in lo.plugins if p.enabled and not p.dropped and not archives.is_official(p) and p.path]
    for p in mine:
        if p.name.lower() in build_names:
            replaced.append(p.name)
            continue
        checked += 1
        view = archives.ba2s_in(os.path.dirname(p.path))
        owned, _taken = archives.assign([p], lambda _p: view)
        for full in owned.get(p.name, []):
            out[("archive", full)] = (p.name, [p.name])
    return out, replaced, checked, no_folder


def _expand_archives(inst, cache):
    """Turns the ("archive", full) keys of installed_files into per-path entries."""
    unreadable = []
    for key in [k for k in inst if isinstance(k, tuple)]:
        owner, plugs = inst.pop(key)
        full = key[1]
        try:
            names = cache.names(full)
        except (OSError, ValueError, struct.error) as e:
            unreadable.append(full)
            log.info(SECTION, f"can't read {full}: {e}")
            continue
        for n in names:
            inst[n].append((owner, plugs, "archive", full))
    return unreadable


def _ex(paths_):
    paths_ = sorted(paths_)
    return ", ".join(paths_[:SAMPLES]) + (f" and {len(paths_) - SAMPLES} more" if len(paths_) > SAMPLES else "")


def compare(build_paths, inst, masters, cache, depth):
    """{(level, kind, owner, where): [paths]} for every build path an installed mod also ships."""
    deep = depth != "light"
    groups = defaultdict(set)
    for p, ours in sorted(build_paths.items()):
        for owner, plugs, where, full in inst.get(p, ()):
            how = "unknown"
            if deep:
                if where == "loose":
                    if archives.CONTENT_NAMED.match(p):
                        theirs = "name:" + p
                    else:
                        try:
                            theirs = cache.file(full)["digest"]
                        except OSError:
                            theirs = None
                else:
                    theirs = cache.digest(full, p)
                mine = {d for d in ours if d}
                if theirs and mine and len(mine) == len(ours):
                    how = "same" if mine == {theirs} else "differ"
            built_on = any(x.lower() in masters for x in plugs)
            if built_on:
                key = ("caution", "master-differ") if how == "differ" else ("note", "master")
            elif p.endswith(".pex"):
                key = ("problem", "script")
            elif how == "differ":
                key = ("problem", "differ")
            elif how == "same":
                key = ("note", "same")
            else:
                key = ("caution", "light" if depth == "light" else "unknown")
            groups[key + (owner, where)].add(p)
    return groups


def _text(kind, owner, where, n, ex):
    a = "a loose" if where == "loose" else "an archived"
    if kind == "script":
        return (f"{n} script path(s) this build ships are also shipped by {owner} ({where}): whichever copy wins "
                f"replaces the script for both mods: {ex}.",
                "Give this build's scripts their own names (a prefix of its own), or leave out the copies the other "
                "mod already ships.")
    if kind == "differ":
        return (f"{owner} ships {a} copy of {n} path(s) this build ships, with different content: "
                + ("a loose copy beats every archive, so " if where == "loose" else "the load order decides ")
                + ("the mod manager's rules decide" if where == "loose" else "which one the game uses")
                + f": {ex}.",
                "Make sure the copy you want wins (plugin order for archives, the mod manager's rules for loose "
                "files), or give this build's files their own paths.")
    if kind == "master-differ":
        return (f"This build overrides {n} file(s) of {owner}, which it is built on, with different content "
                f"({where}): {ex}.", "Check that these overrides are meant.")
    if kind == "master":
        return f"This build overrides {n} file(s) of {owner}, which it is built on ({where}): {ex}.", None
    if kind == "same":
        return f"{owner} ships an identical {'loose' if where == 'loose' else 'archived'} copy of {n} path(s): {ex}.", None
    if kind == "light":
        return (f"{owner} ships {a} copy of {n} path(s) this build ships; --depth light compares names only: {ex}.",
                "Run again without --depth light to compare the contents.")
    return (f"{owner} ships {a} copy of {n} path(s) this build ships that couldn't be compared (a cube map, a file "
            f"that isn't a readable DDS, or a damaged or LZ4-packed archive): {ex}.",
            "Compare these files by hand, or install the python lz4 package if the archive is LZ4-packed.")


def own_parts(build, arch_files):
    """[(level, text, fix, paths)] for paths two parts of the build both ship."""
    parts = defaultdict(set)
    zip_of = {pl["name"]: pl["zip"] for pl in build["plugins"]}  # a part is a zip, whether loose or archived
    for p, entries in arch_files.items():
        for plug, _full, _d in entries:
            parts[p].add(zip_of.get(plug, plug))
    for p, entries in build["loose"].items():
        for zname, _d in entries:
            parts[p].add(zname)
    dup = sorted(p for p, s in parts.items() if len(s) > 1)
    out = []
    scripts = [p for p in dup if p.endswith(".pex")]
    rest = [p for p in dup if not p.endswith(".pex")]
    if scripts:
        out.append(("problem", f"{len(scripts)} script path(s) are shipped by more than one part of this build; each "
                    f"part becomes its own mod, so a stale part replaces the script for every part: {_ex(scripts)}.",
                    "Give each part its own scripts.", scripts))
    if rest:
        out.append(("caution", f"{len(rest)} path(s) are shipped by more than one part of this build: {_ex(rest)}.",
                    "Ship each file in one part only.", rest))
    return out


def _heavy(ctx, build, report):
    """sf-mod-check check on each build plugin. Returns {plugin: run_check dict} (or {"why": ...})."""
    args, lo = ctx.args, ctx.load_order
    add = report.add
    prog, err = sfmodcheck.find(args)
    if not err and prog is None:
        err = (f"sf-mod-check wasn't found on PATH. Fix: give --sf-mod-check the path of sf-mod-check.exe "
               f"(download it from {sfmodcheck.RELEASES}).")
    if not err:
        err = sfmodcheck.version(prog) or sfmodcheck.too_old(prog)
    if not err and not (lo.data and os.path.isdir(lo.data)):
        err = "no Data folder was found, and the cleaning check reads the build's masters there. Fix: pass --data."
    if err:
        add(Finding("caution", SECTION, "The cleaning check (--depth heavy) didn't run: " + err.split(" Fix: ")[0],
                    fix=err.split(" Fix: ")[1] if " Fix: " in err else None))
        return {"why": err}
    out = {}
    for p in build["plugins"]:
        name = p["name"]
        if not p["copy"]:
            add(Finding("caution", SECTION, f"The cleaning check skipped {name}: its name isn't a plain file name.",
                        plugins=[name]))
            continue
        r = sfmodcheck.run_check(prog, p["copy"], lo.data)
        out[name] = r
        if r["why"]:
            add(Finding("caution", SECTION, f"The cleaning check couldn't check {name}: {r['why']}.",
                        fix="It reads the build's masters from the Data folder: install them there (or pass --data), "
                            f"then run sf-mod-check check \"{name}\" yourself to see why.", plugins=[name]))
            continue
        risky = {k: v for k, v in r["deleted"].items() if k in DELETED_RISKY}
        other = {k: v for k, v in r["deleted"].items() if k not in DELETED_RISKY}
        if risky:
            what = ", ".join(f"{v} {k}" for k, v in sorted(risky.items()))
            add(Finding("problem", SECTION, f"{name} deletes records ({what}); a deleted placed reference or navmesh "
                        "that another mod or the game still uses can crash the game.",
                        fix="Disable the references instead (xEdit's Undelete and Disable References); replace a "
                            "deleted navmesh in the Creation Kit.", plugins=[name], data=dict(r["deleted"])))
        if other:
            what = ", ".join(f"{v} {k}" for k, v in sorted(other.items()))
            add(Finding("note", SECTION, f"{name} deletes other records ({what}).",
                        fix="Usually meant; check it if something from the game goes missing.", plugins=[name],
                        data=dict(other)))
        if r["itms"]:
            add(Finding("caution", SECTION, f"{name} has {r['itms']} record(s) identical to the version it overrides "
                        "(ITM); they change nothing but can undo other mods' edits to the same records.",
                        fix="Leave them out of the build (xEdit's QuickAutoClean removes them).", plugins=[name],
                        data={"itms": r["itms"]}))
        if r["errors"]:
            add(Finding("caution", SECTION, f"sf-mod-check check found {r['errors']} error(s) in {name}.",
                        fix=f"Run sf-mod-check check \"{name}\" to see them.", plugins=[name]))
        if not (risky or r["itms"] or r["errors"]):
            add(Finding("note", SECTION, f"The cleaning check found nothing to clean in {name} "
                        f"({r['records']} records, {r['overrides']} overrides).", plugins=[name]))
    return out


def run(ctx, zips):
    """Fills ctx.report (findings in section "candidate") and returns the report.json "candidate" dict.
    Raises CandidateError when a zip can't be read."""
    args, lo, report = ctx.args, ctx.load_order, ctx.report
    depth = getattr(args, "depth", None) or "medium"
    deep = depth != "light"
    work = os.path.join(ctx.work, "candidate")
    if os.path.isdir(work):
        shutil.rmtree(work)  # our own numbered copies from an earlier run
    build = read_zips(zips, work, deep)
    add = report.add
    if not build["plugins"] and not build["archives"] and not build["loose"]:
        add(Finding("caution", SECTION, "The zip holds no plugin, archive or game file, so there is nothing to "
                    "compare.", fix="Check that this is the mod's own zip (its files at the top, or in a Data "
                    "folder)."))
    for zi in build["zips"]:
        if zi["fomod"]:
            add(Finding("caution", SECTION, f"{os.path.basename(zi['path'])} has an installer (fomod), whose choices "
                        "aren't resolved here, so every file in it was checked, chosen or not.",
                        fix="Read the findings with that in mind."))
    cache = archives.Cache(archives.cache_path(args, "archives.json"))
    loose_cache = archives.Cache(archives.cache_path(args, "loose.json"))
    arch_files, idle = build_archive_files(build, cache, deep)
    if idle:
        add(Finding("caution", SECTION, f"{len(idle)} archive(s) in the build don't load with any of its plugins "
                    f"(the game's exact-name rule): {_ex(idle)}.",
                    fix="Name each archive \"<plugin name> - Main.ba2\" (or Textures.ba2, Main01.ba2 and so on).",
                    data={"archives": idle}))
    for lvl, text, fix, ps in own_parts(build, arch_files):
        add(Finding(lvl, SECTION, text, fix=fix, data={"paths": ps[:50]}))

    build_names = {p["name"].lower() for p in build["plugins"]}
    masters = {m.lower() for p in build["plugins"] for m in p["masters"]} - build_names
    inst, replaced, checked, no_folder = installed_files(
        lo, build_names, {mod_stem(z["path"]) for z in build["zips"]})
    unreadable = _expand_archives(inst, cache)
    build_paths = defaultdict(list)
    for p, entries in build["loose"].items():
        build_paths[p] += [d for _z, d in entries]
    for p, entries in arch_files.items():
        build_paths[p] += [d for _plug, _f, d in entries]
    shared = sorted(p for p in build_paths if p in inst)

    class _Both:  # loose digests come from the loose cache, archived ones from the archive cache
        def file(self, full):
            return loose_cache.file(full)

        def digest(self, full, name):
            return cache.digest(full, name)
    groups = compare(build_paths, inst, masters, _Both(), depth)
    for (lvl, kind, owner, where), ps in sorted(groups.items(), key=lambda kv: (
            ("problem", "caution", "note").index(kv[0][0]), kv[0][2].lower(), kv[0][1])):
        text, fix = _text(kind, owner, where, len(ps), _ex(ps))
        add(Finding(lvl, SECTION, text, fix=fix, data={"mod": owner, "where": where, "kind": kind,
                                                       "count": len(ps), "paths": sorted(ps)[:50]}))
    if no_folder:
        add(Finding("caution", SECTION, f"{len(no_folder)} enabled mod(s) have no folder to read, so their files "
                    f"weren't compared: {_ex(no_folder)}.",
                    fix="Pass --vortex-staging (Vortex) or check the MO2 mods folder."))
    if unreadable:
        add(Finding("caution", SECTION, f"{len(unreadable)} installed archive(s) couldn't be read: "
                    f"{_ex([os.path.basename(u) for u in unreadable])}.", fix="Reinstall the mod they belong to."))
    if lo.mods is None:
        add(Finding("note", SECTION, "With a plain Plugins.txt the Data folder's loose files can't be told apart by "
                    "mod, so a loose file of an installed copy of this build counts as shared with the Data folder.",
                    fix="Use --mo2 or --vortex-profile to compare mod by mod."))
    heavy = None
    if depth == "heavy":
        heavy = _heavy(ctx, build, report)
    cache.save()
    loose_cache.save()
    log.info(SECTION, f"{len(build_paths)} build path(s), {len(shared)} shared, {checked} mod(s) checked, "
                      f"{len(replaced)} replaced by the build")
    return {"zips": build["zips"], "plugins": [p["name"] for p in build["plugins"]],
            "masters": sorted(masters), "depth": depth, "replaced": replaced, "mods_checked": checked,
            "build_paths": len(build_paths), "shared": len(shared),
            "findings": {lv: sum(1 for f in report.findings if f.section == SECTION and f.level == lv)
                         for lv in ("problem", "caution", "note")},
            "heavy": heavy}
