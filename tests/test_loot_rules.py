"""Tests for the regex guard (clashes.safe_regex) and LOOT's ordering rules (loot_rules.py) (DESIGN-0.3.0.md 1.4 and
2.6). The masterlists here are made up; the three regex names are the ones today's Starfield masterlist uses (entry
names only, checked 2026-11-24)."""
import os
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import clashes, cli, loot_rules, sortgraph  # noqa: E402
from sf_load_order.model import LoadOrder, Plugin, Report  # noqa: E402
from tests import fixtures  # noqa: E402

REAL_REGEX_NAMES = (r"(blueprintships-)?sfta0[1-6]\.esm", r"MoreDramaticGravJumps\.es[mp]", r"legendaries\.es[mp]")

MASTERLIST = r"""
groups:
  - name: default
  - name: Early
  - name: Late
    after: [ default ]
plugins:
  - name: 'ExampleB.esm'
    after: [ 'ExampleA.esm' ]
    group: Late
  - name: 'ExampleC.esm'
    after:
      - name: 'ExampleA.esm'
        condition: 'active("ExampleD.esm")'
      - 'ExampleMissing.esm'
  - name: 'ExampleWeapon.*\.esm'
    after: [ 'ExampleC.esm' ]
  - name: 'ExampleD.esm'
    group: Early
  - name: '(a+)+\.esm'
    after: [ 'ExampleA.esm' ]
"""

USERLIST = """
plugins:
  - name: 'ExampleA.esm'
    after: [ 'ExampleB.esm' ]
"""


class Guard(unittest.TestCase):
    def test_the_three_real_regex_names_pass(self):
        for name in REAL_REGEX_NAMES:
            rx, why = clashes.safe_regex(name)
            self.assertIsNotNone(rx, f"{name}: {why}")
        rx = clashes.safe_regex(REAL_REGEX_NAMES[0])[0]
        self.assertTrue(clashes.rx_match(rx, "BlueprintShips-SFTA03.esm"))
        self.assertTrue(clashes.rx_match(rx, "sfta01.esm"))
        self.assertFalse(clashes.rx_match(rx, "sfta07.esm"))

    def test_nested_repeats_are_refused(self):
        for p in (r"(a+)+", r"(a+)+$", r"(a*)*", r"(x+){2,}", r"((ab)+)*", r"(?:a+)+", r"(a{1,3})+", r"([a-z]+)*",
                  r"(a|a)*\.esm", r"(a|ab)*c", r"(\w|\d)+\.esm", r"((a|b)c)+"):
            self.assertIn("repeat", clashes.regex_refusal(p), p)

    def test_many_open_ended_repeats_are_refused(self):
        for p in (r"a*a*a*a*a*a*a*a*\.esm", r".*.*.*.*.*.*\.esm", r"a+b+c{2,}", r"x*y*z*"):
            self.assertIn("open-ended", clashes.regex_refusal(p), p)
        for p in (r"a*b*", r".*\.esm", r"a{2,5}b{3}c{1,}"):
            self.assertIsNone(clashes.regex_refusal(p), p)

    def test_optional_repeats_count_too(self):
        # code review round 3: ? and {n,m} are repeats as well (each one doubles the work of a long chain)
        self.assertIn("repeat", clashes.regex_refusal(r"(a?a?)+b"))
        self.assertIn("repeat", clashes.regex_refusal(r"(a{0,2})*b"))
        self.assertIn("repeats", clashes.regex_refusal("a?" * 26 + "a" * 26))
        self.assertIn("repeats", clashes.regex_refusal("a{0,1}" * 8))
        for p in (r"(?:blueprintships-)?sfta0[1-6]\.esm", r"(?i)abc\.esm", r"(foo|bar)?\.esm", r"a?b?c?\.es[mp]"):
            self.assertIsNone(clashes.regex_refusal(p), p)

    def test_long_backrefs_and_lookbehinds_are_refused(self):
        self.assertIn("256", clashes.regex_refusal("a" * 300))
        self.assertIn("back-reference", clashes.regex_refusal(r"(a)\1"))
        self.assertIn("back-reference", clashes.regex_refusal(r"(?P<x>a)(?P=x)"))
        self.assertIn("look-behind", clashes.regex_refusal(r"(?<=a)b"))
        self.assertIn("look-behind", clashes.regex_refusal(r"(?<!a)b"))

    def test_fine_patterns_pass(self):
        for p in (r"(a+)?", r"(ab)+", r"a+b*", r"[(]+", r"\(a+\)+", r"foo\.es[mp]", r"(x|y)?z{2}", r"\d+\.esm",
                  r"a|b+", r"(foo|bar)\.esm"):
            self.assertIsNone(clashes.regex_refusal(p), p)

    def test_bad_regex_is_refused_not_raised(self):
        rx, why = clashes.safe_regex("(unclosed")
        self.assertIsNone(rx)
        self.assertIn("not a valid", why)

    def test_long_names_never_match(self):
        rx = clashes.safe_regex(r"a.*")[0]
        self.assertFalse(clashes.rx_match(rx, "a" * 261))
        self.assertTrue(clashes.rx_match(rx, "a" * 260))


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = os.path.join(self.tmp, "Data")
        self.env = {"LOCALAPPDATA": os.path.join(self.tmp, "AppData")}

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def lo(self, names=("ExampleA.esm", "ExampleB.esm", "ExampleC.esm", "ExampleD.esm", "ExampleWeaponsPlus.esm")):
        ps = [Plugin("Starfield.esm", True, official=True, masters=[])]
        for n in names:
            ps.append(Plugin(n, True, path=fixtures.plugin(os.path.join(self.data, n), author=n), masters=[]))
        return LoadOrder(source="plugins.txt", data=self.data, plugins=ps)

    def ctx(self, lo, **args):
        a = dict(out=os.path.join(self.tmp, "out"), clashes=None, accept=None, picks=None, no_loot=False,
                 loot_masterlist=self.write("masterlist.yaml", MASTERLIST), loot_userlist=None)
        a.update(args)
        return cli.Context(types.SimpleNamespace(**a), lo, Report(tool_version="test"))


class Reading(Base):
    def test_after_rules_conditions_and_regex_names(self):
        lo = self.lo()
        r = loot_rules.read(self.ctx(lo), self.env)
        got = sorted((a["plugin"], a["after"], a["source"]) for a in r["after"])
        self.assertEqual(got, [("ExampleB.esm", "ExampleA.esm", "loot_masterlist"),
                               ("ExampleC.esm", "ExampleA.esm", "loot_masterlist"),  # active("ExampleD.esm") is true
                               ("ExampleC.esm", "ExampleMissing.esm", "loot_masterlist"),
                               ("ExampleWeaponsPlus.esm", "ExampleC.esm", "loot_masterlist")])
        self.assertEqual(r["unsafe"], 1)  # (a+)+\.esm
        self.assertEqual((r["members"], r["groups"]), ([], []))  # groups only with --loot-groups

    def test_a_false_condition_leaves_the_item_out(self):
        lo = self.lo(("ExampleA.esm", "ExampleB.esm", "ExampleC.esm"))
        r = loot_rules.read(self.ctx(lo), self.env)
        self.assertNotIn(("ExampleC.esm", "ExampleA.esm"), [(a["plugin"], a["after"]) for a in r["after"]])

    def test_groups_only_with_loot_groups(self):
        r = loot_rules.read(self.ctx(self.lo()), self.env, groups=True)
        self.assertEqual(sorted((m["plugin"], m["group"]) for m in r["members"]),
                         [("ExampleB.esm", "Late"), ("ExampleD.esm", "Early")])
        self.assertEqual([(g["name"], g["after"]) for g in r["groups"]],
                         [("default", []), ("Early", []), ("Late", ["default"])])

    def test_no_loot_reads_nothing(self):
        r = loot_rules.read(self.ctx(self.lo(), no_loot=True), self.env)
        self.assertEqual((r["files"], r["after"]), ({}, []))

    def test_size_cap(self):
        old = clashes.MAX_LOOT_BYTES
        clashes.MAX_LOOT_BYTES = 10
        try:
            r = loot_rules.read(self.ctx(self.lo()), self.env)
        finally:
            clashes.MAX_LOOT_BYTES = old
        self.assertEqual(r["after"], [])
        self.assertIn("wasn't read", r["errors"][0])


class Ranks(Base):
    def test_userlist_outranks_masterlist(self):
        lo = self.lo()
        ul = self.write("userlist.yaml", USERLIST)
        loot = loot_rules.read(self.ctx(lo, loot_userlist=ul), self.env)
        b = sortgraph.build(lo, lo.enabled(), sortgraph.Sources(loot=loot))
        full = b.full
        self.assertLess(full.index("ExampleB.esm"), full.index("ExampleA.esm"))  # the userlist's rule won
        lost = [r for r in b.rejected if r["edge"].source == "loot_masterlist"]
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["level"], "note")
        self.assertIn("LOOT's masterlist says ExampleB.esm loads after ExampleA.esm, but LOOT's userlist says "
                      "ExampleA.esm loads after ExampleB.esm", sortgraph.rejected_text(lost[0]))

    def test_groups_order_plugins(self):
        lo = self.lo(("ExampleB.esm", "ExampleZ.esm", "ExampleD.esm"))
        loot = loot_rules.read(self.ctx(lo), self.env, groups=True)
        b = sortgraph.build(lo, lo.enabled(), sortgraph.Sources(loot=loot))
        # Late after default: ExampleB.esm (Late) goes after ExampleZ.esm (no group, so default); Early has no order
        self.assertEqual(b.full, ["Starfield.esm", "ExampleZ.esm", "ExampleB.esm", "ExampleD.esm"])
        self.assertEqual(len(sortgraph.recommend.moved(b.order, b.movable)), 1)
        without = sortgraph.build(lo, lo.enabled(), sortgraph.Sources(loot=loot_rules.read(self.ctx(lo), self.env)))
        self.assertEqual(without.full, lo.enabled())  # no groups without --loot-groups: nothing moves
        self.assertTrue(all(not n.startswith("<") for n in b.full))


class ClashesUsesTheGuard(Base):
    def test_run_loot_skips_and_counts_an_unsafe_name_quickly(self):
        evil = "a" * 30 + "!.esm"  # (a+)+ on this would take minutes without the guard
        lo = self.lo(("ExampleA.esm", evil))
        ml = self.write("ml.yaml", "plugins:\n  - name: '(a+)+\\.esm'\n    msg:\n      - type: warn\n"
                                   "        content: 'never shown'\n"
                                   "  - name: 'ExampleA.esm'\n    msg:\n      - type: warn\n"
                                   "        content: 'Shown.'\n"
                                   "        condition: 'active(\"(x+)+\\.esm\")'\n")
        ctx = self.ctx(lo, loot_masterlist=ml)
        t = time.monotonic()
        st, _res = clashes.run_loot(ctx, self.env, [], {})
        self.assertLess(time.monotonic() - t, 5)
        self.assertEqual(st["unsafe_patterns"], 2)  # the entry name and the condition's pattern
        self.assertEqual(st["msg"], 0)
        self.assertEqual(st["unknown_conditions"], 1)

    def test_a_clean_run_has_no_unsafe_key(self):
        lo = self.lo(("ExampleA.esm",))
        ml = self.write("ml.yaml", "plugins:\n  - name: 'Example.*\\.esm'\n    msg:\n      - type: warn\n"
                                   "        content: 'Shown.'\n")
        st, _res = clashes.run_loot(self.ctx(lo, loot_masterlist=ml), self.env, [], {})
        self.assertNotIn("unsafe_patterns", st)
        self.assertEqual(st["msg"], 1)


if __name__ == "__main__":
    unittest.main()
