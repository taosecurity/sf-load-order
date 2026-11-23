"""Tests for sf_load_order.rules (the Vortex mod rules check). Synthetic data only.

Vortex setup (staging):
  S1   S1.esm, textures\\t.dds "one", scripts\\u.pex "u1"
  S2   textures\\t.dds "two", scripts\\u.pex "u2"          -> differs from S1 on both paths
  S3   meshes\\same.nif "same"
  S4   meshes\\same.nif "same"                             -> identical to S3
  S5   interface\\only.swf "five"                          -> shares nothing
Data holds S2's copies of both S1/S2 paths; the deployment record says so.
Both export shapes are tested: today's (no rules key) and Modlist Backup 0.6.2's (rules, fileOverrides).
"""
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, loose, model, rules, vortex_deploy  # noqa: E402
from tests import fixtures  # noqa: E402

MODS = ("S1", "S2", "S3", "S4", "S5")


def put(path, b):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b)


def entry(mid, rules_=None, overrides=None, enabled=True, archive=None):
    e = {"name": mid + " name", "game": "starfield", "source": "nexus", "enabled": enabled, "vortexId": mid,
         "archive": archive or (mid + "-1-0.7z")}
    if rules_ is not None:
        e["rules"] = rules_
    if overrides is not None:
        e["fileOverrides"] = overrides
    return e


def rule(kind, **ref):
    return {"type": kind, "reference": ref}


def build(d, modlist, record=True):
    data, st = os.path.join(d, "Data"), os.path.join(d, "staging")
    fixtures.plugin(os.path.join(data, "Starfield.esm"))
    fixtures.plugin(os.path.join(data, "S1.esm"))
    fixtures.plugin(os.path.join(st, "S1", "S1.esm"))
    for mid, rel, b in (("S1", "textures\\t.dds", b"one"), ("S1", "scripts\\u.pex", b"u1"),
                        ("S2", "textures\\t.dds", b"two"), ("S2", "scripts\\u.pex", b"u2"),
                        ("S3", "meshes\\same.nif", b"same"), ("S4", "meshes\\same.nif", b"same"),
                        ("S5", "interface\\only.swf", b"five")):
        put(os.path.join(st, mid, *rel.split("\\")), b)
    put(os.path.join(data, "textures", "t.dds"), b"two")
    put(os.path.join(data, "scripts", "u.pex"), b"u2")
    mods = [model.Mod(m, True, os.path.join(st, m), ["S1.esm"] if m == "S1" else [], id=m) for m in MODS]
    plugins = [model.Plugin("Starfield.esm", True, official=True, path=os.path.join(data, "Starfield.esm"), kind="F",
                            masters=[]),
               model.Plugin("S1.esm", True, path=os.path.join(data, "S1.esm"), kind="F", masters=[])]
    lo = model.LoadOrder(source="vortex", data=data, plugins=plugins, mods=mods)
    lo._vortex_modlist = modlist
    if record:
        lo._vortex_deployment = vortex_deploy.Deployment(path="record", files={
            "textures\\t.dds": "S2", "scripts\\u.pex": "S2", "s1.esm": "S1", "meshes\\same.nif": "S4",
            "interface\\only.swf": "S5"})
    return lo


def run(d, modlist, record=True):
    lo = build(d, modlist, record)
    args = types.SimpleNamespace(out=d, state=os.path.join(d, "state"), no_state=True)
    ctx = cli.Context(args, lo, model.Report("test"))
    ctx.report.sections["loose"] = loose.run(ctx)
    st = rules.run(ctx)
    return ctx, st, [f for f in ctx.report.findings if f.section == "rules"]


class NoRules(unittest.TestCase):
    """Today's export: no mod has rules or fileOverrides."""

    def test_note_per_differing_pair_names_the_deployed_mod(self):
        with tempfile.TemporaryDirectory() as d:
            ctx, st, found = run(d, [entry(m) for m in MODS])
        self.assertEqual(st["status"], "done")
        self.assertFalse(st["has_rules"])
        self.assertTrue(all(f.level == "note" for f in found))
        pair = [f for f in found if "S1 and S2" in f.text]
        self.assertEqual(len(pair), 1)
        self.assertIn("Vortex deployed S2's copies", pair[0].text)
        self.assertNotIn(True, ["S3" in f.text for f in found], "identical pairs get no note")
        self.assertEqual(sum(f.text == rules.NO_RULES for f in found), 1)
        j = ctx.report.to_dict()["rules"]
        self.assertFalse(j["has_rules"])
        self.assertEqual(j["findings"], {"problem": 0, "caution": 0, "note": 2})
        p12 = next(p for p in j["pairs"] if p["mods"] == ["S1", "S2"])
        self.assertEqual((p12["winner"], p12["same"], p12["rule"]), ("S2", False, None))

    def test_without_a_record_the_copy_in_data_decides(self):
        with tempfile.TemporaryDirectory() as d:
            _ctx, _st, found = run(d, [entry(m) for m in MODS], record=False)
        t = next(f.text for f in found if "S1 and S2" in f.text)
        self.assertIn("Vortex deployed S2's copies (the copies in Data)", t)
        self.assertNotIn("deployment record", t)

    def test_with_a_record_the_note_names_it(self):
        with tempfile.TemporaryDirectory() as d:
            _ctx, _st, found = run(d, [entry(m) for m in MODS])
        self.assertIn("(Vortex's deployment record)", next(f.text for f in found if "S1 and S2" in f.text))



class WithRules(unittest.TestCase):
    """Modlist Backup 0.6.2's export: rules and fileOverrides."""

    def test_real_clash_with_no_rule_is_a_caution(self):
        with tempfile.TemporaryDirectory() as d:
            _ctx, st, found = run(d, [entry(m, rules_=[]) for m in MODS])
        self.assertTrue(st["has_rules"])
        c = [f for f in found if f.level == "caution"]
        self.assertEqual(len(c), 1)
        self.assertIn("Real clash with no rule", c[0].text)
        self.assertIn("Vortex picked S2's copy", c[0].text)
        self.assertNotIn(rules.NO_RULES, [f.text for f in found])

    def test_an_after_rule_settles_the_clash(self):
        with tempfile.TemporaryDirectory() as d:
            ctx, _st, found = run(d, [entry("S1"), entry("S2", [rule("after", id="S1")]), entry("S3"),
                                      entry("S4"), entry("S5")])
        self.assertEqual([f for f in found if f.level == "caution"], [])
        p = next(p for p in ctx.report.rules["pairs"] if p["mods"] == ["S1", "S2"])
        self.assertEqual(p["rule"], [{"mod": "S2", "type": "after", "other": "S1", "wins": "S2"}])
        # code review round 6: report.json's pairs hold every key the README lists
        self.assertEqual(set(p), {"mods", "paths", "same", "differ", "unknown", "winner", "deployed",
                                  "deployed_from", "rule", "overrides", "sample"})

    def test_a_before_rule_counts_either_way_and_by_file_expression(self):
        with tempfile.TemporaryDirectory() as d:
            ctx, _st, found = run(d, [entry("S1", [rule("before", fileExpression="s2-*")]), entry("S2"),
                                      entry("S3"), entry("S4"), entry("S5")])
        self.assertEqual([f for f in found if f.level == "caution"], [])
        p = next(p for p in ctx.report.rules["pairs"] if p["mods"] == ["S1", "S2"])
        self.assertEqual(p["rule"][0]["wins"], "S2")

    def test_file_overrides_settle_the_clash(self):
        with tempfile.TemporaryDirectory() as d:
            _ctx, _st, found = run(d, [entry("S1", overrides=["textures/t.dds", "Scripts\\U.pex"])]
                                   + [entry(m) for m in MODS[1:]])
        self.assertEqual([f for f in found if f.level == "caution"], [])

    def test_overrides_for_only_some_paths_leave_the_clash(self):
        with tempfile.TemporaryDirectory() as d:
            _ctx, _st, found = run(d, [entry("S1", overrides=["textures/t.dds"])] + [entry(m) for m in MODS[1:]])
        self.assertEqual(len([f for f in found if f.level == "caution"]), 1)

    def test_a_third_mods_copy_doesnt_count_against_a_pair(self):
        # A and B ship the same x.dds (C's differs) and differ only on y.nif, which A's fileOverrides cover
        loose_result = {
            "paths": [{"path": "textures\\x.dds", "mods": ["A", "B", "C"], "identical": False},
                      {"path": "meshes\\y.nif", "mods": ["A", "B"], "identical": False}],
            "pairs": [{"mods": ["A", "B"], "files": 2, "same": 1, "differ": 1, "unknown": 0, "advice": "differ",
                       "in_data": {}, "not_same": ["meshes\\y.nif"], "sample": ["meshes\\y.nif"]}]}
        lo = model.LoadOrder(source="vortex", mods=[])
        r = rules.analyse(lo, [entry("A", rules_=[], overrides=["meshes/y.nif"]), entry("B"), entry("C")],
                          loose_result)
        self.assertTrue(r["pairs"][0]["overrides"], r["pairs"][0])
        self.assertFalse(any("A and B" in f.text for f in rules._findings(None, r) if f.level == "caution"))

    def test_rule_over_identical_copies_and_over_nothing_shared(self):
        with tempfile.TemporaryDirectory() as d:
            _ctx, _st, found = run(d, [entry("S1"), entry("S2", [rule("after", id="S1")]),
                                       entry("S3", [rule("after", id="S4"), rule("before", logicalFileName="S5 name")]),
                                       entry("S4"), entry("S5")])
        notes = [f.text for f in found if f.level == "note"]
        self.assertTrue(any("only order identical copies" in t and "S3 / S4" in t for t in notes), notes)
        self.assertTrue(any("share no loose file" in t and "S3 / S5" in t for t in notes), notes)

    def test_unmatched_and_non_ordering_rules(self):
        with tempfile.TemporaryDirectory() as d:
            ctx, _st, found = run(d, [entry("S1", [rule("after", id="Gone"), rule("requires", id="S2"),
                                                   rule("conflicts", id="S2")]),
                                      entry("S2", [rule("after", id="S1")]), entry("S3"), entry("S4"), entry("S5"),
                                      entry("Off", [rule("after", id="S1")], enabled=False)])
        un = [f for f in found if "isn't enabled" in f.text]
        self.assertEqual(len(un), 1)
        self.assertEqual(un[0].data["rules"], [{"mod": "S1", "type": "after", "reference": {"id": "Gone"}}])
        p = next(p for p in ctx.report.rules["pairs"] if p["mods"] == ["S1", "S2"])
        self.assertEqual([r["type"] for r in p["rule"]], ["after"], "requires/conflicts aren't ordering rules")


class Skips(unittest.TestCase):
    def ctx(self, lo, d):
        return cli.Context(types.SimpleNamespace(out=d, state=d, no_state=True), lo, model.Report("test"))

    def test_pair_that_couldnt_be_compared_says_so(self):
        self.assertEqual(rules._differ_words({"differ": 2, "paths": 3}), "ship 2 loose file(s) with different copies")
        self.assertEqual(rules._differ_words({"differ": 0, "paths": 3}),
                         "share 3 loose file(s) that couldn't be compared")
        self.assertEqual(rules._differ_words({"differ": 0, "paths": 50, "unknown": 2}),
                         "share 2 loose file(s) that couldn't be compared")

    def test_pair_that_couldnt_be_compared_isnt_a_real_clash(self):
        r = {"has_rules": True, "pairs": [{"mods": ["A", "B"], "same": False, "rule": [], "overrides": [],
                                           "differ": 0, "paths": 2, "winner": None, "deployed": None}],
             "no_shared": [], "unmatched": []}
        found = rules._findings(None, r)
        t = next(f.text for f in found if "A and B" in f.text)
        self.assertTrue(t.startswith("Possible clash with no rule"), t)
        self.assertIn("share 2 loose file(s) that couldn't be compared", t)

    def test_not_vortex(self):
        with tempfile.TemporaryDirectory() as d:
            st = rules.run(self.ctx(model.LoadOrder(source="mo2", mods=[]), d))
        self.assertEqual(st["status"], "skipped")
        self.assertIn("Vortex", st["why"])

    def test_no_modlist(self):
        with tempfile.TemporaryDirectory() as d:
            st = rules.run(self.ctx(model.LoadOrder(source="vortex", mods=[]), d))
        self.assertEqual(st["status"], "skipped")
        self.assertIn("--vortex-modlist", st["why"])

    def test_loose_did_not_run(self):
        with tempfile.TemporaryDirectory() as d:
            lo = model.LoadOrder(source="vortex", mods=[])
            lo._vortex_modlist = []
            c = self.ctx(lo, d)
            c.report.sections["loose"] = {"status": "skipped", "why": "no staging"}
            st = rules.run(c)
        self.assertEqual(st["status"], "skipped")
        self.assertIn("no staging", st["why"])


class EndToEnd(unittest.TestCase):
    """The whole command with --vortex-loadorder and --vortex-modlist: the section runs and report.json has rules."""

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            data, _ = fixtures.game(os.path.join(d, "Game"), {"ModA.esm": {"masters": ["Starfield.esm"]}})
            st = os.path.join(d, "Staging")
            fixtures.plugin(os.path.join(st, "moda-1", "ModA.esm"), masters=["Starfield.esm"])
            put(os.path.join(st, "moda-1", "textures", "t.dds"), b"one")
            put(os.path.join(st, "modb-2", "textures", "t.dds"), b"two")
            put(os.path.join(data, "textures", "t.dds"), b"two")
            with open(os.path.join(data, vortex_deploy.RECORD), "w", encoding="utf-8") as f:
                json.dump({"stagingPath": st, "files": [{"relPath": "ModA.esm", "source": "moda-1"},
                                                        {"relPath": "textures\\t.dds", "source": "modb-2"}]}, f)
            lo_file = fixtures.vortex_loadorder(os.path.join(d, "lo.json"),
                                                [("Starfield.esm", True), ("ModA.esm", True, "moda-1")])
            ml = os.path.join(d, "modlist.json")
            with open(ml, "w", encoding="utf-8") as f:
                json.dump([entry("moda-1", rules_=[]), entry("modb-2")], f)
            out = os.path.join(d, "out")
            code = cli.main(["--vortex-loadorder", lo_file, "--vortex-modlist", ml, "--data", data,
                             "--vortex-staging", st, "--depth", "light", "--no-state", "--out", out, "--quiet"])
            with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
                j = json.load(f)
            with open(os.path.join(out, "report.txt"), encoding="utf-8") as f:
                txt = f.read()
        self.assertEqual(code, 0)
        self.assertEqual(j["sections"]["rules"]["status"], "done", j["sections"]["rules"])
        self.assertTrue(j["rules"]["has_rules"])
        self.assertEqual(j["rules"]["findings"]["caution"], 1)
        self.assertEqual(j["rules"]["pairs"][0]["winner"], "modb-2")
        self.assertIn("Vortex mod rules", txt)
        self.assertIn("Real clash with no rule", txt)


if __name__ == "__main__":
    unittest.main()
