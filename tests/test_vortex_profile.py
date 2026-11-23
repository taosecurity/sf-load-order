"""Tests for --vortex-profile, --vortex-exports, --vortex-stamp, --vortex-modlist and the deployed-profile check.
Synthetic data only: every file is built in a temp folder; no real Vortex export or game file is read."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, inputs, paths  # noqa: E402
from tests import fixtures  # noqa: E402

PROFILE = "Test-Profile"


def args(*argv):
    return cli.parser().parse_args(list(argv))


def modlist(path, mods):
    """mods: [(vortexId, enabled, [plugins])], in the extension's modlist shape."""
    out = [{"name": mid, "game": "starfield", "source": "nexus", "enabled": on, "vortexId": mid,
            "installedGame": "starfield", "archive": mid + ".7z", "plugins": list(pl)} for mid, on, pl in mods]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name
        self.env = {"LOCALAPPDATA": os.path.join(self.t, "LocalAppData"), "APPDATA": os.path.join(self.t, "AppData"),
                    "ProgramFiles(x86)": os.path.join(self.t, "PF86")}
        # Data deploys ModA and ModB; staging holds ModA, ModB and ModC (ModC is another profile's mod)
        self.data, _ = fixtures.game(os.path.join(self.t, "Game"), {
            "ModA.esm": {"masters": ["Starfield.esm"]}, "ModB.esp": {"masters": ["ModA.esm"], "kind": "L"}})
        self.staging = os.path.join(self.t, "Staging")
        fixtures.plugin(os.path.join(self.staging, "moda-1", "ModA.esm"), masters=["Starfield.esm"])
        fixtures.plugin(os.path.join(self.staging, "modb-2", "ModB.esp"), masters=["ModA.esm"], kind="L")
        fixtures.plugin(os.path.join(self.staging, "modc-3", "ModC.esm"), masters=["Starfield.esm"])
        self.exports = os.path.join(self.t, "Exports")
        os.makedirs(os.path.join(self.exports, "old"))

    def tearDown(self):
        self._t.cleanup()

    def export_set(self, stamp, entries, mods, folder=None, plugins=True, profile=PROFILE):
        folder = folder or self.exports
        base = os.path.join(folder, f"vortex-starfield-{profile}-")
        fixtures.vortex_loadorder(base + f"loadorder-backup-{stamp}.json", entries)
        modlist(base + f"modlist-backup-{stamp}.json", mods)
        if plugins:
            fixtures.plugins_txt(base + f"plugins-{stamp}.txt",
                                 [(e[0], e[1]) for e in entries if e[0] not in fixtures.OFFICIAL])
        return base

    def deployed_set(self, stamp="20261101-100000", **kw):
        return self.export_set(stamp, [("Starfield.esm", True), ("ModA.esm", True, "moda-1"),
                                       ("ModB.esp", True, "modb-2")],
                               [("moda-1", True, ["ModA.esm"]), ("modb-2", True, ["ModB.esp"])], **kw)

    def other_set(self, stamp="20261102-100000", **kw):
        """A profile with ModA, ModC and ModD: ModB in Data isn't its own, ModC isn't in Data (only in staging), and
        ModD is nowhere."""
        return self.export_set(stamp, [("Starfield.esm", True), ("ModA.esm", True, "moda-1"),
                                       ("ModC.esm", True, "modc-3"), ("ModD.esm", True, "modd-4")],
                               [("moda-1", True, ["ModA.esm"]), ("modc-3", True, ["ModC.esm"]),
                                ("modd-4", True, ["ModD.esm"])], **kw)

    def read(self, *argv):
        return inputs.read(args(*argv), env=self.env, drives=[], registry=lambda: None)

    def ok(self, *argv):
        lo, err = self.read(*argv)
        self.assertIsNone(err)
        return lo

    def common(self):
        return ["--vortex-exports", self.exports, "--data", self.data, "--vortex-staging", self.staging]


class FindSet(Base):
    def test_newest_complete_set_wins(self):
        self.deployed_set("20261101-100000")
        self.other_set("20261102-100000")
        # a newer stamp with only a loadorder file is not a complete set
        fixtures.vortex_loadorder(os.path.join(self.exports, f"vortex-starfield-{PROFILE}-loadorder-backup-"
                                                             "20261103-100000.json"), [("Starfield.esm", True)])
        lo = self.ok("--vortex-profile", PROFILE, *self.common())
        self.assertEqual(lo._vortex_profile["stamp"], "20261102-100000")
        self.assertEqual(lo.profile, PROFILE)
        self.assertTrue(lo.files["vortex_modlist"].endswith("modlist-backup-20261102-100000.json"))

    def test_stamp_picks_an_older_set_from_old(self):
        self.deployed_set("20261101-100000", folder=os.path.join(self.exports, "old"))
        self.other_set("20261102-100000")
        lo = self.ok("--vortex-profile", PROFILE, "--vortex-stamp", "20261101-100000", "--vortex-exports",
                     self.exports, "--vortex-exports", os.path.join(self.exports, "old"), "--data", self.data,
                     "--vortex-staging", self.staging)
        self.assertEqual(lo._vortex_profile["stamp"], "20261101-100000")
        self.assertTrue(lo._vortex_profile["deployed"])

    def test_profile_name_is_exact(self):
        """A profile whose name starts with another's ("Test-Profile-2") isn't picked for "Test-Profile"."""
        self.other_set("20261105-100000", profile=PROFILE + "-2")
        self.deployed_set("20261101-100000")
        lo = self.ok("--vortex-profile", PROFILE, *self.common())
        self.assertEqual(lo._vortex_profile["stamp"], "20261101-100000")

    def test_unknown_stamp_and_bad_stamp(self):
        self.deployed_set()
        lo, err = self.read("--vortex-profile", PROFILE, "--vortex-stamp", "20250101-000000", *self.common())
        self.assertIsNone(lo)
        self.assertIn("20261101-100000", err)
        lo, err = self.read("--vortex-profile", PROFILE, "--vortex-stamp", "yesterday", *self.common())
        self.assertIn("isn't a stamp", err)

    def test_no_set_for_the_profile(self):
        self.deployed_set()
        lo, err = self.read("--vortex-profile", "Nobody", *self.common())
        self.assertIsNone(lo)
        self.assertIn("Nobody", err)
        self.assertIn(self.exports, err)

    def test_default_folder_from_the_extension_settings(self):
        self.deployed_set(folder=os.path.join(self.exports, "old"))
        settings = paths.vortex_export_settings(self.env)
        os.makedirs(os.path.dirname(settings))
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "folder": self.exports, "recommendedFolder": ""}, f)
        lo = self.ok("--vortex-profile", PROFILE, "--data", self.data, "--vortex-staging", self.staging)
        self.assertEqual(lo._vortex_profile["stamp"], "20261101-100000")  # found in old\

    def test_no_settings_file_names_the_fix(self):
        lo, err = self.read("--vortex-profile", PROFILE, "--data", self.data)
        self.assertIsNone(lo)
        self.assertIn("--vortex-exports", err)
        self.assertIn("modlist-backup-auto-export.json", err)

    def test_missing_exports_folder(self):
        lo, err = self.read("--vortex-profile", PROFILE, "--vortex-exports", os.path.join(self.t, "nope"))
        self.assertIn("is not a folder", err)

    def test_bad_modlist_is_refused(self):
        base = self.deployed_set()
        with open(base + "modlist-backup-20261101-100000.json", "w", encoding="utf-8") as f:
            f.write("{not json")
        lo, err = self.read("--vortex-profile", PROFILE, *self.common())
        self.assertIsNone(lo)
        self.assertIn("isn't a Modlist Backup modlist export", err)


class Options(Base):
    def test_profile_refuses_a_second_source(self):
        self.deployed_set()
        for extra in (["--plugins-txt", "x.txt"], ["--mo2", self.t], ["--vortex-loadorder", "x.json"]):
            lo, err = self.read("--vortex-profile", PROFILE, *extra, *self.common())
            self.assertIsNone(lo)
            self.assertIn("can't be combined with " + extra[0], err)

    def test_profile_only_options_need_the_profile(self):
        for extra in (["--vortex-exports", self.exports], ["--vortex-stamp", "20261101-100000"]):
            lo, err = self.read(*extra, "--plugins-txt", "x.txt")
            self.assertIn("works only with --vortex-profile", err)

    def test_modlist_with_a_plain_loadorder(self):
        base = self.deployed_set()
        lo = self.ok("--vortex-loadorder", base + "loadorder-backup-20261101-100000.json", "--vortex-modlist",
                     base + "modlist-backup-20261101-100000.json", "--data", self.data,
                     "--vortex-staging", self.staging)
        self.assertEqual([m["vortexId"] for m in lo._vortex_modlist], ["moda-1", "modb-2"])
        self.assertIsNone(getattr(lo, "_vortex_profile", None))
        lo, err = self.read("--vortex-modlist", base + "modlist-backup-20261101-100000.json")
        self.assertIn("works only with --vortex-loadorder", err)

    def test_same_findings_as_vortex_loadorder(self):
        """--vortex-profile reads the set's loadorder file exactly as --vortex-loadorder reads it."""
        base = self.deployed_set()
        a = self.ok("--vortex-profile", PROFILE, *self.common())
        b = self.ok("--vortex-loadorder", base + "loadorder-backup-20261101-100000.json", "--data", self.data,
                    "--vortex-staging", self.staging)
        self.assertEqual([(p.name, p.enabled, p.path, p.kind) for p in a.plugins],
                         [(p.name, p.enabled, p.path, p.kind) for p in b.plugins])
        self.assertEqual([f.text for f in inputs.findings(a)], [f.text for f in inputs.findings(b)])


class Deployed(Base):
    def test_deployed_profile(self):
        self.deployed_set()
        lo = self.ok("--vortex-profile", PROFILE, *self.common())
        vp = lo._vortex_profile
        self.assertIs(vp["deployed"], True)
        self.assertEqual((vp["extra_plugins"], vp["extra_mods"], vp["missing_plugins"]), ([], [], []))
        self.assertFalse(any("another profile" in f.text for f in inputs.findings(lo)))

    def test_other_profile_deployed_gives_one_caution(self):
        self.other_set()
        lo = self.ok("--vortex-profile", PROFILE, *self.common())
        vp = lo._vortex_profile
        self.assertIs(vp["deployed"], False)
        self.assertEqual(vp["extra_plugins"], ["ModB.esp"])
        self.assertEqual(vp["missing_plugins"], ["ModC.esm", "modd.esm"])  # modd: no file, so the export's case
        fs = inputs.findings(lo)
        self.assertEqual([f.level for f in fs if "another profile" in f.text], ["caution"])
        self.assertFalse([f for f in fs if f.level == "problem"], [f.text for f in fs])  # no "not in Data" wall
        # ModC is read from its staging folder, so its masters are known
        modc = next(p for p in lo.plugins if p.name == "ModC.esm")
        self.assertEqual(modc.path, os.path.join(self.staging, "modc-3", "ModC.esm"))
        self.assertEqual(modc.masters, ["Starfield.esm"])

    def test_other_profile_shared_plugin_read_from_staging(self):
        """Code review 2026-11-23: a plugin both profiles have is read from this profile's staging copy, not from
        Data (the deployed profile's copy, maybe another version)."""
        fixtures.plugin(os.path.join(self.staging, "moda-1", "ModA.esm"), masters=["Starfield.esm", "ModC.esm"])
        self.other_set()
        lo = self.ok("--vortex-profile", PROFILE, *self.common())
        moda = next(p for p in lo.plugins if p.name.lower() == "moda.esm")
        self.assertEqual(os.path.normcase(moda.path), os.path.normcase(os.path.join(self.staging, "moda-1",
                                                                                     "ModA.esm")))
        self.assertEqual(moda.masters, ["Starfield.esm", "ModC.esm"])

    def test_other_profile_mods_follow_the_modlist(self):
        """Bug hunt 2026-11-23: with the staging folder known only from the deployment record (no --vortex-staging,
        no default), a profile that isn't deployed still reads its own plugins from staging, and its mods are the
        modlist's enabled ones, not the deployed profile's."""
        os.makedirs(os.path.join(self.staging, "modf-6", "textures"))
        with open(os.path.join(self.staging, "modf-6", "textures", "f.dds"), "wb") as f:
            f.write(b"F")
        self.export_set("20261102-100000", [("Starfield.esm", True), ("ModA.esm", True, "moda-1"),
                                            ("ModC.esm", True, "modc-3")],
                        [("moda-1", True, ["ModA.esm"]), ("modc-3", True, ["ModC.esm"]), ("modf-6", True, []),
                         ("modb-2", False, ["ModB.esp"])])
        with open(os.path.join(self.data, "vortex.deployment.starfield-data-folder.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"stagingPath": self.staging, "files": [
                {"relPath": "ModA.esm", "source": "moda-1"}, {"relPath": "ModB.esp", "source": "modb-2"},
                {"relPath": "textures\\e.dds", "source": "mode-5"}]}, f)
        fixtures.plugins_txt(paths.default_plugins_txt(self.env), [("ModA.esm", True), ("ModB.esp", True)])
        lo = self.ok("--vortex-profile", PROFILE, "--vortex-exports", self.exports, "--data", self.data)
        self.assertIs(lo._vortex_profile["deployed"], False)
        modc = next(p for p in lo.plugins if p.name == "ModC.esm")
        self.assertEqual(modc.path, os.path.join(self.staging, "modc-3", "ModC.esm"))
        on = {m.id: m.path for m in lo.mods if m.enabled}
        self.assertEqual(sorted(on), ["moda-1", "modc-3", "modf-6"])
        self.assertEqual(on["modf-6"], os.path.join(self.staging, "modf-6"))
        # the game's Plugins.txt is the deployed profile's, so its difference isn't reported
        self.assertIs(lo._vortex_profile["plugins_txt_matches"], False)
        self.assertFalse(any("differs from the game's Plugins.txt" in f.text for f in inputs.findings(lo)))

    def test_deployment_record_naming_a_mod_not_enabled(self):
        self.deployed_set()
        with open(os.path.join(self.data, "vortex.deployment.starfield-data-folder.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"stagingPath": self.staging, "files": [
                {"relPath": "ModA.esm", "source": "moda-1"}, {"relPath": "ModB.esp", "source": "modb-2"},
                {"relPath": "textures\\c.dds", "source": "modc-3"}]}, f)
        lo = self.ok("--vortex-profile", PROFILE, *self.common())
        self.assertIs(lo._vortex_profile["deployed"], False)
        self.assertEqual(lo._vortex_profile["extra_mods"], ["modc-3"])

    def test_plugins_txt_matches(self):
        self.deployed_set()
        live = paths.default_plugins_txt(self.env)
        fixtures.plugins_txt(live, [("ModA.esm", True), ("ModB.esp", True)])
        lo = self.ok("--vortex-profile", PROFILE, *self.common())
        self.assertIs(lo._vortex_profile["plugins_txt_matches"], True)
        fixtures.plugins_txt(live, [("ModA.esm", True), ("ModB.esp", False)])
        lo = self.ok("--vortex-profile", PROFILE, *self.common())
        self.assertIs(lo._vortex_profile["plugins_txt_matches"], False)
        self.assertTrue(any("differs from the game's Plugins.txt" in f.text for f in inputs.findings(lo)))

    def test_no_plugins_file_leaves_the_field_out(self):
        self.deployed_set(plugins=False)
        fixtures.plugins_txt(paths.default_plugins_txt(self.env), [("ModA.esm", True)])
        lo = self.ok("--vortex-profile", PROFILE, *self.common())
        self.assertNotIn("plugins_txt_matches", lo._vortex_profile)


class Run(Base):
    """The whole run: report.json gains vortex_profile, and records and patches are skipped when not deployed."""

    def run_tool(self, *argv):
        out = os.path.join(self.t, "out")
        a = args("--vortex-profile", PROFILE, *self.common(), "--depth", "light", "--no-state", "--quiet",
                 "--out", out, *argv)
        saved = inputs.read
        inputs.read = lambda a_, env=None, drives=None, registry=None: saved(a_, self.env, [], lambda: None)
        try:
            code, report = cli.run(a)
        finally:
            inputs.read = saved
        self.assertEqual(code, 0)
        with open(report.outputs["json"], encoding="utf-8") as f:
            return report, json.load(f)

    def test_report_json_and_skips(self):
        self.other_set()
        report, d = self.run_tool("--depth", "medium")  # the last --depth wins
        self.assertEqual(d["vortex_profile"]["name"], PROFILE)
        self.assertIs(d["vortex_profile"]["deployed"], False)
        for section in ("patches", "records"):
            self.assertEqual(d["sections"][section]["status"], "skipped")
            self.assertIn("another profile", d["sections"][section]["why"])

    def test_other_profile_loose_pairs_ignore_data(self):
        """Bug hunt 2026-11-23: when Data holds another profile, its loose copies don't say which of this profile's
        copies wins."""
        for mod, body in (("moda-1", b"A"), ("modc-3", b"C")):
            os.makedirs(os.path.join(self.staging, mod, "textures"))
            with open(os.path.join(self.staging, mod, "textures", "t.dds"), "wb") as f:
                f.write(body)
        os.makedirs(os.path.join(self.data, "textures"), exist_ok=True)
        with open(os.path.join(self.data, "textures", "t.dds"), "wb") as f:
            f.write(b"A")
        self.other_set()
        report, d = self.run_tool()
        pair = [f for f in d["findings"] if f["section"] == "loose" and "both ship" in f["text"]]
        self.assertEqual(len(pair), 1, [f["text"] for f in d["findings"]])
        self.assertNotIn("Data holds", pair[0]["text"])
        self.assertEqual(pair[0]["data"]["in_data"], {})

    def test_deployed_runs_patches(self):
        self.deployed_set()
        report, d = self.run_tool()
        self.assertIs(d["vortex_profile"]["deployed"], True)
        self.assertNotEqual(d["sections"]["patches"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
