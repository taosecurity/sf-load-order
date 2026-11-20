"""The shapes every part of sf-load-order shares. Master-only file (WORKPLAN.md): chunks read these, never change them.

LoadOrder comes from inputs (C1). Findings come from each check. A Report holds both and is what report.json writes.
"""
import dataclasses
from dataclasses import dataclass, field

SCHEMA_VERSION = 1

# Finding levels, worst first. "problem" = fix before playing, "caution" = look at it, "note" = good to know.
LEVELS = ("problem", "caution", "note")

# Sections, in the order the report shows them.
SECTIONS = ("inputs", "order", "patches", "clashes", "archives", "loose", "records", "recommend")


@dataclass
class Plugin:
    name: str                 # file name, with the case of the file on disk when found
    enabled: bool             # loads in the game
    official: bool = False    # an official master the game loads by itself
    listed: bool = True       # the source names it (False for official masters added from Data)
    path: str = None          # the file found on disk (Data, or the winning MO2 mod folder), None if missing
    dropped: bool = False     # the game removes this name from Plugins.txt, so it never loads
    kind: str = None          # "F" full, "M" medium, "L" light, "?" unreadable, None not read yet
    masters: list = None      # master file names from the header, None until read


@dataclass
class Mod:
    name: str
    enabled: bool
    path: str = None          # Vortex staging or MO2 mod folder, None if unknown
    plugins: list = None      # plugin file names, None if unknown
    priority: int = None      # MO2: higher wins loose-file conflicts. Vortex: None (its rules decide)
    id: str = None            # Vortex mod id
    files: list = None        # relative paths the mod owns (filled only when loose files are checked)


@dataclass
class LoadOrder:
    source: str                                  # "plugins.txt", "vortex" or "mo2"
    profile: str = None
    files: dict = field(default_factory=dict)    # {role: path} of every input file read
    data: str = None                             # the game's Data folder, None if none was found
    plugins: list = field(default_factory=list)  # [Plugin] in load order, enabled and disabled
    mods: list = None                            # [Mod] or None when the source has no mod manager
    mods_note: str = None                        # why mods is None

    def enabled(self):
        """Names of the plugins that load, in load order (official masters included)."""
        return [p.name for p in self.plugins if p.enabled and not p.dropped]


@dataclass
class Finding:
    level: str                # one of LEVELS
    section: str              # one of SECTIONS
    text: str                 # one plain sentence (or two): what is wrong
    fix: str = None           # what to do about it, plainly; None for notes with nothing to do
    plugins: list = field(default_factory=list)  # plugin file names it is about
    data: dict = field(default_factory=dict)     # extra machine-readable detail (counts, FormIDs, paths)

    def __post_init__(self):
        if self.level not in LEVELS:
            raise ValueError(f"bad finding level {self.level!r}")
        if self.section not in SECTIONS:
            raise ValueError(f"bad finding section {self.section!r}")


@dataclass
class Report:
    tool_version: str
    load_order: LoadOrder = None
    findings: list = field(default_factory=list)
    sections: dict = field(default_factory=dict)  # section -> {"status": "done"|"skipped"|"failed", "why": str, ...}
    recommended: list = None                      # recommended order (plugin names), None when not worked out
    outputs: dict = field(default_factory=dict)   # {role: path} of every file written

    def add(self, finding):
        self.findings.append(finding)

    def sorted_findings(self):
        return sorted(self.findings, key=lambda f: (LEVELS.index(f.level), SECTIONS.index(f.section)))

    def counts(self):
        return {lv: sum(1 for f in self.findings if f.level == lv) for lv in LEVELS}

    def to_dict(self):
        d = dataclasses.asdict(self)
        d["schema"] = SCHEMA_VERSION
        from . import AUTHOR, LICENSE, URL  # here, not at the top: the package imports nothing from model
        d.update(author=AUTHOR, license=LICENSE, url=URL)
        d["findings"] = [dataclasses.asdict(f) for f in self.sorted_findings()]
        d["counts"] = self.counts()
        return d
