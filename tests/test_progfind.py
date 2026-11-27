"""C3 (Round 3): programs are found only on PATH, never in the current folder."""
import os
import shutil
import tempfile
import types
import unittest

from sf_load_order import candidate, progfind, sfmodcheck


class ProgramLookupTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.env = dict(os.environ)
        self.planted = tempfile.mkdtemp()
        self.empty = tempfile.mkdtemp()
        self.pathdir = tempfile.mkdtemp()
        for n in ("sf-mod-check.exe", "7z.exe"):
            open(os.path.join(self.planted, n), "wb").close()
        os.chdir(self.planted)
        os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
        os.environ["PATH"] = self.empty
        os.environ["ProgramFiles"] = os.environ["ProgramFiles(x86)"] = self.empty

    def tearDown(self):
        os.chdir(self.cwd)
        os.environ.clear()
        os.environ.update(self.env)
        for d in (self.planted, self.empty, self.pathdir):
            shutil.rmtree(d, ignore_errors=True)

    def test_current_folder_is_never_searched(self):
        prog, err = sfmodcheck.find(types.SimpleNamespace(sf_mod_check=None))
        self.assertIsNone(prog)
        self.assertIsNone(candidate.find_7z())

    def test_dot_and_empty_path_entries_are_skipped(self):
        os.environ["PATH"] = os.pathsep.join([".", "", os.path.relpath(self.planted), self.empty])
        self.assertIsNone(progfind.which("7z.exe"))

    def test_absolute_path_folder_still_works(self):
        open(os.path.join(self.pathdir, "7z.exe"), "wb").close()
        os.environ["PATH"] = os.pathsep.join([self.empty, self.pathdir])
        if os.name == "nt":
            self.assertEqual(os.path.normcase(candidate.find_7z()), os.path.normcase(os.path.join(self.pathdir, "7z.exe")))
            self.assertIsNotNone(progfind.which("7z"))

    def test_full_path_given_still_works(self):
        full = os.path.join(self.planted, "sf-mod-check.exe")
        prog, err = sfmodcheck.find(types.SimpleNamespace(sf_mod_check=full))
        self.assertEqual(os.path.normcase(prog.path), os.path.normcase(full))

    def test_full_path_without_exe_still_works(self):
        # 0.3.3 code-review round 1: shutil.which added PATHEXT to a given path; --sf-mod-check C:\tools\sf-mod-check
        # must still find C:\tools\sf-mod-check.exe
        if os.name != "nt":
            return
        given = os.path.join(self.planted, "sf-mod-check")
        prog, err = sfmodcheck.find(types.SimpleNamespace(sf_mod_check=given))
        self.assertIsNone(err)
        self.assertEqual(os.path.normcase(prog.path), os.path.normcase(given + ".exe"))

    def test_taskkill_is_the_system_one(self):
        self.assertTrue(progfind.system_tool("taskkill.exe").lower().endswith(os.path.join("system32", "taskkill.exe")))

    def test_wrapper_without_comspec_uses_the_system_cmd(self):
        os.environ.pop("ComSpec", None)
        line = sfmodcheck.wrapper_command_line(["x.cmd", "a"])
        self.assertTrue(line.lower().startswith('"' + os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                                                     "System32", "cmd.exe").lower() + '"'), line)


if __name__ == "__main__":
    unittest.main()
