"""Patches built against an older version of a master (section "patches").

Starfield numbers a plugin's masters separately by kind. Inside a plugin file, a record's FormID names its master by
an index into the list of masters *of one kind*: full masters by the top byte (00, 01, ...), medium masters as FD +
index, light masters as FE + index. The plugin's own records use the index just past its masters (Mutagen and others
also write own records in the full style, whatever the plugin's kind). So when a master changes kind (full to light
is common), every FormID the patch holds for it, and for the later masters of its old kind, points somewhere else,
with no error from the game or the mod manager. The patch has to be rebuilt against the new master.

Two checks:
  1. Header-only FormID scan (no history needed). Reads only record headers. It flags record FormIDs that
       - are the plugin's own records but past its own size limit (light 0xFFF, medium 0xFFFF);
       - override a record of a mod master (not an official file) that the master doesn't define: after a kind
         change the IDs shift onto the wrong master, where the record usually doesn't exist.
     An index *past* the end of a master list is read by the engine as the plugin itself (any index at or past the
     master count means "this file"), so those records are the plugin's own and are not a problem. Some real plugins
     are saved that way (for example built while they had one more master). They get a note, because a master that
     changed kind can also push own records there.
     Blind spot: a shifted override that lands on the plugin's own index. Check 2 covers that.
  2. "A master changed kind since the plugin was built." Each run saves a snapshot in the state folder (--state,
     default %LOCALAPPDATA%\\sf-load-order, one file per load order: <source>-<key>.json, the key a hash of stable
     inputs only: the source, the profile, the Data folder, and for MO2 the instance folder or for Plugins.txt its
     path; never a Vortex export's file name, which changes with every export). For every plugin it holds the kind,
     the file's size + date ("stamp"), and "built_for": the kinds its masters had when this copy of the plugin was
     first seen. The rule:
       - plugin file unchanged (same stamp) and a master's kind now differs from built_for: PROBLEM, on every run
         until the plugin is updated or the master goes back to its old kind (built_for is carried over unchanged);
         - plugin file changed (new stamp) and a master's kind differs from the old copy's built_for: CAUTION once (make
         sure the update was built against the new master); built_for is then re-baselined to the masters' current
         kinds, since the new copy is the one to judge from now on (the FormID scan above still checks it).
     A snapshot from before built_for existed (or a plugin seen for the first time) uses the masters' kinds in the
     previous snapshot as built_for. --no-state turns this off (nothing read or written).
The snapshot is the only file this check writes, and only in the state folder.
"""
import datetime
import hashlib
import json
import mmap
import os
import struct

from . import __version__, log, paths, plugin_header
from .model import Finding

SECTION = "patches"
LIMIT = {"L": 0xFFF, "M": 0xFFFF, "F": 0xFFFFFF}
STYLE = {"F": "full", "M": "medium", "L": "light"}
KINDS = ("F", "M", "L")
SNAPSHOT_SCHEMA = 1
EXAMPLES = 5


# ---------- reading ----------
def stamp(path):
    """size|modified time (UTC): which copy of a file a snapshot saw. Size and date together, since mod managers can
    keep an archive's older file date on an update."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{st.st_size}|{datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).isoformat()}"


def record_ids(path):
    """Yield every record's raw FormID (record headers only; groups are walked, nothing is decompressed)."""
    with open(path, "rb") as fh:
        try:
            d = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # empty file
            return
        try:
            if len(d) < 24:
                return
            stack = [(struct.unpack_from("<I", d, 4)[0] + 24, len(d))]
            while stack:
                i, end = stack.pop()
                while i + 24 <= end:
                    size = struct.unpack_from("<I", d, i + 4)[0]
                    if d[i:i + 4] == b"GRUP":
                        if size < 24:
                            return  # damaged group header: stop rather than loop
                        stack.append((i + size, end))
                        end = min(i + size, end)
                        i += 24
                        continue
                    yield struct.unpack_from("<I", d, i + 12)[0]
                    i += 24 + size
        finally:
            d.close()


def decode(fid):
    """(list key F/M/L, index, local ID) of a raw in-file FormID."""
    top = fid >> 24
    if top == 0xFE:
        return "L", (fid >> 12) & 0xFFF, fid & 0xFFF
    if top == 0xFD:
        return "M", (fid >> 16) & 0xFF, fid & 0xFFFF
    return "F", top, fid & 0xFFFFFF


def master_lists(masters, kind_of):
    """{F: [...], M: [...], L: [...]} of a plugin's masters by kind, or None when a master's kind is unknown."""
    lists = {k: [] for k in KINDS}
    for m in masters:
        k = kind_of(m)
        if k not in lists:
            return None
        lists[k].append(m)
    return lists


def classify(fid, lists):
    """("own", None, local, past) for the plugin's own records (past = the index is beyond the list, not just at its
    end), or ("master", name, local, False) for an override of a master's record."""
    k, idx, local = decode(fid)
    if idx >= len(lists[k]):  # the engine reads any index at or past the master count as this file
        return "own", None, local, idx > len(lists[k])
    return "master", lists[k][idx], local, False


class Scanner:
    """Runs the FormID scan over plugins of one load order. kind_of(name) -> F/M/L or None; info(name) -> (path,
    masters, official) or None. Own-record sets of mod masters are read once."""

    def __init__(self, kind_of, info):
        self.kind_of, self.info, self._own = kind_of, info, {}

    def own_ids(self, name):
        """Local IDs a plugin defines itself, or None when they can't be worked out."""
        key = name.lower()
        if key not in self._own:
            got = self.info(name)
            ids = None
            if got and got[0]:
                lists = master_lists(got[1] or [], self.kind_of)
                if lists is not None:
                    try:
                        ids = {local for what, _, local, _ in (classify(f, lists) for f in record_ids(got[0]))
                               if what == "own"}
                    except OSError as e:
                        log.info(SECTION, f"can't read {got[0]}: {e}")
            self._own[key] = ids
        return self._own[key]

    def scan(self, path, kind, masters):
        """{"bad": n, "examples": [...], "past": n, "past_example": str, "judged": bool}."""
        out = {"bad": 0, "examples": [], "past": 0, "past_example": None, "judged": False}
        lists = master_lists(masters, self.kind_of)
        if lists is None:
            return out  # a master is missing or unreadable: the order section says so; IDs can't be judged
        out["judged"] = True
        for fid in record_ids(path):
            what, m, local, past = classify(fid, lists)
            why = None
            if what == "own":
                if past:
                    out["past"] += 1
                    out["past_example"] = out["past_example"] or f"{fid:08X}"
                if local > LIMIT.get(kind, 0xFFFFFF):
                    why = f"{fid:08X}: own ID 0x{local:X} is past the limit of a {STYLE.get(kind, kind)} plugin"
            else:
                got = self.info(m)
                if got and not got[2]:  # official files aren't looked up (always first, never change kind)
                    own = self.own_ids(m)
                    if own is not None and local not in own:
                        why = f"{fid:08X}: overrides 0x{local:X} in {m}, which has no such record"
            if why:
                out["bad"] += 1
                if len(out["examples"]) < EXAMPLES:
                    out["examples"].append(why)
        return out


# ---------- snapshot ----------
def default_state():
    return paths.default_state()


def _norm(path):
    return os.path.normcase(os.path.abspath(path)) if path else None


def source_key(lo):
    """A stable name for one load order, from inputs that stay the same between runs: source, profile, Data folder,
    plus the MO2 instance folder (MO2) or the Plugins.txt path (Plugins.txt). Not the Vortex export's file name, nor
    staging or deployment paths. (Keys made by 0.x builds before this rule simply start a fresh snapshot.)"""
    files = lo.files or {}
    where = None
    if lo.source == "mo2":
        ini = files.get("modorganizer_ini")
        where = os.path.dirname(ini) if ini else files.get("plugins_txt")
    elif lo.source == "plugins.txt":
        where = files.get("plugins_txt")
    raw = json.dumps([lo.source, lo.profile, _norm(lo.data), _norm(where)])
    return f"{lo.source or 'unknown'}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def snapshot(lo, built_for=None):
    """built_for: {plugin lower name: {master lower name: kind}} (see the module text)."""
    built_for = built_for or {}
    return {"schema": SNAPSHOT_SCHEMA, "tool_version": __version__, "source": lo.source, "profile": lo.profile,
            "files": dict(lo.files or {}),
            "written": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "plugins": {p.name: {"kind": p.kind, "stamp": stamp(p.path),
                                 "built_for": dict(built_for.get(p.name.lower(), {}))}
                        for p in lo.plugins if p.path and p.kind in KINDS}}


def read_snapshot(path):
    """(snapshot dict or None, error text or None)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"
    if not isinstance(d, dict) or not isinstance(d.get("plugins"), dict):
        return None, "not a snapshot file"
    return d, None


def write_snapshot(path, snap):
    """Returns an error text or None. Written to a temporary file first, so a failed write keeps the old one."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        return f"{e.strerror or e} ({path})"
    return None


def kind_changes(prev, lo):
    """{lower name: (name, old kind, new kind)} for plugins whose kind differs from the previous snapshot."""
    old = {k.lower(): v for k, v in prev.get("plugins", {}).items() if isinstance(v, dict)}
    out = {}
    for p in lo.plugins:
        o = old.get(p.name.lower(), {}).get("kind")
        if o in KINDS and p.kind in KINDS and o != p.kind:
            out[p.name.lower()] = (p.name, o, p.kind)
    return out


def baseline(prev, lo):
    """{plugin lower name: {"same": bool, "kinds": {master lower name: kind}}} for every plugin the snapshot keeps.
    same = the plugin's file has the stamp the previous snapshot saw. kinds = its built_for: carried over while the
    file is unchanged (missing masters filled from the previous snapshot's kinds, else today's), else the masters'
    kinds today (a new or updated copy is re-baselined). See the module text."""
    old = {k.lower(): v for k, v in (prev or {}).get("plugins", {}).items() if isinstance(v, dict)}
    now = {p.name.lower(): p.kind for p in lo.plugins if p.path and p.kind in KINDS}
    out = {}
    for p in lo.plugins:
        if not (p.path and p.kind in KINDS):
            continue
        o = old.get(p.name.lower(), {})
        st = stamp(p.path)
        same = o.get("stamp") is not None and o.get("stamp") == st
        masters = [m.lower() for m in (p.masters or [])]
        if same:
            kept = o.get("built_for")
            kinds = {k: v for k, v in kept.items() if v in KINDS} if isinstance(kept, dict) else {}
            for m in masters:
                if m not in kinds:
                    was = old.get(m, {}).get("kind")
                    k = was if was in KINDS else now.get(m)
                    if k in KINDS:
                        kinds[m] = k
            was = kinds
        else:
            kinds = {m: now[m] for m in masters if m in now}
            # what the previous copy was built for, to judge the update against once
            kept = o.get("built_for")
            if isinstance(kept, dict):
                was = {k: v for k, v in kept.items() if v in KINDS}
            else:
                was = {}
            for m in masters:
                k = old.get(m, {}).get("kind")
                if m not in was and k in KINDS and o:
                    was[m] = k
        out[p.name.lower()] = {"same": same, "kinds": kinds, "was": was}
    return out


# ---------- the check ----------
def _add(ctx, level, text, fix=None, plugins=(), **data):
    ctx.report.add(Finding(level, SECTION, text, fix, list(plugins), data))


def run(ctx):
    """The older-master checks below, then the merged patches (merged.py: sf-mod-check patch check), whose result
    is report.json `patches`. A failure in the merged part is reported there and leaves the rest of the section."""
    status = _older_master(ctx)  # (the whole section is skipped when Data holds another profile: cli.NEEDS_DEPLOYED)
    from . import merged
    try:
        ctx.report.patches, status["merged"] = merged.run(ctx)
    except Exception as e:  # a bug in the merged part must not hide the older-master findings
        import traceback
        log.info(SECTION, "merged patches failed:\n" + traceback.format_exc())
        ctx.report.patches = {"not_checked": f"the check failed ({type(e).__name__}: {e})"}
        status["merged"] = {"status": "failed", "why": f"{type(e).__name__}: {e}"}
    return status


def _older_master(ctx):
    lo = ctx.load_order
    if any(p.path and p.kind is None for p in lo.plugins):
        plugin_header.fill(lo)
    by_name = {p.name.lower(): p for p in lo.plugins}
    loading = [p for p in lo.plugins if p.enabled and not p.dropped]
    loading_names = {p.name.lower() for p in loading}

    def kind_of(m):
        p = by_name.get(m.lower())
        return p.kind if p is not None and p.path and m.lower() in loading_names else None

    def info(m):
        p = by_name.get(m.lower())
        return (p.path, p.masters, p.official) if p is not None else None

    # 1. header-only FormID scan
    scanner = Scanner(kind_of, info)
    scanned = flagged = 0
    for p in loading:
        if p.official or p.kind not in KINDS or not p.path:
            continue
        try:
            r = scanner.scan(p.path, p.kind, p.masters or [])
        except OSError as e:
            log.info(SECTION, f"{p.name}: can't read records: {e}")
            continue
        if not r["judged"]:
            log.info(SECTION, f"{p.name}: skipped, a master is missing or unreadable")
            continue
        scanned += 1
        log.info(SECTION, f"{p.name}: {r['bad']} bad FormID(s), {r['past']} own record(s) past the master list")
        if r["bad"]:
            flagged += 1
            _add(ctx, "problem",
                 f"{p.name} holds {r['bad']} FormID(s) that don't fit its current masters. A master probably changed "
                 "between full, medium and light after it was built, e.g. " + "; ".join(r["examples"]) + ".",
                 f"Get a version of {p.name} built for your current masters (update it, or ask its author to rebuild "
                 "it), or disable it.", [p.name], count=r["bad"], examples=r["examples"], past_list=r["past"])
        elif r["past"]:
            _add(ctx, "note",
                 f"{p.name} saves {r['past']} of its own records with a master index past its master list (e.g. "
                 f"{r['past_example']}). The game reads those as the plugin's own records, so this is usually "
                 "harmless (a leftover from a build with more masters).", None, [p.name], past_list=r["past"])

    status = {"status": "done", "scanned": scanned, "flagged": flagged}

    # 2. master changed kind since the last run
    if getattr(ctx.args, "no_state", False):
        status["state"] = "off (--no-state)"
        return status
    folder = getattr(ctx.args, "state", None) or default_state()
    path = os.path.join(folder, source_key(lo) + ".json")
    prev, err = read_snapshot(path)
    if err:
        log.info(SECTION, f"previous snapshot {path} unreadable ({err}); starting a new one")
        status["state_warning"] = f"the previous snapshot couldn't be read ({err}); a new one was started"
    built_for = baseline(prev, lo)
    if prev is not None:
        changes = kind_changes(prev, lo)
        name_of = {p.name.lower(): p.name for p in lo.plugins}
        still_old = set()  # plugins unchanged since built for a master's older kind: a problem on every run
        for p in loading:
            if p.official or p.kind not in KINDS:
                continue
            bf = built_for.get(p.name.lower())
            if bf is None:
                continue
            for m in p.masters or []:
                ml = m.lower()
                q = by_name.get(ml)
                new = q.kind if q is not None and q.path else None
                name = name_of.get(ml, m)
                old = bf["was"].get(ml)
                if not (old in KINDS and new in KINDS and old != new):
                    continue
                if bf["same"]:
                    still_old.add(p.name.lower())
                    _add(ctx, "problem",
                         f"{p.name} was built for the {STYLE[old]} version of its master {name}, which is now "
                         f"{STYLE[new]}: its FormIDs for that master now point at the wrong records.",
                         f"Get a version of {p.name} rebuilt for the new {name}, or disable it.",
                         [p.name, name], master=name, old_kind=old, new_kind=new)
                else:
                    _add(ctx, "caution",
                         f"{name} changed from {STYLE[old]} to {STYLE[new]} since {p.name}, which masters it, was "
                         f"last seen, and {p.name} changed too.",
                         f"Make sure that version of {p.name} was built against the new {name}.",
                         [p.name, name], master=name, old_kind=old, new_kind=new)
        status["kind_changes"] = len(changes)
        status["built_for_old"] = len(still_old)
        log.info(SECTION, f"snapshot {path}: {len(changes)} kind change(s) since {prev.get('written')}, "
                          f"{len(still_old)} plugin(s) still built for a master's older kind")
    else:
        log.info(SECTION, f"no previous snapshot at {path}: the kind-change check starts with the next run")
    werr = write_snapshot(path, snapshot(lo, {k: v["kinds"] for k, v in built_for.items()}))
    if werr:
        status["state_warning"] = f"the snapshot couldn't be written: {werr}"
        log.info(SECTION, f"snapshot not written: {werr}")
    status["state"] = path
    status["previous"] = prev is not None
    return status
