"""PMT ordering rules: the rules file sf-load-order-rules.json, schema 1 (DESIGN-0.3.0.md sections 2.1 to 2.5).

A rules file holds the player's own ordering rules ("kind": "user", by default in the state folder, see
default_path()) or a shared set ("kind": "community", given with --community-rules). Each rule names one plugin
exactly and says what it loads after or before, what it needs, what it can't run with, and which group it is in:

    {"schema": 1, "kind": "user", "about": {"name": ..., "url": ..., "version": ...},
     "groups": [{"name": "Late Patches", "after": ["default"], "note": "..."}],
     "rules": [{"id": "u-0001", "plugin": "MyPatch.esm", "after": ["ModA.esm"], "before": ["ModC.esm"],
                "requires": ["ModA.esm"], "incompatible": ["OldMyPatch.esp"], "group": "Late Patches",
                "note": "...", "evidence": {"kind": "choice", "text": "...", "link": "https://...",
                "records": ["0012AB:ModA.esm"], "files": {"MyPatch.esm": {"sha256": "<64 hex>"}},
                "tool": "sf-load-order 0.3.0", "date": "2026-11-24", "proposal": "p-..."}}]}

Reading (read()): plain json only, and a file is refused as a whole (RulesError) when it isn't there, is over 5 MB,
isn't JSON, has a schema other than 1, or goes past a limit (10,000 rules, 500 groups, a list of over 500 items, a
string of over 2,000 characters). An entry with an unknown field (fields starting with "_" are comments), a name that
isn't a plain plugin file name (no patterns or wildcards: a rule names files exactly) or no rule in it is skipped,
and RulesFile.problems says which and why. Nothing here writes, except write_atomic() (used only by `rules accept`).
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field

from . import log, paths
from .clashes import _is_plugin_name, _plugin_path, _sha256

SECTION = "rules"
FILE_NAME = "sf-load-order-rules.json"
SCHEMA = 1
KINDS = ("user", "community")
MAX_BYTES = 5 * 1024 * 1024
MAX_RULES = 10000
MAX_GROUPS = 500
MAX_LIST = 500
MAX_STR = 2000
MAX_RECORDS = 20
TOP_FIELDS = ("schema", "kind", "about", "groups", "rules")
ABOUT_FIELDS = ("name", "url", "version")
GROUP_FIELDS = ("name", "after", "note")
RULE_FIELDS = ("id", "plugin", "after", "before", "requires", "incompatible", "group", "note", "evidence")
LISTS = ("after", "before", "requires", "incompatible")
EVIDENCE_FIELDS = ("kind", "text", "link", "records", "files", "tool", "date", "proposal")
EVIDENCE_KINDS = ("master", "patch", "choice", "clash", "manual", "link")
PATTERN_CHARS = set('*?[]()|^$:{}<>"+')
SHA = re.compile(r"[0-9a-fA-F]{64}")
DEFAULT_GROUP = "default"


class RulesError(ValueError):
    """The whole file can't be used (exit 2 for `rules check`). The text names the file and says what to do."""


@dataclass
class RulesFile:
    path: str
    kind: str = "user"
    about: dict = field(default_factory=dict)
    groups: list = field(default_factory=list)   # [{"entry", "name", "after", "note"}]
    rules: list = field(default_factory=list)    # [{"entry" (1-based), "id", "plugin", lists..., "group", ...}]
    entries: int = 0                             # rules in the file, good and bad
    problems: list = field(default_factory=list)  # [{"entry", "what" ("rule" or "group"), "why"}]
    warnings: list = field(default_factory=list)  # file-level cautions that don't stop the file being used

    def label(self, r):
        """How a rule is named in reports: "entry 4" or "entry 4, id u-0004"."""
        return f"entry {r['entry']}" + (f", id {r['id']}" if r.get("id") else "")


def default_path(state=None, env=None):
    """The user rules file: <state folder>\\sf-load-order-rules.json (--state, else paths.default_state())."""
    return os.path.join(state or paths.default_state(env), FILE_NAME)


def is_name(s):
    """A plain plugin file name: .esm, .esp or .esl, no folder, no pattern or wildcard characters."""
    return _is_plugin_name(s) and not (set(s) & PATTERN_CHARS) and len(s) <= 260


def _limits(d):
    """The first limit a rules file goes past (a list of over MAX_LIST items, a string of over MAX_STR characters),
    or None. The top-level rules and groups lists are counted on their own (MAX_RULES, MAX_GROUPS)."""
    stack = []
    for k, v in d.items():
        if len(k) > MAX_STR:
            return f"the file has a field name of {len(k)} characters (the limit is {MAX_STR})"
        if k in ("rules", "groups") and isinstance(v, list):
            stack += [(x, f'"{k}"') for x in v]
        else:
            stack.append((v, f'"{k}"'))
    while stack:
        v, at = stack.pop()
        if isinstance(v, str):
            if len(v) > MAX_STR:
                return f"{at} holds a text of {len(v)} characters (the limit is {MAX_STR})"
        elif isinstance(v, list):
            if len(v) > MAX_LIST:
                return f"{at} holds a list of {len(v)} items (the limit is {MAX_LIST})"
            stack += [(x, at) for x in v]
        elif isinstance(v, dict):
            if len(v) > MAX_LIST:
                return f"{at} holds an object with {len(v)} fields (the limit is {MAX_LIST})"
            for k, x in v.items():
                if len(k) > MAX_STR:
                    return f"{at} has a field name of {len(k)} characters (the limit is {MAX_STR})"
                stack.append((x, at))
    return None


def load_json(path):
    """The parsed file, or RulesError naming the file and the fix."""
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        raise RulesError(f"{path}: file not found. Fix: check the path.")
    except OSError as e:
        raise RulesError(f"{path}: can't read it ({e.strerror}). Fix: check that the file can be opened.")
    if size > MAX_BYTES:
        raise RulesError(f"{path}: the file is {size // 1024} KB, over the {MAX_BYTES // (1024 * 1024)} MB limit for a "
                         "rules file. Fix: split it, or remove old rules.")
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except OSError as e:
        raise RulesError(f"{path}: can't read it ({e.strerror}). Fix: check that the file can be opened.")
    except UnicodeDecodeError:
        raise RulesError(f"{path}: isn't UTF-8 text. Fix: save it as UTF-8 (any text editor can).")
    except json.JSONDecodeError as e:
        raise RulesError(f"{path}: line {e.lineno}, column {e.colno}: {e.msg}. Fix: correct the JSON there (a "
                         "missing comma, quote or bracket is the usual cause).")
    except RecursionError:
        raise RulesError(f"{path}: the JSON is nested too deeply. Fix: see the README for the format.")


def read(path, expect_kind=None):
    """Read and check a rules file. Returns RulesFile; raises RulesError when the whole file can't be used.
    expect_kind: "user" or "community" (where it was given); the other kind is a warning, not a refusal."""
    d = load_json(path)
    if not isinstance(d, dict):
        raise RulesError(f"{path}: a rules file is a JSON object ({{\"schema\": 1, \"rules\": [...]}}). Fix: see the "
                         "README and examples\\sf-load-order-rules.example.json for the format.")
    schema = d.get("schema")
    if isinstance(schema, int) and not isinstance(schema, bool) and schema > SCHEMA:
        raise RulesError(f"{path}: schema {schema} was made by a newer sf-load-order (this one reads schema "
                         f"{SCHEMA}). Fix: update sf-load-order.")
    if schema != SCHEMA:
        raise RulesError(f"{path}: \"schema\" must be {SCHEMA}. Fix: add \"schema\": {SCHEMA} at the top of the file.")
    extra = [k for k in d if k not in TOP_FIELDS and not k.startswith("_")]
    if extra:
        raise RulesError(f"{path}: unknown top-level field {extra[0]!r} (allowed: {', '.join(TOP_FIELDS)}). Fix: "
                         "remove it, or start its name with _ to keep it as a comment.")
    rules, groups = d.get("rules", []), d.get("groups", [])
    if not isinstance(rules, list) or not isinstance(groups, list):
        raise RulesError(f"{path}: \"rules\" and \"groups\" must be JSON lists. Fix: see the README for the format.")
    if len(rules) > MAX_RULES:
        raise RulesError(f"{path}: {len(rules)} rules, over the limit of {MAX_RULES}. Fix: split the file.")
    if len(groups) > MAX_GROUPS:
        raise RulesError(f"{path}: {len(groups)} groups, over the limit of {MAX_GROUPS}. Fix: use fewer groups.")
    over = _limits(d)
    if over:
        raise RulesError(f"{path}: {over}. Fix: shorten it.")
    kind = d.get("kind", "user")
    if kind not in KINDS:
        raise RulesError(f"{path}: \"kind\" must be \"user\" or \"community\", not {kind!r}. Fix: correct it.")
    about = d.get("about", {})
    if not isinstance(about, dict) or any(k not in ABOUT_FIELDS and not k.startswith("_") for k in about) or \
            any(not isinstance(v, str) for k, v in about.items() if not k.startswith("_")):
        raise RulesError(f"{path}: \"about\" must be an object with text fields {', '.join(ABOUT_FIELDS)}. Fix: "
                         "correct it.")
    rf = RulesFile(path, kind, {k: v for k, v in about.items() if not k.startswith("_")}, entries=len(rules))
    if expect_kind and kind != expect_kind:
        if expect_kind == "community":
            rf.warnings.append(f"{path} is a user rules file (\"kind\": \"user\"), given as a community file. Its "
                               "rules were used at the community rank. Fix: give your own rules with --rules, and a "
                               "shared file with --community-rules.")
        else:
            rf.warnings.append(f"{path} is a community rules file (\"kind\": \"community\"), given as your own rules "
                               "file. Its rules were used at your rules' rank. Fix: give it with --community-rules.")
    gnames = set()
    for i, g in enumerate(groups):
        errs = _group_errors(g)
        if not errs and g["name"].lower() in gnames:
            errs.append(f"the group {g['name']!r} is defined twice")
        if errs:
            rf.problems.append({"entry": i + 1, "what": "group", "why": "; ".join(errs)})
            continue
        gnames.add(g["name"].lower())
        rf.groups.append({"entry": i + 1, "name": g["name"], "after": list(g.get("after") or []),
                          "note": g.get("note")})
    ids = {}
    for i, r in enumerate(rules):
        errs = _rule_errors(r)
        if not errs and r.get("id"):
            if r["id"].lower() in ids:
                errs.append(f"the id {r['id']!r} is already used by entry {ids[r['id'].lower()]}")
        if errs:
            rf.problems.append({"entry": i + 1, "what": "rule", "why": "; ".join(errs)})
            continue
        if r.get("id"):
            ids[r["id"].lower()] = i + 1
        item = {"entry": i + 1, "id": r.get("id"), "plugin": r["plugin"], "group": r.get("group"),
                "note": r.get("note"), "evidence": r.get("evidence")}
        for k in LISTS:
            item[k] = list(r.get(k) or [])
        rf.rules.append(item)
    log.info(SECTION, f"{path}: kind {kind}, {len(rf.rules)} usable rule(s) of {len(rules)}, {len(rf.groups)} "
                      f"group(s), {len(rf.problems)} skipped")
    return rf


def _text_errors(e, keys):
    return [f"\"{k}\" must be text" for k in keys if k in e and not isinstance(e[k], str)]


def _unknown(e, allowed):
    return [f"unknown field {k!r} (allowed: {', '.join(allowed)})" for k in e if k not in allowed
            and not k.startswith("_")]


def _group_errors(g):
    if not isinstance(g, dict):
        return ["the group isn't a JSON object ({...})"]
    errs = _unknown(g, GROUP_FIELDS)
    if not isinstance(g.get("name"), str) or not g["name"].strip():
        errs.append("\"name\" must be a group name (text)")
    a = g.get("after", [])
    if not isinstance(a, list) or not all(isinstance(x, str) and x.strip() for x in a):
        errs.append("\"after\" must be a list of group names")
    elif isinstance(g.get("name"), str) and any(x.lower() == g["name"].lower() for x in a):
        errs.append("a group can't load after itself")
    return errs + _text_errors(g, ("note",))


def _rule_errors(r):
    if not isinstance(r, dict):
        return ["the entry isn't a JSON object ({...})"]
    errs = _unknown(r, RULE_FIELDS)
    if "plugin" not in r:
        errs.append("the field 'plugin' is missing")
    elif not is_name(r["plugin"]):
        errs.append("\"plugin\" must be a plugin file name (.esm, .esp or .esl, no folder, no pattern or wildcard)")
    for k in LISTS:
        if k in r:
            v = r[k]
            if not isinstance(v, list) or not all(is_name(x) for x in v):
                errs.append(f"\"{k}\" must be a list of plugin file names (exact names, no patterns or wildcards)")
            elif isinstance(r.get("plugin"), str) and any(x.lower() == r["plugin"].lower() for x in v):
                errs.append(f"\"{k}\" names the rule's own plugin")
    if "group" in r and (not isinstance(r["group"], str) or not r["group"].strip()):
        errs.append("\"group\" must be a group name (text)")
    if not any(r.get(k) for k in LISTS + ("group",)):
        errs.append("the rule says nothing: give at least one of after, before, requires, incompatible or group")
    if "id" in r and (not isinstance(r["id"], str) or not r["id"].strip()):
        errs.append("\"id\" must be text")
    errs += _text_errors(r, ("note",))
    if "evidence" in r:
        errs += _evidence_errors(r["evidence"])
    return errs


def _evidence_errors(ev):
    if not isinstance(ev, dict):
        return ["\"evidence\" must be a JSON object"]
    errs = [f"evidence: {x}" for x in _unknown(ev, EVIDENCE_FIELDS)]
    if "kind" in ev and ev["kind"] not in EVIDENCE_KINDS:
        errs.append(f"evidence \"kind\" must be one of {', '.join(EVIDENCE_KINDS)}")
    errs += [f"evidence: {x}" for x in _text_errors(ev, ("text", "link", "tool", "date", "proposal"))]
    if isinstance(ev.get("link"), str) and not ev["link"].lower().startswith(("https://", "http://")):
        errs.append("evidence \"link\" must start with https:// or http://")
    recs = ev.get("records", [])
    if not isinstance(recs, list) or len(recs) > MAX_RECORDS or not all(isinstance(x, str) for x in recs):
        errs.append(f"evidence \"records\" must be a list of at most {MAX_RECORDS} FormKeys (text)")
    files = ev.get("files", {})
    if not isinstance(files, dict) or not all(is_name(k) and isinstance(v, dict) and isinstance(v.get("sha256"), str)
                                              and SHA.fullmatch(v["sha256"]) for k, v in files.items()):
        errs.append("evidence \"files\" must map plugin file names to {\"sha256\": \"<64 hex digits>\"}")
    return errs


# ---------- what a file says, in plain words ----------
def describe(r):
    """One rule in words: "MyPatch.esm loads after ModA.esm and ModB.esm; is in the group Late Patches"."""
    bits = []
    if r["after"]:
        bits.append("loads after " + _and(r["after"]))
    if r["before"]:
        bits.append("loads before " + _and(r["before"]))
    if r["requires"]:
        bits.append("needs " + _and(r["requires"]))
    if r["incompatible"]:
        bits.append("can't run with " + _and(r["incompatible"]))
    if r.get("group"):
        bits.append(f"is in the group {r['group']}")
    return f"{r['plugin']} " + "; ".join(bits)


def _and(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def names_in(r):
    """Every plugin a rule names (the plugin first)."""
    return [r["plugin"]] + [x for k in LISTS for x in r[k]]


# ---------- stale pins (2.5) ----------
def stale(rf, lo, cache=None):
    """The rules whose evidence.files hashes no longer match the installed plugins: [{"entry", "id", "plugin",
    "changed": [plugin names]}]. A plugin that isn't installed doesn't make a rule stale (the rule is inactive)."""
    cache = {} if cache is None else cache
    out = []
    for r in rf.rules:
        files = (r.get("evidence") or {}).get("files") or {}
        changed = []
        for name, f in files.items():
            path = _plugin_path(lo, name)
            if not path:
                continue
            sha, _size = _sha256(path, cache)
            if sha and sha.lower() != f["sha256"].lower():
                changed.append(name)
        if changed:
            out.append({"entry": r["entry"], "id": r.get("id"), "plugin": r["plugin"], "changed": changed})
    return out


# ---------- proposals (2.4) ----------
def proposal_id(plugin, after, kind):
    """p- plus the first 10 hex digits of SHA-256("plugin|after names|kind"), lower case and sorted, so the same
    proposal gets the same id on every run."""
    key = f"{plugin.lower()}|{','.join(sorted(a.lower() for a in after))}|{kind}"
    return "p-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def file_hashes(lo, names, cache=None):
    """{name: {"sha256": ...}} for the installed copies of these plugins (a file that can't be read is left out)."""
    cache = {} if cache is None else cache
    out = {}
    for n in names:
        path = _plugin_path(lo, n)
        sha = _sha256(path, cache)[0] if path else None
        if sha:
            out[n] = {"sha256": sha}
    return out


def to_json(rf_kind, rules, groups=None, about=None):
    """A schema 1 document."""
    d = {"schema": SCHEMA, "kind": rf_kind}
    if about:
        d["about"] = about
    if groups:
        d["groups"] = groups
    d["rules"] = rules
    return d


def write_atomic(path, doc, backup=True):
    """Write a rules file through a temporary file in the same folder and os.replace, so a crash never leaves half
    a file. With backup, an existing file is first copied to <path>.bak. Returns the backup path or None."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    bak = None
    if backup and os.path.isfile(path):
        bak = path + ".bak"
        shutil.copyfile(path, bak)
    fd, tmp = tempfile.mkstemp(prefix=".sf-load-order-rules-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return bak
