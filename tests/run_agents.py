"""Run every sf-load-order command in AGENTS.md against made-up data, and check what each one gives back.

  python -I tests\\run_agents.py           run them all; prints PASS or FAIL per command, exit 1 on any failure
  python -I tests\\run_agents.py --keep    keep the temp folder (its path is printed) to look at the reports

COMMANDS below is the list of commands, kept in step with AGENTS.md: each one's text must appear in AGENTS.md
word for word, and every sf-load-order command AGENTS.md shows (a code line or an inline `...` that starts with
".\\sf-load-order.cmd" or "python -I sf-load-order.py") must be in COMMANDS. Either mismatch is a FAIL. The
`python -m pip install lz4` step is not run: it changes the Python install.

Nothing real is read. Every run gets synthetic files from tests\\fixtures.py in a temp folder:
  - a game with a Data folder (plugins of each kind, archives that share a texture) and its Plugins.txt;
  - an MO2 instance whose ModOrganizer.ini points gamePath at that game;
  - a Vortex Load Order export and a staging folder;
  - a conflicts.json, a LOOT masterlist and userlist, a LOOT-sorted Plugins.txt, the examples\\*.json files;
  - a fake sf-mod-check (a small Python script behind a .cmd or shell wrapper) that says it is 0.8.0.
Commands that would read the real game use these instead, and the runner says so on the command's line:
  - LOCALAPPDATA and APPDATA point at the temp folder, so "the game's own Plugins.txt", the state folder, LOOT's
    folder and Vortex's default staging folder are all fake ones there;
  - --data <fake Data folder> is added when the command has no --data and no MO2 source (so Steam and Game Pass
    are never searched);
  - PATH holds only Python's folder and the system folders, so a real sf-mod-check is never found;
  - each command runs in its own empty folder (with a copy of examples\\), so the default report folder and a
    relative --log land there.
On Windows ".\\sf-load-order.cmd" runs the real launcher; elsewhere it runs "python -I sf-load-order.py", which is
what the launcher does.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests import fixtures  # noqa: E402
from tests.test_records import FAKE, field, lost  # noqa: E402  (the fake sf-mod-check the unit tests use)

AGENTS = os.path.join(ROOT, "AGENTS.md")
LAUNCHERS = (".\\sf-load-order.cmd", "python -I sf-load-order.py")
WIN = os.name == "nt"
ALT_PROFILE = "Alternate"  # the MO2 instance's second profile; "Default" is the selected one


def report_json(out):
    with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- checks run after a command
def has_report(r):
    for name in ("report.txt", "report.html", "report.json", "plugins.txt", "HOW-TO-APPLY.txt"):
        if not os.path.isfile(os.path.join(r["out"], name)):
            return f"{name} wasn't written to {r['out']}"
    return None


def section_done(name):
    def check(r):
        st = report_json(r["out"])["sections"].get(name, {})
        return None if st.get("status") == "done" else f"section {name} is {st}"
    return check


def section_skipped(name, why_part):
    def check(r):
        st = report_json(r["out"])["sections"].get(name, {})
        if st.get("status") != "skipped" or why_part not in (st.get("why") or ""):
            return f"section {name} should be skipped ({why_part}), but is {st}"
        return None
    return check


def source_is(src):
    def check(r):
        got = report_json(r["out"])["load_order"]["source"]
        return None if got == src else f"source is {got}, expected {src}"
    return check


def profile_is(name):
    def check(r):
        got = report_json(r["out"])["load_order"]["profile"]
        return None if got == name else f"profile is {got}, expected {name}"
    return check


def mo2_alternate_read(r):
    """The second, non-selected MO2 profile was read: it enables only the base mod's plugin."""
    on = {p["name"] for p in report_json(r["out"])["load_order"]["plugins"] if p.get("enabled")}
    if "ExampleMO2Base.esm" not in on or "ExampleMO2Patch.esm" in on:
        return f"the plugins enabled aren't the {ALT_PROFILE} profile's: {sorted(on)}"
    return None


def vortex_file(r):
    p = os.path.join(r["out"], "vortex-loadorder-recommended.json")
    return None if os.path.isfile(p) else "vortex-loadorder-recommended.json wasn't written"


def loose_done(r):
    st = report_json(r["out"])["sections"].get("loose", {})
    return None if st.get("status") == "done" else f"section loose is {st}"


def vortex_profile_read(r):
    vp = report_json(r["out"]).get("vortex_profile")
    if not vp or vp.get("name") != "Example-Profile":
        return f"vortex_profile is {vp}"
    return None if vp.get("deployed") is True else f"the profile wasn't found deployed: {vp}"


def patches_not_checked(r):
    p = report_json(r["out"]).get("patches")
    return None if isinstance(p, dict) and p.get("not_checked") else f"patches is {p}"


def candidate_ran(r):
    d = report_json(r["out"]).get("candidate")
    if not d or d.get("plugins") != ["ExampleNew.esm"]:
        return f"candidate is {d}"
    return None if d.get("shared") == 1 else f"expected 1 shared path, got {d.get('shared')}"


def heavy_ran(r):
    st = report_json(r["out"])["sections"].get("records", {})
    return None if "sf-mod-check check on" in str(st.get("heavy")) else f"heavy checks didn't run: {st}"


def max_memory_passed(r):
    with open(os.path.join(r["fx"]["bin"], "calls.jsonl"), encoding="utf-8") as f:
        calls = [json.loads(line) for line in f]
    ok = any(c and c[0] == "conflict" and "--max-memory" in c and c[c.index("--max-memory") + 1] == "8"
             for c in calls)
    return None if ok else f"--max-memory 8 wasn't passed on: {calls}"


def loot_read(r):
    texts = " ".join(f["text"] for f in report_json(r["out"])["findings"])
    return None if "Made-up masterlist warning." in texts and "Made-up userlist warning." in texts else \
        "the masterlist and userlist messages aren't in the findings"


def own_files_read(r):
    st = report_json(r["out"])["sections"].get("clashes", {})
    parts = st.get("parts") or {}
    bad = [k for k in ("clashes", "accept", "picks") if (parts.get(k) or {}).get("status") != "done"]
    return f"these parts weren't read: {bad} ({st})" if bad else None


def loot_order_compared(r):
    vl = report_json(r["out"])["results"].get("recommend", {}).get("vs_loot")
    return None if vl and not vl.get("error") else f"no LOOT comparison: {vl}"


def state_written(r):
    folder = r["subs"]["<folder>"]
    files = [n for n in os.listdir(folder) if n.endswith(".json")] if os.path.isdir(folder) else []
    return None if files else f"no snapshot in {folder}"


def no_state(r):
    folder = os.path.join(r["env"]["LOCALAPPDATA"], "sf-load-order")
    return f"{folder} was written with --no-state" if os.path.exists(folder) else None


def default_state(r):
    folder = os.path.join(r["env"]["LOCALAPPDATA"], "sf-load-order")
    return None if os.path.isdir(folder) and os.listdir(folder) else f"no snapshot in {folder}"


def log_written(r):
    p = os.path.join(r["cwd"], "sf-load-order-log.txt")
    if not os.path.isfile(p) or os.path.getsize(p) == 0:
        return f"{p} is missing or empty"
    if "[inputs]" not in r["stderr"]:
        return "--verbose wrote no log lines to stderr"
    return None


def quiet(r):
    lines = [x for x in r["stdout"].splitlines() if x.strip()]
    if not (len(lines) == 1 and lines[0].startswith("RESULT:")):
        return f"--quiet printed {lines}"
    return "--quiet still printed the byline on stderr" if "sf-load-order " in r["stderr"] else None


def byline_on_stderr(r):
    """The byline is the first stderr line; stdout holds only the RESULT line; the report files carry it too."""
    first = (r["stderr"].splitlines() or [""])[0]
    if not first.startswith("sf-load-order ") or " by " not in first or " - GPL-3.0 - https://" not in first:
        return f"the first stderr line isn't the byline: {first!r}"
    if [x for x in r["stdout"].splitlines() if x.strip() and not x.startswith("RESULT:")]:
        return f"stdout holds more than the RESULT line: {r['stdout']!r}"
    for name in ("report.txt", "report.html"):
        with open(os.path.join(r["out"], name), encoding="utf-8") as f:
            if first.split(" - https://")[0] not in f.read():
                return f"{name} lacks the byline"
    return None


def prints(text):
    def check(r):
        return None if text in r["stdout"] else f"stdout lacks {text!r}"
    return check


def version_lines_quoted(r):
    """AGENTS.md quotes the exact --version lines: the code block after "Expect exit code 0 and these ... lines" must
    hold the printed lines, in order, and nothing else."""
    with open(AGENTS, encoding="utf-8") as f:
        doc = f.read().splitlines()
    lines = [x.strip() for x in r["stdout"].splitlines() if x.strip()]
    start = next((i for i, x in enumerate(doc) if x.startswith("Expect exit code 0 and these ")), None)
    if start is None:
        return "AGENTS.md lacks the line \"Expect exit code 0 and these ... lines\" before the --version block"
    fences = [i for i in range(start + 1, len(doc)) if doc[i].strip().startswith("```")][:2]
    if len(fences) < 2:
        return "AGENTS.md has no code block after the --version line"
    block = [x.strip() for x in doc[fences[0] + 1:fences[1]] if x.strip()]
    return None if block == lines else f"AGENTS.md quotes {block}, but --version prints {lines}"


# ---------------------------------------------------------------- the commands (keep in step with AGENTS.md)
# text: exactly as in AGENTS.md. expect: exit code. checks: run after it. game: "ok" or "broken" (a plugin whose
# master is missing), used for the fake LOCALAPPDATA Plugins.txt and the added --data. report: False for commands
# that write no report (--version, --help).
C = r".\sf-load-order.cmd"
COMMANDS = [
    dict(text=C + " --version", expect=0, report=False, checks=[prints("sf-load-order "), version_lines_quoted]),
    dict(text="python -I sf-load-order.py --version", expect=0, report=False, checks=[prints("sf-load-order ")]),
    dict(text=C + " --help", expect=0, report=False, checks=[prints("--vortex-loadorder")]),
    dict(text=C, checks=[source_is("plugins.txt"), default_state,
                         section_skipped("records", "sf-mod-check wasn't found")]),
    dict(text=C + " --depth light", checks=[section_skipped("records", "--depth light")]),
    dict(text=C + ' --data "<Data folder>"', checks=[section_done("archives")]),
    dict(text=C + ' --plugins-txt "<Plugins.txt>" --data "<Data folder>"', checks=[source_is("plugins.txt")]),
    dict(text=C + ' --mo2 "<MO2 instance folder>"', checks=[source_is("mo2"), profile_is("Default"), loose_done]),
    dict(text=C + ' --mo2 "<MO2 instance folder>" --profile "<profile name>"',
         checks=[source_is("mo2"), profile_is(ALT_PROFILE), mo2_alternate_read]),
    dict(text=C + ' --vortex-loadorder "<exported file>"', checks=[source_is("vortex"), vortex_file, loose_done]),
    dict(text=C + ' --vortex-loadorder "<exported file>" --vortex-staging "<staging folder>"',
         checks=[source_is("vortex"), vortex_file, loose_done]),
    dict(text=C + ' --vortex-profile "Example-Profile" --vortex-exports "<exports folder>"'
                  ' --vortex-staging "<staging folder>"',
         checks=[source_is("vortex"), vortex_profile_read, loose_done]),
    dict(text=C + ' --sf-mod-check "<path to sf-mod-check.exe>" --patch-sources "<patch-sources.json>"',
         checks=[patches_not_checked]),
    dict(text=C + ' candidate "<build zip>" --mo2 "<MO2 instance folder>"', report=False, checks=[candidate_ran]),
    dict(text=C + " candidate --help", expect=0, report=False, checks=[prints("--vortex-profile")]),
    dict(text=C + ' --sf-mod-check "<path to sf-mod-check.exe>"', checks=[section_done("records")]),
    dict(text=C + ' --sf-mod-check "<path to sf-mod-check.exe>" --max-memory 8',
         checks=[section_done("records"), max_memory_passed]),
    dict(text=C + ' --depth heavy --sf-mod-check "<path to sf-mod-check.exe>"',
         checks=[section_done("records"), heavy_ran]),
    dict(text=C + ' --conflicts "<conflicts.json>"', checks=[section_done("records")]),
    dict(text=C + ' --loot-masterlist "<masterlist.yaml>" --loot-userlist "<userlist.yaml>"', checks=[loot_read]),
    dict(text=C + r" --clashes examples\clashes.example.json --accept examples\accept.example.json"
                  r" --picks examples\picks.example.json", checks=[own_files_read]),
    dict(text=C + ' --loot-order "<LOOT-sorted Plugins.txt>"', checks=[loot_order_compared]),
    dict(text=C + ' --state "<folder>"', checks=[state_written]),
    dict(text=C + " --no-state", checks=[no_state]),
    dict(text=C + ' --out "<report folder>"', checks=[]),
    dict(text=C + " --strict --quiet", game="broken", expect=1, checks=[quiet]),
    dict(text=C + " --verbose --log sf-load-order-log.txt", checks=[log_written]),
    dict(text='python -I sf-load-order.py --plugins-txt "<Plugins.txt>" --data "<Data folder>"',
         checks=[source_is("plugins.txt"), byline_on_stderr]),
]


# ---------------------------------------------------------------- fixtures
OK_PLUGINS = {
    "ExampleBase.esm": {"masters": ["Starfield.esm"]},
    "ExampleAddon.esm": {"masters": ["Starfield.esm", "ExampleBase.esm"], "kind": "L"},
    "ExampleRetexture.esm": {"masters": ["Starfield.esm"], "kind": "M"},
}
BROKEN_PLUGINS = {"ExamplePatch.esm": {"masters": ["Starfield.esm", "ExampleMissing.esm"]}}
MODS = {"ExampleBase.esm": "ExampleBase-1", "ExampleAddon.esm": "ExampleAddon-2",
        "ExampleRetexture.esm": "ExampleRetexture-3"}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def make_game(root, plugins):
    data, ptxt = fixtures.game(root, plugins)
    names = list(plugins)
    if "ExampleBase.esm" in plugins:
        fixtures.ba2(os.path.join(data, "ExampleBase - Main.ba2"), {"textures\\example\\shared.dds": b"base"})
        fixtures.ba2(os.path.join(data, "ExampleRetexture - Main.ba2"), {"textures\\example\\shared.dds": b"retex"})
    return {"data": data, "plugins_txt": ptxt, "names": names}


def make_fake_smc(folder, conflicts):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "fake_smc.py"), "w", encoding="utf-8") as f:
        f.write(FAKE)
    with open(os.path.join(folder, "fake.json"), "w", encoding="utf-8") as f:
        json.dump({"conflicts": conflicts}, f)
    script = os.path.join(folder, "fake_smc.py")
    if WIN:
        exe = os.path.join(folder, "sf-mod-check.cmd")
        with open(exe, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(f'@echo off\n"{sys.executable}" -I "{script}" %*\nexit /b %ERRORLEVEL%\n')
    else:
        exe = os.path.join(folder, "sf-mod-check")
        with open(exe, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n')
        os.chmod(exe, 0o755)
    return exe


def build(t):
    fx = {}
    fx["ok"] = make_game(os.path.join(t, "game"), OK_PLUGINS)
    fx["broken"] = make_game(os.path.join(t, "broken"), BROKEN_PLUGINS)
    data = fx["ok"]["data"]

    # MO2: two mods (a plugin and a loose file each, the file shared), gamePath -> the fake game
    mo2 = os.path.join(t, "mo2")
    fixtures.mo2(mo2, "Default", [
        ("ExampleMO2Base", True, {"ExampleMO2Base.esm": {"masters": ["Starfield.esm"]},
                                  "textures\\example\\loose.dds": b"one"}),
        ("ExampleMO2Patch", True, {"ExampleMO2Patch.esm": {"masters": ["Starfield.esm", "ExampleMO2Base.esm"]},
                                   "textures\\example\\loose.dds": b"two"}),
    ])
    with open(os.path.join(mo2, "ModOrganizer.ini"), "a", encoding="utf-8") as f:
        f.write("gamePath=@ByteArray(" + os.path.dirname(data).replace("\\", "\\\\") + ")\n")
    # a second profile, not the selected one: only the base mod is enabled there, so --profile is seen to be read
    alt = os.path.join(mo2, "profiles", ALT_PROFILE)
    write(os.path.join(alt, "modlist.txt"), "# This file was automatically generated by Mod Organizer.\n"
                                            "-ExampleMO2Patch\n+ExampleMO2Base\n")
    fixtures.plugins_txt(os.path.join(alt, "plugins.txt"), [("ExampleMO2Base.esm", True),
                                                             ("ExampleMO2Patch.esm", False)])
    write(os.path.join(alt, "loadorder.txt"), "ExampleMO2Base.esm\nExampleMO2Patch.esm\n")
    fx["mo2"] = mo2

    # Vortex: an export, the default staging folder (in the fake APPDATA) and another one for --vortex-staging
    fx["vortex"] = fixtures.vortex_loadorder(os.path.join(t, "vortex-loadorder.json"),
                                             [("Starfield.esm", True)] + [(n, True, MODS[n]) for n in OK_PLUGINS])

    def staging(folder):
        for n, mod in MODS.items():
            os.makedirs(os.path.join(folder, mod), exist_ok=True)
            shutil.copy2(os.path.join(data, n), os.path.join(folder, mod, n))
            write(os.path.join(folder, mod, "interface", "example", mod + ".txt"), mod)
        return folder
    fx["staging"] = staging(os.path.join(t, "vortex-staging"))
    fx["staging_default"] = staging(os.path.join(t, "env-template", "Roaming", "Vortex", "starfield", "mods"))

    # --vortex-profile: an export set (modlist, load order, plugins) of the profile "Example-Profile" for the ok game
    ex = os.path.join(t, "vortex-exports")
    os.makedirs(ex)
    stamp = "20261101-100000"
    entries = [("Starfield.esm", True)] + [(n, True, MODS[n]) for n in OK_PLUGINS]
    fixtures.vortex_loadorder(os.path.join(ex, f"vortex-starfield-Example-Profile-loadorder-backup-{stamp}.json"),
                              entries)
    with open(os.path.join(ex, f"vortex-starfield-Example-Profile-modlist-backup-{stamp}.json"), "w",
              encoding="utf-8") as f:
        json.dump([{"name": mod, "game": "starfield", "source": "nexus", "enabled": True, "vortexId": mod,
                    "installedGame": "starfield", "archive": mod + ".7z", "plugins": [n]}
                   for n, mod in MODS.items()], f)
    fixtures.plugins_txt(os.path.join(ex, f"vortex-starfield-Example-Profile-plugins-{stamp}.txt"),
                         [(n, True) for n in OK_PLUGINS])
    fx["exports"] = ex

    # --patch-sources: a manifest in the shape sf-mod-check patch writes
    fx["patch_sources"] = write(os.path.join(t, "patch-sources.json"), json.dumps({
        "schema": 1, "patch": {"name": "ExamplePatch.esm"}, "loadOrder": [{"name": "ExampleBase.esm"}]}))

    # candidate: a build whose plugin is new and whose one loose file also comes from an installed MO2 mod
    fx["build_zip"] = os.path.join(t, "ExampleNew-1.0.zip")
    pl = os.path.join(t, "build-parts", "ExampleNew.esm")
    fixtures.plugin(pl, masters=["Starfield.esm"])
    import zipfile
    with zipfile.ZipFile(fx["build_zip"], "w") as zf:
        zf.write(pl, "ExampleNew.esm")
        zf.writestr("textures/example/loose.dds", b"three")

    # sf-mod-check's conflicts.json for the ok game: the add-on loses a field to the retexture
    lo = [("Starfield.esm", True, [])] + [(n, False, s["masters"]) for n, s in OK_PLUGINS.items()]
    fx["conflicts"] = fixtures.conflicts_json(os.path.join(t, "conflicts.json"), lo, lost=[
        lost("000100:Starfield.esm", "ExampleBase.esm", "ExampleRetexture.esm", [field("Keywords")]),
        lost("000200:Starfield.esm", "ExampleAddon.esm", "ExampleRetexture.esm", [field("Name", winner_too=True)]),
    ])
    with open(fx["conflicts"], encoding="utf-8") as f:
        conflicts = json.load(f)
    fx["bin"] = os.path.join(t, "bin")
    fx["smc"] = make_fake_smc(fx["bin"], conflicts)

    # LOOT
    fx["masterlist"] = write(os.path.join(t, "loot", "masterlist.yaml"),
                             "plugins:\n  - name: 'ExampleBase.esm'\n    msg:\n      - type: warn\n"
                             "        content: 'Made-up masterlist warning.'\n")
    fx["userlist"] = write(os.path.join(t, "loot", "userlist.yaml"),
                           "plugins:\n  - name: 'ExampleAddon.esm'\n    msg:\n      - type: warn\n"
                           "        content: 'Made-up userlist warning.'\n")
    fx["loot_order"] = fixtures.plugins_txt(os.path.join(t, "loot-sorted", "Plugins.txt"),
                                            [(n, True) for n in reversed(list(OK_PLUGINS))])
    return fx


# ---------------------------------------------------------------- running
def split(text):
    """Split a command line on spaces, keeping "double-quoted" parts together (backslashes are literal)."""
    out, cur, quoted, any_ = [], "", False, False
    for ch in text:
        if ch == '"':
            quoted, any_ = not quoted, True
        elif ch == " " and not quoted:
            if cur or any_:
                out.append(cur)
            cur, any_ = "", False
        else:
            cur += ch
    if cur or any_:
        out.append(cur)
    return out


def argv_for(text, subs):
    if text.startswith(LAUNCHERS[0]):
        head = [os.path.join(ROOT, "sf-load-order.cmd")] if WIN else [sys.executable, "-I",
                                                                      os.path.join(ROOT, "sf-load-order.py")]
        rest = split(text[len(LAUNCHERS[0]):])
    elif text.startswith(LAUNCHERS[1]):
        head = [sys.executable, "-I", os.path.join(ROOT, "sf-load-order.py")]
        rest = split(text[len(LAUNCHERS[1]):])
    else:
        raise ValueError(f"not an sf-load-order command: {text}")
    args = []
    for a in rest:
        if a.startswith("<") and a.endswith(">"):
            if a not in subs:
                raise ValueError(f"no fixture for the placeholder {a}")
            a = subs[a]
        elif not WIN:
            a = a.replace("\\", "/")
        args.append(a)
    return head, args


def agents_commands(text):
    """Every sf-load-order command AGENTS.md shows: code-block lines and inline `...` spans."""
    found, in_code = set(), False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code and line.strip().startswith(LAUNCHERS):
            found.add(line.strip())
    i = 0
    while True:
        a = text.find("`", i)
        if a < 0:
            break
        if text.startswith("```", a):
            i = text.find("```", a + 3) + 3 if text.find("```", a + 3) >= 0 else len(text)
            continue
        b = text.find("`", a + 1)
        if b < 0:
            break
        span = text[a + 1:b]
        if "\n" not in span and span.startswith(LAUNCHERS):
            found.add(span)
        i = b + 1
    return found


def clean_env(t, name):
    env = dict(os.environ)
    base = os.path.join(t, "env", name)
    shutil.copytree(os.path.join(t, "env-template"), base)
    env["LOCALAPPDATA"] = os.path.join(base, "Local")
    env["APPDATA"] = os.path.join(base, "Roaming")
    sysdirs = []
    if WIN:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        sysdirs = [os.path.join(root, "System32"), root]
    else:
        sysdirs = ["/usr/bin", "/bin"]
    env["PATH"] = os.pathsep.join([os.path.dirname(sys.executable)] + sysdirs)
    return env


def run_one(i, cmd, fx, t):
    text = cmd["text"]
    game = fx[cmd.get("game", "ok")]
    cwd = os.path.join(t, "runs", str(i))
    os.makedirs(cwd)
    shutil.copytree(os.path.join(ROOT, "examples"), os.path.join(cwd, "examples"))
    env = clean_env(t, str(i))
    shutil.copy2(game["plugins_txt"], os.path.join(env["LOCALAPPDATA"], "Starfield", "Plugins.txt"))
    subs = {"<Data folder>": game["data"], "<Plugins.txt>": game["plugins_txt"], "<MO2 instance folder>": fx["mo2"],
            "<profile name>": ALT_PROFILE, "<exported file>": fx["vortex"], "<staging folder>": fx["staging"],
            "<path to sf-mod-check.exe>": fx["smc"], "<conflicts.json>": fx["conflicts"],
            "<masterlist.yaml>": fx["masterlist"], "<userlist.yaml>": fx["userlist"],
            "<LOOT-sorted Plugins.txt>": fx["loot_order"], "<folder>": os.path.join(t, "state-" + str(i)),
            "<report folder>": os.path.join(t, "report-" + str(i)), "<exports folder>": fx["exports"],
            "<patch-sources.json>": fx["patch_sources"], "<build zip>": fx["build_zip"]}
    head, args = argv_for(text, subs)
    notes = []
    if cmd.get("report", True):
        if "--data" not in args and "--mo2" not in args:
            args += ["--data", game["data"]]
            notes.append("--data <fake Data> added")
        if not any(a in args for a in ("--plugins-txt", "--mo2", "--vortex-loadorder")):
            notes.append("the game's Plugins.txt is a fake one in a temp LOCALAPPDATA")
    out = subs["<report folder>"] if "--out" in args else os.path.join(cwd, "sf-load-order-report")
    try:
        p = subprocess.run(head + args, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"couldn't run: {e}", notes
    r = {"stdout": p.stdout, "stderr": p.stderr, "out": out, "cwd": cwd, "env": env, "subs": subs, "fx": fx}
    want = cmd.get("expect", 0)
    if p.returncode != want:
        tail = (p.stdout.strip().splitlines() or [""])[-1]
        return False, f"exit {p.returncode}, expected {want} ({tail}; stderr: {p.stderr.strip()[-300:]})", notes
    problems = []
    checks = list(cmd.get("checks", []))
    if cmd.get("report", True):
        checks.insert(0, has_report)
    for check in checks:
        try:
            why = check(r)
        except (OSError, ValueError, KeyError) as e:
            why = f"{check.__name__}: {type(e).__name__}: {e}"
        if why:
            problems.append(why)
            break
    return (not problems), "; ".join(problems) or f"exit {p.returncode}", notes


def main(argv):
    keep = "--keep" in argv
    with open(AGENTS, encoding="utf-8") as f:
        agents = f.read()
    failed, passed = 0, 0
    declared = {c["text"] for c in COMMANDS}
    for c in COMMANDS:
        if c["text"] not in agents:
            print(f"FAIL  {c['text']}: not in AGENTS.md (update COMMANDS or AGENTS.md)")
            failed += 1
    for extra in sorted(agents_commands(agents) - declared):
        print(f"FAIL  {extra}: in AGENTS.md but not in tests\\run_agents.py COMMANDS")
        failed += 1
    t = tempfile.mkdtemp(prefix="sf-load-order-agents-")
    try:
        os.makedirs(os.path.join(t, "env-template", "Local", "Starfield"))
        fx = build(t)
        print(f"Running {len(COMMANDS)} AGENTS.md commands on made-up data in {t}"
              + (" (with the .cmd launcher)" if WIN else " (as python -I sf-load-order.py)"))
        for i, c in enumerate(COMMANDS):
            ok, why, notes = run_one(i, c, fx, t)
            note = f" [{'; '.join(notes)}]" if notes else ""
            print(f"{'PASS' if ok else 'FAIL'}  {c['text']}{note}" + ("" if ok else f": {why}"))
            passed += 1 if ok else 0
            failed += 0 if ok else 1
    finally:
        if keep:
            print(f"kept {t}")
        else:
            shutil.rmtree(t, ignore_errors=True)
    print(f"{passed} of {len(COMMANDS)} commands passed" + (f", {failed} failure(s)" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
