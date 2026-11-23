# Changelog

## 0.1.3 (unreleased)

This release is a fix pass. It removes a false caution for `--conflicts` runs that include sfta01.esm to sfta06.esm, makes the recommender use the same "acts on" rule as the ratings, and corrects a wrong line in the 0.1.2 notes. No option or output file changes.

### Example

This is a real run on a small made-up load order: ShipMod.esm plus sfta01.esm and sfta02.esm enabled, and a conflicts.json (as sf-mod-check 0.12.0 writes it) that lists the two sfta files as official and leaves them out of its mod list:

    > .\sf-load-order.cmd --plugins-txt Plugins.txt --data Data --conflicts conflicts.json --state state --out report
    sf-load-order 0.1.3 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 0 problem(s), 0 caution(s), 1 note(s) (report: report\report.txt)

On the same files, 0.1.2 gave 2 cautions: "The --conflicts file was made for another load order (2 enabled plugin(s) aren't in it)" and "The record-conflict data was made for a different load order than this one, so the scores may be off". Both were false.

### Fixes

- The recommender's move candidates now use the same "acts on" rule as the ratings and the merged-patch note (`records.acts_on`), not a second copy of it.
- A pair without an `advice` key is simply not acted on, instead of raising an error.
- With `--conflicts`, sfta01.esm to sfta06.esm (and any file sf-mod-check counts official, such as BlueprintShips-sfta03.esm) no longer give the false "made for another load order" caution. sf-mod-check leaves them out of its mod list, so they are not compared. A mod that is really missing, an extra mod or a changed order still gives the caution, and a mod named like `sfta01Fixes.esm` still counts. A plugin that conflicts.json flags Official is left out of both lists, and the name rule matches sf-mod-check 0.12.0 exactly (SFBGS names are hex only). `--verbose` logs the plugins left out of the comparison.
- A `loadOrder` entry in a `--conflicts` file with no usable file name (missing, null, empty, blank, not text, or not an object) is ignored everywhere: in the load order comparison, the load order shown and the ratings. Before, it counted as an extra plugin named "" and gave the false caution, and it also stayed in the load order list.
- A `--conflicts` file with padded plugin names (spaces around a Loser, Winner or loadOrder name) now matches them the same way everywhere, and a `Masters` value that isn't a list names no masters (before, text was read letter by letter and a number crashed the run). Blank master names are dropped.
- A `--conflicts` file with odd shapes no longer crashes the run or gives wrong counts. Any list in it (`lost`, `Fields`, `Differences`, `LoserChanges`, `itms`, `typeChanges`, `loadOrderProblems`, `Errors`, `missingAssets`, `archiveLimitWarnings`) that isn't a list holds nothing, and items that should be objects or text and aren't are skipped. Names, form keys, record types and EditorIDs that aren't text read as empty (padded ones are trimmed, so a padded ITM or type-change plugin now counts for its plugin). An ITM, type change or missing asset that names no plugin is skipped instead of being reported under a blank name. A flag counts only when it is `true`, and an `UnreadableFields` count that isn't a whole number above 0 is left out (so a count of 0 no longer gives a "couldn't compare everything" note). `assetsChecked` is kept only when it is `true` or `false`, and `archiveCounts` only when it is an object. LoserValue and WinnerValue are kept as they are.
- A plugin listed twice in `loadOrder` (any case or padding) counts once, in its first place, with the masters of both copies. It counts as official only when every copy is flagged Official, so a stray flag can't hide a mod; `--verbose` logs it once per plugin.
- The caution for a `--conflicts` file with the wrong shape now says what the file needs ("it needs a loadOrder list and a lost list"). The old wording read as if the keys were missing, even when they were there but weren't lists.

### Docs

- The 0.1.2 line about conflicts.json's official flag was wrong and is fixed in place. The flag does more than change the wording: it makes the pair a BlueprintShips pair, which also keeps it out of "order matters" and the move candidates. The ratings and the official changes a mod undoes still use the load order's own flags.

## 0.1.2 - 2026-11-22

### What's new

This release changes how record conflicts are worded in two cases, to match sf-mod-check 0.12.0, and adds a few notes to AGENTS.md. No option or output file changes. In `report.json`, `results.records.lost` no longer counts navmesh info map entries (they are in the new `results.records.navi`), and a pair can have the new advice value "blueprint". Because the navmesh info map no longer counts as a lost change, a plugin whose only overlap is the navmesh info map is now rated "neutral" instead of "neutral here".

### Example

This is a real run on a small made-up load order. An official `BlueprintShips-SFBGS050.esm` file wins over ShipMod.esm on a ship, and over OutpostMod.esm on the navmesh info map:

    > .\sf-load-order.cmd --plugins-txt Plugins.txt --data Data --conflicts conflicts.json --state state --out report
    sf-load-order 0.1.2 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 0 problem(s), 1 caution(s), 2 note(s) (report: report\report.txt)

The Top 10 in `report.txt` now says:

     1. [Caution] BlueprintShips-SFBGS050.esm wins over ShipMod.esm on 1 record(s) (fields: record flags (Flags)).
        BlueprintShips-SFBGS050.esm loads after every mod, so no load order or patch brings this change back; the edit
        ShipMod.esm makes to this ship doesn't reach the game.
        Fix: Nothing to change in the load order. If you need ShipMod.esm's version, ask its author for an update made for
        BlueprintShips-SFBGS050.esm.
     2. [Note] BlueprintShips-SFBGS050.esm and OutpostMod.esm both change the navmesh info map (1 record(s)).
        BlueprintShips-SFBGS050.esm loads after every mod, so its copy of the navmesh info map loads last. Each plugin's
        copy lists only its own navmeshes, so this isn't a record one plugin wins whole: it isn't patched, and don't copy
        it by hand either (the Creation Kit rebuilds it when a navmesh is finalized).
     3. [Note] Keep your current order (LOOT's if you just sorted): no move the data supports makes it lose less (loss
        score 3, lower is better). The files written equal the current order.

On the same files, 0.1.1 gave 2 cautions: it called the navmesh info map a "patch candidate" and the ship "pick a winner", said "a patch that loads after it can" fix both, and added a note asking for a merged patch of both records.

### Record conflicts

- **BlueprintShips winner:** when an official `BlueprintShips-*.esm` file wins a record conflict, the report says what sf-mod-check 0.12.0 says. That file loads after every mod, so no load order or patch brings the change back, and the mod's edit doesn't reach the game.
- Such a pair is no longer called a "patch candidate" or "pick a winner". Its advice is "no action: BlueprintShips file wins" in the report and in `report.json`. It isn't counted as one to act on, doesn't make a plugin "order matters", and is left out of the merged-patch note. `--verbose` logs the reason.
- A `BlueprintShips-` file counts as official when sf-mod-check's conflicts.json marks it so (in its load order or on a lost entry), even if sf-load-order's own list of official names doesn't have it. That makes the pair a BlueprintShips pair: its wording changes, and it stays out of "order matters" and the move candidates. The ratings and the official changes a mod undoes keep the load order's own official flags.
- **Navmesh info map:** the navmesh info map (NAVI) gets a note of its own under any winner, as in sf-mod-check. Each plugin's copy lists only its own navmeshes, so it isn't patched, isn't a real loss and shouldn't be copied by hand.
- The navmesh info map is no longer called a "patch candidate". It is left out of a pair's record count, the "to act on" count, the order's score, the merged-patch note and the official changes a mod undoes.
- The records summary counts navmesh info map entries apart from the lost change entries (`report.json` `results.records.navi`), and a pair's line shows them as navmesh info map records.
- A pair with only the navmesh info map no longer makes a plugin "neutral here".
- A pair with other records too keeps its advice for those records. `--verbose` logs the reason.

### Docs

- AGENTS.md and the README say that the exit code is 0 even when the report lists problems, unless `--strict` is given.
- AGENTS.md quotes the exact lines `--version` prints, and `tests/run_agents.py` checks that the quoted block matches them, in order.
- AGENTS.md and the README give a command that shows whether the optional `lz4` package is installed.
- AGENTS.md and the README say that a sandbox or fresh-folder test should use `--state "<folder>"` or `--no-state`, because the default state folder is in the user profile.
- The README's Traps list says that a mod's change to a ship can't win over a BlueprintShips file.

## 0.1.1 - 2026-11-20

### What's new

This is a small fix release. It finds Vortex mod folders whose names start with a space, accepts more real Windows 11 folder names, and names every cause when the report can't be written. The README and AGENTS.md now also explain why MO2 needs both `plugins.txt` and `loadorder.txt`.

### Example

This is a real run on a small made-up Vortex setup, where one mod's staging folder is named ` Spaced Mod-2` (with a leading space, as Vortex allows):

    > .\sf-load-order.cmd --vortex-loadorder loadorder.json --vortex-staging Staging --data Game\Data --depth light --no-state
    sf-load-order 0.1.1 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 1 problem(s), 0 caution(s), 1 note(s) (report: sf-load-order-report\report.txt)

The Loose files part of `report.txt` now counts both mods:

    2 mods ship loose files; 0 pairs of mods ship the same paths.

On the same files, 0.1.0 printed `1 mod ship loose files`, because it never found the folder with the leading space.

### Fixes

- **Vortex mod folders:** the mod id from the Vortex export is used as it is to find the mod's staging folder. Before, it was trimmed, so a folder whose name starts with a space (or, on Linux, ends with one) wasn't found, and that mod's loose files weren't checked. When no folder has the exact name, the trimmed name is tried, as before (Windows drops a trailing space from folder names). The mod's id in the report is still the trimmed id, so ids that differ only by surrounding spaces count as one mod, and a mod keeps the same id from run to run. A blank mod id still counts as no mod.
- **Windows 11 folder names:** `COM¹`, `COM²`, `COM³`, `LPT¹`, `LPT²` and `LPT³` (written with superscript digits) are ordinary folder names on Windows 11, so mods and profiles with those names are now read there. They are still refused on Windows 10 and older, where they are devices. The real device names (`CON`, `NUL`, `COM1` and so on) stay refused on every Windows.
- **RESULT line:** when one report file fails with an internal error and the other two can't be written to disk, the RESULT line now names both causes (the error, and each file the disk refused with the reason), and the fix says to check the `--out` folder as well as to report the log. An error while a report file is being made always counts as an internal error, even when it is a file error.
- **Wording:** the Loose files part now says "1 mod ships loose files" (it said "1 mod ship").

### Docs

- **MO2:** the README and AGENTS.md explain that MO2 reads the order from `loadorder.txt` when it starts, so a `plugins.txt` copied in alone doesn't change the order. sf-load-order already writes both files with the same order; nothing changes in how they are written.

## 0.1.0 - 2026-11-19

### What's new

This is the first public release. sf-load-order checks your Starfield load order and tells you, in plain words, what is wrong with it and how to fix it. It reads the load order you already have (the game's own Plugins.txt, a Mod Organizer 2 profile, or a file exported from Vortex's Load Order page), writes a report with the ten findings that matter most at the top, and suggests a better order when the data supports it. It never changes your game, Vortex or MO2.

### Example

This is a real run on a small made-up load order (three mod plugins and the official ones):

    > .\sf-load-order.cmd --plugins-txt Plugins.txt --data Data --depth light --no-state
    sf-load-order 0.1.0 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 1 problem(s), 2 caution(s), 1 note(s) (report: sf-load-order-report\report.txt)

This is the top of `sf-load-order-report\report.txt`:

    Top 10
    ------
     1. [Problem] NewModA.esm needs NewModC.esm, which isn't installed or isn't enabled.
        Fix: Install and enable NewModC.esm, or disable NewModA.esm.
        (Section: Load order and masters.)
     2. [Caution] NewModB - Meshes.ba2 starts with NewModB.esm's name but the game won't load it: it loads only NewModB -
        Main.ba2, NewModB - Main01.ba2 and on, NewModB - Textures.ba2 and on, and NewModB - Voices_en.ba2.
        Fix: Ask the mod's author: the archive needs one of those names, or its files never reach the game.
        (Section: Archives (.ba2 files).)
     3. [Caution] MyMod.esm and NewModA.esm both ship 1 file(s) in their archives (1 textures; 1 can't be compared).
        NewModA.esm loads later, so its copies are used and MyMod.esm's never show up in the game.
    ...

### What it checks

- **Load order sources:** it reads the game's Plugins.txt, a Mod Organizer 2 profile (`--mo2`, `--profile`) or a file exported from Vortex's Load Order page (`--vortex-loadorder`, `--vortex-staging`, plus Vortex's deployment record for mods with no plugin). It finds the Data folder on Steam (every library) and Game Pass (`XboxGames` and `.GamingRoot`), or takes `--data`.
- **Order and masters:** it reports missing, turned-off, dropped and late masters, unreadable plugins, and plugin counts per kind (full, medium, light) with a caution near each limit. BlueprintShips files are placed last, as the game does.
- **Patches built for an older master:** it uses a scan of record headers, and a snapshot (`--state`, `--no-state`) that catches a master changing kind since the last run. Records that a plugin numbers past its master list are counted as its own (as the game reads them), not as a problem.
- **Known clashes:** it reads LOOT's masterlist and userlist from your own LOOT install (requirements, incompatibilities, warnings, cleaning), plus your own `--clashes`, `--accept` (pinned to SHA-256) and `--picks` files.
- **Archives:** it checks the game's exact archive names, archives that never load, shared paths with advice, and content comparison (stored, zlib, texture pixel data, and LZ4 with the optional `lz4` package).
- **Loose files:** it finds mods shipping the same file (MO2 priority; Vortex's deployed copy), loose files hiding archived ones, and stray files in Data.
- **Record conflicts:** it checks them through sf-mod-check 0.8.0 or newer (`--depth medium`, the default; `--depth heavy` adds a check of each plugin), or reads them from a `--conflicts` file. It reports lost changes, a rating for each plugin and advice for each pair.
- **Suggested order:** it is scored by the changes an order throws away, with the near-tie rule (a saving under 10 points or 3% keeps your order), estimates marked as such, and a comparison with a LOOT-sorted order (`--loot-order`). It writes `plugins.txt`, `vortex-loadorder-recommended.json` (Vortex) and `HOW-TO-APPLY.txt`. For MO2, the files to apply are `plugins.txt` and `loadorder.txt`, because MO2 takes the order from `loadorder.txt`.
- **Report:** it comes as text, HTML (self-contained, light and dark) and JSON (schema 1).

### Safety and troubleshooting

- `--verbose` and `--log <file>` write a troubleshooting log. `--strict` exits 1 when the report lists a problem.
- A run that couldn't write any of the three report files says so (`RESULT: CANNOT CHECK`) and exits 2.
- The Windows launcher runs Python to check it, so the Microsoft Store's placeholder `python.exe` counts as "Python wasn't found" (exit 2) instead of failing silently.
- Plugin names that hold a line break, a NUL or another control character, a `/` or `\`, start with `*` or `#`, or start or end with a space are left out with a caution naming the file, so they can't add or change lines in the files the tool writes. Vortex mod ids and MO2 mod names that aren't a single folder name are never used to find a mod's folder.
- Every run prints a byline on stderr (left out with `--quiet`), and `--version` shows the byline and credits. The reports carry the byline, and the HTML report has a footer with the license, source and issues links.
- It runs on Linux with Python 3.10 or newer, for checking a copy of a load order (paths given with options). Folder and file lookups ignore letter case, and the snapshot folder falls back to `~/.local/state/sf-load-order`.

### Download

Download `sf-load-order-0.1.0.zip` and its `.sha256` file below, unzip it anywhere and follow the Install steps in the README. It needs Python 3.9 or newer and nothing else; sf-mod-check, LOOT and the `lz4` package are optional.
