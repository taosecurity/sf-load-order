# Changelog

## 0.1.0 - 2026-11-19

First public release.

- Reads a load order from the game's Plugins.txt, a Mod Organizer 2 profile (`--mo2`, `--profile`) or a file
  exported from Vortex's Load Order page (`--vortex-loadorder`, `--vortex-staging`, plus Vortex's deployment record
  for mods with no plugin). Finds the Data folder on Steam (every library) and Game Pass (`XboxGames` and
  `.GamingRoot`), or takes `--data`.
- Order check: missing, turned-off, dropped and late masters, unreadable plugins, plugin counts per kind (full,
  medium, light) with a caution near each limit, and BlueprintShips files placed last as the game does.
- Patches built for an older master: a header-only FormID scan, and a snapshot (`--state`, `--no-state`) that
  catches a master changing kind since the last run. Records a plugin numbers past its master list are counted as
  its own (as the game reads them), not as a problem.
- Known clashes: LOOT's masterlist and userlist from the player's own LOOT install (requirements, incompatibilities,
  warnings, cleaning), plus the player's `--clashes`, `--accept` (pinned to SHA-256) and `--picks` files.
- Archives: the game's exact archive names, archives that never load, shared paths with advice, and content
  comparison (stored, zlib, texture pixel data, and LZ4 with the optional `lz4` package).
- Loose files: mods shipping the same file (MO2 priority; Vortex's deployed copy), loose files hiding archived
  ones, and stray files in Data.
- Record conflicts through sf-mod-check 0.8.0 or newer (`--depth medium`, the default; `--depth heavy` adds a check
  of each plugin), or from a `--conflicts` file: lost changes, per-plugin ratings and pair advice.
- Suggested order: scored by the changes an order throws away, with the near-tie rule (a saving under 10 points or
  3% keeps your order), estimates marked as such, and a comparison with a LOOT-sorted order (`--loot-order`).
  Writes `plugins.txt`, `vortex-loadorder-recommended.json` (Vortex) and `HOW-TO-APPLY.txt`.
- Report in text, HTML (self-contained, light and dark) and JSON (schema 1).
- For MO2, the files to apply are `plugins.txt` and `loadorder.txt` (MO2 takes the order from loadorder.txt).
- `--verbose` and `--log <file>` write a troubleshooting log. `--strict` exits 1 when there is a problem.
- Every run prints a byline on stderr (left out with `--quiet`); `--version` shows the byline and credits; the
  reports carry the byline, and the HTML report has a footer with the license, source and issues links.
- Runs on Linux with Python 3.10 or newer for checking a copy of a load order (paths given with options). Folder
  and file lookups ignore letter case, and the snapshot folder falls back to `~/.local/state/sf-load-order`.
