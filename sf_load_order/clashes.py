"""Known clashes (section "clashes"), from four sources:

  1. LOOT's masterlist: the player's own copy, which LOOT downloads when it runs (default
     %LOCALAPPDATA%\\LOOT\\games\\Starfield\\masterlist.yaml, plus userlist.yaml next to it). For enabled plugins it
     reports requirements that are missing, incompatible plugins that are both enabled, LOOT's warn and error
     messages, and "dirty" entries (needs cleaning) whose CRC32 matches the installed file. The masterlist is CC0
     (public domain), but this tool never ships a copy: it reads only the one on the player's PC.
  2. --clashes FILE: the player's own known clashes (format in the README).
  3. --accept FILE: clashes the player has looked at and accepted, pinned to each plugin's SHA-256, so an update to
     any of the files brings the clash back for review. Official plugins count as always enabled.
  4. --picks FILE: "plugin X loads right after plugin Y". Only checked and stored in ctx.results["picks"] for the
     recommended order; the only findings are for entries that can't be used.

A bad file fails only its own part: the finding names the file, the line or entry, and the fix, and the other parts
still run. Nothing here writes anything.

The standard library has no YAML reader, so read_yaml() below reads the subset LOOT's masterlists use: block
mappings and sequences, flow sequences and mappings, plain, single- and double-quoted scalars (also over several
lines), block scalars (| and >), anchors, aliases, merge keys (<<) and comments. Anything else is an error that names
the line. LOOT conditions are evaluated only when they use file(), active(), readable(), checksum(), many(),
many_active(), not, and, or and brackets; any other function makes the item "unknown condition", which is skipped
with a log line (never guessed).
"""
import datetime
import hashlib
import json
import os
import re
import zlib

from . import log
from .inputs import is_official
from .model import Finding

SECTION = "clashes"
LEVELS = {"problem": "problem", "caution": "caution", "pick-one": "caution"}
PLUGIN_EXT = (".esm", ".esp", ".esl")
LOOT_HOW = ("LOOT downloads its masterlist the first time it runs for Starfield and keeps it in its data folder "
            "(games\\Starfield\\masterlist.yaml; LOOT's File menu > Open LOOT Data Folder shows where). Install LOOT "
            "(loot.github.io), open it once with Starfield selected, then run this again.")


# =====================================================================================================================
# YAML subset reader
# =====================================================================================================================
class YamlError(ValueError):
    def __init__(self, line, msg):
        self.line = line
        self.msg = msg
        super().__init__(f"line {line}: {msg}")


class _Line:
    __slots__ = ("raw", "no", "indent", "text")

    def __init__(self, raw, no, indent, text):
        self.raw, self.no, self.indent, self.text = raw, no, indent, text


_BLOCK_HDR = re.compile(r"^([|>])([+-]?)([1-9]?)([+-]?)\s*(#.*)?$")
_ANCHOR = re.compile(r"^&([^\s,\[\]{}]+)\s*")
_INT = re.compile(r"^[-+]?[0-9]+$")
_HEX = re.compile(r"^0x[0-9a-fA-F]+$")
_FLOAT = re.compile(r"^[-+]?([0-9]+\.[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$")


class _Yaml:
    def __init__(self, text):
        self.raw = text.split("\n")
        self.anchors = {}
        self.lines = []
        for i, r in enumerate(self.raw):
            r = r.rstrip("\r")
            self.raw[i] = r
            body = r.lstrip(" ")
            if not body.strip() or body.startswith("#"):
                continue
            if body.startswith("\t"):
                raise YamlError(i + 1, "a tab is used for indentation; use spaces")
            if r.startswith(("---", "...")) and r[3:4] in ("", " "):
                if r.strip() not in ("---", "..."):
                    raise YamlError(i + 1, "text after a document marker isn't supported")
                continue
            if body.startswith(("%", "!")) or re.match(r"^-\s+!", body):
                raise YamlError(i + 1, "YAML directives and tags aren't supported")
            self.lines.append(_Line(i, i + 1, len(r) - len(body), body.rstrip()))
        self.i = 0

    # ----- helpers -----
    def cur(self):
        return self.lines[self.i] if self.i < len(self.lines) else None

    def skip_past_raw(self, raw_index):
        while self.i < len(self.lines) and self.lines[self.i].raw <= raw_index:
            self.i += 1

    @staticmethod
    def is_seq(t):
        return t == "-" or t.startswith("- ")

    @staticmethod
    def key_split(t):
        """(key, rest) when t starts with a mapping key, else None."""
        if not t or t[0] in "[{&*|>!%@`#" or _Yaml.is_seq(t):
            return None
        if t[0] in "'\"":
            q = t[0]
            j = 1
            while j < len(t):
                if t[j] == q:
                    if q == "'" and t[j + 1:j + 2] == "'":
                        j += 2
                        continue
                    break
                if q == '"' and t[j] == "\\":
                    j += 1
                j += 1
            else:
                return None
            after = t[j + 1:]
            if not (after.startswith(":") and (len(after) == 1 or after[1] in " \t")):
                return None
            return _Yaml.scalar_quoted(t[:j + 1], 0)[0], after[1:].strip()
        m = re.search(r":(\s|$)", t)
        if not m or re.search(r"\s#", t[:m.start()]):
            return None
        key = t[:m.start()].strip()
        if key.startswith("? "):
            return None
        return key, t[m.end():].strip()

    # ----- block structure -----
    def parse(self):
        if not self.lines:
            return None
        node = self.node(self.lines[0].indent)
        if self.cur() is not None:
            raise YamlError(self.cur().no, "unexpected text (check the indentation)")
        return node

    def node(self, indent):
        L = self.cur()
        if self.is_seq(L.text):
            return self.seq(L.indent)
        if self.key_split(L.text) is not None:
            return self.mapping(L.indent)
        self.i += 1
        return self.inline(L.text, L)

    def child(self, n, seq_same_indent=False):
        L = self.cur()
        if L is None:
            return None
        if L.indent > n:
            return self.node(L.indent)
        if seq_same_indent and L.indent == n and self.is_seq(L.text):
            return self.seq(n)
        return None

    def value_after(self, rest, L, n, is_key):
        """The value given after '- ' or 'key:' on line L (rest), reading following lines when needed."""
        anchor = None
        m = _ANCHOR.match(rest)
        if m:
            anchor, rest = m.group(1), rest[m.end():]
        if not rest or rest.startswith("#"):
            self.i += 1
            v = self.child(n, seq_same_indent=is_key)
        elif _BLOCK_HDR.match(rest):
            v = self.block_scalar(rest, L, n)
        elif not is_key and (self.is_seq(rest) or self.key_split(rest) is not None):
            # "- key: value" or "- - x": a nested block that starts on this line
            L.indent = L.indent + len(L.text) - len(rest)
            L.text = rest
            v = self.node(L.indent)
        else:
            self.i += 1
            v = self.inline(rest, L)
        if anchor:
            self.anchors[anchor] = v
        return v

    def seq(self, n):
        out = []
        while True:
            L = self.cur()
            if L is None or L.indent != n or not self.is_seq(L.text):
                break
            out.append(self.value_after(L.text[1:].lstrip(), L, n, False))
        L = self.cur()
        if L is not None and L.indent > n:
            raise YamlError(L.no, "bad indentation inside a list")
        return out

    def mapping(self, n):
        out, merges = {}, []
        while True:
            L = self.cur()
            if L is None or L.indent != n or self.is_seq(L.text):
                break
            ks = self.key_split(L.text)
            if ks is None:
                raise YamlError(L.no, "expected 'key: value'")
            key, rest = ks
            no = L.no
            v = self.value_after(rest, L, n, True)
            if key == "<<":
                merges.append((v, no))
                continue
            if key in out:
                raise YamlError(no, f"the key {key!r} appears twice in one mapping")
            out[key] = v
        for v, no in merges:
            for m in (v if isinstance(v, list) else [v]):
                if not isinstance(m, dict):
                    raise YamlError(no, "a merge key (<<) must point at a mapping")
                for k, x in m.items():
                    out.setdefault(k, x)
        L = self.cur()
        if L is not None and L.indent > n:
            raise YamlError(L.no, "bad indentation inside a mapping")
        return out

    def block_scalar(self, header, L, n):
        m = _BLOCK_HDR.match(header)
        style, chomp = m.group(1), (m.group(2) or m.group(4))
        lines, last, content_indent = [], L.raw, None
        if m.group(3):
            content_indent = n + int(m.group(3))
        for r in range(L.raw + 1, len(self.raw)):
            s = self.raw[r]
            if not s.strip():
                lines.append("")
                last = r
                continue
            ind = len(s) - len(s.lstrip(" "))
            if ind <= n:
                break
            if content_indent is None:
                content_indent = ind
            if ind < content_indent:
                break
            lines.append(s[content_indent:])
            last = r
        self.skip_past_raw(last)
        trailing = 0
        while lines and lines[-1] == "":
            lines.pop()
            trailing += 1
        if style == "|":
            text = "\n".join(lines)
        else:
            text = ""
            for ln in lines:
                if not text:
                    text = ln
                elif ln == "":
                    text += "\n"
                else:
                    text += ("" if text.endswith("\n") else " ") + ln
        if chomp == "-" or not text:
            return text
        return text + "\n" * (1 + trailing if chomp == "+" else 1)

    # ----- inline (flow) values -----
    def inline(self, s, L):
        """Parse one flow value starting on line L; reads further raw lines while a quote or bracket is open."""
        text, last = s, L.raw
        while not self._balanced(text):
            if last + 1 >= len(self.raw):
                raise YamlError(L.no, "a quote or bracket is never closed")
            last += 1
            text += "\n" + self.raw[last]
        if last != L.raw:
            self.skip_past_raw(last)
        v, pos = self.flow_value(text, 0, L.no, flow=False)
        rest = text[pos:].strip()
        if rest and not rest.startswith("#"):
            raise YamlError(L.no, f"unexpected text after the value: {rest[:30]!r}")
        return v

    @staticmethod
    def _balanced(t):
        q, depth, j = None, 0, 0
        while j < len(t):
            c = t[j]
            if q:
                if c == q:
                    if q == "'" and t[j + 1:j + 2] == "'":
                        j += 1
                    else:
                        q = None
                elif q == '"' and c == "\\":
                    j += 1
            elif c in "'\"" and (j == 0 or t[j - 1] in " \t\n[{,:"):
                q = c
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
            elif c == "#" and (j == 0 or t[j - 1] in " \t\n"):
                nl = t.find("\n", j)
                if nl < 0:
                    break
                j = nl
            j += 1
        return q is None and depth <= 0

    def flow_value(self, t, p, no, flow):
        while p < len(t) and t[p] in " \t\n":
            p += 1
        if p >= len(t):
            return None, p
        c = t[p]
        if c == "&":
            m = _ANCHOR.match(t[p:])
            v, p = self.flow_value(t, p + m.end(), no, flow)
            self.anchors[m.group(1)] = v
            return v, p
        if c == "*":
            m = re.match(r"\*([^\s,\[\]{}]+)", t[p:])
            name = m.group(1)
            if name not in self.anchors:
                raise YamlError(no, f"the alias *{name} names no anchor defined above it")
            return self.anchors[name], p + m.end()
        if c == "[":
            out, p = [], p + 1
            while True:
                p = self._ws(t, p)
                if p >= len(t):
                    raise YamlError(no, "a [ list is never closed")
                if t[p] == "]":
                    return out, p + 1
                v, p = self.flow_value(t, p, no, True)
                out.append(v)
                p = self._ws(t, p)
                if p < len(t) and t[p] == ",":
                    p += 1
                elif p < len(t) and t[p] != "]":
                    raise YamlError(no, "expected , or ] in a [ list")
        if c == "{":
            out, p = {}, p + 1
            while True:
                p = self._ws(t, p)
                if p >= len(t):
                    raise YamlError(no, "a { mapping is never closed")
                if t[p] == "}":
                    return out, p + 1
                k, p = self.flow_value(t, p, no, True)
                p = self._ws(t, p)
                v = None
                if p < len(t) and t[p] == ":":
                    v, p = self.flow_value(t, p + 1, no, True)
                    p = self._ws(t, p)
                out[k] = v
                if p < len(t) and t[p] == ",":
                    p += 1
                elif p < len(t) and t[p] != "}":
                    raise YamlError(no, "expected , or } in a { mapping")
        if c in "'\"":
            return self.scalar_quoted(t, p, no)
        if c in "|>" and not flow:
            raise YamlError(no, "a block scalar (| or >) must end its line")
        if c in "!%@`":
            raise YamlError(no, f"{c} at the start of a value isn't supported (quote the value)")
        j = p
        while j < len(t):
            ch = t[j]
            if ch == "\n" or (ch == "#" and t[j - 1] in " \t"):
                break
            if flow and (ch in ",[]{}" or (ch == ":" and (j + 1 >= len(t) or t[j + 1] in " \t\n,]}"))):
                break
            j += 1
        return self.plain(t[p:j].strip()), j

    @staticmethod
    def _ws(t, p):
        while p < len(t) and t[p] in " \t\n":
            p += 1
        return p

    @staticmethod
    def plain(s):
        if s in ("", "~", "null", "Null", "NULL"):
            return None
        if s in ("true", "True", "TRUE"):
            return True
        if s in ("false", "False", "FALSE"):
            return False
        if _INT.match(s):
            return int(s)
        if _HEX.match(s):
            return int(s, 16)
        if _FLOAT.match(s):
            return float(s)
        return s

    @staticmethod
    def scalar_quoted(t, p, no=0):
        q = t[p]
        j, buf = p + 1, []
        esc = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "/": "/", "0": "\0", "r": "\r", " ": " ", "e": "\x1b"}
        while True:
            if j >= len(t):
                raise YamlError(no, "a quoted value is never closed")
            ch = t[j]
            if ch == q:
                if q == "'" and t[j + 1:j + 2] == "'":
                    buf.append("'")
                    j += 2
                    continue
                break
            if q == '"' and ch == "\\":
                n = t[j + 1:j + 2]
                if n == "\n":  # an escaped line break joins the lines with nothing between
                    j += 2
                    while j < len(t) and t[j] in " \t":
                        j += 1
                    continue
                if n in ("x", "u", "U"):
                    size = {"x": 2, "u": 4, "U": 8}[n]
                    try:
                        buf.append(chr(int(t[j + 2:j + 2 + size], 16)))
                    except ValueError:
                        raise YamlError(no, f"bad \\{n} escape in a quoted value")
                    j += 2 + size
                    continue
                if n not in esc:
                    raise YamlError(no, f"unknown escape \\{n} in a double-quoted value")
                buf.append(esc[n])
                j += 2
                continue
            if ch == "\n":  # folding: one line break = a space, each empty line = a newline
                while buf and buf[-1] in (" ", "\t"):
                    buf.pop()
                j += 1
                breaks = 0
                while True:
                    k = j
                    while k < len(t) and t[k] in " \t":
                        k += 1
                    if k < len(t) and t[k] == "\n":
                        breaks += 1
                        j = k + 1
                        continue
                    j = k
                    break
                buf.append("\n" * breaks if breaks else " ")
                continue
            buf.append(ch)
            j += 1
        return "".join(buf), j + 1


def read_yaml(text):
    """Parse the YAML subset described in the module docstring. Raises YamlError (with .line) on anything else."""
    if text.startswith("﻿"):
        text = text[1:]
    return _Yaml(text).parse()


# =====================================================================================================================
# LOOT conditions
# =====================================================================================================================
_TOKEN = re.compile(r'\s*(\(|\)|,|"[^"]*"|==|!=|<=|>=|<|>|[A-Za-z0-9_.\-]+)')
SAFE_FUNCS = ("file", "active", "readable", "checksum", "many", "many_active")


class ConditionError(ValueError):
    pass


def parse_condition(s):
    """Tokens -> tree: ("or", [..]), ("and", [..]), ("not", x), ("call", name, [args])."""
    toks, p = [], 0
    s = s.strip()
    while p < len(s):
        m = _TOKEN.match(s, p)
        if not m or m.end() == p:
            raise ConditionError(f"can't read the condition at {s[p:p + 20]!r}")
        toks.append(m.group(1))
        p = m.end()
        while p < len(s) and s[p] in " \t":
            p += 1
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take(want=None):
        t = peek()
        if t is None or (want is not None and t != want):
            raise ConditionError(f"expected {want or 'more'} in the condition, found {t!r}")
        pos[0] += 1
        return t

    def expr():
        items = [conj()]
        while peek() == "or":
            take()
            items.append(conj())
        return items[0] if len(items) == 1 else ("or", items)

    def conj():
        items = [unary()]
        while peek() == "and":
            take()
            items.append(unary())
        return items[0] if len(items) == 1 else ("and", items)

    def unary():
        if peek() == "not":
            take()
            return ("not", unary())
        if peek() == "(":
            take()
            e = expr()
            take(")")
            return e
        name = take()
        if not re.match(r"^[a-z_]+$", name):
            raise ConditionError(f"expected a function name, found {name!r}")
        take("(")
        args, depth, cur = [], 0, []
        while True:
            t = take()
            if t == ")" and depth == 0:
                break
            if t == "(":
                depth += 1
            elif t == ")":
                depth -= 1
            if t == "," and depth == 0:
                args.append(" ".join(cur))
                cur = []
            else:
                cur.append(t)
        if cur:
            args.append(" ".join(cur))
        return ("call", name, args)

    tree = expr()
    if peek() is not None:
        raise ConditionError(f"unexpected {peek()!r} in the condition")
    return tree


def _is_regex(s):
    return any(c in s for c in ":\\*?|")


# The regex guard (DESIGN-0.3.0.md 2.6). LOOT's entry names and active() arguments may be regular expressions; a
# slow one (like (a+)+) can take very long on a short name, so every pattern goes through safe_regex() first, and
# every match through rx_match() (names of at most MAX_MATCH characters). The guard is deliberately strict and simple
# (code review round 4 simplified it): LOOT's real Starfield patterns are short names with a [..] class, a \. and at
# most one optional group, so anything beyond a small budget of choices is refused rather than analysed.
MAX_PATTERN = 256
MAX_MATCH = 260
MAX_REGEX_ENTRIES = 2000      # regex entry names read per LOOT file; the rest are skipped
MAX_LOOT_BYTES = 20 * 1024 * 1024
MAX_CHOICES = 6               # *, +, ?, {..} and | per pattern: each one can double the work of a failed match
MAX_UNBOUNDED = 2             # of those, open-ended repeats (*, +, {n,} or {n,m} with m over MAX_BOUND)
MAX_BOUND = 10
_QUANT = re.compile(r"\{(\d*)(,(\d*))?\}")
_GROUP_OPEN = ("(?:", "(?i)")  # the only (? forms allowed; any other (look-behind, inline flags such as (?x),
                               # named groups and their back-references) is refused


def regex_refusal(text):
    """Why a pattern is refused, or None when it is safe to compile: longer than MAX_PATTERN characters, a
    back-reference, a (? form other than (?: and (?i), a group followed by *, + or {..} (only ? may follow a group),
    more than MAX_CHOICES choices (*, +, ?, {..} and | together) or more than MAX_UNBOUNDED open-ended repeats."""
    if not isinstance(text, str):
        return "not text"
    if len(text) > MAX_PATTERN:
        return f"longer than {MAX_PATTERN} characters"
    choices = unbounded = 0
    after_group = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            nxt = text[i + 1:i + 2]
            if nxt.isdigit() and nxt != "0":
                return "a back-reference"
            i, after_group = i + 2, False
            continue
        if c == "[":
            j = i + 1
            if j < n and text[j] == "^":
                j += 1
            if j < n and text[j] == "]":
                j += 1
            while j < n and text[j] != "]":
                j += 2 if text[j] == "\\" else 1
            i, after_group = j + 1, False
            continue
        if c == "(":
            if text.startswith("(?", i):
                form = next((f for f in _GROUP_OPEN if text.startswith(f, i)), None)
                if form is None:
                    return "a (? form other than (?: or (?i) (look-behinds, inline flags and named groups aren't allowed)"
                i, after_group = i + len(form), False
                continue
            i, after_group = i + 1, False
            continue
        if c == ")":
            i, after_group = i + 1, True
            continue
        q = _QUANT.match(text, i) if c == "{" else None
        if c in "*+?|" or q:
            choices += 1
            if choices > MAX_CHOICES:
                return f"more than {MAX_CHOICES} choices (*, +, ?, {{..}} and | together; it can take very long to match)"
            if after_group and c != "?" and c != "|":
                return "a repeated group (only ? may follow a group; it can take very long to match)"
            open_ended = c in "*+" or (q and q.group(2) is not None and (not q.group(3) or int(q.group(3)) > MAX_BOUND))
            if open_ended:
                unbounded += 1
                if unbounded > MAX_UNBOUNDED:
                    return (f"more than {MAX_UNBOUNDED} open-ended repeats (*, +, {{n,}} or {{n,m}} with m over "
                            f"{MAX_BOUND}; it can take very long to match)")
            i = q.end() if q else i + 1
            if c != "|" and i < n and text[i] in "?+":
                i += 1  # lazy or possessive form of the same repeat
            after_group = False
            continue
        i, after_group = i + 1, False  # a plain character, ^, $ or .
    return None


def safe_regex(text):
    """(compiled pattern, None) for a safe pattern (case-insensitive, as LOOT matches), or (None, why)."""
    why = regex_refusal(text)
    if why:
        return None, why
    try:
        return re.compile(text, re.I), None
    except re.error as e:
        return None, f"not a valid regular expression ({e})"


def rx_match(rx, s):
    """rx.fullmatch(s) for names of at most MAX_MATCH characters (plugin and folder entry names); longer: no match."""
    return isinstance(s, str) and len(s) <= MAX_MATCH and rx.fullmatch(s) is not None


def search_roots(lo):
    """Folders a LOOT path (relative to Data) is looked up in, the winning copy first. MO2: the enabled mods' folders,
    highest priority first (Overwrite included), then the real Data folder, since MO2 shows them all to the game as
    one Data folder. Vortex and Plugins.txt: the Data folder only (Vortex deploys its mods there)."""
    roots = []
    if lo.source == "mo2":
        mods = [(i, m) for i, m in enumerate(lo.mods or []) if m.enabled and m.path]
        mods.sort(key=lambda im: (-(im[1].priority if im[1].priority is not None else im[0]), -im[0]))
        roots += [m.path for _i, m in mods]
    if lo.data:
        roots.append(lo.data)
    return roots


def _ci_join(root, rel):
    """root joined with a "/"-separated path relative to it, as it is on disk, matching each part in any case (the
    game, like Windows, ignores case; a Linux or macOS copy of the files may not). None when a part is missing."""
    cur = root
    for part in (p for p in rel.split("/") if p and p != "."):
        nxt = os.path.join(cur, part)
        if os.path.exists(nxt):
            cur = nxt
            continue
        try:
            low = part.lower()
            hit = next((n for n in os.listdir(cur) if n.lower() == low), None)
        except OSError:
            return None
        if hit is None:
            return None
        cur = os.path.join(cur, hit)
    return cur if os.path.exists(cur) else None


class Env:
    """What conditions can look at: the Data folder (with MO2, the enabled mod folders over it), the load order's
    plugins (path, enabled)."""

    def __init__(self, lo):
        self.data = lo.data
        self.roots = search_roots(lo)
        self.on = {n.lower() for n in lo.enabled()}
        self.paths = {p.name.lower(): p.path for p in lo.plugins if p.path}
        self.names = [p.name for p in lo.plugins]
        self._crc = {}
        self._rx = {}
        self.unsafe = 0  # condition patterns refused by the regex guard (each one makes its condition unknown)

    def regex(self, pattern):
        """The compiled pattern, or None when the guard refuses it or it doesn't compile (logged once each)."""
        if pattern not in self._rx:
            rx, why = safe_regex(pattern)
            if rx is None:
                if regex_refusal(pattern):
                    self.unsafe += 1
                log.info(SECTION, f"loot: condition pattern {pattern[:80]!r} skipped: {why}")
            self._rx[pattern] = rx
        return self._rx[pattern]

    def crc(self, path):
        if path not in self._crc:
            c = 0
            try:
                with open(path, "rb") as f:
                    for block in iter(lambda: f.read(1 << 20), b""):
                        c = zlib.crc32(block, c)
                self._crc[path] = c & 0xFFFFFFFF
            except OSError:
                self._crc[path] = None
        return self._crc[path]

    def _safe(self, rel):
        r = rel.replace("\\", "/") if not _is_regex(rel) else rel
        parts = r.split("/")
        if os.path.isabs(rel) or re.match(r"^[A-Za-z]:", rel) or ".." in parts:
            return None
        return r

    def find(self, rel):
        """Paths matching a LOOT path (relative to Data; the last part may be a regex). None = can't tell."""
        r = self._safe(rel)
        if r is None:
            return None
        if "/" in r:
            parent, leaf = r.rsplit("/", 1)
        else:
            parent, leaf = "", r
        if _is_regex(leaf):
            rx = self.regex(leaf)
            if rx is None:
                return None
            win = {n.lower(): self.paths[n.lower()] for n in self.names if not parent and rx_match(rx, n)
                   and n.lower() in self.paths}
            if not self.roots and not win:
                return None
            for root in self.roots:  # winning copy first: a name already found in a higher root is kept
                d = _ci_join(root, parent)
                if d is None:
                    continue
                try:
                    for x in os.listdir(d):
                        if rx_match(rx, x):
                            win.setdefault(x.lower(), os.path.join(d, x))
                except OSError:
                    pass
            return sorted(win.values())
        if not parent and leaf.lower() in self.paths:
            return [self.paths[leaf.lower()]]
        if not self.roots:
            return None
        for root in self.roots:
            full = _ci_join(root, r)
            if full is not None:
                return [full]
        return []

    def call(self, name, args):
        """True, False, or None (unknown: a function we don't evaluate, or a path we can't check)."""
        if name not in SAFE_FUNCS:
            return None
        if not args or not (args[0].startswith('"') and args[0].endswith('"')):
            return None
        a = args[0][1:-1]
        if name in ("active", "many_active"):
            if _is_regex(a):
                rx = self.regex(a)
                if rx is None:
                    return None
                n = sum(1 for x in self.on if rx_match(rx, x))
                return n > 1 if name == "many_active" else n > 0
            return False if name == "many_active" else a.lower() in self.on
        found = self.find(a)
        if found is None:
            return None
        if name == "file":
            return bool(found)
        if name == "many":
            return len(found) > 1
        if name == "readable":
            return any(os.access(f, os.R_OK) for f in found)
        if name == "checksum":
            if len(args) != 2 or _is_regex(a):
                return None
            try:
                want = int(args[1], 16)
            except ValueError:
                return None
            if not found or os.path.isdir(found[0]):
                return False
            got = self.crc(found[0])
            return None if got is None else got == want
        return None


def evaluate(cond, env):
    """(value, why): value True/False/None. None means unknown; why says which part couldn't be evaluated."""
    if cond is None or cond == "":
        return True, None
    if not isinstance(cond, str):
        return None, "the condition isn't a string"
    try:
        tree = parse_condition(cond)
    except ConditionError as e:
        return None, str(e)
    why = []

    def ev(n):
        if n[0] == "call":
            v = env.call(n[1], n[2])
            if v is None:
                why.append(f"{n[1]}({', '.join(n[2])})")
            return v
        if n[0] == "not":
            v = ev(n[1])
            return None if v is None else not v
        vals = [ev(x) for x in n[1]]
        if n[0] == "and":
            return False if False in vals else None if None in vals else True
        return True if True in vals else None if None in vals else False

    v = ev(tree)
    return v, (None if v is not None else "can't evaluate " + "; ".join(why))


# =====================================================================================================================
# helpers
# =====================================================================================================================
def _sha256(path, cache):
    if path not in cache:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            cache[path] = (h.hexdigest(), os.path.getsize(path))
        except OSError:
            cache[path] = (None, None)
    return cache[path]


def _plugin_path(lo, name):
    for p in lo.plugins:
        if p.name.lower() == name.lower() and p.path:
            return p.path
    if lo.data:
        full = _ci_join(lo.data, name.replace("\\", "/"))
        if full and os.path.isfile(full):
            return full
    return None


def _bad_file(ctx, part, path, text, fix, **data):
    """One caution naming the file (and line or entry) and the fix; returns the part status."""
    ctx.report.add(Finding("caution", SECTION, f"{path}: {text}", fix=fix, data=dict(file=path, part=part, **data)))
    log.info(SECTION, f"{part}: {path}: {text}")
    return {"status": "failed", "why": f"{path}: {text}"}


def _load_json(ctx, part, path, option, key):
    """(list of entries, None) or (None, failed status). Accepts a JSON list, or an object holding it under key."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None, _bad_file(ctx, part, path, "file not found.", f"Check the path given to {option}.")
    except OSError as e:
        return None, _bad_file(ctx, part, path, f"can't read it ({e.strerror}).",
                               f"Check that the file given to {option} can be opened.")
    except UnicodeDecodeError:
        return None, _bad_file(ctx, part, path, "isn't UTF-8 text.", "Save it as UTF-8 (any text editor can).")
    except json.JSONDecodeError as e:
        return None, _bad_file(ctx, part, path, f"line {e.lineno}, column {e.colno}: {e.msg}.",
                               "Correct the JSON there (a missing comma, quote or bracket is the usual cause).",
                               line=e.lineno)
    if isinstance(d, dict) and isinstance(d.get(key), list):
        d = d[key]
    if not isinstance(d, list):
        return None, _bad_file(ctx, part, path, f"the file must hold a JSON list of entries (or {{\"{key}\": [...]}}).",
                               "See the README for the format and the examples folder for a sample.")
    return d, None


def _entry_errors(e, allowed, required):
    if not isinstance(e, dict):
        return ["the entry isn't a JSON object ({...})"]
    errs = [f"unknown field {k!r} (allowed: {', '.join(allowed)})" for k in e
            if k not in allowed and not k.startswith("_")]
    errs += [f"the field {k!r} is missing" for k in required if k not in e]
    return errs


def _bad_entry(ctx, part, path, i, errs, fix):
    ctx.report.add(Finding("caution", SECTION, f"{path}, entry {i + 1}: {'; '.join(errs)}. The entry was skipped.",
                           fix=fix, data={"file": path, "part": part, "entry": i + 1}))
    log.info(SECTION, f"{part}: {path} entry {i + 1} skipped: {'; '.join(errs)}")


def _is_plugin_name(s):
    return isinstance(s, str) and s.strip() == s and s.lower().endswith(PLUGIN_EXT) and not re.search(r"[\\/]", s)


def accept_entry(lo, names, cache, why=""):
    """A ready-to-paste --accept entry for these plugins' current files (None when a file can't be read)."""
    files = {}
    for n in names:
        path = _plugin_path(lo, n)
        sha, size = _sha256(path, cache) if path else (None, None)
        if not sha:
            return None
        files[n] = {"sha256": sha, "size": size}
    return {"plugins": list(names), "date": datetime.date.today().isoformat(), "why": why, "files": files}


# =====================================================================================================================
# part 3: --accept
# =====================================================================================================================
ACCEPT_FIELDS = ("plugins", "files", "date", "why", "by", "history")


def read_accept(ctx, path, cache):
    """(statuses, part status). Each status: {entry, plugins, status, changed, missing, date, why}.
    status: valid | expired | missing | inactive."""
    entries, failed = _load_json(ctx, "accept", path, "--accept", "accepted")
    if failed:
        return [], failed
    lo = ctx.load_order
    on = {n.lower() for n in lo.enabled()}
    out, bad = [], 0
    fix = ("Each entry needs \"files\": {\"<plugin>\": {\"sha256\": \"<64 hex digits>\"}}; the report shows a ready "
           "entry next to each open clash. See the README for the format.")
    for i, e in enumerate(entries):
        errs = _entry_errors(e, ACCEPT_FIELDS, ("files",))
        if not errs:
            files = e["files"]
            if not isinstance(files, dict) or not files:
                errs.append("\"files\" must be a non-empty object of plugin name -> {\"sha256\": ...}")
            else:
                for n, fp in files.items():
                    if not _is_plugin_name(n):
                        errs.append(f"{n!r} isn't a plugin file name (.esm, .esp or .esl)")
                    shas = fp.get("sha256") if isinstance(fp, dict) else None
                    shas = shas if isinstance(shas, list) else [shas]
                    if not shas or not all(isinstance(s, str) and re.fullmatch(r"[0-9a-fA-F]{64}", s) for s in shas):
                        errs.append(f"{n}: \"sha256\" must be 64 hex digits (or a list of them)")
            plugins = e.get("plugins", list(files) if isinstance(files, dict) else [])
            if not isinstance(plugins, list) or not plugins or not all(_is_plugin_name(p) for p in plugins):
                errs.append("\"plugins\" must be a list of plugin file names")
            elif isinstance(files, dict):
                low = {k.lower() for k in files}
                miss = [p for p in plugins if p.lower() not in low]
                if miss:
                    errs.append(f"no hash given for {', '.join(miss)}")
        if errs:
            bad += 1
            _bad_entry(ctx, "accept", path, i, errs, fix)
            continue
        st = {"entry": i + 1, "plugins": plugins, "files": list(files), "changed": [], "missing": [],
              "date": e.get("date"), "why": e.get("why"), "by": e.get("by")}
        if not all(n.lower() in on or is_official(n) for n in files):
            st["status"] = "inactive"
            out.append(st)
            continue
        for n, fp in files.items():
            p = _plugin_path(lo, n)
            sha, size = _sha256(p, cache) if p else (None, None)
            if not sha:
                st["missing"].append(n)
                continue
            ok = [s.lower() for s in (fp["sha256"] if isinstance(fp["sha256"], list) else [fp["sha256"]])]
            if sha not in ok:
                st["changed"].append({"plugin": n, "old": ok[-1], "new": sha, "old_size": fp.get("size"),
                                      "new_size": size})
        st["status"] = "missing" if st["missing"] else "expired" if st["changed"] else "valid"
        log.info(SECTION, f"accept: entry {i + 1} {' + '.join(plugins)}: {st['status']}")
        out.append(st)
        when = st["date"] or "an earlier date"
        if st["status"] == "expired":
            names = ", ".join(c["plugin"] for c in st["changed"])
            ctx.report.add(Finding(
                "caution", SECTION, f"You accepted the clash between {' + '.join(plugins)} on {when}, but {names} "
                f"changed since then, so the acceptance no longer applies.",
                fix=f"Look at the clash again. If it's still fine, put the new sha256 in entry {i + 1} of {path} "
                    "(shown in this finding's data).",
                plugins=list(plugins), data={"file": path, "entry": i + 1, "changed": st["changed"]}))
        elif st["status"] == "missing":
            ctx.report.add(Finding(
                "note", SECTION, f"Your acceptance of {' + '.join(plugins)} ({when}) can't be checked: "
                f"{', '.join(st['missing'])} wasn't found.", plugins=list(plugins),
                data={"file": path, "entry": i + 1}))
    return out, {"status": "done", "file": path, "entries": len(entries), "bad_entries": bad,
                 "valid": sum(1 for s in out if s["status"] == "valid"),
                 "expired": sum(1 for s in out if s["status"] == "expired")}


def _acceptance_for(accepted, names):
    want = {n.lower() for n in names}
    for a in accepted:
        if {p.lower() for p in a["plugins"]} == want:
            return a
    return None


# =====================================================================================================================
# part 2: --clashes
# =====================================================================================================================
CLASH_FIELDS = ("plugins", "min", "level", "note", "fixed_by", "link", "settled_if_current")


def _current_errors(sc):
    """Shape errors of a settled_if_current value: {"plugin": "<plugin>", "sha256": "<64 hex>" or [...]}."""
    if not isinstance(sc, dict) or set(sc) - {"plugin", "sha256"}:
        return ["\"settled_if_current\" must be {\"plugin\": \"<plugin>\", \"sha256\": \"<64 hex digits>\"}"]
    errs = []
    if not _is_plugin_name(sc.get("plugin")):
        errs.append("\"settled_if_current\" needs \"plugin\", a plugin file name")
    shas = sc.get("sha256")
    shas = shas if isinstance(shas, list) else [shas]
    if not shas or not all(isinstance(s, str) and re.fullmatch(r"[0-9a-fA-F]{64}", s) for s in shas):
        errs.append("\"settled_if_current\" needs \"sha256\": 64 hex digits (or a list of them)")
    return errs


def _current_state(lo, sc, real, cache):
    """("current" | "not-current", sentence) for a settled_if_current value: current when its plugin is enabled and
    the installed file's SHA-256 is one of the hashes given."""
    name = sc["plugin"]
    if name.lower() not in real:
        return "not-current", f" {name} settles it when it's the build this entry names, but it isn't enabled."
    name = real[name.lower()]
    path = _plugin_path(lo, name)
    sha = _sha256(path, cache)[0] if path else None
    if sha is None:
        return "not-current", f" {name} settles it when it's the build this entry names, but its file can't be read."
    want = sc["sha256"] if isinstance(sc["sha256"], list) else [sc["sha256"]]
    if sha.lower() in {s.lower() for s in want}:
        return "current", f"{name} is the build this entry names"
    return "not-current", f" {name} isn't the build this entry names: install the current one."


def read_clashes(ctx, path, accepted, cache):
    entries, failed = _load_json(ctx, "clashes", path, "--clashes", "clashes")
    if failed:
        return [], failed
    lo = ctx.load_order
    order = lo.enabled()
    pos = {n.lower(): i for i, n in enumerate(order)}
    real = {n.lower(): n for n in order}
    fix = ("Each entry needs \"plugins\" (a list of plugin file names), \"level\" (problem, caution or pick-one) "
           "and \"note\"; \"min\", \"fixed_by\", \"link\" and \"settled_if_current\" are optional. See the "
           "README for the format.")
    out, bad = [], 0
    for i, e in enumerate(entries):
        errs = _entry_errors(e, CLASH_FIELDS, ("plugins", "level", "note"))
        if not errs:
            ps = e["plugins"]
            if not isinstance(ps, list) or not ps or not all(_is_plugin_name(p) for p in ps):
                errs.append("\"plugins\" must be a non-empty list of plugin file names (.esm, .esp or .esl)")
            elif len({p.lower() for p in ps}) != len(ps):
                errs.append("\"plugins\" names the same plugin twice")
            if e["level"] not in LEVELS:
                errs.append(f"\"level\" is {e['level']!r}; use problem, caution or pick-one")
            if not isinstance(e["note"], str) or not e["note"].strip():
                errs.append("\"note\" must be a sentence saying what goes wrong")
            mn = e.get("min", len(ps) if isinstance(ps, list) else 0)
            if not isinstance(mn, int) or isinstance(mn, bool) or not (1 <= mn <= max(1, len(ps) if isinstance(ps, list) else 1)):
                errs.append("\"min\" must be a whole number from 1 to the number of plugins")
            fb = e.get("fixed_by")
            if fb is not None and not _is_plugin_name(fb):
                errs.append("\"fixed_by\" must be a plugin file name")
            if "link" in e and not isinstance(e["link"], str):
                errs.append("\"link\" must be text")
            if "settled_if_current" in e:
                errs += _current_errors(e["settled_if_current"])
        if errs:
            bad += 1
            _bad_entry(ctx, "clashes", path, i, errs, fix)
            continue
        present = [real[p.lower()] for p in ps if p.lower() in pos]
        mn = e.get("min", len(ps))
        if len(present) < mn:
            log.info(SECTION, f"clashes: entry {i + 1}: {len(present)} of {len(ps)} enabled, needs {mn}: not active")
            continue
        level = LEVELS[e["level"]]
        fb = e.get("fixed_by")
        settled, extra = None, ""
        if fb:
            if fb.lower() in pos and all(pos[fb.lower()] > pos[p.lower()] for p in present):
                settled = f"{real[fb.lower()]} is enabled and loads after them"
            elif fb.lower() in pos:
                extra = f" The patch {real[fb.lower()]} is enabled but must load after all of them."
            else:
                extra = f" The patch {fb} would settle it, but it isn't enabled."
        current = None
        if "settled_if_current" in e:
            current, why = _current_state(lo, e["settled_if_current"], real, cache)
            too_early = bool(fb) and fb.lower() in pos and not settled  # the order still decides
            if current == "current" and not settled and not too_early:
                settled = why
            elif current == "not-current":
                extra += why
        acc = _acceptance_for(accepted, present)
        if not settled and acc and acc["status"] == "valid":
            settled = f"you accepted it on {acc['date'] or 'an earlier date'} and the files are unchanged"
        elif acc and acc["status"] == "expired":
            extra += " Your acceptance no longer matches the files (one of them changed)."
        item = {"entry": i + 1, "plugins": present, "level": e["level"], "note": e["note"], "fixed_by": fb,
                "link": e.get("link"), "settled": settled}
        if current:
            item["current"] = current
        out.append(item)
        names = " + ".join(present)
        if settled:
            ctx.report.add(Finding("note", SECTION, f"Known clash {names} is settled: {settled}. ({e['note']})",
                                   plugins=present, data={"file": path, "entry": i + 1,
                                                          "settled": True, **({"current": current} if current else {})}))
            continue
        if fb:
            fixtext = f"Enable {fb} and load it after {names}."
        elif e["level"] == "pick-one":
            fixtext = ("Pick the one you want to win and load it last (a --picks entry keeps that choice), or keep "
                       "only one of them enabled.")
        else:
            fixtext = ("Disable one of them, or find a patch. If you've checked it and can live with it, add it to "
                       "your --accept file (a ready entry is in this finding's data).")
        data = {"file": path, "entry": i + 1, "level": e["level"]}
        if current:
            data["current"] = current
        if e.get("link"):
            data["link"] = e["link"]
        ae = accept_entry(lo, present, cache, e["note"])
        if ae:
            data["accept_entry"] = ae
        ctx.report.add(Finding(level, SECTION, f"Known clash ({e['level']}): {names}. {e['note'].strip()}{extra}",
                               fix=fixtext, plugins=present, data=data))
    return out, {"status": "done", "file": path, "entries": len(entries), "bad_entries": bad, "active": len(out)}


# =====================================================================================================================
# part 4: --picks
# =====================================================================================================================
PICK_FIELDS = ("plugin", "after", "why", "date")


def read_picks(ctx, path):
    entries, failed = _load_json(ctx, "picks", path, "--picks", "picks")
    if failed:
        return [], failed
    on = {n.lower() for n in ctx.load_order.enabled()}
    fix = ("Each entry is {\"plugin\": \"<plugin to move>\", \"after\": \"<plugin it loads right after>\"}, with "
           "optional \"why\" and \"date\". See the README for the format.")
    good, bad, seen = [], 0, {}
    for i, e in enumerate(entries):
        errs = _entry_errors(e, PICK_FIELDS, ("plugin", "after"))
        if not errs:
            for k in ("plugin", "after"):
                if not _is_plugin_name(e[k]):
                    errs.append(f"\"{k}\" must be a plugin file name (.esm, .esp or .esl)")
            if not errs:
                if e["plugin"].lower() == e["after"].lower():
                    errs.append("a plugin can't load after itself")
                if is_official(e["plugin"]):
                    errs.append(f"{e['plugin']} is an official plugin; the game loads those in a fixed order")
                if e["plugin"].lower() in seen:
                    errs.append(f"{e['plugin']} already has a pick (entry {seen[e['plugin'].lower()]})")
            for k in ("why", "date"):
                if k in e and not isinstance(e[k], str):
                    errs.append(f"\"{k}\" must be text")
        if errs:
            bad += 1
            _bad_entry(ctx, "picks", path, i, errs, fix)
            continue
        seen[e["plugin"].lower()] = i + 1
        good.append({"entry": i + 1, "plugin": e["plugin"], "after": e["after"], "why": e.get("why"),
                     "date": e.get("date"), "active": e["plugin"].lower() in on and e["after"].lower() in on})
    # a loop (A after B, B after A, or longer) can't be met: drop every pick in it
    after = {g["plugin"].lower(): g["after"].lower() for g in good}
    in_loop = set()
    for start in after:
        path_, n = [], start
        while n in after and n not in path_:
            path_.append(n)
            n = after[n]
        if n in path_:
            in_loop.update(path_[path_.index(n):])
    if in_loop:
        loop = [g for g in good if g["plugin"].lower() in in_loop]
        for g in loop:
            bad += 1
        ctx.report.add(Finding(
            "caution", SECTION, f"{path}: entries {', '.join(str(g['entry']) for g in loop)} form a loop "
            f"({', '.join(g['plugin'] + ' after ' + g['after'] for g in loop)}), so none of them can be met. "
            "They were skipped.", fix="Remove or change one of those picks.",
            plugins=[g["plugin"] for g in loop], data={"file": path, "part": "picks"}))
        good = [g for g in good if g["plugin"].lower() not in in_loop]
    log.info(SECTION, f"picks: {path}: {len(good)} usable, {bad} skipped")
    return good, {"status": "done", "file": path, "entries": len(entries), "bad_entries": bad, "usable": len(good)}


# =====================================================================================================================
# part 1: LOOT masterlist
# =====================================================================================================================
def loot_paths(ctx, env):
    folder = os.path.join(env.get("LOCALAPPDATA") or "", "LOOT", "games", "Starfield")
    ml = getattr(ctx.args, "loot_masterlist", None) or (os.path.join(folder, "masterlist.yaml")
                                                         if env.get("LOCALAPPDATA") else None)
    ul = getattr(ctx.args, "loot_userlist", None) or (os.path.join(os.path.dirname(ml), "userlist.yaml")
                                                      if ml else None)
    return ml, ul


def load_loot_file(path, what):
    """(dict, None) or (None, (text, fix, extra data)). Reads nothing over MAX_LOOT_BYTES. Adds no finding, so the
    clashes section and the LOOT rules reader (loot_rules.py) can both use it."""
    try:
        if os.path.getsize(path) > MAX_LOOT_BYTES:
            return None, (f"LOOT's {what} is over {MAX_LOOT_BYTES // (1024 * 1024)} MB, so it wasn't read.",
                          "Let LOOT download the masterlist again (in LOOT: update masterlist).", {})
        with open(path, encoding="utf-8-sig") as f:
            d = read_yaml(f.read())
    except OSError as e:
        return None, (f"can't read LOOT's {what} ({e.strerror}).",
                      "Check that the file can be opened, or update the masterlist in LOOT.", {})
    except UnicodeDecodeError:
        return None, (f"LOOT's {what} isn't UTF-8 text.",
                      "Let LOOT download the masterlist again (in LOOT: update masterlist).", {})
    except YamlError as e:
        return None, (f"line {e.line}: {e.msg} (in LOOT's {what}).",
                      "If LOOT reads this file fine, this tool's YAML reader is missing a feature: please "
                      "report it with the --log file. Otherwise update the masterlist in LOOT.", {"line": e.line})
    if d is None:
        d = {}
    if not isinstance(d, dict) or not isinstance(d.get("plugins", []), list):
        return None, (f"LOOT's {what} doesn't have the expected 'plugins:' list.", "Update the masterlist in LOOT.", {})
    return d, None


def _read_loot_file(ctx, path, what):
    """(dict, None) or (None, failed status)."""
    d, err = load_loot_file(path, what)
    if err:
        text, fix, extra = err
        return None, _bad_file(ctx, "loot", path, text, fix, **extra)
    return d, None


def loot_entries(d, src):
    """[(match, entry, src)] for a LOOT file's plugin entries: match(name) -> bool. A regex name goes through the
    regex guard; a refused one, and those past MAX_REGEX_ENTRIES, are skipped. Returns (entries, unsafe count)."""
    entries, unsafe, nrx = [], 0, 0
    for k, e in enumerate(d.get("plugins") or []):
        if not isinstance(e, dict) or not isinstance(e.get("name"), str):
            log.info(SECTION, f"loot: {src}: plugin entry {k + 1} has no name, skipped")
            continue
        if _is_regex(e["name"]):
            nrx += 1
            if nrx > MAX_REGEX_ENTRIES:
                unsafe += 1
                log.info(SECTION, f"loot: {src}: entry {k + 1} skipped: over {MAX_REGEX_ENTRIES} regex entries")
                continue
            rx, why = safe_regex(e["name"])
            if rx is None:
                if regex_refusal(e["name"]):
                    unsafe += 1
                log.info(SECTION, f"loot: {src}: regex name {e['name'][:80]!r} skipped: {why}")
                continue
            entries.append((lambda n, rx=rx: rx_match(rx, n), e, src))
        else:
            low = e["name"].lower()
            entries.append((lambda n, low=low: n.lower() == low, e, src))
    return entries, unsafe


def _text(content, subs):
    if isinstance(content, list):
        pick = next((c for c in content if isinstance(c, dict) and c.get("lang") == "en"), None)
        pick = pick or next((c for c in content if isinstance(c, dict)), None)
        content = pick.get("text") if pick else None
    if not isinstance(content, str):
        return None
    for k, s in enumerate(subs or []):
        content = content.replace("{%d}" % k, str(s))
    return content.strip()


def _file_item(x):
    """(name, display, condition) from a req/inc item (a string or {name, display, condition})."""
    if isinstance(x, str):
        return x, x, None
    if isinstance(x, dict) and isinstance(x.get("name"), str):
        disp = x.get("display") if isinstance(x.get("display"), str) else x["name"]
        return x["name"], disp, x.get("condition")
    return None, None, None


def run_loot(ctx, env, accepted, cache):
    if getattr(ctx.args, "no_loot", False):  # `sort --no-loot` (the main run has no such option)
        log.info(SECTION, "loot: off (--no-loot)")
        return {"status": "skipped", "why": "--no-loot was given"}, {}
    ml, ul = loot_paths(ctx, env)
    if not ml or not os.path.isfile(ml):
        log.info(SECTION, f"loot: no masterlist at {ml}")
        return {"status": "skipped", "why": f"LOOT's masterlist wasn't found{' at ' + ml if ml else ''}. " + LOOT_HOW}, {}
    lists = []
    d, failed = _read_loot_file(ctx, ml, "masterlist")
    if failed:
        return failed, {}
    lists.append(d)
    used = [ml]
    if ul and os.path.isfile(ul):
        u, ufailed = _read_loot_file(ctx, ul, "userlist")
        if u is not None:
            lists.append(u)
            used.append(ul)
    lo = ctx.load_order
    cenv = Env(lo)
    entries, unsafe = [], 0
    for src, d in zip(used, lists):
        got, bad = loot_entries(d, src)
        entries += got
        unsafe += bad
    unknown = [0]

    def ok(cond, what):
        v, why = evaluate(cond, cenv)
        if v is None:
            unknown[0] += 1
            log.info(SECTION, f"loot: unknown condition, skipped: {what}: {cond!r} ({why})")
        return v is True

    enabled = [p for p in lo.plugins if p.enabled and not p.dropped]
    on = {p.name.lower() for p in enabled}
    real = {p.name.lower(): p.name for p in lo.plugins}
    inc_pairs, counts = set(), {"req": 0, "inc": 0, "msg": 0, "dirty": 0}
    results = {"masterlist": ml, "userlist": used[1] if len(used) > 1 else None, "incompatible": []}
    for p in enabled:
        for match, e, src in entries:
            if not match(p.name):
                continue
            for x in (e.get("req") or []) if isinstance(e.get("req"), list) else []:
                name, disp, cond = _file_item(x)
                if not name or not ok(cond, f"{p.name} req {name}"):
                    continue
                found = cenv.find(name)
                if found is None:
                    unknown[0] += 1
                    log.info(SECTION, f"loot: can't check {p.name}'s requirement {name}")
                    continue
                is_pl = name.lower().endswith(PLUGIN_EXT) and not _is_regex(name)
                if not found:
                    counts["req"] += 1
                    ctx.report.add(Finding("problem", SECTION, f"{p.name} needs {disp}, which isn't installed "
                                           "(LOOT masterlist).", fix=f"Install {disp}, or disable {p.name}.",
                                           plugins=[p.name], data={"source": "loot", "file": src, "requires": name}))
                elif is_pl and name.lower() not in on:
                    counts["req"] += 1
                    ctx.report.add(Finding("problem", SECTION, f"{p.name} needs {disp}, which is installed but not "
                                           "enabled (LOOT masterlist).", fix=f"Enable {real.get(name.lower(), name)}, "
                                           f"or disable {p.name}.", plugins=[p.name],
                                           data={"source": "loot", "file": src, "requires": name}))
            for x in (e.get("inc") or []) if isinstance(e.get("inc"), list) else []:
                name, disp, cond = _file_item(x)
                if not name or not ok(cond, f"{p.name} inc {name}"):
                    continue
                is_pl = name.lower().endswith(PLUGIN_EXT) and not _is_regex(name)
                if is_pl:
                    hit = name.lower() in on
                    other = real.get(name.lower(), name)
                else:
                    found = cenv.find(name)
                    if found is None:
                        unknown[0] += 1
                        continue
                    hit, other = bool(found), disp
                if not hit:
                    continue
                pair = frozenset((p.name.lower(), other.lower()))
                if pair in inc_pairs:
                    continue
                inc_pairs.add(pair)
                counts["inc"] += 1
                both = [p.name, other] if is_pl else [p.name]
                results["incompatible"].append(both)
                acc = _acceptance_for(accepted, both) if is_pl else None
                if acc and acc["status"] == "valid":
                    ctx.report.add(Finding("note", SECTION, f"LOOT lists {p.name} and {other} as incompatible; you "
                                           f"accepted that on {acc['date'] or 'an earlier date'} and the files are "
                                           "unchanged.", plugins=both, data={"source": "loot", "settled": True}))
                    continue
                data = {"source": "loot", "file": src}
                ae = accept_entry(lo, both, cache) if is_pl else None
                if ae:
                    data["accept_entry"] = ae
                ctx.report.add(Finding("problem", SECTION, f"{p.name} and {other} don't work together (LOOT's "
                                       "masterlist lists them as incompatible), and both are "
                                       f"{'enabled' if is_pl else 'installed'}.",
                                       fix=f"Disable or remove one of them.", plugins=both, data=data))
            for x in (e.get("msg") or []) if isinstance(e.get("msg"), list) else []:
                if not isinstance(x, dict) or x.get("type") not in ("say", "warn", "error"):
                    continue
                if x["type"] == "say" or not ok(x.get("condition"), f"{p.name} msg"):
                    continue
                subs = x.get("subs") if isinstance(x.get("subs"), list) else []
                t = _text(x.get("content"), subs)
                if not t:
                    continue
                counts["msg"] += 1
                ctx.report.add(Finding("problem" if x["type"] == "error" else "caution", SECTION,
                                       f"LOOT's masterlist on {p.name}: {t}",
                                       fix="Do what LOOT's message says (LOOT shows the same message).",
                                       plugins=[p.name], data={"source": "loot", "file": src, "type": x["type"]}))
            for x in (e.get("dirty") or []) if isinstance(e.get("dirty"), list) else []:
                if not isinstance(x, dict) or not isinstance(x.get("crc"), int) or not p.path:
                    continue
                crc = cenv.crc(p.path)
                if crc is None or crc != x["crc"] & 0xFFFFFFFF:
                    continue
                counts["dirty"] += 1
                bits = [f"{x[k]} {label}" for k, label in (("itm", "identical-to-master records"),
                                                           ("udr", "deleted references"),
                                                           ("nav", "deleted navmeshes"))
                        if isinstance(x.get(k), int) and x[k]]
                util = x.get("util") if isinstance(x.get("util"), str) else "xEdit"
                fix = f"Clean it with {util} (Quick Auto Clean), or get a cleaned version from the mod's author."
                if isinstance(x.get("nav"), int) and x["nav"]:
                    fix += " Deleted navmeshes can't be fixed by auto-cleaning; ask the author."
                ctx.report.add(Finding("caution", SECTION, f"LOOT's masterlist says this exact copy of {p.name} "
                                       f"(CRC32 {crc:08X}) needs cleaning" + (f": {', '.join(bits)}." if bits else "."),
                                       fix=fix, plugins=[p.name], data={"source": "loot", "file": src, "crc": crc}))
    unsafe += cenv.unsafe
    log.info(SECTION, f"loot: {len(entries)} entries read from {', '.join(used)}; found {counts}; "
                      f"{unknown[0]} unknown condition(s) skipped")
    st = {"status": "done", "file": ml, "userlist": results["userlist"], "entries": len(entries),
          "unknown_conditions": unknown[0], **counts}
    if unsafe:  # only when there are some, so a run without them reports exactly what 0.2.3 did
        log.info(SECTION, f"loot: {unsafe} LOOT entries skipped: unsafe pattern")
        st["unsafe_patterns"] = unsafe
    return st, results


# =====================================================================================================================
# run
# =====================================================================================================================
def run(ctx, env=None):
    if env is None:  # tests pass env (a dict like os.environ); the command line uses the real one
        env = getattr(ctx, "env", None) or os.environ
    args = ctx.args
    cache = {}
    parts = {}
    accepted = []
    if getattr(args, "accept", None):
        accepted, parts["accept"] = read_accept(ctx, args.accept, cache)
    else:
        parts["accept"] = {"status": "skipped", "why": "no --accept file given"}
    parts["loot"], loot = run_loot(ctx, env, accepted, cache)
    known = []
    if getattr(args, "clashes", None):
        known, parts["clashes"] = read_clashes(ctx, args.clashes, accepted, cache)
    else:
        parts["clashes"] = {"status": "skipped", "why": "no --clashes file given"}
    if getattr(args, "picks", None):
        ctx.results["picks"], parts["picks"] = read_picks(ctx, args.picks)
    else:
        ctx.results["picks"] = []
        parts["picks"] = {"status": "skipped", "why": "no --picks file given"}
    ctx.results[SECTION] = {"accepted": accepted, "known": known, "loot": loot}
    failed = [k for k, v in parts.items() if v["status"] == "failed"]
    st = {"status": "done", "parts": parts}
    if failed:
        st["why"] = "couldn't use: " + "; ".join(parts[k]["why"] for k in failed)
    return st
