# Privacy

sf-load-order runs on your own computer, offline. It collects no data and sends nothing anywhere.

## What it does not do

- It makes no network connections.
- It has no telemetry, analytics, crash reporting or update checks.
- It uploads nothing. The report's only link is to the project page, and nothing is fetched from it.
- It never changes the game, Plugins.txt, Vortex or Mod Organizer 2. Applying a suggested order is your own step.

## What it reads

- The load order you point it at: the game's `%LOCALAPPDATA%\Starfield\Plugins.txt`, an MO2 instance's
  `ModOrganizer.ini` and profile files, or a Vortex Load Order export and Vortex's staging folder.
- The game's Data folder: plugin headers and records, archive (.ba2) indexes and the archived files it compares,
  loose files, and Vortex's deployment record (`vortex.deployment.*.json`) when there is one.
- To find the Data folder without `--data`: Steam's location from the Windows registry, Steam's
  `libraryfolders.vdf`, and whether `<drive>:\XboxGames\Starfield\Content\Data` and each drive's `.GamingRoot`
  exist.
- LOOT's masterlist and userlist on your PC, and any `--clashes`, `--accept`, `--picks`, `--loot-order` or
  `--conflicts` file you give.

## What it runs

- sf-mod-check, only when it is found on PATH or given with `--sf-mod-check`, and not when you pass `--conflicts`
  or `--depth light`. sf-mod-check is a separate program with its own privacy notes; it also works offline.

## What it writes

- The report folder (`--out`, default `sf-load-order-report`): the reports, the files to apply, and sf-mod-check's
  input and output in `work\`.
- The snapshot and cache in the state folder (`--state`, default `%LOCALAPPDATA%\sf-load-order`). `--no-state`
  writes nothing there.
- The log file, only when you give `--log`.

Nothing else.

## What the output contains

The reports and the log show your plugin and mod names and the paths of the folders read, which include your
Windows user name, your Steam library and your mod manager folders. The snapshot holds plugin names, kinds, sizes and
dates. Read a report or log before you post it.

## Downloading it

Downloading sf-load-order from GitHub is covered by GitHub's own
[privacy statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement). Its
author receives only the public download counts GitHub shows.

## Questions

Open an issue at https://github.com/taosecurity/sf-load-order/issues.
