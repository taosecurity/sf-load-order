"""Merged patches: is each conflict patch that sf-mod-check built still up to date? (part of section "patches")

sf-mod-check 0.15.0's `patch` command writes `patch-sources.json` beside the patch it builds: what the patch was built
from. Its `patch check <patch.esm>` command reads that file and the load order as it is now, and says whether the patch
needs a rebuild (a master removed, moved or updated, a new plugin that changes a patched record). sf-load-order never
judges a patch itself: it finds the manifests, runs the released `sf-mod-check patch check` on each one, and reads the
exit code and the --json file it writes (sf-mod-check's README, "Is the patch still up to date?"):

  exit 0 = up to date (NOTE lines only, or the patch isn't active); 1 = something to rebuild or review;
  2 = couldn't check. --json: {patch, state, result, findings: [{level, rule, plugin, text, records}], why}.

Where manifests are looked for: a `patch-sources.json` in the folder of each enabled plugin (Data, or its MO2 mod
folder), in each enabled Vortex mod's staging folder, and in what --patch-sources names (a manifest file, or a folder:
its patch-sources.json and every other .json directly inside it that is a manifest).

report.json `patches` (the shape the Load Order Tracker's status.json uses, plus a few keys):
  [{patch, manifest, state, level, rebuild, reasons, checked, result, findings}]  one entry per manifest
  []                                                                              no manifest was found
  {"not_checked": why}                                                            the check couldn't run fairly
state: "rebuild", "not enabled" (the patch isn't in the load order, nothing checked), "up to date" or "cannot check".
level: the worst finding's level ("problem", "caution", "note"), or "ok".
"""
import json
import os

from . import log, paths, sfmodcheck
from .model import Finding

SECTION = "patches"
MANIFEST = "patch-sources.json"
MIN_VERSION = (0, 15, 0)
TIMEOUT = 600          # seconds per patch (a 160-plugin load order takes a few seconds)
LEVEL = {"PROBLEM": "problem", "REVIEW": "caution", "NOTE": "note"}
PLUGIN_EXT = (".esm", ".esp", ".esl")
SHOWN = 5              # reasons quoted in one finding's text


def _add(ctx, level, text, fix=None, plugins=None, **data):
    ctx.report.add(Finding(level, SECTION, text, fix=fix, plugins=list(plugins or []), data=data))


# ---------- finding the manifests ----------
def _read_manifest(path):
    """(patch name, None) or (None, why)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"it couldn't be read ({getattr(e, 'strerror', None) or e})"
    p = d.get("patch") if isinstance(d, dict) else None
    name = p.get("name") if isinstance(p, dict) else None
    if not isinstance(d, dict) or not isinstance(d.get("schema"), int) or not isinstance(name, str):
        return None, "it isn't a patch-sources.json written by sf-mod-check patch (no schema or patch name)"
    if not paths.safe_component(name) or not name.lower().endswith(PLUGIN_EXT):
        return None, f"the patch name it holds ({name!r}) isn't a plugin file name"
    return name, None


def _load(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _looks_like_manifest(d):
    return isinstance(d, dict) and "schema" in d and isinstance(d.get("patch"), dict) and "loadOrder" in d


def _old_manifest(d):
    """A manifest from the old patch_sources.py (an int schema and the patch's name as a string)."""
    return isinstance(d, dict) and isinstance(d.get("schema"), int) and isinstance(d.get("patch"), str)


def find_manifests(lo, given=None):
    """[(path, given_by_option)], each manifest once (old patch_sources.py ones too). given: the --patch-sources
    values (files or folders)."""
    seen, out = {}, []

    def take(path, explicit):
        # the same manifest twice (Vortex deploys a staging copy into Data) is one patch: key by its bytes
        try:
            with open(path, "rb") as f:
                key = ("bytes", f.read())
        except OSError:
            key = ("path", os.path.normcase(os.path.abspath(path)))
        if key not in seen:
            seen[key] = len(out)
            out.append((os.path.abspath(path), explicit))
        elif explicit and not out[seen[key]][1]:
            out[seen[key]] = (os.path.abspath(path), True)

    folders = [m.path for m in lo.mods or [] if m.enabled and m.path]  # first, so a mod's own copy is the one named
    for p in lo.plugins:
        if p.enabled and not p.dropped and p.path:
            folders.append(os.path.dirname(p.path))
    if lo.data:
        folders.append(lo.data)
    done = set()
    for folder in folders:
        key = os.path.normcase(os.path.abspath(folder))
        if key in done:
            continue
        done.add(key)
        cand = os.path.join(folder, MANIFEST)
        if os.path.isfile(cand):
            take(cand, False)
    for g in given or []:
        if os.path.isdir(g):
            for name in sorted(os.listdir(g)):
                full = os.path.join(g, name)
                if not os.path.isfile(full) or not name.lower().endswith(".json"):
                    continue
                if name.lower() == MANIFEST or _looks_like_manifest(d := _load(full)) or _old_manifest(d):
                    take(full, True)
        else:
            take(g, True)  # a missing or unreadable file is reported by run()
    return out


# ---------- reading patch check ----------
EXIT1_NO_REASON = "sf-mod-check patch check exited 1 (something to rebuild or review) without a problem or caution line"


def _state(code, findings):
    if code == 2:
        return "cannot check"
    if any(f["level"] in ("problem", "caution") for f in findings):
        return "rebuild"
    if any(f["rule"] == "patch-off" for f in findings):
        return "not enabled"
    if code == 1:  # sf-mod-check says there is something to rebuild or review, even with no problem or caution line
        return "rebuild"
    return "up to date"


def _worst(findings):
    for lv in ("problem", "caution", "note"):
        if any(f["level"] == lv for f in findings):
            return lv
    return "ok"


def read_json(path):
    """The --json file's findings, as [{level, rule, plugin, text, records}] with our level words, plus its state and
    result line. Returns (dict, None) or (None, why)."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"its --json file couldn't be read ({getattr(e, 'strerror', None) or e})"
    if not isinstance(d, dict) or not isinstance(d.get("findings"), list):
        return None, "its --json file has no findings list"
    fs = []
    for f in d["findings"]:
        if not isinstance(f, dict) or f.get("level") not in LEVEL or not isinstance(f.get("text"), str):
            return None, "its --json file holds a finding this version doesn't understand"
        recs = f.get("records")
        fs.append({"level": LEVEL[f["level"]], "rule": str(f.get("rule") or ""),
                   "plugin": f.get("plugin") if isinstance(f.get("plugin"), str) else None, "text": f["text"],
                   "records": [r for r in recs if isinstance(r, str)] if isinstance(recs, list) else []})
    return {"state": d.get("state") if isinstance(d.get("state"), str) else None,
            "result": d.get("result") if isinstance(d.get("result"), str) else None,
            "why": d.get("why") if isinstance(d.get("why"), str) else None, "findings": fs}, None


def _entry(patch, manifest, code, findings, result=None, why=None, smc_state=None):
    state = _state(code, findings)
    e = {"patch": patch, "manifest": manifest, "state": state, "level": _worst(findings),
         "rebuild": state == "rebuild", "reasons": [f["text"] for f in findings if f["level"] != "note"],
         "checked": state not in ("not enabled", "cannot check"), "result": result, "findings": findings}
    if smc_state:
        e["smc_state"] = smc_state
    if state == "cannot check":
        e["level"] = "caution"
        e["reasons"] = [why or "sf-mod-check patch check couldn't check it"]
    elif state == "rebuild" and not e["reasons"]:
        e["level"] = "caution"
        e["reasons"] = [EXIT1_NO_REASON]
    return e


# ---------- the run ----------
def _not_checked(why):
    return {"not_checked": why}, {"status": "not checked", "why": why}


def _patch_file(lo, name, manifest):
    """The patch file to name on the command line: the load order's copy, else one beside the manifest, else None."""
    for p in lo.plugins:
        if p.name.lower() == name.lower() and p.path and os.path.isfile(p.path):
            return p.path
    beside = os.path.join(os.path.dirname(manifest), name)
    return beside if os.path.isfile(beside) else None


def run(ctx):
    """Returns (report.json patches value, a status dict for the section). Adds findings to ctx.report."""
    args, lo = ctx.args, ctx.load_order
    if (getattr(args, "depth", None) or "medium") == "light":
        return _not_checked("--depth light (merged patches are checked with sf-mod-check at medium and heavy)")
    manifests = find_manifests(lo, getattr(args, "patch_sources", None))
    old = [m for m, _ in manifests if _old_manifest(_load(m))]
    if old:
        manifests = [x for x in manifests if x[0] not in old]
        _add(ctx, "note", f"{len(old)} file(s) passed with --patch-sources are manifests from the old "
                          f"patch_sources.py, which sf-load-order can't check: {', '.join(old)}.",
             "Rebuild the patch with sf-mod-check 0.15.0 or later (its patch command writes patch-sources.json), "
             "and pass that file.", old_manifests=old)
    log.info(SECTION, f"merged patches: {len(manifests)} manifest(s) found, {len(old)} old")
    if not manifests:
        return [], {"status": "done", "manifests": 0}
    if not lo.data:
        return _not_checked("no Data folder was found, so sf-mod-check has nothing to read (pass --data)")
    hidden = [p.name for p in lo.plugins if p.enabled and not p.dropped and not p.official
              and not os.path.isfile(os.path.join(lo.data, p.name))]
    if hidden and lo.source == "mo2":
        return _not_checked(f"{len(hidden)} enabled plugin(s) aren't in {lo.data} (MO2 shows mods in Data only to "
                            "programs it starts): start sf-load-order from MO2")
    prog, err = sfmodcheck.find(args)
    if prog is not None and not err:
        err = sfmodcheck.version(prog)
        if not err and prog.version < MIN_VERSION:
            err = (f"found sf-mod-check {'.'.join(map(str, prog.version))} at {prog.path}, but checking merged "
                   "patches needs 0.15.0 or later")
    elif prog is None and not err:
        err = "sf-mod-check wasn't found (on PATH, or with --sf-mod-check)"
    if err:
        why = err.rstrip(".")
        _add(ctx, "note", f"{len(manifests)} merged patch manifest(s) ({MANIFEST}) were found, but weren't checked: "
                          f"{why}.",
             f"Download sf-mod-check 0.15.0 or later from {sfmodcheck.RELEASES} and pass it with --sf-mod-check.",
             manifests=[m for m, _ in manifests])
        return _not_checked(why)

    work = os.path.join(ctx.work, "patches")
    os.makedirs(work, exist_ok=True)
    ptxt = sfmodcheck.write_plugins_txt(os.path.join(work, "plugins.txt"), lo)
    entries = []
    for n, (man, explicit) in enumerate(manifests, 1):
        name, why = _read_manifest(man)
        if name is None:
            _add(ctx, "caution", f"The merged patch manifest {man} wasn't used: {why}.",
                 "Give --patch-sources a patch-sources.json that sf-mod-check patch wrote, or remove the file."
                 if explicit else "Build the patch again with sf-mod-check patch, or remove the file.",
                 manifest=man)
            entries.append(_entry(None, man, 2, [], why=f"{MANIFEST} not used: {why}"))
            continue
        patch = _patch_file(lo, name, man)
        if patch is None:
            text = f"{name} isn't installed, so its manifest wasn't checked"
            fs = [{"level": "note", "rule": "patch-off", "plugin": name, "text": text + ".", "records": []}]
            _add(ctx, "note", f"Merged patch {name}: {text} ({man}).", None, [name], manifest=man)
            entries.append(_entry(name, man, 0, fs))
            continue
        out_json = os.path.join(work, f"{n}.json")
        if os.path.exists(out_json):
            os.remove(out_json)
        r = sfmodcheck.run_patch_check(prog, patch, man, ptxt, lo.data, out_json, timeout=TIMEOUT)
        got, jerr = (read_json(out_json) if os.path.isfile(out_json) else (None, "it wrote no --json file"))
        if r.start_error or r.timed_out or r.exit not in (0, 1, 2) or (got is None and r.exit != 2):
            why = (f"sf-mod-check couldn't be started ({r.start_error})" if r.start_error else
                   f"it didn't finish within {TIMEOUT} seconds" if r.timed_out else
                   f"exit {r.exit}, {r.result or 'no RESULT line'}" if r.exit not in (0, 1, 2) else jerr)
            code, fs, result = 2, [], r.result
        elif r.exit == 2:
            why = (got or {}).get("why") or r.result or "RESULT: CANNOT CHECK"
            code, fs, result = 2, (got or {}).get("findings", []), r.result
        else:
            why, code, fs, result = None, r.exit, got["findings"], got["result"] or r.result
        e = _entry(name, man, code, fs, result=result, why=why, smc_state=(got or {}).get("state"))
        entries.append(e)
        log.info(SECTION, f"merged patch {name}: {e['state']} (exit {r.exit}; {result})")
        _report(ctx, e)
    st = {"status": "done", "manifests": len(manifests),
          "rebuild": sum(1 for e in entries if e["rebuild"]), "program": prog.to_dict()}
    return entries, st


def _report(ctx, e):
    name = e["patch"]
    if e["state"] == "cannot check":
        _add(ctx, "caution", f"Couldn't check whether merged patch {name} is up to date: {e['reasons'][0]}.",
             f"Run sf-mod-check patch check \"{name}\" --log <file> to see why.", [name], manifest=e["manifest"])
        return
    if e["state"] == "rebuild" and not any(f["level"] != "note" for f in e["findings"]):
        _add(ctx, "caution", f"Merged patch {name} may need a rebuild: {EXIT1_NO_REASON}.",
             f"Run sf-mod-check patch check \"{name}\" --log <file> to see why.", [name], manifest=e["manifest"])
    elif e["state"] == "rebuild":
        bad = [f for f in e["findings"] if f["level"] != "note"]
        shown = "; ".join(f"[{f['rule']}] {f['text'].rstrip('.')}" for f in bad[:SHOWN])
        more = f"; and {len(bad) - SHOWN} more" if len(bad) > SHOWN else ""
        level = "problem" if any(f["level"] == "problem" for f in bad) else "caution"
        plugins = [name] + sorted({f["plugin"] for f in bad if f["plugin"] and f["plugin"] != name})
        _add(ctx, level, f"Merged patch {name} needs a rebuild ({len(bad)} reason(s)): {shown}{more}.",
             f"Run the sf-mod-check patch command that built {name} again, install the new build, and check it "
             "with sf-mod-check patch check.", plugins, manifest=e["manifest"],
             rules=sorted({f["rule"] for f in bad}))
    elif e["state"] == "up to date":
        _add(ctx, "note", f"Merged patch {name} is up to date (sf-mod-check patch check).", None, [name],
             manifest=e["manifest"])
    for f in e["findings"]:
        if f["level"] == "note":
            _add(ctx, "note", f"Merged patch {name}: [{f['rule']}] {f['text']}", None,
                 [name] + ([f["plugin"]] if f["plugin"] and f["plugin"] != name else []), manifest=e["manifest"])
