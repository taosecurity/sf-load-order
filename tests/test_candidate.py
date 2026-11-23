"""Tests for `sf-load-order candidate <zip>...` (sf_load_order.candidate). Synthetic data only (tests.fixtures).

MO2 setup (all enabled):
  MasterMod  MasterMod.esm, meshes\\m.nif "master"
  OtherMod   OtherMod.esm, scripts\\shared.pex "x", meshes\\diff.nif "theirs", meshes\\same.nif "same",
             "OtherMod - Main.ba2" holding meshes\\arc.nif "theirs"
  OldBuild   Build.esm (the installed copy of the build), meshes\\diff.nif "old"
The build: Build-1.0.zip = Data\\Build.esm (master MasterMod.esm), Data\\scripts\\shared.pex "x", Data\\meshes\\diff.nif
"mine", Data\\meshes\\same.nif "same", Data\\meshes\\m.nif "over", Data\\scripts\\part.pex "p", Data\\readme.txt,
"Build - Main.ba2" holding meshes\\arc.nif "mine"; Build-Part-1.0.zip = BuildPart.esm and "BuildPart - Main.ba2"
holding scripts\\part.pex "p".
"""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import candidate, cli, sfmodcheck  # noqa: E402
from tests import fixtures  # noqa: E402


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def make_zip(path, members):
    """members: {name in the zip: bytes, or ("ba2", {path: bytes}), or ("plugin", masters)}."""
    tmp = path + ".parts"
    os.makedirs(tmp, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            if isinstance(content, tuple) and content[0] == "ba2":
                f = os.path.join(tmp, os.path.basename(name))
                fixtures.ba2(f, content[1])
                zf.write(f, name)
            elif isinstance(content, tuple) and content[0] == "plugin":
                f = os.path.join(tmp, os.path.basename(name))
                fixtures.plugin(f, masters=content[1])
                zf.write(f, name)
            else:
                zf.writestr(name, content)
    return path


class Setup(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name
        self.data = os.path.join(self.t, "game", "Data")
        fixtures.plugin(os.path.join(self.data, "Starfield.esm"))
        self.mo2 = os.path.join(self.t, "mo2")
        fixtures.mo2(self.mo2, "Default", [
            ("MasterMod", True, {"MasterMod.esm": {}, "meshes\\m.nif": b"master"}),
            ("OtherMod", True, {"OtherMod.esm": {}, "scripts\\shared.pex": b"x", "meshes\\diff.nif": b"theirs",
                                "meshes\\same.nif": b"same"}),
            ("OldBuild", True, {"Build.esm": {"masters": ("MasterMod.esm",)}, "meshes\\diff.nif": b"old"}),
        ])
        fixtures.ba2(os.path.join(self.mo2, "mods", "OtherMod", "OtherMod - Main.ba2"), {"meshes\\arc.nif": b"theirs"})
        self.main = make_zip(os.path.join(self.t, "Build-1.0.zip"), {
            "Data/Build.esm": ("plugin", ("MasterMod.esm",)),
            "Data/scripts/shared.pex": b"x", "Data/meshes/diff.nif": b"mine", "Data/meshes/same.nif": b"same",
            "Data/meshes/m.nif": b"over", "Data/scripts/part.pex": b"p", "Data/readme.txt": b"r",
            "Data/Build - Main.ba2": ("ba2", {"meshes\\arc.nif": b"mine"})})
        self.part = make_zip(os.path.join(self.t, "Build-Part-1.0.zip"), {
            "BuildPart.esm": ("plugin", ("Build.esm",)),
            "BuildPart - Main.ba2": ("ba2", {"scripts\\part.pex": b"p"})})
        self.out = os.path.join(self.t, "out dir")

    def tearDown(self):
        self._t.cleanup()

    def run_candidate(self, *extra, zips=None):
        code, out, err = run_cli("candidate", *(zips or [self.main, self.part]), "--mo2", self.mo2, "--data",
                                 self.data, "--no-state", "--out", self.out, *extra)
        j = None
        if os.path.isfile(os.path.join(self.out, "report.json")):
            with open(os.path.join(self.out, "report.json"), encoding="utf-8") as f:
                j = json.load(f)
        return code, out, j

    def kinds(self, j):
        return {(f["level"], f["data"].get("kind"), f["data"].get("mod"), f["data"].get("where"))
                for f in j["findings"] if f["section"] == "candidate" and f["data"].get("kind")}


class Medium(Setup):
    def test_findings(self):
        code, out, j = self.run_candidate()
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT:", out)
        k = self.kinds(j)
        self.assertIn(("problem", "script", "OtherMod", "loose"), k)        # identical script: still a problem
        self.assertIn(("problem", "differ", "OtherMod", "loose"), k)
        self.assertIn(("problem", "differ", "OtherMod", "archive"), k)      # archived copy differs
        self.assertIn(("note", "same", "OtherMod", "loose"), k)
        self.assertIn(("caution", "master-differ", "MasterMod", "loose"), k)
        self.assertNotIn(("problem", "differ", "MasterMod", "loose"), k)  # built on: never a problem

    def test_installed_copy_left_out(self):
        _code, _out, j = self.run_candidate()
        self.assertEqual(j["candidate"]["replaced"], ["OldBuild"])
        named = [f for f in j["findings"] if "OldBuild" in f["text"]]
        self.assertEqual(len(named), 1)  # only the note saying it was left out
        self.assertEqual(named[0]["level"], "note")
        self.assertIn("left out as this build's own older copy", named[0]["text"])

    def test_own_parts(self):
        _code, _out, j = self.run_candidate()
        own = [f for f in j["findings"] if "more than one part of this build" in f["text"]]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["level"], "problem")
        self.assertEqual(own[0]["data"]["paths"], ["scripts\\part.pex"])

    def test_report_shape(self):
        _code, _out, j = self.run_candidate()
        self.assertEqual(set(j["sections"]), {"inputs", "candidate"})
        c = j["candidate"]
        self.assertEqual(c["plugins"], ["Build.esm", "BuildPart.esm"])
        self.assertEqual(c["depth"], "medium")
        self.assertEqual(c["masters"], ["mastermod.esm"])
        self.assertGreater(c["shared"], 0)
        self.assertEqual(c["findings"]["problem"], sum(1 for f in j["findings"] if f["level"] == "problem"
                                                       and f["section"] == "candidate"))
        with open(os.path.join(self.out, "report.txt"), encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("The new build against the installed mods", txt)
        self.assertNotIn("Suggested order", txt)
        self.assertNotIn("Record conflicts", txt)

    def test_archives_copied_to_numbered_names(self):
        self.run_candidate()
        work = os.path.join(self.out, "work", "candidate")
        self.assertTrue(os.path.isfile(os.path.join(work, "0.ba2")))
        self.assertTrue(os.path.isfile(os.path.join(work, "1.ba2")))
        self.assertFalse(os.path.exists(os.path.join(work, "Build - Main.ba2")))

    def test_strict(self):
        code, _out, _j = self.run_candidate("--strict")
        self.assertEqual(code, 1)

    def test_loose_part_installed_copy_left_out(self):
        # a build part with no plugin: its installed older version is found by the zip's name without the version
        mo2 = os.path.join(self.t, "mo2b")
        fixtures.mo2(mo2, "Default", [
            ("Build-Loose-0.9", True, {"scripts\\loose.pex": b"old", "meshes\\l.nif": b"old"}),
            ("OtherMod", True, {"OtherMod.esm": {}, "scripts\\shared.pex": b"x"})])
        z = make_zip(os.path.join(self.t, "Build-Loose-1.0.zip"), {"scripts/loose.pex": b"new",
                                                                    "meshes/l.nif": b"new"})
        code, out, _err = run_cli("candidate", z, "--mo2", mo2, "--data", self.data, "--no-state", "--out", self.out)
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.out, "report.json"), encoding="utf-8") as f:
            j = json.load(f)
        self.assertEqual(j["candidate"]["replaced"], ["Build-Loose-0.9"])
        self.assertEqual(j["candidate"]["findings"]["problem"], 0)

    def test_wrapper_folder_read_as_data(self):
        z = make_zip(os.path.join(self.t, "Wrapped-1.0.zip"), {"Wrapped/Wrapped.esm": ("plugin", ()),
                                                                "Wrapped/scripts/shared.pex": b"x"})
        _code, _out, j = self.run_candidate(zips=[z])
        self.assertEqual(j["candidate"]["plugins"], ["Wrapped.esm"])
        self.assertIn(("problem", "script", "OtherMod", "loose"), self.kinds(j))

    def test_one_zip_loose_and_archived_is_one_part(self):
        z = make_zip(os.path.join(self.t, "Solo-1.0.zip"), {
            "Solo.esm": ("plugin", ()), "scripts/solo.pex": b"s",
            "Solo - Main.ba2": ("ba2", {"scripts\\solo.pex": b"s"})})
        _code, _out, j = self.run_candidate(zips=[z])
        self.assertFalse([f for f in j["findings"] if "more than one part of this build" in f["text"]])

    def test_no_clash_is_clean(self):
        z = make_zip(os.path.join(self.t, "Clean-1.0.zip"), {"Clean.esm": ("plugin", ()),
                                                              "meshes/clean.nif": b"c"})
        code, out, j = self.run_candidate("--strict", zips=[z])
        self.assertEqual(code, 0, out)
        self.assertEqual(j["candidate"]["shared"], 0)
        self.assertEqual(j["candidate"]["findings"]["problem"], 0)


class Light(Setup):
    def test_names_only(self):
        _code, _out, j = self.run_candidate("--depth", "light")
        k = self.kinds(j)
        self.assertIn(("problem", "script", "OtherMod", "loose"), k)
        self.assertIn(("caution", "light", "OtherMod", "loose"), k)
        self.assertNotIn(("note", "same", "OtherMod", "loose"), k)
        self.assertFalse(any(x[1] == "differ" for x in k))


class BadZips(Setup):
    def test_7z_refused(self):
        p = os.path.join(self.t, "Mod.7z")
        with open(p, "wb") as f:
            f.write(b"7z\xbc\xaf")
        code, out, _j = self.run_candidate(zips=[p])
        self.assertEqual(code, 2)
        self.assertIn("CANNOT CHECK", out)
        self.assertIn(".zip files only", out)

    def test_missing_zip(self):
        code, out, _j = self.run_candidate(zips=[os.path.join(self.t, "nope.zip")])
        self.assertEqual(code, 2)
        self.assertIn("doesn't exist", out)

    def test_not_a_zip(self):
        p = os.path.join(self.t, "fake.zip")
        with open(p, "wb") as f:
            f.write(b"not a zip")
        code, out, _j = self.run_candidate(zips=[p])
        self.assertEqual(code, 2)
        self.assertIn("isn't a readable .zip", out)

    def test_traversal_names_write_nothing_outside(self):
        z = os.path.join(self.t, "Evil-1.0.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../../escaped.esm", b"x")
            zf.writestr("../escaped - Main.ba2", b"x")
            zf.writestr("Evil.esm", b"TES4")
        code, _out, _j = self.run_candidate(zips=[z])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.t, "escaped.esm")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "escaped - Main.ba2")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "work", "escaped.esm")))

    def test_same_archive_name_in_two_zips(self):
        """Code review round 2: two zips shipping one archive name is a problem, not an idle archive."""
        z1 = make_zip(os.path.join(self.t, "Dup-1.0.zip"), {"Dup.esm": ("plugin", ()),
                                                            "Dup - Main.ba2": ("ba2", {"meshes\\a.nif": b"1"})})
        z2 = make_zip(os.path.join(self.t, "Dup-Old-0.9.zip"), {"Dup - Main.ba2": ("ba2", {"meshes\\a.nif": b"2"})})
        _code, _out, j = self.run_candidate(zips=[z1, z2])
        texts = {(f["level"], f["text"]) for f in j["findings"]}
        self.assertTrue(any(lv == "problem" and "1 archive name(s) are in more than one zip" in t for lv, t in texts))
        self.assertFalse(any("don't load with any of its plugins" in t for _lv, t in texts))

    def test_fomod_caution(self):
        z = make_zip(os.path.join(self.t, "Fomod-1.0.zip"), {"fomod/ModuleConfig.xml": b"<config/>",
                                                              "Fomod.esm": ("plugin", ())})
        _code, _out, j = self.run_candidate(zips=[z])
        self.assertTrue(any("installer (fomod)" in f["text"] and f["level"] == "caution" for f in j["findings"]))

    def test_fomod_option_folders_not_claimed_checked(self):
        z = make_zip(os.path.join(self.t, "Opt-1.0.zip"), {"fomod/ModuleConfig.xml": b"<config/>",
                                                           "Core/Opt.esm": ("plugin", ()),
                                                           "Core/Scripts/shared.pex": b"pex"})
        _code, _out, j = self.run_candidate(zips=[z])
        texts = [f["text"] for f in j["findings"] if "installer (fomod)" in f["text"]]
        self.assertEqual(len(texts), 1)
        self.assertIn("the 2 file(s) in its option folders weren't compared", texts[0])
        self.assertNotIn("every file in it was checked", texts[0])
        self.assertEqual(j["candidate"]["zips"][0]["not_compared"], 2)


class PluginsTxt(unittest.TestCase):
    def test_data_loose_files(self):
        with tempfile.TemporaryDirectory() as t:
            data, ptxt = fixtures.game(os.path.join(t, "game"), {"Other.esm": {}})
            os.makedirs(os.path.join(data, "meshes"))
            with open(os.path.join(data, "meshes", "x.nif"), "wb") as f:
                f.write(b"data copy")
            z = make_zip(os.path.join(t, "New-1.0.zip"), {"New.esm": ("plugin", ()), "meshes/x.nif": b"new copy"})
            out = os.path.join(t, "out")
            code, text, _e = run_cli("candidate", z, "--plugins-txt", ptxt, "--data", data, "--no-state",
                                     "--out", out)
            self.assertEqual(code, 0, text)
            with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
                j = json.load(f)
            self.assertTrue(any(f["data"].get("kind") == "differ" and f["data"].get("mod") == "the Data folder (loose)"
                                for f in j["findings"]))
            self.assertTrue(any("plain Plugins.txt" in f["text"] for f in j["findings"]))

    def test_upgrade_over_its_older_copy(self):
        """Code review round 6: with a plain Plugins.txt and the build's older copy installed, the older copy's loose
        script and changed files in Data are a caution, not script or differ problems."""
        with tempfile.TemporaryDirectory() as t:
            data, ptxt = fixtures.game(os.path.join(t, "game"), {"New.esm": {}})
            os.makedirs(os.path.join(data, "scripts"))
            with open(os.path.join(data, "scripts", "new_quest.pex"), "wb") as f:
                f.write(b"old pex")
            z = make_zip(os.path.join(t, "New-1.1.zip"), {"New.esm": ("plugin", ()),
                                                          "Scripts/new_quest.pex": b"new pex"})
            out = os.path.join(t, "out")
            code, text, _e = run_cli("candidate", z, "--plugins-txt", ptxt, "--data", data, "--no-state",
                                     "--out", out)
            self.assertEqual(code, 0, text)
            with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
                j = json.load(f)
            mine = [f for f in j["findings"] if f["data"].get("mod") == "the Data folder (loose)"]
            self.assertEqual([(f["level"], f["data"]["kind"]) for f in mine], [("caution", "data-old")])


class Heavy(Setup):
    def test_cleaning_findings(self):
        prog = sfmodcheck.Program("C:\\fake\\sf-mod-check.exe", "--sf-mod-check")
        seen = []

        def fake_check(_prog, path, data):
            seen.append((os.path.basename(path), data))
            return {"exit": 0, "result": "RESULT: OK", "records": 9, "overrides": 4, "itms": 2 if "Part" not in path
                    else 0, "deleted": {"REFR": 3} if "Part" not in path else {}, "errors": 0, "warnings": 1,
                    "why": None}
        with mock.patch.object(sfmodcheck, "find", return_value=(prog, None)), \
                mock.patch.object(sfmodcheck, "version", return_value=None), \
                mock.patch.object(sfmodcheck, "too_old", return_value=None), \
                mock.patch.object(sfmodcheck, "run_check", side_effect=fake_check):
            _code, _out, j = self.run_candidate("--depth", "heavy")
        # BuildPart.esm's master Build.esm is a part of this build, and Data would give the check the wrong one
        self.assertEqual(sorted(n for n, _d in seen), ["Build.esm"])
        self.assertTrue(all(d == self.data for _n, d in seen))
        texts = {(f["level"], f["text"]) for f in j["findings"]}
        self.assertTrue(any(lv == "problem" and "Build.esm deletes records (3 REFR)" in t for lv, t in texts))
        self.assertTrue(any(lv == "caution" and "Build.esm has 2 record(s) identical" in t for lv, t in texts))
        self.assertTrue(any(lv == "caution" and "skipped BuildPart.esm: its master Build.esm is a part of this build"
                            in t for lv, t in texts))
        self.assertIn("Build.esm", j["candidate"]["heavy"])

    def test_unread_output_is_not_clean(self):
        prog = sfmodcheck.Program("C:\\fake\\sf-mod-check.exe", "--sf-mod-check")

        def fake_check(_prog, _path, _data):
            return {"exit": 0, "result": "", "records": None, "overrides": None, "itms": None, "deleted": {},
                    "errors": None, "warnings": None, "why": None}
        with mock.patch.object(sfmodcheck, "find", return_value=(prog, None)), \
                mock.patch.object(sfmodcheck, "version", return_value=None), \
                mock.patch.object(sfmodcheck, "too_old", return_value=None), \
                mock.patch.object(sfmodcheck, "run_check", side_effect=fake_check):
            _code, _out, j = self.run_candidate("--depth", "heavy")
        texts = [f["text"] for f in j["findings"]]
        self.assertFalse(any("nothing to clean" in t for t in texts))
        self.assertTrue(any("couldn't check Build.esm: its output couldn't be read" in t for t in texts))

    def test_other_profile_deployed(self):
        from types import SimpleNamespace
        found = []
        rep = SimpleNamespace(vortex_profile={"name": "B", "deployed": False}, add=found.append)
        ctx = SimpleNamespace(args=SimpleNamespace(), load_order=SimpleNamespace(data=self.data), report=rep)
        with mock.patch.object(sfmodcheck, "find") as find, mock.patch.object(sfmodcheck, "run_check") as rc:
            out = candidate._heavy(ctx, {"plugins": [{"name": "Build.esm", "copy": "x", "masters": []}]}, rep)
        find.assert_not_called()
        rc.assert_not_called()
        self.assertIn("another profile", out["why"])
        self.assertTrue(any("Deploy B in Vortex" in (f.fix or "") for f in found))

    def test_no_program(self):
        with mock.patch.object(sfmodcheck, "find", return_value=(None, None)):
            _code, _out, j = self.run_candidate("--depth", "heavy")
        self.assertTrue(any("cleaning check (--depth heavy) didn't run" in f["text"] for f in j["findings"]))


class Parsers(unittest.TestCase):
    def test_help_exits_0(self):
        self.assertEqual(run_cli("--help")[0], 0)
        code, out, _e = run_cli("candidate", "--help")
        self.assertEqual(code, 0)
        self.assertIn("sf-load-order candidate", out)

    def test_main_parser_mentions_candidate(self):
        self.assertIn("sf-load-order candidate", run_cli("--help")[1])

    def test_member_path(self):
        self.assertEqual(candidate._member_path("Data/Textures/A.DDS"), "textures\\a.dds")
        self.assertEqual(candidate._member_path("./data\\x.esm"), "x.esm")
        self.assertIsNone(candidate._member_path("Data/"))
        self.assertEqual(candidate._member_path("MyMod/Data/Scripts/a.pex", "mymod"), "scripts\\a.pex")

    def test_wrapper_folder(self):
        self.assertEqual(candidate.wrapper_folder(["MyMod/MyMod.esm", "MyMod/Scripts/a.pex"]), "mymod")
        self.assertIsNone(candidate.wrapper_folder(["Scripts/a.pex", "Scripts/b.pex"]))  # a game folder
        self.assertIsNone(candidate.wrapper_folder(["Data/a.esm", "Data/b.esm"]))
        self.assertIsNone(candidate.wrapper_folder(["MyMod/a.esm", "b.esm"]))
        self.assertIsNone(candidate.wrapper_folder(["A/a.esm", "B/b.esm"]))
        # code review round 4: a readme at the top doesn't hide the mod's own folder
        self.assertEqual(candidate.wrapper_folder(["README.txt", "MyMod/MyMod.esm", "MyMod/Scripts/a.pex"]), "mymod")

    def test_wrapper_folder_with_readme_read_as_data(self):
        with tempfile.TemporaryDirectory() as t:
            z = make_zip(os.path.join(t, "MyMod-1.0.zip"), {"README.txt": b"r", "MyMod/MyMod.esm": ("plugin", ()),
                                                             "MyMod/Scripts/foo.pex": b"s"})
            b = candidate.read_zips([z], os.path.join(t, "work"), True)
            self.assertEqual([p["name"] for p in b["plugins"]], ["MyMod.esm"])
            self.assertEqual(list(b["loose"]), ["scripts\\foo.pex"])

    def test_compare_mixed_loose_and_archive(self):
        """Code review round 4: a differing pair of one loose and one archived copy names who always wins."""
        class C:
            def file(self, _full):
                return {"digest": "T"}

            def digest(self, _full, _name):
                return "T"
        loose_inst = {"textures\\x.dds": [("OtherMod", ["OtherMod.esm"], "loose", "x")]}
        arch_inst = {"textures\\x.dds": [("OtherMod", ["OtherMod.esm"], "archive", "x")]}
        bp = {"textures\\x.dds": ["M"]}
        self.assertEqual(list(candidate.compare(bp, loose_inst, set(), C(), "medium", set())),
                         [("problem", "hidden", "OtherMod", "loose")])
        self.assertEqual(list(candidate.compare(bp, arch_inst, set(), C(), "medium", {"textures\\x.dds"})),
                         [("problem", "wins", "OtherMod", "archive")])
        self.assertEqual(list(candidate.compare(bp, loose_inst, set(), C(), "medium", {"textures\\x.dds"})),
                         [("problem", "differ", "OtherMod", "loose")])
        text, _fix = candidate._text("hidden", "OtherMod", "loose", 1, "textures\\x.dds")
        self.assertIn("never shows", text)
        self.assertNotIn("load order decides", text)

    def test_compare_plain_data_loose_master_file(self):
        """Code review round 4: with a plain Plugins.txt, a Data loose file a master's archive also ships is the
        master's, so overriding it is a caution, not a problem."""
        class C:
            def file(self, _full):
                return {"digest": "T"}

            def digest(self, _full, _name):
                return "T"
        inst = {"textures\\x.dds": [(candidate.DATA_OWNER, [], "loose", "a"),
                                    ("Foo.esm", ["Foo.esm"], "archive", "b")]}
        g = candidate.compare({"textures\\x.dds": ["M"]}, inst, {"foo.esm"}, C(), "medium", {"textures\\x.dds"})
        self.assertEqual(sorted(k[:2] for k in g), [("caution", "master-differ"), ("caution", "master-differ")])
        g = candidate.compare({"textures\\x.dds": ["M"]}, inst, set(), C(), "medium", {"textures\\x.dds"})
        self.assertIn(("problem", "differ", candidate.DATA_OWNER, "loose"), g)

    def test_compare_repeated_copies(self):
        """Code review round 2: a path the build ships twice with the same content is still compared."""
        class C:
            def file(self, _full):
                return {"digest": "T"}

            def digest(self, _full, _name):
                return "T"
        inst = {"meshes\\a.nif": [("OtherMod", [], "loose", "x")]}
        g = candidate.compare({"meshes\\a.nif": ["M", "M"]}, inst, set(), C(), "medium")
        self.assertEqual(list(g), [("problem", "differ", "OtherMod", "loose")])
        g = candidate.compare({"meshes\\a.nif": ["T", "T"]}, inst, set(), C(), "medium")
        self.assertEqual(list(g), [("note", "same", "OtherMod", "loose")])
        g = candidate.compare({"meshes\\a.nif": ["T", None]}, inst, set(), C(), "medium")
        self.assertEqual(list(g), [("caution", "unknown", "OtherMod", "loose")])

    def test_mod_stem(self):
        self.assertEqual(candidate.mod_stem("C:\\x\\ShipMod-Loose-8.8.40.zip"), "shipmod-loose")
        self.assertEqual(candidate.mod_stem("ShipMod-Loose-8.8.40-1700000000"), "shipmod-loose")
        self.assertEqual(candidate.mod_stem("ShipMod v2"), "shipmod")
        self.assertEqual(candidate.mod_stem("Mod-2D"), "mod-2d")
        self.assertEqual(candidate.mod_stem(None), "")


if __name__ == "__main__":
    unittest.main()
