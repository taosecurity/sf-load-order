"""Synthetic test data: tiny Starfield plugins, BA2 archives, fake game, MO2 and Vortex folders, fake conflicts.json.

Nothing here comes from the game or from anyone's mods: every file is built byte by byte from the format rules, so
the tests can be published. A test that needs a shape these don't make builds it the same way, from the format.

Plugins:  plugin(path, masters=(), kind="F", records=())   records: [(signature, raw FormID, editor ID)]
Archives: ba2(path, files, version=2, kind=b"GNRL")         files: {name: bytes | ("zlib"|"lz4", content, ...)}
Folders:  game(root, plugins)            -> Data folder with the given plugins, and a Plugins.txt
          mo2(root, profile, mods)       -> an MO2 instance with profiles\\<profile> and mods\\<mod>
          vortex_loadorder(path, entries)-> a Vortex Load Order page export
          conflicts_json(path, ...)      -> a conflicts.json shaped like sf-mod-check's
"""
import json
import os
import struct
import zlib

# Plugin header flags (TES4 record flags)
FLAG_MASTER = 0x1
FLAG_LIGHT = 0x100
FLAG_MEDIUM = 0x400
FLAG_PARTIAL = 0x4000  # a record flag, not a header flag: the record holds only the fields it changes

HEADER_SIZE = {1: 24, 2: 32, 3: 36}  # BA2 header size by version (v2 adds 8 bytes, v3 12)


def _sub(sig, payload):
    return sig + struct.pack("<H", len(payload)) + payload


def _zstr(s):
    return s.encode("cp1252") + b"\0"


def _record(sig, fid, payload, flags=0):
    return sig + struct.pack("<IIIIHH", len(payload), flags, fid, 0, 0, 0) + payload


def _group(label, body):
    # group header: "GRUP", total size including the 24-byte header, label, type 0 (top), stamp, version
    return b"GRUP" + struct.pack("<I", 24 + len(body)) + label + struct.pack("<iIHH", 0, 0, 0, 0) + body


def plugin(path, masters=(), kind="F", records=(), author="fixture"):
    """Write a plugin. kind: "F" full, "M" medium, "L" light. records: [(sig, raw FormID, editor ID[, flags])],
    grouped by signature in top groups like the Creation Kit writes them. Returns path."""
    flags = FLAG_MASTER | {"F": 0, "M": FLAG_MEDIUM, "L": FLAG_LIGHT}[kind]
    hedr = _sub(b"HEDR", struct.pack("<fII", 0.96, len(records), 0x800))
    body = hedr + _sub(b"CNAM", _zstr(author))
    for m in masters:
        body += _sub(b"MAST", _zstr(m))
    out = _record(b"TES4", 0, body, flags)
    by_sig = {}
    for r in records:
        sig = r[0].encode("ascii") if isinstance(r[0], str) else r[0]
        by_sig.setdefault(sig, []).append(r)
    for sig, recs in by_sig.items():
        inner = b"".join(_record(sig, r[1], _sub(b"EDID", _zstr(r[2])), r[3] if len(r) > 3 else 0) for r in recs)
        out += _group(sig, inner)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(out)
    return path


def _pack(how, content):
    if how == "zlib":
        return zlib.compress(content)
    import lz4.block  # optional, as in the tool; tests that need it skip when it's missing
    return lz4.block.compress(content, store_size=False)


def ba2(path, files, version=2, kind=b"GNRL"):
    """Write a BA2. GNRL gets real 36-byte records and data; DX10 gets texture records, one chunk per piece of pixel
    data ((how, chunk1, chunk2) gives two). A v3 archive with any LZ4 file says LZ4 in its header. Returns path."""
    names = list(files)
    hs = HEADER_SIZE[version]
    lz4 = any(isinstance(b, tuple) and b[0] == "lz4" for b in files.values())
    if kind == b"DX10":
        rec_len = sum(24 + 24 * (2 if isinstance(files[n], tuple) and len(files[n]) > 2 else 1) for n in names)
    else:
        rec_len = 36 * len(names)
    blobs, recs, off = b"", b"", hs + rec_len
    for n in names:
        b = files[n]
        if kind == b"DX10":
            parts = [b] if not isinstance(b, tuple) else [(b[0], c) for c in b[1:]]
            recs += struct.pack("<I4sIBBHHHBBBB", 0, b"dds\0", 0, 0, len(parts), 24, 4, 4, 1, 0, 0, 0)
            for p in parts:
                raw = _pack(*p) if isinstance(p, tuple) else p
                up = len(p[1]) if isinstance(p, tuple) else len(p)
                recs += struct.pack("<QIIHHI", off, len(raw) if isinstance(p, tuple) else 0, up, 0, 0, 0xBAADF00D)
                blobs += raw
                off += len(raw)
            continue
        if isinstance(b, tuple):
            packed = _pack(*b)
            recs += struct.pack("<I4sIIQIII", 0, b"bin\0", 0, 0, off, len(packed), len(b[1]), 0xBAADF00D)
            b = packed
        else:
            recs += struct.pack("<I4sIIQIII", 0, b"bin\0", 0, 0, off, 0, len(b), 0xBAADF00D)
        blobs += b
        off += len(b)
    table = b"".join(struct.pack("<H", len(n)) + n.encode() for n in names)
    head = b"BTDX" + struct.pack("<I", version) + kind + struct.pack("<IQ", len(names), hs + len(recs) + len(blobs))
    if version == 3:
        head += struct.pack("<III", 0, 0, 3 if lz4 else 0)
    head += b"\0" * (hs - len(head))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(head + recs + blobs + table)
    return path


def on_disk(folder, rel):
    """folder joined with a Data-relative path written the game's way ("textures\\x.dds"): the backslashes become
    this system's separator, so Linux makes the subfolders too instead of one file named "textures\\x.dds". Paths
    inside archives and in Vortex's records keep their backslashes; only files written to disk go through this."""
    return os.path.join(folder, *rel.replace("/", "\\").split("\\"))


OFFICIAL = ["Starfield.esm", "ShatteredSpace.esm", "SFBGS003.esm", "SFBGS004.esm", "SFBGS006.esm",
            "SFBGS007.esm", "SFBGS008.esm"]


def plugins_txt(path, entries):
    """entries: [(name, enabled)]. Writes the game's format: '*' = enabled, '#' = comment."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("# This file is used by Starfield to keep track of your downloaded content.\n")
        for name, on in entries:
            f.write(("*" if on else "") + name + "\n")
    return path


def game(root, plugins, official=OFFICIAL):
    """A fake install: <root>\\Data with the official masters plus plugins ({name: dict(masters, kind, records)}),
    and <root>\\Plugins.txt listing the mod plugins enabled in the given order. Returns (data, plugins_txt)."""
    data = os.path.join(root, "Data")
    for name in official:
        plugin(os.path.join(data, name), kind="F")
    for name, spec in plugins.items():
        plugin(os.path.join(data, name), **(spec or {}))
    return data, plugins_txt(os.path.join(root, "Plugins.txt"), [(n, True) for n in plugins])


def mo2(root, profile, mods):
    """A fake MO2 instance. mods: [(mod name, enabled, {relative path: bytes or plugin spec dict})], lowest priority
    first (MO2's modlist.txt lists highest first, so it is written reversed). Plugin files (.esm/.esp) given a dict
    are built with plugin(). Plugins load in the order they appear. Returns the instance folder."""
    prof = os.path.join(root, "profiles", profile)
    os.makedirs(prof, exist_ok=True)
    order = []
    for name, on, files in mods:
        for rel, content in files.items():
            full = on_disk(os.path.join(root, "mods", name), rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if isinstance(content, dict):
                plugin(full, **content)
                if on and "\\" not in rel and "/" not in rel:
                    order.append(rel)
            else:
                with open(full, "wb") as f:
                    f.write(content)
    with open(os.path.join(prof, "modlist.txt"), "w", encoding="utf-8") as f:
        f.write("# This file was automatically generated by Mod Organizer.\n")
        for name, on, _ in reversed(mods):
            f.write(("+" if on else "-") + name + "\n")
    plugins_txt(os.path.join(prof, "plugins.txt"), [(n, True) for n in order])
    with open(os.path.join(prof, "loadorder.txt"), "w", encoding="utf-8") as f:
        f.write("".join(n + "\n" for n in order))
    with open(os.path.join(root, "ModOrganizer.ini"), "w", encoding="utf-8") as f:
        f.write(f"[General]\ngameName=Starfield\nselected_profile=@ByteArray({profile})\n")
    return root


def vortex_loadorder(path, entries):
    """A Vortex Load Order page export (Export button; the same list as Vortex's persistent.loadOrder[profile]), in
    load order. entries: [(name, enabled)] or [(name, enabled, mod id)]. Official masters are locked and keep their
    name as id; mod plugins get a lower-case id, the Vortex mod id, locked false and data.isInvalid, as Vortex
    writes them (shape checked on a real export, 2026-11-19)."""
    out = []
    for e in entries:
        n, on = e[0], e[1]
        if n in OFFICIAL:
            out.append({"enabled": on, "id": n, "name": n, "locked": True})
        else:
            out.append({"enabled": on, "id": n.lower(), "name": n.lower(),
                        "modId": e[2] if len(e) > 2 else os.path.splitext(n)[0], "locked": False,
                        "data": {"isInvalid": False}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return path


def conflicts_json(path, load_order, lost=(), itms=(), problems=()):
    """A conflicts.json shaped like sf-mod-check conflict's (top-level keys as seen in 0.5.1 and later).
    load_order: [(name, official, masters)]. lost: [dict(formKey, RecordType, EditorID, Loser, Winner, Fields)]."""
    d = {"generated": "2000-01-01T00:00:00Z", "loadOrderSource": "fixture",
         "loadOrder": [{"Index": i, "FileName": n, "Official": off, "Masters": list(m)}
                       for i, (n, off, m) in enumerate(load_order)],
         "loadOrderProblems": list(problems), "RecordsOverriddenByMods": 0, "lost": list(lost),
         "itms": list(itms), "UnreadableFields": {}, "Errors": [], "typeChanges": []}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    return path
