"""Tests for writers.py. Synthetic only: a Vortex export from fixtures.vortex_loadorder and load orders built in
memory (make_lo and ctx_for from test_recommend)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, inputs, model, recommend, writers  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_recommend import ctx_for, entry, make_lo, pair, rec_data  # noqa: E402


def read(path):
    with open(path, "rb") as f:
        return f.read()


def setUpModule():
    global _NEAR
    _NEAR = (recommend.NEAR_MIN, recommend.NEAR_SHARE)
    recommend.NEAR_MIN, recommend.NEAR_SHARE = 0, 0  # small test scores; see test_recommend.NearTie


def tearDownModule():
    recommend.NEAR_MIN, recommend.NEAR_SHARE = _NEAR


class PluginsTxt(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()

    def test_format(self):
        lo = make_lo(["A.esm", "-Off.esp", "B.esm"])
        lo.plugins.append(model.Plugin("Foo-Starfield.esm", True, dropped=True))
        lo.plugins.append(model.Plugin("BlueprintShips-Starfield.esm", True, official=True))
        ctx = ctx_for(self.t, lo)
        ctx.results["recommend"] = {"order": ["Starfield.esm", "SFBGS003.esm", "B.esm", "A.esm",
                                              "BlueprintShips-Starfield.esm"], "same_as_current": False}
        files = writers.write(ctx)
        raw = read(files["plugins_txt"])
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\r\r", raw)
        lines = raw.decode("utf-8").split("\r\n")
        self.assertTrue(lines[0].startswith("# This file is used by Starfield"))
        body = [x for x in lines if x and not x.startswith("#")]
        # official masters and dropped names left out; the disabled plugin stays after the one it followed (A)
        self.assertEqual(body, ["*B.esm", "*A.esm", "Off.esp"])
        self.assertEqual(os.path.basename(files["plugins_txt"]), "plugins.txt")
        self.assertNotIn("vortex_json", files)
        how = read(files["how_to"]).decode("utf-8")
        self.assertIn(r"%LOCALAPPDATA%\Starfield\Plugins.txt", how)
        self.assertIn("Back up first", how)

    def test_disabled_before_any_enabled_stays_first(self):
        lo = make_lo(["-Off.esp", "A.esm", "B.esm"])
        self.assertEqual(writers.plugins_txt_lines(lo, ["B.esm", "A.esm"])[2:], ["Off.esp", "*B.esm", "*A.esm"])

    def test_mo2_how_to(self):
        lo = make_lo(["A.esm"], source="mo2")
        lo.profile = "Default"
        ctx = ctx_for(self.t, lo)
        ctx.results["recommend"] = {"order": lo.enabled(), "same_as_current": True}
        how = read(writers.write(ctx)["how_to"]).decode("utf-8")
        self.assertIn("Close MO2", how)
        self.assertIn("loadorder.txt", how)
        self.assertIn("equal your current order", how)

    def test_mo2_pair_has_the_same_order(self):
        """MO2 (game_bethesda's GamebryoGamePlugins::readPluginLists) takes the order from loadorder.txt when it
        starts, and from plugins.txt only when plugins.txt alone changed while it ran. Either way the result must
        be the recommended order, so the two files must list the mod plugins in the same order (0.1.1, NOTES)."""
        lo = make_lo(["A.esm", "-Off.esp", "B.esm", "C.esm"], source="mo2")
        lo.profile = "Default"
        ctx = ctx_for(self.t, lo)
        ctx.results["recommend"] = {"order": ["Starfield.esm", "C.esm", "B.esm", "A.esm"], "same_as_current": False}
        files = writers.write(ctx)

        def body(role):
            return [x.lstrip("*") for x in read(files[role]).decode("utf-8").split("\r\n")
                    if x and not x.startswith("#")]
        mods = [n for n in body("loadorder_txt") if not inputs.is_official(n)]
        self.assertEqual(mods, ["C.esm", "B.esm", "A.esm", "Off.esp"])
        self.assertEqual(body("plugins_txt"), mods)
        self.assertEqual(body("loadorder_txt")[0], "Starfield.esm")  # MO2's file keeps the official masters

    def test_mo2_without_loadorder_txt_says_why(self):
        lo = make_lo(["A.esm"], source="mo2")
        out = os.path.join(self.t, "out")
        os.makedirs(os.path.join(out, "loadorder.txt"))  # a folder in the way: the file can't be written
        ctx = ctx_for(out, lo)
        ctx.results["recommend"] = {"order": lo.enabled(), "same_as_current": True}
        files = writers.write(ctx)
        self.assertNotIn("loadorder_txt", files)
        f = [x for x in ctx.report.findings if x.level == "caution" and x.section == "recommend"]
        self.assertEqual(len(f), 1)
        self.assertIn("loadorder.txt", f[0].text)
        self.assertTrue(f[0].fix)
        how = read(files["how_to"]).decode("utf-8")
        self.assertIn("couldn't be written", how)
        self.assertNotIn("Copy both", how)

    def test_only_writes_into_out(self):
        out = os.path.join(self.t, "out")
        lo = make_lo(["A.esm"])
        ctx = ctx_for(out, lo)
        ctx.results["recommend"] = {"order": lo.enabled(), "same_as_current": True}
        files = writers.write(ctx)
        for p in files.values():
            self.assertEqual(os.path.dirname(p), out)
        self.assertEqual(sorted(os.listdir(self.t)), ["out"])


class Vortex(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.export = fixtures.vortex_loadorder(os.path.join(self.t, "export.json"), [
            ("Starfield.esm", True), ("SFBGS003.esm", True), ("A.esm", True, "mod-a"), ("Off.esp", False, "mod-o"),
            ("B.esm", True, "mod-b"), ("C.esm", True, "mod-c")])
        self.lo = make_lo(["A.esm", "-Off.esp", "B.esm", "C.esm"], source="vortex")
        self.lo.files["vortex_loadorder"] = self.export
        self.out = os.path.join(self.t, "out")

    def run_rec(self, rec=None):
        ctx = ctx_for(self.out, self.lo)
        if rec:
            ctx.results["records"] = rec
        recommend.run(ctx)
        return ctx

    def test_round_trip_new_order(self):
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        ctx = self.run_rec(rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_)]))
        path = ctx.report.outputs["vortex_json"]
        self.assertEqual(os.path.basename(path), "vortex-loadorder-recommended.json")
        old = json.loads(read(self.export))
        new = json.loads(read(path))
        key = lambda e: json.dumps(e, sort_keys=True)  # noqa: E731
        self.assertEqual(sorted(map(key, old)), sorted(map(key, new)))       # the same entries, nothing changed
        self.assertEqual([e["name"] for e in new], ["Starfield.esm", "SFBGS003.esm", "b.esm", "a.esm", "off.esp",
                                                    "c.esm"])                 # lower case kept, disabled follows A
        for i, e in enumerate(old):
            if e["locked"]:
                self.assertEqual(new[i], e)                                  # locked entries in place
        self.assertIn("Import", read(ctx.report.outputs["how_to"]).decode("utf-8"))
        self.assertIn("Export", read(ctx.report.outputs["how_to"]).decode("utf-8"))
        self.assertIn("plugins_txt", ctx.report.outputs)

    def test_byte_identical_when_nothing_moves(self):
        ctx = self.run_rec()
        self.assertTrue(ctx.results["recommend"]["same_as_current"])
        self.assertEqual(read(ctx.report.outputs["vortex_json"]), read(self.export))
        self.assertIn("equal your current order", read(ctx.report.outputs["how_to"]).decode("utf-8"))

    def test_export_gone_is_caution_and_no_import_steps(self):
        os.remove(self.export)  # the export was read once; it's gone by the time the files are written
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        ctx = self.run_rec(rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_)]))
        self.assertNotIn("vortex_json", ctx.report.outputs)
        f = [x for x in ctx.report.findings if x.level == "caution" and "vortex-loadorder-recommended.json" in x.text]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].section, "recommend")
        self.assertIn(self.export, f[0].fix)
        how = read(ctx.report.outputs["how_to"]).decode("utf-8")
        self.assertNotIn("Click Import", how)
        self.assertIn("couldn't be written", how)

    def test_locked_entries_keep_positions_anywhere(self):
        entries = [{"name": "a.esm", "locked": False}, {"name": "Starfield.esm", "locked": True},
                   {"name": "b.esm", "locked": False}]
        new = writers.vortex_entries(entries, ["B.esm", "A.esm"])
        self.assertEqual([e["name"] for e in new], ["b.esm", "Starfield.esm", "a.esm"])


class CreationsAndMo2Officials(unittest.TestCase):
    """G06 (the Creations-only part of HOW-TO-APPLY) and A5 (MO2 and the official plugins)."""

    def setUp(self):
        self.t = tempfile.mkdtemp()

    def how(self, lo):
        ctx = ctx_for(self.t, lo)
        ctx.results["recommend"] = {"order": lo.enabled(), "same_as_current": True}
        return read(writers.write(ctx)["how_to"]).decode("utf-8")

    def test_creations_part_for_a_plain_source_only(self):
        plain = self.how(make_lo(["A.esm"]))
        self.assertIn("If you use only the game's Creations menu", plain)
        self.assertIn("then Load Order", plain)
        self.assertIn("https://taosecurity.github.io/starfield-load-order-facts/", plain)
        self.assertNotIn("Creations menu", self.how(make_lo(["A.esm"], source="mo2")))

    def mo2_lo(self, extra):
        lo = make_lo(["A.esm"], source="mo2")
        lo.profile = "Default"
        for n in ["Starfield.esm"] + extra:
            for p in lo.plugins:
                if p.name == n:
                    p.path = "x"
            if n not in [p.name for p in lo.plugins]:
                lo.plugins.append(model.Plugin(n, True, official=True, path="x", masters=[]))
        return lo

    def test_mo2_officials_step_and_caution(self):
        lo = self.mo2_lo(["ShatteredSpace.esm", "SFBGS004.esm"])
        found = inputs.mo2_unpinned_officials(lo)
        self.assertIn("ShatteredSpace.esm", found)
        self.assertIn("SFBGS004.esm", found)
        self.assertIn("still ticked", self.how(lo))
        ctx = ctx_for(self.t, lo)
        ctx.results["records"] = None
        recommend.run(ctx)
        got = [f for f in ctx.report.findings if f.data.get("kind") == "mo2-officials"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].level, "caution")
        self.assertEqual(got[0].data["plugins"], found)

    def test_only_pinned_officials_say_nothing(self):
        lo = self.mo2_lo(["SFBGS003.esm", "SFBGS006.esm"])
        for p in lo.plugins:
            if p.official and p.name not in ("Starfield.esm", "SFBGS003.esm", "SFBGS006.esm"):
                p.path = None  # not installed
        self.assertEqual(inputs.mo2_unpinned_officials(lo), [])
        self.assertNotIn("still ticked", self.how(lo))
        ctx = ctx_for(self.t, lo)
        ctx.results["records"] = None
        recommend.run(ctx)
        self.assertFalse([f for f in ctx.report.findings if f.data.get("kind") == "mo2-officials"])

    def test_other_sources_say_nothing(self):
        lo = self.mo2_lo(["ShatteredSpace.esm"])
        lo.source = "vortex"
        self.assertEqual(inputs.mo2_unpinned_officials(lo), [])


class Mo2RoundTrip(unittest.TestCase):
    """loadorder.txt + plugins.txt written for MO2 read back through inputs.from_mo2 give the recommended order."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name
        self.data, _ = fixtures.game(os.path.join(self.t, "Game"), {})
        self.inst = fixtures.mo2(os.path.join(self.t, "MO2"), "Default", [
            ("Mod A", True, {"A.esm": {"masters": ["Starfield.esm"]}}),
            ("Mod O", True, {"Off.esp": {"masters": ["Starfield.esm"]}}),
            ("Mod B", True, {"B.esm": {"masters": ["Starfield.esm"]}}),
            ("Mod C", True, {"C.esm": {"masters": ["Starfield.esm"]}})])
        self.prof = os.path.join(self.inst, "profiles", "Default")
        fixtures.plugins_txt(os.path.join(self.prof, "plugins.txt"),
                             [("A.esm", True), ("Off.esp", False), ("B.esm", True), ("C.esm", True)])
        with open(os.path.join(self.prof, "loadorder.txt"), "w", encoding="utf-8") as f:
            f.write("".join(n + "\n" for n in fixtures.OFFICIAL + ["A.esm", "Off.esp", "B.esm", "C.esm"]))
        self.out = os.path.join(self.t, "out")

    def tearDown(self):
        self._t.cleanup()

    def read_lo(self):
        a = cli.parser().parse_args(["--mo2", self.inst, "--data", self.data, "--out", self.out])
        lo, err = inputs.read(a, env={"LOCALAPPDATA": os.path.join(self.t, "L"), "APPDATA": os.path.join(self.t, "A")},
                              drives=[], registry=lambda: None)
        self.assertIsNone(err)
        return lo

    def test_round_trip(self):
        lo = self.read_lo()
        ctx = ctx_for(self.out, lo)
        ctx.results["recommend"] = {"order": ["B.esm", "A.esm", "C.esm"], "same_as_current": False}
        files = writers.write(ctx)
        self.assertIn("loadorder_txt", files)
        raw = read(files["loadorder_txt"]).decode("utf-8")
        lines = [x for x in raw.split("\r\n") if x]
        self.assertEqual(lines[0], "# This file was automatically generated by Mod Organizer.")
        self.assertFalse(any(x.startswith("*") for x in lines))
        self.assertEqual(lines[1:], fixtures.OFFICIAL + ["B.esm", "A.esm", "Off.esp", "C.esm"])  # disabled included
        how = read(files["how_to"]).decode("utf-8")
        self.assertIn("Close MO2", how)
        self.assertIn("Copy both plugins.txt and loadorder.txt", how)
        for k in ("plugins_txt", "loadorder_txt"):  # what the user does: copy both into the profile folder
            shutil.copyfile(files[k], os.path.join(self.prof, os.path.basename(files[k])))
        lo2 = self.read_lo()
        mods = [(p.name, p.enabled) for p in lo2.plugins if not p.official]
        self.assertEqual(mods, [("B.esm", True), ("A.esm", True), ("Off.esp", False), ("C.esm", True)])
        self.assertEqual([n for n in lo2.enabled() if n not in fixtures.OFFICIAL], ["B.esm", "A.esm", "C.esm"])


if __name__ == "__main__":
    unittest.main()
