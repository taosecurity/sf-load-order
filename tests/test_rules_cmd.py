"""Tests for the `rules` command (rules_cmd.py; DESIGN-0.3.0.md 2.4 and 2.7): list, explain, check, accept and
propose, run through cli.main as a player would. Synthetic game and plugins from tests\\fixtures.py; LOOT is either
turned off (--no-loot) or pointed at a made-up masterlist, so the real LOOT folder is never read."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, rulesfile  # noqa: E402
from tests import fixtures  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.state = os.path.join(self.tmp, "state")
        # current order: A, B, C, D, Patch (Patch has A as a master)
        self.data, self.ptxt = fixtures.game(self.tmp, {
            "ExampleA.esm": {}, "ExampleB.esm": {}, "ExampleC.esm": {}, "ExampleD.esm": {},
            "ExamplePatch.esm": {"masters": ["Starfield.esm", "ExampleA.esm"]}})
        self.src = ["--plugins-txt", self.ptxt, "--data", self.data]

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        return path

    def rules(self, rules, name="my-rules.json", **top):
        return self.write(name, dict({"schema": 1, "kind": "user", "rules": rules}, **top))


class NoCommand(Base):
    def test_rules_alone_names_the_commands(self):
        code, out = run("rules")
        self.assertEqual(code, 2)
        self.assertIn("list, explain, check, accept, propose", out)

    def test_main_run_is_unchanged_by_the_word(self):
        with self.assertRaises(SystemExit):  # an unknown option for the main parser, as before
            run("--rules-nope")


class ListCmd(Base):
    def test_list_with_a_source_marks_active(self):
        r = self.rules([{"id": "u-0001", "plugin": "ExampleA.esm", "after": ["ExampleD.esm"]},
                        {"plugin": "ExampleA.esm", "after": ["NotHere.esm"]}])
        js = os.path.join(self.tmp, "list.json")
        code, out = run("rules", "list", "--rules", r, "--no-loot", "--json", js, *self.src)
        self.assertEqual(code, 0)
        self.assertIn("entry 1, id u-0001: ExampleA.esm loads after ExampleD.esm [active]", out)
        self.assertIn("entry 2: ExampleA.esm loads after NotHere.esm [inactive", out)
        with open(js, encoding="utf-8") as f:
            rows = json.load(f)["rules"]
        self.assertEqual([x["active"] for x in rows], [True, False])

    def test_list_marks_a_rule_the_sort_leaves_out(self):
        # 0.3.0 bug hunt: a rule the game's masters contradict was listed as plain [active]
        r = self.rules([{"plugin": "ExampleA.esm", "after": ["ExamplePatch.esm"]},
                        {"plugin": "ExampleB.esm", "after": ["ExampleD.esm"]}])
        js = os.path.join(self.tmp, "list.json")
        code, out = run("rules", "list", "--rules", r, "--no-loot", "--json", js, *self.src)
        self.assertEqual(code, 0)
        self.assertIn("entry 1: ExampleA.esm loads after ExamplePatch.esm [active, but the sort doesn't use it: "
                      "ExamplePatch.esm has ExampleA.esm as a master]", out)
        self.assertIn("entry 2: ExampleB.esm loads after ExampleD.esm [active]", out)
        with open(js, encoding="utf-8") as f:
            rows = json.load(f)["rules"]
        self.assertEqual([bool(x["not_used"]) for x in rows], [True, False])

    def test_a_left_out_group_entry_doesnt_flag_the_rule_with_its_number(self):
        # code review round 1: group entry N and rule entry N shared a key
        r = self.rules([{"plugin": "ExampleA.esm", "after": ["ExampleD.esm"]},
                        {"plugin": "ExampleB.esm", "after": ["ExampleD.esm"]}],
                       groups=[{"name": "Late", "after": ["Early"]}, {"name": "Early", "after": ["Late"]}])
        code, out = run("rules", "list", "--rules", r, "--no-loot", *self.src)
        self.assertEqual(code, 0, out)
        self.assertIn("entry 1: ExampleA.esm loads after ExampleD.esm [active]", out)
        self.assertIn("entry 2: ExampleB.esm loads after ExampleD.esm [active]", out)

    def test_list_without_a_file_or_source(self):
        code, out = run("rules", "list", "--state", self.state, "--no-loot")
        self.assertEqual(code, 0)
        self.assertIn("Your rules: none (no file at", out)
        self.assertIn("Give a source option", out)

    def test_derived_lists_the_game_rules(self):
        code, out = run("rules", "list", "--no-rules", "--no-loot", "--derived", *self.src)
        self.assertEqual(code, 0)
        self.assertIn("ExamplePatch.esm has ExampleA.esm as a master", out)

    def test_loot_masterlist_rules_are_listed(self):
        ml = self.write("masterlist.yaml", "plugins:\n  - name: 'ExampleB.esm'\n    after: [ 'ExampleD.esm' ]\n")
        code, out = run("rules", "list", "--no-rules", "--loot-masterlist", ml, *self.src)
        self.assertEqual(code, 0)
        self.assertIn("LOOT masterlist:", out)
        self.assertIn("ExampleB.esm loads after ExampleD.esm [active]", out)

    def test_a_missing_rules_file_given_is_exit_2(self):
        code, out = run("rules", "list", "--rules", os.path.join(self.tmp, "nope.json"), "--no-loot")
        self.assertEqual(code, 2)
        self.assertIn("file not found", out)


class ExplainCmd(Base):
    def test_explain_names_the_rule_that_moved_it(self):
        r = self.rules([{"plugin": "ExampleA.esm", "after": ["ExampleC.esm"]}])
        js = os.path.join(self.tmp, "x.json")
        code, out = run("rules", "explain", "ExampleA.esm", "--rules", r, "--no-loot", "--json", js, *self.src)
        self.assertEqual(code, 0, out)
        self.assertIn("the sort moves it", out)
        self.assertIn("your rule (my-rules.json, entry 1) says ExampleA.esm loads after ExampleC.esm", out)
        self.assertIn("ExamplePatch.esm has ExampleA.esm as a master", out)  # an edge out of it
        with open(js, encoding="utf-8") as f:
            x = json.load(f)
        self.assertTrue(x["moved"])
        self.assertEqual(x["position"], x["was"] + 2)  # moved down past ExampleB.esm and ExampleC.esm
        self.assertEqual(x["anchor"], "ExampleC.esm")

    def test_explain_refuses_a_vortex_profile_that_isnt_deployed(self):
        from unittest import mock
        real = cli.read_load_order

        def fake(args):
            lo, err = real(args)
            lo._vortex_profile = {"name": "Other", "deployed": False}
            return lo, err
        with mock.patch.object(cli, "read_load_order", fake):
            code, out = run("rules", "explain", "ExamplePatch.esm", "--no-rules", "--no-loot", *self.src)
        self.assertEqual(code, 2, out)
        self.assertIn("Data holds another profile, not Other", out)

    def test_explain_uses_the_patch_edges_sort_uses(self):
        # code review round 1: the rules commands left out sort's patch edges (a patch-sources.json beside the patch)
        self.write(os.path.join(self.data, "patch-sources.json"), {
            "schema": 1, "patch": {"name": "ExampleA.esm"}, "loadOrder": {"plugins": ["Starfield.esm"]},
            "records": [{"formKey": "000800:ExampleD.esm", "type": "MiscItem",
                         "editors": [{"plugin": "ExampleD.esm", "hash": None}]}]})
        code, out = run("rules", "explain", "ExampleA.esm", "--no-rules", "--no-loot", *self.src)
        self.assertEqual(code, 0, out)
        self.assertIn("the sort moves it", out)
        self.assertIn("the patch ExampleA.esm was built from ExampleD.esm", out)

    def test_explain_a_rule_that_wasnt_used(self):
        r = self.rules([{"plugin": "ExampleA.esm", "after": ["ExamplePatch.esm"]}])
        code, out = run("rules", "explain", "ExampleA.esm", "--rules", r, "--no-loot", *self.src)
        self.assertEqual(code, 0)
        self.assertIn("Rules about it that weren't used:", out)
        self.assertIn("but ExamplePatch.esm has ExampleA.esm as a master. The rule wasn't used.", out)
        self.assertIn("the sort doesn't move it", out)

    def test_explain_a_plugin_not_in_the_order(self):
        code, out = run("rules", "explain", "Nope.esm", "--no-rules", "--no-loot", *self.src)
        self.assertEqual(code, 2)
        self.assertIn("isn't enabled", out)

    def test_explain_an_official_plugin(self):
        code, out = run("rules", "explain", "starfield.esm", "--no-rules", "--no-loot", *self.src)
        self.assertEqual(code, 0)
        self.assertIn("Starfield.esm is a plugin the game places itself", out)


class CheckCmd(Base):
    def test_clean_file_is_exit_0(self):
        r = self.rules([{"plugin": "ExampleA.esm", "after": ["ExampleB.esm"]}])
        code, out = run("rules", "check", "--rules", r)
        self.assertEqual(code, 0, out)
        self.assertIn("OK: nothing to fix", out)

    def test_bad_entry_and_loop_are_exit_1(self):
        r = self.rules([{"plugin": "ExampleA.esm", "after": ["ExampleB.esm"]},
                        {"plugin": "ExampleB.esm", "after": ["ExampleC.esm"]},
                        {"plugin": "ExampleC.esm", "after": ["ExampleA.esm"]},
                        {"plugin": "Example*.esm", "after": ["ExampleA.esm"]}])
        code, out = run("rules", "check", "--rules", r)
        self.assertEqual(code, 1)
        self.assertIn("rule entry 4:", out)
        self.assertIn("the file's own rules form a loop", out)

    def test_unreadable_and_newer_files_are_exit_2(self):
        bad = self.write("bad.json", "{ nope")
        self.assertEqual(run("rules", "check", "--rules", bad)[0], 2)
        newer = self.write("newer.json", {"schema": 2, "rules": []})
        code, out = run("rules", "check", "--rules", newer)
        self.assertEqual(code, 2)
        self.assertIn("newer sf-load-order", out)

    def test_with_a_source_contradictions_and_stale_pins(self):
        r = self.rules([{"plugin": "ExampleA.esm", "after": ["ExamplePatch.esm"]},
                        {"plugin": "ExampleB.esm", "after": ["ExampleC.esm"],
                         "evidence": {"kind": "choice", "files": {"ExampleB.esm": {"sha256": "0" * 64}}}},
                        {"plugin": "Starfield.esm", "after": ["ExampleC.esm"]}])
        code, out = run("rules", "check", "--rules", r, *self.src)
        self.assertEqual(code, 1)
        self.assertIn("but ExamplePatch.esm has ExampleA.esm as a master", out)
        self.assertIn("entry 2 about ExampleB.esm is stale", out)
        self.assertIn("Starfield.esm is an official plugin", out)

    def test_a_path_with_spaces(self):
        folder = os.path.join(self.tmp, "my rules folder")
        os.makedirs(folder)
        path = os.path.join(folder, "my rules.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "rules": [{"plugin": "ExampleA.esm", "group": "G"}]}, f)
        self.assertEqual(run("rules", "check", "--rules", path)[0], 0)


class AcceptCmd(Base):
    def proposed(self, *rules):
        return self.write("rules-proposed.json", {"schema": 1, "kind": "user", "rules": list(rules)})

    def prop(self, plugin, after):
        return {"id": rulesfile.proposal_id(plugin, after, "choice"), "plugin": plugin, "after": after,
                "evidence": {"kind": "choice", "text": "So its changes win."}}

    def test_accept_into_a_new_file_then_an_existing_one(self):
        p1, p2 = self.prop("ExampleA.esm", ["ExampleB.esm"]), self.prop("ExampleC.esm", ["ExampleD.esm"])
        pp = self.proposed(p1, p2)
        mine = os.path.join(self.state, "sf-load-order-rules.json")
        code, out = run("rules", "accept", pp, "--id", p1["id"], "--state", self.state)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(mine + ".bak"))  # nothing to back up the first time
        rf = rulesfile.read(mine)
        self.assertEqual([(r["id"], r["plugin"], r["evidence"]["proposal"]) for r in rf.rules],
                         [("u-0001", "ExampleA.esm", p1["id"])])
        with open(mine, "rb") as f:
            first = f.read()
        code, out = run("rules", "accept", pp, "--all", "--state", self.state)
        self.assertEqual(code, 0, out)
        self.assertIn("Already in your rules", out)
        with open(mine + ".bak", "rb") as f:
            self.assertEqual(f.read(), first)
        self.assertEqual([r["id"] for r in rulesfile.read(mine).rules], ["u-0001", "u-0002"])

    def test_unknown_id_and_contradiction_write_nothing(self):
        mine = self.rules([{"id": "u-0001", "plugin": "ExampleB.esm", "after": ["ExampleA.esm"]}], "mine.json")
        with open(mine, "rb") as f:
            before = f.read()
        pp = self.proposed(self.prop("ExampleA.esm", ["ExampleB.esm"]))
        code, out = run("rules", "accept", pp, "--id", "p-0000000000", "--rules", mine)
        self.assertEqual(code, 2)
        self.assertIn("no proposal with the id", out)
        code, out = run("rules", "accept", pp, "--all", "--rules", mine)
        self.assertEqual(code, 2)
        self.assertIn("contradict rules already in your file", out)
        with open(mine, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertFalse(os.path.exists(mine + ".bak"))

    def test_needs_id_or_all(self):
        with self.assertRaises(SystemExit):
            run("rules", "accept", self.proposed())


class ProposeCmd(Base):
    def test_propose_writes_an_empty_file_without_data(self):
        out_dir = os.path.join(self.tmp, "out")
        code, out = run("rules", "propose", "--no-rules", "--no-loot", "--out", out_dir, *self.src)
        self.assertEqual(code, 0, out)
        with open(os.path.join(out_dir, "rules-proposed.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"schema": 1, "kind": "user", "rules": []})

    def test_patch_proposals_from_fixed_by_then_accept(self):
        cl = self.write("clashes.json", [{"plugins": ["ExampleB.esm", "ExampleC.esm"], "level": "caution",
                                          "note": "Both change the same weapons.", "fixed_by": "ExampleD.esm"}])
        out_dir = os.path.join(self.tmp, "out")
        code, out = run("rules", "propose", "--kind", "patch", "--clashes", cl, "--no-rules", "--no-loot",
                        "--out", out_dir, *self.src)
        self.assertEqual(code, 0, out)
        pp = os.path.join(out_dir, "rules-proposed.json")
        rf = rulesfile.read(pp)
        self.assertEqual(rf.problems, [])
        r = rf.rules[0]
        self.assertEqual((r["plugin"], r["after"], r["evidence"]["kind"]),
                         ("ExampleD.esm", ["ExampleB.esm", "ExampleC.esm"], "patch"))
        self.assertEqual(r["id"], rulesfile.proposal_id("ExampleD.esm", ["ExampleB.esm", "ExampleC.esm"], "patch"))
        self.assertEqual(sorted(r["evidence"]["files"]), ["ExampleB.esm", "ExampleC.esm", "ExampleD.esm"])
        self.assertIn("rules accept", out)
        code, out = run("rules", "accept", pp, "--all", "--state", self.state)
        self.assertEqual(code, 0, out)


class ChoiceProposals(Base):
    """propose and explain with --conflicts use sort's own soft climb (sort.soft), so they agree with `sort`."""

    def conflicts(self):
        names = fixtures.OFFICIAL + ["ExampleA.esm", "ExampleB.esm", "ExampleC.esm", "ExampleD.esm",
                                     "ExamplePatch.esm"]
        f = {"Field": "Keywords", "WinnerChangedToo": False, "LoserValue": "a", "WinnerValue": "b",
             "Differences": [{"Text": "differs", "Minor": False}], "Original": "o",
             "LoserChanges": [{"Text": "changed", "Minor": False}], "WinnerChanges": []}
        lost = [{"formKey": f"00010{i}:Starfield.esm", "RecordType": "Npc", "EditorID": f"Npc{i}",
                 "Loser": "ExampleA.esm", "LoserOfficial": False, "Winner": "ExampleC.esm", "Fields": [f]}
                for i in range(4)]
        return fixtures.conflicts_json(os.path.join(self.tmp, "conflicts.json"),
                                       [(n, n in fixtures.OFFICIAL, []) for n in names], lost=lost)

    def test_propose_writes_the_choice(self):
        out = os.path.join(self.tmp, "out")
        code, text = run("rules", "propose", "--no-rules", "--no-loot", "--conflicts", self.conflicts(), "--out", out,
                         *self.src)
        self.assertEqual(code, 0, text)
        with open(os.path.join(out, "rules-proposed.json"), encoding="utf-8") as f:
            prop = json.load(f)["rules"]
        self.assertEqual([(p["plugin"], p["after"], p["evidence"]["kind"]) for p in prop],
                         [("ExampleA.esm", ["ExampleC.esm"], "choice")])
        self.assertEqual(len(prop[0]["evidence"]["records"]), 4)

    def test_explain_names_the_soft_move(self):
        code, text = run("rules", "explain", "ExampleA.esm", "--no-rules", "--no-loot", "--conflicts",
                         self.conflicts(), *self.src)
        self.assertEqual(code, 0, text)
        self.assertIn("A record-conflict choice moves it after ExampleC.esm: so its changes to 4 record(s)", text)
        code, text = run("rules", "explain", "ExampleB.esm", "--no-rules", "--no-loot", "--conflicts",
                         self.conflicts(), *self.src)
        self.assertIn("No record-conflict choice moves it.", text)


if __name__ == "__main__":
    unittest.main()
