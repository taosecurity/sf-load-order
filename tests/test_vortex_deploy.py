"""C1b tests: Vortex's deployment record (sf_load_order.vortex_deploy). Synthetic data only.

The record is written here from its documented shape: an object with stagingPath and a files list, each entry
{"relPath": <path relative to Data>, "source": <Vortex mod id>, ...}. No real Vortex file is read.

Setup: a fake game Data folder with ModA.esm (mod moda-1) plus loose files, a Vortex export listing only moda-1, and
a record that also lists two loose-only mods, retex-2 (textures) and sfse-dll-3 (an SFSE DLL); retex-2 has a staging
folder, sfse-dll-3 doesn't.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, inputs, vortex_deploy  # noqa: E402
from sf_load_order.model import LoadOrder, Mod  # noqa: E402
from tests import fixtures  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def record(data, staging, files, name=vortex_deploy.RECORD, **extra):
    """A deployment record shaped like Vortex's: files [(relPath, source)]."""
    d = {"version": 1, "instance": "test", "deploymentMethod": "hardlink", "gameId": "starfield",
         "deploymentTime": 0, "stagingPath": staging, "targetPath": data,
         "files": [{"relPath": r, "source": s, "time": 0} for r, s in files]}
    d.update(extra)
    return write(os.path.join(data, name), json.dumps(d))


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name
        self.data, _ = fixtures.game(os.path.join(self.t, "Game"), {"ModA.esm": {"masters": ["Starfield.esm"]}})
        self.staging = os.path.join(self.t, "Staging")
        fixtures.plugin(os.path.join(self.staging, "moda-1", "ModA.esm"))
        write(os.path.join(self.staging, "moda-1", "textures", "a.dds"), "a")
        write(os.path.join(self.staging, "retex-2", "Textures", "Rock.dds"), "r")
        for rel in ("textures\\a.dds", "Textures\\Rock.dds", "SFSE\\Plugins\\Foo.dll", "mytool\\stray.cfg"):
            write(fixtures.on_disk(self.data, rel), "x")
        self.files = [("ModA.esm", "moda-1"), ("textures/a.dds", "moda-1"), ("Textures\\Rock.dds", "retex-2"),
                      ("SFSE\\Plugins\\Foo.dll", "sfse-dll-3")]

    def tearDown(self):
        self._t.cleanup()

    def lo(self):
        lo = LoadOrder("vortex", files={"vortex_loadorder": "x.json", "vortex_staging": self.staging},
                       data=self.data)
        lo.mods = [Mod("moda-1", True, path=os.path.join(self.staging, "moda-1"), plugins=["ModA.esm"],
                       id="moda-1")]
        return lo


class Read(Base):
    def test_reads_files_and_staging(self):
        record(self.data, self.staging, self.files)
        d = vortex_deploy.read(self.data)
        self.assertEqual(d.path, os.path.join(self.data, vortex_deploy.RECORD))
        self.assertEqual(d.staging, self.staging)
        self.assertEqual(d.files, {"moda.esm": "moda-1", "textures\\a.dds": "moda-1",
                                   "textures\\rock.dds": "retex-2", "sfse\\plugins\\foo.dll": "sfse-dll-3"})
        self.assertEqual(d.by_mod()["moda-1"], ["moda.esm", "textures\\a.dds"])

    def test_missing_record(self):
        self.assertIsNone(vortex_deploy.read(self.data))
        self.assertIsNone(vortex_deploy.read(os.path.join(self.t, "nowhere")))
        self.assertIsNone(vortex_deploy.read(None))

    def test_corrupt_record(self):
        write(os.path.join(self.data, vortex_deploy.RECORD), "{not json")
        self.assertIsNone(vortex_deploy.read(self.data))
        write(os.path.join(self.data, vortex_deploy.RECORD), "[1, 2]")
        self.assertIsNone(vortex_deploy.read(self.data))
        write(os.path.join(self.data, vortex_deploy.RECORD), '{"files": "nope"}')
        self.assertIsNone(vortex_deploy.read(self.data))

    def test_bad_entries_skipped_and_bom(self):
        p = record(self.data, None, [("a\\b.txt", "m1")])
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["files"] += [{"relPath": "x.dds"}, {"source": "m2"}, "junk", {"relPath": "", "source": "m3"}]
        with open(p, "w", encoding="utf-8-sig") as f:
            json.dump(d, f)
        dep = vortex_deploy.read(self.data)
        self.assertEqual(dep.files, {"a\\b.txt": "m1"})
        self.assertEqual(dep.skipped, 4)
        self.assertIsNone(dep.staging)

    def test_record_name_case_and_other_records(self):
        record(self.data, self.staging, [("a.txt", "m1"), ("b.txt", "m1")], name="Vortex.Deployment.Starfield-Data-Folder.JSON")
        record(self.data, None, [("b.txt", "other"), ("c.txt", "m2")], name="vortex.deployment.json")
        d = vortex_deploy.read(self.data)
        self.assertEqual(d.files, {"a.txt": "m1", "b.txt": "m1", "c.txt": "m2"})  # the main record wins
        self.assertEqual(len(d.records), 2)
        self.assertEqual(d.staging, self.staging)

    def test_owner_case_and_slashes(self):
        record(self.data, self.staging, self.files)
        d = vortex_deploy.read(self.data)
        self.assertEqual(vortex_deploy.owner(d, "TEXTURES/ROCK.DDS"), "retex-2")
        self.assertEqual(vortex_deploy.owner(d, "\\sfse\\plugins\\foo.dll"), "sfse-dll-3")
        self.assertIsNone(vortex_deploy.owner(d, "mytool\\stray.cfg"))
        self.assertIsNone(vortex_deploy.owner(None, "textures\\a.dds"))


class Enrich(Base):
    def test_adds_loose_only_mods(self):
        record(self.data, self.staging, self.files)
        lo = self.lo()
        before = list(lo.plugins)
        fs = vortex_deploy.enrich(lo, vortex_deploy.read(self.data))
        self.assertEqual(lo.plugins, before)
        got = {m.id: (m.enabled, m.path, m.plugins, m.files) for m in lo.mods}
        self.assertEqual(got["retex-2"], (True, os.path.join(self.staging, "retex-2"), [], ["textures\\rock.dds"]))
        self.assertEqual(got["sfse-dll-3"], (True, None, [], ["sfse\\plugins\\foo.dll"]))  # no staging folder
        self.assertEqual(got["moda-1"][3], ["moda.esm", "textures\\a.dds"])  # listed mod: files filled
        self.assertEqual([m.id for m in lo.mods], ["moda-1", "retex-2", "sfse-dll-3"])
        self.assertEqual(lo.files["vortex_deployment"], os.path.join(self.data, vortex_deploy.RECORD))
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0].section, "inputs")
        self.assertEqual(fs[0].data["mods"], ["retex-2", "sfse-dll-3"])
        self.assertEqual(fs[0].data["no_folder"], ["sfse-dll-3"])

    def test_mod_id_case_matches_listed_mod(self):
        record(self.data, self.staging, [("ModA.esm", "MODA-1"), ("x\\y.txt", "MODA-1")])
        lo = self.lo()
        vortex_deploy.enrich(lo, vortex_deploy.read(self.data))
        self.assertEqual([m.id for m in lo.mods], ["moda-1"])
        self.assertEqual(lo.mods[0].files, ["moda.esm", "x\\y.txt"])

    def test_listed_mod_with_plugin_off_counts_as_deployed(self):
        record(self.data, self.staging, self.files)
        lo = self.lo()
        lo.mods[0].enabled = False  # the export marks a mod enabled only through its plugins
        lo.mods.append(Mod("notdeployed-5", False, plugins=["X.esm"], id="notdeployed-5"))
        vortex_deploy.enrich(lo, vortex_deploy.read(self.data))
        got = {m.id: m.enabled for m in lo.mods}
        self.assertTrue(got["moda-1"])
        self.assertFalse(got["notdeployed-5"])

    def test_existing_files_kept(self):
        record(self.data, self.staging, self.files)
        lo = self.lo()
        lo.mods[0].files = ["keep.txt"]
        vortex_deploy.enrich(lo, vortex_deploy.read(self.data))
        self.assertEqual(lo.mods[0].files, ["keep.txt"])

    def test_deployed_plugin_of_unlisted_mod_takes_data_case(self):
        fixtures.plugin(os.path.join(self.data, "Extra.esp"))
        record(self.data, self.staging, self.files + [("extra.esp", "extra-9")])
        lo = self.lo()
        vortex_deploy.enrich(lo, vortex_deploy.read(self.data))
        self.assertEqual(next(m for m in lo.mods if m.id == "extra-9").plugins, ["Extra.esp"])

    def test_staging_absent(self):
        gone = os.path.join(self.t, "NoStaging")
        record(self.data, gone, self.files)
        lo = self.lo()
        lo.files.pop("vortex_staging")
        lo.mods[0].path = None
        vortex_deploy.enrich(lo, vortex_deploy.read(self.data))
        self.assertTrue(all(m.path is None for m in lo.mods))
        self.assertNotIn("vortex_staging", lo.files)
        self.assertEqual(len(lo.mods), 3)

    def test_record_staging_fills_paths_when_inputs_had_none(self):
        record(self.data, self.staging, self.files)
        lo = self.lo()
        lo.files.pop("vortex_staging")
        lo.mods[0].path = None
        vortex_deploy.enrich(lo, vortex_deploy.read(self.data))
        self.assertEqual(lo.files["vortex_staging"], self.staging)
        self.assertEqual(lo.mods[0].path, os.path.join(self.staging, "moda-1"))

    def test_no_deployment_or_other_source(self):
        lo = self.lo()
        self.assertEqual(vortex_deploy.enrich(lo, None), [])
        self.assertEqual(len(lo.mods), 1)
        self.assertNotIn("vortex_deployment", lo.files)
        record(self.data, self.staging, self.files)
        mo2 = LoadOrder("mo2", data=self.data, mods=[])
        self.assertEqual(vortex_deploy.enrich(mo2, vortex_deploy.read(self.data)), [])
        self.assertEqual(mo2.mods, [])

    def test_mods_none_becomes_list(self):
        record(self.data, self.staging, self.files)
        lo = LoadOrder("vortex", data=self.data)
        vortex_deploy.enrich(lo, vortex_deploy.read(self.data))
        self.assertEqual(sorted(m.id for m in lo.mods), ["moda-1", "retex-2", "sfse-dll-3"])

    def test_through_inputs_read(self):
        """The real export path: inputs.read() then enrich(), as the master will wire it."""
        record(self.data, self.staging, self.files)
        lof = fixtures.vortex_loadorder(os.path.join(self.t, "lo.json"),
                                        [("Starfield.esm", True), ("ModA.esm", True, "moda-1")])
        env = {"LOCALAPPDATA": os.path.join(self.t, "L"), "APPDATA": os.path.join(self.t, "A")}
        a = cli.parser().parse_args(["--vortex-loadorder", lof, "--data", self.data, "--vortex-staging", self.staging])
        lo, err = inputs.read(a, env=env, drives=[], registry=lambda: None)
        self.assertIsNone(err)
        order = [p.name for p in lo.plugins]
        vortex_deploy.enrich(lo, vortex_deploy.read(lo.data))
        self.assertEqual([p.name for p in lo.plugins], order)
        self.assertEqual([m.id for m in lo.mods], ["moda-1", "retex-2", "sfse-dll-3"])


if __name__ == "__main__":
    unittest.main()
