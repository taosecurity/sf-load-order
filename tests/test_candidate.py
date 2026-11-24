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


class SevenZip(Setup):
    """A .7z or .rar is read through 7-Zip; the tests stand in a fake 7-Zip run for it (a real one is tried too)."""

    def fake(self, members, listed=None, fail=None):
        """members {name: bytes} are what 'x' writes; listed is what 'l' prints (default: the members)."""
        calls = []

        def run7z(exe, args):
            calls.append(args)
            r = mock.Mock(returncode=0, stdout="", stderr="")
            if fail == args[0]:
                r.returncode = 2
                r.stderr = "ERROR: Wrong password"
            elif args[0] == "l":
                r.stdout = "".join(f"Path = {n}\nSize = 1\n\n" for n in (listed if listed is not None else members))
            else:
                dest = [a for a in args if a.startswith("-o")][0][2:]
                for n, data in members.items():
                    f = os.path.join(dest, *n.replace("\\", "/").split("/"))
                    os.makedirs(os.path.dirname(f), exist_ok=True)
                    with open(f, "wb") as fh:
                        fh.write(data)
            return r
        return calls, mock.patch.object(candidate, "_run_7z", run7z), \
            mock.patch.object(candidate, "find_7z", lambda: "7z.exe")

    def members_of(self, zpath):
        with zipfile.ZipFile(zpath) as zf:
            return {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}

    def kinds_of(self, zips):
        return self.kinds(self.run_candidate("--depth", "light", zips=zips)[2])

    def test_7z_gives_the_zip_findings(self):
        want = self.kinds_of([self.main])
        sz = os.path.join(self.t, "Build-1.0.7z")
        with open(sz, "wb") as f:
            f.write(b"7z\xbc\xaf")
        calls, a, b = self.fake(self.members_of(self.main))
        with a, b:
            code, out, j = self.run_candidate("--depth", "light", zips=[sz])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.kinds(j), want)
        self.assertTrue(want)
        self.assertEqual(j["candidate"]["zips"][0]["format"], "7z")
        self.assertTrue(j["candidate"]["zips"][0]["path"].endswith("Build-1.0.7z"))
        self.assertEqual([c[0] for c in calls], ["l", "x"])
        self.assertIn("-p", calls[1])

    def test_zip_format_key(self):
        _c, _o, j = self.run_candidate("--depth", "light")
        self.assertEqual({z["format"] for z in j["candidate"]["zips"]}, {"zip"})

    def test_rar_format(self):
        rar = os.path.join(self.t, "Build-1.0.rar")
        with open(rar, "wb") as f:
            f.write(b"Rar!")
        _calls, a, b = self.fake(self.members_of(self.main))
        with a, b:
            code, _out, j = self.run_candidate("--depth", "light", zips=[rar])
        self.assertEqual(code, 0)
        self.assertEqual(j["candidate"]["zips"][0]["format"], "rar")

    def test_no_7zip(self):
        sz = os.path.join(self.t, "Mod.7z")
        with open(sz, "wb") as f:
            f.write(b"7z\xbc\xaf")
        with mock.patch.object(candidate, "find_7z", lambda: None):
            code, out, _j = self.run_candidate(zips=[sz])
        self.assertEqual(code, 2)
        self.assertIn("needs 7-Zip", out)
        self.assertIn("https://www.7-zip.org", out)

    def test_unsafe_member_refused(self):
        sz = os.path.join(self.t, "Mod.7z")
        with open(sz, "wb") as f:
            f.write(b"7z\xbc\xaf")
        calls, a, b = self.fake({"a.txt": b"x"}, listed=["Data/ok.esm", "../evil.dll"])
        with a, b:
            code, out, _j = self.run_candidate(zips=[sz])
        self.assertEqual(code, 2)
        self.assertIn("unsafe path", out)
        self.assertEqual([c[0] for c in calls], ["l"])  # nothing was unpacked

    def test_failed_unpack(self):
        sz = os.path.join(self.t, "Mod.7z")
        with open(sz, "wb") as f:
            f.write(b"7z\xbc\xaf")
        _calls, a, b = self.fake({"a.txt": b"x"}, fail="x")
        with a, b:
            code, out, _j = self.run_candidate(zips=[sz])
        self.assertEqual(code, 2)
        self.assertIn("couldn't unpack Mod.7z: ERROR: Wrong password", out)

    def test_unsafe_member_names(self):
        for bad in ("../x", "a/../x", "/x", "C:/x", "a\\..\\x"):
            self.assertTrue(candidate._unsafe_member(bad), bad)
        for ok in ("x", "Data/a..b.esm", "Data\\x.esm"):
            self.assertFalse(candidate._unsafe_member(ok), ok)

    @unittest.skipUnless(candidate.find_7z(), "7-Zip isn't installed")
    def test_real_7zip(self):
        import subprocess
        exe = candidate.find_7z()
        src = os.path.join(self.t, "srcfiles")
        with zipfile.ZipFile(self.main) as zf:
            zf.extractall(src)
        sz = os.path.join(self.t, "Build-1.0.7z")
        subprocess.run([exe, "a", sz, os.path.join(src, "*")], check=True, capture_output=True)
        self.assertEqual(self.kinds_of([sz]), self.kinds_of([self.main]))


class BadZips(Setup):
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
        """0.2.2: with a plain Plugins.txt and the build's older copy installed, Data's loose files aren't compared
        (no script or differ problem for the older copy's files) and one data-unsorted caution says so."""
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
            self.assertEqual(mine, [])
            uns = [f for f in j["findings"] if f["data"].get("kind") == "data-unsorted"]
            self.assertEqual([(f["level"], f["data"]["replaced"]) for f in uns], [("caution", ["New.esm"])])
            self.assertIn("weren't compared", uns[0]["text"])
            self.assertIn("--vortex-profile", uns[0]["fix"])
            self.assertFalse(any("installed copy of this build" in f["text"] for f in j["findings"]))


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


def _field(name, winner_too=False, minor=False):
    return {"Field": name, "WinnerChangedToo": winner_too, "LoserValue": "a", "WinnerValue": "b",
            "Differences": [{"Text": f"{name} differs", "Minor": minor}], "LoserChanges": [{"Text": "c", "Minor": minor}]}


def _lost(fk, loser, winner, fields, rtype="Npc", edid="Rec", loser_official=False):
    return {"formKey": fk, "RecordType": rtype, "EditorID": edid, "Loser": loser, "LoserOfficial": loser_official,
            "Winner": winner, "Fields": fields}


class FakeProgram:
    """The records tests' fake sf-mod-check (tests.test_records.FAKE), written to <t>\\bin."""

    def __init__(self, t):
        self.bin = os.path.join(t, "bin")
        os.makedirs(self.bin, exist_ok=True)

    def write(self, **cfg):
        from tests import test_records as tr
        with open(os.path.join(self.bin, "fake_smc.py"), "w", encoding="utf-8") as f:
            f.write(tr.FAKE)
        with open(os.path.join(self.bin, "fake.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        script = os.path.join(self.bin, "fake_smc.py")
        if os.name == "nt":
            cmd = os.path.join(self.bin, "sf-mod-check.cmd")
            with open(cmd, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(f'@echo off\n"{sys.executable}" -I "{script}" %*\nexit /b %ERRORLEVEL%\n')
            return cmd
        cmd = os.path.join(self.bin, "sf-mod-check")
        with open(cmd, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n')
        os.chmod(cmd, 0o755)
        return cmd

    def calls(self):
        p = os.path.join(self.bin, "calls.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def plugins_seen(self):
        with open(os.path.join(self.bin, "plugins-seen.txt"), encoding="utf-8") as f:
            return [x.strip() for x in f if x.strip() and not x.startswith("#")]


class Records(unittest.TestCase):
    """0.2.3 (G02): candidate runs sf-mod-check conflict with the build's plugins loading last and reports the
    records they share with installed mods. On 0.2.2 there is no candidate.records key and no shared-records finding.

    Plain setup: Data has New.esm (the installed older copy of the build), Other.esm and Third.esm, enabled in that
    order; the build is New-1.1.zip (New.esm) and New-Part-1.1.zip (NewPart.esm, master New.esm)."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name
        self.data, self.ptxt = fixtures.game(os.path.join(self.t, "game"),
                                             {"New.esm": {}, "Other.esm": {}, "Third.esm": {}})
        self.zips = [make_zip(os.path.join(self.t, "New-1.1.zip"), {"New.esm": ("plugin", ())}),
                     make_zip(os.path.join(self.t, "New-Part-1.1.zip"), {"NewPart.esm": ("plugin", ("New.esm",))})]
        self.out = os.path.join(self.t, "out")
        self.fake = FakeProgram(self.t)
        lo = [(n, True, []) for n in fixtures.OFFICIAL] + [("Other.esm", False, []), ("Third.esm", False, []),
                                                           ("New.esm", False, []), ("NewPart.esm", False, ["New.esm"])]
        self.cj = {"generated": "x", "loadOrder": [{"FileName": n, "Official": o, "Masters": m} for n, o, m in lo],
                   "loadOrderProblems": [], "itms": [], "typeChanges": [], "Errors": [], "UnreadableFields": {},
                   "lost": [
                       _lost("000801:Other.esm", "Other.esm", "New.esm", [_field("Name"), _field("Keywords")],
                             edid="OtherNpc"),
                       _lost("000802:Other.esm", "Other.esm", "NewPart.esm", [_field("Value", winner_too=True)],
                             rtype="Weapon", edid="OtherGun"),
                       _lost("000803:Third.esm", "Third.esm", "New.esm", [_field("Name", minor=True)],
                             edid="ThirdThing"),
                       _lost("000100:Starfield.esm", "Starfield.esm", "New.esm", [_field("Name")],
                             loser_official=True, edid="Vanilla"),
                       _lost("000804:Other.esm", "Other.esm", "Third.esm", [_field("Name")], edid="ModVsMod"),
                       _lost("000805:New.esm", "New.esm", "NewPart.esm", [_field("Name")], edid="OwnParts"),
                       _lost("000806:Other.esm", "Other.esm", "New.esm", [_field("NavMeshes")],
                             rtype="NavigationMeshInfoMap", edid=None),
                   ]}

    def tearDown(self):
        self._t.cleanup()

    def run(self, result=None):
        with mock.patch.dict(os.environ, {"PATH": os.path.join(tempfile.gettempdir(), "no-such-folder")}):
            return super().run(result)  # never a real sf-mod-check from PATH

    def go(self, *extra, prog=None, args=None):
        a = args or ["--plugins-txt", self.ptxt, "--data", self.data]
        if prog:
            a += ["--sf-mod-check", prog]
        code, text, _e = run_cli("candidate", *self.zips, *a, "--no-state", "--out", self.out, *extra)
        self.assertEqual(code, 0, text)
        with open(os.path.join(self.out, "report.json"), encoding="utf-8") as f:
            j = json.load(f)
        return j

    def shared(self, j):
        return [f for f in j["findings"] if f["data"].get("kind") == "shared-records"]

    def test_shared_records(self):
        j = self.go(prog=self.fake.write(conflicts=self.cj))
        conflict = [c for c in self.fake.calls() if c and c[0] == "conflict"]
        self.assertEqual(len(conflict), 1)
        c = conflict[0]
        self.assertIn("--no-assets", c)
        extra = c[c.index("--extra-data") + 1]
        self.assertEqual(sorted(os.listdir(extra)), ["New.esm", "NewPart.esm"])
        self.assertEqual(c[c.index("--data") + 1], self.data)
        # the installed copy of the build is left out, and the build loads last
        self.assertEqual(self.fake.plugins_seen(), ["*Other.esm", "*Third.esm", "*New.esm", "*NewPart.esm"])
        rec = j["candidate"]["records"]
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["shared_records"], 3)
        self.assertEqual([(m["plugin"], m["records"], m["forwardable"], m["choice"], m["no_effect"])
                          for m in rec["mods"]], [("Other.esm", 2, 1, 1, 0), ("Third.esm", 1, 0, 0, 1)])
        fs = self.shared(j)
        self.assertEqual([(f["level"], f["data"]["plugin"]) for f in fs], [("caution", "Other.esm"),
                                                                          ("note", "Third.esm")])
        self.assertIn("This build changes 2 record(s) that Other.esm also changes", fs[0]["text"])
        self.assertIn("1 with fields a conflict patch can forward (Forwardable), 1 where both", fs[0]["text"])
        self.assertIn("OtherNpc (Npc)", fs[0]["text"])
        self.assertIn("sf-mod-check patch", fs[0]["fix"])
        self.assertIn("No effect", fs[1]["text"])
        self.assertFalse(any(x in f["text"] for f in j["findings"] for x in ("Vanilla", "ModVsMod", "OwnParts")))
        with open(os.path.join(self.out, "report.txt"), encoding="utf-8") as f:
            self.assertIn("Record check (sf-mod-check conflict): 3 records shared with 2 installed plugins.", f.read())

    def test_nothing_shared(self):
        self.cj["lost"] = []
        j = self.go(prog=self.fake.write(conflicts=self.cj))
        self.assertEqual(self.shared(j), [])
        self.assertTrue(any("share no changed records with the installed mods" in f["text"] and
                            "2 mod plugin(s) loaded" in f["text"] for f in j["findings"]))
        self.assertEqual(j["candidate"]["records"]["status"], "done")

    def test_max_memory_passed(self):
        self.go("--max-memory", "8", prog=self.fake.write(conflicts=self.cj))
        c = [c for c in self.fake.calls() if c and c[0] == "conflict"][0]
        self.assertEqual(c[c.index("--max-memory") + 1], "8")

    def test_light_doesnt_run(self):
        j = self.go("--depth", "light", prog=self.fake.write(conflicts=self.cj))
        self.assertEqual(self.fake.calls(), [])
        self.assertIsNone(j["candidate"]["records"])
        self.assertTrue(any("--depth light compares file names only" in f["text"] for f in j["findings"]))

    def test_no_program(self):
        j = self.go()
        rec = j["candidate"]["records"]
        self.assertEqual(rec["status"], "skipped")
        self.assertIn("wasn't found", rec["why"])
        self.assertTrue(any(f["level"] == "caution" and f["text"].startswith(candidate.REC_SKIPPED)
                            and "--sf-mod-check" in (f["fix"] or "") for f in j["findings"]))

    def test_failed_run(self):
        j = self.go(prog=self.fake.write(conflicts=None, exit=2, result="RESULT: CANNOT CHECK (bad)"))
        rec = j["candidate"]["records"]
        self.assertEqual((rec["status"], rec["exit"]), ("failed", 2))
        self.assertTrue(any(f["text"].startswith(candidate.REC_SKIPPED) and "RESULT: CANNOT CHECK (bad)" in f["text"]
                            for f in j["findings"]))
        self.assertEqual(self.shared(j), [])

    def test_bad_conflicts_json(self):
        j = self.go(prog=self.fake.write(conflicts={"loadOrder": "no"}))
        self.assertEqual(j["candidate"]["records"]["status"], "failed")
        self.assertTrue(any(f["text"].startswith(candidate.REC_SKIPPED) for f in j["findings"]))

    def test_mo2_hidden_skipped(self):
        mo2 = os.path.join(self.t, "mo2")
        fixtures.mo2(mo2, "Default", [("OtherMod", True, {"Other.esm": {}})])
        data = os.path.join(self.t, "mo2game", "Data")
        fixtures.plugin(os.path.join(data, "Starfield.esm"))
        j = self.go(prog=self.fake.write(conflicts=self.cj), args=["--mo2", mo2, "--data", data])
        self.assertFalse([c for c in self.fake.calls() if c and c[0] == "conflict"])
        rec = j["candidate"]["records"]
        self.assertEqual(rec["status"], "skipped")
        self.assertIn("aren't in", rec["why"])
        self.assertTrue(any("Start sf-load-order from MO2" in (f["fix"] or "") for f in j["findings"]))

    def test_mo2_visible_names_the_mod(self):
        mo2 = os.path.join(self.t, "mo2")
        fixtures.mo2(mo2, "Default", [("OtherMod", True, {"Other.esm": {}}), ("ThirdMod", True, {"Third.esm": {}}),
                                      ("OldNew", True, {"New.esm": {}})])
        data = os.path.join(self.t, "mo2game", "Data")
        for n in ("Starfield.esm", "Other.esm", "Third.esm", "New.esm"):  # as MO2 shows them to a program it starts
            fixtures.plugin(os.path.join(data, n))
        j = self.go(prog=self.fake.write(conflicts=self.cj), args=["--mo2", mo2, "--data", data])
        self.assertEqual(self.fake.plugins_seen(), ["*Other.esm", "*Third.esm", "*New.esm", "*NewPart.esm"])
        fs = self.shared(j)
        self.assertEqual([f["data"]["mod"] for f in fs], ["OtherMod", "ThirdMod"])
        self.assertIn("that OtherMod (Other.esm) also changes", fs[0]["text"])

    def test_undeployed_profile_skipped(self):
        from types import SimpleNamespace
        found = []
        rep = SimpleNamespace(vortex_profile={"name": "B", "deployed": False}, add=found.append)
        ctx = SimpleNamespace(args=SimpleNamespace(depth=None), load_order=SimpleNamespace(data=self.data),
                              report=rep, work=self.t)
        with mock.patch.object(sfmodcheck, "find") as find, mock.patch.object(sfmodcheck, "run_conflict") as rc:
            out = candidate._records(ctx, {"plugins": [{"name": "New.esm", "copy": "x", "masters": []}]}, [])
        find.assert_not_called()
        rc.assert_not_called()
        self.assertEqual(out["status"], "skipped")
        self.assertIn("another profile", out["why"])
        self.assertTrue(any("Deploy B in Vortex" in (f.fix or "") for f in found))


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
