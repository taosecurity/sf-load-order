"""clashes.py tests. Synthetic data only: a tiny made-up masterlist written here (never a copy of LOOT's), plugins
from tests\\fixtures.py, and made-up plugin names."""
import json
import os
import sys
import tempfile
import types
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import clashes, cli  # noqa: E402
from sf_load_order.clashes import YamlError, read_yaml  # noqa: E402
from sf_load_order.model import LoadOrder, Plugin, Report  # noqa: E402
from tests import fixtures  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def crc(path):
    with open(path, "rb") as f:
        return zlib.crc32(f.read()) & 0xFFFFFFFF


class YamlTests(unittest.TestCase):
    def test_mappings_lists_and_scalars(self):
        d = read_yaml("# a comment\n"
                      "top:\n"
                      "  plain: Some words here  # trailing comment\n"
                      "  single: 'It''s # not a comment'\n"
                      "  double: \"tab\\there \\u00e9\"\n"
                      "  hex: 0x1A2B\n"
                      "  num: 12\n"
                      "  yes: true\n"
                      "  nothing: ~\n"
                      "  flow: [ 'a', b , 3 ]\n"
                      "  fmap: { x: 1, 'y': two }\n"
                      "  items:\n"
                      "    - one\n"
                      "    - name: 'X.esm'\n"
                      "      after: [ 'Y.esm' ]\n"
                      "    -\n"
                      "      nested: yes\n"
                      "  same_indent:\n"
                      "  - p\n"
                      "  - q\n")
        t = d["top"]
        self.assertEqual(t["plain"], "Some words here")
        self.assertEqual(t["single"], "It's # not a comment")
        self.assertEqual(t["double"], "tab\there \u00e9")
        self.assertEqual(t["hex"], 0x1A2B)
        self.assertEqual((t["num"], t["yes"], t["nothing"]), (12, True, None))
        self.assertEqual(t["flow"], ["a", "b", 3])
        self.assertEqual(t["fmap"], {"x": 1, "y": "two"})
        self.assertEqual(t["items"], ["one", {"name": "X.esm", "after": ["Y.esm"]}, {"nested": "yes"}])
        self.assertEqual(t["same_indent"], ["p", "q"])

    def test_anchors_aliases_merge(self):
        d = read_yaml("common:\n"
                      "  - &note\n"
                      "    type: say\n"
                      "    content: 'Text {0}.'\n"
                      "  - &grp Main Group\n"
                      "list:\n"
                      "  - <<: *note\n"
                      "    subs: [ 'A' ]\n"
                      "    type: warn\n"
                      "  - *grp\n"
                      "  - after: [ *grp ]\n")
        self.assertEqual(d["list"][0], {"type": "warn", "content": "Text {0}.", "subs": ["A"]})
        self.assertEqual(d["list"][1], "Main Group")
        self.assertEqual(d["list"][2], {"after": ["Main Group"]})

    def test_multiline_and_block_scalars(self):
        d = read_yaml("a: 'first\n"
                      "   second\n"
                      "\n"
                      "   third'\n"
                      "b: |\n"
                      "  line one\n"
                      "  line two\n"
                      "c: >-\n"
                      "  folded\n"
                      "  text\n"
                      "d: \"x\\\n"
                      "    y\"\n"
                      "e: [ 1,\n"
                      "     2 ]\n")
        self.assertEqual(d["a"], "first second\nthird")
        self.assertEqual(d["b"], "line one\nline two\n")
        self.assertEqual(d["c"], "folded text")
        self.assertEqual(d["d"], "xy")
        self.assertEqual(d["e"], [1, 2])

    def test_document_marker_and_empty(self):
        self.assertIsNone(read_yaml(""))
        self.assertEqual(read_yaml("---\nk: v\n"), {"k": "v"})

    def test_errors_name_the_line(self):
        cases = {"a:\n\t- x\n": 2, "a: 'never closed\nb: c\n": 1, "a: *nope\n": 1,
                 "a:\n  b: 1\n    c: 2\n": 3, "a: 1\na: 2\n": 2, "a: !tag x\n": 1, "just: [1, 2\n": 1}
        for text, line in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(YamlError) as cm:
                    read_yaml(text)
                self.assertEqual(cm.exception.line, line, str(cm.exception))


class ConditionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self._tmp.name, "Data")
        self.a = fixtures.plugin(os.path.join(self.data, "ExampleA.esm"))
        fixtures.plugin(os.path.join(self.data, "ExampleOff.esm"))
        os.makedirs(os.path.join(self.data, "SFSE", "Plugins"))
        with open(os.path.join(self.data, "SFSE", "Plugins", "ExampleTool.dll"), "wb") as f:
            f.write(b"dll")
        lo = LoadOrder("plugins.txt", data=self.data, plugins=[
            Plugin("ExampleA.esm", True, path=self.a),
            Plugin("ExampleOff.esm", False, path=os.path.join(self.data, "ExampleOff.esm"))])
        self.env = clashes.Env(lo)

    def tearDown(self):
        self._tmp.cleanup()

    def ev(self, c):
        return clashes.evaluate(c, self.env)[0]

    def test_paths_match_in_any_case(self):
        """The game ignores case, so a LOOT path finds the file however a (Linux) copy of Data spells it."""
        self.assertTrue(self.ev('file("sfse/plugins/exampletool.dll")'))
        self.assertTrue(self.ev('file("SFSE/PLUGINS/example[a-z]+\\.dll")'))
        self.assertFalse(self.ev('file("sfse/other/exampletool.dll")'))
        found = clashes._ci_join(self.data, "sfse/plugins/EXAMPLETOOL.DLL")
        self.assertEqual(os.path.normcase(found),
                         os.path.normcase(os.path.join(self.data, "SFSE", "Plugins", "ExampleTool.dll")))

    def test_simple_functions(self):
        self.assertTrue(self.ev('file("ExampleA.esm")'))
        self.assertTrue(self.ev('file("ExampleOff.esm")'))
        self.assertFalse(self.ev('file("ExampleMissing.esm")'))
        self.assertTrue(self.ev('active("ExampleA.esm")'))
        self.assertFalse(self.ev('active("ExampleOff.esm")'))
        self.assertTrue(self.ev('readable("SFSE/Plugins/ExampleTool.dll")'))
        self.assertTrue(self.ev('file("SFSE/Plugins/([^\\.]+\\.dll)")'))  # a regex file name
        self.assertTrue(self.ev('active("Example[A-Z]\\.esm")'))
        self.assertFalse(self.ev('many_active("Example.*\\.esm")'))
        self.assertTrue(self.ev('many("Example.*\\.esm")'))
        self.assertTrue(self.ev(f'checksum("ExampleA.esm", {crc(self.a):08X})'))
        self.assertFalse(self.ev('checksum("ExampleA.esm", DEADBEEF)'))
        self.assertTrue(self.ev(None))

    def test_logic(self):
        self.assertTrue(self.ev('not file("ExampleMissing.esm")'))
        self.assertTrue(self.ev('file("ExampleA.esm") and (active("ExampleA.esm") or file("ExampleMissing.esm"))'))
        self.assertFalse(self.ev('file("ExampleA.esm") and not active("ExampleA.esm")'))

    def test_unknown_is_never_guessed(self):
        for c in ('version("ExampleA.esm", "1.0", >=)', 'not product_version("../Starfield.exe", "1.0", <)',
                  'file("ExampleA.esm") and is_master("ExampleA.esm")', 'file("../outside.txt")',
                  'file("ExampleA.esm" garbled', 'file(ExampleA.esm)'):
            with self.subTest(c=c):
                v, why = clashes.evaluate(c, self.env)
                self.assertIsNone(v)
                self.assertTrue(why)

    def test_known_parts_settle_unknown_ones(self):
        # false and X = false, true or X = true, whatever X is
        self.assertFalse(self.ev('file("ExampleMissing.esm") and version("x", "1", >=)'))
        self.assertTrue(self.ev('file("ExampleA.esm") or version("x", "1", >=)'))


class Mo2ConditionTests(unittest.TestCase):
    """MO2 keeps mods in their own folders: LOOT paths are looked up in the enabled mods (highest priority first),
    then Data. Vortex deploys into Data, so only Data counts there."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = self._tmp.name
        self.data = os.path.join(t, "Data")
        os.makedirs(self.data)
        self.low, self.high, self.off = (os.path.join(t, "mods", n) for n in ("Low", "High", "Off"))
        for folder, body in ((self.low, b"low"), (self.high, b"high"), (self.off, b"off")):
            os.makedirs(os.path.join(folder, "SFSE", "Plugins"))
            name = "Gone.dll" if folder == self.off else "foo.dll"
            with open(os.path.join(folder, "SFSE", "Plugins", name), "wb") as f:
                f.write(body)
        from sf_load_order.model import Mod
        self.mods = [Mod("Low", True, path=self.low, priority=0), Mod("High", True, path=self.high, priority=1),
                     Mod("Off", False, path=self.off, priority=2)]

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, source):
        return clashes.Env(LoadOrder(source, data=self.data, plugins=[], mods=self.mods))

    def test_mo2_mod_folders_searched(self):
        e = self.env("mo2")
        ev = lambda c: clashes.evaluate(c, e)[0]  # noqa: E731
        self.assertTrue(ev('file("SFSE/Plugins/foo.dll")'))
        self.assertTrue(ev('readable("SFSE/Plugins/foo.dll")'))
        self.assertTrue(ev('file("SFSE/Plugins/fo+\\.dll")'))
        self.assertFalse(ev('many("SFSE/Plugins/fo+\\.dll")'))  # one file as the game sees it, not two copies
        self.assertTrue(ev(f'checksum("SFSE/Plugins/foo.dll", {zlib.crc32(b"high"):08X})'))  # the winning copy
        self.assertFalse(ev('file("SFSE/Plugins/Gone.dll")'))  # only in a disabled mod
        self.assertEqual(e.roots, [self.high, self.low, self.data])

    def test_vortex_uses_data_only(self):
        e = self.env("vortex")
        self.assertFalse(clashes.evaluate('file("SFSE/Plugins/foo.dll")', e)[0])
        self.assertEqual(e.roots, [self.data])

    def test_mo2_requirement_in_mod_folder(self):
        """A LOOT req on a non-plugin file that only an MO2 mod holds is installed, not missing."""
        t = self._tmp.name
        env = {"LOCALAPPDATA": os.path.join(t, "AppData")}
        d = os.path.join(env["LOCALAPPDATA"], "LOOT", "games", "Starfield")
        os.makedirs(d)
        with open(os.path.join(d, "masterlist.yaml"), "w", encoding="utf-8") as f:
            f.write("plugins:\n  - name: 'ExampleUser.esm'\n    req:\n      - 'SFSE/Plugins/foo.dll'\n"
                    "      - 'SFSE/Plugins/Gone.dll'\n")
        pl = fixtures.plugin(os.path.join(self.high, "ExampleUser.esm"))
        lo = LoadOrder("mo2", data=self.data, plugins=[Plugin("ExampleUser.esm", True, path=pl)], mods=self.mods)
        args = types.SimpleNamespace(out=os.path.join(t, "out"), clashes=None, accept=None, picks=None)
        ctx = cli.Context(args, lo, Report(tool_version="test"))
        clashes.run(ctx, env=env)
        probs = [f.text for f in ctx.report.findings if f.level == "problem"]
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("Gone.dll", probs[0])


MASTERLIST = """\
# made-up masterlist for tests
prelude:
  common:
    - &oldVersion
      type: warn
      content: 'This is an old version of {0}.'
plugins:
  - name: 'ExampleArmor.esm'
    req:
      - 'ExampleFramework.esm'
      - name: 'ExampleLib.esm'
        display: '[Example Lib](https://example.invalid/lib)'
      - name: 'ExampleNotThere.esm'
        condition: 'file("ExampleTrigger.esm")'
    inc:
      - 'ExampleRival.esm'
    msg:
      - <<: *oldVersion
        subs: [ 'Example Armor' ]
      - type: error
        content:
          - lang: de
            text: 'Deutsch'
          - lang: en
            text: 'Breaks saves.'
      - type: say
        content: 'Just saying.'
      - type: warn
        content: 'Never shown.'
        condition: 'file("ExampleMissing.esm")'
      - type: warn
        content: 'Unknown condition.'
        condition: 'version("ExampleArmor.esm", "2.0", <)'
    dirty:
      - crc: 0x{crc}
        util: 'xEdit 4.1.5'
        itm: 4
        udr: 2
      - crc: 0xDEADBEEF
        itm: 99
  - name: 'ExampleRival.esm'
    inc: [ 'ExampleArmor.esm' ]
  - name: 'ExampleWeapon.*\\.esm'
    msg:
      - type: warn
        content: 'Regex entry.'
  - name: 'ExampleNotEnabled.esm'
    msg:
      - type: error
        content: 'Not enabled, not shown.'
"""


def make_ctx(tmp, plugins, **args):
    a = dict(out=os.path.join(tmp, "out"), clashes=None, accept=None, picks=None)
    a.update(args)
    lo = LoadOrder(source="plugins.txt", data=os.path.join(tmp, "Data"), plugins=plugins)
    return cli.Context(types.SimpleNamespace(**a), lo, Report(tool_version="test"))


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = os.path.join(self.tmp, "Data")
        self.env = {"LOCALAPPDATA": os.path.join(self.tmp, "AppData")}

    def tearDown(self):
        self._tmp.cleanup()

    def p(self, name, enabled=True, official=False, file=True):
        path = fixtures.plugin(os.path.join(self.data, name), author=name) if file else None
        return Plugin(name, enabled, official=official, path=path)

    def write(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        return path

    def loot_dir(self):
        d = os.path.join(self.env["LOCALAPPDATA"], "LOOT", "games", "Starfield")
        os.makedirs(d, exist_ok=True)
        return d

    def texts(self, ctx, level=None):
        return [f.text for f in ctx.report.findings if level is None or f.level == level]


class LootTests(Base):
    def plugins(self):
        return [self.p("Starfield.esm", official=True), self.p("ExampleFramework.esm", enabled=False),
                self.p("ExampleArmor.esm"), self.p("ExampleRival.esm"), self.p("ExampleWeaponsPlus.esm"),
                self.p("ExampleNotEnabled.esm", enabled=False)]

    def test_masterlist_findings(self):
        ps = self.plugins()
        armor = ps[2].path
        with open(os.path.join(self.loot_dir(), "masterlist.yaml"), "w", encoding="utf-8") as f:
            f.write(MASTERLIST.replace("{crc}", f"{crc(armor):08X}"))
        ctx = make_ctx(self.tmp, ps)
        st = clashes.run(ctx, env=self.env)
        loot = st["parts"]["loot"]
        self.assertEqual(loot["status"], "done", loot)
        probs, cauts = self.texts(ctx, "problem"), self.texts(ctx, "caution")
        self.assertTrue(any("needs ExampleFramework.esm, which is installed but not enabled" in t for t in probs))
        self.assertTrue(any("needs [Example Lib](https://example.invalid/lib), which isn't installed" in t
                            for t in probs))
        self.assertFalse(any("ExampleNotThere" in t for t in probs))  # its condition is false
        inc = [t for t in probs if "don't work together" in t]
        self.assertEqual(len(inc), 1, inc)  # listed from both sides, reported once
        self.assertTrue(any("Breaks saves." in t for t in probs))
        self.assertTrue(any("This is an old version of Example Armor." in t for t in cauts))
        self.assertTrue(any("Regex entry." in t and "ExampleWeaponsPlus.esm" in t for t in cauts))
        dirty = [t for t in cauts if "needs cleaning" in t]
        self.assertEqual(len(dirty), 1, dirty)  # only the CRC that matches
        self.assertIn("4 identical-to-master records, 2 deleted references", dirty[0])
        all_t = " ".join(self.texts(ctx))
        for never in ("Just saying.", "Never shown.", "Unknown condition.", "Not enabled, not shown.", "Deutsch"):
            self.assertNotIn(never, all_t)
        self.assertEqual(loot["unknown_conditions"], 1)
        self.assertEqual(ctx.results["clashes"]["loot"]["incompatible"], [["ExampleArmor.esm", "ExampleRival.esm"]])

    def test_userlist_adds_entries(self):
        ps = self.plugins()
        d = self.loot_dir()
        with open(os.path.join(d, "masterlist.yaml"), "w", encoding="utf-8") as f:
            f.write("plugins: []\n")
        with open(os.path.join(d, "userlist.yaml"), "w", encoding="utf-8") as f:
            f.write("plugins:\n  - name: 'ExampleRival.esm'\n    msg:\n      - type: warn\n        content: 'Mine.'\n")
        ctx = make_ctx(self.tmp, ps)
        st = clashes.run(ctx, env=self.env)
        self.assertTrue(st["parts"]["loot"]["userlist"])
        self.assertTrue(any("Mine." in t for t in self.texts(ctx, "caution")))

    def test_no_masterlist_is_skipped_with_how_to(self):
        ctx = make_ctx(self.tmp, self.plugins())
        st = clashes.run(ctx, env=self.env)
        self.assertEqual(st["parts"]["loot"]["status"], "skipped")
        self.assertIn("Open LOOT Data Folder", st["parts"]["loot"]["why"])
        self.assertEqual(ctx.report.findings, [])
        self.assertEqual(clashes.run(make_ctx(self.tmp, self.plugins()), env={})["parts"]["loot"]["status"],
                         "skipped")

    def test_bad_masterlist_fails_only_its_part(self):
        with open(os.path.join(self.loot_dir(), "masterlist.yaml"), "w", encoding="utf-8") as f:
            f.write("plugins:\n  - name: 'ExampleArmor.esm'\n    msg: [ 'unclosed\n")
        picks = self.write("picks.json", [{"plugin": "ExampleRival.esm", "after": "ExampleArmor.esm"}])
        ctx = make_ctx(self.tmp, self.plugins(), picks=picks)
        st = clashes.run(ctx, env=self.env)
        self.assertEqual(st["parts"]["loot"]["status"], "failed")
        self.assertIn("masterlist.yaml", st["why"])
        bad = self.texts(ctx, "caution")
        self.assertEqual(len(bad), 1)
        self.assertIn("line 3", bad[0])
        self.assertEqual(st["parts"]["picks"]["status"], "done")
        self.assertEqual(len(ctx.results["picks"]), 1)


class ClashFileTests(Base):
    def test_levels_min_and_fixed_by(self):
        ps = [self.p("Starfield.esm", official=True), self.p("ExampleQuests.esm"), self.p("ExampleCompanion.esm"),
              self.p("ExampleQuestsPatch.esm"), self.p("ExampleArmor.esm"), self.p("ExampleArmorHD.esm"),
              self.p("ExampleLatePatch.esm"), self.p("ExampleLoner.esm")]
        path = self.write("clashes.json", {"clashes": [
            {"plugins": ["ExampleQuests.esm", "ExampleQuestsExpanded.esm", "ExampleCompanion.esm"], "min": 2,
             "level": "problem", "fixed_by": "ExampleQuestsPatch.esm", "note": "Same quest stages."},
            {"plugins": ["examplearmor.esm", "ExampleArmorHD.esm"], "level": "pick-one", "note": "Same armor."},
            {"plugins": ["ExampleLoner.esm", "ExampleAbsent.esm"], "level": "problem", "note": "Not active."},
            {"plugins": ["ExampleArmorHD.esm", "ExampleLoner.esm"], "level": "caution", "note": "Late patch.",
             "fixed_by": "ExampleArmor.esm"},
            {"plugins": ["ExampleLoner.esm"], "level": "caution", "note": "Patch not enabled.",
             "fixed_by": "ExampleGone.esm"}]})
        ctx = make_ctx(self.tmp, ps, clashes=path)
        st = clashes.run(ctx, env={})
        self.assertEqual(st["parts"]["clashes"]["active"], 4)
        notes = self.texts(ctx, "note")
        self.assertTrue(any("settled: ExampleQuestsPatch.esm is enabled and loads after them" in t for t in notes))
        pick = [f for f in ctx.report.findings if "Same armor." in f.text][0]
        self.assertEqual(pick.level, "caution")
        self.assertEqual(pick.plugins, ["ExampleArmor.esm", "ExampleArmorHD.esm"])  # real case from the order
        self.assertIn("--picks", pick.fix)
        ae = pick.data["accept_entry"]
        self.assertEqual(len(ae["files"]["ExampleArmor.esm"]["sha256"]), 64)
        late = [f for f in ctx.report.findings if "Late patch." in f.text][0]
        self.assertIn("must load after all of them", late.text)
        gone = [f for f in ctx.report.findings if "Patch not enabled." in f.text][0]
        self.assertIn("isn't enabled", gone.text)
        self.assertFalse(any("Not active." in t for t in self.texts(ctx)))

    def test_bad_entries_skipped_others_used(self):
        ps = [self.p("ExampleA.esm"), self.p("ExampleB.esm")]
        path = self.write("clashes.json", [
            {"plugins": ["ExampleA.esm", "ExampleB.esm"], "level": "severe", "note": "x"},
            {"plugins": ["ExampleA.esm"], "level": "caution", "note": "x", "fixedby": "ExampleB.esm"},
            {"plugins": "ExampleA.esm", "level": "caution", "note": "x"},
            {"plugins": ["ExampleA.esm", "ExampleB.esm"], "level": "caution", "note": "x", "min": 3},
            "not an object",
            {"plugins": ["ExampleA.esm", "ExampleB.esm"], "level": "caution", "note": "Good one."}])
        ctx = make_ctx(self.tmp, ps, clashes=path)
        st = clashes.run(ctx, env={})
        self.assertEqual(st["parts"]["clashes"]["bad_entries"], 5)
        bad = [t for t in self.texts(ctx, "caution") if "was skipped" in t]
        self.assertEqual(len(bad), 5)
        self.assertTrue(any("entry 1" in t and "'severe'" in t for t in bad))
        self.assertTrue(any("entry 2" in t and "unknown field 'fixedby'" in t for t in bad))
        self.assertTrue(any("Good one." in t for t in self.texts(ctx)))

    def test_bad_json_and_missing_file(self):
        bad = self.write("clashes.json", '[\n  {"plugins": ["ExampleA.esm"]\n  "level": "x"}\n]')
        ctx = make_ctx(self.tmp, [self.p("ExampleA.esm")], clashes=bad)
        st = clashes.run(ctx, env={})
        self.assertEqual(st["parts"]["clashes"]["status"], "failed")
        self.assertIn("line 3", self.texts(ctx)[0])
        self.assertEqual(ctx.report.findings[0].data["line"], 3)
        ctx = make_ctx(self.tmp, [], clashes=os.path.join(self.tmp, "nope.json"))
        st = clashes.run(ctx, env={})
        self.assertIn("file not found", st["parts"]["clashes"]["why"])
        self.assertIn("--clashes", ctx.report.findings[0].fix)


class SettledIfCurrentTests(Base):
    """0.2.0 (relay step 4): settled_if_current settles a clash when the named plugin is enabled and its installed
    file's SHA-256 is one of the hashes given (lov.py writes our current builds' hashes)."""

    def setup_pair(self):
        ps = [self.p("Starfield.esm", official=True), self.p("ExampleA.esm"), self.p("ExampleB.esm"),
              self.p("ExampleFix.esm")]
        sha = clashes._sha256(ps[3].path, {})[0]
        return ps, sha

    def run_one(self, ps, sc, **extra):
        e = {"plugins": ["ExampleA.esm", "ExampleB.esm"], "level": "problem", "note": "They fight.",
             "settled_if_current": sc}
        e.update(extra)
        ctx = make_ctx(self.tmp, ps, clashes=self.write("clashes.json", [e]))
        st = clashes.run(ctx, env={})
        return ctx, st, [f for f in ctx.report.findings if "They fight." in f.text]

    def test_current_build_settles(self):
        ps, sha = self.setup_pair()
        ctx, st, fs = self.run_one(ps, {"plugin": "examplefix.esm", "sha256": sha.upper()})
        self.assertEqual([f.level for f in fs], ["note"])
        self.assertIn("settled: ExampleFix.esm is the build this entry names", fs[0].text)
        self.assertIs(fs[0].data["settled"], True)
        self.assertEqual(fs[0].data["current"], "current")
        self.assertEqual(ctx.results["clashes"]["known"][0]["current"], "current")

    def test_a_list_with_the_hash_settles(self):
        ps, sha = self.setup_pair()
        _, _, fs = self.run_one(ps, {"plugin": "ExampleFix.esm", "sha256": ["0" * 64, sha]})
        self.assertEqual([f.level for f in fs], ["note"])

    def test_other_build_stays_open(self):
        ps, sha = self.setup_pair()
        fixtures.plugin(ps[3].path, author="newer build")
        _, _, fs = self.run_one(ps, {"plugin": "ExampleFix.esm", "sha256": sha})
        self.assertEqual([f.level for f in fs], ["problem"])
        self.assertIn("ExampleFix.esm isn't the build this entry names: install the current one.", fs[0].text)
        self.assertEqual(fs[0].data["current"], "not-current")
        self.assertNotIn("settled", fs[0].data)

    def test_not_enabled_stays_open(self):
        ps, sha = self.setup_pair()
        ps[3] = Plugin("ExampleFix.esm", False, path=ps[3].path)
        _, _, fs = self.run_one(ps, {"plugin": "ExampleFix.esm", "sha256": sha})
        self.assertEqual(fs[0].level, "problem")
        self.assertIn("isn't enabled", fs[0].text)
        self.assertEqual(fs[0].data["current"], "not-current")

    def test_fixed_by_still_settles_when_the_build_is_old(self):
        ps, _ = self.setup_pair()
        _, _, fs = self.run_one(ps, {"plugin": "ExampleFix.esm", "sha256": "0" * 64}, fixed_by="ExampleFix.esm")
        self.assertEqual(fs[0].level, "note")
        self.assertIs(fs[0].data["settled"], True)

    def test_bad_shapes_are_refused(self):
        ps, sha = self.setup_pair()
        for sc in ("ExampleFix.esm", {"plugin": "ExampleFix.esm"}, {"plugin": "ExampleFix.esm", "sha256": "abc"},
                   {"plugin": "ExampleFix.txt", "sha256": sha}, {"plugin": "ExampleFix.esm", "sha256": []},
                   {"plugin": "ExampleFix.esm", "sha256": sha, "size": 3}):
            ctx, st, fs = self.run_one(ps, sc)
            self.assertEqual(st["parts"]["clashes"]["bad_entries"], 1, sc)
            self.assertEqual(fs, [], sc)
            self.assertTrue(any("settled_if_current" in t for t in self.texts(ctx, "caution")), sc)


class AcceptTests(Base):
    def setup_pair(self):
        ps = [self.p("Starfield.esm", official=True), self.p("ExampleA.esm"), self.p("ExampleB.esm")]
        cl = self.write("clashes.json", [{"plugins": ["ExampleA.esm", "ExampleB.esm"], "level": "problem",
                                          "note": "They fight."}])
        first = make_ctx(self.tmp, ps, clashes=cl)
        clashes.run(first, env={})
        entry = [f for f in first.report.findings if "They fight." in f.text][0].data["accept_entry"]
        return ps, cl, entry

    def test_accept_settles_until_a_file_changes(self):
        ps, cl, entry = self.setup_pair()
        acc = self.write("accept.json", [entry])
        ctx = make_ctx(self.tmp, ps, clashes=cl, accept=acc)
        st = clashes.run(ctx, env={})
        self.assertEqual(st["parts"]["accept"]["valid"], 1)
        self.assertEqual([f.level for f in ctx.report.findings], ["note"])
        self.assertIn("you accepted it", ctx.report.findings[0].text)
        # an update to one plugin reopens the clash
        fixtures.plugin(ps[2].path, author="updated")
        ctx = make_ctx(self.tmp, ps, clashes=cl, accept=acc)
        st = clashes.run(ctx, env={})
        self.assertEqual(st["parts"]["accept"]["expired"], 1)
        exp = [f for f in ctx.report.findings if "changed since then" in f.text]
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0].data["changed"][0]["plugin"], "ExampleB.esm")
        reopened = [f for f in ctx.report.findings if "They fight." in f.text][0]
        self.assertEqual(reopened.level, "problem")
        self.assertIn("acceptance no longer matches", reopened.text)
        # a list of hashes: the old and the new one both count
        entry["files"]["ExampleB.esm"]["sha256"] = [entry["files"]["ExampleB.esm"]["sha256"],
                                                   reopened.data["accept_entry"]["files"]["ExampleB.esm"]["sha256"]]
        acc = self.write("accept.json", {"accepted": [entry]})
        ctx = make_ctx(self.tmp, ps, clashes=cl, accept=acc)
        self.assertEqual(clashes.run(ctx, env={})["parts"]["accept"]["valid"], 1)

    def test_official_counts_as_enabled_and_inactive(self):
        ps = [self.p("ExampleA.esm"), self.p("ExampleOff.esm", enabled=False)]
        sf = fixtures.plugin(os.path.join(self.data, "SFBGS050.esm"))
        cache = {}
        a_sha = clashes._sha256(ps[0].path, cache)[0]
        acc = self.write("accept.json", [
            {"plugins": ["ExampleA.esm", "SFBGS050.esm"], "files": {
                "ExampleA.esm": {"sha256": a_sha}, "SFBGS050.esm": {"sha256": clashes._sha256(sf, cache)[0]}}},
            {"files": {"ExampleA.esm": {"sha256": a_sha}, "ExampleOff.esm": {"sha256": "0" * 64}}}])
        ctx = make_ctx(self.tmp, ps, accept=acc)  # SFBGS050.esm isn't in the list: official, found in Data
        clashes.run(ctx, env={})
        st = {tuple(a["plugins"]): a["status"] for a in ctx.results["clashes"]["accepted"]}
        self.assertEqual(st[("ExampleA.esm", "SFBGS050.esm")], "valid")
        self.assertEqual(st[("ExampleA.esm", "ExampleOff.esm")], "inactive")

    def test_bad_accept_entries(self):
        acc = self.write("accept.json", [
            {"plugins": ["ExampleA.esm"], "files": {"ExampleA.esm": {"sha256": "abc"}}},
            {"plugins": ["ExampleA.esm", "ExampleB.esm"], "files": {"ExampleA.esm": {"sha256": "0" * 64}}},
            {"plugins": ["ExampleA.esm"]}])
        ctx = make_ctx(self.tmp, [self.p("ExampleA.esm")], accept=acc)
        st = clashes.run(ctx, env={})
        self.assertEqual(st["parts"]["accept"]["bad_entries"], 3)
        t = " | ".join(self.texts(ctx))
        self.assertIn("64 hex digits", t)
        self.assertIn("no hash given for ExampleB.esm", t)
        self.assertIn("'files' is missing", t)


class PicksTests(Base):
    def test_valid_and_invalid_picks(self):
        ps = [self.p("Starfield.esm", official=True), self.p("ExampleA.esm"), self.p("ExampleB.esm"),
              self.p("ExampleC.esm"), self.p("ExampleD.esm")]
        path = self.write("picks.json", {"picks": [
            {"plugin": "ExampleA.esm", "after": "ExampleB.esm", "why": "A wins"},
            {"plugin": "ExampleZ.esm", "after": "ExampleB.esm"},
            {"plugin": "ExampleA.esm", "after": "ExampleC.esm"},
            {"plugin": "ExampleB.esm", "after": "ExampleB.esm"},
            {"plugin": "Starfield.esm", "after": "ExampleB.esm"},
            {"plugin": "ExampleB", "after": "ExampleA.esm"},
            {"plugin": "ExampleC.esm", "after": "ExampleD.esm"},
            {"plugin": "ExampleD.esm", "after": "ExampleC.esm"},
            {"plugin": "ExampleB.esm", "after": "ExampleC.esm", "when": "x"}]})
        ctx = make_ctx(self.tmp, ps, picks=path)
        st = clashes.run(ctx, env={})
        got = [(g["plugin"], g["after"], g["active"]) for g in ctx.results["picks"]]
        self.assertEqual(got, [("ExampleA.esm", "ExampleB.esm", True), ("ExampleZ.esm", "ExampleB.esm", False)])
        self.assertEqual(st["parts"]["picks"]["bad_entries"], 7)
        t = " | ".join(self.texts(ctx))
        for want in ("already has a pick (entry 1)", "can't load after itself", "official plugin",
                     "must be a plugin file name", "form a loop", "unknown field 'when'"):
            self.assertIn(want, t)
        self.assertTrue(all(f.level == "caution" for f in ctx.report.findings))

    def test_no_files_given(self):
        ctx = make_ctx(self.tmp, [self.p("ExampleA.esm")])
        st = clashes.run(ctx, env={})
        self.assertEqual({k: v["status"] for k, v in st["parts"].items()},
                         {"accept": "skipped", "loot": "skipped", "clashes": "skipped", "picks": "skipped"})
        self.assertEqual(ctx.results["picks"], [])


class ExampleFileTests(Base):
    def test_examples_read_cleanly(self):
        ex = os.path.join(ROOT, "examples")
        ps = [self.p(n) for n in ("ExampleArmor.esm", "ExampleArmorRetexture.esm", "ExampleQuests.esm",
                                   "ExampleCompanion.esm", "ExampleQuestsPatch.esm")]
        ctx = make_ctx(self.tmp, ps, clashes=os.path.join(ex, "clashes.example.json"),
                       accept=os.path.join(ex, "accept.example.json"), picks=os.path.join(ex, "picks.example.json"))
        st = clashes.run(ctx, env={})
        for part in ("clashes", "accept", "picks"):
            self.assertEqual(st["parts"][part]["status"], "done", st["parts"][part])
            self.assertEqual(st["parts"][part]["bad_entries"], 0, part)
        self.assertEqual(len(ctx.results["picks"]), 2)
        self.assertEqual(st["parts"]["accept"]["expired"], 1)  # made-up hashes never match


if __name__ == "__main__":
    unittest.main()
