"""Skeleton tests: options, exit codes, the report model and the fixtures. Synthetic data only."""
import io
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, model  # noqa: E402
from tests import fixtures  # noqa: E402


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Options(unittest.TestCase):
    def test_unknown_option_exits_2(self):
        code, _, err = run_cli("--no-such-option")
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments", err)

    def test_missing_value_exits_2(self):
        self.assertEqual(run_cli("--plugins-txt")[0], 2)

    def test_abbreviation_refused(self):
        self.assertEqual(run_cli("--plug", "x")[0], 2)

    def test_version(self):
        code, out, _ = run_cli("--version")
        self.assertEqual(code, 0)
        self.assertIn("sf-load-order", out)

    def test_bad_log_file_exits_2(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, _ = run_cli("--log", os.path.join(t, "missing", "x.log"), "--out", t)
        self.assertEqual(code, 2)
        self.assertIn("CANNOT CHECK", out)


class Runs(unittest.TestCase):
    """Whole runs on a made-up game (--depth light: no sf-mod-check)."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name

    def tearDown(self):
        self._t.cleanup()

    def game(self, root):
        return fixtures.game(root, {"ModA.esm": {"masters": ["Starfield.esm"]}})

    def test_log_inside_a_new_out_folder(self):
        data, pt = self.game(os.path.join(self.t, "Game"))
        out = os.path.join(self.t, "new", "report")
        code, stdout, _ = run_cli("--plugins-txt", pt, "--data", data, "--depth", "light", "--no-state",
                                  "--out", out, "--log", os.path.join(out, "run.log"))
        self.assertEqual(code, 0, stdout)
        self.assertGreater(os.path.getsize(os.path.join(out, "run.log")), 0)

    def test_no_report_written_exits_2(self):
        data, pt = self.game(os.path.join(self.t, "Game"))
        out = os.path.join(self.t, "out")
        with mock.patch("sf_load_order.report.write", return_value={}):
            code, stdout, _ = run_cli("--plugins-txt", pt, "--data", data, "--depth", "light", "--no-state",
                                      "--out", out)
        self.assertEqual(code, 2)
        self.assertIn("RESULT: CANNOT CHECK: couldn't write the report to " + out, stdout)
        self.assertIn("Fix:", stdout)

    def test_redirected_output_with_a_non_ansi_path(self):
        """stdout piped (a file or another program) and a path the Windows code page can't show: no crash."""
        root = os.path.join(self.t, "Łukasz")
        data, pt = self.game(os.path.join(root, "Game"))
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sf-load-order.py")
        p = subprocess.run([sys.executable, "-X", "utf8=0", "-I", script, "--plugins-txt", pt, "--data", data,
                            "--depth", "light", "--no-state", "--verbose", "--out", os.path.join(root, "out")],
                           env=env, capture_output=True, timeout=300)
        out = p.stdout.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0, out + p.stderr.decode("utf-8", "replace")[-2000:])
        self.assertIn("RESULT:", out)
        self.assertIn("Łukasz", out)


class LineEndings(unittest.TestCase):
    def test_build_rules_match_gitattributes(self):
        """build_release.py --allow-dirty and a build from the commit give CRLF to the same files."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, ".gitattributes")
        if not os.path.isfile(path):
            self.skipTest("no .gitattributes (it isn't in the release zip)")
        import build_release
        crlf = {line.split()[0].lstrip("*").lower() for line in read_text(path).splitlines()
                if "eol=crlf" in line and not line.startswith("#")}
        self.assertEqual(crlf, set(build_release.CRLF_SUFFIXES))


class Model(unittest.TestCase):
    def test_bad_level_refused(self):
        with self.assertRaises(ValueError):
            model.Finding("bad", "order", "x")

    def test_sorted_worst_first(self):
        r = model.Report("0")
        r.add(model.Finding("note", "order", "n"))
        r.add(model.Finding("problem", "archives", "p"))
        r.add(model.Finding("problem", "order", "p0"))
        self.assertEqual([f.text for f in r.sorted_findings()], ["p0", "p", "n"])
        d = r.to_dict()
        self.assertEqual(d["counts"], {"problem": 2, "caution": 0, "note": 1})
        self.assertEqual(d["schema"], model.SCHEMA_VERSION)


class Fixtures(unittest.TestCase):
    def test_plugin_header(self):
        with tempfile.TemporaryDirectory() as t:
            p = fixtures.plugin(os.path.join(t, "A.esm"), masters=["Starfield.esm"], kind="L",
                                records=[("KYWD", 0x01000800, "AKeyword")])
            b = read_bytes(p)
        self.assertEqual(b[:4], b"TES4")
        self.assertTrue(struct.unpack_from("<I", b, 8)[0] & fixtures.FLAG_LIGHT)
        self.assertIn(b"MAST", b)
        self.assertEqual(b.count(b"GRUP"), 1)

    def test_ba2_layout(self):
        with tempfile.TemporaryDirectory() as t:
            p = fixtures.ba2(os.path.join(t, "A - Main.ba2"), {"scripts\\a.pex": b"x", "meshes\\b.nif": ("zlib", b"y" * 50)})
            b = read_bytes(p)
        self.assertEqual(b[:4], b"BTDX")
        self.assertEqual(struct.unpack_from("<I", b, 12)[0], 2)

    def test_mo2_layout(self):
        with tempfile.TemporaryDirectory() as t:
            fixtures.mo2(t, "Default", [("ModA", True, {"ModA.esm": {"masters": ["Starfield.esm"]}}),
                                        ("ModB", False, {"textures\\x.dds": b"z"})])
            ml = read_text(os.path.join(t, "profiles", "Default", "modlist.txt")).splitlines()
            pt = read_text(os.path.join(t, "profiles", "Default", "plugins.txt"))
        self.assertEqual(ml[1:], ["-ModB", "+ModA"])
        self.assertIn("*ModA.esm", pt)


if __name__ == "__main__":
    unittest.main()
