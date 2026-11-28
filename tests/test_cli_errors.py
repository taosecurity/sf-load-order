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

    def test_log_refuses_load_order_files_no_option_names(self):
        # 0.3.4 code-review round 1: the game's own Plugins.txt (read with no source option), an MO2 profile's
        # files and the Vortex exports are read without the command line naming them, so --log refuses their names
        with tempfile.TemporaryDirectory() as t:
            for name in ("Plugins.txt", "loadorder.txt", "modlist.txt",
                         "vortex-starfield-My Profile-loadorder-20261121-190400.json"):
                target = os.path.join(t, name)
                with open(target, "w", encoding="utf-8") as f:
                    f.write("*ModA.esm\n")
                code, out, _ = run_cli("--out", os.path.join(t, "out"), "--log", target)
                with open(target, encoding="utf-8") as f:
                    self.assertEqual(f.read(), "*ModA.esm\n", name)
                self.assertEqual(code, 2, name)
                self.assertIn("load order file", out, name)

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

    def test_odd_mod_ids_mean_no_mod(self):
        import json
        with tempfile.TemporaryDirectory() as t:
            vx = os.path.join(t, "lo.json")
            with open(vx, "w", encoding="utf-8") as f:
                json.dump([{"name": "a.esm", "enabled": True, "modId": v} for v in (False, ["x"], {"k": 1}, "", " ")]
                          + [{"name": "b.esm", "enabled": True, "modId": v} for v in (7, 7.0, " 7 ")], f)
            got = [e[2] for e in inputs._vortex_entries(vx)]
        self.assertEqual(got, [None, None, None, None, None, "7", "7", " 7 "])  # raw: " 7 " is a folder name

    def test_render_bug_in_every_file_is_internal_error(self):
        from sf_load_order import report as report_mod

        def boom(*a, **k):
            raise TypeError("render bug (test)")
        with tempfile.TemporaryDirectory() as t:
            data, ptxt = fixtures.game(os.path.join(t, "game"), {"A.esm": {"masters": ["Starfield.esm"]}})
            with mock.patch.object(report_mod, "render_text", boom), mock.patch.object(report_mod, "render_html", boom), \
                    mock.patch.object(report_mod, "to_json", boom):
                code, out, _ = run_cli("--plugins-txt", ptxt, "--data", data, "--depth", "light", "--no-state",
                                       "--out", os.path.join(t, "out"))
        self.assertEqual(code, 2)
        self.assertIn("internal error stopped the report writer", out)
        self.assertNotIn("couldn't be written", out)  # no disk error, so only the bug is named

    def test_render_bug_plus_disk_error_names_both(self):
        """One file hits a render bug and the disk refuses the other two: the RESULT line names both (0.1.1)."""
        from sf_load_order import report as report_mod

        def boom(*a, **k):
            raise TypeError("render bug (test)")
        real_open = open

        def fake_open(path, mode="r", *a, **k):
            if "w" in mode and os.path.basename(str(path)) in ("report.txt", "report.json"):
                raise OSError(28, "No space left on device (test)")
            return real_open(path, mode, *a, **k)
        with tempfile.TemporaryDirectory() as t:
            data, ptxt = fixtures.game(os.path.join(t, "game"), {"A.esm": {"masters": ["Starfield.esm"]}})
            with mock.patch.object(report_mod, "render_html", boom), \
                    mock.patch("builtins.open", fake_open):
                code, out, _ = run_cli("--plugins-txt", ptxt, "--data", data, "--depth", "light", "--no-state",
                                       "--out", os.path.join(t, "out"))
        self.assertEqual(code, 2)
        self.assertIn("internal error stopped the report writer (TypeError: render bug (test))", out)
        self.assertIn("report.txt: No space left on device (test)", out)
        self.assertIn("report.json: No space left on device (test)", out)
        self.assertIn("make sure you can write to", out)

    def test_writer_bug_is_named_as_such(self):
        real = cli._module

        class Broken:
            @staticmethod
            def write(ctx):
                raise RuntimeError("boom (test)")

        def fake(name):
            return Broken if name == "report" else real(name)
        with tempfile.TemporaryDirectory() as t:
            data, ptxt = fixtures.game(os.path.join(t, "game"), {"A.esm": {"masters": ["Starfield.esm"]}})
            with mock.patch.object(cli, "_module", side_effect=fake):
                code, out, _ = run_cli("--plugins-txt", ptxt, "--data", data, "--depth", "light", "--no-state",
                                       "--out", os.path.join(t, "out"))
        self.assertEqual(code, 2)
        self.assertIn("internal error stopped the report writer", out)

    def test_unsafe_mo2_profile_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            fixtures.mo2(t, "Default", [("ModA", True, {"ModA.esm": {"masters": ["Starfield.esm"]}})])
            code, out, _ = run_cli("--mo2", t, "--profile", "..\\..\\x", "--out", os.path.join(t, "out"))
        self.assertEqual(code, 2)
        self.assertIn("isn't a single folder name", out)

    def test_mod_folder_names_stay_inside(self):
        for bad in ("..", ".", "...", ".. ", ". ", "..\\..\\Windows", "../x", "C:\\x", "C:x", "a\0b", "", 123):
            self.assertFalse(paths.safe_component(bad), repr(bad))
        windows_only = ("Mod.", "Mod ", "NUL", "con", "COM1", "CONIN$")  # trailing dots/spaces; bare devices
        self.assertTrue(paths.safe_component("COM0"))  # COM0 and LPT0 are plain names
        # a device name with an extension: a device on Windows 10, a real folder on Windows 11 and elsewhere
        for win11, want in ((True, True), (False, os.name != "nt")):
            with mock.patch.object(paths, "_windows_11_or_later", return_value=win11):
                self.assertEqual(paths.safe_component("Aux.Lighting"), want, f"win11={win11}")
                # COM/LPT with a superscript digit: a device before Windows 11, a real folder on Windows 11 (0.1.1)
                for sup in ("COM¹", "com²", "LPT³", "COM¹.x"):
                    self.assertEqual(paths.safe_component(sup), want, f"{sup!r} win11={win11}")
                # the real device names stay refused on every Windows
                for dev in ("COM1", "LPT3", "NUL", "con"):
                    self.assertEqual(paths.safe_component(dev), os.name != "nt", f"{dev!r} win11={win11}")
        for name in windows_only:
            self.assertEqual(paths.safe_component(name), os.name != "nt", repr(name))
        self.assertTrue(paths.safe_component("My Mod-123-1-0"))
        self.assertTrue(paths.safe_component("Console Commands"))  # starts like CON, but isn't a device name
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "outside"))
            staging = os.path.join(t, "staging")
            os.makedirs(staging)
            self.assertIsNone(paths.child_folder(staging, "..\\outside"))
            self.assertIsNone(paths.child_folder(staging, "../outside"))
            self.assertIsNone(paths.child_folder(staging, ".."))


class Console(unittest.TestCase):
    def test_redirected_run_with_a_non_ansi_path_doesnt_crash(self):
        """A real run with stdout piped and a path holding the Polish L: exit 2 with the RESULT line, never a crash.
        The system picks the encoding (python -I ignores PYTHONIOENCODING): UTF-8 on Linux and macOS, the ANSI code
        page on Windows, where the letter is escaped unless the code page has it (cp1250 does)."""
        import locale
        import subprocess
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, NAME)
            r = subprocess.run([sys.executable, "-I", os.path.join(here, "sf-load-order.py"), "--out", out,
                                "--plugins-txt", os.path.join(out, "missing.txt")], capture_output=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(b"CANNOT CHECK", r.stdout)
        forms = {ESCAPED.encode("ascii"), NAME.encode("utf-8"),
                 NAME.encode(locale.getpreferredencoding(False), "backslashreplace")}
        self.assertTrue(any(f in r.stdout for f in forms), r.stdout)

    def test_main_escapes_on_a_cp1252_stream(self):
        """Through main(), on any system: a cp1252 console gets an escape for the letter instead of a crash."""
        buf = io.BytesIO()
        stream = io.TextIOWrapper(buf, encoding="cp1252")
        with tempfile.TemporaryDirectory() as t:
            missing = os.path.join(t, NAME, "missing.txt")
            with mock.patch.object(sys, "stdout", stream), mock.patch.object(sys, "stderr", io.StringIO()):
                code = cli.main(["--out", os.path.join(t, "out"), "--plugins-txt", missing])
                stream.flush()
        self.assertEqual(code, 2)
        self.assertIn(ESCAPED.encode("ascii"), buf.getvalue())


NAME = chr(0x141) + "ukasz"         # the Polish L with stroke, then "ukasz"
ESCAPED = chr(92) + "u0141ukasz"    # what errors="backslashreplace" writes for NAME on a cp1252 console


if __name__ == "__main__":
    unittest.main()
