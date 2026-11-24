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
import subprocess
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
DATA_OWNER = "the Data folder (loose)"  # a plain Plugins.txt: Data's loose files have no mod


class CandidateError(Exception):
    """A zip that can't be checked at all: the run stops with exit 2."""


def _raw_path(name):
    p = name.replace("/", "\\").strip("\\")
    while p.startswith(".\\"):
        p = p[2:]
    return p


def wrapper_folder(names):
    """The one top folder every member sits under when it isn't a game folder, Data or fomod (a zip packed with
    its mod's own folder, such as MyMod\\MyMod.esm), else None. A readme or other document at the top doesn't count."""
    tops = set()
    for n in names:
        p = _raw_path(n)
        if not p:
            continue
        if "\\" not in p:
            if loose.DOC.search(p.lower()):
                continue
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


def find_7z():
    """The 7-Zip command line program: 7z.exe (or 7z) on PATH, else in the 7-Zip folder of Program Files. None when not found."""
    for name in ("7z.exe", "7z"):
        found = shutil.which(name)
        if found:
            return found
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            cand = os.path.join(base, "7-Zip", "7z.exe")
            if os.path.isfile(cand):
                return cand
    return None


def _run_7z(exe, args):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.run([exe] + args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          errors="replace", timeout=600, creationflags=flags)


def _unsafe_member(name):
    p = name.replace("\\", "/")
    return p.startswith("/") or re.match(r"^[A-Za-z]:", p) is not None or ".." in p.split("/")


def _unpack(path, work, n):
    """Unpacks a .7z or .rar through 7-Zip into <work>/unpacked/<n> and packs the files as a stored zip, whose path it
    returns. Raises CandidateError when 7-Zip is missing, the archive has an unsafe member path or the unpack fails."""
    base = os.path.basename(path)
    ext = os.path.splitext(path)[1]
    exe = find_7z()
    if not exe:
        raise CandidateError(f"{base} is a {ext} file, and reading one needs 7-Zip (7z.exe), which wasn't found on PATH "
                             "or in the 7-Zip folder of Program Files. Fix: install 7-Zip from https://www.7-zip.org, or unpack the "
                             "file and pack it again as a .zip.")
    again = "Fix: download it again, or unpack it yourself and pack it again as a .zip."
    try:
        r = _run_7z(exe, ["l", "-slt", "-ba", "-p", "--", path])
    except (OSError, subprocess.SubprocessError) as e:
        raise CandidateError(f"{base} couldn't be listed with 7-Zip ({type(e).__name__}: {e}). {again}")
    if r.returncode != 0:
        last = ((r.stderr or r.stdout).strip().splitlines() or ["no message"])[-1]
        raise CandidateError(f"7-Zip couldn't list {base}: {last}. {again}")
    for line in r.stdout.splitlines():
        if line.startswith("Path = ") and _unsafe_member(line[7:]):
            raise CandidateError(f"{base} holds a file with an unsafe path ({line[7:]}), so it wasn't unpacked. "
                                 "Fix: get the mod from its author again, or unpack it yourself and pack it as a .zip.")
    dest = os.path.join(work, "unpacked", str(n))
    os.makedirs(dest, exist_ok=True)
    try:
        r = _run_7z(exe, ["x", "-y", "-p", "-o" + dest, "--", path])
    except (OSError, subprocess.SubprocessError) as e:
        raise CandidateError(f"{base} couldn't be unpacked with 7-Zip ({type(e).__name__}: {e}). {again}")
    if r.returncode != 0:
        last = ((r.stderr or r.stdout).strip().splitlines() or ["no message"])[-1]
        raise CandidateError(f"7-Zip couldn't unpack {base}: {last}. {again}")
    root = os.path.realpath(dest)
    out = os.path.join(work, "unpacked", f"{n}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for folder, _dirs, files in os.walk(dest):
            for f in files:
                full = os.path.join(folder, f)
                if os.path.islink(full):
                    continue
                if not os.path.realpath(full).startswith(root + os.sep):
                    raise CandidateError(f"{base} unpacked a file outside its folder ({full}). {again}")
                zf.write(full, os.path.relpath(full, dest).replace("\\", "/"))
    return out


def read_zips(zips, work, deep):
    """What the build ships. Returns a dict:
    zips: [{path, files, loose, archives, plugins, fomod}]
    loose: {path: [(zip name, digest or None)]}
    archives: [{zip, name, copy}]   (each .ba2 copied to work\\<n>.ba2)
    plugins: [{zip, name, copy, masters}]
    A .7z or .rar is unpacked through 7-Zip first (_unpack) and read as a stored zip; it keeps its own name.
    Raises CandidateError for a missing file, a .7z/.rar 7-Zip can't read, or a file that isn't a readable zip."""
    out = {"zips": [], "loose": defaultdict(list), "archives": [], "plugins": []}
    os.makedirs(work, exist_ok=True)
    for z in zips:
        if not os.path.isfile(z):
            raise CandidateError(f"{z} doesn't exist. Fix: give the path of the mod's .zip file.")
        fmt = "zip"
        src = z
        if z.lower().endswith(NOT_ZIP):
            fmt = os.path.splitext(z)[1].lower().lstrip(".")
            src = _unpack(z, work, len(out["zips"]))
        elif not zipfile.is_zipfile(z):
            raise CandidateError(f"{z} isn't a readable .zip file. Fix: download it again, or pack the mod's files "
                                 "as a .zip.")
        zname = os.path.basename(z)
        info_out = {"path": os.path.abspath(z), "format": fmt, "files": 0, "loose": 0, "archives": [], "plugins": [],
                    "fomod": False, "not_compared": 0}
        try:
            with zipfile.ZipFile(src) as zf:
                wrapper = wrapper_folder([i.filename for i in zf.infolist() if not i.is_dir()])
                if wrapper:
                    log.info(SECTION, f"{zname}: every file sits in the folder {wrapper}, read as the Data folder")
                fomod = any(_member_path(i.filename, wrapper) == "fomod\\moduleconfig.xml"
                            for i in zf.infolist() if not i.is_dir())
                info_out["fomod"] = fomod
                for i in zf.infolist():
                    if i.is_dir():
                        continue
                    p = _member_path(i.filename, wrapper)
                    if not p:
                        continue
                    info_out["files"] += 1
                    base = p.rsplit("\\", 1)[-1]
                    top = p.split("\\", 1)[0]
                    if fomod and "\\" in p and top != "fomod" and top not in loose.ROOTS:
                        # a fomod option folder (00 Core\...): where its files land depends on the installer's choices
                        info_out["not_compared"] += 1
                        continue
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
                out[p].append((DATA_OWNER, [], "loose", full))
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


def compare(build_paths, inst, masters, cache, depth, build_loose=None):
    """{(level, kind, owner, where): [paths]} for every build path an installed mod also ships. build_loose: the
    paths the build ships loose (None: the same kind as the installed copy). A differing pair is "differ" when
    both copies are loose or both archived, "wins" when only the build's copy is loose (it always replaces the
    other) and "hidden" when only the installed copy is loose (the build's copy never shows)."""
    deep = depth != "light"
    groups = defaultdict(set)
    master_archived = {p for p, entries in inst.items()  # a plain Plugins.txt: a master's paths in its archives
                       if any(w == "archive" and any(x.lower() in masters for x in pl) for _o, pl, w, _f in entries)}
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
                if theirs and ours and all(ours):  # every copy the build ships was read (copies may repeat)
                    how = "same" if set(ours) == {theirs} else "differ"
            built_on = any(x.lower() in masters for x in plugs) or (owner == DATA_OWNER and p in master_archived)
            if built_on:
                key = ("caution", "master-differ") if how == "differ" else ("note", "master")
            elif p.endswith(".pex"):
                key = ("problem", "script")
            elif how == "differ":
                ours_loose = (where == "loose") if build_loose is None else (p in build_loose)
                if ours_loose == (where == "loose"):
                    key = ("problem", "differ")
                else:
                    key = ("problem", "wins" if ours_loose else "hidden")
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
                + ("the mod manager's rules decide which loose copy is deployed" if where == "loose"
                   else "the load order decides which one the game uses")
                + f": {ex}.",
                "Make sure the copy you want wins (plugin order for archives, the mod manager's rules for loose "
                "files), or give this build's files their own paths.")
    if kind == "wins":
        return (f"{owner} ships an archived copy of {n} path(s) this build ships loose, with different content: a "
                f"loose copy beats every archive, so this build's copy always replaces {owner}'s: {ex}.",
                "Make sure that is meant, or give this build's files their own paths.")
    if kind == "hidden":
        return (f"{owner} ships a loose copy of {n} path(s) this build ships in an archive, with different content: "
                f"a loose copy beats every archive, so this build's copy never shows, whatever the load order: {ex}.",
                f"Ship these files loose and let them win over {owner}'s (the mod manager's rules), or give this "
                "build's files their own paths.")
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
    vp = getattr(ctx.report, "vortex_profile", None) or {}
    if vp.get("deployed") is False:  # the check reads the masters in Data, which are another profile's
        why = f"Data holds another profile, not {vp.get('name')}"
        add(Finding("caution", SECTION, f"The cleaning check (--depth heavy) didn't run: {why}, and it reads the "
                    "build's masters there.", fix=f"Deploy {vp.get('name')} in Vortex and run again."))
        return {"why": why}
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
    build_names = {b["name"].lower(): b["name"] for b in build["plugins"]}
    for p in build["plugins"]:
        name = p["name"]
        if not p["copy"]:
            add(Finding("caution", SECTION, f"The cleaning check skipped {name}: its name isn't a plain file name.",
                        plugins=[name]))
            continue
        own = sorted(build_names[m.lower()] for m in p["masters"] if m.lower() in build_names)
        if own:  # the check reads masters from Data, which holds an older copy of that part or none
            add(Finding("caution", SECTION, f"The cleaning check skipped {name}: its master "
                        f"{', '.join(own)} is a part of this build, and the check reads masters from the Data folder.",
                        fix=f"Install the build, then run sf-mod-check check \"{name}\" yourself.", plugins=[name]))
            out[name] = {"why": "a master is a part of this build"}
            continue
        r = sfmodcheck.run_check(prog, p["copy"], lo.data)
        out[name] = r
        if not r["why"] and r.get("records") is None:
            r["why"] = "its output couldn't be read (no summary line)"
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


REC_SKIPPED = "Records the build shares with installed mods weren't checked: "
REC_FIX_CONFLICT = ("Run it with --log to see why: sf-mod-check conflict --plugins <file> --data <folder> --extra-data "
                    "<folder> --log sf-mod-check-log.txt.")


def _rec_skip(report, why, fix=None, level="caution", **kw):
    """One finding saying the record check didn't run, and the report.json "records" dict for it."""
    report.add(Finding(level, SECTION, REC_SKIPPED + why.rstrip(".") + ".", fix=fix))
    log.info(SECTION, f"record check skipped: {why}")
    return dict({"status": "skipped", "why": why, "program": None, "exit": None, "result_line": None, "file": None,
                 "shared_records": 0, "mods": []}, **kw)


def _split_fix(err):
    return (err.split(" Fix: ")[0], err.split(" Fix: ")[1]) if " Fix: " in err else (err, None)


def _rec_plugins_txt(lo, build, replaced):
    """The Plugins.txt the record check runs: the load order without the installed copy of the build, then the
    build's plugins as active lines at the end (where a new mod lands)."""
    drop = {p["name"].lower() for p in build["plugins"]}
    gone = set(replaced or ())
    for m in (lo.mods or []):
        if m.name in gone:
            drop |= {p.lower() for p in (m.plugins or [])}
    drop |= {n.lower() for n in gone}  # a plain Plugins.txt: replaced holds plugin names
    lines = [ln for ln in sfmodcheck.plugins_txt_lines(lo) if ln.startswith("#") or ln.lstrip("*").lower() not in drop]
    todo, seen = [], set()
    for p in build["plugins"]:
        if p["copy"] and p["name"].lower() not in seen:
            seen.add(p["name"].lower())
            todo.append(p)
    placed = set()
    while todo:  # each build plugin after the build plugins it masters, else in the order given
        ready = [p for p in todo if not ({m.lower() for m in p["masters"]} & seen) - placed] or todo[:1]
        p = ready[0]
        todo.remove(p)
        placed.add(p["name"].lower())
        lines.append("*" + p["name"])
    return lines, drop


def _shared_records(cj, lo, build_names):
    """[{mod, plugin, records, forwardable, choice, no_effect, examples}] from conflicts.json's lost entries between a
    build plugin and an installed mod plugin (official files, other build plugins and the navmesh info map left out),
    most records first."""
    from . import inputs, records
    official = {(e.get("FileName") or "").lower() for e in cj.get("loadOrder") or [] if e.get("Official")}

    def is_off(name, flag=False):
        n = name.lower()
        return bool(flag) or n in official or inputs.is_official(name) or inputs.modcheck_official(n)
    modof = {}
    for m in (lo.mods or []):
        for p in (m.plugins or []):
            modof.setdefault(p.lower(), m.name)
    per = {}  # installed plugin (lower) -> {fk: {"labels": set, "edid", "type"}}
    names = {}
    for e in cj.get("lost") or []:
        loser, winner = e.get("Loser") or "", e.get("Winner") or ""
        lb, wb = loser.lower() in build_names, winner.lower() in build_names
        if lb == wb or records.is_navi(e.get("RecordType")):
            continue
        other, other_flag = (winner, e.get("WinnerOfficial")) if lb else (loser, e.get("LoserOfficial"))
        if is_off(other, other_flag):
            continue
        key = other.lower()
        names.setdefault(key, other)
        fk = e.get("formKey") or ""
        rec = per.setdefault(key, {}).setdefault(fk, {"labels": set(), "edid": e.get("EditorID"),
                                                       "type": e.get("RecordType") or "", "build_won": wb})
        rec["labels"] |= {records.label(f) for f in e.get("Fields") or []}
    out = []
    for key, recs in per.items():
        n = {"forwardable": 0, "choice": 0, "no_effect": 0,
             "build_wins": sum(1 for r in recs.values() if r["build_won"])}
        n["build_loses"] = len(recs) - n["build_wins"]
        for r in recs.values():
            if records.FORWARDABLE in r["labels"]:
                n["forwardable"] += 1
            elif records.CHOICE in r["labels"]:
                n["choice"] += 1
            else:
                n["no_effect"] += 1
        ex = [{"fk": fk, "edid": r["edid"], "type": r["type"]} for fk, r in sorted(recs.items())]
        out.append(dict({"mod": modof.get(key, names[key]), "plugin": names[key], "records": len(recs),
                         "examples": ex[:SAMPLES]}, **n))
    out.sort(key=lambda x: (-x["records"], x["mod"].lower(), x["plugin"].lower()))
    return out


def _records(ctx, build, replaced):
    """The record check (G02): sf-mod-check conflict with the build's plugins loading last (--extra-data), and the
    records they share with installed mods as findings. Returns the report.json candidate "records" dict, or None
    at --depth light."""
    from . import records
    args, lo, report = ctx.args, ctx.load_order, ctx.report
    add = report.add
    depth = getattr(args, "depth", None) or "medium"
    if depth == "light":
        add(Finding("note", SECTION, "--depth light compares file names only, so records the build shares with "
                    "installed mods weren't checked.", fix="Run without --depth light to compare records too."))
        return None
    plugs = [p for p in build["plugins"]]
    if not plugs:
        log.info(SECTION, "record check: the build has no plugin, so it has no records")
        return {"status": "skipped", "why": "the build has no plugin", "program": None, "exit": None,
                "result_line": None, "file": None, "shared_records": 0, "mods": []}
    for p in plugs:
        if not p["copy"]:
            add(Finding("caution", SECTION, f"The record check left out {p['name']}: its name isn't a plain file name.",
                        plugins=[p["name"]]))
    if not any(p["copy"] for p in plugs):
        return _rec_skip(report, "no plugin of the build has a plain file name")
    vp = getattr(report, "vortex_profile", None) or {}
    if vp.get("deployed") is False:
        return _rec_skip(report, f"Data holds another profile, not {vp.get('name')}, and the check reads the "
                         "installed mods' plugins there", fix=f"Deploy {vp.get('name')} in Vortex and run again.")
    prog, err = sfmodcheck.find(args)
    if not err and prog is None:
        err = (f"sf-mod-check wasn't found on PATH. Fix: give --sf-mod-check the path of sf-mod-check.exe "
               f"(download it from {sfmodcheck.RELEASES}).")
    if not err:
        err = sfmodcheck.version(prog) or sfmodcheck.too_old(prog)
    if err:
        why, fix = _split_fix(err)
        return _rec_skip(report, why, fix=fix, program=prog.to_dict() if prog else None)
    pd = prog.to_dict()
    if not (lo.data and os.path.isdir(lo.data)):
        return _rec_skip(report, "no Data folder was found, and the check reads the installed mods' plugins there",
                         fix="Pass --data.", program=pd)
    _lines, drop = _rec_plugins_txt(lo, build, replaced)
    hidden = [n for n in records._not_visible(lo) if n.lower() not in drop]
    if hidden and lo.source == "mo2":
        return _rec_skip(report, f"{len(hidden)} enabled plugin(s) aren't in {lo.data} (MO2 keeps mods in their own "
                         "folders, and only programs started from MO2 see them in Data)",
                         fix="Start sf-load-order from MO2 (add it as an executable) and run candidate there.",
                         program=pd)
    work = os.path.join(ctx.work, "candidate")
    extra = os.path.join(work, "extra")
    os.makedirs(extra, exist_ok=True)
    done = set()
    for p in plugs:
        if p["copy"] and p["name"].lower() not in done:  # two zips with one plugin name: the first (the archive-name
            done.add(p["name"].lower())                   # problem already covers the clash)
            shutil.copyfile(p["copy"], os.path.join(extra, p["name"]))
    ptxt = os.path.join(work, "plugins.txt")
    with open(ptxt, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(_lines) + "\n")
    r = sfmodcheck.run_conflict(prog, ptxt, lo.data, os.path.join(work, "conflict"), getattr(args, "max_memory", None),
                                timeout=getattr(args, "sf_mod_check_timeout", None) or sfmodcheck.CONFLICT_TIMEOUT,
                                extra=extra, no_assets=True)
    kw = {"program": pd, "exit": r.exit, "result_line": r.result}
    failed = None
    if getattr(r, "refused", False):
        failed = (f"sf-mod-check wasn't run: {r.start_error}",
                  "Give --sf-mod-check the sf-mod-check.exe itself (not the .cmd), or use folders whose names hold "
                  "none of & | < > ^ % ! \" for the output (--out) and the game.")
    elif r.start_error:
        failed = (f"sf-mod-check couldn't be started ({r.start_error})",
                  "Check the --sf-mod-check path, or reinstall sf-mod-check.")
    elif r.timed_out:
        failed = (f"sf-mod-check conflict didn't finish within {int(r.seconds)} seconds and was stopped",
                  "Run again with --depth light, or install the build and run sf-load-order on the load order.")
    elif r.exit == 3:
        failed = (f"sf-mod-check conflict stopped at the memory limit ({r.result or 'RESULT: STOPPED'})",
                  "Close other programs and run again with a higher --max-memory (or none).")
    elif r.exit == 2:
        failed = (f"sf-mod-check conflict couldn't run ({r.result or 'no RESULT line'})", REC_FIX_CONFLICT)
    elif r.exit != 0:
        failed = (f"sf-mod-check conflict ended with exit code {r.exit} ({r.result or 'no RESULT line'})",
                  REC_FIX_CONFLICT)
    elif not r.conflicts:
        failed = ("sf-mod-check conflict said it finished but wrote no conflicts.json", REC_FIX_CONFLICT)
    cj = None
    if not failed:
        cj, err = records.load(r.conflicts)
        if err:
            failed = _split_fix(err)
    if failed:
        add(Finding("caution", SECTION, REC_SKIPPED + failed[0].rstrip(".") + ".", fix=failed[1]))
        log.info(SECTION, f"record check failed: {failed[0]}")
        return dict({"status": "failed", "why": failed[0], "file": None, "shared_records": 0, "mods": []}, **kw)
    names = {p["name"].lower() for p in plugs if p["copy"]}
    mods = _shared_records(cj, lo, names)
    loaded = sum(1 for e in cj.get("loadOrder") or [] if not e.get("Official")
                 and (e.get("FileName") or "").lower() not in names)
    for m in mods:
        who = m["mod"] if m["mod"].lower() == m["plugin"].lower() else f"{m['mod']} ({m['plugin']})"
        ex = ", ".join(f"{x['edid'] or x['fk']} ({x['type']})" for x in m["examples"])
        data = dict({"kind": "shared-records"}, **m)
        if not m["build_loses"]:
            side = f"Loading after it, the build wins them, so {m['mod']}'s changes to them are lost"
        elif not m["build_wins"]:
            side = f"{m['mod']} loads after the build and wins them, so the build's changes to them are lost"
        else:
            side = (f"The build wins {m['build_wins']} of them ({m['mod']}'s changes are lost) and {m['mod']} wins "
                    f"{m['build_loses']} (the build's changes are lost)")
        if m["forwardable"] or m["choice"]:
            add(Finding("caution", SECTION, f"This build changes {m['records']} record(s) that {who} also changes. "
                        f"{side}: "
                        f"{m['forwardable']} with fields a conflict patch can forward (Forwardable), {m['choice']} "
                        f"where both mods changed the same field (Choice). Examples: {ex}.",
                        fix="For Forwardable fields, build a conflict patch (sf-mod-check patch) after installing the "
                            "build; for Choice fields, decide which mod should win and order the plugins so it loads "
                            "last.", plugins=[m["plugin"]], data=data))
        else:
            add(Finding("note", SECTION, f"This build changes {m['records']} record(s) that {who} also changes, with "
                        f"no difference that matters (No effect). Examples: {ex}.", plugins=[m["plugin"]], data=data))
    if not mods:
        add(Finding("note", SECTION, "The build's plugins share no changed records with the installed mods "
                    f"(sf-mod-check conflict, {loaded} mod plugin(s) loaded)."))
    total = sum(m["records"] for m in mods)
    log.info(SECTION, f"record check: {total} shared record(s) with {len(mods)} installed plugin(s), {loaded} mod "
                      f"plugin(s) loaded")
    return dict({"status": "done", "why": None, "file": r.conflicts, "shared_records": total, "mods": mods}, **kw)


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
        if zi["fomod"] and zi["not_compared"]:
            add(Finding("caution", SECTION, f"{os.path.basename(zi['path'])} has an installer (fomod), whose choices "
                        f"aren't resolved here, so the {zi['not_compared']} file(s) in its option folders weren't "
                        "compared (only files at its top, in a Data folder or in a game folder were).",
                        fix="Install it with Vortex or MO2 and run sf-load-order on the profile, or check a zip "
                        "packed without the installer."))
        elif zi["fomod"]:
            add(Finding("caution", SECTION, f"{os.path.basename(zi['path'])} has an installer (fomod), whose choices "
                        "aren't resolved here, so every file in it was checked, chosen or not.",
                        fix="Read the findings with that in mind."))
    cache = archives.Cache(archives.cache_path(args, "archives.json"))
    loose_cache = archives.Cache(archives.cache_path(args, "loose.json"))
    arch_files, idle = build_archive_files(build, cache, deep)
    zips_of = defaultdict(set)
    for a in build["archives"]:
        zips_of[a["name"].lower()].add(a["zip"])
    dupes = {n: sorted(z) for n, z in zips_of.items() if len(z) > 1}
    if dupes:  # only one copy of a name can be in Data, so the other part's copy is lost
        names = sorted({a["name"] for a in build["archives"] if a["name"].lower() in dupes})
        add(Finding("problem", SECTION, f"{len(dupes)} archive name(s) are in more than one zip of this build, and "
                    f"only one copy of a name can be installed: {_ex(names)} (in "
                    f"{'; '.join(', '.join(z) for z in dupes.values())}).",
                    fix="Ship each archive in one zip only, and pass only the current version of each part.",
                    data={"archives": dupes}))
        idle = [n for n in idle if n.lower() not in dupes]
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
    if replaced:
        add(Finding("note", SECTION, f"{len(replaced)} installed mod(s) or plugin(s) were left out as this build's own "
                    f"older copy (they ship one of its plugins, or have a build zip's name): {_ex(replaced)}.",
                    fix="If one of them isn't a copy of this build, give the build's zips a more specific name.",
                    data={"replaced": replaced}))
    unsorted = lo.mods is None and bool(replaced)
    if unsorted:  # a plain Plugins.txt: the older copy's loose files in Data look like any other mod's
        for p in [k for k in inst if not isinstance(k, tuple)]:
            del inst[p]
        add(Finding("caution", SECTION, f"This build's older copy ({_ex(replaced)}) is installed, and with a plain "
                    "Plugins.txt its loose files in Data can't be told apart from other mods', so Data's loose files "
                    "weren't compared with the build.",
                    fix="Use --mo2 or --vortex-profile to compare mod by mod, or remove the older copy and run again.",
                    data={"kind": "data-unsorted", "replaced": replaced}))
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
    groups = compare(build_paths, inst, masters, _Both(), depth, set(build["loose"]))
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
    if lo.mods is None and not unsorted:
        add(Finding("note", SECTION, "With a plain Plugins.txt the Data folder's loose files can't be told apart by "
                    "mod, so a master's loose file counts as shared with the Data folder unless one of the master's "
                    "archives ships the same path.",
                    fix="Use --mo2 or --vortex-profile to compare mod by mod."))
    rec = _records(ctx, build, replaced)
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
            "heavy": heavy, "records": rec}
