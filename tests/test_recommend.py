"""Tests for recommend.py. Synthetic only: load orders built in memory, records data built by hand (the shape
records.py documents) or from fixtures.conflicts_json through records.analyse.

Fixtures written here (not in tests\\fixtures.py): make_lo() (a LoadOrder with masters and mods, no files),
pair()/entry() (hand-built ctx.results["records"] pairs and lost entries), field() and lost() (conflicts.json lost
entries as sf-mod-check 0.8.0 writes them)."""
import argparse
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import model, recommend, records  # noqa: E402
from sf_load_order.cli import Context  # noqa: E402
from tests import fixtures  # noqa: E402

OFFICIAL = ("Starfield.esm", "SFBGS003.esm")


def make_lo(names, masters=None, mods=None, source="plugins.txt"):
    """names: mod plugins in load order ('-' prefix = disabled). masters: {name: [masters]}. mods: {mod: [plugins]}."""
    masters = masters or {}
    ps = [model.Plugin(n, True, official=True, masters=[]) for n in OFFICIAL]
    for n in names:
        on = not n.startswith("-")
        n = n.lstrip("-")
        ps.append(model.Plugin(n, on, masters=["Starfield.esm"] + list(masters.get(n, []))))
    ms = [model.Mod(m, True, plugins=list(p)) for m, p in (mods or {}).items()] if mods is not None else None
    return model.LoadOrder(source=source, plugins=ps, mods=ms)


def ctx_for(t, lo, **opts):
    a = argparse.Namespace(out=t, loot_order=None, depth=None)
    for k, v in opts.items():
        setattr(a, k, v)
    return Context(a, lo, model.Report("test"))


def entry(fk, loser, winner, fields, edid=None, rtype="Npc", loser_official=False):
    """A ctx.results["records"]["lost"] entry. fields: [(name, label)]."""
    return {"fk": fk, "type": rtype, "edid": edid or fk, "loser": loser, "winner": winner,
            "loser_official": loser_official, "winner_official": False, "outcome": "merge",
            "fields": [{"field": f, "label": lab, "weight": records.weight(f, lab), "winner_changed_too":
                        lab == "Choice", "loser_value": "a", "winner_value": "b", "differences": []}
                       for f, lab in fields]}


def pair(loser, winner, records_, lost, advice="merge", cost=None, complete=False):
    allf = [f for i in records_ for f in lost[i]["fields"]]
    gain = sum(f["weight"] for f in allf if f["label"] != "No effect")
    known = sum(f["weight"] for f in allf if f["label"] == "Choice") if cost is None else cost
    return {"loser": loser, "winner": winner, "winner_official": False, "advice": advice,
            "label": records.ADVICE_LABEL[advice], "mod": None, "records": list(records_),
            "n": {}, "fields": {}, "gain_if_flipped": gain, "known_cost_if_flipped": known,
            "cost_complete": complete}


def rec_data(lost, pairs, official_undone=()):
    return {"lost": lost, "pairs": pairs, "official_undone": list(official_undone), "differs": None}


def field(name, winner_too=False, minor=False):
    return {"Field": name, "WinnerChangedToo": winner_too, "LoserValue": "a", "WinnerValue": "b",
            "Differences": [{"Text": f"{name} differs", "Minor": minor}], "Original": "o",
            "LoserChanges": [{"Text": "changed", "Minor": minor}], "WinnerChanges": []}


def lost(fk, loser, winner, fields, rtype="Npc", loser_official=False, edid="Rec"):
    return {"formKey": fk, "RecordType": rtype, "EditorID": edid, "Loser": loser, "LoserOfficial": loser_official,
            "Winner": winner, "Fields": fields}


def mods_of(order):
    return [n for n in order if n not in OFFICIAL]


_NEAR = None


def setUpModule():
    # These tests use small scores, which the near-tie rule would treat as "not worth it"; NearTie tests the rule.
    global _NEAR
    _NEAR = (recommend.NEAR_MIN, recommend.NEAR_SHARE)
    recommend.NEAR_MIN, recommend.NEAR_SHARE = 0, 0


def tearDownModule():
    recommend.NEAR_MIN, recommend.NEAR_SHARE = _NEAR


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()

    def run_rec(self, lo, rec=None, **kw):
        ctx = ctx_for(self.t, lo, **kw.pop("opts", {}))
        if rec is not None:
            ctx.results["records"] = rec
        for k, v in kw.items():
            ctx.results[k] = v
        st = recommend.run(ctx)
        return ctx, st, ctx.results["recommend"]

    def texts(self, ctx):
        return [f.text for f in ctx.report.findings if f.section == "recommend"]


class Scoring(Base):
    def test_move_that_lowers_the_score(self):
        # via sf-mod-check's file shape: A loses Keywords (functional, Forwardable) to C
        lo = make_lo(["A.esm", "B.esm", "C.esm"])
        p = fixtures.conflicts_json(os.path.join(self.t, "conflicts.json"),
                                    [(n, n in OFFICIAL, []) for n in lo.enabled()],
                                    lost=[lost("000100:Starfield.esm", "A.esm", "C.esm", [field("Keywords")],
                                               edid="SomeNpc")])
        cj, err = records.load(p)
        self.assertIsNone(err)
        ctx, st, r = self.run_rec(lo, records.analyse(cj, lo))
        self.assertEqual(mods_of(r["order"]), ["B.esm", "C.esm", "A.esm"])
        self.assertEqual((r["score_current"], r["score_recommended"]), (3, 0))
        self.assertTrue(r["estimate"])          # cost of the flip is a lower bound
        self.assertFalse(r["same_as_current"])
        self.assertEqual([m["plugin"] for m in r["moves"]], ["A.esm"])
        self.assertEqual(r["moves"][0]["after"], "C.esm")
        self.assertIn("keywords", r["moves"][0]["reason"])
        self.assertIn("estimated", r["moves"][0]["reason"])
        self.assertEqual(ctx.report.recommended, r["order"])
        self.assertTrue(any("an estimate" in t for t in self.texts(ctx)))
        self.assertTrue(any(LABEL in t for t in self.texts(ctx)))
        self.assertEqual(r["remaining"], [])

    def test_move_rejected_because_it_raises_the_score(self):
        # an exact cost (cost_complete True) higher than the gain: flipping would lose more
        lo = make_lo(["A.esm", "B.esm"])
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        rec = rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_, cost=10, complete=True)])
        ctx, st, r = self.run_rec(lo, rec)
        self.assertTrue(r["same_as_current"])
        self.assertEqual(r["moves"], [])
        self.assertEqual((r["score_current"], r["score_recommended"]), (3, 3))
        self.assertFalse(r["estimate"])
        self.assertEqual(len(r["rejected"]), 1)
        self.assertIn("score 10 vs 3", r["rejected"][0]["why"])
        # what stays lost becomes one merged-patch note
        self.assertTrue(any("merged patch" in t for t in self.texts(ctx)))
        self.assertEqual(r["remaining"][0]["loser"], "A.esm")

    def test_tie_is_not_a_move(self):
        # both changed the field (Choice): flipping only swaps who loses, so the score doesn't drop
        lo = make_lo(["A.esm", "B.esm"])
        lost_ = [entry("1", "A.esm", "B.esm", [("Flags", "Choice")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_, advice="conflict")]))
        self.assertTrue(r["same_as_current"])
        self.assertEqual(r["score_recommended"], 3)

    def test_official_changes_undone_count(self):
        lo = make_lo(["A.esm"])
        lost_ = [entry("1", "SFBGS003.esm", "A.esm", [("Location", "Forwardable")], loser_official=True)]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [], official_undone=[0]))
        self.assertEqual(r["score_current"], 3)
        self.assertTrue(r["remaining"][0]["official"])

    def test_same_mod_and_built_pairs_never_move(self):
        lo = make_lo(["A.esm", "B.esm"])
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        for adv in ("samemod", "built", "same"):
            ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_, advice=adv)]))
            self.assertTrue(r["same_as_current"], adv)


class NearTie(Base):
    def test_small_saving_keeps_the_order(self):
        recommend.NEAR_MIN, recommend.NEAR_SHARE = 10, 0.03  # the real rule: a saving under 10 points keeps it
        try:
            lo = make_lo(["A.esm", "B.esm", "C.esm"])
            p = fixtures.conflicts_json(os.path.join(self.t, "conflicts.json"),
                                        [(n, n in OFFICIAL, []) for n in lo.enabled()],
                                        lost=[lost("000100:Starfield.esm", "A.esm", "C.esm", [field("Keywords")])])
            cj, err = records.load(p)
            ctx, st, r = self.run_rec(lo, records.analyse(cj, lo))
        finally:
            recommend.NEAR_MIN, recommend.NEAR_SHARE = 0, 0
        self.assertTrue(r["same_as_current"])
        self.assertEqual(r["moves"], [])
        self.assertTrue(any("too small" in x["why"] for x in r["rejected"]))


class Constraints(Base):
    def test_master_moves_with_its_plugin(self):
        # C needs A as a master: when A moves below B, C comes along below it
        lo = make_lo(["A.esm", "C.esm", "B.esm"], masters={"C.esm": ["A.esm"]})
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_)]))
        order = mods_of(r["order"])
        self.assertEqual(order, ["B.esm", "A.esm", "C.esm"])

    def test_move_reason_names_the_master_that_moved(self):
        # C has two masters, X (stays) and A (moves below B3): C's reason names A, not X
        lo = make_lo(["X.esm", "A.esm", "C.esm", "B1.esm", "B2.esm", "B3.esm"], masters={"C.esm": ["X.esm", "A.esm"]})
        lost_ = [entry("1", "A.esm", "B3.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B3.esm", [0], lost_)]))
        self.assertEqual(mods_of(r["order"]), ["X.esm", "B1.esm", "B2.esm", "B3.esm", "A.esm", "C.esm"])
        why = {m["plugin"]: m["reason"] for m in r["moves"]}
        self.assertEqual(why["C.esm"], "it needs A.esm as a master, which moved")

    def test_move_reason_when_no_master_counts_as_moved(self):
        lo = make_lo(["B.esm", "A.esm"], masters={"B.esm": ["A.esm"]})
        ctx, st, r = self.run_rec(lo)
        for m in r["moves"]:
            self.assertNotIn("which moved", m["reason"])  # the one that moved is the plugin itself
            if m["plugin"] == "B.esm":
                self.assertIn("A.esm", m["reason"])

    def test_master_above_dependent_blocks_a_flip(self):
        # B has A as a master, so A can't load after B, whatever the score says
        lo = make_lo(["A.esm", "B.esm"], masters={"B.esm": ["A.esm"]})
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_)]))
        self.assertTrue(r["same_as_current"])
        self.assertIn("master", r["rejected"][0]["why"])

    def test_master_order_fixed_even_without_records(self):
        lo = make_lo(["B.esm", "A.esm"], masters={"B.esm": ["A.esm"]})
        ctx, st, r = self.run_rec(lo)
        self.assertEqual(mods_of(r["order"]), ["A.esm", "B.esm"])
        self.assertEqual(len(r["moves"]), 1)

    def test_mod_keeps_its_plugin_order(self):
        # A1 and A2 are one mod (A1 first). Moving A1 below B takes A2 along, below A1.
        lo = make_lo(["A1.esm", "A2.esm", "B.esm"], mods={"ModA": ["A1.esm", "A2.esm"], "ModB": ["B.esm"]})
        lost_ = [entry("1", "A1.esm", "B.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A1.esm", "B.esm", [0], lost_)]))
        self.assertEqual(mods_of(r["order"]), ["B.esm", "A1.esm", "A2.esm"])

    def test_mod_order_blocks_a_flip(self):
        # A1 and A2 are one mod: A1 can't load below A2, even for a pair marked as a patch candidate
        lo = make_lo(["A1.esm", "A2.esm"], mods={"ModA": ["A1.esm", "A2.esm"]})
        lost_ = [entry("1", "A1.esm", "A2.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A1.esm", "A2.esm", [0], lost_)]))
        self.assertTrue(r["same_as_current"])

    def test_official_and_blueprint_stay(self):
        lo = make_lo(["A.esm", "B.esm"])
        lo.plugins.append(model.Plugin("BlueprintShips-Starfield.esm", True, official=True, masters=[]))
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_)]))
        self.assertEqual(r["order"], ["Starfield.esm", "SFBGS003.esm", "B.esm", "A.esm",
                                      "BlueprintShips-Starfield.esm"])


class Picks(Base):
    def pick(self, plugin, after, why=None):
        return {"entry": 1, "plugin": plugin, "after": after, "why": why, "date": None, "active": True}

    def test_pick_right_after(self):
        lo = make_lo(["A.esm", "B.esm", "C.esm", "D.esm"])
        ctx, st, r = self.run_rec(lo, picks=[self.pick("D.esm", "A.esm", "looks better")])
        self.assertEqual(mods_of(r["order"]), ["A.esm", "D.esm", "B.esm", "C.esm"])
        self.assertEqual(r["moves"][0]["plugin"], "D.esm")
        self.assertIn("your pick", r["moves"][0]["reason"])
        self.assertIn("looks better", r["moves"][0]["reason"])

    def test_pick_takes_dependents_along(self):
        lo = make_lo(["X.esm", "Z.esm", "Y.esm"], masters={"Z.esm": ["X.esm"]})
        ctx, st, r = self.run_rec(lo, picks=[self.pick("X.esm", "Y.esm")])
        self.assertEqual(mods_of(r["order"]), ["Y.esm", "X.esm", "Z.esm"])

    def test_pick_wins_over_the_score(self):
        # the pick puts A after B, so B now loses its Keywords change to A; letting B win again would undo the
        # pick, so the climb can't, even though the score goes up
        lo = make_lo(["A.esm", "B.esm"])
        lost_ = [entry("1", "B.esm", "A.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("B.esm", "A.esm", [0], lost_)]),
                                  picks=[self.pick("A.esm", "B.esm")])
        self.assertEqual(mods_of(r["order"]), ["B.esm", "A.esm"])
        self.assertGreater(r["score_recommended"], r["score_current"])
        self.assertIn("undo one of your picks", r["rejected"][0]["why"])

    def test_bad_picks(self):
        lo = make_lo(["A.esm", "B.esm"], masters={"B.esm": ["A.esm"]})
        ctx, st, r = self.run_rec(lo, picks=[self.pick("A.esm", "B.esm"), self.pick("Q.esm", "A.esm")])
        self.assertTrue(r["same_as_current"])
        self.assertEqual(len(r["picks"]["not_applied"]), 2)
        cautions = [f for f in ctx.report.findings if f.level == "caution"]
        self.assertEqual(len(cautions), 2)


class ArchivesAndLoot(Base):
    def test_archive_flip_warned(self):
        lo = make_lo(["A.esm", "B.esm"])
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        arc = {"paths": {"textures/x.dds": ["A.esm", "B.esm"], "textures/same.dds": ["A.esm", "B.esm"]},
               "digests": {"textures/same.dds": {"A.esm": "d1", "B.esm": "d1"}}}
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_)]), archives=arc)
        self.assertEqual(r["archive_flips"], [{"path": "textures/x.dds", "before": "B.esm", "after": "A.esm"}])
        f = [f for f in ctx.report.findings if f.level == "caution" and "archived file" in f.text]
        self.assertEqual(len(f), 1)

    def test_light_depth_no_records(self):
        lo = make_lo(["A.esm", "B.esm"])
        ctx, st, r = self.run_rec(lo)
        self.assertFalse(r["rated"])
        self.assertTrue(r["same_as_current"])
        self.assertIsNone(r["score_current"])
        self.assertEqual(r["moves"], [])
        self.assertTrue(any("can't be rated beyond masters and archives" in t for t in self.texts(ctx)))
        self.assertIn("plugins_txt", ctx.report.outputs)

    def test_loot_comparison(self):
        lo = make_lo(["A.esm", "B.esm", "C.esm"])
        lp = fixtures.plugins_txt(os.path.join(self.t, "loot.txt"),
                                  [("A.esm", True), ("C.esm", True), ("B.esm", True), ("Old.esm", False)])
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_)]),
                                  opts={"loot_order": lp})
        self.assertEqual(mods_of(r["order"]), ["B.esm", "A.esm", "C.esm"])
        v = r["vs_loot"]
        self.assertEqual(v["moved_count"], 1)
        self.assertEqual(v["moved"], ["B.esm"])
        self.assertEqual(v["score"], 3)  # LOOT's order has A above B: A's change is lost
        self.assertEqual(v["missing"], [])

    def test_loot_duplicate_lines_first_wins(self):
        # a second A.esm line in another case, and C.esm twice: only the first line of each counts
        lo = make_lo(["A.esm", "B.esm", "C.esm"])
        lp = fixtures.plugins_txt(os.path.join(self.t, "loot.txt"),
                                  [("A.esm", True), ("C.esm", True), ("B.esm", True), ("a.esm", True),
                                   ("C.esm", True)])
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_)]),
                                  opts={"loot_order": lp})
        v = r["vs_loot"]
        self.assertEqual(v["moved"], ["B.esm"])
        self.assertEqual(v["moved_count"], 1)
        self.assertEqual(v["score"], 3)
        self.assertEqual(v["extra"], [])
        self.assertEqual(v["missing"], [])

    def test_loot_file_missing(self):
        lo = make_lo(["A.esm"])
        ctx, st, r = self.run_rec(lo, opts={"loot_order": os.path.join(self.t, "nope.txt")})
        self.assertIn("error", r["vs_loot"])


class Helpers(unittest.TestCase):
    def test_moved_longest_run(self):
        self.assertEqual(recommend.moved(["B", "C", "A"], ["A", "B", "C"]), ["A"])
        self.assertEqual(recommend.moved(["A", "B"], ["A", "B"]), [])

    def test_plain(self):
        self.assertEqual(recommend.plain(["AddedReferences", "Music", "Timestamp", "Keywords", "Music"]),
                         [recommend.QUEST, "music and ambient sound", "keywords"])


LABEL = recommend.LABEL_CURRENT


class BlueprintWinner(Base):
    """0.1.2: a pair an official BlueprintShips file wins is never moved, counted or put in the merged-patch note
    (the game loads that file after every plugin, so neither an order nor a patch can load after it)."""
    BP = "BlueprintShips-SFBGS003.esm"

    def test_not_moved_and_not_in_merged_patch_note(self):
        lo = make_lo(["A.esm", "B.esm"])
        lo.plugins.append(model.Plugin(self.BP, True, official=True, masters=["Starfield.esm"]))
        lost_ = [entry("1", "A.esm", self.BP, [("Keywords", "Forwardable")], rtype="GenericBaseForm"),
                 entry("2", "B.esm", self.BP, [("Flags", "Choice")], rtype="GenericBaseForm")]
        lost_[0]["winner_official"] = lost_[1]["winner_official"] = True
        ps = [pair("A.esm", self.BP, [0], lost_), pair("B.esm", self.BP, [1], lost_, advice="conflict")]
        for p in ps:
            p["winner_official"] = True
        ctx, st, r = self.run_rec(lo, rec_data(lost_, ps))
        self.assertTrue(r["same_as_current"])
        self.assertEqual(r["moves"], [])
        self.assertEqual(r["order"][-1], self.BP)
        self.assertEqual(r["remaining"], [])
        self.assertFalse(any("merged patch" in t for t in self.texts(ctx)))

    def test_blueprint_key_alone_keeps_it_out_of_merged_patch_note(self):
        """code review 0.1.2, round 3: a pair with "blueprint": True (a BlueprintShips file sf-mod-check marks
        official but this tool doesn't know) stays out of the merged-patch note even without winner_official."""
        W = "W.esm"  # 0.1.3: an ordinary name, so only the key keeps the pair out (no second rule)
        lo = make_lo(["A.esm", W])
        lost_ = [entry("1", "A.esm", W, [("Keywords", "Forwardable")], rtype="GenericBaseForm")]
        p = pair("A.esm", W, [0], lost_)
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [p]))
        self.assertTrue(r["moves"] or r["remaining"])  # without the key the pair is acted on (moved or listed)
        p = pair("A.esm", W, [0], lost_)
        p["blueprint"] = True
        self.assertFalse(records.acts_on(p))
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [p]))
        self.assertEqual(r["moves"], [])
        self.assertEqual(r["remaining"], [])
        self.assertFalse(any("merged patch" in t for t in self.texts(ctx)))

    def test_blueprint_key_alone_is_never_moved(self):
        """code review 0.1.2, round 4, reworked in 0.1.3: a pair with "blueprint": True isn't a flip candidate. The
        winner W.esm is an ordinary movable mod and not official, so only the key keeps the pair out (the same pair
        without the key is moved, which the first half proves), so the second half fails if acts_on stops reading
        the key."""
        lo = make_lo(["A.esm", "W.esm"])
        # enough lost records that the saving is worth a move
        lost_ = [entry(str(i), "A.esm", "W.esm", [("Keywords", "Forwardable")], rtype="GenericBaseForm")
                 for i in range(40)]
        p = pair("A.esm", "W.esm", list(range(40)), lost_)
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [p]))
        self.assertEqual(len(r["moves"]), 1)  # without the key the pair is a move candidate
        p = pair("A.esm", "W.esm", list(range(40)), lost_)
        p["blueprint"] = True
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [p]))
        self.assertEqual(r["moves"], [])
        self.assertTrue(r["same_as_current"])

    def test_pair_without_advice_is_not_acted_on(self):
        """0.1.3: records.acts_on reads advice with get, so a pair without the key is not acted on (it raised before)."""
        self.assertFalse(records.acts_on({"loser": "A.esm", "winner": "B.esm"}))

    def test_move_candidates_use_the_shared_acts_on_rule(self):
        """0.1.3: the recommender's move candidates come from records.acts_on, not a second copy of the rule.
        With acts_on forced to False a pair that would be moved stays put (fails on the 0.1.2 code, which ignored it)."""
        from unittest import mock
        lo = make_lo(["A.esm", "B.esm", "C.esm"])
        p = fixtures.conflicts_json(os.path.join(self.t, "conflicts.json"),
                                    [(n, n in OFFICIAL, []) for n in lo.enabled()],
                                    lost=[lost("000100:Starfield.esm", "A.esm", "C.esm", [field("Keywords")],
                                               edid="SomeNpc")])
        cj, err = records.load(p)
        self.assertIsNone(err)
        rec = records.analyse(cj, lo)
        ctx, st, r = self.run_rec(lo, rec)
        self.assertEqual([m["plugin"] for m in r["moves"]], ["A.esm"], "control: a move candidate without the patch")
        with mock.patch.object(records, "acts_on", return_value=False):
            ctx, st, r = self.run_rec(lo, rec)
        self.assertEqual(r["moves"], [])

    def test_navi_under_a_mod_is_not_moved_or_scored(self):
        """The navmesh info map is never a lost change (each plugin's copy lists only its own navmeshes), so a pair
        with only NAVI scores nothing and moves nothing (before 0.1.2 A.esm moved below C.esm for it)."""
        lo = make_lo(["A.esm", "B.esm", "C.esm"])
        p = fixtures.conflicts_json(os.path.join(self.t, "conflicts.json"),
                                    [(n, n in OFFICIAL, []) for n in lo.enabled()],
                                    lost=[lost("000100:Starfield.esm", "A.esm", "C.esm", [field("MapInfos")],
                                               rtype="NavigationMeshInfoMap")])
        cj, err = records.load(p)
        self.assertIsNone(err)
        ctx, st, r = self.run_rec(lo, records.analyse(cj, lo))
        self.assertEqual((r["score_current"], r["score_recommended"]), (0, 0))
        self.assertEqual(r["moves"], [])
        self.assertEqual(r["remaining"], [])

    def test_navi_left_out_of_merged_patch_note(self):
        """A pair with NAVI and a real record that can't be fixed by moving: the merged-patch note lists the real
        record only."""
        lo = make_lo(["A.esm", "B.esm", "C.esm"])
        p = fixtures.conflicts_json(os.path.join(self.t, "conflicts.json"),
                                    [(n, n in OFFICIAL, []) for n in lo.enabled()],
                                    lost=[lost("000100:Starfield.esm", "A.esm", "C.esm", [field("MapInfos")],
                                               rtype="NavigationMeshInfoMap", edid="NaviMap"),
                                          lost("000200:Starfield.esm", "A.esm", "C.esm", [field("Keywords")],
                                               edid="SomeNpc")])
        cj, err = records.load(p)
        self.assertIsNone(err)
        # the user's pick keeps C.esm after A.esm, so no move keeps A's changes
        ctx, st, r = self.run_rec(lo, records.analyse(cj, lo), picks=[{"plugin": "C.esm", "after": "A.esm"}])
        self.assertEqual([m["plugin"] for m in r["moves"]], ["C.esm"])  # the pick only; A.esm stays above C.esm
        self.assertEqual([x["edid"] for x in r["remaining"]], ["SomeNpc"])
        note = next(t for t in self.texts(ctx) if "merged patch" in t)
        self.assertNotIn("NaviMap", note)
        self.assertNotIn("NavigationMeshInfoMap", note)

    def test_ordinary_mod_winner_still_in_merged_patch_note(self):
        lo = make_lo(["A.esm", "B.esm"])
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")])]
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0], lost_, cost=10, complete=True)]))
        self.assertEqual([x["loser"] for x in r["remaining"]], ["A.esm"])

    def test_merged_patch_note_names_a_record_with_no_name(self):
        """Opus rerun round 5: a lost entry with no record type, EditorID or form key left a blank in the merged-patch
        note ("  (keywords: ...)"); it now says "a record", and a type with no name says "a <type> record"."""
        lo = make_lo(["A.esm", "B.esm"])
        lost_ = [entry("1", "A.esm", "B.esm", [("Keywords", "Forwardable")]),
                 entry("2", "A.esm", "B.esm", [("Flags", "Forwardable")])]
        lost_[0].update(fk="", edid=None, type="")
        lost_[1].update(fk="", edid=None, type="Keyword")
        ctx, st, r = self.run_rec(lo, rec_data(lost_, [pair("A.esm", "B.esm", [0, 1], lost_, cost=10, complete=True)]))
        note = next(t for t in self.texts(ctx) if "merged patch" in t)
        self.assertIn("a record (keywords: A.esm loses to B.esm)", note)
        self.assertIn("a Keyword record (record flags: A.esm loses to B.esm)", note)
        self.assertNotIn("  (", note)


if __name__ == "__main__":
    unittest.main()
