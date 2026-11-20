"""Tests for sf_load_order.loose (C3). Synthetic data only (tests.fixtures).

MO2 setup, lowest priority first:
  ModA   textures\\x.dds "1", scripts\\s.pex "a", meshes\\m.nif "loose", readme.txt, meta.ini
  ModB   textures\\x.dds "1" (identical to A's), scripts\\s.pex "b" (differs: B has the higher priority)
  ModC   ModC.esm + "ModC - Main.ba2" holding meshes\\m.nif "arc" -> A's loose m.nif hides it
Game Data: Starfield.esm, "Starfield - Textures01.ba2" holding textures\\base.dds, and loose files no mod ships:
  textures\\base.dds (hides the base game's copy), sfse\\plugins\\foo.dll + foo.log, scripts\\a.psc + scripts\\a.pex,
  mytool\\notes.cfg
"""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import archives, cli, loose, model, vortex_deploy  # noqa: E402
from tests import fixtures  # noqa: E402


def P(name, path, enabled=True, official=False, masters=()):
    return model.Plugin(name=name, enabled=enabled, official=official, path=path, kind="F", masters=list(masters))


def ctx_for(lo, tmp, no_state=False):
    args = types.SimpleNamespace(out=tmp, state=os.path.join(tmp, "state"), no_state=no_state)
    return cli.Context(args, lo, model.Report("test"))


def put(path, b):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b)


def build_mo2(d):
    data = os.path.join(d, "game", "Data")
    fixtures.plugin(os.path.join(data, "Starfield.esm"))
    fixtures.ba2(os.path.join(data, "Starfield - Textures01.ba2"), {"textures\\base.dds": b"base"})
    for rel, b in (("textures\\base.dds", b"mine"), ("sfse\\plugins\\foo.dll", b"d"), ("sfse\\plugins\\foo.log", b"l"),
                   ("scripts\\a.psc", b"s"), ("scripts\\a.pex", b"p"), ("mytool\\notes.cfg", b"n")):
        put(fixtures.on_disk(data, rel), b)
    fixtures.mo2(d, "Default", [
        ("ModA", True, {"textures\\x.dds": b"1", "scripts\\s.pex": b"a", "meshes\\m.nif": b"loose",
                        "readme.txt": b"r", "meta.ini": b"[General]"}),
        ("ModB", True, {"textures\\x.dds": b"1", "scripts\\s.pex": b"b"}),
        ("ModC", True, {"ModC.esm": {}}),
    ])
    mods_dir = os.path.join(d, "mods")
    fixtures.ba2(os.path.join(mods_dir, "ModC", "ModC - Main.ba2"), {"meshes\\m.nif": b"arc"})
    mods = [model.Mod(n, True, os.path.join(mods_dir, n), pl, i)
            for i, (n, pl) in enumerate((("ModA", []), ("ModB", []), ("ModC", ["ModC.esm"])))]
    plugins = [P("Starfield.esm", os.path.join(data, "Starfield.esm"), official=True),
               P("ModC.esm", os.path.join(mods_dir, "ModC", "ModC.esm"))]
    return model.LoadOrder(source="mo2", data=data, plugins=plugins, mods=mods)


class Skips(unittest.TestCase):
    def test_plugins_txt_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            lo = model.LoadOrder(source="plugins.txt", data=d, mods=None, mods_note="no mod manager")
            st = loose.run(ctx_for(lo, d))
        self.assertEqual(st, {"status": "skipped", "why": "no mod manager"})

    def test_no_mod_folders(self):
        with tempfile.TemporaryDirectory() as d:
            lo = model.LoadOrder(source="vortex", data=d, mods=[model.Mod("X", True, None)])
            st = loose.run(ctx_for(lo, d))
        self.assertEqual(st["status"], "skipped")
        self.assertIn("folder", st["why"])


class MO2(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name
        self.lo = build_mo2(self.d)
        self.ctx = ctx_for(self.lo, self.d)
        archives.run(self.ctx)
        self.status = loose.run(self.ctx)
        self.r = self.ctx.results["loose"]

    def tearDown(self):
        self.t.cleanup()

    def test_pairs(self):
        self.assertEqual(len(self.r["pairs"]), 1)
        pr = self.r["pairs"][0]
        self.assertEqual((pr["mods"], pr["files"], pr["same"], pr["differ"], pr["winner"], pr["advice"]),
                         (["ModA", "ModB"], 2, 1, 1, "ModB", "differ"))
        f = [x for x in self.ctx.report.findings if x.level == "caution" and "ModB has the higher priority" in x.text]
        self.assertEqual(len(f), 1)

    def test_files_owned_and_docs_skipped(self):
        a = next(m for m in self.lo.mods if m.name == "ModA")
        self.assertIn("readme.txt", a.files)
        self.assertNotIn("meta.ini", a.files)
        self.assertFalse(any(p["path"] == "readme.txt" for p in self.r["paths"]))

    def test_hides(self):
        got = {(h["loose"], h["archive"]): (h["files"], h["differ"], h["unknown"]) for h in self.r["hides"]}
        self.assertEqual(got, {("ModA", "ModC.esm"): (1, 1, 0), ("(no mod)", "(base game)"): (1, 0, 1)})
        self.assertTrue(any(x.level == "caution" and "ModC.esm's archives" in x.text for x in self.ctx.report.findings))

    def test_stray(self):
        s = self.r["stray"]
        self.assertEqual(s["count"], 6)
        by = {x["path"]: x["rule"] for x in s["files"]}
        self.assertEqual(by["sfse\\plugins\\foo.dll"], "sfse-dll")
        self.assertEqual(by["sfse\\plugins\\foo.log"], "sfse-log")
        self.assertEqual(by["scripts\\a.psc"], "psc-with-pex")
        self.assertEqual(by["mytool\\notes.cfg"], "tool-folder")
        self.assertEqual(s["folders"]["sfse"], 2)
        self.assertEqual(self.status["stray"], 6)

    def test_cache(self):
        path = os.path.join(self.d, "state", "cache", "loose.json")
        self.assertTrue(os.path.isfile(path))
        c = archives.Cache(path)
        loose.analyse(self.lo, c, archives.Cache(), self.ctx.results["archives"])
        self.assertFalse(c.changed, "second run reads the hashes from the cache")
        # a changed file is hashed again
        put(os.path.join(self.d, "mods", "ModB", "scripts", "s.pex"), b"a")
        st = os.stat(os.path.join(self.d, "mods", "ModB", "scripts", "s.pex"))
        os.utime(os.path.join(self.d, "mods", "ModB", "scripts", "s.pex"), ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        r = loose.analyse(self.lo, c, archives.Cache(), self.ctx.results["archives"])
        self.assertTrue(c.changed)
        self.assertEqual(r["pairs"][0]["advice"], "same")

    def test_without_archive_results(self):
        with tempfile.TemporaryDirectory() as t:
            ctx = ctx_for(self.lo, t, no_state=True)
            loose.run(ctx)
            self.assertEqual(len(ctx.results["loose"]["hides"]), 2)
            self.assertFalse(os.path.exists(os.path.join(t, "state")))


class Vortex(unittest.TestCase):
    def build(self, d):
        data, st = os.path.join(d, "Data"), os.path.join(d, "staging")
        fixtures.plugin(os.path.join(data, "Starfield.esm"))
        fixtures.plugin(os.path.join(data, "S1.esm"))
        fixtures.plugin(os.path.join(st, "S1", "S1.esm"))
        put(os.path.join(st, "S1", "textures", "t.dds"), b"one")
        put(os.path.join(st, "S1", "scripts", "u.pex"), b"u1")
        put(os.path.join(st, "S1", "textures", "readme.txt"), b"r")
        put(os.path.join(st, "S2", "textures", "t.dds"), b"two")
        put(os.path.join(st, "S2", "scripts", "u.pex"), b"u2")
        put(os.path.join(st, "S2", "fomod", "ModuleConfig.xml"), b"x")
        put(os.path.join(data, "textures", "t.dds"), b"two")        # S2's copy is deployed
        put(os.path.join(data, "scripts", "u.pex"), b"edited")      # matches neither
        put(os.path.join(data, "textures", "readme.txt"), b"r")     # S1 ships it: not stray
        put(os.path.join(data, "textures", "stray.dds"), b"s")
        mods = [model.Mod("S1", True, os.path.join(st, "S1"), ["S1.esm"]), model.Mod("S2", True, os.path.join(st, "S2"))]
        plugins = [P("Starfield.esm", os.path.join(data, "Starfield.esm"), official=True),
                   P("S1.esm", os.path.join(data, "S1.esm"))]
        return model.LoadOrder(source="vortex", data=data, plugins=plugins, mods=mods)

    def test_data_copy_named(self):
        with tempfile.TemporaryDirectory() as d:
            lo = self.build(d)
            # stray files need Vortex's deployment record (C1b): everything but stray.dds was deployed by a mod
            lo._vortex_deployment = vortex_deploy.Deployment(path="record", files={
                "textures\\t.dds": "S2", "scripts\\u.pex": "S1", "textures\\readme.txt": "S1", "s1.esm": "S1"})
            ctx = ctx_for(lo, d)
            loose.run(ctx)
            r = ctx.results["loose"]
            pr = r["pairs"][0]
            self.assertEqual((pr["mods"], pr["differ"], pr["in_data"], pr["nomatch"]),
                             (["S1", "S2"], 2, {"S2": 1}, 1))
            self.assertNotIn("winner", pr, "Vortex: its rules decide, no priority winner")
            f = next(x for x in ctx.report.findings if x.level == "caution")
            self.assertIn("Vortex's conflict rules decide", f.text)
            self.assertIn("Data holds S2's copy of 1", f.text)
            self.assertIn("match neither", f.text)
            self.assertEqual([x["path"] for x in r["stray"]["files"]], ["textures\\stray.dds"])

    def test_stray_only_when_data_matches(self):
        with tempfile.TemporaryDirectory() as d:
            lo = self.build(d)
            fixtures.plugin(os.path.join(lo.data, "Other.esm"))  # another profile is deployed
            ctx = ctx_for(lo, d, no_state=True)
            st = loose.run(ctx)
            self.assertIsNone(ctx.results["loose"]["stray"])
            self.assertIn("note", st)
            self.assertEqual(len(ctx.results["loose"]["pairs"]), 1, "pairs are still found")


class StrayRules(unittest.TestCase):
    def test_rules(self):
        known = {"sfse\\plugins\\mything.dll", "scripts\\x.pex"}
        res = loose.classify(["sfse\\plugins\\mythingcustom.ini", "sf1edit backups\\a.esm.backup.2026_01_01_00_00_00",
                              "backup\\autosave.bak", "scripts\\source\\x.psc", "video\\intro.bk2",
                              "textures\\a.dds.bak", "textures\\photos\\photo_2026-01-01-120000.png",
                              "textures\\sub\\q.dds", "__folder_managed_by_vortex", "odd.bin"],
                             known, {"textures\\sub": {"OnlyMod"}, "textures": {"OnlyMod", "Other"}})
        got = [r["rule"] for r in res]
        self.assertEqual(got, ["sfse-dll-settings", "xedit-backup", "ck-autosave", "psc-with-pex", "video-bk2",
                               "backup-suffix", "photo-mode", "mod-folder", "vortex-marker", "unknown"])
        self.assertEqual(res[7]["owner"], "OnlyMod")


if __name__ == "__main__":
    unittest.main()
