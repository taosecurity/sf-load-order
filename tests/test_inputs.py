"""Tests for inputs (Plugins.txt, Vortex Load Order export, MO2) and paths (Data folder detection).
Synthetic data only: every file is built in a temp folder; no real game, Vortex or MO2 file is read."""
import argparse
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, inputs, paths, writers  # noqa: E402
from tests import fixtures  # noqa: E402

OFF = fixtures.OFFICIAL  # Starfield, ShatteredSpace, SFBGS003/004/006/007/008


def args(*argv):
    return cli.parser().parse_args(list(argv))


def write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as f:
        f.write(text)
    return path


def gaming_root(drive, *folders):
    """A .GamingRoot file: 'RGBX', a 4-byte count, then UTF-16 names each ending in a zero character."""
    body = b"".join((f + "\0").encode("utf-16-le") for f in folders)
    with open(os.path.join(drive, ".GamingRoot"), "wb") as f:
        f.write(b"RGBX" + struct.pack("<I", len(folders)) + body)


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name
        # an empty environment: no Steam, no Game Pass, no Plugins.txt unless a test makes them
        self.env = {"LOCALAPPDATA": os.path.join(self.t, "LocalAppData"), "APPDATA": os.path.join(self.t, "AppData"),
                    "ProgramFiles(x86)": os.path.join(self.t, "PF86")}
        self.drives = []

    def tearDown(self):
        self._t.cleanup()

    def read(self, *argv):
        return inputs.read(args(*argv), env=self.env, drives=self.drives, registry=lambda: None)

    def ok(self, *argv):
        lo, err = self.read(*argv)
        self.assertIsNone(err)
        return lo

    def by_name(self, lo, name):
        return next(p for p in lo.plugins if p.name == name)


class PluginsTxt(Base):
    def setUp(self):
        super().setUp()
        self.data, _ = fixtures.game(os.path.join(self.t, "Game"), {
            "ModA.esm": {"masters": ["Starfield.esm"], "kind": "L"},
            "ModB.esm": {"masters": ["Starfield.esm", "ModA.esm"], "kind": "M"},
            "Off.esm": {}, "Mine-ShatteredSpace.esm": {}})
        fixtures.plugin(os.path.join(self.data, "BlueprintShips-Starfield.esm"))
        fixtures.plugin(os.path.join(self.data, "SFBGS099.esm"))
        self.pt = write(os.path.join(self.t, "Plugins.txt"),
                        "# comment\n*moda.esm\n*ModB.esm\nOff.esm\n*Missing.esm\n*Mine-ShatteredSpace.esm\n"
                        "*ShatteredSpace.esm\n*BlueprintShips-Mine.esm\n*ModA.esm\n\n", encoding="utf-8-sig")

    def test_order_and_rules(self):
        lo = self.ok("--plugins-txt", self.pt, "--data", self.data)
        self.assertEqual(lo.source, "plugins.txt")
        self.assertEqual(lo.enabled(), OFF + ["SFBGS099.esm", "ModA.esm", "ModB.esm", "Missing.esm",
                                              "BlueprintShips-Starfield.esm"])
        self.assertEqual([p.name for p in lo.plugins if not p.enabled], ["Off.esm"])
        self.assertEqual(sorted(p.name for p in lo.plugins if p.dropped),
                         ["BlueprintShips-Mine.esm", "Mine-ShatteredSpace.esm"])
        self.assertEqual([p.name for p in lo.plugins if p.official and p.listed], ["ShatteredSpace.esm"])
        self.assertIsNone(lo.mods)
        self.assertTrue(lo.mods_note)
        self.assertEqual(lo.files, {"plugins_txt": self.pt})

    def test_case_from_data_and_headers(self):
        lo = self.ok("--plugins-txt", self.pt, "--data", self.data)
        a = self.by_name(lo, "ModA.esm")  # listed as moda.esm
        self.assertEqual(a.path, os.path.join(self.data, "ModA.esm"))
        self.assertEqual((a.kind, a.masters), ("L", ["Starfield.esm"]))
        self.assertEqual(self.by_name(lo, "ModB.esm").kind, "M")
        self.assertIsNone(self.by_name(lo, "Missing.esm").kind)

    def test_findings(self):
        lo = self.ok("--plugins-txt", self.pt, "--data", self.data)
        fs = inputs.findings(lo)
        self.assertTrue(all(f.section == "inputs" for f in fs))
        missing = [f for f in fs if "wasn't found" in f.text]
        self.assertEqual([(f.level, f.plugins) for f in missing], [("problem", ["Missing.esm"])])
        dropped = sorted(f.plugins[0] for f in fs if "removes this name" in f.text)
        self.assertEqual(dropped, ["BlueprintShips-Mine.esm", "Mine-ShatteredSpace.esm"])
        self.assertTrue(all(f.level == "problem" for f in fs if "removes this name" in f.text))  # both enabled
        self.assertTrue(any("ShatteredSpaceAddon.esm" in f.fix for f in fs if f.plugins == ["Mine-ShatteredSpace.esm"]))
        self.assertTrue(any("listed twice" in f.text and f.level == "caution" for f in fs))

    def test_disabled_dropped_name_is_a_note(self):
        pt = write(os.path.join(self.t, "P2.txt"), "*ModA.esm\nMine-ShatteredSpace.esm\n")
        lo = self.ok("--plugins-txt", pt, "--data", self.data)
        fs = [f for f in inputs.findings(lo) if "removes this name" in f.text]
        self.assertEqual([(f.level, f.plugins) for f in fs], [("note", ["Mine-ShatteredSpace.esm"])])
        self.assertIn("even if you enable it", fs[0].text)

    def test_default_plugins_txt(self):
        write(paths.default_plugins_txt(self.env), "*ModA.esm\n")
        lo = self.ok("--data", self.data)
        self.assertEqual(lo.files["plugins_txt"], os.path.join(self.env["LOCALAPPDATA"], "Starfield", "Plugins.txt"))
        self.assertIn("ModA.esm", lo.enabled())

    def test_no_default_plugins_txt_is_an_error(self):
        lo, err = self.read()
        self.assertIsNone(lo)
        self.assertIn(paths.default_plugins_txt(self.env), err)
        self.assertIn("Fix:", err)
        self.assertIn("--plugins-txt", err)

    def test_missing_plugins_txt_named(self):
        bad = os.path.join(self.t, "nope.txt")
        lo, err = self.read("--plugins-txt", bad)
        self.assertIsNone(lo)
        self.assertIn(bad, err)

    def test_bad_data_is_an_error(self):
        lo, err = self.read("--plugins-txt", self.pt, "--data", os.path.join(self.t, "nowhere"))
        self.assertIsNone(lo)
        self.assertIn("nowhere", err)
        self.assertIn("--data", err)

    def test_no_data_found(self):
        lo = self.ok("--plugins-txt", self.pt)
        self.assertIsNone(lo.data)
        self.assertTrue(all(p.path is None and p.kind is None for p in lo.plugins))
        fs = inputs.findings(lo)
        self.assertFalse([f for f in fs if "wasn't found" in f.text and "Data folder wasn't" not in f.text])
        self.assertTrue(any(f.level == "caution" and "Data folder wasn't found" in f.text for f in fs))

    def test_cli_pipeline_runs(self):
        out = os.path.join(self.t, "out")
        code, report = cli.run(args("--plugins-txt", self.pt, "--data", self.data, "--out", out, "--depth", "light"))
        self.assertEqual(code, 0)
        self.assertEqual(report.load_order.source, "plugins.txt")


class Rules(unittest.TestCase):
    def test_dropped(self):
        self.assertEqual([inputs.dropped_name(n) for n in ["A-Constellation.esm", "x-sfta01.esm",
                                                           "ShatteredSpaceAddon.esm", "BlueprintShips-X.esm",
                                                           "Foo.esm", "A-SFBGS003.esp"]],
                         [True, True, False, True, False, True])

    def test_official(self):
        self.assertEqual([inputs.is_official(n) for n in ["OldMars.esm", "SFBGS0AB.esm",
                                                          "BlueprintShips-SFBGS050.esm", "BlueprintShips-Mine.esm",
                                                          "sfta01.esm", "ModA.esm"]],
                         [True, True, True, False, False, False])

    def test_official_order(self):
        present = {n.lower(): n for n in ["SFBGS008.esm", "Constellation.esm", "Starfield.esm", "SFBGS0ZZ.esm",
                                          "BlueprintShips-SFBGS003.esm", "BlueprintShips-Starfield.esm",
                                          "SFBGS003.esm", "OldMars.esm"]}
        plugins, _ = inputs.build_order([], present, lambda n: None)
        # BlueprintShips files not found on disk: by name
        self.assertEqual([p.name for p in plugins],
                         ["Starfield.esm", "Constellation.esm", "OldMars.esm", "SFBGS003.esm", "SFBGS008.esm",
                          "SFBGS0ZZ.esm", "BlueprintShips-SFBGS003.esm", "BlueprintShips-Starfield.esm"])
        self.assertTrue(all(p.official and not p.listed for p in plugins))

    def test_blueprint_oldest_file_first(self):
        """The game appends BlueprintShips files after all others, oldest file first, wherever they are listed."""
        with tempfile.TemporaryDirectory() as d:
            names = ["Starfield.esm", "BlueprintShips-Starfield.esm", "BlueprintShips-SFBGS003.esm", "SFBGS003.esm",
                     "ModA.esm"]
            for n in names:
                fixtures.plugin(os.path.join(d, n))
            os.utime(os.path.join(d, "BlueprintShips-Starfield.esm"), (3_000_000, 3_000_000))
            os.utime(os.path.join(d, "BlueprintShips-SFBGS003.esm"), (1_000_000, 1_000_000))
            present = {n.lower(): n for n in names}
            plugins, _ = inputs.build_order([("BlueprintShips-Starfield.esm", True), ("ModA.esm", True)], present,
                                            lambda n: os.path.join(d, present[n]))
        self.assertEqual([p.name for p in plugins],
                         ["Starfield.esm", "SFBGS003.esm", "ModA.esm", "BlueprintShips-SFBGS003.esm",
                          "BlueprintShips-Starfield.esm"])


class Vortex(Base):
    def setUp(self):
        super().setUp()
        self.data, _ = fixtures.game(os.path.join(self.t, "Game"), {
            "ModA.esm": {"masters": ["Starfield.esm"]}, "CamelCase_Patch.esp": {"kind": "L"}})
        self.staging = os.path.join(self.t, "Staging")
        fixtures.plugin(os.path.join(self.staging, "moda-1", "ModA.esm"))
        write(os.path.join(self.staging, "moda-1", "textures", "a.dds"), "x")
        fixtures.plugin(os.path.join(self.staging, "patch-2", "CamelCase_Patch.esp"))
        fixtures.plugin(os.path.join(self.staging, "undeployed-3", "NotDeployed.esm"))
        self.lof = fixtures.vortex_loadorder(os.path.join(self.t, "loadorder.json"), [
            ("Starfield.esm", True), ("ShatteredSpace.esm", True), ("ModA.esm", True, "moda-1"),
            ("CamelCase_Patch.esp", True, "patch-2"), ("NotDeployed.esm", True, "undeployed-3"),
            ("Gone.esm", False, "gone-4")])

    def test_case_recovery_and_mods(self):
        lo = self.ok("--vortex-loadorder", self.lof, "--data", self.data, "--vortex-staging", self.staging)
        self.assertEqual(lo.source, "vortex")
        self.assertEqual([p.name for p in lo.plugins if p.listed and not p.official],
                         ["ModA.esm", "CamelCase_Patch.esp", "NotDeployed.esm", "gone.esm"])
        self.assertEqual(self.by_name(lo, "CamelCase_Patch.esp").path, os.path.join(self.data, "CamelCase_Patch.esp"))
        self.assertEqual(self.by_name(lo, "CamelCase_Patch.esp").kind, "L")
        nd = self.by_name(lo, "NotDeployed.esm")  # case from staging; not in Data, so not deployed
        self.assertIsNone(nd.path)
        self.assertEqual([(m.id, m.enabled, m.plugins, m.path) for m in lo.mods], [
            ("moda-1", True, ["ModA.esm"], os.path.join(self.staging, "moda-1")),
            ("patch-2", True, ["CamelCase_Patch.esp"], os.path.join(self.staging, "patch-2")),
            ("undeployed-3", True, ["NotDeployed.esm"], os.path.join(self.staging, "undeployed-3")),
            ("gone-4", False, ["gone.esm"], None)])
        self.assertTrue(all(m.priority is None for m in lo.mods))
        self.assertEqual(lo.files["vortex_staging"], self.staging)
        fs = inputs.findings(lo)
        miss = [f for f in fs if "wasn't found" in f.text]
        self.assertEqual([f.plugins for f in miss], [["NotDeployed.esm"]])
        self.assertIn("Deploy", miss[0].fix)

    def test_mod_id_with_leading_space_finds_its_staging_folder(self):
        """The raw modId names the staging folder: one whose name starts with a space is found (0.1.1)."""
        fixtures.plugin(os.path.join(self.staging, " spaced-5", "SpacedMod.esm"))
        lof = fixtures.vortex_loadorder(os.path.join(self.t, "spaced.json"), [
            ("Starfield.esm", True), ("SpacedMod.esm", True, " spaced-5")])
        lo = self.ok("--vortex-loadorder", lof, "--data", self.data, "--vortex-staging", self.staging)
        self.assertEqual([(m.id, m.plugins, m.path) for m in lo.mods],
                         [("spaced-5", ["SpacedMod.esm"], os.path.join(self.staging, " spaced-5"))])  # id trimmed

    def test_mod_id_with_trailing_space_falls_back_to_trimmed_folder(self):
        """Windows drops a trailing space from a folder name: "ModT-7 " finds the folder ModT-7, as in 0.1.0."""
        fixtures.plugin(os.path.join(self.staging, "ModT-7", "ModT.esm"))
        lof = fixtures.vortex_loadorder(os.path.join(self.t, "trail.json"), [
            ("Starfield.esm", True), ("ModT.esm", True, "ModT-7 ")])
        lo = self.ok("--vortex-loadorder", lof, "--data", self.data, "--vortex-staging", self.staging)
        self.assertEqual([(m.id, m.path) for m in lo.mods], [("ModT-7", os.path.join(self.staging, "ModT-7"))])

    def test_trimmed_and_untrimmed_ids_are_one_mod(self):
        """Two plugins of one mod, one export entry with " 8" and one with "8": one mod, not two."""
        fixtures.plugin(os.path.join(self.staging, "8", "A8.esm"))
        fixtures.plugin(os.path.join(self.staging, "8", "B8.esm"))
        lof = fixtures.vortex_loadorder(os.path.join(self.t, "dup.json"), [
            ("Starfield.esm", True), ("A8.esm", True, "8"), ("B8.esm", True, " 8")])
        lo = self.ok("--vortex-loadorder", lof, "--data", self.data, "--vortex-staging", self.staging)
        self.assertEqual([(m.id, sorted(m.plugins)) for m in lo.mods], [("8", ["A8.esm", "B8.esm"])])

    def test_spaced_folder_and_plain_id_are_one_mod_with_the_folder(self):
        """Folder " 7" exists; the export says " 7" for one plugin and "7" for the other: one mod, with the folder."""
        fixtures.plugin(os.path.join(self.staging, " 7", "A7.esm"))
        fixtures.plugin(os.path.join(self.staging, " 7", "B7.esm"))
        lof = fixtures.vortex_loadorder(os.path.join(self.t, "sp7.json"), [
            ("Starfield.esm", True), ("A7.esm", True, " 7"), ("B7.esm", True, "7")])
        lo = self.ok("--vortex-loadorder", lof, "--data", self.data, "--vortex-staging", self.staging)
        self.assertEqual([(m.id, sorted(m.plugins), m.path) for m in lo.mods],
                         [("7", ["A7.esm", "B7.esm"], os.path.join(self.staging, " 7"))])

    def test_default_staging_from_appdata(self):
        s = paths.default_vortex_staging(self.env)
        fixtures.plugin(os.path.join(s, "moda-1", "ModA.esm"))
        lo = self.ok("--vortex-loadorder", self.lof, "--data", self.data)
        self.assertEqual(lo.mods[0].path, os.path.join(self.env["APPDATA"], "Vortex", "starfield", "mods", "moda-1"))

    def test_staging_missing_is_a_note(self):
        lo = self.ok("--vortex-loadorder", self.lof, "--data", self.data)
        self.assertTrue(all(m.path is None for m in lo.mods))
        self.assertEqual(self.by_name(lo, "notdeployed.esm").path, None)  # no Data copy, no staging: lower case
        notes = [f for f in inputs.findings(lo) if "staging folder" in f.text]
        self.assertEqual([f.level for f in notes], ["note"])

    def test_staging_from_deployment_record_no_note(self):
        """No --vortex-staging and no default folder, but the deployment record names a staging folder that
        exists: enrich() finds it, so the 'staging folder wasn't found' note must not appear."""
        with open(os.path.join(self.data, "vortex.deployment.starfield-data-folder.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"stagingPath": self.staging, "files": [{"relPath": "ModA.esm", "source": "moda-1"}]}, f)
        lo = self.ok("--vortex-loadorder", self.lof, "--data", self.data)
        self.assertEqual(lo.files["vortex_staging"], self.staging)
        self.assertEqual([f for f in inputs.findings(lo) if "staging folder" in f.text], [])

    def test_deployed_mod_with_plugin_off_is_enabled(self):
        """A mod in the deployment record is deployed even when its only plugin is off on the Load Order page."""
        lof = fixtures.vortex_loadorder(os.path.join(self.t, "off.json"), [
            ("Starfield.esm", True), ("ModA.esm", False, "moda-1"), ("Gone.esm", False, "gone-4")])
        with open(os.path.join(self.data, "vortex.deployment.starfield-data-folder.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"stagingPath": self.staging, "files": [{"relPath": "ModA.esm", "source": "moda-1"},
                                                              {"relPath": "textures\\a.dds", "source": "moda-1"}]}, f)
        lo = self.ok("--vortex-loadorder", lof, "--data", self.data, "--vortex-staging", self.staging)
        mods = {m.id: m.enabled for m in lo.mods}
        self.assertEqual(mods, {"moda-1": True, "gone-4": False})  # gone-4 isn't deployed
        self.assertFalse(self.by_name(lo, "ModA.esm").enabled)  # the plugin flag is untouched

    def test_not_json(self):
        bad = write(os.path.join(self.t, "bad.json"), "{nope")
        lo, err = self.read("--vortex-loadorder", bad)
        self.assertIsNone(lo)
        self.assertIn(bad, err)
        self.assertIn("Export", err)

    def test_wrong_shape(self):
        bad = write(os.path.join(self.t, "obj.json"), json.dumps({"loadOrder": []}))
        lo, err = self.read("--vortex-loadorder", bad)
        self.assertIsNone(lo)
        self.assertIn("isn't a Vortex load order export", err)

    def test_missing_file(self):
        lo, err = self.read("--vortex-loadorder", os.path.join(self.t, "none.json"))
        self.assertIsNone(lo)
        self.assertIn("none.json", err)


class MO2(Base):
    def setUp(self):
        super().setUp()
        self.data, _ = fixtures.game(os.path.join(self.t, "Game"), {"InData.esm": {}})
        self.inst = fixtures.mo2(os.path.join(self.t, "MO2"), "Default", [
            ("Low Mod", True, {"Shared.esm": {"kind": "F"}, "textures\\y.dds": b"y"}),
            ("Off Mod", False, {"OffOnly.esm": {}, "Shared.esm": {"kind": "M"}}),
            ("High Mod", True, {"Shared.esm": {"kind": "L"}, "Extra.esm": {}}),
        ])
        prof = os.path.join(self.inst, "profiles", "Default")
        write(os.path.join(prof, "plugins.txt"), "*Shared.esm\n*Extra.esm\n*InData.esm\n*Lost.esm\nOffOnly.esm\n")
        write(os.path.join(prof, "loadorder.txt"), "Starfield.esm\nExtra.esm\nShared.esm\nOffOnly.esm\n")
        with open(os.path.join(prof, "modlist.txt"), "a", encoding="utf-8") as f:
            f.write("+Stuff_separator\n*DLC: ShatteredSpace\n+No Folder\n")

    def test_priority_and_winner(self):
        lo = self.ok("--mo2", self.inst, "--data", self.data)
        self.assertEqual((lo.source, lo.profile), ("mo2", "Default"))
        self.assertEqual([(m.name, m.enabled, m.priority) for m in lo.mods],
                         [("No Folder", True, 0), ("Low Mod", True, 1), ("Off Mod", False, 2), ("High Mod", True, 3)])
        shared = self.by_name(lo, "Shared.esm")
        self.assertEqual(shared.path, os.path.join(self.inst, "mods", "High Mod", "Shared.esm"))
        self.assertEqual(shared.kind, "L")  # the High Mod copy was read
        self.assertEqual(self.by_name(lo, "InData.esm").path, os.path.join(self.data, "InData.esm"))
        self.assertIsNone(self.by_name(lo, "OffOnly.esm").path)  # only in a disabled mod
        self.assertEqual(lo.mods[3].plugins, ["Extra.esm", "Shared.esm"])

    def test_loadorder_txt_order(self):
        lo = self.ok("--mo2", self.inst, "--data", self.data)
        self.assertEqual([p.name for p in lo.plugins if p.listed and not p.official],
                         ["Extra.esm", "Shared.esm", "OffOnly.esm", "InData.esm", "Lost.esm"])
        self.assertEqual([p.name for p in lo.plugins if not p.enabled], ["OffOnly.esm"])
        self.assertEqual(set(lo.files), {"plugins_txt", "modlist", "loadorder_txt", "modorganizer_ini"})

    def test_findings(self):
        lo = self.ok("--mo2", self.inst, "--data", self.data)
        fs = inputs.findings(lo)
        self.assertEqual([f.plugins for f in fs if "wasn't found" in f.text], [["Lost.esm"]])
        self.assertTrue(any("No Folder" in f.text for f in fs))

    def test_overwrite_wins(self):
        fixtures.plugin(os.path.join(self.inst, "overwrite", "Extra.esm"), kind="M")
        lo = self.ok("--mo2", self.inst, "--data", self.data)
        self.assertEqual(lo.mods[-1].name, "Overwrite")
        self.assertEqual(self.by_name(lo, "Extra.esm").path, os.path.join(self.inst, "overwrite", "Extra.esm"))

    def test_profile_option_and_errors(self):
        os.makedirs(os.path.join(self.inst, "profiles", "Other"))
        lo, err = self.read("--mo2", self.inst, "--profile", "Other", "--data", self.data)
        self.assertIsNone(lo)
        self.assertIn("plugins.txt", err)
        lo, err = self.read("--mo2", self.inst, "--profile", "Nope", "--data", self.data)
        self.assertIn("--profile", err)
        self.assertIn(os.path.join(self.inst, "profiles"), err)
        lo, err = self.read("--mo2", os.path.join(self.t, "NoMO2"))
        self.assertIn("NoMO2", err)
        os.remove(os.path.join(self.inst, "ModOrganizer.ini"))
        lo, err = self.read("--mo2", self.inst)
        self.assertIn("ModOrganizer.ini", err)
        self.assertIn("--profile", err)

    def test_gamepath_hint(self):
        game = os.path.dirname(self.data)
        write(os.path.join(self.inst, "ModOrganizer.ini"), "[General]\nselected_profile=@ByteArray(Default)\n"
              f"gamePath=@ByteArray({game.replace(chr(92), chr(92) * 2)})\n")  # MO2 doubles backslashes only
        lo = self.ok("--mo2", self.inst)
        self.assertEqual(lo.data, os.path.join(game, "Data"))

    def test_no_modlist(self):
        os.remove(os.path.join(self.inst, "profiles", "Default", "modlist.txt"))
        lo = self.ok("--mo2", self.inst, "--data", self.data)
        self.assertIsNone(lo.mods)
        self.assertIn("modlist.txt", lo.mods_note)


class DataDetection(Base):
    def steam_data(self, lib):
        d = os.path.join(lib, "steamapps", "common", "Starfield", "Data")
        os.makedirs(d)
        return d

    def test_vdf_reader(self):
        vdf = write(os.path.join(self.t, "libraryfolders.vdf"),
                    '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"\n'
                    '\t\t"apps"\n\t\t{\n\t\t\t"1716740"\t\t"123"\n\t\t}\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t'
                    '"E:\\\\Games\\\\SteamLibrary"\n\t}\n}\n')
        self.assertEqual(paths.vdf_library_paths(vdf),
                         [os.path.normpath(r"C:\Program Files (x86)\Steam"), os.path.normpath(r"E:\Games\SteamLibrary")])
        old = write(os.path.join(self.t, "old.vdf"),
                    '"LibraryFolders"\n{\n\t"TimeNextStatsReport"\t\t"1"\n\t"1"\t\t"D:\\\\SteamLibrary"\n}\n')
        self.assertEqual(paths.vdf_library_paths(old), [os.path.normpath(r"D:\SteamLibrary")])
        self.assertEqual(paths.vdf_library_paths(os.path.join(self.t, "none.vdf")), [])

    def test_steam_library_from_vdf(self):
        steam = os.path.join(self.env["ProgramFiles(x86)"], "Steam")
        lib = os.path.join(self.t, "Lib2")
        want = self.steam_data(lib)
        write(os.path.join(steam, "steamapps", "libraryfolders.vdf"),
              '"libraryfolders" { "0" { "path" "%s" } "1" { "path" "%s" } }'
              % (steam.replace("\\", "\\\\"), lib.replace("\\", "\\\\")))
        self.assertEqual(paths.find_data(env=self.env, drives=[], registry=lambda: None)[:2], (want, "Steam install"))

    def test_registry_steam_first(self):
        reg = os.path.join(self.t, "RegSteam")
        want = self.steam_data(reg)
        self.steam_data(os.path.join(self.env["ProgramFiles(x86)"], "Steam"))
        self.assertEqual(paths.find_data(env=self.env, drives=[], registry=lambda: reg)[0], want)

    def test_gamepass_default_and_gamingroot(self):
        d1, d2 = os.path.join(self.t, "D1"), os.path.join(self.t, "D2")
        os.makedirs(d1)
        os.makedirs(d2)
        gaming_root(d2, "\\My Games")
        want = os.path.join(d2, "My Games", "Starfield", "Content", "Data")
        os.makedirs(want)
        self.assertEqual(paths.gaming_root_folders(d2), ["My Games"])
        self.assertIn(os.path.join(d1, "XboxGames", "Starfield", "Content", "Data"), paths.gamepass_candidates([d1, d2]))
        self.assertEqual(paths.find_data(env=self.env, drives=[d1, d2], registry=lambda: None)[:2],
                         (want, "Game Pass install"))
        xg = os.path.join(d1, "XboxGames", "Starfield", "Content", "Data")
        os.makedirs(xg)
        self.assertEqual(paths.find_data(env=self.env, drives=[d1, d2], registry=lambda: None)[0], xg)

    def test_order_given_hint_steam_gamepass(self):
        d = os.path.join(self.t, "D")
        gp = os.path.join(d, "XboxGames", "Starfield", "Content", "Data")
        os.makedirs(gp)
        kw = dict(env=self.env, drives=[d], registry=lambda: None)
        self.assertEqual(paths.find_data(**kw)[0], gp)
        steam = self.steam_data(os.path.join(self.env["ProgramFiles(x86)"], "Steam"))
        self.assertEqual(paths.find_data(**kw)[0], steam)  # Steam before Game Pass
        hint = os.path.join(self.t, "Hint")
        os.makedirs(hint)
        self.assertEqual(paths.find_data(hint=hint, hint_why="MO2's gamePath", **kw)[:2], (hint, "MO2's gamePath"))
        given = os.path.join(self.t, "Given")
        os.makedirs(given)
        self.assertEqual(paths.find_data(given, hint, "MO2's gamePath", **kw)[:2], (given, "given with --data"))

    def test_nothing_found(self):
        data, why, tried = paths.find_data(env=self.env, drives=[os.path.join(self.t, "X")], registry=lambda: None)
        self.assertIsNone(data)
        self.assertEqual(len(tried), 2)  # one Steam place, one Game Pass place

    def test_read_uses_detection(self):
        steam = self.steam_data(os.path.join(self.env["ProgramFiles(x86)"], "Steam"))
        fixtures.plugin(os.path.join(steam, "Starfield.esm"))
        pt = write(os.path.join(self.t, "Plugins.txt"), "")
        lo = self.ok("--plugins-txt", pt)
        self.assertEqual(lo.data, steam)
        self.assertEqual(self.by_name(lo, "Starfield.esm").kind, "F")


class NotWindows(Base):
    """Off Windows LOCALAPPDATA and APPDATA aren't set: no default path may be made up relative to the current
    folder, and the state folder falls back to the XDG place."""

    def test_no_localappdata_no_default_plugins_txt(self):
        self.env = {}
        self.assertIsNone(paths.default_plugins_txt(self.env))
        self.assertIsNone(paths.default_vortex_staging(self.env))
        lo, err = self.read()
        self.assertIsNone(lo)
        self.assertIn("LOCALAPPDATA isn't set", err)
        self.assertIn("--plugins-txt", err)

    def test_vortex_without_appdata(self):
        self.env = {}
        data, _ = fixtures.game(os.path.join(self.t, "G"), {"ModA.esm": {}})
        lof = fixtures.vortex_loadorder(os.path.join(self.t, "lo.json"), [("ModA.esm", True, "moda-1")])
        lo = self.ok("--vortex-loadorder", lof, "--data", data)
        notes = [f for f in inputs.findings(lo) if "staging folder" in f.text]
        self.assertEqual(len(notes), 1)
        self.assertIn("APPDATA isn't set", notes[0].text)

    def test_default_state(self):
        self.assertEqual(paths.default_state({"LOCALAPPDATA": self.t}), os.path.join(self.t, "sf-load-order"))
        if os.name != "nt":
            self.assertEqual(paths.default_state({"XDG_STATE_HOME": self.t}), os.path.join(self.t, "sf-load-order"))
            self.assertEqual(paths.default_state({}),
                             os.path.join(os.path.expanduser("~"), ".local", "state", "sf-load-order"))

    def test_child_folder_any_case(self):
        os.makedirs(os.path.join(self.t, "St", "MyMod-12"))
        self.assertEqual(os.path.normcase(paths.child_folder(os.path.join(self.t, "St"), "mymod-12")),
                         os.path.normcase(os.path.join(self.t, "St", "MyMod-12")))  # Windows keeps the given case
        self.assertIsNone(paths.child_folder(os.path.join(self.t, "St"), "other"))
        self.assertIsNone(paths.child_folder(os.path.join(self.t, "none"), "x"))


class BadNames(Base):
    """A name holding a line break, a NUL or a folder separator is left out with a caution naming its file, so it
    can never add lines to the plugins.txt or loadorder.txt the tool writes."""

    def setUp(self):
        super().setUp()
        self.data, _ = fixtures.game(os.path.join(self.t, "Game"), {"ModA.esm": {"masters": ["Starfield.esm"]}})

    def check(self, lo, source_file, *bad):
        names = [p.name for p in lo.plugins]
        for b in bad:
            self.assertNotIn(b, names)
        self.assertIn("ModA.esm", lo.enabled())
        for p in lo.plugins:
            self.assertFalse(inputs.bad_name(p.name), p.name)
        cautions = [f for f in inputs.findings(lo) if f.level == "caution" and "can't be plugin file names" in f.text]
        self.assertEqual(len(cautions), 1, [f.text for f in inputs.findings(lo)])
        self.assertIn(source_file, cautions[0].text)
        self.assertEqual(sorted(cautions[0].data["names"]), sorted(bad))
        lines = writers.plugins_txt_lines(lo, [p.name for p in lo.plugins]) + \
            writers.loadorder_txt_lines(lo, [p.name for p in lo.plugins])
        self.assertFalse([x for x in lines if "\r" in x or "\n" in x or "\0" in x or "Injected" in x], lines)

    def test_plugins_txt(self):
        pt = os.path.join(self.t, "Plugins.txt")
        with open(pt, "wb") as f:
            f.write(b"*ModA.esm\r\n*Bad\0Name.esm\r\n*sub/Injected.esm\r\n*sub\\Other.esm\r\n")
        lo = self.ok("--plugins-txt", pt, "--data", self.data)
        self.check(lo, pt, "Bad\0Name.esm", "sub/Injected.esm", "sub\\Other.esm")

    def test_vortex_export(self):
        lof = os.path.join(self.t, "lo.json")
        with open(lof, "w", encoding="utf-8") as f:
            json.dump([{"enabled": True, "id": "Starfield.esm", "name": "Starfield.esm", "locked": True},
                       {"enabled": True, "id": "moda.esm", "name": "moda.esm", "modId": "ModA"},
                       {"enabled": True, "id": "x", "name": "evil.esm\r\n*Injected.esm", "modId": "Evil"},
                       {"enabled": True, "id": "y", "name": "nul\0.esm", "modId": "Evil"}], f)
        lo = self.ok("--vortex-loadorder", lof, "--data", self.data)
        self.check(lo, lof, "evil.esm\r\n*Injected.esm", "nul\0.esm")
        self.assertFalse(any("Injected" in p for m in lo.mods or [] for p in m.plugins))

    def test_mo2_files(self):
        mo2 = fixtures.mo2(os.path.join(self.t, "mo2"), "Default",
                           [("ModA", True, {"ModA.esm": {"masters": ["Starfield.esm"]}})])
        prof = os.path.join(mo2, "profiles", "Default")
        with open(os.path.join(prof, "plugins.txt"), "ab") as f:
            f.write(b"*..\\Injected.esm\r\n")
        with open(os.path.join(prof, "loadorder.txt"), "ab") as f:
            f.write(b"a/Injected.esm\n")
        lo = self.ok("--mo2", mo2, "--data", self.data)
        names = [p.name for p in lo.plugins]
        self.assertNotIn("..\\Injected.esm", names)
        self.assertNotIn("a/Injected.esm", names)
        cautions = [f for f in inputs.findings(lo) if "can't be plugin file names" in f.text]
        self.assertEqual(sorted(os.path.basename(f.data["file"]) for f in cautions), ["loadorder.txt", "plugins.txt"])


if __name__ == "__main__":
    unittest.main()
