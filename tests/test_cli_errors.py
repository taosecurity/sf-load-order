"""Exit codes and safe input handling for the cases the pre-release review found. Synthetic data only."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, inputs, paths  # noqa: E402
from tests import fixtures  # noqa: E402


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class BrokenParts(unittest.TestCase):
    def test_check_that_cant_import_is_marked_failed(self):
        real = cli._module

        def fake(name):
            if name == "loose":
                raise ImportError("no module named loose (test)")
            return real(name)
        with tempfile.TemporaryDirectory() as t:
            data, ptxt = fixtures.game(os.path.join(t, "game"), {"A.esm": {"masters": ["Starfield.esm"]}})
            with mock.patch.object(cli, "_module", side_effect=fake):
                code, out, _ = run_cli("--plugins-txt", ptxt, "--data", data, "--depth", "light", "--no-state",
                                       "--out", os.path.join(t, "out"))
            self.assertEqual(code, 0)
            self.assertIn("1 check(s) failed: loose", out)
            self.assertTrue(os.path.isfile(os.path.join(t, "out", "report.txt")))

    def test_damaged_part_is_cannot_check(self):
        def fake(name):
            raise SyntaxError("invalid syntax (test)")
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(cli, "_module", side_effect=fake):
                code, out, _ = run_cli("--out", os.path.join(t, "out"))
        self.assertEqual(code, 2)
        self.assertIn("missing or broken", out)

    def test_report_writer_that_cant_load_says_reinstall(self):
        real = cli._module

        def fake(name):
            if name == "report":
                raise ImportError("no module named report (test)")
            return real(name)
        with tempfile.TemporaryDirectory() as t:
            data, ptxt = fixtures.game(os.path.join(t, "game"), {"A.esm": {"masters": ["Starfield.esm"]}})
            with mock.patch.object(cli, "_module", side_effect=fake):
                code, out, _ = run_cli("--plugins-txt", ptxt, "--data", data, "--depth", "light", "--no-state",
                                       "--out", os.path.join(t, "out"))
        self.assertEqual(code, 2)
        self.assertIn("unzip the release again", out)

    def test_inputs_that_cant_import_is_cannot_check(self):
        def fake(name):
            raise ImportError("no module named inputs (test)")
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(cli, "_module", side_effect=fake):
                code, out, _ = run_cli("--out", os.path.join(t, "out"))
        self.assertEqual(code, 2)
        self.assertIn("RESULT: CANNOT CHECK: part of sf-load-order is missing or broken", out)


class BadPaths(unittest.TestCase):
    def test_nul_in_out_is_exit_2(self):
        code, out, _ = run_cli("--out", "a\0b")
        self.assertEqual(code, 2)
        self.assertIn("can't create the output folder", out)

    def test_nul_in_log_is_exit_2(self):
        with tempfile.TemporaryDirectory() as t:
            code, out, _ = run_cli("--out", t, "--log", "a\0b.log")
        self.assertEqual(code, 2)
        self.assertIn("can't write the log file", out)

    def test_log_outside_out_records_the_out_failure(self):
        with tempfile.TemporaryDirectory() as t:
            blocker = os.path.join(t, "file")
            with open(blocker, "w") as f:
                f.write("x")
            log_path = os.path.join(t, "run.log")
            code, out, _ = run_cli("--out", os.path.join(blocker, "sub"), "--log", log_path)
            with open(log_path, encoding="utf-8") as f:
                logged = f.read()
        self.assertEqual(code, 2)
        self.assertIn("can't create the output folder", out)
        self.assertIn("can't create the output folder", logged)


class Names(unittest.TestCase):
    def test_plugin_names_that_change_meaning(self):
        for bad in ("*X.esm", "#X.esm", " X.esm", "X.esm ", "X\x07.esm", "", "a/b.esm", "a\\b.esm", "a\nb.esm"):
            self.assertTrue(inputs.bad_name(bad), repr(bad))
        for good in ("X.esm", "Mod - Part.esm", "Łódź.esm", "a*b.esm"):
            self.assertFalse(inputs.bad_name(good), repr(good))

    def test_disabled_star_name_from_vortex_is_left_out(self):
        with tempfile.TemporaryDirectory() as t:
            data, _ = fixtures.game(os.path.join(t, "game"), {"A.esm": {"masters": ["Starfield.esm"]}})
            vx = fixtures.vortex_loadorder(os.path.join(t, "lo.json"),
                                           [("Starfield.esm", True), ("A.esm", True), ("*X.esm", False),
                                            ("#P.esm", False)])
            code, _, _ = run_cli("--vortex-loadorder", vx, "--data", data, "--depth", "light", "--no-state",
                                 "--vortex-staging", os.path.join(t, "none"), "--out", os.path.join(t, "out"))
            with open(os.path.join(t, "out", "plugins.txt"), encoding="utf-8") as f:
                body = [ln.rstrip("\r\n") for ln in f][2:]  # after the game's two header comment lines
        self.assertEqual(code, 0)
        self.assertEqual([ln for ln in body if ln], ["*A.esm"])  # real case from Data; *X.esm and #P.esm left out

    def test_non_string_mod_id_doesnt_crash(self):
        import json
        with tempfile.TemporaryDirectory() as t:
            data, _ = fixtures.game(os.path.join(t, "game"), {"A.esm": {"masters": ["Starfield.esm"]}})
            vx = os.path.join(t, "lo.json")
            with open(vx, "w", encoding="utf-8") as f:
                json.dump([{"id": "a.esm", "name": "a.esm", "enabled": True, "modId": 12345}], f)
            code, out, _ = run_cli("--vortex-loadorder", vx, "--data", data, "--depth", "light", "--no-state",
                                   "--vortex-staging", t, "--out", os.path.join(t, "out"))
        self.assertEqual(code, 0, out)

    def test_unsafe_mo2_profile_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            fixtures.mo2(t, "Default", [("ModA", True, {"ModA.esm": {"masters": ["Starfield.esm"]}})])
            code, out, _ = run_cli("--mo2", t, "--profile", "..\\..\\x", "--out", os.path.join(t, "out"))
        self.assertEqual(code, 2)
        self.assertIn("isn't a single folder name", out)

    def test_mod_folder_names_stay_inside(self):
        for bad in ("..", ".", "...", ".. ", ". ", "Mod.", "Mod ", "..\\..\\Windows", "../x", "C:\\x", "C:x", "a\0b",
                    "", 123):
            self.assertFalse(paths.safe_component(bad), repr(bad))
        self.assertTrue(paths.safe_component("My Mod-123-1-0"))
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "outside"))
            staging = os.path.join(t, "staging")
            os.makedirs(staging)
            self.assertIsNone(paths.child_folder(staging, "..\\outside"))
            self.assertIsNone(paths.child_folder(staging, "../outside"))
            self.assertIsNone(paths.child_folder(staging, ".."))


class Console(unittest.TestCase):
    def test_unshowable_characters_are_escaped_not_fatal(self):
        import subprocess
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "Łukasz")
            env = dict(os.environ, PYTHONIOENCODING="cp1252")
            r = subprocess.run([sys.executable, "-I", os.path.join(here, "sf-load-order.py"), "--out", out,
                                "--plugins-txt", os.path.join(out, "missing.txt")], capture_output=True, env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(b"CANNOT CHECK", r.stdout)
        self.assertIn(b"\\u0141ukasz", r.stdout)  # cp1252 can't show it: escaped, the run doesn't crash


if __name__ == "__main__":
    unittest.main()
