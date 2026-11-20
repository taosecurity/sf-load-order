"""Command line and the run pipeline. Master-only file (WORKPLAN.md).

Each check lives in its own module with run(ctx) (see Context). The pipeline calls them in order; a module that is
missing or raises marks its section "failed" in the report and the run carries on, so one broken check never hides
the others.

Exit codes: 0 report written; 1 report written and --strict was given and there are problems; 2 couldn't run (bad
options, no load order found, output folder not writable).
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
    ("order", "order"),        # C2: masters, missing files, slots, dropped names, BlueprintShips last
    ("patches", "patches"),    # C2: patches built against an older version of a master
    ("clashes", "clashes"),    # C5: LOOT masterlist + the player's own --clashes / --accept
    ("archives", "archives"),  # C3: BA2 overlap and archive names
    ("loose", "loose"),        # C3: loose files (needs mod folders: MO2 or Vortex staging)
    ("records", "records"),    # C4: sf-mod-check conflict (depth medium and heavy)
    ("recommend", "recommend"),  # C6: score, moves, files to apply
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
    try:
        return importlib.import_module(f"{__package__}.{name}")
    except ModuleNotFoundError as e:
        if e.name == f"{__package__}.{name}":
            return None
        raise


def read_load_order(args):
    """Returns (LoadOrder, None) or (None, error text)."""
    inputs = _module("inputs")
    if inputs is None:
        return None, "the inputs module isn't built yet"
    return inputs.read(args)


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
        mod = _module(modname)
        if mod is None:
            report.sections[section] = {"status": "skipped", "why": "not built yet"}
            continue
        try:
            status = mod.run(ctx) or {"status": "done"}
        except Exception as e:  # one broken check must not hide the others
            log.info(section, "failed:\n" + traceback.format_exc())
            status = {"status": "failed", "why": f"{type(e).__name__}: {e}"}
        report.sections[section] = status
    writer = _module("report")
    if writer is not None:
        report.outputs.update(writer.write(ctx))
    return 0, report


def main(argv=None):
    args = parser().parse_args(argv)  # unknown option or a missing value: argparse prints why and exits 2
    err = log.setup(args.verbose, args.log)
    if err:
        print(f"RESULT: CANNOT CHECK: {err}")
        return 2
    try:
        try:
            os.makedirs(args.out, exist_ok=True)
        except OSError as e:
            print(f"RESULT: CANNOT CHECK: can't create the output folder {args.out}: {e.strerror}. "
                  "Fix: give --out a folder you can write to.")
            return 2
        if not args.quiet:
            print(BYLINE, file=sys.stderr)  # stderr: stdout stays script-friendly (RESULT line)
        log.info("start", f"{BYLINE}; python {sys.version.split()[0]}, args {vars(args)}")
        code, report = run(args)
        if code:
            return code
        c = report.counts()
        failed = [s for s, v in report.sections.items() if v.get("status") == "failed"]
        tail = f", {len(failed)} check(s) failed: {', '.join(failed)}" if failed else ""
        where = report.outputs.get("txt") or args.out
        print(f"RESULT: {c['problem']} problem(s), {c['caution']} caution(s), {c['note']} note(s){tail} "
              f"(report: {where})")
        return 1 if args.strict and c["problem"] else 0
    finally:
        log.close()
