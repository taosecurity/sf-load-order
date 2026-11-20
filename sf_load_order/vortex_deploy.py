"""Vortex's deployment record: which mod deployed which file into Data (C1b).

A Vortex Load Order export lists only mods with plugins, so a mod that ships loose files only (textures, scripts,
an SFSE DLL) is missing from it, and its files would look like stray files. Vortex writes a record of every file it
deploys into the folder it deploys to. For Starfield's Data folder that is

  Data\\vortex.deployment.starfield-data-folder.json
  {"stagingPath": "<Vortex's mod staging folder>",
   "files": [{"relPath": "textures\\x.dds", "source": "<mod id>", ...}, ...], ...other keys}

relPath is relative to Data; source is the Vortex mod id, which is also the mod's folder name under stagingPath.
Any other vortex.deployment*.json in Data (another mod type deploying there) is read too and merged after it.

  read(data)              -> Deployment, or None when there is no record or it can't be read (logged)
  enrich(lo, deployment)  -> [model.Finding] for "inputs"; adds the mods the export left out, fills Mod.files, marks
                             every mod in the record enabled (it is deployed, even with its plugin turned off), and
                             records the file in lo.files["vortex_deployment"]. Plugins (order, enabled) are never
                             touched.
  owner(deployment, rel)  -> the mod id that deployed a Data-relative path, or None (no mod: a stray file)

Nothing here writes anywhere or changes Vortex.
"""
import json
import os
from dataclasses import dataclass, field

from . import log, paths
from .model import Finding, Mod

SECTION = "inputs"
RECORD = "vortex.deployment.starfield-data-folder.json"
PLUGIN_EXT = (".esm", ".esp", ".esl")


@dataclass
class Deployment:
    path: str                                    # the main record file read (the first one, see read())
    staging: str = None                          # stagingPath from the record, None if it names none
    files: dict = field(default_factory=dict)    # {relative path lower, backslashes: source mod id}
    records: list = field(default_factory=list)  # every record file read, main one first
    skipped: int = 0                             # entries with no relPath or no source

    def by_mod(self):
        """{mod id: sorted [relative path lower]}."""
        out = {}
        for p, mid in self.files.items():
            out.setdefault(mid, []).append(p)
        return {k: sorted(v) for k, v in out.items()}


def norm(rel):
    """A Data-relative path as the record keys hold it: lower case, backslashes, no leading separator."""
    return str(rel).replace("/", "\\").lstrip("\\").lower()


def _records_in(data):
    try:
        names = os.listdir(data)
    except OSError:
        return []
    main = [n for n in names if n.lower() == RECORD]
    other = sorted(n for n in names if n.lower().startswith("vortex.deployment") and n.lower().endswith(".json")
                   and n.lower() != RECORD)
    return [os.path.join(data, n) for n in main + other if os.path.isfile(os.path.join(data, n))]


def _load(path):
    """(dict, None) or (None, why)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except OSError as e:
        return None, f"can't read it ({e.strerror})"
    except (ValueError, UnicodeDecodeError):
        return None, "it isn't valid JSON"
    if not isinstance(d, dict) or not isinstance(d.get("files", []), list):
        return None, "it isn't shaped like a Vortex deployment record (an object with a files list)"
    return d, None


def read(data):
    """The deployment record(s) in Data merged into one Deployment, or None (no Data, no record, or none readable).
    When two records list the same path, the main record's entry wins."""
    if not data or not os.path.isdir(data):
        log.info(SECTION, "vortex deployment: no Data folder, so no deployment record")
        return None
    found = _records_in(data)
    if not found:
        log.info(SECTION, f"vortex deployment: no {RECORD} in {data}")
        return None
    dep = None
    for path in found:
        d, why = _load(path)
        if d is None:
            log.info(SECTION, f"vortex deployment: skipping {path}: {why}")
            continue
        if dep is None:
            sp = d.get("stagingPath")
            dep = Deployment(path, staging=sp if isinstance(sp, str) and sp else None)
        dep.records.append(path)
        n = 0
        for e in d.get("files", []):
            rel = e.get("relPath") if isinstance(e, dict) else None
            src = e.get("source") if isinstance(e, dict) else None
            if not isinstance(rel, str) or not rel or not isinstance(src, str) or not src:
                dep.skipped += 1
                continue
            dep.files.setdefault(norm(rel), src)
            n += 1
        log.info(SECTION, f"vortex deployment: {path}: {n} files")
    if dep is None:
        return None
    log.info(SECTION, f"vortex deployment: {len(dep.files)} files from {len(dep.by_mod())} mods, staging "
                      f"{dep.staging or '(not named)'}, {dep.skipped} entries skipped")
    return dep


def owner(deployment, rel):
    """The Vortex mod id whose copy of rel (Data-relative, any case or slash) Vortex deployed, else None."""
    if deployment is None:
        return None
    return deployment.files.get(norm(rel))


def _staging(lo, deployment):
    """The staging folder to find mod folders in: the one inputs found (--vortex-staging, else Vortex's default; set
    only when it exists), else the record's stagingPath. An explicit option wins over the record (master, 2026-11-19)."""
    for s in ((lo.files or {}).get("vortex_staging"), deployment.staging):
        if s and os.path.isdir(s):
            return s
    return None


def _case(folder, names):
    """{name lower: name as on disk} for names in folder (top level only)."""
    try:
        on_disk = {n.lower(): n for n in os.listdir(folder)} if folder else {}
    except OSError:
        on_disk = {}
    return {n: on_disk.get(n, n) for n in names}


def enrich(lo, deployment):
    """Add what the deployment record knows to a Vortex LoadOrder. Returns [Finding] for section "inputs"."""
    if lo is None or lo.source != "vortex":
        return []
    if deployment is None:
        log.info(SECTION, "vortex deployment: none, mods stay as the export lists them")
        return []
    lo.files["vortex_deployment"] = deployment.path
    lo._vortex_deployment = deployment  # not a dataclass field, so report.json never sees it; loose.py reads it
    if lo.mods is None:
        lo.mods = []
    staging = _staging(lo, deployment)
    if staging and "vortex_staging" not in lo.files:
        lo.files["vortex_staging"] = staging
    by_mod = deployment.by_mod()
    known = {(m.id or m.name).lower(): m for m in lo.mods}
    filled, added = 0, []
    for mid in sorted(by_mod, key=str.lower):
        files = by_mod[mid]
        m = known.get(mid.lower())
        folder = paths.child_folder(staging, mid) if staging else None
        if m is not None:
            # deployed = enabled in Vortex, even when its plugin is turned off on the Load Order page (the export
            # marks a mod enabled only through its plugins); plugin flags stay as the export has them
            if not m.enabled:
                log.info(SECTION, f"vortex deployment: {m.name} is deployed, so its mod counts as enabled")
                m.enabled = True
            if m.files is None:
                m.files = list(files)
                filled += 1
            if m.path is None and folder:
                m.path = folder
            continue
        tops = [p for p in files if "\\" not in p and p.endswith(PLUGIN_EXT)]
        case = _case(lo.data, tops)
        m = Mod(mid, True, path=folder, plugins=sorted(case[p] for p in tops), id=mid, files=list(files))
        lo.mods.append(m)
        known[mid.lower()] = m
        added.append(m)
        log.info(SECTION, f"vortex deployment: added mod {mid} ({len(files)} files, folder "
                          f"{folder or 'not found'})")
    log.info(SECTION, f"vortex deployment: {len(added)} mods added, {filled} mods' file lists filled")
    out = []
    if added:
        names = [m.name for m in added]
        out.append(Finding(
            "note", SECTION,
            f"{len(added)} mod(s) Vortex deployed aren't in the load order export (it lists only mods with "
            f"plugins), so they were added from Vortex's deployment record: " + ", ".join(names[:10])
            + (f"; and {len(names) - 10} more" if len(names) > 10 else "") + ".",
            None, [], {"mods": names, "record": deployment.path,
                       "no_folder": [m.name for m in added if m.path is None]}))
    return out
