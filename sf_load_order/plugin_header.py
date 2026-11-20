"""Read a Starfield plugin's header: its kind (F full, M medium, L light) and its masters.

The game's kind rule: the light flag (0x100) wins over the medium flag (0x400); neither = full.
Subrecords are a 4-byte signature, a 16-bit size and the data. An XXXX subrecord (size 4) holds the real 32-bit size
of the subrecord after it, whose own 16-bit size is then 0; a long ONAM list in a big plugin's header uses it.
"""
import struct

from . import log

FLAG_LIGHT = 0x100
FLAG_MEDIUM = 0x400
MAX_HEADER = 1 << 20  # a TES4 header is a few KB; read at most 1 MB


def _name(raw):
    """A master's file name: UTF-8 when it is valid UTF-8, else Windows-1252 (bytes it lacks become U+FFFD)."""
    raw = raw.rstrip(b"\0")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def masters_of(data):
    """[master names] from a TES4 record's data (its subrecords)."""
    masters, j, big = [], 0, None
    while j + 6 <= len(data):
        sig, ln = data[j:j + 4], struct.unpack_from("<H", data, j + 4)[0]
        if big is not None:  # the subrecord after XXXX: its real size is XXXX's value
            ln, big = big, None
        if j + 6 + ln > len(data):  # cut off (or a size past the end): nothing after this can be read
            break
        if sig == b"XXXX" and ln == 4 and j + 10 <= len(data):
            big = struct.unpack_from("<I", data, j + 6)[0]
        elif sig == b"MAST":
            masters.append(_name(data[j + 6:j + 6 + ln]))
        j += 6 + ln
    return masters


def header(path):
    """Return (kind, [masters]). (None, None) when the file is missing or can't be opened; ("?", []) when it isn't
    a plugin (no TES4 record) or its header can't be read. Never raises."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
            if len(head) < 24 or head[:4] != b"TES4":
                return "?", []
            size = struct.unpack_from("<I", head, 4)[0]
            flags = struct.unpack_from("<I", head, 8)[0]
            data = f.read(min(size, MAX_HEADER))
    except (OSError, ValueError):  # ValueError: a path the system can't open (a NUL in it)
        return None, None
    try:
        masters = masters_of(data)
    except Exception as e:  # a header this can't read must never stop the run
        log.info("inputs", f"can't read the header of {path}: {type(e).__name__}: {e}")
        return "?", []
    kind = "L" if flags & FLAG_LIGHT else "M" if flags & FLAG_MEDIUM else "F"
    return kind, masters


def fill(load_order):
    """Read kind and masters for every plugin in a LoadOrder that has a path and hasn't been read yet. Never raises:
    a plugin whose header can't be read gets kind "?" and no masters."""
    for p in load_order.plugins:
        if p.path and p.kind is None:
            try:
                p.kind, p.masters = header(p.path)
            except Exception as e:  # header() doesn't raise; this guards the run against anything it missed
                log.info("inputs", f"can't read the header of {p.path}: {type(e).__name__}: {e}")
                p.kind, p.masters = "?", []
