"""C0 skeleton tests: options, exit codes, the report model and the fixtures. Synthetic data only."""
import io
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

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
