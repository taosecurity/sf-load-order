# AGENTS.md: sf-load-order for AI assistants

Plain steps for an AI assistant helping someone check a Starfield load order with sf-load-order. The README has the
background and the file formats; this file has what to run. Every command below is run by `tests\run_agents.py`
before each release.

## 1. What it is for

1. sf-load-order reads a Starfield load order (the game's Plugins.txt, a Mod Organizer 2 profile, or a file exported
   from Vortex's Load Order page) and writes a report: missing or late masters, plugin limits, patches built for an
   older version of a master, known clashes (LOOT's masterlist and the person's own files), archive (.ba2) and
   loose-file overlap, Vortex mod rules, merged patches that are out of date, and record conflicts through
   sf-mod-check. `sf-load-order candidate <zip>` checks a new mod build before it is installed.
2. It rates the order, suggests a better one when the data supports it, and writes files to apply it, with steps.
3. Use it when someone asks whether their load order is right, why a mod's changes don't show up in game, whether a
   patch still fits after an update, or which mod's textures or files win.
4. `sf-load-order sort` builds a whole, valid load order from the enabled plugins without LOOT: game facts (masters,
   master plugins first), the person's picks and rules file, an optional community rules file and, when found, LOOT's
   masterlist. It keeps the current order wherever it can, explains every move, and never applies anything.
   `sf-load-order rules` lists, explains, checks, accepts and proposes the ordering rules.
5. Don't use it to edit records or build patches (use xEdit), or for games other than Starfield.

## 2. Prerequisites (exact versions)

1. Python 3.9 or newer on Windows, 3.10 or newer on Linux (`python --version`). Standard library only. On Windows,
   the python.org installer with "Add python.exe to PATH" ticked.
2. Optional: the `lz4` package (any current version, for example 4.4.5), to compare the contents of LZ4-packed
   archives. Without it those contents are reported as "can't compare".
3. Optional: sf-mod-check 0.8.0 or newer (https://github.com/taosecurity/sf-mod-check/releases), for record
   conflicts. Older versions (from when its old name was sfmodcheck) are refused with a note. Without it, the record section is skipped
   and everything else runs (the same as `--depth light`).
4. Optional: LOOT, run once for Starfield, so its masterlist is on the PC
   (`%LOCALAPPDATA%\LOOT\games\Starfield\masterlist.yaml`).

## 3. Install

1. Download `sf-load-order-<version>.zip` from https://github.com/taosecurity/sf-load-order/releases.
2. Unzip it anywhere. Work in the unzipped folder `sf-load-order-<version>` (the one holding `sf-load-order.cmd`).
3. Optional extras:

   ```
   python -m pip install lz4
   ```

   For sf-mod-check, unzip its release and put its folder on PATH, or pass `--sf-mod-check` (see 5).

## 4. Prove the install works

Run in the unzipped folder:

```
.\sf-load-order.cmd --version
```

Expect exit code 0 and these four lines (the byline, then three credit lines):

```
sf-load-order 0.3.0 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
Reads LOOT's Starfield masterlist (CC0 1.0) from your own LOOT install when it's there.
Record conflicts come from sf-mod-check (https://github.com/taosecurity/sf-mod-check), when installed.
This is free software: you may share and change it under the GPL-3.0. It comes with no warranty.
```

If it says Python wasn't found, install Python (step 2.1) and open a new window. On Linux, or without the launcher
(on Linux the command may be `python3`):

```
python -I sf-load-order.py --version
```

To see whether the optional `lz4` package is installed (step 2.2), run:

```
python -I -c "import lz4; print(lz4.__version__)"
```

With it, this prints its version (for example `4.4.5`) and exits 0. Without it, it ends with
`ModuleNotFoundError: No module named 'lz4'` and exits 1; that is fine unless the person wants LZ4-packed archive
contents compared (then install it as in step 3.3).

## 5. Common tasks

Run these in the unzipped folder. `.\sf-load-order.cmd` works in both Command Prompt and PowerShell. Replace each
`<...>` with the person's real path, in double quotes. Every run starts with the byline on stderr (left out with
`--quiet`) and ends with one `RESULT:` line on stdout naming the report, for example:

```
RESULT: 1 problem(s), 2 caution(s), 1 note(s) (report: sf-load-order-report\report.txt)
```

The exit code is 0 whenever the report was written, even when it lists problems. Only with `--strict` is it 1 when
the report lists at least one problem (see 8).

In a sandbox, a test folder or any run that shouldn't touch the person's profile, add `--state "<folder>"` (or
`--no-state`). Without it, every run reads and writes the snapshot and cache in the default state folder, which is
in the user profile (`%LOCALAPPDATA%\sf-load-order`, on Linux `~/.local/state/sf-load-order`), so an earlier run
there changes what "changed since the last run" reports.

- Check the order the game reads (its own Plugins.txt, Data folder found on Steam or Game Pass):
  `.\sf-load-order.cmd`
- A quick check without record conflicts: `.\sf-load-order.cmd --depth light`
- The game is somewhere the tool doesn't find, or there are several copies: `.\sf-load-order.cmd --data "<Data folder>"`
- Check another Plugins.txt: `.\sf-load-order.cmd --plugins-txt "<Plugins.txt>" --data "<Data folder>"`
- Mod Organizer 2, its selected profile: `.\sf-load-order.cmd --mo2 "<MO2 instance folder>"`
- Mod Organizer 2, a named profile: `.\sf-load-order.cmd --mo2 "<MO2 instance folder>" --profile "<profile name>"`
- Vortex: first have the person open Vortex's Load Order page, click Export and save the file. Then:
  `.\sf-load-order.cmd --vortex-loadorder "<exported file>"`
- Vortex with a staging folder that isn't the default (Vortex: Settings, Mods, Mod Staging Folder):
  `.\sf-load-order.cmd --vortex-loadorder "<exported file>" --vortex-staging "<staging folder>"`
- Vortex with the Modlist Backup extension: name the profile and the tool reads its newest export set (modlist and
  load order) and checks that Data holds this profile. `--vortex-exports` names the folder the exports are in when it
  isn't the extension's own folder (shown in `%APPDATA%\Vortex\modlist-backup-auto-export.json`); `--vortex-stamp
  YYYYMMDD-HHMMSS` picks an older set:
  `.\sf-load-order.cmd --vortex-profile "Example-Profile" --vortex-exports "<exports folder>" --vortex-staging "<staging folder>"`
  If the profile isn't the deployed one, the report has one caution saying so, and every check that reads the
  profile's files is skipped (order, patches, archives, loose files, rules, records and the suggested order); only the
  dropped-name and known-clash checks run. The fix is to deploy the profile in Vortex, or to pass `--data` with a
  folder that holds its files. In report.json, `vortex_profile` holds the check, the skipped sections say why, and
  the mod plugins' `kind` and `masters` are `null`.
- Merged patches built by `sf-mod-check patch`: a `patch-sources.json` beside the patch is found by itself; one
  somewhere else is given with `--patch-sources`. This needs sf-mod-check 0.15.0 or newer (the section says why when it
  can't run). An old `patch_sources.py` manifest isn't checked: a note names it and says to rebuild the patch with
  sf-mod-check 0.15.0 or later:
  `.\sf-load-order.cmd --sf-mod-check "<path to sf-mod-check.exe>" --patch-sources "<patch-sources.json>"`
- Check a new mod's zip before it is installed (the zips are the parts of one build, one plugin each; the load order
  options are the same as above, and nothing is installed or unpacked into the game):
  `.\sf-load-order.cmd candidate "<build zip>" --mo2 "<MO2 instance folder>"`
  The report has the inputs and one section, "The new build against the installed mods". A shared script (`.pex`)
  or a file with different content is a problem; a build part clashing with another part of the same build is a
  problem for scripts and a caution for other files; one archive name in two zips is a problem. At the default
  depth it also runs `sf-mod-check conflict` with the build's plugins where its installed copy sat (else last) and reports, per installed mod, the
  records the build also changes (kind `shared-records`: a caution when any is Forwardable or Choice, else a note;
  report.json `candidate.records`). Without sf-mod-check, or under MO2 run from outside MO2, one caution says the
  record check was skipped. `--depth heavy` adds
  `sf-mod-check check` on the build's plugins (a plugin whose master is another part of the build is skipped with a
  caution). A note names the installed mods left out as the build's older copy. With a plain Plugins.txt and that
  older copy installed, Data's loose files aren't compared, and one caution (kind `data-unsorted`) says so.
  A `.7z` or `.rar` is read through 7-Zip's `7z.exe` (on PATH or in Program Files\7-Zip), and each `candidate.zips[]` entry has `format` (`zip`, `7z` or `rar`). Exit 2 for a missing file, a `.7z` or `.rar` with no 7-Zip, an unsafe member path, or a file that isn't an archive. In report.json the key
  `candidate` has the counts; the findings are in `findings` with section `candidate`.
- The options `candidate` takes: `.\sf-load-order.cmd candidate --help`
- Record conflicts with an sf-mod-check that isn't on PATH:
  `.\sf-load-order.cmd --sf-mod-check "<path to sf-mod-check.exe>"`
- The same, with a memory limit for sf-mod-check (in GB):
  `.\sf-load-order.cmd --sf-mod-check "<path to sf-mod-check.exe>" --max-memory 8`
- Everything, including ITM and deleted-reference checks per plugin (slow):
  `.\sf-load-order.cmd --depth heavy --sf-mod-check "<path to sf-mod-check.exe>"`
- Reuse a conflicts.json from an earlier `sf-mod-check conflict` run (needed for MO2 when the tool isn't started from
  MO2): `.\sf-load-order.cmd --conflicts "<conflicts.json>"`
- LOOT's files in another place:
  `.\sf-load-order.cmd --loot-masterlist "<masterlist.yaml>" --loot-userlist "<userlist.yaml>"`
- The person's own clashes, acceptances and picks (formats in the README; samples in `examples\`):
  `.\sf-load-order.cmd --clashes examples\clashes.example.json --accept examples\accept.example.json --picks examples\picks.example.json`
- Compare with an order LOOT sorted: `.\sf-load-order.cmd --loot-order "<LOOT-sorted Plugins.txt>"`
- Keep the snapshot somewhere else: `.\sf-load-order.cmd --state "<folder>"`
- Don't read or write the snapshot and cache: `.\sf-load-order.cmd --no-state`
- Put the report somewhere else: `.\sf-load-order.cmd --out "<report folder>"`
- For scripts: exit 1 when there is any problem, print only the RESULT line: `.\sf-load-order.cmd --strict --quiet`
- Troubleshoot: `.\sf-load-order.cmd --verbose --log sf-load-order-log.txt`
- All options: `.\sf-load-order.cmd --help`
- Linux (a copy of a load order; give every path, since the Windows defaults don't apply there):
  `python -I sf-load-order.py --plugins-txt "<Plugins.txt>" --data "<Data folder>"`

- Sort a load order (`sort` runs every check of a normal run, then builds the order; it reads the same load order
  options and writes plugins.txt, `loadorder.txt` for MO2, the Vortex import file, HOW-TO-APPLY.txt,
  `rules-proposed.json` and the report; nothing is applied):
  `.\sf-load-order.cmd sort`
  The RESULT line and `report.txt` name the number of plugins moved; the "Sorted order" part lists each move with its
  reason, the rules that weren't used and why, and the requires and incompatible checks. In report.json the key
  `sort` has `moves`, `rejected`, `proposed` and `files`. A master loop (plugins that each need the other) is a
  problem and no order files are written. Use `--tie-break name` for an order by file name that is the same on any PC
  (it moves many plugins), `--no-loot` to leave LOOT out, `--loot-groups` to also use LOOT's groups, and
  `--depth light` for a quick run.
- Sort with the person's own rules file (format in the README, a sample in `examples\`; a missing file given here is
  exit 2): `.\sf-load-order.cmd sort --rules "<rules file>"`
- Sort with a community rules file (a local file; nothing is downloaded):
  `.\sf-load-order.cmd sort --community-rules "<community rules file>"`
- Say why a plugin is where it is in the sorted order (writes nothing but the log):
  `.\sf-load-order.cmd rules explain ExampleAddon.esm --data "<Data folder>"`
- List every rule and whether it is active in this load order, with the reason when `sort` leaves an active rule
  out (`--derived` adds the game's own rules):
  `.\sf-load-order.cmd rules list --derived --data "<Data folder>"`
- Check a rules file, and against the load order when a source is given (exit 0 clean, 1 something to fix, 2 a file
  couldn't be read): `.\sf-load-order.cmd rules check --rules "<rules file>" --data "<Data folder>"`
- Keep proposals from a `sort` run. Show the person `rules-proposed.json` first and ask. `rules accept` is the only
  command that writes outside `--out`: it writes the rules file (default `sf-load-order-rules.json` in the state
  folder) and its `.bak`, nothing else:
  `.\sf-load-order.cmd rules accept "<rules-proposed.json>" --all --state "<folder>"`
- Write only the proposals, without the order files: `.\sf-load-order.cmd rules propose --out "<report folder>"`

After a run, read `sf-load-order-report\report.txt` (or `report.json`) and explain the top 10 to the person. To
apply a suggested order, follow `sf-load-order-report\HOW-TO-APPLY.txt` with them; it starts with a backup. Never
copy the files into place without the person agreeing. For MO2 the steps are: close MO2, back up the profile's
`plugins.txt` and `loadorder.txt` (in `profiles\<profile>\`), then copy both files from the report folder into that
folder. MO2 takes the order from `loadorder.txt` and the on/off marks from `plugins.txt`, so copying only one of them
doesn't work. When MO2 starts, it reads the order from `loadorder.txt` whenever that file exists, so a `plugins.txt`
copied in alone leaves the old order in place. The two files the tool writes list the mods in the same order.

## 6. Inputs

- One load order source: the game's `%LOCALAPPDATA%\Starfield\Plugins.txt` (default), `--plugins-txt`, `--mo2`
  (with `--profile`), `--vortex-loadorder` (with `--vortex-staging`, default `%APPDATA%\Vortex\starfield\mods`, and
  optionally `--vortex-modlist`), or `--vortex-profile` (with `--vortex-exports`, `--vortex-stamp`, `--vortex-staging`).
- For `candidate`: one or more build `.zip` files, and the same source and `--data` options.
- Read without an option: `StarfieldCustom.ini` in `Documents\My Games\Starfield` (Windows's own Documents
  folder, even when it's moved to another drive, then `%USERPROFILE%\Documents` and `%OneDrive%\Documents`), or the MO2 profile's own copy when the profile uses local INI files. When it has
  `sTestFile` lines, the game loads those files instead of Plugins.txt, and the report gives one caution (kind
  `stestfile`).
- The game's Data folder: `--data`, else MO2's gamePath, else Steam (registry and `libraryfolders.vdf`), else Game
  Pass (`<drive>:\XboxGames\Starfield\Content\Data` and each drive's `.GamingRoot`).
- Optional: `--conflicts`, `--loot-masterlist`, `--loot-userlist`, `--clashes`, `--accept`, `--picks`,
  `--loot-order`, `--patch-sources`. The JSON formats are in the README ("Known clashes"; a `--clashes` entry may
  carry `settled_if_current`, a plugin and a SHA-256, to count as settled only while that build is installed).
- On Linux the Windows defaults (the `%LOCALAPPDATA%` Plugins.txt and LOOT folder, the `%APPDATA%` Vortex staging
  folder, the Steam and Game Pass search) don't apply. Give the source and `--data` (and `--vortex-staging`,
  `--loot-masterlist`, `--sf-mod-check` when wanted). With no source option it exits 2 and says so. File names are
  matched in any letter case.

## 7. Outputs

All in `--out` (default `sf-load-order-report` in the current folder):

- `report.txt`, `report.html` and `report.json` (schema 1; keys in the README, "report.json"). report.txt and
  report.html carry the byline; report.json has `author`, `license` and `url`. In report.json, `outputs` lists each
  file written by role (`txt`, `html`, `json`, `plugins_txt`, `loadorder_txt` for MO2, `vortex_json` for Vortex,
  `how_to`), and `sections.patches.built_for_old` counts plugins still built for an older kind of a master. A normal
  run has nine sections (inputs, order, patches, clashes, archives, loose, rules, records, recommend); `rules` is the
  Vortex mod rules check (skipped with a reason for MO2 and plain Plugins.txt runs). New keys in 0.2.0: `vortex_profile`,
  `patches` (a list of merged patches with `state` up to date, rebuild, not enabled or cannot check; `[]` without a
  manifest; `{"not_checked": why}` when it couldn't run), `rules` and `candidate` (see the README, "report.json").
  The missing-file findings count scripts, models, materials, textures and sounds separately.
- A `candidate` run writes `report.txt`, `report.html` and `report.json` only (no plugins.txt, no HOW-TO-APPLY.txt),
  and puts the copies it makes in `work\candidate`.
- `plugins.txt` (the suggested order in the game's format) and `HOW-TO-APPLY.txt`.
- A `sort` run writes the same files from the sorted order, plus `rules-proposed.json` (rules the person can keep with
  `rules accept`; `"rules": []` when there are none). Its report has the `sort` key and the "Sorted order" part in
  place of the suggested order.
- `loadorder.txt`, only for an MO2 source (MO2's order file; it goes into the profile folder with `plugins.txt`).
- `vortex-loadorder-recommended.json`, only for a Vortex source (for the Load Order page's Import button).
- `work\`: sf-mod-check's input Plugins.txt and its `conflict\conflicts.json`, when it was run.

Outside `--out`: the snapshot and cache in `--state` (default `%LOCALAPPDATA%\sf-load-order`, on Linux
`~/.local/state/sf-load-order`; nothing with `--no-state`), and the log file when `--log` is given. One `RESULT:`
line goes to stdout; the byline goes to stderr unless `--quiet` is given. `--version` prints the byline and the
credits and writes nothing. Screen output keeps the console's character set and escapes characters it can't show
(`\u0141`), so take exact paths from `report.json` (`outputs`, UTF-8), not from the RESULT line.

## 8. Exit codes

- 0: the report was written, whatever it found, or `--help` or `--version` printed what it shows (writing nothing).
- 1: the report was written, `--strict` was given, and it lists at least one problem.
- 2: it couldn't run (for `candidate`: a zip that is missing, a `.7z` or `.rar` with no 7-Zip, or not an archive; otherwise a bad or shortened option, no Python found by the launcher, no load order found, an output or
  log folder it can't write, or none of the three report files could be written). The `RESULT: CANNOT CHECK:` line
  says why and what to do.

## 9. The log

- `--verbose`: log lines (`HH:MM:SS [section] message`) on stderr.
- `--log <file>`: the same lines in that file, replaced on each run.
- A check that fails with an unexpected error is marked "failed" in the report, the other checks still run, and the
  log has the full error.
- The log holds the person's folder paths; tell them to read it before posting it anywhere.

## 10. Limits

- It never changes the game, Plugins.txt, Vortex or MO2. It writes only `--out`, the `--state` folder and the
  `--log` file. Applying a suggestion is the person's own step (HOW-TO-APPLY.txt).
- It never uploads anything and makes no network connections. It runs sf-mod-check only when one is found or given.
- It never ships or copies LOOT's masterlist; it reads the person's own copy.
- `sort` and `rules` never apply an order and never download a rules file. A plugin counts as a master when it has the
  master flag or an `.esm` or `.esl` name (esplugin 6.1.4, which libloot uses); light and medium never change a
  plugin's place. Vortex mod rules are only a tie-break. A rule that breaks the game's own rules is left out, with one
  caution that says why. LOOT's regular-expression names are checked before use: a pattern over 256 characters, a
  back-reference, a look-behind, a nested repeat, more than two open-ended repeats or more than six repeats of any
  kind is refused with a caution.
- Our own rules files take exact plugin file names only: no regex, no wildcard (`( ) [ ] +` are plain name characters).
- Merged-patch checks need sf-mod-check 0.15.0 or newer. `candidate`'s record check needs sf-mod-check 0.8.0 or newer
  and, under MO2, sf-load-order started from MO2. Modlist Backup exports before 0.6.2 hold no mod rules, so the Vortex rules section only
  names who won; it says so. Install extension 0.6.2 or later for the rules.
- Record conflicts need sf-mod-check 0.8.0 or newer. Under MO2, sf-mod-check sees the mods only when started from MO2;
  otherwise use `--conflicts`.
- A suggested order with "estimate" in its score is based on partial data: the moved mods should be checked in game.
- When the suggested order saves less than 10 points or 3% (whichever is more), the current order is kept.
- Don't use it while the game or the mod manager is changing the load order; run it after deploying.
- Off Windows, a Vortex mod id ending in a dot (`Mod9.`) doesn't match a staging folder copied from Windows (`Mod9`).
  Run it on Windows for such a folder.
