"""Tests for rulesfile.py, the PMT ordering rules file (DESIGN-0.3.0.md 2.1 to 2.5). Synthetic names only."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import rulesfile, sortgraph  # noqa: E402
from sf_load_order.model import LoadOrder, Plugin  # noqa: E402
from tests import fixtures  # noqa: E402

FULL = {
    "schema": 1, "kind": "user", "about": {"name": "My rules", "url": "https://example.invalid/r", "version": "1"},
    "_comment": "fields starting with _ are comments",
    "groups": [{"name": "Late Patches", "after": ["default"], "note": "Patches that must win."}],
    "rules": [{
        "id": "u-0001", "plugin": "MyPatch.esm", "after": ["ModA.esm", "ModB.esm"], "before": ["ModC.esm"],
        "requires": ["ModA.esm"], "incompatible": ["OldMyPatch.esp"], "group": "Late Patches",
        "note": "One sentence.", "_why": "a comment",
        "evidence": {"kind": "choice", "text": "So its changes win.", "link": "https://example.invalid/x",
                     "records": ["0012AB:ModA.esm"], "files": {"MyPatch.esm": {"sha256": "a" * 64}},
                     "tool": "sf-load-order 0.3.0", "date": "2026-11-24", "proposal": "p-0123456789"}}]}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, obj, name="rules.json"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        return path

    def one(self, rule, **top):
        return rulesfile.read(self.write(dict({"schema": 1, "rules": [rule]}, **top)))


class Reading(Base):
    def test_every_field_is_read(self):
        rf = rulesfile.read(self.write(FULL), "user")
        self.assertEqual((rf.kind, rf.about["name"], rf.entries, rf.problems, rf.warnings),
                         ("user", "My rules", 1, [], []))
        self.assertEqual(rf.groups, [{"entry": 1, "name": "Late Patches", "after": ["default"],
                                      "note": "Patches that must win."}])
        r = rf.rules[0]
        self.assertEqual((r["entry"], r["id"], r["plugin"], r["after"], r["before"], r["requires"],
                          r["incompatible"], r["group"]),
                         (1, "u-0001", "MyPatch.esm", ["ModA.esm", "ModB.esm"], ["ModC.esm"], ["ModA.esm"],
                          ["OldMyPatch.esp"], "Late Patches"))
        self.assertEqual(r["evidence"]["kind"], "choice")
        self.assertEqual(rf.label(r), "entry 1, id u-0001")
        self.assertIn("loads after ModA.esm and ModB.esm", rulesfile.describe(r))

    def test_kind_defaults_to_user_and_lists_default_empty(self):
        rf = self.one({"plugin": "A.esm", "group": "G"})
        self.assertEqual((rf.kind, rf.rules[0]["after"], rf.rules[0]["before"]), ("user", [], []))

    def test_a_bom_is_fine(self):
        path = os.path.join(self.tmp, "bom.json")
        with open(path, "w", encoding="utf-8-sig") as f:
            json.dump({"schema": 1, "rules": [{"plugin": "A.esm", "after": ["B.esm"]}]}, f)
        self.assertEqual(len(rulesfile.read(path).rules), 1)

    def test_default_path_is_in_the_state_folder(self):
        self.assertEqual(rulesfile.default_path(self.tmp), os.path.join(self.tmp, "sf-load-order-rules.json"))
        self.assertEqual(rulesfile.default_path(env={"LOCALAPPDATA": self.tmp}),
                         os.path.join(self.tmp, "sf-load-order", "sf-load-order-rules.json"))


class EntryRefusals(Base):
    def skipped(self, rule):
        rf = self.one(rule)
        self.assertEqual(rf.rules, [], rule)
        self.assertEqual(len(rf.problems), 1)
        return rf.problems[0]["why"]

    def test_unknown_field_skips_the_entry(self):
        self.assertIn("unknown field 'afterr'", self.skipped({"plugin": "A.esm", "afterr": ["B.esm"]}))

    def test_bad_names(self):
        self.assertIn("plugin file name", self.skipped({"plugin": "A.txt", "after": ["B.esm"]}))
        self.assertIn("plugin file name", self.skipped({"plugin": "Sub\\A.esm", "after": ["B.esm"]}))
        self.assertIn("\"after\" must be", self.skipped({"plugin": "A.esm", "after": "B.esm"}))
        self.assertIn("own plugin", self.skipped({"plugin": "A.esm", "after": ["a.ESM"]}))

    def test_patterns_and_wildcards_are_refused(self):
        for name in ("Mod*.esm", "Mod?.esm", "Mod.es[mp]", "A|B.esm", "A<B.esm"):
            self.assertIn("no pattern", self.skipped({"plugin": "A.esm", "after": [name]}), name)
        self.assertIn("no pattern", self.skipped({"plugin": "Mod.*\\.esm", "after": ["B.esm"]}))

    def test_legal_file_name_characters_are_plain_names(self):
        # Windows allows ( ) [ ] + { } ^ $ in file names; a rule names files exactly, so they are plain text
        for name in ("Mod (Fixed).esm", "[SFBGS] X.esm", "Mod+Patch.esp", "A{1}^$.esm"):
            self.assertTrue(rulesfile.is_name(name), name)

    def test_a_rule_must_say_something(self):
        self.assertIn("says nothing", self.skipped({"plugin": "A.esm", "note": "hi"}))

    def test_evidence_checks(self):
        self.assertIn("evidence \"kind\"", self.skipped({"plugin": "A.esm", "after": ["B.esm"],
                                                         "evidence": {"kind": "guess"}}))
        self.assertIn("https://", self.skipped({"plugin": "A.esm", "after": ["B.esm"],
                                                "evidence": {"link": "javascript:alert(1)"}}))
        self.assertIn("at most 20", self.skipped({"plugin": "A.esm", "after": ["B.esm"],
                                                  "evidence": {"records": ["x"] * 21}}))
        self.assertIn("64 hex", self.skipped({"plugin": "A.esm", "after": ["B.esm"],
                                              "evidence": {"files": {"A.esm": {"sha256": "12"}}}}))

    def test_duplicate_id_skips_the_second(self):
        rf = rulesfile.read(self.write({"schema": 1, "rules": [{"id": "x", "plugin": "A.esm", "after": ["B.esm"]},
                                                               {"id": "X", "plugin": "C.esm", "after": ["B.esm"]}]}))
        self.assertEqual([r["plugin"] for r in rf.rules], ["A.esm"])
        self.assertIn("already used by entry 1", rf.problems[0]["why"])

    def test_bad_entries_dont_stop_good_ones(self):
        rf = rulesfile.read(self.write({"schema": 1, "rules": [{"plugin": "A.esm", "after": ["B.esm"]}, "text",
                                                               {"plugin": "C.esm", "before": ["B.esm"]}]}))
        self.assertEqual([r["entry"] for r in rf.rules], [1, 3])
        self.assertEqual(rf.problems[0]["entry"], 2)

    def test_bad_group(self):
        rf = rulesfile.read(self.write({"schema": 1, "groups": [{"name": "G", "after": ["G"]}, {"name": 3}],
                                        "rules": []}))
        self.assertEqual(rf.groups, [])
        self.assertEqual([p["what"] for p in rf.problems], ["group", "group"])


class FileRefusals(Base):
    def refused(self, obj, *words):
        with self.assertRaises(rulesfile.RulesError) as cm:
            rulesfile.read(self.write(obj) if obj is not None else os.path.join(self.tmp, "missing.json"))
        for w in words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)

    def test_not_found_not_json_not_an_object(self):
        self.refused(None, "file not found")
        self.refused("{ nope", "line 1")
        self.refused("[1, 2]", "JSON object")

    def test_schema(self):
        self.refused({"schema": 2, "rules": []}, "newer sf-load-order", "update")
        self.refused({"rules": []}, "\"schema\" must be 1")
        self.refused({"schema": "1", "rules": []}, "\"schema\" must be 1")

    def test_unknown_top_field_and_kind(self):
        self.refused({"schema": 1, "rule": []}, "unknown top-level field 'rule'")
        self.refused({"schema": 1, "kind": "shared", "rules": []}, "\"kind\"")

    def test_limits(self):
        self.refused({"schema": 1, "rules": [{"plugin": "A.esm", "note": "x" * 2001, "group": "G"}]}, "2001")
        self.refused({"schema": 1, "rules": [{"plugin": "A.esm", "after": [f"M{i}.esm" for i in range(501)]}]},
                     "501 items")
        self.refused({"schema": 1, "rules": [{"plugin": "A.esm", "group": "G"}] * 10001}, "10001 rules")
        self.refused({"schema": 1, "groups": [{"name": f"G{i}"} for i in range(501)], "rules": []}, "501 groups")

    def test_size_limit(self):
        path = os.path.join(self.tmp, "big.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"schema": 1, "rules": [], "_pad": "' + "x" * (5 * 1024 * 1024) + '"}')
        with self.assertRaises(rulesfile.RulesError) as cm:
            rulesfile.read(path)
        self.assertIn("5 MB", str(cm.exception))


class Kinds(Base):
    def test_community_file_given_as_user_and_the_reverse(self):
        com = self.write({"schema": 1, "kind": "community", "rules": []}, "c.json")
        usr = self.write({"schema": 1, "kind": "user", "rules": []}, "u.json")
        self.assertIn("--community-rules", rulesfile.read(com, "user").warnings[0])
        self.assertIn("given as a community file", rulesfile.read(usr, "community").warnings[0])
        self.assertEqual(rulesfile.read(com, "community").warnings, [])


class BeforeIsReversedAfter(Base):
    def order(self, rule):
        rf = self.one(rule)
        lo = LoadOrder(source="plugins.txt", plugins=[Plugin(n, True, masters=[]) for n in ("A.esm", "B.esm")])
        return sortgraph.build(lo, ["A.esm", "B.esm"], sortgraph.Sources(user=rf)).full

    def test_before_equals_reversed_after(self):
        self.assertEqual(self.order({"plugin": "A.esm", "after": ["B.esm"]}), ["B.esm", "A.esm"])
        self.assertEqual(self.order({"plugin": "B.esm", "before": ["A.esm"]}), ["B.esm", "A.esm"])


class StaleAndProposals(Base):
    def test_stale_pins(self):
        data = os.path.join(self.tmp, "Data")
        a = fixtures.plugin(os.path.join(data, "A.esm"), author="A")
        fixtures.plugin(os.path.join(data, "B.esm"), author="B")
        lo = LoadOrder(source="plugins.txt", data=data, plugins=[Plugin("A.esm", True, path=a),
                                                                 Plugin("B.esm", True, path=os.path.join(data, "B.esm"))])
        good = rulesfile.file_hashes(lo, ["A.esm", "B.esm"])
        rf = self.one({"plugin": "A.esm", "after": ["B.esm"], "evidence": {"kind": "choice", "files": good}})
        self.assertEqual(rulesfile.stale(rf, lo), [])
        fixtures.plugin(a, author="A, updated")  # the mod was updated
        st = rulesfile.stale(rf, lo)
        self.assertEqual([(s["entry"], s["changed"]) for s in st], [(1, ["A.esm"])])

    def test_a_missing_plugin_isnt_stale(self):
        rf = self.one({"plugin": "A.esm", "after": ["B.esm"],
                       "evidence": {"files": {"Gone.esm": {"sha256": "b" * 64}}}})
        self.assertEqual(rulesfile.stale(rf, LoadOrder(source="plugins.txt")), [])

    def test_proposal_ids_are_stable(self):
        a = rulesfile.proposal_id("X.esm", ["B.esm", "A.esm"], "choice")
        self.assertEqual(a, rulesfile.proposal_id("x.ESM", ["a.esm", "B.ESM"], "choice"))
        self.assertNotEqual(a, rulesfile.proposal_id("X.esm", ["A.esm", "B.esm"], "patch"))
        self.assertRegex(a, r"^p-[0-9a-f]{10}$")

    def test_write_atomic_keeps_a_backup(self):
        path = self.write({"schema": 1, "rules": []}, "mine.json")
        bak = rulesfile.write_atomic(path, rulesfile.to_json("user", [{"plugin": "A.esm", "after": ["B.esm"]}]))
        self.assertEqual(bak, path + ".bak")
        with open(bak, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["rules"], [])
        self.assertEqual(len(rulesfile.read(path).rules), 1)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
