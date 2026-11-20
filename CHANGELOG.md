# Changelog

## 0.1.1 - 2026-11-20

### What's new

This is a small fix release. It finds Vortex mod folders whose names start with a space, accepts more real Windows 11 folder names, and names every cause when the report can't be written. The README and AGENTS.md now also explain why MO2 needs both `plugins.txt` and `loadorder.txt`.

### Example

A real run on a small made-up Vortex setup, where one mod's staging folder is named ` Spaced Mod-2` (with a leading space, as Vortex allows):

    > .\sf-load-order.cmd --vortex-loadorder loadorder.json --vortex-staging Staging --data Game\Data --depth light --no-state
    sf-load-order 0.1.1 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 1 problem(s), 0 caution(s), 1 note(s) (report: sf-load-order-report\report.txt)

The Loose files part of `report.txt` now counts both mods:

    2 mods ship loose files; 0 pairs of mods ship the same paths.

On the same files, 0.1.0 printed `1 mod ship loose files`, because it never found the folder with the leading space.

### Fixes

- **Vortex mod folders:** the mod id from the Vortex export is used as it is to find the mod's staging folder. Before, it was trimmed, so a folder whose name starts with a space (or, on Linux, ends with one) wasn't found, and that mod's loose files weren't checked. When no folder has the exact name, the trimmed name is tried, as before (Windows drops a trailing space from folder names), and `" 7"` and `"7"` still count as one mod. A blank mod id still counts as no mod.
- **Windows 11 folder names:** `COM¹`, `COM²`, `COM³`, `LPT¹`, `LPT²` and `LPT³` (written with superscript digits) are ordinary folder names on Windows 11, so mods and profiles with those names are now read there. They are still refused on Windows 10 and older, where they are devices. The real device names (`CON`, `NUL`, `COM1` and so on) stay refused on every Windows.
- **RESULT line:** when one report file fails with an internal error and the other two can't be written to disk, the RESULT line now names both causes (the error, and each file the disk refused with the reason), and the fix says to check the `--out` folder as well as to report the log. An error while a report file is being made always counts as an internal error, even when it is a file error.
- **Wording:** "1 mod ships loose files" (it said "1 mod ship").

### Docs

- **MO2:** the README and AGENTS.md explain that MO2 reads the order from `loadorder.txt` when it starts, so a `plugins.txt` copied in alone doesn't change the order. sf-load-order already writes both files with the same order; nothing changes in how they are written.

## 0.1.0 - 2026-11-19

### What's new

This is the first public release. sf-load-order checks your Starfield load order and tells you, in plain words, what is wrong with it and how to fix it. It reads the load order you already have (the game's own Plugins.txt, a Mod Organizer 2 profile, or a file exported from Vortex's Load Order page), writes a report with the ten findings that matter most at the top, and suggests a better order when the data supports it. It never changes your game, Vortex or MO2.

### Example

A real run on a small made-up load order (three mod plugins and the official ones):

    > .\sf-load-order.cmd --plugins-txt Plugins.txt --data Data --depth light --no-state
    sf-load-order 0.1.0 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 1 problem(s), 2 caution(s), 1 note(s) (report: sf-load-order-report\report.txt)

The top of `sf-load-order-report\report.txt`:

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

- **Load order sources:** the game's Plugins.txt, a Mod Organizer 2 profile (`--mo2`, `--profile`) or a file exported from Vortex's Load Order page (`--vortex-loadorder`, `--vortex-staging`, plus Vortex's deployment record for mods with no plugin). It finds the Data folder on Steam (every library) and Game Pass (`XboxGames` and `.GamingRoot`), or takes `--data`.
- **Order and masters:** missing, turned-off, dropped and late masters, unreadable plugins, and plugin counts per kind (full, medium, light) with a caution near each limit. BlueprintShips files are placed last, as the game does.
- **Patches built for an older master:** a scan of record headers, and a snapshot (`--state`, `--no-state`) that catches a master changing kind since the last run. Records that a plugin numbers past its master list are counted as its own (as the game reads them), not as a problem.
- **Known clashes:** LOOT's masterlist and userlist from your own LOOT install (requirements, incompatibilities, warnings, cleaning), plus your own `--clashes`, `--accept` (pinned to SHA-256) and `--picks` files.
- **Archives:** the game's exact archive names, archives that never load, shared paths with advice, and content comparison (stored, zlib, texture pixel data, and LZ4 with the optional `lz4` package).
- **Loose files:** mods shipping the same file (MO2 priority; Vortex's deployed copy), loose files hiding archived ones, and stray files in Data.
- **Record conflicts:** through sf-mod-check 0.8.0 or newer (`--depth medium`, the default; `--depth heavy` adds a check of each plugin), or from a `--conflicts` file. It reports lost changes, a rating for each plugin and advice for each pair.
- **Suggested order:** scored by the changes an order throws away, with the near-tie rule (a saving under 10 points or 3% keeps your order), estimates marked as such, and a comparison with a LOOT-sorted order (`--loot-order`). It writes `plugins.txt`, `vortex-loadorder-recommended.json` (Vortex) and `HOW-TO-APPLY.txt`. For MO2, the files to apply are `plugins.txt` and `loadorder.txt`, because MO2 takes the order from `loadorder.txt`.
- **Report:** text, HTML (self-contained, light and dark) and JSON (schema 1).

### Safety and troubleshooting

- `--verbose` and `--log <file>` write a troubleshooting log. `--strict` exits 1 when the report lists a problem.
- A run that couldn't write any of the three report files says so (`RESULT: CANNOT CHECK`) and exits 2.
- The Windows launcher runs Python to check it, so the Microsoft Store's placeholder `python.exe` counts as "Python wasn't found" (exit 2) instead of failing silently.
- Plugin names that hold a line break, a NUL or another control character, a `/` or `\`, start with `*` or `#`, or start or end with a space are left out with a caution naming the file, so they can't add or change lines in the files the tool writes. Vortex mod ids and MO2 mod names that aren't a single folder name are never used to find a mod's folder.
- Every run prints a byline on stderr (left out with `--quiet`), and `--version` shows the byline and credits. The reports carry the byline, and the HTML report has a footer with the license, source and issues links.
- It runs on Linux with Python 3.10 or newer, for checking a copy of a load order (paths given with options). Folder and file lookups ignore letter case, and the snapshot folder falls back to `~/.local/state/sf-load-order`.

### Download

Download `sf-load-order-0.1.0.zip` and its `.sha256` file below, unzip it anywhere and follow the Install steps in the README. It needs Python 3.9 or newer and nothing else; sf-mod-check, LOOT and the `lz4` package are optional.
