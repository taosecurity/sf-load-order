"""Command line and the run pipeline.

Each check lives in its own module with run(ctx) (see Context). The pipeline calls them in order; a check that raises
marks its section "failed" in the report and the run carries on, so one broken check never hides the others. (A
missing module is a packaging error, and it stops the run.)

Exit codes: 0 report written; 1 report written and --strict was given and there are problems; 2 couldn't run (bad
options, no load order found, output folder not writable, no report file could be written).
"""
import argparse
import importlib
import os
import sys
import traceback

from . import BYLINE, CREDITS, URL, __version__, log
from .model import SECTIONS, Report

MAIN_SECTIONS = tuple(s for s in SECTIONS if s != "candidate")

# (section, module) in run order. Each module has run(ctx) -> None or a section status dict.
PIPELINE = [
    ("order", "order"),        # masters, missing files, slots, dropped names, BlueprintShips last
    ("patches", "patches"),    # patches built against an older version of a master
    ("clashes", "clashes"),    # LOOT masterlist + the player's own --clashes / --accept
    ("archives", "archives"),  # BA2 overlap and archive names
    ("loose", "loose"),        # loose files (needs mod folders: MO2 or Vortex staging)
    ("rules", "rules"),        # Vortex mod rules against the loose pairs (needs a modlist export)
    ("records", "records"),    # sf-mod-check conflict (depth medium and heavy)
    ("recommend", "recommend"),  # score, moves, files to apply
]
DEPTHS = ("light", "medium", "heavy")
SKIPPED_AT_LIGHT = {"records"}
NEEDS_DEPLOYED = {"records", "patches"}  # both read the plugin files in Data
CANDIDATE_SECTIONS = ("inputs", "candidate")  # the sections a `candidate` run reports


class Context:
    """What every check gets: the options, the load order, the report to add findings to, a work folder."""

    def __init__(self, args, load_order, report):
        self.args = args
        self.load_order = load_order
        self.report = report
        self.work = os.path.join(args.out, "work")
        self.results = {}  # section -> whatever a check wants later checks to see (records -> conflicts data)
        self.sections = MAIN_SECTIONS  # the sections the report shows (CANDIDATE_SECTIONS for `candidate`)


class _Version(argparse.Action):
    """--version: the byline and credits as written (argparse's own version action re-wraps the lines)."""

    def __init__(self, option_strings, dest, **kw):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kw)

    def __call__(self, parser, namespace, values, option_string=None):
        print(BYLINE + "\n" + CREDITS)
        parser.exit()


def parser():
    p = argparse.ArgumentParser(
        prog="sf-load-order",
        description="Check a Starfield load order: masters, missing files, clashes, archive overlap, patches built "
                    "against an older version of a mod, then rate it and suggest a better order. Reads only; "
                    "writes only to --out.",
        epilog="To check a new mod's zip against this load order before installing it: sf-load-order candidate "
               "<zip> [<zip> ...] (sf-load-order candidate --help shows its options).",
        allow_abbrev=False)
    _source_options(p)
    chk = p.add_argument_group("checks")
    chk.add_argument("--depth", choices=DEPTHS, default=None,
                     help="light: order, headers, archives (seconds). medium: adds record conflicts via sf-mod-check "
                          "(default when sf-mod-check is found). heavy: adds ITM and deleted-reference checks")
    chk.add_argument("--sf-mod-check", metavar="EXE", help="the sf-mod-check program (default: found on PATH)")
    chk.add_argument("--conflicts", metavar="FILE", help="reuse a conflicts.json from an earlier sf-mod-check conflict run")
    chk.add_argument("--max-memory", metavar="GB", type=float, default=None, help="passed to sf-mod-check conflict")
    chk.add_argument("--loot-masterlist", metavar="FILE",
                     help="LOOT's Starfield masterlist.yaml (default: %%LOCALAPPDATA%%\\LOOT\\games\\Starfield\\masterlist.yaml)")
    chk.add_argument("--loot-userlist", metavar="FILE", help="LOOT's userlist.yaml (default: next to the masterlist)")
    chk.add_argument("--clashes", metavar="FILE", help="your own known-clashes file (format in the README)")
    chk.add_argument("--accept", metavar="FILE", help="clashes you have accepted, pinned to the files' hashes")
    chk.add_argument("--picks", metavar="FILE", help="your own order picks: plugin X loads right after plugin Y")
    chk.add_argument("--loot-order", metavar="FILE", help="a Plugins.txt sorted by LOOT, to compare with")
    chk.add_argument("--patch-sources", metavar="FILE|FOLDER", action="append",
                     help="a patch-sources.json (or a folder of them) from sf-mod-check patch, checked with sf-mod-check "
                          "patch check besides those found next to the enabled plugins (may be given more than once)")
    _state_options(chk)
    _output_options(p, "the report and the files to apply")
    return p


def candidate_parser():
    p = argparse.ArgumentParser(
        prog="sf-load-order candidate",
        description="Check a new mod build (its .zip, or the zips of its parts) against the enabled mods of a load "
                    "order before installing it: script paths other mods also ship, shared files with different "
                    "content, and paths two parts of the build both ship. Reads only; writes only to --out.",
        allow_abbrev=False)
    p.add_argument("zips", metavar="ZIP", nargs="+", help="the build's .zip file(s); several are the parts of one "
                                                          "build (one plugin each)")
    _source_options(p)
    chk = p.add_argument_group("checks")
    chk.add_argument("--depth", choices=DEPTHS, default=None,
                     help="light: names only. medium (default): compares the contents of shared files. heavy: adds "
                          "the cleaning check (sf-mod-check check on each build plugin; its masters must be in Data)")
    chk.add_argument("--sf-mod-check", metavar="EXE",
                     help="the sf-mod-check program, for --depth heavy (default: found on PATH)")
    _state_options(chk)
    _output_options(p, "the report")
    return p


def _state_options(chk):
    chk.add_argument("--state", metavar="FOLDER",
                     help="where to keep the snapshot used for 'changed since the last run' and the file caches "
                          "(default: %%LOCALAPPDATA%%\\sf-load-order)")
    chk.add_argument("--no-state", action="store_true", help="don't read or write the snapshot or the caches")


def _output_options(p, what):
    out = p.add_argument_group("output")
    out.add_argument("--out", metavar="FOLDER", default="sf-load-order-report",
                     help=f"where {what} go (default: .\\sf-load-order-report)")
    out.add_argument("--strict", action="store_true", help="exit 1 when the report lists any problem")
    out.add_argument("--quiet", action="store_true", help="print only the RESULT line")
    out.add_argument("--verbose", action="store_true", help="write the troubleshooting log to the screen (stderr)")
    out.add_argument("--log", metavar="FILE", help="write the troubleshooting log to this file")
    out.add_argument("--version", action=_Version, help="show the version, author, license and credits, then stop")


def _source_options(p):
    src = p.add_argument_group("where the load order comes from (default: the game's own Plugins.txt)")
    src.add_argument("--plugins-txt", metavar="FILE", help="a Plugins.txt to check (default: %%LOCALAPPDATA%%\\Starfield\\Plugins.txt)")
    src.add_argument("--mo2", metavar="FOLDER", help="a Mod Organizer 2 instance folder (holds profiles\\ and mods\\)")
    src.add_argument("--profile", metavar="NAME", help="the MO2 profile (default: MO2's selected profile)")
    src.add_argument("--vortex-loadorder", metavar="FILE",
                     help="a load order file exported from Vortex's Load Order page (Export button)")
    src.add_argument("--vortex-modlist", metavar="FILE",
                     help="a modlist export from the Modlist Backup extension, read next to --vortex-loadorder")
    src.add_argument("--vortex-profile", metavar="NAME",
                     help="a Vortex profile: its newest export set (modlist and load order) from the Modlist Backup "
                          "extension, and a check that Data holds this profile")
    src.add_argument("--vortex-exports", metavar="FOLDER", action="append",
                     help="where the extension's export sets are (may be given more than once; default: the folder "
                          "in %%APPDATA%%\\Vortex\\modlist-backup-auto-export.json and its old subfolder)")
    src.add_argument("--vortex-stamp", metavar="YYYYMMDD-HHMMSS",
                     help="with --vortex-profile: use the export set with this stamp instead of the newest")
    src.add_argument("--vortex-staging", metavar="FOLDER",
                     help="Vortex's mod staging folder, for loose-file checks (default: %%APPDATA%%\\Vortex\\starfield\\mods)")
    src.add_argument("--data", metavar="FOLDER",
                     help="the game's Data folder (default: Steam's, then Game Pass's XboxGames folder on each drive)")


def _module(name):
    """A check's module. One that is missing is a packaging error, so the ImportError is left to stop the run."""
    return importlib.import_module(f"{__package__}.{name}")


def read_load_order(args):
    """Returns (LoadOrder, None) or (None, error text)."""
    return _module("inputs").read(args)


def run_candidate(args):
    """`candidate <zip>...`: the inputs and candidate sections only."""
    report = Report(tool_version=__version__)
    lo, err = read_load_order(args)
    if lo is None:
        print(f"RESULT: CANNOT CHECK: {err}")
        return 2, report
    report.load_order = lo
    report.findings += _module("inputs").findings(lo)
    report.sections["inputs"] = {"status": "done"}
    report.vortex_profile = getattr(lo, "_vortex_profile", None)
    ctx = Context(args, lo, report)
    ctx.sections = CANDIDATE_SECTIONS
    cand = _module("candidate")
    try:
        report.candidate = cand.run(ctx, args.zips)
        report.sections["candidate"] = {"status": "done"}
    except cand.CandidateError as e:
        log.info("candidate", str(e))
        print(f"RESULT: CANNOT CHECK: {e}")
        return 2, report
    except Exception as e:  # a bug: the report still says what was read
        log.info("candidate", "failed:\n" + traceback.format_exc())
        report.sections["candidate"] = {"status": "failed", "why": f"{type(e).__name__}: {e}"}
    writer = _module("report")
    try:
        report.outputs.update(writer.write(ctx))
    except Exception as e:
        log.info("report", "writing the report failed:\n" + traceback.format_exc())
        report.sections["report"] = {"status": "failed", "why": f"{type(e).__name__}: {e}"}
    return 0, report


def run(args):
    if getattr(args, "zips", None) is not None:
        return run_candidate(args)
    report = Report(tool_version=__version__)
    lo, err = read_load_order(args)
    if lo is None:
        print(f"RESULT: CANNOT CHECK: {err}")
        return 2, report
    report.load_order = lo
    report.findings += _module("inputs").findings(lo)
    report.sections["inputs"] = {"status": "done"}
    log.info("inputs", f"{lo.source}: {len(lo.enabled())} enabled plugins, data {lo.data}")
    vp = getattr(lo, "_vortex_profile", None)
    report.vortex_profile = vp
    ctx = Context(args, lo, report)
    depth = args.depth or "medium"
    for section, modname in PIPELINE:
        if depth == "light" and section in SKIPPED_AT_LIGHT:
            report.sections[section] = {"status": "skipped", "why": "--depth light"}
            continue
        if vp and vp.get("deployed") is False and section in NEEDS_DEPLOYED:
            report.sections[section] = {"status": "skipped", "why": f"Data holds another profile, not {vp['name']} "
                                        f"(deploy {vp['name']} in Vortex and run again)"}
            if section == "patches":
                report.patches = {"not_checked": report.sections[section]["why"]}
            continue
        try:  # the import too: a check that can't load is marked failed, and the others still run
            status = _module(modname).run(ctx) or {"status": "done"}
        except Exception as e:  # one broken check must not hide the others
            log.info(section, "failed:\n" + traceback.format_exc())
            status = {"status": "failed", "why": f"{type(e).__name__}: {e}"}
        report.sections[section] = status
    # A report writer that can't load is a broken install: main() says so (CANNOT CHECK, unzip again). Only the
    # import goes that way; write() catches its own errors per file, and main() reports a run that wrote none.
    writer = _module("report")
    try:
        report.outputs.update(writer.write(ctx))
    except Exception as e:  # a bug in the writer, not a disk problem: main() names it as such
        log.info("report", "writing the report failed:\n" + traceback.format_exc())
        report.sections["report"] = {"status": "failed", "why": f"{type(e).__name__}: {e}"}
    return 0, report


REPORT_ROLES = ("txt", "html", "json")


def _utf8_console():
    """Keep the console's own encoding, but write a character it can't show as an escape (\\u0141) instead of
    stopping, so a run whose output is redirected never fails on a path (a Windows default like cp1252), and tools
    that read the output in the console's code page still read every other character as it is."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="backslashreplace")
            except (OSError, ValueError):
                pass


def main(argv=None):
    _utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "candidate":  # read before the main parser, so `sf-load-order [options]` is unchanged
        args = candidate_parser().parse_args(argv[1:])
    else:
        args = parser().parse_args(argv)  # unknown option or a missing value: argparse prints why and exits 2
    out_err = None
    try:  # first, so a --log file inside --out can be opened
        os.makedirs(args.out, exist_ok=True)
    except (OSError, ValueError) as e:
        out_err = (f"can't create the output folder {args.out}: {getattr(e, 'strerror', None) or e}. "
                   "Fix: give --out a folder you can write to.")
    err = log.setup(args.verbose, args.log)
    if err:  # when --out failed too (a --log inside it can't open either), the --out problem is the one to fix
        print(f"RESULT: CANNOT CHECK: {out_err or err}")
        return 2
    if out_err:  # after log.setup, so a --log outside --out records why the run stopped
        log.info("start", out_err)
        log.close()
        print(f"RESULT: CANNOT CHECK: {out_err}")
        return 2
    try:
        if not args.quiet:
            print(BYLINE, file=sys.stderr)  # stderr: stdout stays script-friendly (RESULT line)
        log.info("start", f"{BYLINE}; python {sys.version.split()[0]}, args {vars(args)}")
        try:
            code, report = run(args)
        except (ImportError, SyntaxError) as e:  # a file of the tool itself missing or damaged
            log.info("start", "can't load part of the tool:\n" + traceback.format_exc())
            print(f"RESULT: CANNOT CHECK: part of sf-load-order is missing or broken ({e}). Fix: unzip the release "
                  "again into an empty folder.")
            return 2
        if code:
            return code
        writer_failed = report.sections.get("report", {}).get("status") == "failed"
        if writer_failed and not any(role in report.outputs for role in REPORT_ROLES):
            sec = report.sections["report"]
            disk = sec.get("disk_errors") or {}
            if disk:  # a bug in one file and the disk refusing the others: name both causes
                why = "; ".join(f"{os.path.basename(p)}: {w}" for p, w in disk.items())
                print(f"RESULT: CANNOT CHECK: an internal error stopped the report writer ({sec['why']}), and "
                      f"the disk refused some files in {args.out} ({why}). Fix: make sure you can write to "
                      f"{args.out} and its drive has room, then run again with --log <file> and report the log at "
                      f"{URL}/issues.")
            else:
                print(f"RESULT: CANNOT CHECK: an internal error stopped the report writer "
                      f"({sec['why']}). Fix: run again with --log <file> and report the log at {URL}/issues.")
            return 2
        if not any(role in report.outputs for role in REPORT_ROLES):
            print(f"RESULT: CANNOT CHECK: couldn't write the report to {args.out} (none of report.txt, report.html "
                  "or report.json could be written). Fix: give --out a folder you can write to, with room on its "
                  "drive, and run with --log <file> to see why.")
            return 2
        c = report.counts()
        failed = [s for s, v in report.sections.items() if v.get("status") == "failed"]
        tail = f", {len(failed)} check(s) failed: {', '.join(failed)}" if failed else ""
        where = report.outputs.get("txt") or args.out
        print(f"RESULT: {c['problem']} problem(s), {c['caution']} caution(s), {c['note']} note(s){tail} "
              f"(report: {where})")
        return 1 if args.strict and c["problem"] else 0
    finally:
        log.close()
