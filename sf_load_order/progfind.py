"""Finds a program by name the safe way: only in the folders PATH names, never in the current folder.

shutil.which on Windows also searches the current folder first, so a planted sf-mod-check.exe or 7z.exe in a
folder the user runs sf-load-order from would be started. which() walks PATH itself and skips empty, "." and any
other relative entry. A configured or full path is the caller's own business (it is checked with os.path.isfile).
"""
import os

from . import log


def _exts(name):
    if os.name != "nt":
        return [""]
    pathext = [e.lower() for e in (os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(";") if e]
    if os.path.splitext(name)[1].lower() in pathext:
        return [""]
    return pathext


def which(name, path=None):
    """The full path of program `name` found in an absolute PATH folder, else None. path: a PATH string (tests)."""
    if os.path.dirname(name) or os.path.isabs(name):
        return None  # not a bare program name: callers check such a path themselves
    raw = os.environ.get("PATH", "") if path is None else path
    for folder in raw.split(os.pathsep):
        folder = folder.strip().strip('"')
        if not folder or not os.path.isabs(folder):
            continue  # empty, "." and relative entries mean the current folder: never searched
        for ext in _exts(name):
            cand = os.path.join(folder, name + ext)
            if os.path.isfile(cand) and (os.name == "nt" or os.access(cand, os.X_OK)):
                return os.path.abspath(cand)
    return None


def given_path(path):
    """A program path the user gave (it has a folder part): the path itself if it is a file, else on Windows the
    path plus the first PATHEXT extension that is a file (C:\\tools\\sf-mod-check finds sf-mod-check.exe, as
    shutil.which did). None when nothing matches."""
    if os.path.isfile(path):
        return os.path.abspath(path)
    for ext in _exts(path):
        if ext and os.path.isfile(path + ext):
            return os.path.abspath(path + ext)
    return None


def system_tool(name):
    """A Windows system program (taskkill) by its full path under %SystemRoot%\\System32."""
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    path = os.path.join(root, "System32", name)
    log.info("progfind", f"using {path}")
    return path
