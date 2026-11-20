"""Read a Starfield plugin's header: its kind (F full, M medium, L light) and its masters. Master-only file.

The game's kind rule: the light flag (0x100) wins over the medium flag (0x400); neither = full.
"""
import struct

FLAG_LIGHT = 0x100
FLAG_MEDIUM = 0x400
MAX_HEADER = 1 << 20  # a TES4 header is a few KB; read at most 1 MB


def header(path):
    """Return (kind, [masters]). (None, None) when the file is missing or can't be opened; ("?", []) when it isn't
    a plugin (no TES4 record)."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
            if len(head) < 24 or head[:4] != b"TES4":
                return "?", []
            size = struct.unpack_from("<I", head, 4)[0]
            flags = struct.unpack_from("<I", head, 8)[0]
            data = f.read(min(size, MAX_HEADER))
    except OSError:
        return None, None
    masters, j = [], 0
    while j + 6 <= len(data):
        sig, ln = data[j:j + 4], struct.unpack_from("<H", data, j + 4)[0]
        if sig == b"MAST":
            masters.append(data[j + 6:j + 6 + ln].rstrip(b"\0").decode("cp1252"))
        j += 6 + ln
    kind = "L" if flags & FLAG_LIGHT else "M" if flags & FLAG_MEDIUM else "F"
    return kind, masters


def fill(load_order):
    """Read kind and masters for every plugin in a LoadOrder that has a path and hasn't been read yet."""
    for p in load_order.plugins:
        if p.path and p.kind is None:
            p.kind, p.masters = header(p.path)
