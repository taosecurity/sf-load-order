"""Find and run sf-mod-check (https://github.com/taosecurity/sf-mod-check), the plugin checker whose `conflict`
command compares a whole load order record by record. sf-load-order never reads records itself: it runs this
program and reads the conflicts.json it writes (records.py turns that into ratings and findings).

  find(args, path=None)            -> Program or None    --sf-mod-check, else sf-mod-check on PATH, else sfmodcheck
  version(prog)                    -> fills prog.version / prog.version_text, returns an error text or None
  too_old(prog)                    -> a plain sentence when the version is older than MIN_VERSION, else None
  write_plugins_txt(path, lo)      -> the load order as the game's Plugins.txt (mod plugins only; see below)
  run_conflict(prog, ...)          -> Run (exit code, RESULT line, conflicts.json path, timed out?)
  run_check(prog, plugin, data)    -> Run for `check <plugin>` (used at --depth heavy)
  run_patch_check(prog, ...)       -> Run for `patch check <patch>` (merged.py; needs sf-mod-check 0.15.0)

Exit codes of `sf-mod-check conflict` (its README, "Exit codes"): 0 report written, 2 couldn't run (bad arguments,
a missing file, out of memory; the RESULT line says CANNOT CHECK), 3 stopped at --max-memory (RESULT: STOPPED).
`check`: 0 no errors, 1 errors, 2 couldn't check. Only the exit code and the RESULT line are read from `conflict`
(its README: other stdout lines, such as Mutagen's own stack traces, may change between versions).

Mod Organizer 2: MO2 keeps each mod in its own folder and shows them to programs it starts through a virtual Data
folder. sf-mod-check reads plugins from --data, so it sees MO2's mods only when it is started from MO2 (directly,
or by starting sf-load-order from MO2). records.py checks that every enabled mod plugin is in the Data folder before
it runs anything, and says how to do this when they aren't.
"""
import os
import re
import subprocess
import time

from . import log, progfind

SECTION = "records"
MIN_VERSION = (0, 8, 0)
MO2_VERSION = (0, 16, 0)  # first sf-mod-check whose conflict command takes --mo2 and --profile
NAMES = ("sf-mod-check", "sfmodcheck")  # 0.8.0 renamed the command; its zip still holds sfmodcheck.cmd
RELEASES = "https://github.com/taosecurity/sf-mod-check/releases"
CONFLICT_TIMEOUT = 1800   # seconds; a 150-plugin order takes about 2 minutes on the author's PC
CHECK_TIMEOUT = 600       # seconds per plugin for `check` (a typical mod takes about 4 seconds)
VERSION_TIMEOUT = 60


class Program:
    def __init__(self, path, how):
        self.path = path            # full path of the exe (or .cmd)
        self.how = how              # "--sf-mod-check", "PATH sf-mod-check" or "PATH sfmodcheck"
        self.version = None         # (major, minor, patch) or None
        self.version_text = None    # the first line --version printed
        self.note = None            # set when a .cmd/.bat was replaced by an .exe found beside it (see unwrap_info)

    def to_dict(self):
        return {"path": self.path, "how": self.how,
                "version": ".".join(map(str, self.version)) if self.version else None,
                "version_text": self.version_text, "note": self.note}


class Run:
    def __init__(self, cmd):
        self.cmd = cmd
        self.exit = None            # exit code, None when it timed out or couldn't start
        self.result = None          # the last "RESULT:" line on stdout, None when there was none
        self.stdout = ""
        self.stderr = ""
        self.timed_out = False
        self.start_error = None     # why it couldn't start (OSError text, or why a wrapper call was refused)
        self.refused = False        # True when a .cmd/.bat wrapper call was refused (see wrapper_refusal)
        self.seconds = 0.0
        self.conflicts = None       # conflicts.json path (conflict runs only)


def find(args, path=None):
    """The program to run: --sf-mod-check (must exist), else sf-mod-check, else sfmodcheck on PATH.
    Returns (Program or None, error text or None). path: a PATH string (tests). A .cmd/.bat wrapper is replaced by
    the .exe it launches when that can be found (see unwrap)."""
    given = getattr(args, "sf_mod_check", None)
    if given:
        if os.path.isfile(given):
            log.info(SECTION, f"sf-mod-check from --sf-mod-check: {given}")
            return _program(os.path.abspath(given), "--sf-mod-check"), None
        if os.path.dirname(given):  # a path without .exe: C:\tools\sf-mod-check finds sf-mod-check.exe
            hit = progfind.given_path(given)
            if hit:
                log.info(SECTION, f"sf-mod-check from --sf-mod-check {given}: {hit}")
                return _program(hit, "--sf-mod-check"), None
        hit = progfind.which(given, path=path)
        if hit:
            log.info(SECTION, f"sf-mod-check from --sf-mod-check {given} on PATH: {hit}")
            return _program(hit, "--sf-mod-check"), None
        return None, (f"--sf-mod-check {given} doesn't exist. Fix: give the full path of sf-mod-check.exe "
                      f"(download it from {RELEASES}).")
    for name in NAMES:
        hit = progfind.which(name, path=path)
        if hit:
            log.info(SECTION, f"found {name} on PATH: {hit}")
            return _program(hit, f"PATH {name}"), None
        log.info(SECTION, f"no {name} on PATH")
    return None, None


# ---------- .cmd / .bat wrappers ----------
# Windows runs a .cmd or .bat through cmd.exe, which can read & | < > ^ % ! " in its arguments as commands,
# redirections or variables (Python's own quoting leaves an argument with no space unquoted). So a wrapper is
# replaced by the .exe it starts when that can be found; when it can't, arguments holding any of these characters
# are refused rather than passed to cmd.exe, and the rest are all quoted. ( ) are allowed: they are plain text
# inside quotes, and "Program Files (x86)" holds them.
WRAPPER_EXT = (".cmd", ".bat")
CMD_SPECIAL = set('&|<>^%!"\r\n')
# a wrapper line that only starts an exe with the wrapper's own arguments: [@]["][%~dp0]path.exe["] %*
SIMPLE_LAUNCH = re.compile(r'^\s*@?\s*"?(%~dp0)?([^"%*&|<>^]+?\.exe)"?\s+%\*\s*$', re.I)


def is_wrapper(path):
    return str(path).lower().endswith(WRAPPER_EXT)


PROGRAM_FILE = re.compile(r'([^\s"\'\\/:%=]+)\.(exe|com|cmd|bat|py|pyw|ps1|jar|js)\b', re.I)
INTERPRETERS = {"python", "python3", "pythonw", "py", "powershell", "pwsh", "node", "java", "javaw", "dotnet", "cmd",
                "wsl", "bash"}
SKIP_LINE = re.compile(r'^\s*@?\s*(rem\b|::|echo\b|echo\.|title\b|:)', re.I)


def names_other_program(lines, stem):
    """True when a wrapper's lines start or name a program other than sf-mod-check itself (stem or NAMES): another
    .exe/.bat/.py and so on, or an interpreter such as python. Comments, echo and labels are ignored."""
    ok = {stem.lower(), *NAMES}
    for line in lines:
        if not line.strip() or SKIP_LINE.match(line):
            continue
        line = re.sub(r"%~[a-z]*\d", "", line, flags=re.I)  # %~dp0 = the wrapper's own folder
        for m in PROGRAM_FILE.finditer(line):
            if m.group(1).lower() not in ok:
                return True
        words = re.sub(r'^\s*@?\s*((call|start)\s+)?', "", line, flags=re.I).split()
        first = words[0].strip('"').replace("/", "\\").split("\\")[-1].lower() if words else ""
        if os.path.splitext(first)[0] in INTERPRETERS:
            return True
    return False


def unwrap(path):
    """For a .cmd/.bat: the .exe it simply launches (a line like "%~dp0sf-mod-check.exe" %*), else an .exe with the
    same name, or one of NAMES, next to it when the wrapper names no other program. Anything else is returned
    unchanged."""
    return unwrap_info(path)[0]


def unwrap_info(path):
    """(path to run, note or None). The note is set when the .exe beside the wrapper was chosen (a guess, so the
    report says which .exe ran)."""
    if not is_wrapper(path):
        return path, None
    folder = os.path.dirname(os.path.abspath(path))
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read(64 * 1024).splitlines()
    except OSError:
        lines = []
    for line in lines:
        m = SIMPLE_LAUNCH.match(line)
        if not m:
            continue
        target = m.group(2).strip()
        exe = os.path.join(folder, target) if m.group(1) or not os.path.isabs(target) else target
        if os.path.isfile(exe):
            log.info(SECTION, f"{path} launches {exe}: running that directly")
            return os.path.abspath(exe), None
    stem = os.path.splitext(os.path.basename(path))[0]
    if names_other_program(lines, stem):
        log.info(SECTION, f"{path} starts another program, so no .exe beside it is guessed: run through cmd.exe, "
                          "arguments checked")
        return path, None
    for name in (stem,) + NAMES:
        exe = os.path.join(folder, name + ".exe")
        if os.path.isfile(exe):
            log.info(SECTION, f"{exe} sits next to {path}, which names no other program: running that directly")
            return exe, (f"{os.path.basename(path)} was given, and sf-load-order ran {exe}, which sits next to it, "
                         "instead of going through cmd.exe.")
    log.info(SECTION, f"{path} is a wrapper with no .exe found beside it: run through cmd.exe, arguments checked")
    return path, None


def _program(path, how):
    run_path, note = unwrap_info(path)
    prog = Program(run_path, how)
    prog.note = note
    return prog


def wrapper_refusal(cmd):
    """None, or a plain why when cmd starts a .cmd/.bat and an argument holds a character cmd.exe treats specially."""
    if not is_wrapper(cmd[0]):
        return None
    for a in cmd:
        bad = sorted(set(str(a)) & CMD_SPECIAL)
        if bad:
            return (f"{cmd[0]} is a .cmd/.bat wrapper, which Windows runs through cmd.exe, and the argument {a!r} "
                    f"holds {' '.join(bad)}, which cmd.exe would read as part of a command")
    return None


def _quote(a):
    """One argument in double quotes for a program's command line (backslashes before the closing quote doubled)."""
    a = str(a)
    tail = len(a) - len(a.rstrip("\\"))
    return '"' + a + "\\" * tail + '"'


def wrapper_command_line(cmd, comspec=None):
    """The cmd.exe command line for a wrapper with every part quoted: cmd /d /s /c ""<wrapper>" "<arg>" ...".
    Only for arguments that passed wrapper_refusal."""
    # never a bare "cmd.exe": Windows would also look for it in the current folder
    comspec = comspec or os.environ.get("ComSpec") or progfind.system_tool("cmd.exe")
    return f'{_quote(comspec)} /d /s /c "' + " ".join(_quote(a) for a in cmd) + '"'


def parse_version(text):
    m = re.search(r"\b(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def version(prog, timeout=VERSION_TIMEOUT):
    """Run `<prog> --version`. Fills prog.version; returns an error text or None."""
    r = _run([prog.path, "--version"], timeout)
    first = next((l.strip() for l in r.stdout.splitlines() if l.strip()), "")
    prog.version_text = first or None
    prog.version = parse_version(first)
    log.info(SECTION, f"{prog.path} --version: exit {r.exit}, {first!r}")
    if r.start_error:
        return f"{prog.path} couldn't be started ({r.start_error})."
    if r.timed_out:
        return f"{prog.path} --version didn't answer within {timeout} seconds."
    if prog.version is None:
        return f"{prog.path} --version printed no version number ({first or 'nothing'!r}); is it sf-mod-check?"
    return None


def too_old(prog):
    if prog.version is None or prog.version >= MIN_VERSION:
        return None
    need = ".".join(map(str, MIN_VERSION))
    return (f"found {os.path.basename(prog.path)} {'.'.join(map(str, prog.version))} at {prog.path} "
            f"({prog.how}), but sf-load-order needs sf-mod-check {need} or later. Fix: download the newest release "
            f"from {RELEASES} (it was renamed sf-mod-check in 0.8.0), or run the conflict command of the version "
            f"you have yourself and pass its conflicts.json with --conflicts.")


def plugins_txt_lines(lo):
    """What sf-mod-check conflict gets as --plugins: the enabled mod plugins in load order ('*' lines). Official
    masters are left out because sf-mod-check loads Bethesda's own files first by itself, as the game does; an
    official one turned off is written without '*' so it stays off. Names the game drops are left out: they never
    load."""
    out = ["# Written by sf-load-order for sf-mod-check conflict"]
    for p in lo.plugins:
        if p.dropped:
            continue
        if p.official:
            if not p.enabled:
                out.append(p.name)
        elif p.enabled:
            out.append("*" + p.name)
    return out


def write_plugins_txt(path, lo):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(plugins_txt_lines(lo)) + "\n")
    return path


def _num(x):
    return format(float(x), "g")


def run_conflict(prog, plugins_txt, data, out_dir, max_memory=None, timeout=CONFLICT_TIMEOUT, extra=None,
                 no_assets=False, mo2=None, profile=None):
    """Run `conflict` into out_dir. An old conflicts.json there is removed first, so a failed run is never mistaken
    for a finished one. extra: an --extra-data folder (plugins read before Data; `candidate` puts the build's plugins
    there). no_assets: pass --no-assets (records only). mo2 (+ profile): the MO2 instance folder, passed as
    --mo2 --profile instead of --plugins, so sf-mod-check sees MO2's mod folders (0.16.0)."""
    os.makedirs(out_dir, exist_ok=True)
    cj = os.path.join(out_dir, "conflicts.json")
    if os.path.exists(cj):
        os.remove(cj)
    if mo2:
        cmd = [prog.path, "conflict", "--mo2", mo2] + (["--profile", profile] if profile else [])
        cmd += ["--data", data, "--out", out_dir] if data else ["--out", out_dir]
    else:
        cmd = [prog.path, "conflict", "--plugins", plugins_txt, "--data", data, "--out", out_dir]
    if extra:
        cmd += ["--extra-data", extra]
    if no_assets:
        cmd.append("--no-assets")
    if max_memory is not None:
        cmd += ["--max-memory", _num(max_memory)]
    r = _run(cmd, timeout)
    r.conflicts = cj if os.path.isfile(cj) else None
    return r


def run_patch_check(prog, patch, manifest, plugins_txt, data, json_out, timeout=600):
    """Run `patch check <patch> --manifest <m> --plugins <txt> --data <folder> --json <file>` (sf-mod-check 0.15.0;
    exit 0 up to date, 1 rebuild or review, 2 couldn't check). merged.py reads the --json file."""
    cmd = [prog.path, "patch", "check", patch, "--manifest", manifest, "--plugins", plugins_txt, "--data", data,
           "--json", json_out]
    return _run(cmd, timeout)


CHECK_SUMMARY = re.compile(r", (\d+) records, (\d+) overrides \((\d+) ITM\)")
CHECK_DELETED = re.compile(r"^WARNING \[records\] (\d+) deleted (\w{4}) record")
CHECK_RESULT = re.compile(r"^RESULT: (\w[\w ]*?), (\d+) errors?, (\d+) warnings?")


def run_check(prog, plugin_path, data, timeout=CHECK_TIMEOUT):
    """Run `check <plugin>` (no hints, no assets, every line) and read what --depth heavy uses from it:
    {"exit", "result", "records", "overrides", "itms", "deleted": {sig: count}, "errors", "warnings", "why"}."""
    cmd = [prog.path, "check", plugin_path, "--data", data, "--no-hints", "--no-assets", "--max", "100000"]
    r = _run(cmd, timeout)
    out = {"exit": r.exit, "result": r.result, "records": None, "overrides": None, "itms": None, "deleted": {},
           "errors": None, "warnings": None, "why": None}
    for line in r.stdout.splitlines():
        m = CHECK_SUMMARY.search(line)
        if m and out["records"] is None:
            out["records"], out["overrides"], out["itms"] = (int(x) for x in m.groups())
        m = CHECK_DELETED.match(line.strip())
        if m:
            out["deleted"][m.group(2)] = out["deleted"].get(m.group(2), 0) + int(m.group(1))
    m = CHECK_RESULT.match(r.result or "")
    if m:
        out["errors"], out["warnings"] = int(m.group(2)), int(m.group(3))
    if r.start_error:
        out["why"] = f"couldn't start ({r.start_error})"
    elif r.timed_out:
        out["why"] = f"stopped after {timeout} seconds"
    elif r.exit not in (0, 1):
        out["why"] = f"couldn't check it (exit {r.exit}; {r.result or 'no RESULT line'})"
    return out


def _job_for(proc):
    """Windows: put the process in a job object, so a timeout stops it and everything it starts (a .cmd wrapper
    starts the real exe as a child). Returns the job handle, or None when that isn't possible."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        if not k32.AssignProcessToJobObject(job, int(proc._handle)):
            k32.CloseHandle(ctypes.c_void_p(job))
            return None
        return job
    except (OSError, AttributeError, ValueError):
        return None


def _close_job(job, kill=False):
    if not job:
        return
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if kill:
        k32.TerminateJobObject(ctypes.c_void_p(job), 1)
    k32.CloseHandle(ctypes.c_void_p(job))


def _kill_tree(proc, job):
    """Stop the program and anything it started."""
    if job:
        _close_job(job, kill=True)
    elif os.name == "nt":
        try:
            subprocess.run([progfind.system_tool("taskkill.exe"), "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run(cmd, timeout):
    r = Run(cmd)
    log.info(SECTION, f"run: {subprocess.list2cmdline(cmd)} (timeout {timeout} s)")
    t0 = time.monotonic()
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    refused = wrapper_refusal(cmd)
    if refused:
        r.start_error = refused
        r.refused = True
        log.info(SECTION, f"not run: {refused}")
        return r
    line = wrapper_command_line(cmd) if os.name == "nt" and is_wrapper(cmd[0]) else cmd
    try:
        proc = subprocess.Popen(line, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    except OSError as e:
        r.start_error = e.strerror or str(e)
        log.info(SECTION, f"couldn't start: {r.start_error}")
        return r
    job = _job_for(proc)
    try:
        out, err = proc.communicate(timeout=timeout)
        r.exit = proc.returncode
        _close_job(job)
    except subprocess.TimeoutExpired:
        _kill_tree(proc, job)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
        r.timed_out = True
    r.seconds = time.monotonic() - t0
    r.stdout = (out or b"").decode("utf-8", "replace")
    r.stderr = (err or b"").decode("utf-8", "replace")
    r.result = next((l.strip() for l in reversed(r.stdout.splitlines()) if l.strip().startswith("RESULT:")), None)
    log.info(SECTION, f"exit {r.exit}{' (timed out)' if r.timed_out else ''} after {r.seconds:.1f} s; {r.result}")
    if r.stderr.strip() and log.enabled():
        log.info(SECTION, "stderr (last lines): " + " | ".join(r.stderr.strip().splitlines()[-5:]))
    return r
