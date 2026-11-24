# Changelog

## 0.2.3 (not released yet)

### What's new

`candidate` now checks the records a new build shares with the installed mods, not only its files. No option or report.json key was renamed or removed; new keys were added.

- At `--depth medium` (the default) and `heavy`, `candidate` runs `sf-mod-check conflict` with the installed copy of the build left out and the build's plugins in its place, so installed patches built on it still load after it, or loading last, where a new mod lands, when no copy is installed (each part after the build's plugins it masters). A build plugin that sf-mod-check couldn't load, such as one whose master isn't installed, gets a caution naming that master instead of a "no shared records" note. The build's plugins are read from a copy in `<out>\work\candidate\extra` (`--extra-data`), and the run checks records only (`--no-assets`), since the file comparison already covers the build's files.
- For each installed mod whose records the build also changes, one finding says how many records, and how many of them have fields a conflict patch can forward (Forwardable) or fields both mods changed (Choice), and which side wins them (the build, or an installed mod that still loads after it, such as a plain .esp after the build's .esm). That is a caution; a mod whose shared records differ in nothing that matters (No effect) gets a note. Up to five records are named as examples. Bethesda's records that the build overrides, and the navmesh info map, aren't reported.
- When the check can't run, one caution says why and names the fix: sf-mod-check not found or older than 0.8.0, no Data folder, a Vortex profile that isn't deployed, or an MO2 source run from outside MO2 (sf-mod-check then can't see MO2's mods). A run that fails is one caution with sf-mod-check's RESULT line. At `--depth light` a note says the record check didn't run.
- `candidate` takes `--max-memory <GB>`, passed to `sf-mod-check conflict`.
- report.json: `candidate.records` (null at `--depth light`) with `status` (`done`, `skipped` or `failed`), `why`, `program`, `exit`, `result_line`, `file`, `shared_records` and `mods` (each with `mod`, `plugin`, `records`, `forwardable`, `choice`, `no_effect`, `build_wins`, `build_loses` and `examples`) and `not_loaded` (the build plugins sf-mod-check didn't load). The findings have kind `shared-records`.
- `candidate` reads `.7z` and `.rar` builds through 7-Zip's `7z.exe` (found on PATH or in Program Files\7-Zip). Python stays standard library only; without 7-Zip the run stops with exit 2 and names the fix. A member path that is absolute or holds `..` stops the run. report.json: each `candidate.zips[]` entry has `format` (`zip`, `7z` or `rar`).
- A load order whose `StarfieldCustom.ini` has `sTestFile` lines gets one caution: the game (and MO2) load those files and skip Plugins.txt, so the reported order may not be what the game loads. For an MO2 profile that uses local INIs, the profile's own copy is read.
- HOW-TO-APPLY.txt for a plain Plugins.txt now has a part for players who use only the game's Creations menu, and the README explains it.
- For an MO2 source with official plugins MO2 2.5.2 doesn't pin (Shattered Space, Terran Armada and so on), one caution and one HOW-TO-APPLY step say to check they are still ticked in MO2's Plugins tab.
- When every merged-patch manifest found is from the old patch_sources.py, the `patches` list in report.json has one `cannot check` entry per manifest instead of being empty.
- A failed `--clashes` file now reads "couldn't be used" in the report, not "was failed".
- README: the line about who writes Plugins.txt is exact for MO2, and a Traps entry says MO2's view also holds the real `Documents\My Games\Starfield\Data` folder, which this tool doesn't read.

### Behavior change

- At `--depth medium` a `candidate` run now takes as long as one `sf-mod-check conflict` run (about 2 minutes on a 150-plugin load order), not seconds. `--depth light` keeps the old speed and compares file names only.
- A `candidate` run without sf-mod-check, or under MO2 started from outside MO2, now has one more caution, saying the record check didn't run.

### Example

The `candidate` Example from the README, rebuilt from its made-up files. Its second caution says the record check didn't run, because sf-mod-check can't see MO2's mods unless sf-load-order is started from MO2:

    > .\sf-load-order.cmd candidate ShipMod-0.3.0.zip --mo2 mo2 --data game\Data --no-state --out out
    sf-load-order 0.2.3 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 0 problem(s), 2 caution(s), 1 note(s) (report: out\report.txt)

### Docs

- `tests\test_doc_examples.py` checks the `candidate` Example in the README and in the newest CHANGELOG entry only; older entries show what their release printed.

## 0.2.2 (2026-11-23)

### What's new

This release makes the check of a Vortex profile that isn't deployed, and `candidate` over an older copy of the build, simpler and clearer, and gives a note for old patch manifests. No option or report.json key was renamed or removed.

### Behavior change

- When `--vortex-profile` names a profile that Data doesn't hold (Vortex has another profile deployed), every check that reads the profile's plugin, archive or loose files is skipped: load order and masters, patches, archives, loose files, Vortex mod rules, record conflicts and the suggested order. Before, some of these read the profile's staging copies and others gave weaker findings; now only the dropped-name and known-clash checks run.
- One caution says so and names the fix: deploy the profile in Vortex and run again, or pass `--data` with a folder that holds this profile's files.
- In report.json the skipped sections are `{"status": "skipped", "why": ...}`, so their `results` entries and `outputs.vortex_json` are absent, and the top-level `rules` and `recommended` are `null`. The mod plugins' `kind` and `masters` are `null`, since their headers aren't read. `patches` stays `{"not_checked": why}`.
- `candidate` with a plain Plugins.txt and the build's older copy installed no longer compares Data's loose files, which can't be told apart from that copy's. One caution (kind `data-unsorted`, with `replaced`) says so and names the fix: use `--mo2` or `--vortex-profile`, or remove the older copy. Before, each such file was a caution of kind `data-old`; that kind is gone. Archives are still compared plugin by plugin.

### Example

A made-up profile `Example-Profile` while Data holds another one (here a Data folder with `ModA.esm` and `ModB.esp`, and an export of `Example-Profile` with `ModA.esm` and `ModC.esm`):

    > python sf-load-order.py --vortex-profile Example-Profile --vortex-exports exports --data game\Data --vortex-staging staging --no-state --out out
    sf-load-order 0.2.2 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 0 problem(s), 1 caution(s), 0 note(s) (report: out\report.txt)

In `out\report.json`, the `order` section reads:

    "order": {"status": "skipped", "why": "Data holds another profile, not Example-Profile (deploy Example-Profile in Vortex and run again)"}

The caution's fix reads: "Deploy the profile Example-Profile in Vortex and run again, or pass --data with a folder that holds this profile's files."

### Fixes

- A manifest from the old `patch_sources.py` passed with `--patch-sources` was skipped with no finding. Now one note in the Patches section names each such file, with the fix "rebuild the patch with sf-mod-check 0.15.0 or later", and report.json's finding has `data.old_manifests`. An old file named directly, or an old `patch-sources.json` found by itself beside a patch, gets this note in place of the caution "it isn't a patch-sources.json".
- For a Vortex profile that isn't deployed, the note "N mod(s) Vortex deployed aren't in the load order export ..., so they were added from Vortex's deployment record" named the deployed profile's mods as if they were this profile's. That note is no longer given for such a profile; the caution already counts those mods as ones the profile doesn't enable.
- A `--patch-sources` file that doesn't exist was counted as a manifest found in the note that sf-mod-check wasn't found. Now it gets its own caution, "the file wasn't found", whether or not sf-mod-check is installed, and it is no longer listed in report.json's `patches`.
- The exit-code tables in the README and AGENTS.md now say that `--help` and `--version` exit 0.

## 0.2.1 (2026-11-23)

This release fixes the docs. It changes no behavior, option or report.json key.

### Example

The `candidate` Example below is the one from 0.2.0 with its RESULT line corrected: it ends in "1 note(s)", because the note that names the older copy left out is counted. The same run, rebuilt from the made-up files:

    > .\sf-load-order.cmd candidate ShipMod-0.3.0.zip --mo2 mo2 --data game\Data --no-state --out out
    sf-load-order 0.2.1 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 0 problem(s), 1 caution(s), 1 note(s) (report: out\report.txt)

### Fixes

- The `candidate` Example's RESULT line said "0 note(s)" in the README and the CHANGELOG; the release prints "1 note(s)".
- The README's `.pex` row for "Checking a new mod build" pointed at "the row below", which is no longer the row it meant. It now names the row.
- The README's Install section named 0.1.3; it now names 0.2.1.

### Docs

- A test, `tests\test_doc_examples.py`, runs the `candidate` Example and fails when the RESULT line in the README or the CHANGELOG differs from what the run prints.

## 0.2.0 (2026-11-23)

This release adds the checks that used to need a private page builder: Vortex profiles by name with a check that the profile is the deployed one, a Vortex mod rules check, merged patches checked with `sf-mod-check patch check`, `settled_if_current` in the clashes file, per-kind counts of missing files, and `sf-load-order candidate <zip>` for a new mod build. No option or report.json key was renamed or removed; new keys were added. A normal report now has nine sections (the new one is "Vortex mod rules", after "Loose files").

### Example

This is a real run on a small made-up load order: ShipMod.esm 0.3.0 is a new build, built on the installed CoreTools, and ships a script and a mesh that CoreTools ships too, with other content. The older ShipMod is installed as a mod of its own and is left out of the comparison. (Corrected in 0.2.1: this RESULT line first said "0 note(s)".)

    > .\sf-load-order.cmd candidate ShipMod-0.3.0.zip --mo2 mo2 --data game\Data --no-state --out out
    sf-load-order 0.2.0 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 0 problem(s), 1 caution(s), 1 note(s) (report: out\report.txt)

The note names the older ShipMod that was left out: "1 installed mod(s) or plugin(s) were left out as this build's own older copy ...: OldShipMod." The caution reads: "This build overrides 2 file(s) of CoreTools, which it is built on, with different content (loose): meshes\crate.nif, scripts\shared.pex." It is a caution, not a problem, because overriding the mod it is built on is usual. A script or a file shared with a mod the build isn't built on would be a problem.

### What's new

- `--vortex-profile NAME` reads the newest export set of a Vortex profile (modlist and load order) from the Modlist Backup extension's folder, with `--vortex-exports`, `--vortex-stamp` and `--vortex-modlist`. It checks that Data holds that profile; if not, one caution replaces the per-plugin "file wasn't found" problems, and the record and merged-patch checks are skipped. The other checks still describe the profile: its mods come from its modlist and its plugins from their staging folders. New report.json key `vortex_profile`.
- A "Vortex mod rules" section (`rules` in report.json): for each pair of mods that ship different copies of a loose file, who won; with the rules in the export (Modlist Backup 0.6.2 or later), a caution for a real clash with no rule, and notes for rules that order nothing.
- Merged patches: the Patches section runs `sf-mod-check patch check` (sf-mod-check 0.15.0 or newer, depth medium or heavy) on each `patch-sources.json` found in Data, beside the enabled plugins or in Vortex staging, or named with the new `--patch-sources`. New report.json key `patches`.
- `--clashes` entries may carry `settled_if_current` (a plugin and its SHA-256): the clash is settled while that build is installed, unless the entry's `fixed_by` patch loads too early.
- The "files its records use aren't installed" findings count scripts, models, materials, textures and sounds separately, from the `kind` in sf-mod-check's `missingFiles`.
- `sf-load-order candidate <zip> [<zip> ...]` checks a new mod build against the enabled mods before it is installed: shared scripts and files with different content, the build's own parts (one archive name in two zips is a problem), and at `--depth heavy` `sf-mod-check check` on the build's plugins (a plugin whose master is another part of the build is skipped with a caution). The installed older copy of the build (the same plugin name, or the zip's name with another version) is left out, with a note naming it, and a zip packed inside one folder of its own (a readme beside it is fine) is read as if that folder were Data. A file that is loose on one side and archived on the other is reported with the loose copy as the one that always wins. Files in a fomod installer's option folders aren't compared, and a caution says how many. With a plain Plugins.txt and the build's older copy installed, Data's loose files the build also ships are a caution, since they may be that copy's. New report.json key `candidate`. Records shared with other mods aren't checked yet.

### Docs

- The README, AGENTS.md and `--help` list the new options, sections and report.json keys. `tests\run_agents.py` runs the new commands from AGENTS.md.

## 0.1.3 - 2026-11-22

This release is a fix pass. It removes a false caution for `--conflicts` runs that include sfta01.esm to sfta06.esm, makes the recommender use the same "acts on" rule as the ratings, and corrects a wrong line in the 0.1.2 notes. No option or output file was added or removed, but some report text and counts changed, as the Fixes list below says.

### Example

This is a real run on a small made-up load order: ShipMod.esm plus sfta01.esm and sfta02.esm enabled, and a conflicts.json (as sf-mod-check 0.12.0 writes it) that lists the two sfta files as official and leaves them out of its mod list:

    > .\sf-load-order.cmd --plugins-txt Plugins.txt --data Data --conflicts conflicts.json --state state --out report
    sf-load-order 0.1.3 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
    RESULT: 0 problem(s), 0 caution(s), 1 note(s) (report: report\report.txt)

On the same files, 0.1.2 gave 2 cautions: "The --conflicts file was made for another load order (2 enabled plugin(s) aren't in it)" and "The record-conflict data was made for a different load order than this one, so the scores may be off". Both were false.

### Fixes

- The recommender's move candidates now use the same "acts on" rule as the ratings and the merged-patch note (`records.acts_on`), not a second copy of it.
- A pair without an `advice` key is simply not acted on, instead of raising an error.
- With `--conflicts`, sfta01.esm to sfta06.esm (and any file sf-mod-check counts official, such as BlueprintShips-sfta03.esm) no longer give the false "made for another load order" caution. sf-mod-check leaves them out of its mod list, so they are not compared. A mod that is really missing, an extra mod or a changed order still gives the caution, and a mod named like `sfta01Fixes.esm` still counts. A plugin that conflicts.json flags Official is left out of both lists, and the name rule matches sf-mod-check 0.12.0 exactly (SFBGS names are hex only). A file this tool counts official but sf-mod-check lists as a mod (such as BlueprintShips-ShatteredSpace.esm) is not called "extra" either. `--verbose` logs the plugins left out of the comparison.
- A record type change now counts in the rating of both plugins it names (its `Plugin` and its `First`), not only the first one, so a plugin a type-change finding names is no longer rated "neutral" in the same report. The rating says "named in N record type change(s)", because these counts don't add up to the summary's total.
- A `--conflicts` file that isn't in the shape sf-mod-check writes is now refused with one caution that names the file and the fix (rerun sf-mod-check conflict), instead of being half read: for example a list that isn't a list, a `lost` item without Loser and Winner names, a `loadOrder` item without a FileName, `Masters` that aren't a list of names, or a form key, record type, field name or type change's `First` that isn't text. The caution also says the file may come from a newer sf-mod-check than this sf-load-order knows. The section is then skipped, as for a file that isn't JSON. Files that sf-mod-check writes are read as before.
- The caution for a `--conflicts` file without a loadOrder list and a lost list now says what the file needs ("it needs a loadOrder list and a lost list"). The old wording read as if the keys were missing, even when they were there but weren't lists.

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
