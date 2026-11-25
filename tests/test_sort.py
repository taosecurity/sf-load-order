"""Tests for `sf-load-order sort` (sort.py; DESIGN-0.3.0.md 1.1 to 1.10 and 3.1): end to end through cli.main for a
Plugins.txt, an MO2 and a Vortex source, the files written, report.json's `sort` key, moves and their reasons, rules
that can't be met, requires and incompatible, stale rules, LOOT (made-up masterlist) and --no-loot, --no-rules, the
patch targets, the soft climb with a made-up conflicts.json and the near-tie rule, Vortex rules as a tie-break,
--tie-break name, and the 1,000-plugin timing test. Synthetic data only (tests\\fixtures.py); LOOT is off (--no-loot)
or pointed at a made-up file, so the real LOOT folder is never read."""
import argparse
import io
import json
import os
import random
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, model, recommend, records, rulesfile, sort, sortgraph  # noqa: E402
from tests import fixtures  # noqa: E402

SORT_KEYS = {"tie_break", "order", "moved", "moves", "score_current", "score_sorted", "estimate", "sources",
             "rejected", "inactive_rules", "requires_missing", "incompatible", "proposed", "stale", "files"}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue()


def lines(path):
    with open(path, encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if ln and not ln.startswith("#")]


def no_master_flag(path):
    """Clear the master flag (0x1) in a fixture plugin's header (the TES4 flags are at byte 8)."""
    with open(path, "r+b") as f:
        f.seek(8)
        flags = int.from_bytes(f.read(4), "little") & ~0x1
        f.seek(8)
        f.write(flags.to_bytes(4, "little"))


class Base(unittest.TestCase):
    PLUGINS = {"ExampleA.esm": {}, "ExampleB.esm": {}, "ExampleC.esm": {}, "ExampleD.esm": {},
               "ExamplePatch.esm": {"masters": ["Starfield.esm", "ExampleA.esm"]}}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.state = os.path.join(self.tmp, "state")
        self.out = os.path.join(self.tmp, "out")
        self.data, self.ptxt = fixtures.game(self.tmp, self.PLUGINS)
        self.src = ["--plugins-txt", self.ptxt, "--data", self.data]

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        return path

    def rules(self, rules, name="my-rules.json", kind="user", **top):
        return self.write(name, dict({"schema": 1, "kind": kind, "rules": rules}, **top))

    def sort(self, *extra, loot=False):
        argv = ["sort", "--depth", "light", "--state", self.state, "--out", self.out, *self.src, *extra]
        if not loot:
            argv.append("--no-loot")
        code, out = run(*argv)
        rj = os.path.join(self.out, "report.json")
        j = None
        if os.path.isfile(rj):
            with open(rj, encoding="utf-8") as f:
                j = json.load(f)
        return code, out, j

    def mods(self):
        return lines(os.path.join(self.out, "plugins.txt"))

    def finding(self, j, kind=None, text=None):
        return [f for f in j["findings"] if f["section"] == "sort" and (kind is None or f["data"].get("kind") == kind)
                and (text is None or text in f["text"])]


class PluginsTxt(Base):
    def test_no_rules_keeps_the_order(self):
        code, out, j = self.sort()
        self.assertEqual(code, 0)
        self.assertIn("RESULT:", out)
        s = j["sort"]
        self.assertTrue(SORT_KEYS <= set(s))
        self.assertEqual(s["moves"], [])
        self.assertTrue(s["same_as_current"])
        self.assertEqual(self.mods(), ["*ExampleA.esm", "*ExampleB.esm", "*ExampleC.esm", "*ExampleD.esm",
                                       "*ExamplePatch.esm"])
        for name in ("plugins.txt", "HOW-TO-APPLY.txt", "rules-proposed.json", "report.txt", "report.html"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name)), name)
        with open(os.path.join(self.out, "rules-proposed.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["rules"], [])
        self.assertNotIn("recommend", j["sections"])
        self.assertEqual(j["sections"]["sort"]["status"], "done")

    def test_user_rule_moves_one_plugin_with_its_reason(self):
        r = self.rules([{"id": "u-0001", "plugin": "ExampleB.esm", "after": ["ExampleD.esm"]}])
        code, _out, j = self.sort("--rules", r)
        self.assertEqual(code, 0)
        self.assertEqual(self.mods(), ["*ExampleA.esm", "*ExampleC.esm", "*ExampleD.esm", "*ExampleB.esm",
                                       "*ExamplePatch.esm"])
        s = j["sort"]
        self.assertEqual(s["moved"], ["ExampleB.esm"])
        m = s["moves"][0]
        self.assertEqual((m["plugin"], m["after"], m["source"]), ("ExampleB.esm", "ExampleD.esm", "user"))
        self.assertIn("your rule (my-rules.json, entry 1, id u-0001)", m["reason"])
        self.assertEqual(s["sources"]["user"], 1)
        with open(os.path.join(self.out, "HOW-TO-APPLY.txt"), encoding="utf-8") as f:
            how = f.read()
        self.assertIn("Sorting moves 1 plugin(s). Back up your current order first", how)
        with open(os.path.join(self.out, "report.txt"), encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("Sorted order", txt)
        self.assertIn("Sorting moves 1 plugin", txt)
        self.assertIn("Load ExampleB.esm after ExampleD.esm. Why: Your rule", txt)
        self.assertNotIn("Suggested order", txt)
        with open(os.path.join(self.out, "report.html"), encoding="utf-8") as f:
            self.assertIn('<section id="sorted"><h2>Sorted order</h2>', f.read())

    def test_the_default_group_isnt_counted_as_your_rules(self):
        r = self.rules([{"plugin": "ExampleA.esm", "group": "Late"}],
                       groups=[{"name": "Late", "after": ["default"]}])
        _code, _out, j = self.sort("--rules", r)
        s = j["sort"]
        self.assertNotIn("default_group", s["sources"])
        self.assertLessEqual(s["sources"]["user"], 3)  # the group's edge and A's two member edges, not 2 per plugin
        with open(os.path.join(self.out, "report.txt"), encoding="utf-8") as f:
            self.assertNotIn("default group", f.read().split("Rules used, by source:")[1].split(".")[0])

    def test_a_group_on_an_official_plugin_isnt_inactive(self):
        # code review round 5: the game places Starfield.esm, so its group is moot, not "a plugin that isn't enabled"
        r = self.rules([{"plugin": "Starfield.esm", "group": "Late"}], groups=[{"name": "Late"}])
        _code, _out, j = self.sort("--rules", r)
        self.assertEqual(j["sort"]["inactive_rules"], 0)

    def test_how_to_has_no_move_line_when_nothing_moves(self):
        # code review round 5: HOW-TO-APPLY.txt said "Sorting moves 0 plugin(s). Back up ..." before "nothing to apply"
        self.sort()
        with open(os.path.join(self.out, "HOW-TO-APPLY.txt"), encoding="utf-8") as f:
            self.assertNotIn("Sorting moves", f.read())

    def test_rule_against_a_master_is_not_used(self):
        # ExamplePatch has ExampleA as a master, so "ExampleA after ExamplePatch" can't be met
        r = self.rules([{"plugin": "ExampleA.esm", "after": ["ExamplePatch.esm"]}])
        code, _out, j = self.sort("--rules", r)
        self.assertEqual(code, 0)
        self.assertEqual(self.mods()[0], "*ExampleA.esm")
        rej = j["sort"]["rejected"]
        self.assertEqual(len(rej), 1)
        self.assertEqual((rej[0]["source"], rej[0]["level"]), ("user", "caution"))
        f = self.finding(j, "rule-not-used")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["level"], "caution")
        self.assertIn("ExamplePatch.esm has ExampleA.esm as a master", f[0]["text"])
        self.assertIn("The rule wasn't used", f[0]["text"])

    def test_inactive_rule_is_counted_not_reported(self):
        r = self.rules([{"plugin": "ExampleA.esm", "after": ["NotEnabled.esm"]}])
        _code, _out, j = self.sort("--rules", r)
        self.assertEqual(j["sort"]["inactive_rules"], 1)
        self.assertEqual(self.finding(j, "rule-not-used"), [])

    def test_requires_and_incompatible(self):
        r = self.rules([{"plugin": "ExampleA.esm", "requires": ["NotEnabled.esm"]},
                        {"plugin": "ExampleB.esm", "incompatible": ["ExampleC.esm"]}])
        code, _out, j = self.sort("--rules", r, "--strict")
        self.assertEqual(code, 1)
        self.assertEqual(len(j["sort"]["requires_missing"]), 1)
        self.assertEqual(len(j["sort"]["incompatible"]), 1)
        self.assertEqual([f["level"] for f in self.finding(j, "requires")], ["problem"])
        self.assertEqual([f["level"] for f in self.finding(j, "incompatible")], ["problem"])

    def test_stale_rule_is_used_with_a_caution(self):
        r = self.rules([{"plugin": "ExampleB.esm", "after": ["ExampleD.esm"],
                         "evidence": {"kind": "choice", "files": {"ExampleB.esm": {"sha256": "0" * 64}}}}])
        _code, _out, j = self.sort("--rules", r)
        self.assertEqual(j["sort"]["moved"], ["ExampleB.esm"])  # still applied
        self.assertEqual(len(j["sort"]["stale"]), 1)
        f = self.finding(j, "stale")
        self.assertEqual(len(f), 1)
        self.assertIn("accept or delete the old rule", f[0]["fix"])

    def test_default_rules_file_and_no_rules(self):
        os.makedirs(self.state)
        with open(rulesfile.default_path(self.state), "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "kind": "user", "rules": [{"plugin": "ExampleB.esm", "after": ["ExampleD.esm"]}]},
                      f)
        _code, _out, j = self.sort()
        self.assertEqual(j["sort"]["moved"], ["ExampleB.esm"])
        _code, _out, j = self.sort("--no-rules")
        self.assertEqual(j["sort"]["moved"], [])

    def test_missing_rules_file_given_is_exit_2(self):
        code, out, _j = self.sort("--rules", os.path.join(self.tmp, "nope.json"))
        self.assertEqual(code, 2)
        self.assertIn("RESULT: CANNOT CHECK", out)

    def test_community_rule_loses_to_the_user_rule(self):
        user = self.rules([{"plugin": "ExampleB.esm", "after": ["ExampleD.esm"]}])
        com = self.rules([{"plugin": "ExampleD.esm", "after": ["ExampleB.esm"]}], name="community.json",
                         kind="community", about={"name": "Example Fixes"})
        _code, _out, j = self.sort("--rules", user, "--community-rules", com)
        self.assertEqual(j["sort"]["moved"], ["ExampleB.esm"])
        f = self.finding(j, "rule-not-used")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["level"], "note")
        self.assertIn("Community rule (Example Fixes, entry 1)", f[0]["text"])

    def test_picks_are_applied(self):
        picks = self.write("picks.json", {"picks": [{"plugin": "ExampleA.esm", "after": "ExampleC.esm"}]})
        _code, _out, j = self.sort("--picks", picks)
        self.assertEqual(self.mods()[:3], ["*ExampleB.esm", "*ExampleC.esm", "*ExampleA.esm"])
        self.assertIn("ExampleA.esm", j["sort"]["moved"])
        self.assertEqual(self.mods()[3:], ["*ExampleD.esm", "*ExamplePatch.esm"])

    def test_tie_break_name(self):
        _code, _out, j = self.sort("--tie-break", "name")
        self.assertEqual(j["sort"]["tie_break"], "name")
        self.assertEqual(self.mods(), ["*ExampleA.esm", "*ExampleB.esm", "*ExampleC.esm", "*ExampleD.esm",
                                       "*ExamplePatch.esm"])


class TieBreakNameReasons(Base):
    PLUGINS = {"ExampleC.esm": {}, "ExampleA.esm": {}, "ExampleB.esm": {}}

    def test_each_move_names_the_tie_break(self):
        # 0.3.0 bug hunt: the first move said "it moves along with the moves above" with no move above it
        _code, _out, j = self.sort("--tie-break", "name", "--no-rules")
        self.assertEqual(self.mods(), ["*ExampleA.esm", "*ExampleB.esm", "*ExampleC.esm"])
        self.assertTrue(j["sort"]["moves"])
        for m in j["sort"]["moves"]:
            self.assertIn("--tie-break name", m["reason"])
            self.assertNotIn("moves along", m["reason"])


class LootAndPatches(Base):
    MASTERLIST = """
plugins:
  - name: 'ExampleA.esm'
    after: [ 'ExampleC.esm' ]
"""

    def test_loot_masterlist_rule_and_no_loot(self):
        ml = self.write("masterlist.yaml", self.MASTERLIST)
        _code, _out, j = self.sort("--loot-masterlist", ml, "--loot-userlist", os.path.join(self.tmp, "none.yaml"),
                                   loot=True)
        self.assertEqual(self.mods()[:3], ["*ExampleB.esm", "*ExampleC.esm", "*ExampleA.esm"])
        self.assertEqual(j["sort"]["sources"]["loot_masterlist"], 1)
        self.assertIn("loot_masterlist", j["sort"]["loot_files"])
        m = next(m for m in j["sort"]["moves"] if m["plugin"] == "ExampleA.esm")
        self.assertEqual(m["source"], "loot_masterlist")
        _code, _out, j = self.sort("--loot-masterlist", ml, "--no-loot", loot=True)
        self.assertEqual(j["sort"]["moved"], [])
        self.assertEqual(j["sections"]["clashes"]["parts"]["loot"]["status"], "skipped")

    def test_user_rule_outranks_loot(self):
        ml = self.write("masterlist.yaml", self.MASTERLIST)
        user = self.rules([{"plugin": "ExampleC.esm", "after": ["ExampleA.esm"]}])
        _code, _out, j = self.sort("--rules", user, "--loot-masterlist", ml, "--loot-userlist",
                                   os.path.join(self.tmp, "none.yaml"), loot=True)
        self.assertEqual(j["sort"]["moved"], [])
        f = self.finding(j, "rule-not-used")
        self.assertEqual([x["level"] for x in f], ["note"])
        self.assertIn("LOOT's masterlist says ExampleA.esm loads after ExampleC.esm", f[0]["text"])

    def test_clashes_fixed_by_is_a_patch_target(self):
        cl = self.write("clashes.json", {"clashes": [{"plugins": ["ExampleB.esm", "ExampleD.esm"], "level": "caution",
                                                      "note": "They clash.", "fixed_by": "ExampleC.esm"}]})
        _code, _out, j = self.sort("--clashes", cl)
        self.assertEqual(j["sort"]["sources"]["patch"], 2)
        self.assertEqual(self.mods()[:4], ["*ExampleA.esm", "*ExampleB.esm", "*ExampleD.esm", "*ExampleC.esm"])

    def test_patch_sources_editors_are_targets(self):
        man = self.write("patch-sources.json", {
            "schema": 1, "patch": {"name": "ExampleB.esm"}, "loadOrder": {"plugins": ["Starfield.esm"]},
            "records": [{"formKey": "000800:ExampleD.esm", "type": "MiscItem", "editors": [{"plugin": "ExampleD.esm",
                                                                                           "hash": None}]}]})
        _code, _out, j = self.sort("--patch-sources", man)
        self.assertEqual(j["sort"]["sources"]["patch"], 1)
        self.assertEqual(j["sort"]["moved"], ["ExampleB.esm"])
        self.assertEqual(j["sort"]["moves"][0]["source"], "patch")

    def test_a_manifest_with_bad_records_is_skipped(self):
        man = self.write("patch-sources.json", {
            "schema": 1, "patch": {"name": "ExampleB.esm"}, "loadOrder": {"plugins": ["Starfield.esm"]}, "records": 3})
        code, _out, j = self.sort("--patch-sources", man)
        self.assertEqual(code, 0)
        self.assertEqual(j["sort"]["sources"].get("patch", 0), 0)
        self.assertIsNotNone(j["sort"]["order"])


class TypeRuleAndLoop(Base):
    PLUGINS = {"ExampleE.esp": {}, "ExampleA.esm": {}, "ExampleB.esm": {}}

    def test_non_master_esp_loads_after_the_masters(self):
        no_master_flag(os.path.join(self.data, "ExampleE.esp"))
        _code, _out, j = self.sort()
        self.assertEqual(self.mods(), ["*ExampleA.esm", "*ExampleB.esm", "*ExampleE.esp"])
        m = j["sort"]["moves"][0]
        self.assertEqual((m["plugin"], m["source"]), ("ExampleE.esp", "game"))
        self.assertIn("isn't a master plugin", m["reason"])


class Fixes031(Base):
    """0.3.1: the review rounds' sort nits (each test fails on 0.3.0)."""
    PLUGINS = {"ExampleE.esp": {}, "ExampleA.esm": {}, "ExampleB.esm": {}, "ExampleC.esm": {}}

    def test_a_user_rule_move_isnt_credited_to_master_plugins_first(self):
        # ExampleA.esm moves for the user rule; the .esp above it in the current order made 0.3.0 name the
        # master-plugins-first rule instead
        no_master_flag(os.path.join(self.data, "ExampleE.esp"))
        r = self.rules([{"id": "u-0001", "plugin": "ExampleA.esm", "after": ["ExampleC.esm"]}])
        _code, _out, j = self.sort("--rules", r)
        self.assertEqual(self.mods(), ["*ExampleB.esm", "*ExampleC.esm", "*ExampleA.esm", "*ExampleE.esp"])
        m = {x["plugin"]: x for x in j["sort"]["moves"]}
        self.assertEqual(m["ExampleA.esm"]["source"], "user", m)
        self.assertEqual(m["ExampleE.esp"]["source"], "game")

    def test_a_pick_naming_a_disabled_plugin_gets_a_caution(self):
        picks = self.write("picks.json", [{"plugin": "ExampleA.esm", "after": "NotEnabled.esm"}])
        _code, _out, j = self.sort("--picks", picks)
        c = self.finding(j, "pick-inactive")
        self.assertEqual([f["level"] for f in c], ["caution"])
        self.assertIn("NotEnabled.esm", c[0]["text"])

    def test_rules_used_counts_rules_not_edges(self):
        r = self.rules([{"id": "u-0001", "plugin": "ExampleA.esm", "after": ["ExampleB.esm", "ExampleC.esm"]}])
        _code, _out, j = self.sort("--rules", r)
        self.assertEqual(j["sort"]["sources"]["user"], 1)

    def test_default_group_across_the_master_plugins_gives_no_notes(self):
        # a group after `default` holding a master plugin: 0.3.0 listed one "rule not met" per .esp
        for n in ("ExampleE.esp",):
            no_master_flag(os.path.join(self.data, n))
        r = self.rules([{"id": "u-0001", "plugin": "ExampleA.esm", "group": "late"}],
                       groups=[{"name": "late", "after": ["default"]}])
        _code, _out, j = self.sort("--rules", r)
        self.assertEqual([x for x in j["sort"]["rejected"] if x["source"] == "default_group"], [])
        self.assertIsNotNone(j["sort"]["order"])

    def test_sorted_part_says_the_section_failed_not_master_loop(self):
        real = sort.soft

        def boom(*a, **k):
            raise RuntimeError("made-up crash")
        sort.soft = boom
        try:
            _code, _out, j = self.sort()
        finally:
            sort.soft = real
        self.assertEqual(j["sections"]["sort"]["status"], "failed")
        with open(os.path.join(self.out, "report.txt"), encoding="utf-8") as f:
            txt = f.read()
        self.assertNotIn("masters form a loop", txt)
        self.assertIn("No order was sorted", txt)


class PickCredit(Base):
    # X.esm needs M.esm, which loads below Y.esm: the pick "X right after Y" can't place X there, the master does
    PLUGINS = {"ExampleX.esm": {"masters": ["Starfield.esm", "ExampleM.esm"]}, "ExampleY.esm": {},
               "ExampleM.esm": {}}

    def test_moves_says_your_pick_only_when_the_pick_placed_it(self):
        picks = self.write("picks.json", [{"plugin": "ExampleX.esm", "after": "ExampleY.esm"}])
        _code, _out, j = self.sort("--picks", picks)
        self.assertEqual(self.mods(), ["*ExampleY.esm", "*ExampleM.esm", "*ExampleX.esm"])
        m = {x["plugin"]: x for x in j["sort"]["moves"]}["ExampleX.esm"]
        self.assertEqual(m["source"], "game", m)
        self.assertNotIn("your pick", m["reason"])


class Loop(Base):
    PLUGINS = {"ExampleA.esm": {"masters": ["ExampleB.esm"]}, "ExampleB.esm": {"masters": ["ExampleA.esm"]}}

    def test_master_loop_writes_no_order_files(self):
        code, _out, j = self.sort("--strict")
        self.assertEqual(code, 1)
        self.assertIsNone(j["sort"]["order"])
        self.assertTrue(j["sort"]["loop"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "plugins.txt")))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "rules-proposed.json")))
        self.assertEqual([f["level"] for f in self.finding(j, "master-loop")], ["problem"])
        code, _out, _j = self.sort()
        self.assertEqual(code, 0)

    def test_master_loop_removes_an_earlier_runs_order_files(self):
        os.makedirs(self.out, exist_ok=True)
        for n in ("plugins.txt", "HOW-TO-APPLY.txt"):
            with open(os.path.join(self.out, n), "w", encoding="utf-8") as f:
                f.write("old\n")
        _code, _out, j = self.sort()
        self.assertFalse(os.path.exists(os.path.join(self.out, "plugins.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "HOW-TO-APPLY.txt")))
        self.assertTrue(self.finding(j, "master-loop", "were removed"))


class Mo2AndVortex(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.out = os.path.join(self.tmp, "out")
        self.rules = os.path.join(self.tmp, "r.json")
        with open(self.rules, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "kind": "user", "rules": [{"plugin": "ExampleA.esm", "after": ["ExampleB.esm"]}]},
                      f)

    def tearDown(self):
        self._tmp.cleanup()

    def test_mo2_writes_loadorder_txt(self):
        data = os.path.join(self.tmp, "game", "Data")
        for n in fixtures.OFFICIAL:
            fixtures.plugin(os.path.join(data, n))
        inst = fixtures.mo2(os.path.join(self.tmp, "mo2"), "Default", [
            ("ModA", True, {"ExampleA.esm": {}}), ("ModB", True, {"ExampleB.esm": {}})])
        code, _out = run("sort", "--depth", "light", "--no-loot", "--no-state", "--rules", self.rules, "--mo2", inst,
                         "--data", data, "--out", self.out)
        self.assertEqual(code, 0)
        lo = lines(os.path.join(self.out, "loadorder.txt"))
        self.assertLess(lo.index("ExampleB.esm"), lo.index("ExampleA.esm"))
        with open(os.path.join(self.out, "report.json"), encoding="utf-8") as f:
            j = json.load(f)
        self.assertEqual(len(j["sort"]["moved"]), 1)
        # code review round 1: the MO2 official-plugins caution was missing from a sort run
        got = [x for x in j["findings"] if (x.get("data") or {}).get("kind") == "mo2-officials"]
        self.assertEqual([(x["section"], x["level"]) for x in got], [("sort", "caution")])

    def test_vortex_writes_the_import_file(self):
        data = os.path.join(self.tmp, "Data")
        for n in fixtures.OFFICIAL + ["ExampleA.esm", "ExampleB.esm"]:
            fixtures.plugin(os.path.join(data, n))
        vl = fixtures.vortex_loadorder(os.path.join(self.tmp, "lo.json"),
                                       [(n, True) for n in fixtures.OFFICIAL] + [("ExampleA.esm", True),
                                                                                 ("ExampleB.esm", True)])
        code, _out = run("sort", "--depth", "light", "--no-loot", "--no-state", "--rules", self.rules,
                         "--vortex-loadorder", vl, "--data", data, "--out", self.out)
        self.assertEqual(code, 0)
        with open(os.path.join(self.out, "vortex-loadorder-recommended.json"), encoding="utf-8") as f:
            names = [e["name"] for e in json.load(f)]
        self.assertLess(names.index("exampleb.esm"), names.index("examplea.esm"))


# ---------- in memory: the soft climb, the near-tie rule, Vortex rules ----------
OFFICIAL = ("Starfield.esm",)


def make_lo(names, mods=None):
    ps = [model.Plugin(n, True, official=True, masters=[], master=True) for n in OFFICIAL]
    ps += [model.Plugin(n, True, masters=["Starfield.esm"], master=True) for n in names]
    ms = [model.Mod(m, True, plugins=list(p), id=m.lower()) for m, p in (mods or {}).items()] if mods else None
    return model.LoadOrder(source="plugins.txt", plugins=ps, mods=ms)


def ctx_for(t, lo, **opts):
    a = argparse.Namespace(out=t, tie_break="current", loot_groups=False, no_loot=True, clashes=None,
                           patch_sources=None, depth="light")
    for k, v in opts.items():
        setattr(a, k, v)
    return cli.Context(a, lo, model.Report("test"))


def field(name, winner_too=False):
    return {"Field": name, "WinnerChangedToo": winner_too, "LoserValue": "a", "WinnerValue": "b",
            "Differences": [{"Text": f"{name} differs", "Minor": False}], "Original": "o",
            "LoserChanges": [{"Text": "changed", "Minor": False}], "WinnerChanges": []}


def lost(fk, loser, winner, fields):
    return {"formKey": fk, "RecordType": "Npc", "EditorID": "SomeNpc", "Loser": loser, "LoserOfficial": False,
            "Winner": winner, "Fields": fields}


class Soft(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self._near = (recommend.NEAR_MIN, recommend.NEAR_SHARE)

    def tearDown(self):
        recommend.NEAR_MIN, recommend.NEAR_SHARE = self._near

    def rec(self, lo, lost_):
        p = fixtures.conflicts_json(os.path.join(self.t, "conflicts.json"),
                                    [(n, n in OFFICIAL, []) for n in lo.enabled()], lost=lost_)
        cj, err = records.load(p)
        self.assertIsNone(err)
        return records.analyse(cj, lo)

    def test_climb_flips_a_pair_and_proposes_a_rule(self):
        recommend.NEAR_MIN, recommend.NEAR_SHARE = 0, 0
        lo = make_lo(["A.esm", "B.esm", "C.esm"])
        ctx = ctx_for(self.t, lo)
        ctx.results["records"] = self.rec(lo, [lost("000100:Starfield.esm", "A.esm", "C.esm", [field("Keywords")])])
        sort.run(ctx)
        s = ctx.results["sort"]
        self.assertEqual([n for n in s["order"] if n not in OFFICIAL], ["B.esm", "C.esm", "A.esm"])
        self.assertEqual(s["moves"][0]["source"], "records")
        self.assertIn("win over C.esm", s["moves"][0]["reason"])
        self.assertEqual((s["score_current"], s["score_sorted"]), (3, 0))
        self.assertEqual(s["proposed"], 1)
        with open(os.path.join(self.t, "rules-proposed.json"), encoding="utf-8") as f:
            prop = json.load(f)["rules"]
        self.assertEqual((prop[0]["plugin"], prop[0]["after"]), ("A.esm", ["C.esm"]))
        self.assertTrue(prop[0]["id"].startswith("p-"))
        self.assertEqual(prop[0]["evidence"]["kind"], "choice")
        self.assertEqual(prop[0]["evidence"]["records"], ["000100:Starfield.esm"])
        # the proposal file is a valid rules file
        self.assertEqual(rulesfile.read(os.path.join(self.t, "rules-proposed.json"), "user").problems, [])

    def test_a_pair_a_master_flips_gets_no_choice_proposal(self):
        # code review round 3: B.esm has A.esm as a master but loads above it; the master edge moves it below A.esm,
        # which also flips the record pair (loser B, winner A). That is the master's doing, not a choice.
        recommend.NEAR_MIN, recommend.NEAR_SHARE = 0, 0
        ps = [model.Plugin("Starfield.esm", True, official=True, masters=[], master=True),
              model.Plugin("B.esm", True, masters=["Starfield.esm", "A.esm"], master=True),
              model.Plugin("A.esm", True, masters=["Starfield.esm"], master=True),
              model.Plugin("C.esm", True, masters=["Starfield.esm"], master=True)]
        lo = model.LoadOrder(source="plugins.txt", plugins=ps)
        ctx = ctx_for(self.t, lo)
        ctx.results["records"] = self.rec(lo, [lost("000100:Starfield.esm", "B.esm", "A.esm", [field("Keywords")])])
        sort.run(ctx)
        s = ctx.results["sort"]
        self.assertLess(s["order"].index("A.esm"), s["order"].index("B.esm"))
        self.assertEqual(s["proposed"], 0)
        self.assertTrue(all(m["source"] != "records" for m in s["moves"]), s["moves"])

    def test_a_pair_a_master_flips_indirectly_gets_no_choice_proposal(self):
        # code review round 7: X.esm needs M.esm, so it moves below M.esm and so below Y.esm too; the pair
        # (loser X, winner Y) has no graph path between them but the climb didn't choose the flip
        recommend.NEAR_MIN, recommend.NEAR_SHARE = 0, 0
        ps = [model.Plugin("Starfield.esm", True, official=True, masters=[], master=True),
              model.Plugin("X.esm", True, masters=["Starfield.esm", "M.esm"], master=True),
              model.Plugin("Y.esm", True, masters=["Starfield.esm"], master=True),
              model.Plugin("M.esm", True, masters=["Starfield.esm"], master=True)]
        lo = model.LoadOrder(source="plugins.txt", plugins=ps)
        ctx = ctx_for(self.t, lo)
        ctx.results["records"] = self.rec(lo, [lost("000100:Starfield.esm", "X.esm", "Y.esm", [field("Keywords")])])
        sort.run(ctx)
        s = ctx.results["sort"]
        self.assertLess(s["order"].index("Y.esm"), s["order"].index("X.esm"))
        self.assertEqual(s["proposed"], 0)
        self.assertTrue(all(m["source"] != "records" for m in s["moves"]), s["moves"])

    def restart_case(self, extra=(), mods=None):
        # C.esm needs A.esm and D.esm needs B.esm. Pair 1 (D wins over A, 3 points) and pair 2 (C wins over B, 6
        # points) can't both flip (D<A<C<B<D). The first climb takes pair 1; a restart that starts from that order
        # finds pair 2, which saves more (0.3.0's sort restarted from the graph's order and found nothing).
        ps = [model.Plugin("Starfield.esm", True, official=True, masters=[], master=True)]
        for n in ["A.esm", "B.esm", *extra, "C.esm", "D.esm"]:
            ms = {"C.esm": ["A.esm"], "D.esm": ["B.esm"]}.get(n, [])
            ps.append(model.Plugin(n, True, masters=["Starfield.esm"] + ms, master=True))
        lo = model.LoadOrder(source="plugins.txt", plugins=ps,
                             mods=[model.Mod(m, True, plugins=list(p), id=m.lower()) for m, p in (mods or {}).items()]
                             or None)
        rec = self.rec(lo, [lost("000100:Starfield.esm", "A.esm", "D.esm", [field("Keywords")]),
                            lost("000200:Starfield.esm", "B.esm", "C.esm", [field("Keywords"), field("Name")])])
        return lo, rec

    def test_a_restart_keeps_the_improved_order(self):
        recommend.NEAR_MIN, recommend.NEAR_SHARE = 0, 0
        lo, rec = self.restart_case()
        ctx = ctx_for(self.t, lo)
        ctx.results["records"] = rec
        sort.run(ctx)
        s = ctx.results["sort"]
        self.assertEqual([n for n in s["order"] if n not in OFFICIAL], ["A.esm", "C.esm", "B.esm", "D.esm"])
        self.assertEqual(s["score_sorted"], 3)

    def test_the_arranger_keeps_the_base_order_where_the_rules_allow(self):
        # 0.3.0's sort arranger ignored base, so a restart started again from the graph's order
        lo, _rec = self.restart_case()
        b = sortgraph.build(lo, lo.enabled(), sortgraph.Sources())
        arr = sort.arranger(b.graph)
        self.assertEqual(arr(["B.esm", "D.esm", "A.esm", "C.esm"], [], []), ["B.esm", "D.esm", "A.esm", "C.esm"])
        self.assertEqual(arr(None, [], []), ["A.esm", "B.esm", "C.esm", "D.esm"])
        name = sortgraph.build(lo, lo.enabled(), sortgraph.Sources(), "name")
        self.assertEqual(sort.arranger(name.graph)(["D.esm", "C.esm", "B.esm", "A.esm"], [], []),
                         ["A.esm", "B.esm", "C.esm", "D.esm"])  # --tie-break name ignores base

    def test_the_vortex_trial_starts_from_the_climbed_order(self):
        # E.esm sits wherever the climb left it; the Vortex rule (G's mod after F's) moves only F and G
        recommend.NEAR_MIN, recommend.NEAR_SHARE = 0, 0
        lo, rec = self.restart_case(extra=("E.esm", "G.esm", "F.esm"), mods={"ModF": ["F.esm"], "ModG": ["G.esm"]})
        lo._vortex_modlist = [{"vortexId": "modg", "enabled": True, "rules": [{"type": "after",
                                                                              "reference": {"id": "modf"}}]},
                              {"vortexId": "modf", "enabled": True}]
        ctx = ctx_for(self.t, lo)
        ctx.results["records"] = rec
        lo_no_rule, _ = self.restart_case(extra=("E.esm", "G.esm", "F.esm"))
        ctx0 = ctx_for(self.t, lo_no_rule)
        ctx0.results["records"] = rec
        sort.run(ctx0)
        sort.run(ctx)
        climbed = [n for n in ctx0.results["sort"]["order"] if n not in OFFICIAL]
        got = [n for n in ctx.results["sort"]["order"] if n not in OFFICIAL]
        self.assertEqual([n for n in got if n not in ("F.esm", "G.esm")],
                         [n for n in climbed if n not in ("F.esm", "G.esm")])
        self.assertLess(got.index("F.esm"), got.index("G.esm"))
        self.assertEqual(ctx.results["sort"]["vortex_broken"], [])

    def test_near_tie_keeps_the_order(self):
        lo = make_lo(["A.esm", "B.esm", "C.esm"])
        ctx = ctx_for(self.t, lo)
        ctx.results["records"] = self.rec(lo, [lost("000100:Starfield.esm", "A.esm", "C.esm", [field("Keywords")])])
        sort.run(ctx)  # a saving of 3 is under the near-tie minimum of 10
        self.assertEqual(ctx.results["sort"]["moves"], [])
        self.assertEqual(ctx.results["sort"]["proposed"], 0)

    def test_pair_a_rule_decides_is_not_a_candidate(self):
        recommend.NEAR_MIN, recommend.NEAR_SHARE = 0, 0
        lo = make_lo(["A.esm", "B.esm", "C.esm"])
        user = rulesfile.RulesFile("u.json", "user", {}, [], [{"entry": 1, "plugin": "C.esm", "after": ["A.esm"],
                                                              "before": [], "requires": [], "incompatible": []}])
        ctx = ctx_for(self.t, lo)
        ctx.results["sort_files"] = {"user": user, "community": None}
        ctx.results["records"] = self.rec(lo, [lost("000100:Starfield.esm", "A.esm", "C.esm", [field("Keywords")])])
        sort.run(ctx)
        self.assertEqual(ctx.results["sort"]["moves"], [])
        self.assertEqual(ctx.results["sort"]["decided"], 1)

    def test_vortex_rule_is_a_tie_break(self):
        lo = make_lo(["A.esm", "B.esm"], mods={"ModA": ["A.esm"], "ModB": ["B.esm"]})
        lo._vortex_modlist = [{"vortexId": "moda", "enabled": True, "rules": [{"type": "after",
                                                                              "reference": {"id": "modb"}}]},
                              {"vortexId": "modb", "enabled": True}]
        ctx = ctx_for(self.t, lo)
        sort.run(ctx)
        s = ctx.results["sort"]
        self.assertEqual([n for n in s["order"] if n not in OFFICIAL], ["B.esm", "A.esm"])
        self.assertEqual(s["sources"]["vortex"], 1)
        self.assertEqual(s["moves"][0]["source"], "vortex")
        self.assertEqual(s["vortex_broken"], [])

    def test_vortex_rule_a_hard_rule_breaks_gets_a_note(self):
        lo = make_lo(["A.esm", "B.esm"], mods={"ModA": ["A.esm"], "ModB": ["B.esm"]})
        lo._vortex_modlist = [{"vortexId": "moda", "enabled": True, "rules": [{"type": "after",
                                                                              "reference": {"id": "modb"}}]},
                              {"vortexId": "modb", "enabled": True}]
        user = rulesfile.RulesFile("u.json", "user", {}, [], [{"entry": 1, "plugin": "B.esm", "after": ["A.esm"],
                                                              "before": [], "requires": [], "incompatible": []}])
        ctx = ctx_for(self.t, lo)
        ctx.results["sort_files"] = {"user": user, "community": None}
        sort.run(ctx)
        s = ctx.results["sort"]
        self.assertEqual([n for n in s["order"] if n not in OFFICIAL], ["A.esm", "B.esm"])
        self.assertEqual(len(s["vortex_broken"]), 1)
        notes = [f for f in ctx.report.findings if f.data.get("kind") == "vortex-rule"]
        self.assertEqual([f.level for f in notes], ["note"])

    def test_tie_break_name_same_from_shuffled_inputs(self):
        names = [f"Mod{i:02d}.esm" for i in range(12)]
        orders = []
        for seed in (1, 2):
            sh = list(names)
            random.Random(seed).shuffle(sh)
            ctx = ctx_for(self.t, make_lo(sh), tie_break="name")
            sort.run(ctx)
            orders.append(ctx.results["sort"]["order"])
        self.assertEqual(orders[0], orders[1])


class Timing(unittest.TestCase):
    def test_1000_plugins_sort_in_time(self):
        rnd = random.Random(7)
        names = [f"Mod{i:04d}.esm" for i in range(1000)]
        ps = [model.Plugin("Starfield.esm", True, official=True, masters=[], master=True)]
        for i, n in enumerate(names):
            ms = rnd.sample(names[:i], min(i, 5)) if i else []
            ps.append(model.Plugin(n, True, masters=["Starfield.esm"] + ms, master=True))
        rnd.shuffle(ps[1:])  # a current order that breaks many master edges
        ps = [ps[0]] + sorted(ps[1:], key=lambda p: rnd.random())
        lo = model.LoadOrder(source="plugins.txt", plugins=ps)
        user = rulesfile.RulesFile("u.json", "user", {}, [], [
            {"entry": k + 1, "plugin": rnd.choice(names), "after": [rnd.choice(names)], "before": [], "requires": [],
             "incompatible": []} for k in range(15000)])
        t0 = time.time()
        ctx = ctx_for(tempfile.mkdtemp(), lo)
        ctx.results["sort_files"] = {"user": user, "community": None}
        sort.run(ctx)
        took = time.time() - t0
        s = ctx.results["sort"]
        self.assertIsNotNone(s["order"])
        pos = {n.lower(): i for i, n in enumerate(s["order"])}
        for p in ps[1:]:
            for m in p.masters[1:]:
                self.assertLess(pos[m.lower()], pos[p.name.lower()])
        self.assertLess(took, 30, f"sorting 1,000 plugins took {took:.1f} s")


if __name__ == "__main__":
    unittest.main()
