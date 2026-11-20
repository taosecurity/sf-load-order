# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Richard Bejtlich
"""Build the release zip and its SHA-256 checksum file.

  python -I build_release.py                 build dist/sf-load-order-<version>.zip (+ .sha256)
  python -I build_release.py --tag v0.1.0    the same, but first check that the tag matches
                                             sf_load_order.__version__ and that CHANGELOG.md has a dated entry for it
  python -I build_release.py --allow-dirty   build from the files on disk, uncommitted changes included (to try it)

Also writes build/release-notes.md (the CHANGELOG entry and the checksum), used as the GitHub release text.

The zip holds one folder, sf-load-order-<version>/, with every committed file except the GitHub workflows and the
git settings (.github/, .gitignore, .gitattributes). The tests stay in: they use only made-up data and let anyone
check the tool on their own PC. The files come from the commit (git cat-file --filters HEAD:<file>, which applies
.gitattributes: LF everywhere, CRLF for the .cmd launcher), so uncommitted edits never reach a zip and the line
endings don't depend on the system. A working tree with changes or new files is refused unless --allow-dirty is
given; --allow-dirty reads the files on disk (new files included) and applies the same line-ending rules.

Every entry has a fixed time, the same "made on" system (Unix), mode 0644 and no compression (stored), so the same
commit gives the same zip, byte for byte, on Windows and Linux. CI builds it on both and compares the checksums.
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "sf-load-order"
LEAVE_OUT = (".github/", ".gitignore", ".gitattributes")
# Never shipped, wherever this runs (the maintainer's private notes and helper; they never reach the public repo).
PRIVATE_FILES = {"NOTES.md", "WORKPLAN.md", "sync_public.py"}
PRIVATE_DIRS = ("realdata/", "private/")
FIXED_TIME = (2026, 1, 1, 0, 0, 0)
CRLF_SUFFIXES = (".cmd", ".bat")


def git(*args):
    try:
        return subprocess.run(["git", "-C", HERE, *args], capture_output=True, check=True).stdout
    except FileNotFoundError:
        raise SystemExit("git wasn't found. Fix: install git (https://git-scm.com) and run this from a clone.")
    except subprocess.CalledProcessError as e:
        msg = e.stderr.decode("utf-8", "replace").strip()
        raise SystemExit(f"git {' '.join(args[:2])} failed: {msg}. Fix: run this from a clone of the repo.")


def _split(out):
    return [n for n in out.decode("utf-8").split("\0") if n]


def shipped(rel):
    if rel.startswith(LEAVE_OUT) or rel.startswith(PRIVATE_DIRS):
        return False
    return rel.rsplit("/", 1)[-1] not in PRIVATE_FILES


def shipped_files(include_untracked=False):
    names = _split(git("ls-files", "-z"))
    if include_untracked:
        names += _split(git("ls-files", "-z", "--others", "--exclude-standard"))
    return sorted(n for n in set(names) if shipped(n) and (not include_untracked or os.path.isfile(os.path.join(HERE, n))))


def dirty_files():
    out = git("status", "--porcelain").decode("utf-8", "replace")
    return [line[3:] for line in out.splitlines() if line.strip()]


def normalize(rel, data):
    """The .gitattributes rules for a file read from disk: text gets LF, the .cmd launcher CRLF."""
    if b"\0" in data:
        return data
    data = data.replace(b"\r\n", b"\n")
    if rel.lower().endswith(CRLF_SUFFIXES):
        data = data.replace(b"\n", b"\r\n")
    return data


def package_version():
    path = os.path.join(HERE, "sf_load_order", "__init__.py")
    with open(path, encoding="utf-8") as f:
        m = re.search(r'^__version__ = "([^"]+)"', f.read(), re.M)
    if not m:
        raise SystemExit(f"__version__ not found in {path}")
    return m.group(1)


def changelog_entry(version):
    """(heading rest, body) of the CHANGELOG entry for this version ("## 0.1.0 - 2026-11-19")."""
    with open(os.path.join(HERE, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read().replace("\r\n", "\n")
    m = re.search(rf"^## {re.escape(version)}(?=[ \t]|$)([^\n]*)\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2).strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the sf-load-order release zip and its checksum.")
    ap.add_argument("--tag", help="the git tag being released, e.g. v0.1.0")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="build from the files on disk, uncommitted changes included (never for a release)")
    args = ap.parse_args(argv)
    if args.allow_dirty and args.tag:
        raise SystemExit("--allow-dirty can't be used with --tag: a release is built from the commit")
    if not args.allow_dirty:
        dirty = dirty_files()
        if dirty:
            raise SystemExit("these files have uncommitted changes or aren't committed yet: " + ", ".join(dirty) +
                             ". Commit them first (or use --allow-dirty to try a build).")

    version = package_version()
    heading, notes = changelog_entry(version)
    if args.tag is not None:
        if args.tag != "v" + version:
            raise SystemExit(f"tag {args.tag} doesn't match sf_load_order.__version__ {version} (expected v{version})")
        if heading is None:
            raise SystemExit(f"CHANGELOG.md has no entry for {version}")
        if "not yet released" in heading.lower() or not re.search(r"\d{4}-\d\d-\d\d", heading):
            raise SystemExit(f"CHANGELOG.md entry for {version} needs a release date, e.g. '## {version} - 2026-11-19'")
    if notes is None:
        notes = f"sf-load-order {version} (no CHANGELOG entry for this version)."

    dist = os.path.join(HERE, "dist")
    build = os.path.join(HERE, "build")
    os.makedirs(dist, exist_ok=True)
    os.makedirs(build, exist_ok=True)
    top = f"{NAME}-{version}"
    zpath = os.path.join(dist, top + ".zip")
    files = shipped_files(include_untracked=args.allow_dirty)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for rel in files:
            if args.allow_dirty:
                with open(os.path.join(HERE, rel), "rb") as f:
                    data = normalize(rel, f.read())
            else:
                data = git("cat-file", "--filters", f"HEAD:{rel}")
            info = zipfile.ZipInfo(f"{top}/{rel}", FIXED_TIME)
            info.create_system = 3   # Unix, on every OS (Python sets 0 on Windows, which changes the zip's bytes)
            info.compress_type = zipfile.ZIP_STORED   # no zlib: its output can differ between systems
            info.external_attr = 0o100644 << 16       # a regular file, rw-r--r--
            z.writestr(info, data)
    with open(zpath, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(zpath + ".sha256", "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{digest}  {os.path.basename(zpath)}\n")
    with open(os.path.join(build, "release-notes.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(notes + "\n\n" + f"SHA-256 of `{os.path.basename(zpath)}`: `{digest}`\n")

    print(f"{len(files)} files")
    print(zpath)
    print(f"SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
