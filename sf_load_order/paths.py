"""Where things are on a player's PC: the game's Data folder, Plugins.txt and Vortex's staging folder.

Nothing here is wired to one PC. Every function takes the environment (a dict like os.environ), the drive roots to
search and the registry reader as parameters, so tests can point them at temp folders.

Data folder search order (find_data):
  1. --data, when given
  2. a source's own hint (MO2's gamePath), when it is a folder
  3. Steam: the Steam folder from the registry (optional), %ProgramFiles(x86)%\\Steam, and every library listed in
     each one's steamapps\\libraryfolders.vdf, as <library>\\steamapps\\common\\Starfield\\Data
  4. Game Pass: <drive>:\\XboxGames\\Starfield\\Content\\Data on every drive, plus the folders named in each drive's
     .GamingRoot file (the Xbox app's list of game folders on that drive)
"""
import json
import os
import string

from . import log

STEAM_GAME = os.path.join("steamapps", "common", "Starfield", "Data")
GAMEPASS_GAME = os.path.join("Starfield", "Content", "Data")


def _env(env):
    return os.environ if env is None else env


def default_plugins_txt(env=None):
    """%LOCALAPPDATA%\\Starfield\\Plugins.txt (the file the game reads, Steam and Game Pass alike), or None when
    LOCALAPPDATA isn't set (Linux, macOS): there is no default there, and a path relative to the current folder
    would be a wrong guess."""
    base = _env(env).get("LOCALAPPDATA")
    return os.path.join(base, "Starfield", "Plugins.txt") if base else None


def default_vortex_staging(env=None):
    """%APPDATA%\\Vortex\\starfield\\mods (Vortex's default staging folder for Starfield), or None when APPDATA
    isn't set (Linux, macOS)."""
    base = _env(env).get("APPDATA")
    return os.path.join(base, "Vortex", "starfield", "mods") if base else None


VORTEX_EXPORT_SETTINGS = "modlist-backup-auto-export.json"


def vortex_export_settings(env=None):
    """%APPDATA%\\Vortex\\modlist-backup-auto-export.json, the settings file of the Vortex extension "Modlist Backup"
    (its Automatic Export), or None when APPDATA isn't set."""
    base = _env(env).get("APPDATA")
    return os.path.join(base, "Vortex", VORTEX_EXPORT_SETTINGS) if base else None


def default_vortex_exports(env=None):
    """(folders, why): the export folder named in the extension's settings file ("folder"), then its "old"
    subfolder, each only when it is a folder. folders is [] when there is no settings file, it names no folder, or
    the folder is gone; why says which, for the error message."""
    path = vortex_export_settings(env)
    if not path:
        return [], "APPDATA isn't set, so the Vortex extension's settings file can't be found"
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except OSError:
        return [], f"there is no {path} (the Vortex extension's Automatic Export settings)"
    except (ValueError, UnicodeDecodeError):
        return [], f"{path} isn't valid JSON"
    folder = d.get("folder") if isinstance(d, dict) else None
    if not isinstance(folder, str) or not folder.strip():
        return [], f"{path} names no export folder"
    folder = folder.strip()
    if not os.path.isdir(folder):
        return [], f"the export folder {folder} named in {path} doesn't exist"
    out = [folder]
    old = os.path.join(folder, "old")
    if os.path.isdir(old):
        out.append(old)
    return out, f"from {path}"


def safe_component(name):
    """True when name (a Vortex mod id, an MO2 mod or profile name) is one folder name: a string with no folder
    separator, no drive (":"), no NUL or other control character, and not made only of dots and spaces. On Windows
    it also may not end in a dot or a space or be a device name (CON, NUL, COM1 and so on). Anything else could
    point outside the folder it is joined to."""
    if not isinstance(name, str) or not name or any(c in name for c in "/\\:") or any(ord(c) < 32 for c in name):
        return False
    if name.strip(" .") == "":  # ".", "..", "...", ".. ": Windows drops trailing dots and spaces, so these act like ".."
        return False
    if os.name == "nt":
        # On Windows a trailing dot or space is dropped too ("Mod." is "Mod"), and device names (CON, NUL, COM1...)
        # aren't folders. Elsewhere such names are real folder names, so they stay allowed.
        # Windows 11 reserves only the bare device name ("Aux.Lighting" is a real folder there); Windows 10 and older
        # also treat a device name plus an extension ("NUL.x", "Aux.Lighting") as the device.
        # COM and LPT with a superscript 1, 2 or 3 ("COM¹") are devices before Windows 11 only: Windows 11 makes
        # them as plain folders (checked on build 26300, 2026-11-19 and 2026-11-20).
        win11 = _windows_11_or_later()
        stem = name if win11 else name.split(".")[0].rstrip(" ")
        devices = WINDOWS_DEVICES if win11 else WINDOWS_DEVICES | SUPERSCRIPT_DEVICES
        if name != name.rstrip(" .") or stem.upper() in devices:
            return False
    return True


def _windows_11_or_later():
    try:
        import sys
        return sys.getwindowsversion().build >= 22000
    except (AttributeError, OSError):
        return False


WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                   *(f"{d}{i}" for d in ("COM", "LPT") for i in "123456789")}  # COM0/LPT0 are plain names
SUPERSCRIPT_DEVICES = {f"{d}{i}" for d in ("COM", "LPT") for i in "¹²³"}  # devices before Windows 11 only


def mod_folder(staging, raw_id):
    """A Vortex mod's staging folder for a raw mod id; None if none exists (0.1.1). Tried in this order: the exact
    name (it may start with a space), then on Windows only the name as Windows stores it (trailing spaces and dots
    dropped), then the fully trimmed name. Elsewhere "Mod9." and "Mod9" can be two mods' folders, so the dot form
    isn't tried there (a staging folder copied from Windows may then miss such a mod: README, Limits)."""
    if not staging or not raw_id:
        return None
    tries = (raw_id, raw_id.rstrip(" ."), raw_id.strip()) if os.name == "nt" else (raw_id, raw_id.strip())
    for name in dict.fromkeys(tries):
        folder = child_folder(staging, name)
        if folder:
            return folder
    return None


def child_folder(folder, name):
    """folder\\name as it is on disk, matching name in any case (Linux and macOS file systems are case-sensitive,
    and a copied Vortex staging folder or deployment record may spell a mod id differently). None when there is no
    such folder."""
    if not folder or not name or not safe_component(name):
        return None
    exact = os.path.join(folder, name)
    if os.path.isdir(exact):
        return exact
    try:
        low = name.lower()
        hit = next((n for n in os.listdir(folder) if n.lower() == low), None)
    except OSError:
        return None
    return os.path.join(folder, hit) if hit and os.path.isdir(os.path.join(folder, hit)) else None


def default_state(env=None):
    """Where sf-load-order keeps its snapshot and cache when --state isn't given: %LOCALAPPDATA%\\sf-load-order on
    Windows (~\\AppData\\Local\\sf-load-order if LOCALAPPDATA isn't set), else $XDG_STATE_HOME/sf-load-order or
    ~/.local/state/sf-load-order (Linux, macOS)."""
    env = _env(env)
    if env.get("LOCALAPPDATA"):
        return os.path.join(env["LOCALAPPDATA"], "sf-load-order")
    home = os.path.expanduser("~")
    if os.name == "nt":
        return os.path.join(home, "AppData", "Local", "sf-load-order")
    return os.path.join(env.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state"), "sf-load-order")


# ---------- Steam ----------
def _registry_steam():
    """Steam's folder from HKCU\\Software\\Valve\\Steam SteamPath, or None (no Windows, no Steam, no access)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return os.path.normpath(winreg.QueryValueEx(k, "SteamPath")[0])
    except (ImportError, OSError):
        return None


def _vdf_tokens(text):
    """Quoted strings (with \\\\ and \\" escapes) and braces from a Valve KeyValues (.vdf) file."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "{}":
            yield c
            i += 1
        elif c == '"':
            i += 1
            out = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                out.append(text[i])
                i += 1
            i += 1
            yield ("s", "".join(out))
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        else:
            i += 1


def vdf_library_paths(path):
    """Library folders from a libraryfolders.vdf: every "path" value, plus the old format's numbered entries
    ("1" "D:\\\\SteamLibrary"). [] when the file can't be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        log.info("inputs", f"no Steam library list at {path}: {e.strerror}")
        return []
    out, stack, key = [], [], None
    for t in _vdf_tokens(text):
        if t == "{":
            stack.append(key)
            key = None
        elif t == "}":
            if stack:
                stack.pop()
            key = None
        elif key is None:
            key = t[1]
        else:
            parent = (stack[-1] or "").lower() if stack else ""
            if key.lower() == "path" or (parent == "libraryfolders" and key.isdigit()):
                out.append(os.path.normpath(t[1]))
            key = None
    return out


def steam_libraries(env=None, registry=None):
    """Steam library folders, the Steam folder itself first. registry: a function returning Steam's folder or None
    (default: read the Windows registry; pass lambda: None to skip it)."""
    env = _env(env)
    registry = _registry_steam if registry is None else registry
    roots = []
    reg = registry()
    if reg:
        roots.append(reg)
    pf = env.get("ProgramFiles(x86)") or env.get("PROGRAMFILES(X86)") or (r"C:\Program Files (x86)"
                                                                           if os.name == "nt" else None)
    if pf:  # off Windows there is no Program Files: only the registry (none there) and the given env count
        roots.append(os.path.join(pf, "Steam"))
    libs = []
    for r in roots:
        for p in [r] + vdf_library_paths(os.path.join(r, "steamapps", "libraryfolders.vdf")):
            if os.path.normcase(p) not in (os.path.normcase(x) for x in libs):
                libs.append(p)
    return libs


# ---------- Game Pass ----------
def drive_roots():
    """Every drive root that exists on this PC (C:\\, D:\\, ...). [] off Windows."""
    if os.name != "nt":
        return []
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]


def gaming_root_folders(root):
    """Folder names from <root>\\.GamingRoot: 'RGBX', a 4-byte count, then UTF-16 names each ending in a zero
    character. [] when the file is missing or unreadable."""
    try:
        with open(os.path.join(root, ".GamingRoot"), "rb") as f:
            b = f.read(64 * 1024)
    except OSError:
        return []
    if len(b) <= 8 or b[:4] != b"RGBX":
        return []
    names = b[8:].decode("utf-16-le", errors="replace").split("\0")
    return [n.strip().lstrip("\\/") for n in names if n.strip().lstrip("\\/")]


def gamepass_candidates(drives=None):
    """<drive>\\XboxGames\\Starfield\\Content\\Data and <drive>\\<.GamingRoot folder>\\Starfield\\Content\\Data."""
    out = []
    for root in drive_roots() if drives is None else drives:
        folders = ["XboxGames"]
        for f in gaming_root_folders(root):
            if f.lower() not in (x.lower() for x in folders):
                folders.append(f)
        out += [os.path.join(root, f, GAMEPASS_GAME) for f in folders]
    return out


# ---------- the Data folder ----------
def find_data(given=None, hint=None, hint_why=None, env=None, drives=None, registry=None):
    """(Data folder, why, tried). Data folder is None when none was found; an explicit --data that isn't a folder
    is the caller's error to report (why says so)."""
    if given:
        if os.path.isdir(given):
            return given, "given with --data", [given]
        return None, f"--data {given} is not a folder", [given]
    tried = []
    if hint:
        tried.append(hint)
        if os.path.isdir(hint):
            return hint, hint_why, tried
        log.info("inputs", f"{hint_why} {hint} is not a folder; searching")
    for lib in steam_libraries(env, registry):
        p = os.path.join(lib, STEAM_GAME)
        tried.append(p)
        if os.path.isdir(p):
            return p, "Steam install", tried
    for p in gamepass_candidates(drives):
        tried.append(p)
        if os.path.isdir(p):
            return p, "Game Pass install", tried
    return None, "no Data folder found", tried
