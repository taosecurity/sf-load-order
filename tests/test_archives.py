"""Tests for sf_load_order.archives. Synthetic data only (tests.fixtures).

Main setup (one Data folder, plugins.txt source): A.esm, B.esm (masters A), C.esm, D.esm + E.esm (one mod),
F.esm, G.esm, H.esm, Z.esm (listed, disabled).
  A - Main.ba2 (v1)   textures\\x.dds "1", meshes\\m.nif "a"
  B - Main.ba2        textures\\x.dds "2"           -> B beats A, built on A: "built"
  C - Main.ba2 (v3)   meshes\\m.nif "c"             -> C beats A with other bytes: "overlap"
  C - Textures.ba2    textures\\y.dds (DX10)
  C - Voices_de.ba2, C - Main_xbox.ba2              -> left out on purpose
  D/E - Main.ba2      sound\\s.wem                  -> "samemod"
  F/G - Main.ba2      scripts\\q.pex same bytes (stored and zlib) -> "same"
  G/H - Main.ba2      a content-named geometry mesh  -> "same"
  H - Main01.ba2 (loads), H - Main03.ba2 (gap), H - Meshes.ba2 (bad name) -> won't load
  Old - Main.ba2 (nobody's), Z - Main.ba2 (disabled plugin) -> no enabled plugin loads them
  Starfield - Textures01.ba2 -> base game, never a mod archive
"""
import hashlib
import os
import struct
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import archives, cli, model  # noqa: E402
from tests import fixtures  # noqa: E402

try:
    import lz4.block  # noqa: F401
    HAVE_LZ4 = True
except ImportError:
    HAVE_LZ4 = False

MESH = "geometries\\" + "a" * 20 + "\\" + "b" * 20 + ".mesh"


def P(name, path, masters=(), enabled=True, official=False):
    return model.Plugin(name=name, enabled=enabled, official=official, path=path, kind="F",
                        masters=list(masters))


def ctx_for(lo, tmp, no_state=False):
    args = types.SimpleNamespace(out=tmp, state=os.path.join(tmp, "state"), no_state=no_state)
    return cli.Context(args, lo, model.Report("test"))


def build_main(d):
    data = os.path.join(d, "Data")
    b = lambda n, files, **kw: fixtures.ba2(os.path.join(data, n), files, **kw)  # noqa: E731
    b("A - Main.ba2", {"Textures/X.dds": b"1", "meshes\\m.nif": b"a"}, version=1)
    b("B - Main.ba2", {"textures\\x.dds": b"2"})
    b("C - Main.ba2", {"meshes\\m.nif": b"c"}, version=3)
    b("C - Textures.ba2", {"textures\\y.dds": b""}, kind=b"DX10")
    b("C - Voices_de.ba2", {"sound\\z.wem": b"z"})
    b("C - Main_xbox.ba2", {"meshes\\m.nif": b"x"})
    b("D - Main.ba2", {"sound\\s.wem": b"s"})
    b("E - Main.ba2", {"sound\\s.wem": b"t"})
    b("F - Main.ba2", {"scripts\\q.pex": b"same"})
    b("G - Main.ba2", {"scripts\\q.pex": ("zlib", b"same"), MESH: ("zlib", b"m1")})
    b("H - Main.ba2", {MESH: b"other"})
    b("H - Main01.ba2", {"misc\\h1.txt": b"h1"})
    b("H - Main03.ba2", {"misc\\h3.txt": b"h3"})
    b("H - Meshes.ba2", {"meshes\\h.nif": b"h"})
    b("Old - Main.ba2", {"textures\\old.dds": b"o"})
    b("Z - Main.ba2", {"textures\\z.dds": b"z"})
    b("Starfield - Textures01.ba2", {"textures\\x.dds": b"base"})
    plugins = [P("Starfield.esm", os.path.join(data, "Starfield.esm"), official=True)]
    for n, m in (("A", ()), ("B", ("A.esm",)), ("C", ()), ("D", ()), ("E", ()), ("F", ()), ("G", ()), ("H", ())):
        path = fixtures.plugin(os.path.join(data, n + ".esm"), masters=list(m))
        plugins.append(P(n + ".esm", path, m))
    plugins.append(P("Z.esm", fixtures.plugin(os.path.join(data, "Z.esm")), enabled=False))
    fixtures.plugin(os.path.join(data, "Starfield.esm"))
    mods = [model.Mod("DE mod", True, None, ["D.esm", "E.esm"])]
    return model.LoadOrder(source="plugins.txt", data=data, plugins=plugins, mods=mods)


class NameRule(unittest.TestCase):
    def test_exact_names_only(self):
        with tempfile.TemporaryDirectory() as d:
            names = ["H - Main.ba2", "H - Main01.ba2", "H - Main03.ba2", "H - Textures.ba2", "H - Voices_en.ba2",
                     "H - Meshes.ba2", "H - Addon - Main.ba2", "H - Addon - Voices_fr.ba2"]
            view = {n.lower(): os.path.join(d, n) for n in names}
            owned, _taken = archives.assign([P("H.esm", "x"), P("H - Addon.esm", "y")], lambda p: view)
        got = {k: [os.path.basename(a) for a in v] for k, v in owned.items()}
        self.assertEqual(got, {"H.esm": ["H - Main.ba2", "H - Main01.ba2", "H - Textures.ba2", "H - Voices_en.ba2"],
                               "H - Addon.esm": ["H - Addon - Main.ba2"]})

    def test_base_archive_exact_stem(self):
        """Only '<official stem> - ' counts as the game's: a mod whose name starts with an official one doesn't."""
        base = ["Starfield - Textures01.ba2", "Constellation - Main.ba2", "ShatteredSpace - Voices_en.ba2",
                "SFBGS003 - Main.ba2", "BlueprintShips-Starfield - Main.ba2", "CreationKit - Shaders.ba2",
                "OldMars - Textures.ba2"]
        mods = ["ConstellationRedux - Main.ba2", "ShatteredSpaceTweaks - Main.ba2", "SFBGS003Patch - Main.ba2",
                "OldMarsPlus - Main.ba2", "BlueprintShips-Mine - Main.ba2", "StarfieldPlus - Main.ba2"]
        for n in base:
            with self.subTest(n=n):
                self.assertTrue(archives.is_base_archive(n))
        for n in mods:
            with self.subTest(n=n):
                self.assertFalse(archives.is_base_archive(n))
        self.assertTrue(archives.is_base_archive("SFBGS0AB - Main.ba2", {"sfbgs0ab"}))

    def test_left_out_on_purpose(self):
        for n in ("X - Main_xbox.ba2", "X - Voices_fr.ba2", "X - Voices_en_ps.ba2", "X - Voices_ptbr.ba2"):
            self.assertTrue(archives.skipped_on_purpose(n), n)
        self.assertFalse(archives.skipped_on_purpose("X - Voices_en.ba2"))


class Main(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name
        self.lo = build_main(self.d)
        self.ctx = ctx_for(self.lo, self.d)
        self.status = archives.run(self.ctx)
        self.r = self.ctx.results["archives"]

    def tearDown(self):
        self.t.cleanup()

    def test_pairs_and_advice(self):
        got = {(p["loser"], p["winner"]): p["advice"] for p in self.r["pairs"]}
        self.assertEqual(got, {("A.esm", "B.esm"): "built", ("A.esm", "C.esm"): "overlap",
                               ("D.esm", "E.esm"): "samemod", ("F.esm", "G.esm"): "same", ("G.esm", "H.esm"): "same"})
        self.assertEqual(self.r["pairs"][0]["advice"], "overlap")
        ac = next(p for p in self.r["pairs"] if p["winner"] == "C.esm")
        self.assertEqual((ac["differ"], ac["same"]), (1, 0))
        self.assertEqual(self.r["order_decides"], ["A.esm", "C.esm"])

    def test_archives_per_plugin(self):
        pl = self.r["plugins"]
        self.assertEqual(pl["C.esm"]["archives"], ["C - Main.ba2", "C - Textures.ba2"])
        self.assertEqual(pl["H.esm"]["archives"], ["H - Main.ba2", "H - Main01.ba2"])
        self.assertNotIn("Starfield.esm", pl)

    def test_wont_load_and_orphans(self):
        bad = {(os.path.basename(m["archive"]), m["plugin"], m["why"]) for m in self.r["misnamed"]}
        self.assertEqual(bad, {("H - Main03.ba2", "H.esm", "gap"), ("H - Meshes.ba2", "H.esm", "name")})
        orph = {(os.path.basename(o["archive"]), o["plugin"]) for o in self.r["orphans"]}
        self.assertEqual(orph, {("Old - Main.ba2", None), ("Z - Main.ba2", "Z.esm")})

    def test_digests(self):
        c = archives.Cache()
        data = self.lo.data
        self.assertEqual(c.digest(os.path.join(data, "C - Textures.ba2"), "textures\\y.dds"),
                         "tex:" + hashlib.sha1(b"").hexdigest())
        self.assertEqual(c.digest(os.path.join(data, "F - Main.ba2"), "scripts\\q.pex"),
                         c.digest(os.path.join(data, "G - Main.ba2"), "scripts\\q.pex"))
        self.assertEqual(archives.ba2_names(os.path.join(data, "A - Main.ba2")), ["textures\\x.dds", "meshes\\m.nif"])

    def test_findings(self):
        f = self.ctx.report.findings
        self.assertTrue(all(x.section == "archives" for x in f))
        caut = [x for x in f if x.level == "caution"]
        self.assertEqual(sum(1 for x in caut if x.plugins == ["A.esm", "C.esm"]), 1)
        self.assertEqual(sum(1 for x in caut if "won't load" in x.text), 2)
        self.assertTrue(any(x.level == "note" and "need nothing" in x.text for x in f))
        self.assertTrue(any(x.level == "note" and "Old - Main.ba2" in x.text for x in f))
        self.assertEqual(self.status["status"], "done")

    def test_flips(self):
        old = [p.name for p in self.lo.plugins]
        new = ["Starfield.esm", "C.esm", "A.esm", "B.esm", "D.esm", "E.esm", "H.esm", "G.esm", "F.esm"]
        fl = archives.flips(old, new, self.r)
        self.assertEqual([(x["path"], x["before"], x["after"]) for x in fl], [("meshes\\m.nif", "C.esm", "A.esm")])
        self.assertEqual(archives.flips(old, old, self.r), [])

    def test_cache_file_and_reuse(self):
        path = os.path.join(self.d, "state", "cache", "archives.json")
        self.assertTrue(os.path.isfile(path))
        c = archives.Cache(path)
        archives.analyse(self.lo, c)
        self.assertFalse(c.changed, "second run reads everything from the cache")

    def test_no_state_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            ctx = ctx_for(self.lo, t, no_state=True)
            archives.run(ctx)
            self.assertFalse(os.path.exists(os.path.join(t, "state")))

    def test_archived_sources(self):
        src = archives.archived_sources(self.lo, self.r, archives.Cache())
        self.assertEqual(src["textures\\x.dds"][0], "B.esm", "mods' archives load after the base game's")
        self.assertNotIn("textures\\old.dds", src)


class Vortex(unittest.TestCase):
    def test_orphans_only_when_data_matches(self):
        with tempfile.TemporaryDirectory() as d:
            lo = build_main(d)
            lo.source = "vortex"
            ctx = ctx_for(lo, d)
            archives.run(ctx)
            self.assertTrue(ctx.results["archives"]["data_checked"])
            self.assertEqual(len(ctx.results["archives"]["orphans"]), 2)
            fixtures.plugin(os.path.join(lo.data, "Other.esm"))  # another profile's plugin is deployed
            ctx = ctx_for(lo, d)
            st = archives.run(ctx)
            self.assertFalse(ctx.results["archives"]["data_checked"])
            self.assertEqual(ctx.results["archives"]["orphans"], [])
            self.assertIn("note", st)

    def test_plugin_in_staging(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "Data")
            st = os.path.join(d, "staging", "S")
            fixtures.plugin(os.path.join(data, "Starfield.esm"))
            sp = fixtures.plugin(os.path.join(st, "S.esm"))
            fixtures.ba2(os.path.join(st, "S - Main.ba2"), {"scripts\\s.pex": b"1"})
            fixtures.ba2(os.path.join(st, "S - Spare.ba2"), {"scripts\\t.pex": b"1"})
            lo = model.LoadOrder(source="vortex", data=data, plugins=[
                P("Starfield.esm", os.path.join(data, "Starfield.esm"), official=True), P("S.esm", sp)],
                mods=[model.Mod("S", True, st, ["S.esm"])])
            r = archives.analyse(lo, archives.Cache())
            self.assertEqual(r["plugins"]["S.esm"]["archives"], ["S - Main.ba2"], "found next to the plugin")
            self.assertEqual([os.path.basename(m["archive"]) for m in r["misnamed"]], ["S - Spare.ba2"])


class MO2(unittest.TestCase):
    def test_merged_view_and_priority(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "game", "Data")
            fixtures.plugin(os.path.join(data, "Starfield.esm"))
            fixtures.mo2(d, "Default", [("M1", True, {"M1.esm": {}}), ("Pack", True, {"x.txt": b""}),
                                        ("Pack2", True, {"x.txt": b""}), ("M2", True, {"M2.esm": {}})])
            mods_dir = os.path.join(d, "mods")
            fixtures.ba2(os.path.join(mods_dir, "M1", "M1 - Main.ba2"), {"textures\\a.dds": b"m1"})
            # a texture pack from another mod folder ships M1's Textures archive; the higher priority copy wins
            fixtures.ba2(os.path.join(mods_dir, "Pack", "M1 - Textures.ba2"), {"textures\\b.dds": b"p1"})
            fixtures.ba2(os.path.join(mods_dir, "Pack2", "M1 - Textures.ba2"), {"textures\\b.dds": b"p2"})
            fixtures.ba2(os.path.join(mods_dir, "M2", "M2 - Main.ba2"), {"textures\\a.dds": b"m2"})
            mods = [model.Mod(n, True, os.path.join(mods_dir, n), pl, i)
                    for i, (n, pl) in enumerate((("M1", ["M1.esm"]), ("Pack", []), ("Pack2", []), ("M2", ["M2.esm"])))]
            lo = model.LoadOrder(source="mo2", data=data, plugins=[
                P("Starfield.esm", os.path.join(data, "Starfield.esm"), official=True),
                P("M1.esm", os.path.join(mods_dir, "M1", "M1.esm")), P("M2.esm", os.path.join(mods_dir, "M2", "M2.esm"))],
                mods=mods)
            r = archives.analyse(lo, archives.Cache())
            paths = r["plugins"]["M1.esm"]["paths"]
            self.assertEqual([os.path.basename(os.path.dirname(p)) for p in paths], ["M1", "Pack2"])
            self.assertEqual([(p["loser"], p["winner"], p["advice"]) for p in r["pairs"]],
                             [("M1.esm", "M2.esm", "overlap")])
            self.assertEqual(r["orphans"], [], "the lower-priority copy is hidden by MO2, not an orphan")


class Textures(unittest.TestCase):
    PX = bytes(range(256)) * 40 + b"\0" * 3000

    def test_dds_headers_and_cube(self):
        legacy = b"DDS " + b"\0" * 124
        dx10 = bytearray(b"DDS " + b"\0" * 144)
        dx10[84:88] = b"DX10"
        cube = bytearray(legacy)
        cube[112:116] = struct.pack("<I", 0x200)
        want = "tex:" + hashlib.sha1(self.PX).hexdigest()
        self.assertEqual(archives.bytes_digest("x.dds", legacy + self.PX), want)
        self.assertEqual(archives.bytes_digest("x.dds", bytes(dx10) + self.PX), want)
        self.assertIsNone(archives.bytes_digest("x.dds", bytes(cube) + self.PX))
        with tempfile.TemporaryDirectory() as d:
            t2 = fixtures.ba2(os.path.join(d, "T2 - Textures.ba2"), {"textures\\t.dds": ("zlib", self.PX)}, kind=b"DX10")
            g = fixtures.ba2(os.path.join(d, "G - Main.ba2"), {"textures\\t.dds": ("zlib", legacy + self.PX)})
            c = archives.Cache()
            self.assertEqual(c.digest(t2, "textures\\t.dds"), want)
            self.assertEqual(c.digest(g, "textures\\t.dds"), want)

    def test_damaged_archive_is_cant_compare(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Bad - Main.ba2")
            with open(p, "wb") as f:
                f.write(b"nope")
            self.assertIsNone(archives.Cache().digest(p, "x\\y.nif"))

    @unittest.skipUnless(HAVE_LZ4, "the lz4 package isn't installed")
    def test_lz4(self):
        with tempfile.TemporaryDirectory() as d:
            t3 = fixtures.ba2(os.path.join(d, "T3 - Textures.ba2"),
                              {"textures\\t.dds": ("lz4", self.PX[:5000], self.PX[5000:])}, version=3, kind=b"DX10")
            self.assertTrue(archives.ba2_lz4(t3))
            self.assertEqual(archives.Cache().digest(t3, "textures\\t.dds"), "tex:" + hashlib.sha1(self.PX).hexdigest())
            saved = archives._LZ4
            archives._LZ4 = None  # as if lz4 weren't installed
            try:
                c = archives.Cache()
                self.assertIsNone(c.digest(t3, "textures\\t.dds"))
                self.assertEqual(c.lz4_missing, {t3})
                self.assertNotIn("textures\\t.dds", c.archive(t3)["digest"], "not cached: lz4 may be installed later")
            finally:
                archives._LZ4 = saved

    @unittest.skipUnless(HAVE_LZ4, "the lz4 package isn't installed")
    def test_lz4_missing_note(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "Data")
            fixtures.ba2(os.path.join(data, "A - Main.ba2"), {"scripts\\a.pex": ("lz4", b"x" * 200)}, version=3)
            fixtures.ba2(os.path.join(data, "B - Main.ba2"), {"scripts\\a.pex": b"y"})
            lo = model.LoadOrder(source="plugins.txt", data=data, plugins=[
                P("A.esm", fixtures.plugin(os.path.join(data, "A.esm"))),
                P("B.esm", fixtures.plugin(os.path.join(data, "B.esm")))])
            saved = archives._LZ4
            archives._LZ4 = None
            try:
                ctx = ctx_for(lo, d, no_state=True)
                archives.run(ctx)
            finally:
                archives._LZ4 = saved
            self.assertTrue(any("pip install lz4" in f.text for f in ctx.report.findings))
            self.assertEqual(ctx.results["archives"]["pairs"][0]["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
