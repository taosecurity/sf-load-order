# sf-load-order

sf-load-order checks your Starfield load order and tells you, in plain words, what is wrong with it and how to fix
it. It reads the load order you already have (the game's own Plugins.txt, a Mod Organizer 2 profile, or a file
exported from Vortex's Load Order page), and it never changes your game, Vortex or MO2.

Download the latest zip from [Releases](https://github.com/taosecurity/sf-load-order/releases), unzip it, open a
Command Prompt in the folder (see [Install](#install)) and run:

```
.\sf-load-order.cmd
```

For AI assistants: step-by-step instructions are in [AGENTS.md](AGENTS.md).

## Example

This is a real run on a small made-up load order: three mod plugins and the official ones. NewModA.esm needs a
master that isn't installed, two mods ship the same texture in their archives, and one archive has a name the game
never loads.

```
> .\sf-load-order.cmd --plugins-txt Plugins.txt --data Data --depth light --no-state
sf-load-order 0.3.0 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
RESULT: 1 problem(s), 2 caution(s), 1 note(s) (report: sf-load-order-report\report.txt)
```

The screen shows only the byline and the RESULT line. The findings are in the report, which starts with the ten
that matter most, each with what to do about it:

```
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
    Fix: Order decides here. If you want MyMod.esm's versions, load MyMod.esm after NewModA.esm; if NewModA.esm's are
    right, leave it.
    (Section: Archives (.ba2 files).)
 4. [Note] The order can't be rated beyond masters and archives: there is no record-conflict data (run at --depth
    medium with sf-mod-check 0.8.0 or later, or pass --conflicts), so no record-based move is suggested.
    (Section: Rating.)
...
```

## What it checks

- Plugins whose masters are missing, turned off or loading too late, and how close you are to the plugin limits.
- Patches that were built for an older version of a mod they depend on (a mod that changed from a full to a light
  plugin, for example), which break with no error message.
- Known clashes from LOOT's masterlist and from your own notes.
- Mods whose archives (.ba2 files) or loose files overwrite each other, and which copy the game actually uses.
- Record conflicts, where one mod silently undoes another mod's changes (through sf-mod-check, if you have it).
- Merged patches (built by `sf-mod-check patch`) that are out of date because a mod they merged has changed.
- With Vortex: whether the Data folder really holds the profile you named, and loose files that two mods ship with
  different content, with the winner Vortex deployed.
- A new mod's `.zip`, before you install it (`sf-load-order candidate`, see
  [Checking a new mod build](#checking-a-new-mod-build)).
- A whole sorted load order, with every move explained, without LOOT (`sf-load-order sort`, see
  [Sorting a load order](#sorting-a-load-order-sort)), from the game's own rules, your rules and, when it is there,
  LOOT's masterlist.

It then rates your order, suggests a better one when the data supports it, and writes the files to apply it, with
step-by-step instructions. You decide whether to apply anything.

- It needs Python 3.9 or newer and nothing else.
- It is made for Windows (Steam and Game Pass installs, Vortex and MO2). It also runs on Linux with Python 3.10 or
  newer, for checking a copy of a load order (see [Linux](#linux)).
- It reads only. It writes only its report folder, a small snapshot and cache folder, and a log when you ask for one
  (see [PRIVACY.md](PRIVACY.md)).

This tool was built with [Claude Code](https://claude.com/claude-code).

## First run

With no options, sf-load-order checks the load order the game itself reads (`%LOCALAPPDATA%\Starfield\Plugins.txt`)
against the game's Data folder, which it finds by itself on Steam and Game Pass installs. It prints a byline and a
RESULT line like the one below; the counts depend on your load order:

```
> .\sf-load-order.cmd
sf-load-order 0.3.0 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
RESULT: 0 problem(s), 2 caution(s), 5 note(s) (report: sf-load-order-report\report.txt)
```

Then open `sf-load-order-report\report.html` in your browser, or `report.txt` in any text editor.

If you use Vortex or MO2, point the tool at your mod manager instead (see
[Where the load order comes from](#where-the-load-order-comes-from)). Then it can also check loose files and tell
which mod each file comes from.

Two details matter only if a script reads the output:

- The byline goes to stderr, so a script reading stdout sees only the RESULT line. `--quiet` leaves the byline out.
- The exit code is 0 whenever the report was written, even when it lists problems. Add `--strict` to get exit code 1
  when the report lists at least one problem (see [Exit codes](#exit-codes)).
- A test in a sandbox or a fresh folder should add `--state "<folder>"` (or `--no-state`). Otherwise the run uses
  the snapshot in your user profile, and an earlier run there changes what "changed since the last run" reports.
- The screen output uses your console's character set. A character it can't show (for example the Ł in a folder
  named Łukasz on a Western-European Windows console) is written as an escape such as `Ł`, so the run never
  stops on it. Scripts that need the exact paths should read `report.json` (`outputs`), which is always UTF-8.

## Install

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/). On the first screen of the
   installer, tick **Add python.exe to PATH**. To check that it worked, open a new Command Prompt and type
   `python --version`. It should print a version number. (The tool itself needs only Python 3.9 or newer.)
2. Download `sf-load-order-0.3.0.zip` from the [GitHub releases](https://github.com/taosecurity/sf-load-order/releases).
   If you like, check it against the `.sha256` file next to it with `certutil -hashfile sf-load-order-0.3.0.zip SHA256`.
3. Unzip it anywhere, for example into your Documents folder.
4. Open the unzipped folder in File Explorer, click the address bar at the top, type `cmd` and press Enter. A Command
   Prompt window opens in that folder, ready for the commands in this README.
5. Run `.\sf-load-order.cmd --version`. It should print the byline, starting `sf-load-order 0.3.0 by Richard Bejtlich`.

`.\sf-load-order.cmd` works in both Command Prompt and PowerShell. It only starts `python -I sf-load-order.py` with
the options you give it, so you can also run that line yourself (on Linux you must: see [Linux](#linux)).

Optional extras:

- **lz4**, for archives packed with LZ4 (some newer mod archives are): `python -m pip install lz4`. Without it,
  sf-load-order still lists which archives share files, but says "can't compare" for the contents of LZ4 archives.
  To see whether it's installed, run `python -I -c "import lz4; print(lz4.__version__)"`: it prints a version
  number when it is, and ends with `ModuleNotFoundError: No module named 'lz4'` when it isn't.
- **sf-mod-check** 0.8.0 or newer, for record conflicts: https://github.com/taosecurity/sf-mod-check/releases. Put
  it on PATH, or give its path with `--sf-mod-check`. Without it the tool runs at light depth (see [Depth](#depth)):
  every check except record conflicts still runs. Versions before 0.8.0 are refused with a note saying where to
  get the new one (the old name of those versions was sfmodcheck).

### Linux

sf-load-order runs on Linux with Python 3.10 or newer (its tests run on Linux too). Use it to check a copy of a load
order: a Plugins.txt, an MO2 instance folder or a Vortex export, plus the game's Data folder, copied from the Windows
PC or reached on its disk. Run it as `python3 -I sf-load-order.py` and give every path with an option:

```
python3 -I sf-load-order.py --plugins-txt ~/sf/Plugins.txt --data ~/sf/Data
```

The Windows defaults don't apply on Linux. There is no `%LOCALAPPDATA%` Plugins.txt, no Steam or Game Pass search,
no `%APPDATA%` Vortex staging folder and no LOOT folder. So give these options yourself:

- `--plugins-txt` (or `--mo2` or `--vortex-loadorder`) and `--data`. With no load order option, the tool stops with
  a RESULT: CANNOT CHECK line that says so.
- If you want them: `--vortex-staging`, `--loot-masterlist` and `--sf-mod-check`.

The snapshot and cache go to `~/.local/state/sf-load-order` (or `$XDG_STATE_HOME/sf-load-order`) unless you give
`--state`. File names are matched in any letter case, as the game does on Windows. It hasn't been tried with a Linux
game install (Proton).

## Words used here

- **Plugin:** a mod's data file (`.esm`, `.esp` or `.esl`). It adds or changes records in the game.
- **Master:** a plugin that another plugin needs loaded before it. Starfield.esm is the master of nearly every mod.
- **Full, medium and light plugins:** the three kinds of plugin slot. A light plugin is flagged to use a small slot,
  so it doesn't count toward the limit of about 250 full plugins.
- **Load order:** the order the game loads plugins in. When two plugins change the same record, the one loaded later
  wins.
- **Record:** one thing in a plugin, such as a weapon, a quest, a location or a setting.
- **FormID:** a record's number, such as `0002B560`. The first two digits depend on the load order.
- **Override:** a later plugin's copy of a record from an earlier one, which replaces it in the game.
- **ITM ("identical to master"):** a record a plugin copies without changing anything. It can undo other mods'
  changes, so it is best removed.
- **BA2:** Bethesda's archive format. Mods pack their scripts, models and textures into `.ba2` files.
- **Loose files:** a mod's files placed straight in the Data folder instead of inside an archive.
- **LOOT:** a community tool that sorts load orders and warns about known problems. Its masterlist is the shared list
  of those warnings.
- **xEdit (SF1Edit):** a community tool for viewing and editing plugins record by record.
- **Mutagen:** a .NET library for reading and writing Bethesda plugins, which sf-mod-check uses.

## Where the load order comes from

Give one source. When you give none, the game's own Plugins.txt is read.

| Your setup | Option | What is read |
|---|---|---|
| No mod manager, or you just want what the game reads | (none), or `--plugins-txt <file>` | `%LOCALAPPDATA%\Starfield\Plugins.txt` (Steam and Game Pass use the same file), or the file you name. |
| Mod Organizer 2 | `--mo2 <instance folder>` and, if you like, `--profile <name>` | The folder that holds `ModOrganizer.ini`, `profiles\` and `mods\`. The profile's `plugins.txt`, `loadorder.txt` and `modlist.txt` give the order, which plugins are on, and each mod's priority. Without `--profile`, MO2's selected profile is used. The game folder comes from MO2's `gamePath` setting. |
| Vortex | `--vortex-loadorder <file>`, and `--vortex-staging <folder>` if your staging folder isn't the default | On Vortex's **Load Order** page, click **Export** and save the file. That file gives the order. Each mod's own folder comes from Vortex's staging folder (default `%APPDATA%\Vortex\starfield\mods`; Vortex shows yours under Settings, Mods, Mod Staging Folder). Vortex's deployment record in the Data folder (`vortex.deployment.*.json`) says which mod deployed each loose file, so mods with no plugin are found too. |

With the Modlist Backup extension for Vortex (it writes a modlist and a load order file for each profile), you can
name the profile instead of exporting by hand:

    .\sf-load-order.cmd --vortex-profile "My Profile"

The newest export set of that profile is read from the folder in the extension's own settings file
(`%APPDATA%\Vortex\modlist-backup-auto-export.json`) and its `old` subfolder. `--vortex-exports <folder>` (may be
given more than once) reads another folder, `--vortex-stamp YYYYMMDD-HHMMSS` picks an older set, and
`--vortex-modlist <file>` next to `--vortex-loadorder <file>` pairs a modlist with a load order you exported
yourself. The modlist gives each mod's version and archive name, and its rules when the export has them.

With `--vortex-profile`, the tool also checks that the Data folder holds this profile: no mod plugin may be in Data
that the profile doesn't list, and Vortex's deployment record may name no mod that the profile doesn't enable.

When it isn't (Vortex has another profile deployed), only the checks that work from the lists run: the plugin names
the game drops, and the known clashes. Every check that reads the profile's plugin, archive or loose files is skipped
(load order and masters, patches, archives, loose files, Vortex mod rules, record conflicts and the suggested order),
since Data holds another profile's files. One caution says so, names the fix (deploy the profile in Vortex and run
again, or pass `--data` with a folder that holds this profile's files) and replaces the "file wasn't found" problems.

In `report.json` those sections say `skipped` with the reason, the mod plugins' `kind` and `masters` are `null` (their
headers aren't read), and `recommended` is `null`.

When the profile is the deployed one, the report notes when the export's plugin list differs from the game's own
Plugins.txt (the export is older than the last change).

Vortex writes mod plugin names in lower case in its export, so sf-load-order takes each name's real spelling from
the files in Data (or staging). It never reads Vortex's own database.

### The Data folder

The game's Data folder is found in this order:

1. `--data <folder>`, when you give it.
2. MO2's `gamePath`, for an MO2 source.
3. Steam: the Steam folder from the Windows registry and `%ProgramFiles(x86)%\Steam`, and every library listed in
   `steamapps\libraryfolders.vdf`, as `<library>\steamapps\common\Starfield\Data`. So a game on another drive's
   Steam library is found too.
4. Game Pass (the Xbox app): `<drive>:\XboxGames\Starfield\Content\Data` on every drive, plus the folders the Xbox
   app lists in each drive's `.GamingRoot` file.

If you have more than one copy, or the game is somewhere else, give `--data` (for example
`--data "D:\Games\Starfield\Data"`).

## What each check means

Each finding has a level: **problem** (fix it before playing), **caution** (look at it) or **note** (good to know).
The report groups findings by section, in this order.

### What was read

What the tool read, and anything wrong with the input itself: an enabled plugin whose file is missing, a plugin
listed twice, a name the game drops (a plugin whose name ends in an official plugin's name, such as
`-ShatteredSpace.esm`, or starts with `BlueprintShips-`, is removed from Plugins.txt by the game and never loads),
or no Data folder found.

A caution also comes when `StarfieldCustom.ini` (in `Documents\My Games\Starfield`, or the MO2 profile's own copy
when the profile uses local INI files) has `sTestFile` lines: the game (and MO2) then load the files those lines name
and skip Plugins.txt, so the order the report checks may not be what the game loads. The fix is to remove the lines
and manage the order through Plugins.txt or your mod manager.

### Load order and masters

- Each mod plugin's masters: missing, turned off, dropped by the game, or loading after the plugin that needs them.
- Plugins whose header can't be read (the file isn't a plugin).
- Plugin counts per kind, base game included: full up to 253, medium up to 256, light up to 4,096. A caution comes
  within 5% of a limit, a problem past it.
- `BlueprintShips-*.esm` files: the game always loads these after every other plugin, oldest file first, whatever
  the list says.

Official files (the base game, the DLC and Bethesda's free Creations) load in the game's own fixed order and are not
checked.

### Patches built for an older master

Inside a plugin, each record says which master it belongs to by a number that counts masters of one kind (full,
medium or light) separately. When a master changes kind in an update (full to light is common), every patch for it
now points at the wrong records, and neither the game nor the mod manager says anything. Two checks catch this:

1. **A scan of record headers** (no history needed): records that claim to override a record their master doesn't
   have, and records numbered past the plugin's own size limit.
2. **"A master changed kind since the last run"**: each run saves a small snapshot of every plugin's kind and the
   kinds its masters had (`--state`, default `%LOCALAPPDATA%\sf-load-order`, one file per load order). On the next
   run, a master whose kind changed under an unchanged patch is a problem until the patch is updated. A patch that
   was itself updated gets one caution (check it was built for the new master). The first run has nothing to compare
   with and says so. `--no-state` turns the snapshot off.

### Merged patches

A merged patch built by `sf-mod-check patch` is only right for the mods it was built from. When one of them is
updated, the patch holds old copies of its records. sf-mod-check writes a `patch-sources.json` beside each patch
(what it was built from), and sf-load-order 0.2.0 asks `sf-mod-check patch check` whether each patch is still up to
date. It needs sf-mod-check 0.15.0 or newer and runs at `--depth medium` and `heavy`.

- Manifests are looked for as `patch-sources.json` in the Data folder, in the folder of each enabled plugin, and in
  each enabled Vortex mod's staging folder. `--patch-sources <file or folder>` (may be given more than once) adds
  more: a folder is searched for `patch-sources.json` and for any other `.json` that looks like a manifest.
- A file from the old `patch_sources.py` (an older patch manifest, with the patch's name as plain text) can't be
  checked. Found beside a patch or passed with `--patch-sources` (in a folder or by name), it gives one note naming
  each such file, with the fix: rebuild the patch with sf-mod-check 0.15.0 or later. The `patch-sources.json` it
  writes beside the patch is found by itself.
- A manifest is used only when its `patch.name` is one plain plugin file name. A bad one gives a caution.
- The patch checked is the copy in your load order. A patch that isn't enabled is listed as "not enabled" and not
  checked.
- Result per patch: **up to date** (a note), **rebuild** (a problem when sf-mod-check says PROBLEM, else a caution;
  the reasons are quoted), **not enabled**, or **cannot check** (a caution; sf-mod-check failed or timed out).
- With no manifest found, sf-mod-check isn't started and `report.json` holds an empty list. With `--depth light`, no
  Data folder, sf-mod-check missing or older than 0.15.0, or a Vortex profile that isn't deployed, `report.json`
  says why the check didn't run.

### Known clashes

From four sources:

1. **LOOT's masterlist.** sf-load-order reads the copy LOOT keeps on your PC
   (`%LOCALAPPDATA%\LOOT\games\Starfield\masterlist.yaml`, plus `userlist.yaml` next to it; `--loot-masterlist` and
   `--loot-userlist` point elsewhere). For your enabled plugins it reports missing requirements, incompatible plugins
   that are both on, LOOT's warnings and errors, and plugins LOOT says need cleaning (when the file's CRC matches).
   LOOT downloads the masterlist the first time it runs for Starfield; without LOOT this part is skipped and the
   report says how to get it. The masterlist is never copied into this tool.
2. **`--clashes <file>`**: your own known clashes.
3. **`--accept <file>`**: clashes you have looked at and accepted, so they stop being reported until a file changes.
4. **`--picks <file>`**: your own order choices ("this plugin loads right after that one"), used by the suggested
   order.

The `examples` folder has a sample of each, with made-up plugin names. A file can be a JSON list of entries or an
object holding that list (`{"clashes": [...]}`, `{"accepted": [...]}`, `{"picks": [...]}`). Keys starting with `_`
are comments and are ignored. A bad file or entry is a caution that names the file, the entry and the fix; the rest
still runs.

**`--clashes`** entries ([examples\clashes.example.json](examples/clashes.example.json)):

| Field | Needed | Meaning |
|---|---|---|
| `plugins` | yes | A list of plugin file names (`.esm`, `.esp` or `.esl`). |
| `level` | yes | `problem`, `caution` or `pick-one` (only one of them should win: reported as a caution). |
| `note` | yes | A sentence saying what goes wrong. |
| `min` | no | How many of the plugins must be enabled for the clash to show (default: all of them). |
| `fixed_by` | no | A patch plugin. When it is enabled and loads after all of them, the clash is reported as settled. |
| `link` | no | A web page about the clash, copied into the report. |
| `settled_if_current` | no | `{"plugin": "<patch>.esm", "sha256": "<64 hex digits>"}` (`sha256` may be a list). The clash is settled while the enabled plugin has one of these hashes, so a patch you rebuilt shows up as settled and an old build of it doesn't. When the entry's `fixed_by` patch is enabled but loads before one of the plugins, the clash stays open whatever the hash. A bad value skips the entry with a caution. |

**`--accept`** entries ([examples\accept.example.json](examples/accept.example.json)):

| Field | Needed | Meaning |
|---|---|---|
| `files` | yes | `{"<plugin>": {"sha256": "<64 hex digits>", "size": <bytes>}}` for each plugin. `sha256` may be a list, to accept several versions. |
| `plugins` | no | The plugins of the clash (default: the names in `files`). Each needs a hash in `files`. |
| `date`, `why`, `by`, `history` | no | Your own notes. `date` and `why` are shown in the report. |

An acceptance holds only while every file still has a listed SHA-256. When any of the plugins is updated, the clash
comes back for review. Each open clash in report.json carries a ready-made entry with the current hashes
(`findings[].data.accept_entry`), so you can paste it in.

**`--picks`** entries ([examples\picks.example.json](examples/picks.example.json)):

| Field | Needed | Meaning |
|---|---|---|
| `plugin` | yes | The plugin to move. Not an official plugin, and one pick per plugin. |
| `after` | yes | The plugin it should load right after. |
| `why`, `date` | no | Your own notes. |

Picks that form a loop (A after B, B after A) are all skipped with a caution. A pick that would put a plugin above
its own master isn't applied, and a pick whose plugins aren't both enabled is reported as not applied.

### Archives (.ba2 files)

Mods pack textures, meshes, sounds and scripts in .ba2 archives. When two mods' archives hold a file with the same
path, the game uses the copy from the archive that loads last, and archives follow plugin order. Vortex's own
conflict view checks loose files only, so it never shows these.

- **Which archives load**: the game loads only `<plugin> - Main.ba2`, `- Main01.ba2`, `- Main02.ba2` and so on (it
  stops at the first missing number), the same for `- Textures`, and `- Voices_en.ba2`. An archive named any other
  way (`<plugin> - Meshes.ba2`, or a `Main03` after a missing `Main02`) never loads, and is reported.
- **Shared paths**: each pair of plugins whose archives share paths gets one piece of advice: built on it (the
  winner has the loser as a master, so it's meant to win), same mod, identical copies (order changes nothing), or
  "order decides" (different content: the one loading last is what you see).
- Contents are compared by unpacking the shared files (general archives stored or zlib-packed, texture archives by
  their pixel data, LZ4 archives when `lz4` is installed).
- Archives in Data that no enabled plugin loads are listed too.

### Loose files

Files a mod ships outside archives. A loose file in Data beats every archive, whatever the plugin order. This check
needs each mod's own folder, so it runs for MO2 and Vortex sources, not for a plain Plugins.txt.

- Mods shipping the same loose file: identical copies need nothing. With MO2 the mod with the higher priority wins.
  With Vortex its own rules decide, and when the copy in Data matches one mod's copy, that mod is named.
- Loose files that hide a file inside a loaded archive, and whether the content is the same.
- Stray files: loose files in Data that no enabled mod ships, counted by folder.

### Vortex mod rules

Vortex lets a mod say it loads before or after another. For loose files, which mod wins in Data is decided by those
rules, not by the plugin order, so this section looks at each pair of enabled mods that ship the same loose path
with different content. It needs a Vortex source with a modlist export (`--vortex-profile`, or `--vortex-modlist`),
and the loose-file check must have run.

- Exports from Modlist Backup before 0.6.2 hold no mod rules. Then each differing pair is a note that says whose copy
  Vortex deployed (from the deployment record, else from the copy Data holds), and one note says plainly what can't
  be seen. Installing Modlist Backup 0.6.2 or later adds the rules to the export.
- With rules in the export: a pair of mods with different copies and **no** ordering rule is a caution ("Real clash
  with no rule", or "Possible clash with no rule" when the copies couldn't be compared). Rules that only order identical copies, rules between mods that share no loose file, and rules that
  name a mod that isn't enabled are listed as notes.
- When the profile isn't the deployed one, no winner is named.
- The section is skipped, with the reason, for MO2, a plain Plugins.txt, no modlist, or when the loose-file check
  didn't run.

### Record conflicts (sf-mod-check)

At depth medium and heavy, sf-load-order runs `sf-mod-check conflict` on your load order (or reads a
`conflicts.json` you give with `--conflicts`) and turns the result into plain findings. A "lost change" is a mod's
edit to a record that the game throws away because a later plugin, which doesn't build on it, supplies that record
too. Each field that is lost is labeled:

- **Forwardable**: the winner only carries an older value, so a patch can copy the lost change forward.
- **Choice**: both mods changed that field on purpose, so you have to pick a winner.
- **No effect**: the difference doesn't matter in game (list order, letter case).

Each enabled mod plugin gets a rating: **neutral** (nothing of it is lost and it undoes nothing), **neutral here**
(it is involved, but in nothing the order can change), **order matters** (it is in a pair where moving one of them
changes what you get), or **not checked** (sf-mod-check couldn't load it). Each pair gets advice: no action (built on
it, same mod, same edit), **patch candidate**, or **pick a winner**. Official changes that a mod undoes are listed
too: no order fixes those.

When the winner is an official `BlueprintShips-*.esm` file, the pair gets neither label: its advice is "no action:
BlueprintShips file wins". The game loads those files after every mod, so no load order or patch brings the mod's
change back, and the report says that the mod's edit to those ships (or other records) doesn't reach the game. Such a
pair isn't counted as one "to act on", doesn't make a plugin "order matters", isn't moved, and isn't in the
merged-patch note. A `BlueprintShips-` file that sf-mod-check's conflicts.json marks as official counts too.

The navmesh info map (record type `NavigationMeshInfoMap`, shown as "navmesh map") is never a lost change, whichever
plugin wins it. Each plugin's copy lists only its own navmeshes, so it isn't a record one plugin wins whole: it isn't
patched, and it shouldn't be copied by hand either, because the Creation Kit rebuilds it when a navmesh is finalized.

The report gives such a pair a note (advice "no action: navmesh info map") instead of "patch candidate" or "pick a
winner". The navmesh info map isn't counted in the pair's records, in what's "to act on", in the order's score or in
the merged-patch note, and a mod's copy over an official one isn't counted as undoing an official change. The
records summary counts its entries apart from the lost change entries, and a pair with only the navmesh info map
doesn't change a plugin's rating. A pair that also has other records keeps its advice for those records.

The records an enabled plugin uses can point at files that aren't installed (not loose and not in an archive the game
loads). The finding counts them by kind, for example "1 script(s), 1 model(s) and 2 texture(s) its records use
aren't installed" (kinds: script, model, mesh, material, texture, sound; "mesh" is a `.mesh` geometry file). It is a caution when a script is missing, else
a note. The kinds come from a conflicts.json whose entries name them (newer sf-mod-check versions do); an entry with no kind
counts as a model.

sf-mod-check reads plugins from the Data folder. Under MO2 it only sees your mods when it is started from MO2 (see
[Traps](#traps)).

### Suggested order

The tool scores an order by the changes it throws away: each lost field counts 3 when it affects gameplay, 1 when it
is cosmetic (lighting, music, weather, names and similar), and 0 when it has no effect. Changes to official records
that a mod undoes count too. Lower is better.

Starting from your current order (LOOT's, if you just sorted), it applies your `--picks`. Then it tries letting the
losing plugin of each "patch candidate" or "pick a winner" pair load after the winner, and keeps a move only when
the score of the whole order goes down. Some things never move:

- Masters always stay above the plugins that need them.
- A mod's plugins keep their order.
- Official and BlueprintShips files stay where the game puts them.

Moves that would change which copy of an archived file wins are pointed out.

- **The near-tie rule**: when the moves would save less than 10 points, or less than 3% of your current score
  (whichever is more), your order is kept. Small differences aren't worth reshuffling a working setup.
- **Estimates**: sf-mod-check's conflicts.json lists only the losses of the order it checked. When a pair is
  flipped, the cost to the other plugin is known only for fields both changed, so the recommended order's score is
  then marked as an estimate. Check the moved mods in game after applying it.
- **Without record data** (depth light, or no sf-mod-check), the order can't be rated: only master order and your
  picks are applied.
- **`--loot-order <file>`**: give a Plugins.txt sorted by LOOT, and the report says how many plugins LOOT places
  differently and, with record data, what LOOT's order would score.

Files written to the report folder:

- `plugins.txt`: the suggested order in the game's own format (official masters left out, as the game expects).
- `loadorder.txt`: only for an MO2 source, MO2's own order file. MO2 takes the order from `loadorder.txt` and the
  on/off marks from `plugins.txt`, so you need both. With MO2 closed, back up the profile's `plugins.txt` and
  `loadorder.txt` (in `profiles\<profile>\`), then copy both files from the report folder into that folder.
  When MO2 starts, it reads the order from `loadorder.txt` whenever that file is there; it reads the order from
  `plugins.txt` only when `plugins.txt` alone changed while MO2 was running. So a new `plugins.txt` copied in on its
  own doesn't change the order, and the two files the tool writes always list your mods in the same order.
- `vortex-loadorder-recommended.json`: only for a Vortex source. Your export, reordered, for the Load Order page's
  **Import** button. When nothing moves, it is a byte-for-byte copy of your export.
- `HOW-TO-APPLY.txt`: plain steps for your setup, starting with a backup of what you have now, and how to go back.

The files are written even when nothing moves; they then equal your current order, and HOW-TO-APPLY.txt says so.

**If you use only the game's Creations menu** (no mod manager), the game itself writes Plugins.txt: at every start
and whenever you change the order in Creations. In the test on the
[facts page](https://taosecurity.github.io/starfield-load-order-facts/) the start-up rewrite kept every plugin line in
its place, so copying `plugins.txt` in with the game closed works. If you would rather not copy a file over it,
HOW-TO-APPLY.txt also gives the in-game route: open Creations, then Load Order, and move each Creation until the list
matches the mod lines of `plugins.txt`. Pick one place to manage the order, because a later change in Creations
replaces what you copied.

**If you use MO2**, the `plugins.txt` the tool writes leaves the official plugins out. MO2 2.5.2 pins only eight of
them, so after you copy the files, check in MO2's Plugins tab that Shattered Space, Terran Armada and the other
official plugins are still ticked. The report has a caution and HOW-TO-APPLY.txt a step when your load order has such
a plugin. (This comes from reading MO2's source, not from a run.)

## Checking a new mod build

`sf-load-order candidate <zip> [<zip> ...]` checks a new build before you install it: the zips are the parts of one
build (one plugin each). It uses the same load order options as a normal run and writes `report.txt`,
`report.html` and `report.json` to `--out`. It only reads the zips; the copies it needs go to `<out>\work\candidate`.
A mod that already holds a plugin with the same name as one of the build's, or whose name is a build zip's name
with another version (such as `ShipMod-Loose-0.2.0` for `ShipMod-Loose-0.3.0.zip`), is taken to be the older copy
of this build and is left out of the comparison. A zip whose files all sit in one folder of their own (such as
`ShipMod\ShipMod.esm`, with or without a readme beside it) is read as if that folder were Data.

Example, with a new build of ShipMod, built on the installed CoreTools. The older ShipMod is installed as a mod of
its own (OldShipMod), so it is left out of the comparison:

```
> .\sf-load-order.cmd candidate ShipMod-0.3.0.zip --mo2 mo2 --data game\Data --no-state --out out
sf-load-order 0.3.0 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
RESULT: 0 problem(s), 2 caution(s), 1 note(s) (report: out\report.txt)
```

The note names OldShipMod as the older copy left out. One caution says the build overrides two files of CoreTools
with different content; it is a caution, not a problem, because overriding the mod a build is built on is usual. The
other caution says the records weren't checked: run from the command line, sf-mod-check can't see MO2's mods
(start sf-load-order from MO2 for that check).

| Finding | Level |
|---|---|
| A script (`.pex`) the build ships that another enabled mod also ships | problem (when that mod is a master of the build, it is treated like the "Another file the build ships with different content" row: a caution, or a note when identical) |
| With a plain Plugins.txt and the build's older copy installed: Data's loose files aren't compared, since the older copy's loose files can't be told apart from another mod's (archives are still compared plugin by plugin) | one caution (kind `data-unsorted`, with `replaced`) |
| Another file the build ships with different content than an enabled mod's copy | problem; a caution when the other mod is a master of the build; a note when the content is identical. When one copy is loose and the other archived, the text says the loose copy always wins (kind `wins` when it is the build's, `hidden` when it is the other mod's) |
| Two parts of the build shipping the same path | problem for scripts, a caution for other files |
| Two zips of the build shipping an archive with the same name | problem |
| The installed mods left out as the build's older copy | note |
| Files that can't be compared, or `--depth light` (names only) | caution |
| Build archives no plugin of the build loads, a fomod installer, an installed archive that can't be read | caution |
| Files in a fomod installer's option folders (such as `00 Core\`): not compared, since where they land depends on the installer's choices | caution (counted in `not_compared`) |
| Records the build changes that an installed mod also changes, with fields a conflict patch can forward (Forwardable) or fields both changed (Choice) | caution, one per installed mod (kind `shared-records`) |
| Records shared with an installed mod that differ in nothing that matters (No effect) | note (kind `shared-records`) |
| The record check couldn't run (no sf-mod-check, no Data folder, a Vortex profile that isn't deployed, MO2 started from outside MO2) or failed, or a build plugin couldn't be loaded (a missing master) | caution |
| No shared records, or `--depth light` (the record check doesn't run) | note |

At `--depth medium` (the default) and `heavy` it also checks records: it runs `sf-mod-check conflict` (0.8.0 or
newer) on the load order with the installed copy of the build left out and the build's plugins in its place (so
installed patches built on it still load after it), or added at the end, where a new mod lands, when no copy is
installed. A build plugin that sf-mod-check couldn't load (a master that isn't installed) gets a caution naming the
missing master, and its records aren't compared. The build's plugins are read from `<out>\work\candidate\extra` (`--extra-data`), and
only records are compared (`--no-assets`). For each installed mod whose records the build also changes, one
finding gives the count, how many are Forwardable or Choice, and up to five examples. Bethesda's records the
build overrides aren't reported. Under MO2, sf-mod-check sees the mods only when sf-load-order is started from
MO2; otherwise the record check is skipped with a caution. `--max-memory <GB>` is passed to `conflict`.

At `--depth heavy` it also runs `sf-mod-check check` on each plugin of the build: deleted references are a problem,
ITMs and errors are cautions. It reads each plugin's masters from the Data folder, so a plugin whose master is
another part of the same build is skipped with a caution (install the build and run `sf-mod-check check` on it).
Output it can't read is a caution, never "nothing to clean".

A `.7z` or `.rar` is read through 7-Zip's `7z.exe` when it is installed (on PATH or in Program Files\7-Zip); the
files are unpacked into `<out>\work\candidate\unpacked` and `report.json` names the format of each zip (`zip`,
`7z` or `rar`). A member path that is absolute or holds `..` stops the run. Without 7-Zip, or with a missing file or
a file that isn't an archive, the run stops with exit 2 and the message names the fix.
`--strict` exits 1 when there is a problem.
`report.json` has a `candidate` key (see below) and shows only the inputs and candidate sections.

## Sorting a load order (`sort`)

`sf-load-order sort` builds a whole, valid load order from the plugins you have enabled. The main run checks your
order and suggests the fewest moves that fix it; `sort` also applies your rules, a community rules file and LOOT's
rules, and explains every placement. It starts from your current order, moves as few plugins as it can, and never
applies anything: you copy the files it writes, as `HOW-TO-APPLY.txt` says, after backing up your order.

```
.\sf-load-order.cmd sort
```

It reads the same load order options as a normal run (`--plugins-txt`, `--mo2`, `--vortex-*`, `--data`), runs every
check of a normal run (`--depth light` makes it quick), and takes the same `--picks`, `--clashes`, `--accept`,
`--loot-masterlist` and `--loot-userlist`. It writes, to `--out`: `plugins.txt`, `loadorder.txt` (MO2), the Vortex
import file, `HOW-TO-APPLY.txt`, `rules-proposed.json` (see [Your own ordering rules](#your-own-ordering-rules)) and
the report, which has a "Sorted order" part instead of the "Suggested order" one.

### What decides the order

The rules, from the strongest down. A rule that contradicts a stronger one is left out and the report says which
rule won and why; nothing is silently dropped.

1. **The game's own rules**, read from the plugins on every run: the official plugins load first in their own order,
   BlueprintShips files last, a plugin loads after each of its masters, and master plugins load before the others.
   A plugin is a master when it has the master flag or its name ends in `.esm` or `.esl` (source: esplugin 6.1.4,
   the library LOOT uses); the light and medium flags never change a plugin's place.
2. **Your picks** (`--picks`), **your rules file**, **the patches' targets** (a patch loads after the plugins it
   changes, from `--clashes` `fixed_by` entries and `patch-sources.json` files), **a community rules file**
   (`--community-rules`), and **LOOT's userlist and masterlist** `after` rules (used whenever the masterlist is
   found; LOOT's groups only with `--loot-groups`; `--no-loot` leaves LOOT out).
3. **Record conflicts** (at `--depth medium`, with sf-mod-check): where the rules leave a choice, the plugin that
   loses fields loads after the one that wins them, when that lowers the loss score. The order stays as it is when
   the gain is small.
4. **Vortex mod rules** are only a tie-break: a Vortex rule the sort breaks is kept when the order stays valid, and
   otherwise gets one note.
5. **Your current order** breaks every tie (`--tie-break name` orders by file name instead, the same on any PC; it
   moves many plugins).

### Example

A real run on a made-up load order: five mod plugins and the official ones. `ExampleQuestsPatch.esm` has
`ExampleQuests.esm` as a master but is listed before it, and the rules file says `ExampleArmorRetexture.esm` loads
after `ExampleArmor.esm`. Its second rule, that `ExampleQuests.esm` loads after its own patch, can't be met.

```
> .\sf-load-order.cmd sort --plugins-txt Plugins.txt --data Data --depth light --no-state --no-loot --rules my-rules.json
sf-load-order 0.3.0 by Richard Bejtlich - GPL-3.0 - https://github.com/taosecurity/sf-load-order
RESULT: 1 problem(s), 1 caution(s), 1 note(s) (report: out\report.txt)
```

The "Sorted order" part of `out\report.txt`:

```
Sorted order
------------
Sorting moves 2 plugins; the order wasn't rated (no record-conflict data).
Moves (2):
   1. Load ExampleArmor.esm after ExampleLights.esm. Why: Your rule (my-rules.json, entry 1) says
      ExampleArmorRetexture.esm loads after ExampleArmor.esm.
   2. Load ExampleQuests.esm after ExampleArmorRetexture.esm. Why: ExampleQuestsPatch.esm has ExampleQuests.esm as a
      master.
Rules not met (1):
   1. Your rule (my-rules.json, entry 2) says ExampleQuests.esm loads after ExampleQuestsPatch.esm, but
      ExampleQuestsPatch.esm has ExampleQuests.esm as a master. The rule wasn't used.
Rules checks (requires and incompatible):
  Nothing to fix.
Rules used:
  Rules used, by source: the game (masters, master plugins first): 1, your rules file: 1.
  LOOT files read: none.
Proposed rules:
  0 proposed rules in out\rules-proposed.json.
```

(The one problem in the run's top 10 is the current order, with the patch above its master; the caution is the rule
that wasn't used.) To see why one plugin is where it is:

```
> .\sf-load-order.cmd rules explain ExampleQuests.esm --plugins-txt Plugins.txt --data Data --no-loot --rules my-rules.json
ExampleQuests.esm is number 11 in the sorted order (number 12 now): the sort moves it.
Something loads after it because:
  - ExampleQuestsPatch.esm has ExampleQuests.esm as a master
The nearest plugin above it that the sort doesn't move: ExampleArmorRetexture.esm.
Rules about it that weren't used:
  - Your rule (my-rules.json, entry 2) says ExampleQuests.esm loads after ExampleQuestsPatch.esm, but ExampleQuestsPatch.esm has ExampleQuests.esm as a master. The rule wasn't used.
```

### What `sort` does not do

- It never applies the order, and it never downloads a rules file or LOOT's masterlist (it reads your own copy).
- Two rules that can't both hold are not both applied: the report names the one that was left out. A loop among the
  game's own rules (plugins that are each other's masters) is a problem, and then only the report and
  `rules-proposed.json` are written, with no order files.
- LOOT's `req` and `inc` entries stay in the known-clashes section; `sort`'s own requires and incompatible checks
  come from the rules files.
- It doesn't rate an order without record-conflict data: without sf-mod-check, the report says so and no record-based
  move is made.

## Your own ordering rules

Your rules live in one file, `sf-load-order-rules.json`, in the state folder (`%LOCALAPPDATA%\sf-load-order`), or in
the file you give with `--rules`. A missing default file is fine; a file given with `--rules` that doesn't exist stops
the run with exit 2. `--no-rules` ignores it. `examples\sf-load-order-rules.example.json` is a sample with made-up
names. Schema 1:

```
{
  "schema": 1,
  "kind": "user",
  "about": {"name": "My rules"},
  "groups": [{"name": "Late Patches", "after": ["default"]}],
  "rules": [
    {"id": "u-0001", "plugin": "ExampleArmorRetexture.esm", "after": ["ExampleArmor.esm"],
     "note": "I want the retexture to win.", "evidence": {"kind": "manual", "date": "2026-11-25"}},
    {"plugin": "ExampleQuestsPatch.esm", "group": "Late Patches", "requires": ["ExampleQuests.esm"]},
    {"plugin": "ExampleLights.esm", "incompatible": ["ExampleOldLights.esp"]}
  ]
}
```

- Each rule names one `plugin` (an exact file name; no pattern, no wildcard) and at least one of `after`, `before`,
  `requires`, `incompatible` and `group`. `note` and `evidence` are optional. `evidence.kind` is `master`, `patch`,
  `choice`, `clash`, `manual` or `link`; `evidence.files` pins the plugins' SHA-256 hashes.
- `kind` is `user` for your file and `community` for a file you got from someone else (given with
  `--community-rules`; nothing is downloaded, and a community rule ranks below yours).
- A rule with an unknown field, a bad name or a list that is too long is skipped with a caution naming it. A file
  over 5 MB or with more than 10,000 rules is refused. Keys that start with `_` are comments.
- A rule whose pinned hashes no longer match the installed plugins is **stale**: it is still applied, and one caution
  says to run `sort` again and accept or delete the old rule.

### The `rules` command

| Command | What it does |
|---|---|
| `rules list` | Every rule from your file, the community file and LOOT, and whether it is active in this load order (given a source option). `--derived` adds the game's own rules. |
| `rules explain PLUGIN` | Why the plugin is where it is in the sorted order: every rule into and out of it, and any rule about it that wasn't used. |
| `rules check` | Checks the files: schema, names, limits and loops among a file's own rules; with a source option also rules the game contradicts and stale rules. Exit 0 clean, 1 something to fix, 2 a file couldn't be read. |
| `rules propose` | Writes `rules-proposed.json` into `--out`, as `sort` does, without the order files. `--kind patch` proposes each patch's targets from `--clashes`, for sharing. |
| `rules accept PROPOSED.json` | Copies proposals (`--id ID`, or `--all`) into your rules file. |

`sort` always writes `rules-proposed.json`: the record-conflict choices it made, each as a rule ("the loser loads
after the winner") with the record counts and the plugins' hashes, so the same choice isn't worked out again.
`rules accept` is the only command that writes outside `--out`, and it writes only your rules file and its `.bak`
copy (the old file), in one step, so a crash never leaves half a file. It refuses a proposal that contradicts a rule
already in your file and writes nothing. Each command takes `--json FILE` to write its result as JSON.

## Depth

| Depth | What runs | Time |
|---|---|---|
| `light` | Everything except record conflicts: inputs, masters and order, patches, clashes, archives, loose files and the suggested order (masters and picks only). | Seconds, plus the first read of large archives. |
| `medium` | Adds record conflicts through `sf-mod-check conflict`. This is the default. Without sf-mod-check the record section is skipped with how to get it, which is the same as light. | A few minutes on a big load order (it reads every plugin). |
| `heavy` | Adds `sf-mod-check check` on each enabled mod plugin, for identical-to-master records (ITMs) and deleted references. | Several seconds per plugin. |

For `candidate`, `light` compares file names only (seconds), `medium` compares file contents and runs one
`sf-mod-check conflict` for the records the build shares (the same few minutes as a normal medium run), and `heavy`
adds `sf-mod-check check` on each plugin of the build.

`--max-memory <GB>` is passed to `sf-mod-check conflict`, which stops cleanly at that limit. sf-mod-check is
stopped if `conflict` runs longer than 30 minutes; for a very large load order, run it yourself and give its
`conflicts.json` with `--conflicts`.

## All options

These are the options `.\sf-load-order.cmd --help` lists, and the ones marked `sort` or `rules`, which
`.\sf-load-order.cmd sort --help` and `.\sf-load-order.cmd rules <command> --help` list. `.\sf-load-order.cmd candidate --help` lists the ones
`candidate` takes: the zips, the load order and `--vortex-*` options, `--data`, `--depth`, `--sf-mod-check`,
`--max-memory`, `--state`, `--no-state` and the output options, with the same meanings.

| Option | Meaning |
|---|---|
| `--plugins-txt FILE` | A Plugins.txt to check (default: `%LOCALAPPDATA%\Starfield\Plugins.txt`). |
| `--mo2 FOLDER` | A Mod Organizer 2 instance folder (it holds `profiles\` and `mods\`). |
| `--profile NAME` | The MO2 profile (default: MO2's selected profile). |
| `--vortex-loadorder FILE` | A load order file exported from Vortex's Load Order page (Export button). |
| `--vortex-modlist FILE` | A modlist export from the Modlist Backup extension, read next to `--vortex-loadorder`. |
| `--vortex-profile NAME` | A Vortex profile: its newest export set (modlist and load order) from the Modlist Backup extension, and a check that Data holds this profile. |
| `--vortex-exports FOLDER` | Where the extension's export sets are (may be given more than once; default: the folder in `%APPDATA%\Vortex\modlist-backup-auto-export.json` and its `old` subfolder). |
| `--vortex-stamp YYYYMMDD-HHMMSS` | With `--vortex-profile`: use the export set with this stamp instead of the newest. |
| `--vortex-staging FOLDER` | Vortex's mod staging folder, for loose-file checks (default: `%APPDATA%\Vortex\starfield\mods`). |
| `--data FOLDER` | The game's Data folder (default: Steam's, then Game Pass's XboxGames folder on each drive). |
| `--depth {light,medium,heavy}` | How much to check (see [Depth](#depth)). |
| `--sf-mod-check EXE` | The sf-mod-check program (default: `sf-mod-check` on PATH, then `sfmodcheck`, its old name). |
| `--conflicts FILE` | Reuse a conflicts.json from an earlier `sf-mod-check conflict` run (nothing is run). |
| `--max-memory GB` | Passed to `sf-mod-check conflict`. |
| `--loot-masterlist FILE` | LOOT's Starfield masterlist.yaml (default: `%LOCALAPPDATA%\LOOT\games\Starfield\masterlist.yaml`). |
| `--loot-userlist FILE` | LOOT's userlist.yaml (default: next to the masterlist). |
| `--clashes FILE` | Your own known-clashes file. |
| `--accept FILE` | Clashes you have accepted, pinned to the files' hashes. |
| `--picks FILE` | Your own order picks: plugin X loads right after plugin Y. |
| `--loot-order FILE` | A Plugins.txt sorted by LOOT, to compare with. |
| `--no-loot` | (`sort` and `rules`) Don't read LOOT's masterlist or userlist; `sort` also leaves LOOT out of the known-clashes section. |
| `--loot-groups` | (`sort` and `rules`) Also use LOOT's groups (off by default). |
| `--rules FILE` | (`sort` and `rules`) Your rules file (default: `sf-load-order-rules.json` in the state folder). |
| `--no-rules` | (`sort` and `rules`) Don't read your rules file. |
| `--community-rules FILE` | (`sort` and `rules`) A community rules file (a local file; nothing is downloaded). |
| `--tie-break {current,name}` | (`sort`) `current` keeps your order wherever the rules allow (default); `name` orders by file name. |
| `--patch-sources FILE\|FOLDER` | A `patch-sources.json` (or a folder of them) from `sf-mod-check patch`, checked with `sf-mod-check patch check` besides those found next to the enabled plugins (may be given more than once). A manifest from the old `patch_sources.py` gives a note asking for a rebuild with sf-mod-check 0.15.0 or later. |
| `--state FOLDER` | Where to keep the snapshot used for "changed since the last run", and the archive and loose-file cache (default: `%LOCALAPPDATA%\sf-load-order`; on Linux `~/.local/state/sf-load-order`). |
| `--no-state` | Don't read or write the snapshot or the cache. |
| `--out FOLDER` | Where the report and the files to apply go (default: `.\sf-load-order-report`). |
| `--strict` | Exit 1 when the report lists any problem. |
| `--quiet` | Print only the RESULT line (no byline on stderr). |
| `--verbose` | Write the troubleshooting log to the screen (stderr). |
| `--log FILE` | Write the troubleshooting log to this file. |
| `--version` | Print the byline (version, author, license, project page) and the credits, then stop. |
| `-h`, `--help` | Show the options and exit. |

Options can't be shortened (`--plug` is refused), so a typo never picks the wrong option.

## The report

Every run writes three files to `--out` (default `sf-load-order-report` in the current folder):

- `report.txt`: plain text. A header (the byline, then tool version, source, profile, Data folder, files read, date,
  depth), the top 10 findings (problems, then cautions, then notes; findings with a fix first), every section with
  its status and findings, the suggested order, and the files written.
- `report.html`: the same in one self-contained page, with the byline under the title and a footer with the
  license, the source and issues links, the credits and the privacy line ("It only reads your game and mod manager
  files and never uploads anything."). It has no scripts, and no links except this project's page, its issues page
  and sf-mod-check's page. Light and dark follow your system setting. Long lists fold away with their counts.
- `report.json`: the same for scripts and other tools.

### report.json

Schema 1. Top-level keys:

| Key | Meaning |
|---|---|
| `schema` | `1`. A change that breaks readers raises it. |
| `tool_version` | The sf-load-order version that wrote the file. |
| `author`, `license`, `url` | Who made the tool, its license (`GPL-3.0`) and its project page. |
| `generated` | When, as local time (`2026-11-19T21:30:00`). |
| `counts` | `{"problem": n, "caution": n, "note": n}`. |
| `load_order` | What was read: `source` (`plugins.txt`, `vortex` or `mo2`), `profile`, `files` (each input file by role), `data` (the Data folder), `plugins` (in load order: `name`, `enabled`, `official`, `listed`, `path`, `dropped`, `kind` F/M/L or `?`, `master` (0.3.0: true when the plugin counts as a master, see [Sorting](#sorting-a-load-order-sort)), `masters`; `kind` and `masters` are `null` for a file that wasn't read, such as a mod plugin of a Vortex profile that isn't deployed), `mods` (`name`, `enabled`, `path`, `plugins`, `priority`, `id`, `files_count`; `null` for a plain Plugins.txt) and `mods_note`. |
| `findings` | Worst first. Each: `level` (`problem`, `caution`, `note`), `section`, `text` (what is wrong), `fix` (what to do, or `null`), `plugins` (names it is about) and `data` (extra detail: counts, FormIDs, paths, a ready `accept_entry`). |
| `sections` | Per section (`inputs`, `order`, `patches`, `clashes`, `archives`, `loose`, `rules`, `records`, `recommend`; a `candidate` run has `inputs` and `candidate`): `status` (`done`, `skipped`, `failed` or `missing`), `why` when it didn't run, and a few counts (for example `patches.built_for_old`, the plugins still built for an older kind of a master). |
| `recommended` | The suggested order as plugin names, or `null` when none was worked out. |
| `outputs` | Each file written, by role (`txt`, `html`, `json`, `plugins_txt`, `loadorder_txt` for MO2, `vortex_json` for Vortex, `how_to`). |
| `results` | A summary per section, counts only (for example `records.ratings`, `archives.pairs_by_advice`, `recommend.score_current` and `score_recommended`, `recommend.estimate`, `recommend.moves`). |
| `left_out` | What the summary leaves out and where to find it (for example the lost records with their field values, which stay in sf-mod-check's conflicts.json and report.html). |

New in 0.2.0 (nothing was renamed):

| Key | Meaning |
|---|---|
| `vortex_profile` | With `--vortex-profile`: `name`, `stamp`, `files` (modlist, loadorder, plugins) and the deployed check (`deployed`, `extra_plugins`, `extra_mods`, `missing_plugins`, `plugins_txt_matches`). `null` for other sources. |
| `patches` | Merged patches: a list of `{patch, manifest, state, level, rebuild, reasons, checked, result, findings}` (`state` is `up to date`, `rebuild`, `not enabled` or `cannot check`; `smc_state` is sf-mod-check's own word), `[]` when no manifest was found, or `{"not_checked": why}`. |
| `rules` | The Vortex rules check: `has_rules`, `pairs` (each `{mods, paths, same, differ, unknown, winner, deployed, deployed_from, rule, overrides, sample}`; `deployed_from` is `record`, `data` or null), `no_shared`, `unmatched` and `findings` (counts). |
| `candidate` | `candidate` runs: `zips` (each with `path`, `format` (0.2.3: `zip`, `7z` or `rar`), `files`, `loose`, `archives`, `plugins`, `fomod` and `not_compared`), `plugins`, `masters`, `depth`, `replaced`, `mods_checked`, `build_paths`, `shared`, `findings` (counts by level; the findings themselves are in the top-level `findings`, section `candidate`), `heavy` and `records` (0.2.3; `null` at `--depth light`: `status` `done`, `skipped` or `failed`, `why`, `program`, `exit`, `result_line`, `file` (the conflicts.json), `shared_records`, `not_loaded` (when the check ran: the build's plugins sf-mod-check didn't load), and `mods`, each with `mod`, `plugin`, `records`, `forwardable`, `choice`, `no_effect`, `build_wins`, `build_loses` (how many of the records the build wins, and how many the installed mod wins) and up to five `examples` with `fk`, `edid` and `type`). `null` in a normal run. |

New in 0.3.0 (nothing was renamed or removed; a normal run's file is the same as before except `load_order.plugins[].master`):

| Key | Meaning |
|---|---|
| `sort` | `sort` runs only: `tie_break`, `order` (plugin names), `moved`, `moves` (each `plugin`, `after`, `reason`, `source`), `score_current`, `score_sorted`, `estimate`, `rated`, `same_as_current`, `sources` (rules used, by source: `game`, `mod`, `picks`, `user`, `patch`, `community`, `loot_userlist`, `loot_masterlist`, `loot_groups`, `vortex`), `rejected` (each rule left out, with the `why` and the `path` of rules that beat it), `inactive_rules`, `requires_missing`, `incompatible`, `proposed`, `stale`, `vortex_broken`, `loot_files`, `decided`, `loop` and `files` (by role). Its section is `sections.sort`; the findings have section `sort`. |

The "files its records use aren't installed" findings count the files by kind (script, model, mesh, material,
texture, sound) in their text, and their `data` has a `kinds` count.

Readers should ignore keys they don't know: new keys can appear in any version without a schema change.

## The log

Add `--verbose` to see what the tool read, what it decided and why (lines like `21:30:05 [archives] ...` on stderr),
or `--log <file>` to write the same lines to a file. The file is replaced on each run. Both can be given together.
Send the log when you report a problem. If a check fails with an unexpected error, the report marks that section
"failed", the other checks still run, and the log holds the full error.

The log names the folders it read, which include your user name, your Steam library and your mod folders. Read it
before you post it.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The report was written (whatever it found), or `--help` or `--version` printed what it shows. |
| 1 | The report was written, `--strict` was given, and it lists at least one problem. |
| 2 | It couldn't run: a bad option, no Python found by the launcher, no load order found, a folder it can't write to, or no report file could be written. The `RESULT: CANNOT CHECK` line says why and what to do. |

## Traps

- **A plugin's kind can change in an update.** Full to light is common. Every patch for that plugin then has to be
  rebuilt, and nothing warns you. Keep the snapshot on (don't use `--no-state`) and run sf-load-order after each
  update, so the "changed since the last run" check can see it.
- **Vortex keeps the archive's file date.** When Vortex installs an update, the plugin keeps the date stored in the
  mod's download, which can be older than the file it replaced. So a date alone doesn't show which copy is newer:
  sf-load-order compares size and date together.
- **Plugins.txt is what the game reads.** Vortex writes the game's Plugins.txt when it deploys. MO2 keeps its own
  `plugins.txt` in the profile, rewrites it whenever you change the plugin list, and shows it to the game in place
  of the real file when you start the game from MO2. A Vortex export made before you deployed, or an old MO2
  profile, can differ from what the game loads. Export again
  after each change, and don't copy a plugins.txt into the game folder while a mod manager manages it (it rewrites
  the file).
- **MO2's view also holds one folder this tool doesn't read.** MO2 builds the Data folder it shows the game from
  the real Data folder, each enabled mod and Overwrite, and also the real `Documents\My Games\Starfield\Data`
  folder. sf-load-order doesn't read that last one (most setups keep nothing in it), so a file kept only there is
  missing from its loose-file and record checks.
- **MO2 record checks need sf-mod-check started from MO2.** MO2 keeps each mod in its own folder and shows them to
  the programs it starts through a virtual Data folder. Run from outside MO2, sf-mod-check can't see your mods, and
  the record section says so. Either add sf-load-order (`sf-load-order.cmd`) as an executable in MO2 and start it
  from there, or run `sf-mod-check conflict` from MO2 yourself and give its `conflicts.json` with `--conflicts`.
- **A Vortex staging folder copied off Windows.** Windows drops trailing spaces and dots from folder names, so on
  Windows a mod id like `Mod9.` finds the folder `Mod9`. On Linux and macOS, `Mod9.` and `Mod9` can be two
  different mods, so sf-load-order doesn't make that match there. If you copied your staging folder from Windows,
  run sf-load-order on Windows, or such a mod shows no folder and its loose files aren't checked.
- **LOOT's regular-expression names are checked before use.** A pattern over 256 characters, a back-reference, a
  look-behind or a repeat of a group that holds a repeat (`(a+)+`) is refused with one caution, because it could make
  the match run for hours. A masterlist's real patterns all pass. Our own rules files take no pattern at all.
- **A mod's ship change can't beat a BlueprintShips file.** The game loads the official `BlueprintShips-*.esm` files
  after every mod, so moving a mod lower or adding a patch doesn't bring its change to those ships back. Only an
  update of the mod made for that file does. The report says so instead of suggesting a patch (see
  [Record conflicts](#record-conflicts-sf-mod-check)).

## Similar tools

- **LOOT** (https://loot.github.io) sorts plugins by rules and its community masterlist. `sf-load-order sort` builds
  an order from the game's rules, your own rules file and, when it is there, LOOT's masterlist, keeps your current
  order wherever it can, and explains every move; it needs no LOOT install. LOOT's masterlist is far larger than
  anything here (there is no community rules file yet), so use both if you like: sort with LOOT, then score LOOT's
  order here with `--loot-order`.
- **xEdit** (SF1Edit) shows every record conflict in full and can build patches. sf-load-order shows the same kind of
  conflict as a summary with a suggested order, and points you to xEdit (or a patch) for the ones that need a merge.
- **Vortex's and MO2's conflict views** show loose files that overwrite each other. Vortex's view doesn't look
  inside archives; sf-load-order covers archives, loose files and records in one report.
- **SLOK** (https://www.nexusmods.com/starfield/mods/15786) keeps Plugins.txt from being changed behind your back.
  sf-load-order doesn't protect the file: it tells you whether the order in it works.

## Found a mistake?

Open an issue: https://github.com/taosecurity/sf-load-order/issues. Attach the log (see [The log](#the-log)) if you
can.

## Credits

- **LOOT's masterlist** (https://github.com/loot/starfield) is dedicated to the public domain under CC0 1.0 by the
  LOOT team and its contributors. sf-load-order reads the copy LOOT keeps on your PC and never ships one.
- Record conflicts come from **sf-mod-check** (https://github.com/taosecurity/sf-mod-check), which reads plugins with
  **Mutagen** (https://github.com/Mutagen-Modding/Mutagen).
- Starfield belongs to Bethesda Softworks. sf-load-order ships no game files.

Made by Richard Bejtlich. Source and issues: https://github.com/taosecurity/sf-load-order.

## License

GPL-3.0 (see [LICENSE](LICENSE)). Changes are in [CHANGELOG.md](CHANGELOG.md).
