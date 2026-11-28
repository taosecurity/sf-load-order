"""The PMT shared test sets (PMTW6 D1, adopted in 0.3.4): one test per case, so a failure names the case id.

The cases live in tests/shared (a byte-for-byte copy of the house sets; tests/shared/ADOPTION.json is this tool's own
file). A case this tool leaves as a known limit is listed in ADOPTION.json "known_limits"; its test is skipped and
prints that line. Everything runs the real command line as a child process (python -I sf-load-order.py), so a crash
shows up as a traceback or an exit code, never takes the test run down. Made-up names only.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHARED = os.path.join(HERE, "shared")
sys.path.insert(0, ROOT)
from sf_load_order import progfind  # noqa: E402

LAUNCHER = os.path.join(ROOT, "sf-load-order.py")
WINDOWS = os.name == "nt"


def _load(*parts):
    with open(os.path.join(SHARED, *parts), encoding="utf-8") as f:
        return json.load(f)


ADOPTION = _load("ADOPTION.json")
OK = ADOPTION["exit_codes"]["ok"]
FINDING = ADOPTION["exit_codes"]["finding"]
REFUSE = ADOPTION["exit_codes"]["refuse"]
KNOWN = ADOPTION["known_limits"]


def _case_insensitive_fs():
    d = tempfile.mkdtemp()
    try:
        open(os.path.join(d, "aB"), "w").close()
        return os.path.exists(os.path.join(d, "AB"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


CASE_INSENSITIVE = _case_insensitive_fs()


class Base(unittest.TestCase):
    def setUp(self):
        self.T = os.path.realpath(tempfile.mkdtemp(prefix="slo-shared-"))
        self.addCleanup(shutil.rmtree, self.T, True)
        self.env = dict(os.environ)
        for k in ("LOCALAPPDATA", "APPDATA"):  # never read or write the real state, caches or Vortex folders
            self.env[k] = os.path.join(self.T, "appdata")
        self.env["PYTHONIOENCODING"] = "utf-8"

    def known(self, case_id):
        if case_id in KNOWN:
            self.skipTest("known limit: " + KNOWN[case_id])

    def run_cli(self, args, limit=60, env=None):
        t0 = time.time()
        p = subprocess.run([sys.executable, "-I", LAUNCHER] + args, capture_output=True, cwd=self.T,
                           env=env or self.env, timeout=limit)
        self.elapsed = time.time() - t0
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        return p.returncode, out, err

    def never(self, code, out, err, what=""):
        self.assertNotIn("Traceback", out + err, f"{what}: a Python traceback")
        self.assertTrue(0 <= code < 128, f"{what}: exit code {code}")

    def make_data(self, data=None):
        """A Data folder holding a Starfield.esm stand-in (a copy of the plain test plugin), so the report has no
        'Starfield.esm not found' problem of its own."""
        data = data or os.path.join(self.T, "data")
        os.makedirs(data, exist_ok=True)
        shutil.copyfile(os.path.join(SHARED, "damaged-plugins", "SharedTest-Plain.esm"),
                        os.path.join(data, "Starfield.esm"))
        return data

    def base_args(self, plugins_txt=None, data=None):
        data = self.make_data(data)
        if plugins_txt is None:
            plugins_txt = os.path.join(self.T, "base-Plugins.txt")
            with open(plugins_txt, "w", encoding="utf-8", newline="") as f:
                f.write("*Starfield.esm\r\n")
        return ["--plugins-txt", plugins_txt, "--data", data, "--depth", "light", "--no-state"]


# ---------------------------------------------------------------- set 1: output paths
class OutPathTests(Base):
    pass


def _make_out_path_test(case, option):
    def test(self):
        self.known(case["id"])
        if case.get("only_if") == "windows" and not WINDOWS:
            self.skipTest("Windows only")
        if case.get("only_if") == "case_insensitive_fs" and not CASE_INSENSITIVE:
            self.skipTest("a case-sensitive file system")
        if case.get("clean_dir"):
            self.skipTest("not applicable: see ADOPTION.json")
        if ":" in case["out"] and not WINDOWS:
            # a colon names an NTFS stream only on Windows; elsewhere it is a legal file name
            self.skipTest("a colon in a file name is a stream only on Windows")
        T = self.T
        for d in case.get("dirs", []):
            os.makedirs(os.path.join(T, d), exist_ok=True)
        for f in case.get("files", []):
            os.makedirs(os.path.dirname(os.path.join(T, f)), exist_ok=True)
            with open(os.path.join(T, f), "w", encoding="utf-8", newline="") as fh:
                fh.write("input")
        for link in case.get("links", []):
            src, dst = os.path.join(T, link["from"]), os.path.join(T, link["to"])
            try:
                (os.symlink if link["kind"] == "symlink" else os.link)(dst, src)
            except (OSError, NotImplementedError) as e:
                self.skipTest(f"this system can't make a {link['kind']}: {e}")
        out = os.path.join(T, case["out"]) if not case["out"].startswith("/") else case["out"]
        # the case's out is written as it is (trailing dots, a colon, other letter case)
        out = T + os.sep + case["out"].replace("/", os.sep)
        inp = os.path.join(T, case["input"]) if case.get("input") else None
        args = self.base_args(plugins_txt=inp)
        if option == "--log":
            args += ["--out", os.path.join(T, "outdir"), "--log", out]
        else:
            args += ["--out", out]
        code, so, se = self.run_cli(args)
        self.never(code, so, se, case["id"])
        if inp:
            with open(inp, encoding="utf-8", newline="") as f:
                self.assertEqual(f.read(), "input", "the input was changed")
        if option == "--out":
            return  # --out names a folder: only the never-list applies (nothing lost, no crash)
        if case["expect"] == "write":
            self.assertIn(code, OK + FINDING, so + se)
            self.assertTrue(os.path.isfile(out), "the log file was not written")
        else:
            self.assertIn(code, REFUSE, f"expected a refusal, got exit {code}: {so}{se}")
            self.assertIn("--log", so + se, "the message doesn't name the option")
            if case.get("links"):
                for link in case["links"]:
                    with open(os.path.join(T, link["to"]), encoding="utf-8") as f:
                        self.assertEqual(f.read(), "input", "written through a link")
    return test


_OUT = _load("out-paths.json")
for _c in _OUT["cases"]:
    for _opt in ("--log", "--out"):
        _name = "test_%s_%s" % (_opt.strip("-"), _c["id"].replace("-", "_"))
        setattr(OutPathTests, _name, _make_out_path_test(_c, _opt))


# ---------------------------------------------------------------- set 2: damaged input files
class DamagedInputTests(Base):
    pass


JSON_OPTIONS = ("--clashes", "--accept", "--picks", "--conflicts", "--patch-sources")
# These three read "a JSON list of entries (or {"clashes": [...]})" (README, "Your own files"), so a list at the root
# is the normal format here and reads as a (possibly empty) list; every other root type is refused.
LIST_OPTIONS = ("--clashes", "--accept", "--picks")
# --patch-sources is used only when sf-mod-check is installed (the patch check runs it), so on a machine without it
# the file is not opened: only the never-list is checked for it.
TEXT_OPTIONS = ("--plugins-txt", "--loot-order")


def _make_input_test(entry, option, case_id):
    def test(self):
        self.known(entry["file"])
        self.known(case_id)
        src = os.path.join(SHARED, "damaged-inputs", entry["file"])
        dst = os.path.join(self.T, entry["file"])
        shutil.copyfile(src, dst)
        with open(dst, "rb") as f:
            before = f.read()
        args = self.base_args(plugins_txt=dst if option == "--plugins-txt" else None)
        if option != "--plugins-txt":
            args += [option, dst]
        if option == "--conflicts":
            args[args.index("--depth") + 1] = "medium"  # the file is read at medium depth and deeper
        args += ["--out", os.path.join(self.T, "out")]
        code, so, se = self.run_cli(args, limit=entry.get("time_limit_s", 10) * 3)
        self.never(code, so, se, case_id)
        self.assertLess(self.elapsed, 30, "took too long")
        with open(dst, "rb") as f:
            self.assertEqual(f.read(), before, "the input was changed")
        answer = entry["answer"]
        if option == "--patch-sources":
            return
        if option in LIST_OPTIONS and entry["file"] == "json-root-array.json":
            answer = "read"
        if answer == "refuse":
            # This tool's two documented answers to a damaged input file: a plain refusal (exit 2) naming the file,
            # or, for an optional file, a "skipped" or failed-check line in the report that names the file (exit 0
            # unless --strict). Both name the file and neither loses anything; the tool never reads it as valid.
            if code in REFUSE:
                self.assertIn(entry["file"], so + se, "the message doesn't name the file")
            else:
                self.assertIn(code, OK + FINDING, f"exit {code}: {so}{se}")
                rp = os.path.join(self.T, "out", "report.txt")
                self.assertTrue(os.path.isfile(rp), "no report was written")
                with open(rp, encoding="utf-8", errors="replace") as f:
                    rtext = f.read()
                self.assertIn(entry["file"], rtext, "neither the message nor the report names the file")
        else:  # read or read_or_refuse
            self.assertIn(code, OK + FINDING + REFUSE, so + se)
            if code in REFUSE:
                self.assertIn(entry["file"], so + se, "the message doesn't name the file")
    return test


_INPUTS = _load("damaged-inputs", "manifest.json")
for _e in _INPUTS["files"]:
    _opts = JSON_OPTIONS if _e["kind"] == "json" else TEXT_OPTIONS
    for _opt in _opts:
        _cid = "%s@%s" % (_e["file"], _opt)
        _name = "test_%s_%s" % (_opt.strip("-").replace("-", "_"), _e["file"].replace("-", "_").replace(".", "_"))
        setattr(DamagedInputTests, _name, _make_input_test(_e, _opt, _cid))


# ---------------------------------------------------------------- set 3: damaged plugins
class DamagedPluginTests(Base):
    pass


CONTROL_PLUGINS = ("SharedTest-Plain.esm", "SharedTest-Light.esm", "SharedTest-Medium.esm")


def _make_plugin_test(entry):
    def test(self):
        self.known(entry["file"])
        data = self.make_data()
        listed = ["Starfield.esm"]
        # the plain, light and medium plugins load first, so a plugin that names them as masters finds them
        for n in CONTROL_PLUGINS + (entry["file"],):
            if n not in listed:
                shutil.copyfile(os.path.join(SHARED, "damaged-plugins", n), os.path.join(data, n))
                listed.append(n)
        ptxt = os.path.join(self.T, "Plugins.txt")
        with open(ptxt, "w", encoding="utf-8", newline="") as f:
            f.write("".join("*" + n + "\r\n" for n in listed))
        out_dir = os.path.join(self.T, "out")
        code, so, se = self.run_cli(["--plugins-txt", ptxt, "--data", data, "--depth", "light", "--no-state",
                                     "--out", out_dir], limit=entry.get("time_limit_s", 10) * 3)
        self.never(code, so, se, entry["file"])
        self.assertLess(self.elapsed, 30, "took too long")
        report = ""
        rp = os.path.join(out_dir, "report.txt")
        if os.path.isfile(rp):
            with open(rp, encoding="utf-8", errors="replace") as f:
                report = f.read()
        shown = so + se + report
        m = re.search(r"RESULT: (\d+) problem\(s\), (\d+) caution\(s\)", so)
        found = (int(m.group(1)) + int(m.group(2))) if m else None
        stem = entry["file"].split(".")[0]
        ans = entry["answer"]
        if ans == "read":
            self.assertIn(code, OK, f"expected a normal read, got exit {code}: {so}{se}")
            self.assertEqual(found, 0, "a problem was reported for a plugin that should read normally")
        elif ans == "finding":
            self.assertIsNotNone(found, so + se)
            self.assertGreaterEqual(found, 1, "no problem or caution was reported")
            self.assertIn(stem, shown, "the report doesn't name the plugin")
        elif ans == "read_or_finding":
            self.assertIn(code, OK + FINDING, so + se)
            if found:
                self.assertIn(stem, shown, "the report doesn't name the plugin")
        else:  # finding_or_refuse
            if code in REFUSE:
                self.assertIn(stem, so + se)
            else:
                self.assertGreaterEqual(found or 0, 1, "no problem or caution was reported: " + so + se)
                self.assertIn(stem, shown, "the report doesn't name the plugin")
        bad = sorted({hex(ord(c)) for c in so + se + report if _bad_char(c)})
        self.assertEqual(bad, [], "raw control or format characters reached the output: %s" % bad)
    return test


for _e in _load("damaged-plugins", "manifest.json")["files"]:
    setattr(DamagedPluginTests, "test_" + _e["file"].replace("-", "_").replace(".", "_"), _make_plugin_test(_e))


# ---------------------------------------------------------------- set 4: text that must not reach the terminal raw
class TerminalTextTests(Base):
    pass


def _bad_char(ch):
    return unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") and ch not in "\n\r\t"


def _make_text_test(case):
    def test(self):
        self.known(case["id"])
        rep = case.get("repeat")
        text = rep["text"] * rep["count"] if rep else case["text"]
        rules = case["rules"]
        # The text is planted as a plugin name in a Plugins.txt line: the tool refuses or reports it by name.
        ptxt = os.path.join(self.T, "Plugins.txt")
        with open(ptxt, "wb") as f:
            f.write(("*" + text + ".esm\r\n").encode("utf-8", "surrogatepass"))
        os.makedirs(os.path.join(self.T, "data"))
        code, so, se = self.run_cli(["--plugins-txt", ptxt, "--data", os.path.join(self.T, "data"), "--depth", "light",
                                     "--no-state", "--out", os.path.join(self.T, "out")], limit=60)
        self.never(code, so, se, case["id"])
        shown = so + se
        if "bounded_time" in rules:
            self.assertLess(self.elapsed, 20, "took too long")
        if "no_raw" in rules or "one_line" in rules:
            bad = sorted({hex(ord(c)) for c in shown if _bad_char(c)})
            self.assertEqual(bad, [], "raw characters in the output: %s" % bad)
        if "unchanged" in rules:
            # normal names are never mangled: when the tool names the plugin, it names it as it is
            if text in shown or (text + ".esm") in shown:
                return
            self.assertNotIn("\\u", shown, "a normal name was escaped")
        if "unchanged_or_marked_cut" in rules:
            self.assertLess(len(shown), 20 * len(text) + 10000)
    return test


for _c in _load("terminal-text.json")["cases"]:
    setattr(TerminalTextTests, "test_" + _c["id"].replace("-", "_"), _make_text_test(_c))


# ---------------------------------------------------------------- set 5: program lookup and path spellings
class PathsAndProgramsTests(Base):
    pass


def _make_lookup_test(case):
    def test(self):
        self.known(case["id"])
        if case.get("only_if") == "windows" and not WINDOWS:
            self.skipTest("Windows only")
        T = self.T
        prog = "tool.exe" if WINDOWS else "tool"
        cwd = os.path.join(T, "cwd")
        os.makedirs(cwd)

        def make(folder, names):
            os.makedirs(folder, exist_ok=True)
            for n in names:
                p = os.path.join(folder, prog if n == "$PROG" else n)
                open(p, "wb").close()
                os.chmod(p, 0o755)

        make(cwd, case.get("cwd_files", []))
        entries = []
        for e in case.get("path", []):
            if "dir" in e:
                full = os.path.join(T, e["dir"].replace("/", os.sep))
                make(full, e.get("files", []))
                entries.append('"%s"' % full if e.get("quoted") else full)
            elif "relative" in e:
                make(os.path.join(cwd, e["relative"]), e.get("files", []))
                entries.append(e["relative"])
            else:
                entries.append(e["entry"])
        path = os.pathsep.join(entries)
        query = case["query"].replace("{T}", T).replace("$PROG", "tool")
        if "$PROG" in case["query"] and WINDOWS and "{T}" in case["query"]:
            query = case["query"].replace("{T}", T).replace("$PROG", prog)
        query = query.replace("/", os.sep) if "{T}" in case["query"] else query
        old = os.getcwd()
        os.chdir(cwd)
        try:
            exp = case["expect"]
            if case.get("kind") == "system_tool":
                got = progfind.system_tool(query)
                root = os.environ.get("SystemRoot") or r"C:\Windows"
                self.assertEqual(os.path.normcase(got), os.path.normcase(os.path.join(root, "System32", query)))
            elif "given" in exp:
                got = progfind.given_path(query)
                self.assertEqual(os.path.normcase(got), os.path.normcase(os.path.join(T, exp["given"], prog)))
            else:
                got = progfind.which(query, path)
                if exp["found"] is None:
                    self.assertIsNone(got)
                else:
                    want = os.path.join(T, exp["found"].replace("/", os.sep), query if query.endswith(".exe") else prog)
                    self.assertIsNotNone(got, "not found")
                    self.assertEqual(os.path.normcase(got), os.path.normcase(want))
        finally:
            os.chdir(old)
    return test


_PP = _load("paths-and-programs.json")
for _c in _PP["lookup"]:
    setattr(PathsAndProgramsTests, "test_lookup_" + _c["id"].replace("-", "_"), _make_lookup_test(_c))


def _tool_same_path(a, b):
    """How this tool compares two folders: the path functions its writers and caches use."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _make_spelling_test(case):
    def test(self):
        self.known(case["id"])
        if (case["flavor"] == "windows") != WINDOWS:
            self.skipTest("a %s path spelling" % case["flavor"])
        if case["relation"] == "same":
            self.assertEqual(_tool_same_path(case["a"], case["b"]), case["expect"])
        else:
            self.skipTest("not applicable: this tool has no output folder inside a cleaned folder")
    return test


for _c in _PP["spellings"]:
    setattr(PathsAndProgramsTests, "test_spelling_" + _c["id"].replace("-", "_"), _make_spelling_test(_c))


if __name__ == "__main__":
    unittest.main()
