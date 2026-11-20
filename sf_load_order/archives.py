"""Archive overlap: files inside mods' .ba2 archives that overwrite each other (section "archives").

Mods pack textures, meshes, sounds and scripts in .ba2 archives. When two enabled plugins' archives hold a file with
the same path, the game uses the copy from the archive that loads last, and archives follow plugin order. A loose
file in the Data folder beats every archive (loose.py checks those). Vortex's own conflict check compares loose
files only, so it never reports archive clashes.

Which archives load with a plugin (the game's exact-name rule): for each enabled plugin the game registers only
  <plugin> - Main.ba2, then <plugin> - Main01.ba2, Main02 ... up to 99, stopping at the first missing number
  <plugin> - Textures.ba2, then <plugin> - Textures01.ba2 ... the same way
  <plugin> - Voices_en.ba2
A name already taken by an earlier plugin is skipped. An archive that starts with an enabled plugin's name but
doesn't fit ("<plugin> - Meshes.ba2", a Main03 after a missing Main02) won't load with that plugin. Console copies
("_xbox", "_ps") and other languages' voices are left out on purpose: the PC game never loads them for English.
Base-game and DLC archives aren't mod archives: replacing their files is what a mod is for.

Where archives are looked for: next to where each plugin was found (its Data folder, or its Vortex staging folder),
and for MO2 in the merged view MO2 gives the game (the Data folder, then each enabled mod's folder by priority, the
higher priority winning a name).

Each pair of plugins sharing archive paths gets one piece of advice:
  built     the winner lists the loser as a master, directly or through its masters: it's meant to win
  samemod   both plugins come from the same mod
  same      every shared file has the same content in both archives: order changes nothing
  overlap   unrelated mods ship the same path with different (or uncomparable) contents: order decides
Contents are compared by the SHA-1 of each shared file's unpacked content: general (GNRL) archives stored or
zlib-packed, texture (DX10) archives by their pixel data, and LZ4-packed v3 archives only when the python "lz4"
package is installed (else "can't compare"). Geometry files are named by their content's hash, so a shared
geometries\\<hash>\\<hash>.mesh path is the same file by definition.

Cache: each archive's name list and the digests worked out, keyed by full path + size + modification time, in
<state>\\cache\\archives.json (--state, default %LOCALAPPDATA%\\sf-load-order); --no-state keeps it in memory only.
"""
import hashlib
import json
import os
import re
import struct
import zlib
from collections import defaultdict

from . import log, paths
from .model import Finding

SECTION = "archives"
CACHE_VERSION = 1
SAMPLES = 6
HEADER_SIZE = {1: 24, 7: 24, 8: 24, 2: 32, 3: 36}  # BA2 header size by version (v2 adds 8 bytes, v3 12)
LANGUAGE = "en"
OTHER_LANG = re.compile(r" - voices_(?!en(_xbox|_ps)?\.ba2$)[a-z]+(_xbox|_ps)?\.ba2$", re.I)
CONSOLE = re.compile(r"_(xbox|ps)\.ba2$", re.I)
NUMBERED = re.compile(r" - (main|textures)\d\d\.ba2$", re.I)
# Starfield names geometry files by the SHA-1 of their content: the same path means the same file.
CONTENT_NAMED = re.compile(r"^geometries\\[0-9a-f]{20}\\[0-9a-f]{20}\.mesh$")

# Official plugins the game always loads (base game, DLC and the free Creations), lower case.
BASE = {"starfield.esm", "constellation.esm", "oldmars.esm", "shatteredspace.esm", "sfbgs003.esm", "sfbgs004.esm",
        "sfbgs006.esm", "sfbgs007.esm", "sfbgs008.esm", "sfbgs00d.esm", "sfbgs047.esm", "sfbgs050.esm",
        "blueprintships-starfield.esm", "blueprintships-sfbgs050.esm"}
# Archive names that belong to the game itself (its own archives, DLC, free Creations, official BlueprintShips files,
# the Creation Kit's shader archive): "<official stem> - ", matched exactly, so "ConstellationRedux - Main.ba2" or
# "SFBGS003Patch - Main.ba2" (a mod's) don't count.
BASE_ARCHIVE = re.compile(r"^(starfield|constellation|oldmars|shatteredspace|sfbgs[0-9a-z]{3}|creationkit|"
                          r"blueprintships-(starfield|constellation|oldmars|shatteredspace|sfbgs[0-9a-z]{3})) - ", re.I)
LZ4_NOTE = ("Some shared files are LZ4-packed (v3 archives) and the python lz4 package isn't installed, so their "
            "contents weren't compared. Install it with: python -m pip install lz4")

try:  # optional: LZ4-packed archives are compared only when it's installed
    import lz4.block as _LZ4
except ImportError:
    _LZ4 = None


# ---------------------------------------------------------------- reading BA2 files

def ba2_names(path):
    """The file paths stored in a BA2 (any version, GNRL or DX10), lower case with backslashes. Reads only the
    header and the name table."""
    with open(path, "rb") as f:
        head = f.read(24)
        if len(head) < 24 or head[:4] != b"BTDX":
            raise ValueError("not a BA2 archive (no BTDX header)")
        count = struct.unpack_from("<I", head, 12)[0]
        name_ofs = struct.unpack_from("<Q", head, 16)[0]
        if not name_ofs:
            raise ValueError("the archive has no name table")
        f.seek(name_ofs)
        data = f.read()
    out, pos = [], 0
    for _ in range(count):
        if pos + 2 > len(data):
            raise ValueError("the name table is cut short")
        n = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        out.append(data[pos:pos + n].decode("utf-8", errors="replace").replace("/", "\\").lower())
        pos += n
    return out


def ba2_records(path, names):
    """{path: [offset, packed size, unpacked size]} for a GNRL (general) archive, {} for anything else or anything
    odd. Each record is 36 bytes after the header: name hash, extension, folder hash, flags, offset (u64), packed
    size, unpacked size, 0xBAADF00D."""
    out = {}
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(36)
        ver, kind, count = struct.unpack_from("<I4sI", head, 4)
        hs = HEADER_SIZE.get(ver)
        if kind != b"GNRL" or hs is None or len(names) != count:
            return {}
        f.seek(hs)
        table = f.read(36 * count)
    if len(table) < 36 * count:
        return {}
    for i, n in enumerate(names):
        _nh, _ext, _dh, _fl, off, pk, up, al = struct.unpack_from("<I4sIIQIII", table, 36 * i)
        if al != 0xBAADF00D or off + (pk or up) > size:
            return {}
        out[n] = [off, pk, up]
    return out


def ba2_textures(path, names):
    """{path: {"chunks": [[offset, packed, unpacked], ...], "cube": bool}} for a DX10 (texture) archive, {} for
    anything else. Each file is a 24-byte record (name hash, extension, folder hash, a byte, chunk count, chunk
    header size 24, height, width, mip count, format, cube map flag, tile mode), then 24 bytes per chunk (offset
    u64, packed size, unpacked size, first and last mip, 0xBAADF00D)."""
    out = {}
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(36)
        ver, kind, count = struct.unpack_from("<I4sI", head, 4)
        hs = HEADER_SIZE.get(ver)
        if kind != b"DX10" or hs is None or len(names) != count:
            return {}
        name_ofs = struct.unpack_from("<Q", head, 16)[0]
        f.seek(hs)
        table = f.read(max(0, name_ofs - hs))
    pos = 0
    for n in names:
        if pos + 24 > len(table):
            return {}
        _nh, _ext, _dh, _u, nch, chsz, _h, _w, _m, _fmt, cube, _tile = struct.unpack_from("<I4sIBBHHHBBBB", table, pos)
        pos += 24
        if chsz != 24:
            return {}
        chunks = []
        for _ in range(nch):
            if pos + 24 > len(table):
                return {}
            off, pk, up, _s, _e, al = struct.unpack_from("<QIIHHI", table, pos)
            pos += 24
            if al != 0xBAADF00D or off + (pk or up) > size:
                return {}
            chunks.append([off, pk, up])
        out[n] = {"chunks": chunks, "cube": bool(cube)}
    return out


def ba2_lz4(path):
    """True when a v3 archive's header says LZ4 (compression method 3); v1 and v2 archives use zlib."""
    with open(path, "rb") as f:
        head = f.read(36)
    return (len(head) >= 36 and struct.unpack_from("<I", head, 4)[0] == 3
            and struct.unpack_from("<I", head, 32)[0] == 3)


class NeedLZ4(Exception):
    """The content is LZ4-packed and the lz4 package isn't installed."""


def _unpack(raw, pk, up, lz4):
    if not pk:
        return raw
    if lz4:
        if _LZ4 is None:
            raise NeedLZ4()
        try:
            out = _LZ4.decompress(raw, uncompressed_size=up)
        except Exception as x:  # lz4's own error type isn't a ValueError
            raise ValueError(f"LZ4: {x}") from x
    else:
        out = zlib.decompress(raw)
    if len(out) != up:
        raise ValueError("unpacked size mismatch")
    return out


def dds_pixels(b):
    """The pixel data of a DDS file (after its 128-byte header, or 148 with a DX10 extension), or None for a cube
    map or a file that isn't a DDS. Texture archives store exactly this data, split into chunks."""
    if len(b) < 128 or b[:4] != b"DDS ":
        return None
    if struct.unpack_from("<I", b, 112)[0] & 0x200:  # cube map: faces are laid out differently in archives
        return None
    return b[148:] if b[84:88] == b"DX10" else b[128:]


def bytes_digest(name, b):
    """The comparable digest of one file's content: for a .dds, "tex:" + the SHA-1 of its pixel data (so a loose
    DDS, a DDS in a general archive and a texture-archive copy compare equal); else the SHA-1 of the bytes."""
    if name.lower().endswith(".dds"):
        px = dds_pixels(b)
        return None if px is None else "tex:" + hashlib.sha1(px).hexdigest()
    return hashlib.sha1(b).hexdigest()


def file_digest(path):
    """bytes_digest of a loose file on disk (big non-DDS files are hashed in pieces)."""
    if path.lower().endswith(".dds"):
        with open(path, "rb") as f:
            return bytes_digest(path, f.read())
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _general_digest(path, rec, name, lz4):
    off, pk, up = rec
    with open(path, "rb") as f:
        f.seek(off)
        raw = f.read(pk or up)
    return bytes_digest(name, _unpack(raw, pk, up, lz4))


def _texture_digest(path, tex, lz4):
    if tex["cube"]:
        return None
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for off, pk, up in tex["chunks"]:
            f.seek(off)
            h.update(_unpack(f.read(pk or up), pk, up, lz4))
    return "tex:" + h.hexdigest()


# ---------------------------------------------------------------- cache

def cache_path(args, name):
    """<state>\\cache\\<name>, or None with --no-state (memory only)."""
    if getattr(args, "no_state", False):
        return None
    state = getattr(args, "state", None) or paths.default_state()  # the same folder as the patches snapshot
    return os.path.join(state, "cache", name)


class Cache:
    """A JSON cache of per-file facts, keyed by full path, checked against size + modification time.

    Archives: name list, records, LZ4 flag and the digests asked for. Loose files: CRC32 and digest."""

    def __init__(self, path=None):
        self.path = path
        self.d = {}
        self.changed = False
        self.lz4_missing = set()  # archives whose LZ4-packed files couldn't be compared
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and d.get("v") == CACHE_VERSION:
                    self.d = d.get("files", {})
            except (OSError, ValueError):
                pass

    @staticmethod
    def _key(full):
        st = os.stat(full)
        return os.path.normcase(os.path.abspath(full)), f"{st.st_size}:{st.st_mtime_ns}"

    def _get(self, full, kind):
        k, stamp = self._key(full)
        c = self.d.get(kind + "|" + k)
        if c and c.get("stamp") == stamp:
            return c, None
        return None, (kind + "|" + k, stamp)

    def archive(self, full):
        c, new = self._get(full, "a")
        if c:
            return c
        names = ba2_names(full)
        c = {"stamp": new[1], "names": names, "recs": ba2_records(full, names), "tex": ba2_textures(full, names),
             "lz4": ba2_lz4(full), "digest": {}}
        self.d[new[0]] = c
        self.changed = True
        return c

    def names(self, full):
        return self.archive(full)["names"]

    def digest(self, full, name):
        """Digest of one archived file's unpacked content, or None when it can't be compared. Never raises for a
        damaged archive."""
        if CONTENT_NAMED.match(name):
            return "name:" + name
        try:
            c = self.archive(full)
            if name in c["digest"]:
                return c["digest"][name]
            rec, tex = c["recs"].get(name), c["tex"].get(name)
            try:
                d = (_general_digest(full, rec, name, c["lz4"]) if rec else
                     _texture_digest(full, tex, c["lz4"]) if tex else None)
            except NeedLZ4:
                self.lz4_missing.add(full)
                return None  # not cached: installing lz4 later makes it comparable
        except (OSError, ValueError, struct.error, IndexError, zlib.error):
            return None
        c["digest"][name] = d
        self.changed = True
        return d

    def file(self, full):
        """{"crc": CRC32, "size": bytes, "digest": bytes_digest or None} of a loose file, worked out once."""
        c, new = self._get(full, "f")
        if c:
            return c
        crc, size = 0, 0
        h = hashlib.sha1()
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                crc = zlib.crc32(chunk, crc)
                size += len(chunk)
                h.update(chunk)
        if full.lower().endswith(".dds"):
            try:
                dg = file_digest(full)
            except (OSError, ValueError, struct.error):
                dg = None
        else:
            dg = h.hexdigest()
        c = {"stamp": new[1], "crc": crc & 0xFFFFFFFF, "size": size, "digest": dg}
        self.d[new[0]] = c
        self.changed = True
        return c

    def save(self):
        if not (self.changed and self.path):
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": CACHE_VERSION, "files": self.d}, f)
            os.replace(tmp, self.path)
            self.changed = False
        except OSError as e:
            log.info(SECTION, f"can't write the cache {self.path}: {e}")


def compare_loose(cache, path, archived):
    """"same", "differ" or "unknown" for a loose file on disk against an archived copy's digest."""
    if not archived:
        return "unknown"
    if archived.startswith("name:"):
        return "same"
    try:
        ld = cache.file(path)["digest"]
    except OSError:
        return "unknown"
    return "unknown" if not ld else ("same" if ld == archived else "differ")


# ---------------------------------------------------------------- which archives load

def is_official(p):
    return bool(p.official) or p.name.lower() in BASE


def is_base_archive(name, official_stems=()):
    n = name.lower()
    return bool(BASE_ARCHIVE.match(n)) or any(n.startswith(s + " - ") for s in official_stems)


def skipped_on_purpose(name):
    """Console copies and other languages' voices: the PC game never loads them for English."""
    return bool(CONSOLE.search(name) or OTHER_LANG.search(name))


def ba2s_in(folder):
    """{lower name: full path} of the .ba2 files at the top of a folder ({} when it can't be read)."""
    if not folder:
        return {}
    try:
        return {n.lower(): os.path.join(folder, n) for n in os.listdir(folder) if n.lower().endswith(".ba2")}
    except OSError:
        return {}


def wanted_names(stem, view, language=LANGUAGE):
    """The archive names the game registers for a plugin stem (lower case), given the names present."""
    stem = stem.lower()
    want = []
    for kind in ("main", "textures"):
        want.append(f"{stem} - {kind}.ba2")
        for i in range(1, 100):
            n = f"{stem} - {kind}{i:02d}.ba2"
            if n not in view:
                break
            want.append(n)
    want.append(f"{stem} - voices_{language}.ba2")
    return want


def assign(plugins, view_of):
    """{plugin name: [full archive path, ...]} by the exact-name rule. view_of(plugin) gives the {lower name: full
    path} the plugin's archives are looked for in. A name already registered is skipped."""
    out, taken = {}, set()
    for p in plugins:
        view = view_of(p)
        out[p.name] = []
        for n in wanted_names(os.path.splitext(p.name)[0], view):
            full = view.get(n)
            if full and full.lower() not in taken:
                taken.add(full.lower())
                out[p.name].append(full)
    return out, taken


def data_matches(lo):
    """True when the Data folder holds this load order's mod plugins and no others (so Data describes it)."""
    if not lo.data or not os.path.isdir(lo.data):
        return False
    try:
        in_data = {n.lower() for n in os.listdir(lo.data) if n.lower().endswith((".esm", ".esp", ".esl"))}
    except OSError:
        return False
    in_data -= BASE
    listed = {p.name.lower() for p in lo.plugins}
    official = {p.name.lower() for p in lo.plugins if is_official(p)}
    on = {p.name.lower() for p in lo.plugins if p.enabled and not p.dropped and not is_official(p)}
    return (in_data - official) <= listed and on <= in_data


def _closure(lo):
    """{plugin lower: set of every master lower, direct or through its masters}."""
    direct = {p.name.lower(): {m.lower() for m in (p.masters or [])} for p in lo.plugins}
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


def category(path):
    """Top folder of an archived path (textures, meshes, sound, scripts ...), for the summary counts."""
    return path.split("\\", 1)[0] if "\\" in path else "(top level)"


def _enabled_mods(lo):
    """Enabled mods with a folder, lowest priority first (MO2 priority; else the listed order)."""
    mods = [(i, m) for i, m in enumerate(lo.mods or []) if m.enabled and m.path and os.path.isdir(m.path)]
    mods.sort(key=lambda im: (im[1].priority if im[1].priority is not None else im[0], im[0]))
    return [m for _i, m in mods]


def analyse(lo, cache):
    """Archive overlap for the enabled mod plugins of a LoadOrder. Returns the result dict that run() stores in
    ctx.results["archives"] (keys described in run's docstring)."""
    official_stems = {os.path.splitext(n)[0] for n in BASE} | {
        os.path.splitext(p.name)[0].lower() for p in lo.plugins if is_official(p)}
    mine = [p for p in lo.plugins if p.enabled and not p.dropped and not is_official(p) and p.path
            and p.kind != "?"]
    data = lo.data if lo.data and os.path.isdir(lo.data) else None
    trusted = lo.source != "vortex" or data_matches(lo)
    data_view = ba2s_in(data)

    if lo.source == "mo2":  # MO2's merged view: Data, then each enabled mod's folder by priority
        merged = dict(data_view)
        for m in _enabled_mods(lo):
            merged.update(ba2s_in(m.path))
        views = {"(mo2)": merged}

        def view_key(p):
            return "(mo2)"
        orphan_scan = merged
    else:
        views = {}

        def view_key(p):
            return os.path.normcase(os.path.dirname(os.path.abspath(p.path)))
        for p in mine:
            k = view_key(p)
            if k not in views:
                views[k] = ba2s_in(os.path.dirname(p.path))
        dk = os.path.normcase(os.path.abspath(data)) if data else None
        orphan_scan = {}
        for k, v in views.items():
            if k != dk:
                orphan_scan.update(v)
        if trusted:
            orphan_scan.update(data_view)
    owned, taken = assign(mine, lambda p: views[view_key(p)])

    # archives that start with an enabled plugin's name but don't fit the rule, and archives nobody loads
    scanned = {}
    for v in views.values():
        scanned.update({full.lower(): full for full in v.values()})
    stems = {os.path.splitext(p.name)[0].lower() + " - ": p.name for p in mine}
    idle = {}  # stem prefix -> listed plugin that doesn't load
    for p in lo.plugins:
        if not (p.enabled and not p.dropped and p.path and p.kind != "?") and not is_official(p):
            idle[os.path.splitext(p.name)[0].lower()] = p.name
    misnamed, orphans = [], []
    for fl, full in sorted(scanned.items()):
        name = os.path.basename(full)
        nl = name.lower()
        if fl in taken or skipped_on_purpose(name) or is_base_archive(name, official_stems):
            continue
        hits = [pn for s, pn in stems.items() if nl.startswith(s)]
        if hits:
            p = max(hits, key=len)
            why = "gap" if NUMBERED.search(nl) else "name"
            # a longer plugin name that isn't enabled (e.g. "<plugin> - Addon.esm" disabled) owns it instead
            longer = [s for s in idle if nl.startswith(s + " - ") and len(s) > len(os.path.splitext(p)[0])]
            if not longer:
                misnamed.append({"archive": full, "plugin": p, "why": why})
                continue
    claimed_or_bad = taken | {m["archive"].lower() for m in misnamed}
    for fl, full in sorted({f.lower(): f for f in orphan_scan.values()}.items()):
        name = os.path.basename(full)
        nl = name.lower()
        if fl in claimed_or_bad or skipped_on_purpose(name) or is_base_archive(name, official_stems):
            continue
        owner = next((pn for s, pn in sorted(idle.items(), key=lambda kv: -len(kv[0])) if nl.startswith(s + " - ")),
                     None)
        orphans.append({"archive": full, "plugin": owner})

    # what each plugin's archives hold
    owners = defaultdict(list)  # path -> plugins shipping it, in load order
    where, plugins, unreadable = {}, {}, []
    for p in mine:
        files = {}
        for a in owned[p.name]:
            try:
                for f in cache.names(a):
                    files.setdefault(f, a)
            except (OSError, ValueError, struct.error) as x:
                unreadable.append({"archive": a, "plugin": p.name, "error": str(x)})
                log.info(SECTION, f"can't read {a}: {x}")
        where[p.name] = files
        for f in files:
            owners[f].append(p.name)
        plugins[p.name] = {"archives": [os.path.basename(a) for a in owned[p.name]], "paths": owned[p.name],
                           "files": len(files), "shared": 0, "wins": 0, "loses": 0}
        log.info(SECTION, f"{p.name}: {len(owned[p.name])} archive(s), {len(files)} files")

    shared = {f: ps for f, ps in owners.items() if len(ps) > 1}
    digests = {f: {p: cache.digest(where[p][f], f) for p in ps} for f, ps in shared.items()}
    need = _closure(lo)
    modof = {}
    for m in (lo.mods or []):
        for pn in (m.plugins or []):
            modof.setdefault(pn.lower(), m.name)

    pairs = {}
    for f, ps in shared.items():
        w = ps[-1]
        for los in ps[:-1]:
            pr = pairs.setdefault((los, w), {"loser": los, "winner": w, "files": 0, "same": 0, "differ": 0,
                                             "unknown": 0, "cats": defaultdict(int), "sample": [], "_rest": []})
            pr["files"] += 1
            pr["cats"][category(f)] += 1
            dl, dw = digests[f].get(los), digests[f].get(w)
            how = "unknown" if not (dl and dw) else ("same" if dl == dw else "differ")
            pr[how] += 1
            if how != "same" and len(pr["sample"]) < SAMPLES:
                pr["sample"].append(f)
            elif len(pr["_rest"]) < SAMPLES:
                pr["_rest"].append(f)
            plugins[los]["loses"] += 1
        plugins[w]["wins"] += 1
        for p in ps:
            plugins[p]["shared"] += 1
    for (los, w), pr in pairs.items():
        ll, wl = los.lower(), w.lower()
        if ll in need.get(wl, ()):
            pr["advice"] = "built"
        elif modof.get(ll) and modof.get(ll) == modof.get(wl):
            pr["advice"] = "samemod"
            pr["mod"] = modof[ll]
        elif pr["same"] == pr["files"]:
            pr["advice"] = "same"
        else:
            pr["advice"] = "overlap"
        pr["sample"] = (pr["sample"] + pr.pop("_rest"))[:SAMPLES]
        pr["cats"] = dict(sorted(pr["cats"].items(), key=lambda kv: (-kv[1], kv[0])))
    pos = {p.name: i for i, p in enumerate(mine)}
    out = sorted(pairs.values(), key=lambda pr: (pr["advice"] != "overlap", pos[pr["winner"]], pos[pr["loser"]]))
    decides = sorted({n for pr in out if pr["advice"] == "overlap" for n in (pr["loser"], pr["winner"])},
                     key=lambda n: pos[n])
    loaded = [(p.name, a) for p in mine for a in owned[p.name]]
    lz4_missing = sorted(cache.lz4_missing)
    log.info(SECTION, f"{len(loaded)} archives of {len(mine)} mod plugins, {len(shared)} shared paths, "
                      f"{len(decides)} plugin(s) where order decides, {len(misnamed)} won't load, "
                      f"{len(orphans)} loaded by no enabled plugin (Data {'checked' if trusted else 'not checked'})")
    return {"plugins": plugins, "pairs": out, "order_decides": decides, "paths": shared, "digests": digests,
            "misnamed": misnamed, "orphans": orphans, "unreadable": unreadable, "lz4_missing": lz4_missing,
            "loaded": loaded, "data_checked": trusted}


def archived_sources(lo, result, cache):
    """{path: (owner, archive full path)} for every file inside an archive that loads: the base game's and DLC's
    archives in Data first ("Starfield - *" first, then the rest by name; an approximation, since the game's INI
    lists set their order), then the mods' archives in plugin order. The later archive wins a path."""
    out = {}
    data = lo.data if lo.data and os.path.isdir(lo.data) else None
    official_stems = {os.path.splitext(n)[0] for n in BASE} | {
        os.path.splitext(p.name)[0].lower() for p in lo.plugins if is_official(p)}
    mods = {a.lower() for _p, a in result.get("loaded", [])}
    base = [(n, full) for n, full in ba2s_in(data).items()
            if full.lower() not in mods and not skipped_on_purpose(n) and is_base_archive(n, official_stems)]
    base.sort(key=lambda nf: (not nf[0].startswith("starfield - "), nf[0]))
    for _n, full in base:
        try:
            for f in cache.names(full):
                out[f] = (os.path.basename(full), full)
        except (OSError, ValueError, struct.error):
            continue
    for p, a in result.get("loaded", []):
        try:
            for f in cache.names(a):
                out[f] = (p, a)
        except (OSError, ValueError, struct.error):
            continue
    return out


def flips(old_order, new_order, results):
    """Shared archive paths whose winning plugin changes when the plugins load in new_order instead of old_order.
    results: ctx.results["archives"]. Identical copies never count. Returns [{"path", "before", "after"}]."""
    old = {n.lower(): i for i, n in enumerate(old_order)}
    new = {n.lower(): i for i, n in enumerate(new_order)}
    out = []
    for f, ps in (results or {}).get("paths", {}).items():
        known = [p for p in ps if p.lower() in old and p.lower() in new]
        if len(known) < 2:
            continue
        before = max(known, key=lambda p: old[p.lower()])
        after = max(known, key=lambda p: new[p.lower()])
        dg = results.get("digests", {}).get(f, {})
        if after != before and not (dg.get(after) and dg.get(after) == dg.get(before)):
            out.append({"path": f, "before": before, "after": after})
    return sorted(out, key=lambda x: x["path"])


# ---------------------------------------------------------------- findings

def _cats(pr):
    return ", ".join(f"{n} {c}" for c, n in list(pr["cats"].items())[:4])


def _add_findings(report, r):
    for m in r["misnamed"]:
        name, p = os.path.basename(m["archive"]), m["plugin"]
        stem = os.path.splitext(p)[0]
        if m["why"] == "gap":
            text = (f"{name} won't load: the game reads numbered archives for {p} in order and stops at the first "
                    f"missing number, and one before it is missing.")
            fix = "Rename it to fill the gap (or ask the mod's author for a fixed version); check the mod is complete."
        else:
            text = (f"{name} starts with {p}'s name but the game won't load it: it loads only {stem} - Main.ba2, "
                    f"{stem} - Main01.ba2 and on, {stem} - Textures.ba2 and on, and {stem} - Voices_en.ba2.")
            fix = "Ask the mod's author: the archive needs one of those names, or its files never reach the game."
        report.add(Finding("caution", SECTION, text, fix, [p], {"archive": m["archive"], "why": m["why"]}))
    for u in r["unreadable"]:
        report.add(Finding("caution", SECTION, f"Can't read {os.path.basename(u['archive'])} ({u['error']}), so its "
                           f"files weren't checked.", "Reinstall the mod that ships it; the file may be damaged.",
                           [u["plugin"]], {"archive": u["archive"]}))
    if r["orphans"]:
        names = [os.path.basename(o["archive"]) + (f" (its plugin {o['plugin']} doesn't load)" if o["plugin"] else "")
                 for o in r["orphans"]]
        shown = "; ".join(names[:10]) + (f"; and {len(names) - 10} more" if len(names) > 10 else "")
        report.add(Finding("note", SECTION, f"No enabled plugin loads {len(names)} archive(s): {shown}.",
                           "If you still use the mod, enable its plugin; otherwise the archive is a leftover you can "
                           "remove. (An archive listed in an INI's archive list loads anyway.)",
                           [o["plugin"] for o in r["orphans"] if o["plugin"]],
                           {"archives": [o["archive"] for o in r["orphans"]]}))
    quiet = []
    for pr in r["pairs"]:
        los, w = pr["loser"], pr["winner"]
        if pr["advice"] != "overlap":
            quiet.append(pr)
            continue
        what = (f"{pr['differ']} differ" if pr["differ"] else "") + (
            (", " if pr["differ"] and pr["unknown"] else "") + f"{pr['unknown']} can't be compared" if pr["unknown"]
            else "")
        report.add(Finding(
            "caution", SECTION,
            f"{los} and {w} both ship {pr['files']} file(s) in their archives ({_cats(pr)}; {what}). {w} loads later, "
            f"so its copies are used and {los}'s never show up in the game.",
            f"Order decides here. If you want {los}'s versions, load {los} after {w}; if {w}'s are right, leave it.",
            [los, w], {k: pr[k] for k in ("files", "same", "differ", "unknown", "sample", "cats")}))
    if quiet:
        label = {"built": "built on it", "samemod": "same mod", "same": "identical copies"}
        parts = [f"{pr['loser']} / {pr['winner']} ({label[pr['advice']]}, {pr['files']} file(s))" for pr in quiet]
        shown = "; ".join(parts[:10]) + (f"; and {len(parts) - 10} more" if len(parts) > 10 else "")
        report.add(Finding("note", SECTION, f"{len(quiet)} pair(s) of plugins share archive paths but need nothing: "
                           f"{shown}.", None, sorted({n for pr in quiet for n in (pr["loser"], pr["winner"])}),
                           {"pairs": [{"loser": pr["loser"], "winner": pr["winner"], "advice": pr["advice"],
                                       "files": pr["files"]} for pr in quiet]}))
    if r["lz4_missing"]:
        report.add(Finding("note", SECTION, LZ4_NOTE, "python -m pip install lz4", [],
                           {"archives": r["lz4_missing"]}))


def run(ctx):
    """Section "archives". Stores in ctx.results["archives"]:
      plugins        {plugin: {archives, paths, files, shared, wins, loses}}
      pairs          [{loser, winner, files, same, differ, unknown, cats, sample, advice[, mod]}], advice one of
                     built, samemod, same, overlap ("order decides"); overlap pairs first
      order_decides  plugin names in at least one overlap pair (load order)
      paths          {shared path: [plugins shipping it, load order]}; digests {path: {plugin: digest or None}}
      misnamed, orphans, unreadable, lz4_missing, loaded [(plugin, archive full path)], data_checked
    """
    lo = ctx.load_order
    cache = Cache(cache_path(ctx.args, "archives.json"))
    r = analyse(lo, cache)
    cache.save()
    ctx.results[SECTION] = r
    _add_findings(ctx.report, r)
    status = {"status": "done", "archives": len(r["loaded"]), "shared_paths": len(r["paths"]),
              "order_decides": len(r["order_decides"])}
    if not r["data_checked"]:
        status["note"] = ("Data doesn't match this load order (another profile is deployed), so archives in Data "
                          "that no plugin loads weren't listed.")
    return status
