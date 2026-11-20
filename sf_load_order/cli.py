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

from . import BYLINE, CREDITS, __version__, log
from .model import Report

# (section, module) in run order. Each module has run(ctx) -> None or a section status dict.
PIPELINE = [
    ("order", "order"),        # masters, missing files, slots, dropped names, BlueprintShips last
    ("patches", "patches"),    # patches built against an older version of a master
    ("clashes", "clashes"),    # LOOT masterlist + the player's own --clashes / --accept
    ("archives", "archives"),  # BA2 overlap and archive names
    ("loose", "loose"),        # loose files (needs mod folders: MO2 or Vortex staging)
    ("records", "records"),    # sf-mod-check conflict (depth medium and heavy)
    ("recommend", "recommend"),  # score, moves, files to apply
]
DEPTHS = ("light", "medium", "heavy")
SKIPPED_AT_LIGHT = {"records"}


class Context:
    """What every check gets: the options, the load order, the report to add findings to, a work folder."""

    def __init__(self, args, load_order, report):
        self.args = args
        self.load_order = load_order
        self.report = report
        self.work = os.path.join(args.out, "work")
        self.results = {}  # section -> whatever a check wants later checks to see (records -> conflicts data)


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
        allow_abbrev=False)
    src = p.add_argument_group("where the load order comes from (default: the game's own Plugins.txt)")
    src.add_argument("--plugins-txt", metavar="FILE", help="a Plugins.txt to check (default: %%LOCALAPPDATA%%\\Starfield\\Plugins.txt)")
    src.add_argument("--mo2", metavar="FOLDER", help="a Mod Organizer 2 instance folder (holds profiles\\ and mods\\)")
    src.add_argument("--profile", metavar="NAME", help="the MO2 profile (default: MO2's selected profile)")
    src.add_argument("--vortex-loadorder", metavar="FILE",
                     help="a load order file exported from Vortex's Load Order page (Export button)")
    src.add_argument("--vortex-staging", metavar="FOLDER",
                     help="Vortex's mod staging folder, for loose-file checks (default: %%APPDATA%%\\Vortex\\starfield\\mods)")
    src.add_argument("--data", metavar="FOLDER",
                     help="the game's Data folder (default: Steam's, then Game Pass's XboxGames folder on each drive)")
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
    chk.add_argument("--state", metavar="FOLDER",
                     help="where to keep the snapshot used for 'changed since the last run' "
                          "(default: %%LOCALAPPDATA%%\\sf-load-order)")
    chk.add_argument("--no-state", action="store_true", help="don't read or write the snapshot")
    out = p.add_argument_group("output")
    out.add_argument("--out", metavar="FOLDER", default="sf-load-order-report",
                     help="where the report and the files to apply go (default: .\\sf-load-order-report)")
    out.add_argument("--strict", action="store_true", help="exit 1 when the report lists any problem")
    out.add_argument("--quiet", action="store_true", help="print only the RESULT line")
    out.add_argument("--verbose", action="store_true", help="write the troubleshooting log to the screen (stderr)")
    out.add_argument("--log", metavar="FILE", help="write the troubleshooting log to this file")
    out.add_argument("--version", action=_Version, help="show the version, author, license and credits, then stop")
    return p


def _module(name):
    """A check's module. One that is missing is a packaging error, so the ImportError is left to stop the run."""
    return importlib.import_module(f"{__package__}.{name}")


def read_load_order(args):
    """Returns (LoadOrder, None) or (None, error text)."""
    return _module("inputs").read(args)


def run(args):
    report = Report(tool_version=__version__)
    lo, err = read_load_order(args)
    if lo is None:
        print(f"RESULT: CANNOT CHECK: {err}")
        return 2, report
    report.load_order = lo
    report.findings += _module("inputs").findings(lo)
    report.sections["inputs"] = {"status": "done"}
    log.info("inputs", f"{lo.source}: {len(lo.enabled())} enabled plugins, data {lo.data}")
    ctx = Context(args, lo, report)
    depth = args.depth or "medium"
    for section, modname in PIPELINE:
        if depth == "light" and section in SKIPPED_AT_LIGHT:
            report.sections[section] = {"status": "skipped", "why": "--depth light"}
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
    except Exception:
        log.info("report", "writing the report failed:\n" + traceback.format_exc())
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
