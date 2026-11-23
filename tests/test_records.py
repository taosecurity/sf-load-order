"""Tests for records.py and sfmodcheck.py. Synthetic only: fixtures.conflicts_json files and a fake sf-mod-check (a
small .py plus a .cmd wrapper on Windows or a shell script elsewhere, written to a temp folder) that prints a version and writes a conflicts.json.

Fixtures written here (not in tests\\fixtures.py): FAKE (the fake program), lost()/field() (lost entries shaped like
sf-mod-check 0.8.0's ConflictReport.cs output), make_lo() (a LoadOrder without reading files)."""
import argparse
import io
import json
import os
import sys
import tempfile
import textwrap
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import inputs, log, model, records, sfmodcheck  # noqa: E402
from tests import fixtures  # noqa: E402

FAKE = textwrap.dedent(r'''
    import json, os, sys, time
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = json.load(open(os.path.join(here, "fake.json"), encoding="utf-8"))
    a = sys.argv[1:]
    with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(a) + "\n")
    if a == ["--version"]:
        print(cfg.get("version", "sf-mod-check 0.8.0 by someone - GPL-3.0"))
        sys.exit(0)
    if a and a[0] == "conflict":
        time.sleep(cfg.get("sleep", 0))
        out = a[a.index("--out") + 1]
        plugins = a[a.index("--plugins") + 1]
        with open(os.path.join(here, "plugins-seen.txt"), "w", encoding="utf-8") as f:
            f.write(open(plugins, encoding="utf-8").read())
        if cfg.get("conflicts") is not None:
            os.makedirs(out, exist_ok=True)
            json.dump(cfg["conflicts"], open(os.path.join(out, "conflicts.json"), "w", encoding="utf-8"))
        print("some progress line")
        print(cfg.get("result", "RESULT: OK, 1 lost-change entries (0 official, 1 mod vs mod), 0 ITMs, 0 load-order problems"))
        sys.exit(cfg.get("exit", 0))
    if a and a[0] == "check":
        name = os.path.basename(a[1]).lower()
        c = cfg.get("check", {}).get(name, {})
        print(c.get("stdout", "sf-mod-check 0.8.0 check: x (full master, 1 masters, 3 records, 1 overrides (0 ITM), 2 links)\nRESULT: OK, 0 errors, 0 warnings"))
        sys.exit(c.get("exit", 0))
    sys.exit(2)
''')


def field(name, winner_too=False, minor=False, loser_minor=None):
    lm = minor if loser_minor is None else loser_minor
    return {"Field": name, "WinnerChangedToo": winner_too, "LoserValue": "a", "WinnerValue": "b",
            "Differences": [{"Text": f"{name} differs", "Minor": minor}], "Original": "o",
            "LoserChanges": [{"Text": "changed", "Minor": lm}], "WinnerChanges": []}


def lost(fk, loser, winner, fields, rtype="Npc", loser_official=False, edid="Rec"):
    return {"formKey": fk, "RecordType": rtype, "EditorID": edid, "Loser": loser, "LoserOfficial": loser_official,
            "Winner": winner, "Fields": fields}


def make_lo(names, data=None, source="plugins.txt", mods=None, official=("Starfield.esm", "SFBGS003.esm")):
    ps = [model.Plugin(n, True, official=True) for n in official]
    for n in names:
        on = not n.startswith("-")
        ps.append(model.Plugin(n.lstrip("-"), on))
    return model.LoadOrder(source=source, data=data, plugins=ps, mods=mods)


LO_ENTRIES = [("Starfield.esm", True, []), ("SFBGS003.esm", True, ["Starfield.esm"]),
              ("A.esm", False, ["Starfield.esm"]), ("B.esm", False, ["Starfield.esm"]),
              ("C.esm", False, ["Starfield.esm"]), ("D.esm", False, ["Starfield.esm", "C.esm"]),
              ("E.esm", False, ["Starfield.esm"])]


def ctx_for(t, lo, **opts):
    a = argparse.Namespace(depth=None, sf_mod_check=None, conflicts=None, max_memory=None, out=t)
    for k, v in opts.items():
        setattr(a, k, v)
    from sf_load_order.cli import Context
    return Context(a, lo, model.Report("test"))


def sample(path):
    return fixtures.conflicts_json(path, LO_ENTRIES, lost=[
        # B wins over A: a field B didn't touch (Forwardable) -> patch candidate
        lost("000100:Starfield.esm", "A.esm", "B.esm", [field("Keywords")]),
        # C wins over B: both changed it (Choice) -> pick a winner
        lost("000200:Starfield.esm", "B.esm", "C.esm", [field("Flags", winner_too=True), field("Name")]),
        # E wins over C only in list order (No effect) -> same edit
        lost("000300:Starfield.esm", "C.esm", "E.esm", [field("Items", minor=True)]),
        # an official change undone by E
        lost("000400:Starfield.esm", "SFBGS003.esm", "E.esm", [field("Location")], loser_official=True),
    ], itms=[{"Plugin": "E.esm", "formKey": "000500:Starfield.esm", "RecordType": "Keyword", "EditorID": "K"}],
        problems=["X.esm: cannot be read (IOException), plugin skipped; run sf-mod-check check on it for details",
                  "D.esm: listed before its master C.esm; the game would fail to load it, so it was analyzed after C.esm"])


class Mapping(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.lo = make_lo(["A.esm", "B.esm", "C.esm", "D.esm", "E.esm", "X.esm", "F.esm"])
        p = sample(os.path.join(self.t, "conflicts.json"))
        cj, err = records.load(p)
        self.assertIsNone(err)
        self.res = records.analyse(cj, self.lo)

    def test_labels(self):
        self.assertEqual(records.label(field("x")), "Forwardable")
        self.assertEqual(records.label(field("x", winner_too=True)), "Choice")
        self.assertEqual(records.label(field("x", winner_too=True, minor=True)), "No effect")
        self.assertEqual(records.label(field("x", minor=False, loser_minor=True)), "No effect")

    def test_weights(self):
        self.assertEqual(records.weight("Keywords"), 3)
        self.assertEqual(records.weight("Lighting"), 1)
        self.assertEqual(records.weight("Timestamp"), 0)
        self.assertEqual(records.weight("Keywords", "No effect"), 0)

    def test_pairs_advice(self):
        adv = {(p["loser"], p["winner"]): p["advice"] for p in self.res["pairs"]}
        self.assertEqual(adv, {("A.esm", "B.esm"): "merge", ("B.esm", "C.esm"): "conflict",
                               ("C.esm", "E.esm"): "same"})
        bc = next(p for p in self.res["pairs"] if p["winner"] == "C.esm")
        self.assertEqual(bc["fields"], {"Forwardable": 1, "Choice": 1, "No effect": 0})
        self.assertEqual(bc["gain_if_flipped"], 4)  # Flags 3 + Name 1
        self.assertEqual(bc["known_cost_if_flipped"], 3)
        self.assertFalse(bc["cost_complete"])

    def test_official_undone(self):
        self.assertEqual(len(self.res["official_undone"]), 1)
        x = self.res["lost"][self.res["official_undone"][0]]
        self.assertEqual((x["loser"], x["winner"]), ("SFBGS003.esm", "E.esm"))

    def test_ratings(self):
        r = {k: v["rating"] for k, v in self.res["plugins"].items()}
        self.assertEqual(r["a.esm"], "matters")
        self.assertEqual(r["b.esm"], "matters")
        self.assertEqual(r["c.esm"], "matters")
        self.assertEqual(r["e.esm"], "neutral-here")    # same edit + official undone + ITM
        self.assertEqual(r["d.esm"], "neutral")
        self.assertEqual(r["x.esm"], "unchecked")       # sf-mod-check couldn't read it
        self.assertEqual(r["f.esm"], "unchecked")       # not in conflicts.json's load order
        self.assertIn("doesn't say whether", self.res["plugins"]["d.esm"]["why"])

    def test_problems_split(self):
        seen = {p["kind"]: p["seen_by"] for p in self.res["problems"]}
        self.assertEqual(seen, {"unreadable": "records", "master-order": "order"})

    def test_findings(self):
        ctx = ctx_for(self.t, self.lo)
        records.add_findings(ctx, self.res, "file")
        texts = [f.text for f in ctx.report.findings]
        self.assertTrue(any("patch candidate" in t for t in texts))
        self.assertTrue(any("pick a winner" in t for t in texts))
        self.assertTrue(any("undoes official changes" in t for t in texts))
        self.assertTrue(any("ITM" in t for t in texts))
        self.assertTrue(any("cannot be read" in t for t in texts))
        self.assertFalse(any("listed before its master" in t for t in texts))   # order.py's job
        self.assertFalse(any("E.esm wins over C.esm" in t for t in texts))  # same edit: no finding
        self.assertTrue(any("another load order" in t for t in texts))  # F.esm isn't in the file
        for f in ctx.report.findings:
            self.assertEqual(f.section, "records")

    def test_same_mod_and_built(self):
        lo = make_lo(["A.esm", "B.esm", "C.esm", "D.esm", "E.esm"],
                     mods=[model.Mod("Pack", True, plugins=["A.esm", "B.esm"])])
        cj = json.load(open(os.path.join(self.t, "conflicts.json"), encoding="utf-8"))
        cj["lost"].append(lost("000600:Starfield.esm", "C.esm", "D.esm", [field("Keywords")]))  # D has C as master
        res = records.analyse(cj, lo)
        adv = {(p["loser"], p["winner"]): p["advice"] for p in res["pairs"]}
        self.assertEqual(adv[("A.esm", "B.esm")], "samemod")
        self.assertEqual(adv[("C.esm", "D.esm")], "built")

    def test_null_fields(self):
        """conflicts.json may hold null where a name is expected (sf-mod-check writes C# nulls as null)."""
        cj = {"loadOrder": [{"FileName": None, "Official": False, "Masters": [None, "Starfield.esm"]},
                            {"FileName": "A.esm", "Official": False, "Masters": None}],
              "lost": [{"formKey": None, "RecordType": None, "EditorID": None, "Loser": None, "Winner": "A.esm",
                        "Fields": [{"Field": None, "Differences": [{"Text": None}]}]},
                       {"formKey": "000100:Starfield.esm", "RecordType": "Npc", "Loser": "A.esm", "Winner": None,
                        "Fields": []}],
              "itms": [{"Plugin": None, "formKey": None, "RecordType": None}],
              "typeChanges": [{"Plugin": None, "formKey": None}],
              "loadOrderProblems": []}
        res = records.analyse(cj, make_lo(["A.esm"]))
        self.assertEqual(res["plugins"]["a.esm"]["itms"], 0)
        self.assertEqual(res["lost"][0]["loser"], "")
        self.assertEqual(res["lost"][1]["winner"], "")

    def test_bad_files(self):
        bad = os.path.join(self.t, "bad.json")
        open(bad, "w").write("{not json")
        self.assertIn("isn't valid JSON", records.load(bad)[1])
        open(bad, "w").write('{"x": 1}')
        self.assertIn("doesn't look like", records.load(bad)[1])
        # a key that is present but isn't a list gets the same plain message (bug hunt 0.1.3: the old wording
        # "no loadOrder and lost lists" read as if the keys were missing)
        open(bad, "w").write('{"loadOrder": 5, "lost": "x"}')
        self.assertIn("needs a loadOrder list and a lost list", records.load(bad)[1])
        self.assertIn("can't read", records.load(os.path.join(self.t, "none.json"))[1])


BP = "BlueprintShips-SFBGS003.esm"


class BlueprintWinner(unittest.TestCase):
    """0.1.2: an official BlueprintShips file loads after every mod, so a pair it wins gets sf-mod-check 0.12.0's
    wording (BlueprintAdvice), never "patch candidate", "pick a winner" or "a patch that loads after it can"."""
    def setUp(self):
        import io
        from sf_load_order import log
        self.t = tempfile.mkdtemp()
        self.lo = make_lo(["A.esm", "B.esm", "C.esm"], official=("Starfield.esm", "SFBGS003.esm", BP))
        entries = [("Starfield.esm", True, []), ("SFBGS003.esm", True, ["Starfield.esm"]),
                   ("A.esm", False, ["Starfield.esm"]), ("B.esm", False, ["Starfield.esm"]),
                   ("C.esm", False, ["Starfield.esm"]), (BP, True, ["Starfield.esm", "SFBGS003.esm"])]
        p = fixtures.conflicts_json(os.path.join(self.t, "conflicts.json"), entries, lost=[
            # a ship both changed (Choice): would be "pick a winner" between two mods
            lost("000100:SFBGS003.esm", "A.esm", BP, [field("Flags", winner_too=True)], rtype="GenericBaseForm"),
            # two records only A changed (Forwardable): would be "patch candidate" between two mods
            lost("000200:SFBGS003.esm", "B.esm", BP, [field("Keywords")], rtype="GenericBaseForm"),
            lost("000201:SFBGS003.esm", "B.esm", BP, [field("Keywords")], rtype="Npc"),
            # an ordinary official winner keeps today's text
            lost("000300:Starfield.esm", "C.esm", "SFBGS003.esm", [field("Keywords")]),
        ])
        cj, err = records.load(p)
        self.assertIsNone(err)
        self.res = records.analyse(cj, self.lo)
        self.buf = io.StringIO()
        log._sinks.append(self.buf)
        self.addCleanup(log._sinks.remove, self.buf)
        self.ctx = ctx_for(self.t, self.lo)
        records.add_findings(self.ctx, self.res, "run")
        self.by = {tuple(f.plugins): f for f in self.ctx.report.findings}

    def test_is_blueprint(self):
        self.assertTrue(records.is_blueprint("blueprintships-starfield.esm"))
        self.assertTrue(records.is_blueprint(BP))
        self.assertFalse(records.is_blueprint("BlueprintShips-MyMod.esm"))  # not official: the game drops it
        self.assertFalse(records.is_blueprint("SFBGS003.esm"))

    def test_ship_pair(self):
        f = self.by[("A.esm", BP)]
        whole = f.text + " " + f.fix
        self.assertIn(f"{BP} loads after every mod, so no load order or patch brings this change back; the edit "
                      "A.esm makes to this ship doesn't reach the game.", f.text)
        for bad in ("patch candidate", "pick a winner", "a patch that loads after it can"):
            self.assertNotIn(bad, whole)
        self.assertEqual(f.level, "caution")  # a functional field, as before

    def test_mergeable_pair(self):
        f = self.by[("B.esm", BP)]
        whole = f.text + " " + f.fix
        self.assertIn("the edit B.esm makes to these 2 records doesn't reach the game.", f.text)
        for bad in ("patch candidate", "pick a winner", "a patch that loads after it can", "a patch can keep"):
            self.assertNotIn(bad, whole)

    def test_ordinary_official_winner_unchanged(self):
        f = self.by[("C.esm", "SFBGS003.esm")]
        self.assertIn("(patch candidate)", f.text)
        self.assertIn("a patch that loads after it can", f.fix)

    def test_not_counted_as_to_act_on(self):
        acts = [(p["loser"], p["winner"]) for p in self.res["pairs"] if records.acts_on(p)]
        self.assertEqual(acts, [("C.esm", "SFBGS003.esm")])
        self.assertEqual(self.res["plugins"]["a.esm"]["rating"], "neutral-here")

    def test_verbose_reason(self):
        self.assertIn(f"A.esm vs {BP}: {BP} is an official BlueprintShips file", self.buf.getvalue())

    def test_pair_advice_and_label(self):
        """code review 0.1.2: the pair itself (report.json, the pair list, the ratings' why) says neither "patch
        candidate" nor "pick a winner" either, not only the finding."""
        by = {(p["loser"], p["winner"]): p for p in self.res["pairs"]}
        for loser in ("A.esm", "B.esm"):
            p = by[(loser, BP)]
            self.assertEqual((p["advice"], p["label"], p["blueprint"]),
                             ("blueprint", "no action: BlueprintShips file wins", True))
            self.assertEqual(self.by[(loser, BP)].data["advice"], "blueprint")
        self.assertEqual(by[("C.esm", "SFBGS003.esm")]["advice"], "merge")
        self.assertFalse(by[("C.esm", "SFBGS003.esm")]["blueprint"])
        why = self.res["plugins"]["a.esm"]["why"]
        self.assertIn("no action: BlueprintShips file wins", why)
        self.assertNotIn("pick a winner", why)


class BlueprintOfficialFlag(unittest.TestCase):
    """code review 0.1.2: sf-mod-check counts more names as official than this tool does (sfta01 and its
    BlueprintShips file). A BlueprintShips winner that conflicts.json marks Official gets the BlueprintShips
    wording, isn't "to act on", doesn't make the loser "order matters" and isn't in the merged-patch note."""
    W = "BlueprintShips-sfta01.esm"

    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.lo = make_lo(["A.esm", "B.esm"])
        entries = [("Starfield.esm", True, []), ("SFBGS003.esm", True, ["Starfield.esm"]),
                   ("A.esm", False, ["Starfield.esm"]), ("B.esm", False, ["Starfield.esm"]),
                   (self.W, True, ["Starfield.esm"])]
        p = fixtures.conflicts_json(os.path.join(self.t, "conflicts.json"), entries, lost=[
            lost("000100:Starfield.esm", "A.esm", self.W, [field("Flags", winner_too=True)], rtype="GenericBaseForm"),
            lost("000200:Starfield.esm", "B.esm", self.W, [field("Keywords")], rtype="GenericBaseForm"),
        ])
        cj, err = records.load(p)
        self.assertIsNone(err)
        self.res = records.analyse(cj, self.lo)
        self.ctx = ctx_for(self.t, self.lo)
        records.add_findings(self.ctx, self.res, "run")

    def test_flag_counts(self):
        self.assertTrue(records.is_blueprint(self.W, True))
        self.assertFalse(records.is_blueprint(self.W))  # this tool's own list doesn't name sfta01
        self.assertFalse(records.is_blueprint("BlueprintShips-MyMod.esm", False))

    def test_wording_and_counts(self):
        for p in self.res["pairs"]:
            self.assertEqual(p["advice"], "blueprint")
            self.assertFalse(records.acts_on(p))
        for n in ("a.esm", "b.esm"):
            self.assertNotEqual(self.res["plugins"][n]["rating"], "matters")
        for f in self.ctx.report.findings:
            whole = f.text + " " + (f.fix or "")
            for bad in ("patch candidate", "pick a winner", "a patch that loads after it can", "a patch can keep"):
                self.assertNotIn(bad, whole)
        self.assertTrue(any(f"{self.W} loads after every mod" in f.text for f in self.ctx.report.findings))


class StalenessOfficialNames(unittest.TestCase):
    """0.1.3: sf-mod-check counts sfta01 to sfta06 official and leaves them out of its mod list, so the staleness
    comparison must not call them missing. A real difference still counts."""
    SFTA = ["sfta01.esm", "sfta02.esm", "sfta03.esm", "sfta04.esm", "sfta05.esm", "sfta06.esm"]

    def differs(self, ours, theirs):
        """ours: names enabled here; theirs: (name, official) pairs in conflicts.json's loadOrder."""
        t = tempfile.mkdtemp()
        entries = [("Starfield.esm", True, [])] + [(n, o, ["Starfield.esm"]) for n, o in theirs]
        p = fixtures.conflicts_json(os.path.join(t, "conflicts.json"), entries, lost=[])
        cj, err = records.load(p)
        self.assertIsNone(err)
        return records._differs(make_lo(ours), cj)

    def test_sfta_listed_official_is_not_missing(self):
        d = self.differs(["A.esm"] + self.SFTA, [(n, True) for n in self.SFTA] + [("A.esm", False)])
        self.assertIsNone(d)

    def test_sfta_left_out_of_the_file_is_not_missing(self):
        self.assertIsNone(self.differs(["A.esm"] + self.SFTA, [("A.esm", False)]))

    def test_name_rule_counts_when_the_flag_is_false(self):
        self.assertIsNone(self.differs(["A.esm", "BlueprintShips-sfta03.esm"], [("A.esm", False)]))

    def test_real_missing_mod_still_counts_next_to_sfta(self):
        d = self.differs(["A.esm", "B.esm"] + self.SFTA, [(n, True) for n in self.SFTA] + [("A.esm", False)])
        self.assertEqual(d, {"missing": ["b.esm"], "extra": [], "order": False})

    def test_extra_and_order_still_count(self):
        d = self.differs(["B.esm", "A.esm", "sfta01.esm"], [("sfta01.esm", True), ("A.esm", False),
                                                           ("B.esm", False), ("C.esm", False)])
        self.assertEqual(d, {"missing": [], "extra": ["c.esm"], "order": True})

    def test_a_mod_named_like_sfta_is_not_official(self):
        for n in ("sfta01Fixes.esm", "SFBGS003Fixes.esm", "sfta1.esm", "sfta001.esm", "BlueprintShips-MyMod.esm"):
            self.assertFalse(inputs.modcheck_official(n), n)
            d = self.differs(["A.esm", n], [("A.esm", False)])
            self.assertEqual(d["missing"], [n.lower()], n)

    def test_name_rule(self):
        for n in ("sfta06.esm", "SFTA03.ESM", "SFBGS00D.esm", "BlueprintShips-sfta03.esm",
                  "BlueprintShips-Starfield.esm", "Starfield.esm", "ShatteredSpace.esm"):
            self.assertTrue(inputs.modcheck_official(n), n)
        self.assertFalse(inputs.is_official("sfta01.esm"))  # is_official is unchanged

    def test_verbose_log_names_what_is_left_out(self):
        buf = io.StringIO()
        log._sinks[:] = [buf]
        try:
            self.differs(["A.esm", "sfta01.esm"], [("A.esm", False)])
            text = buf.getvalue()
        finally:
            log._sinks[:] = []
        self.assertIn("left out of the load order comparison", text)
        self.assertIn("sfta01.esm", text)


class NaviCounts(unittest.TestCase):
    """code review 0.1.2: the records summary doesn't count navmesh info map entries as lost changes, and a NAVI-only
    pair's line shows its navmesh records, not "0 records"."""
    def test_status_and_pair_line(self):
        from contextlib import redirect_stdout
        from sf_load_order import cli, report
        t = tempfile.mkdtemp()
        root = os.path.join(t, "game")
        data, ptxt = fixtures.game(root, {n: {"masters": ["Starfield.esm"]} for n in ("A.esm", "B.esm", "C.esm")})
        cj = fixtures.conflicts_json(os.path.join(t, "cli.json"),
                                     [("Starfield.esm", True, []), ("A.esm", False, ["Starfield.esm"]),
                                      ("B.esm", False, ["Starfield.esm"]), ("C.esm", False, ["Starfield.esm"])],
                                     lost=[lost("000100:Starfield.esm", "A.esm", "B.esm", [field("Keywords")]),
                                           lost("000200:Starfield.esm", "A.esm", "C.esm", [field("MapInfos")],
                                                rtype=NAVI_TYPE)])
        out = os.path.join(t, "report")
        args = cli.parser().parse_args(["--plugins-txt", ptxt, "--data", data, "--conflicts", cj, "--out", out,
                                         "--no-state"])
        with redirect_stdout(io.StringIO()):
            code, rep_ = cli.run(args)
        st = rep_.sections["records"]
        self.assertEqual((st["lost"], st["navi"]), (1, 1))
        with open(os.path.join(out, "report.txt"), encoding="utf-8") as f:
            txt = " ".join(f.read().split())  # report.txt wraps its lines
        self.assertIn("1 lost change entry (and 1 navmesh info map entry, which aren't lost changes)", txt)
        cjd, err = records.load(cj)
        self.assertIsNone(err)
        res = records.analyse(cjd, make_lo(["A.esm", "B.esm", "C.esm"]))
        navi_pair = next(p for p in res["pairs"] if p["winner"] == "C.esm")
        line = report._item_text(navi_pair)
        self.assertIn("1 navmesh info map record", line)
        self.assertNotIn("0 records", line)
        with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
            j = json.load(f)
        self.assertEqual(j["results"]["records"]["lost"], 1)
        self.assertEqual(j["results"]["records"]["navi"], 1)


class ReviewRound2(unittest.TestCase):
    """code review 0.1.2, rounds 2 and 3."""
    def analyse(self, entries, lost_, mods=None):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = fixtures.conflicts_json(os.path.join(d.name, "conflicts.json"), entries, lost=lost_)
        cj, err = records.load(p)
        self.assertIsNone(err)
        return records.analyse(cj, make_lo([n for n, off, _ in entries if not off]))

    def test_navi_only_built_pair_leaves_rating_alone(self):
        """A NAVI-only pair whose loser is a master of the winner gets advice "built", but still doesn't make either
        plugin "neutral here"."""
        res = self.analyse([("Starfield.esm", True, []), ("B.esm", False, ["Starfield.esm"]),
                            ("A.esm", False, ["Starfield.esm", "B.esm"])],
                           [lost("000100:Starfield.esm", "B.esm", "A.esm", [field("MapInfos")], rtype=NAVI_TYPE)])
        (p,) = res["pairs"]
        self.assertEqual((p["advice"], p["records"], len(p["navi"])), ("built", [], 1))
        self.assertEqual(res["plugins"]["a.esm"]["rating"], "neutral")
        self.assertEqual(res["plugins"]["b.esm"]["rating"], "neutral")

    def test_winner_official_field_counts(self):
        """sf-mod-check writes WinnerOfficial on each lost entry; a BlueprintShips winner marked there (and not in
        the load order list) gets the BlueprintShips advice."""
        W = "BlueprintShips-sfta01.esm"
        e = lost("000100:Starfield.esm", "A.esm", W, [field("Flags", winner_too=True)], rtype="GenericBaseForm")
        e["WinnerOfficial"] = True
        res = self.analyse([("Starfield.esm", True, []), ("A.esm", False, ["Starfield.esm"]),
                            (W, False, ["Starfield.esm"])], [e])
        (p,) = res["pairs"]
        # round 3: the flag feeds only the BlueprintShips check, not winner_official (ratings, recommender)
        self.assertEqual((p["winner_official"], p["blueprint"], p["advice"]), (False, True, "blueprint"))
        self.assertFalse(records.acts_on(p))

    def test_winner_official_on_a_later_entry(self):
        """Any entry of the pair marking the BlueprintShips winner official counts, not only the first."""
        W = "BlueprintShips-sfta01.esm"
        e1 = lost("000100:Starfield.esm", "A.esm", W, [field("Flags", winner_too=True)], rtype="GenericBaseForm")
        e2 = lost("000200:Starfield.esm", "A.esm", W, [field("Keywords")], rtype="GenericBaseForm")
        e2["WinnerOfficial"] = True
        res = self.analyse([("Starfield.esm", True, []), ("A.esm", False, ["Starfield.esm"]),
                            (W, False, ["Starfield.esm"])], [e1, e2])
        (p,) = res["pairs"]
        self.assertEqual((p["blueprint"], p["advice"], len(p["records"])), (True, "blueprint", 2))

    def test_winner_official_on_a_mod_name_changes_nothing(self):
        """WinnerOfficial on a winner that isn't a BlueprintShips file leaves the pair as it was: still to act on,
        and the loser still "order matters"."""
        e = lost("000100:Starfield.esm", "A.esm", "B.esm", [field("Keywords")])
        e["WinnerOfficial"] = True
        res = self.analyse([("Starfield.esm", True, []), ("A.esm", False, ["Starfield.esm"]),
                            ("B.esm", False, ["Starfield.esm"])], [e])
        (p,) = res["pairs"]
        self.assertEqual((p["winner_official"], p["blueprint"], p["advice"]), (False, False, "merge"))
        self.assertTrue(records.acts_on(p))
        self.assertEqual(res["plugins"]["a.esm"]["rating"], "matters")

    def test_pair_line_with_a_record_count_and_navi(self):
        from sf_load_order import report
        line = report._item_text({"loser": "A.esm", "winner": "B.esm", "records": 3, "navi": [5]})
        self.assertIn("3 records", line)
        self.assertIn("1 navmesh info map record", line)


NAVI_TYPE = "NavigationMeshInfoMap"
NAVI_SHARED = ("Each plugin's copy lists only its own navmeshes, so this isn't a record one plugin wins whole: it isn't "
               "patched, and don't copy it by hand either (the Creation Kit rebuilds it when a navmesh is finalized).")
NAVI_BAD = ("patch candidate", "pick a winner", "a patch that loads after it can", "a patch can keep",
            "doesn't reach the game")


class NaviNote(unittest.TestCase):
    """0.1.2: the navmesh info map (NAVI) under any winner gets a note of its own (sf-mod-check NaviBlueprintNote):
    each plugin's copy lists only its own navmeshes, so it is never a patch candidate, a pick-a-winner choice, a lost
    change to count or something to act on. A pair with NAVI and real records keeps its advice for the real ones."""
    def setUp(self):
        import io
        from sf_load_order import log
        self.t = tempfile.mkdtemp()
        self.lo = make_lo(["A.esm", "B.esm", "D.esm", "C.esm"], official=("Starfield.esm", "SFBGS003.esm", BP))
        entries = [("Starfield.esm", True, []), ("SFBGS003.esm", True, ["Starfield.esm"]),
                   ("A.esm", False, ["Starfield.esm"]), ("B.esm", False, ["Starfield.esm"]),
                   ("D.esm", False, ["Starfield.esm"]), ("C.esm", False, ["Starfield.esm"]),
                   (BP, True, ["Starfield.esm", "SFBGS003.esm"])]
        p = fixtures.conflicts_json(os.path.join(self.t, "conflicts.json"), entries, lost=[
            # NAVI under a BlueprintShips file (the 8 rows of the 2026-11-21 real run had this shape)
            lost("000100:Starfield.esm", "A.esm", BP, [field("MapInfos")], rtype=NAVI_TYPE),
            # NAVI under a mod
            lost("000100:Starfield.esm", "B.esm", "C.esm", [field("MapInfos")], rtype=NAVI_TYPE),
            # NAVI mixed with a real record in one pair
            lost("000100:Starfield.esm", "D.esm", "C.esm", [field("MapInfos")], rtype=NAVI_TYPE),
            lost("000200:Starfield.esm", "D.esm", "C.esm", [field("Keywords")]),
            # NAVI of an official file under a mod: not an official change undone
            lost("000100:Starfield.esm", "SFBGS003.esm", "C.esm", [field("MapInfos")], rtype=NAVI_TYPE,
                 loser_official=True),
        ])
        cj, err = records.load(p)
        self.assertIsNone(err)
        self.buf = io.StringIO()
        log._sinks.append(self.buf)
        self.addCleanup(log._sinks.remove, self.buf)
        self.res = records.analyse(cj, self.lo)
        self.pair = {(x["loser"], x["winner"]): x for x in self.res["pairs"]}
        self.ctx = ctx_for(self.t, self.lo)
        records.add_findings(self.ctx, self.res, "run")
        self.found = self.ctx.report.findings

    def of(self, loser, winner):
        return [f for f in self.found if f.plugins == [loser, winner]]

    def test_navi_under_blueprint(self):
        p = self.pair[("A.esm", BP)]
        self.assertEqual((p["advice"], p["label"], p["records"], len(p["navi"])),
                         ("navi", "no action: navmesh info map", [], 1))
        self.assertEqual(p["gain_if_flipped"], 0)
        self.assertFalse(records.acts_on(p))
        (f,) = self.of("A.esm", BP)
        self.assertEqual(f.level, "note")
        self.assertIsNone(f.fix)
        self.assertEqual(f.text, f"{BP} and A.esm both change the navmesh info map (1 record(s)). {BP} loads after "
                                 f"every mod, so its copy of the navmesh info map loads last. {NAVI_SHARED}")
        for bad in NAVI_BAD:
            self.assertNotIn(bad, f.text)
        # code review 0.1.2: a NAVI-only pair isn't a lost change, so it doesn't make A.esm "neutral here"
        self.assertEqual(self.res["plugins"]["a.esm"]["rating"], "neutral")
        self.assertEqual(self.res["plugins"]["a.esm"]["pairs"], [])
        self.assertEqual(self.res["plugins"]["a.esm"]["lost_records"], 0)

    def test_navi_under_mod(self):
        p = self.pair[("B.esm", "C.esm")]
        self.assertEqual((p["advice"], p["records"], p["gain_if_flipped"]), ("navi", [], 0))
        self.assertFalse(records.acts_on(p))
        (f,) = self.of("B.esm", "C.esm")
        self.assertEqual(f.level, "note")
        self.assertIsNone(f.fix)
        self.assertIn("C.esm loads after B.esm, so its copy of the navmesh info map loads later. " + NAVI_SHARED,
                      f.text)
        for bad in NAVI_BAD:
            self.assertNotIn(bad, f.text)
        self.assertEqual(self.res["plugins"]["b.esm"]["rating"], "neutral")  # not "order matters", not "neutral here"

    def test_navi_mixed_with_a_real_record(self):
        p = self.pair[("D.esm", "C.esm")]
        self.assertEqual((p["advice"], len(p["records"]), len(p["navi"])), ("merge", 1, 1))
        self.assertEqual(p["gain_if_flipped"], 3)  # Keywords only, not MapInfos
        self.assertEqual(p["fields"], {"Forwardable": 1, "Choice": 0, "No effect": 0})
        self.assertTrue(records.acts_on(p))
        fs = self.of("D.esm", "C.esm")
        self.assertEqual(sorted(f.level for f in fs), ["caution", "note"])
        real = next(f for f in fs if f.level == "caution")
        self.assertIn("C.esm wins over D.esm on 1 record(s)", real.text)
        self.assertIn("(patch candidate)", real.text)
        self.assertNotIn("MapInfos", real.text)
        navi = next(f for f in fs if f.level == "note")
        self.assertIn(NAVI_SHARED, navi.text)
        self.assertEqual(self.res["plugins"]["d.esm"]["rating"], "matters")
        acts = sorted((x["loser"], x["winner"]) for x in self.res["pairs"] if records.acts_on(x))
        self.assertEqual(acts, [("D.esm", "C.esm")])

    def test_official_navi_not_counted_as_undone(self):
        self.assertEqual(self.res["official_undone"], [])
        self.assertFalse(any("undoes official changes" in f.text for f in self.found))
        self.assertIn("not counted as undoing an official change", self.buf.getvalue())

    def test_verbose_reason(self):
        self.assertIn(f"A.esm vs {BP}: 1 navmesh info map record(s) get a note", self.buf.getvalue())
        self.assertIn("B.esm vs C.esm: 1 navmesh info map record(s) get a note", self.buf.getvalue())


class Program(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.bin = os.path.join(self.t, "bin")
        os.makedirs(self.bin)
        self.data = os.path.join(self.t, "Data")
        for n in ("Starfield.esm", "SFBGS003.esm", "A.esm", "B.esm", "C.esm", "D.esm", "E.esm"):
            fixtures.plugin(os.path.join(self.data, n))
        self.lo = make_lo(["A.esm", "B.esm", "C.esm", "D.esm", "E.esm", "-Off.esm"], data=self.data)
        sample(os.path.join(self.t, "c.json"))
        self.cj = json.load(open(os.path.join(self.t, "c.json"), encoding="utf-8"))
        self.cj["loadOrderProblems"] = []

    def fake(self, name="sf-mod-check", wrapper=None, **cfg):
        """The fake program: fake_smc.py behind a .cmd wrapper on Windows, or behind an executable shell script
        (no extension, so PATH finds it by name) elsewhere. wrapper="cmd" writes the .cmd on any system (for the
        tests that only read it)."""
        with open(os.path.join(self.bin, "fake_smc.py"), "w", encoding="utf-8") as f:
            f.write(FAKE)
        cfg.setdefault("conflicts", self.cj)
        with open(os.path.join(self.bin, "fake.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        script = os.path.join(self.bin, "fake_smc.py")
        if (wrapper or ("cmd" if os.name == "nt" else "sh")) == "cmd":
            cmd = os.path.join(self.bin, name + ".cmd")
            with open(cmd, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(f'@echo off\n"{sys.executable}" -I "{script}" %*\nexit /b %ERRORLEVEL%\n')
            return cmd
        cmd = os.path.join(self.bin, name)
        with open(cmd, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n')
        os.chmod(cmd, 0o755)
        return cmd

    def calls(self):
        p = os.path.join(self.bin, "calls.jsonl")
        return [json.loads(l) for l in open(p, encoding="utf-8")] if os.path.exists(p) else []

    def test_missing_program(self):
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = self.bin  # empty folder: neither name is found
        try:
            st = records.run(ctx_for(self.t, self.lo))
        finally:
            os.environ["PATH"] = old
        self.assertEqual(st["status"], "skipped")
        self.assertEqual(st["why"], records.NO_PROGRAM)

    def test_given_path_missing(self):
        st = records.run(ctx_for(self.t, self.lo, sf_mod_check=os.path.join(self.t, "nope.exe")))
        self.assertEqual(st["status"], "skipped")
        self.assertIn("doesn't exist", st["why"])

    def test_find_on_path_order(self):
        self.fake("sfmodcheck")
        prog, err = sfmodcheck.find(argparse.Namespace(sf_mod_check=None), path=self.bin)
        self.assertEqual(prog.how, "PATH sfmodcheck")
        self.fake("sf-mod-check")
        prog, err = sfmodcheck.find(argparse.Namespace(sf_mod_check=None), path=self.bin)
        self.assertEqual(prog.how, "PATH sf-mod-check")

    def test_too_old(self):
        exe = self.fake(version="sfmodcheck 0.7.0 by someone - GPL-3.0")
        st = records.run(ctx_for(self.t, self.lo, sf_mod_check=exe))
        self.assertEqual(st["status"], "skipped")
        self.assertIn("0.7.0", st["why"])
        self.assertIn("0.8.0 or later", st["why"])
        self.assertEqual(self.calls(), [["--version"]])  # conflict was never run

    def test_run_ok(self):
        exe = self.fake()
        ctx = ctx_for(self.t, self.lo, sf_mod_check=exe, max_memory=8.0)
        st = records.run(ctx)
        self.assertEqual(st["status"], "done", st)
        self.assertEqual(st["source"], "run")
        self.assertTrue(st["result_line"].startswith("RESULT: OK"))
        conflict = self.calls()[1]
        self.assertEqual(conflict[0], "conflict")
        self.assertEqual(conflict[conflict.index("--data") + 1], self.data)
        self.assertEqual(conflict[conflict.index("--out") + 1], os.path.join(ctx.work, "conflict"))
        self.assertEqual(conflict[conflict.index("--max-memory") + 1], "8")
        seen = open(os.path.join(self.bin, "plugins-seen.txt"), encoding="utf-8").read().splitlines()
        self.assertEqual([l for l in seen if not l.startswith("#")],
                         ["*A.esm", "*B.esm", "*C.esm", "*D.esm", "*E.esm"])  # no officials, no disabled
        res = ctx.results["records"]
        self.assertEqual(res["plugins"]["a.esm"]["rating"], "matters")
        self.assertIsNone(res["differs"])
        self.assertEqual(res["program"]["version"], "0.8.0")

    def test_exit_2(self):
        exe = self.fake(exit=2, conflicts=None, result="RESULT: CANNOT CHECK (no load order: give --plugins)")
        ctx = ctx_for(self.t, self.lo, sf_mod_check=exe)
        st = records.run(ctx)
        self.assertEqual(st["status"], "skipped")
        self.assertEqual(st["exit"], 2)
        self.assertIn("CANNOT CHECK", st["why"])
        self.assertTrue(any("couldn't run" in f.text for f in ctx.report.findings))
        self.assertNotIn("records", ctx.results)

    def test_exit_3(self):
        exe = self.fake(exit=3, conflicts=None, result="RESULT: STOPPED (memory passed 8 GB; no report written)")
        ctx = ctx_for(self.t, self.lo, sf_mod_check=exe)
        st = records.run(ctx)
        self.assertEqual(st["exit"], 3)
        self.assertIn("memory limit", st["why"])
        self.assertTrue(any("higher --max-memory" in (f.fix or "") for f in ctx.report.findings))

    def test_exit_0_without_file(self):
        exe = self.fake(conflicts=None)
        st = records.run(ctx_for(self.t, self.lo, sf_mod_check=exe))
        self.assertEqual(st["status"], "skipped")
        self.assertIn("no conflicts.json", st["why"])

    def test_old_file_not_reused(self):
        exe = self.fake(exit=2, conflicts=None)
        ctx = ctx_for(self.t, self.lo, sf_mod_check=exe)
        os.makedirs(os.path.join(ctx.work, "conflict"))
        json.dump(self.cj, open(os.path.join(ctx.work, "conflict", "conflicts.json"), "w"))
        self.assertEqual(records.run(ctx)["status"], "skipped")
        self.assertFalse(os.path.exists(os.path.join(ctx.work, "conflict", "conflicts.json")))

    def test_timeout(self):
        exe = self.fake(sleep=20)
        ctx = ctx_for(self.t, self.lo, sf_mod_check=exe, sf_mod_check_timeout=2)
        t0 = time.monotonic()
        st = records.run(ctx)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertEqual(st["status"], "skipped")
        self.assertIn("didn't finish", st["why"])
        self.assertTrue(any("--conflicts" in (f.fix or "") for f in ctx.report.findings))

    def test_conflicts_file_skips_program(self):
        p = os.path.join(self.t, "given.json")
        json.dump(self.cj, open(p, "w"))
        ctx = ctx_for(self.t, self.lo, conflicts=p, sf_mod_check=os.path.join(self.t, "nope.exe"))
        st = records.run(ctx)
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["source"], "file")
        self.assertEqual(ctx.results["records"]["pairs"][0]["advice"], "merge")

    def test_bad_conflicts_file(self):
        p = os.path.join(self.t, "given.json")
        open(p, "w").write("[]")
        ctx = ctx_for(self.t, self.lo, conflicts=p)
        st = records.run(ctx)
        self.assertEqual(st["status"], "skipped")
        self.assertTrue(ctx.report.findings)

    def test_mo2_not_visible(self):
        exe = self.fake()
        lo = make_lo(["A.esm", "OnlyInMO2.esm"], data=self.data, source="mo2")
        ctx = ctx_for(self.t, lo, sf_mod_check=exe)
        st = records.run(ctx)
        self.assertEqual(st["status"], "skipped")
        self.assertIn("MO2", st["why"])
        self.assertTrue(any("OnlyInMO2.esm" in f.plugins for f in ctx.report.findings))
        self.assertEqual(self.calls(), [["--version"]])

    def test_no_data(self):
        exe = self.fake()
        st = records.run(ctx_for(self.t, make_lo(["A.esm"]), sf_mod_check=exe))
        self.assertIn("--data", st["why"])

    def test_heavy(self):
        exe = self.fake(check={
            "a.esm": {"stdout": "sf-mod-check 0.8.0 check: A.esm (full master, 1 masters, 9 records, 4 overrides "
                                "(2 ITM), 5 links)\nWARNING [records] 3 deleted REFR record(s), e.g. 000801:A.esm\n"
                                "WARNING [records] 1 deleted KYWD record(s), e.g. 000802:A.esm\n"
                                "RESULT: OK, 0 errors, 2 warnings (A.esm)"},
            "b.esm": {"stdout": "x\nRESULT: FAILED, 2 errors, 0 warnings (B.esm)", "exit": 1},
            "c.esm": {"stdout": "RESULT: CANNOT CHECK (bad)", "exit": 2}})
        ctx = ctx_for(self.t, self.lo, sf_mod_check=exe, depth="heavy")
        st = records.run(ctx)
        self.assertIn("check on 5 plugin(s), 1 not checked", st["heavy"])
        h = ctx.results["records"]["heavy"]
        self.assertEqual(h["a.esm"]["deleted"], {"REFR": 3, "KYWD": 1})
        self.assertEqual(h["a.esm"]["itms"], 2)
        self.assertEqual(h["b.esm"]["errors"], 2)
        texts = [f.text for f in ctx.report.findings]
        self.assertTrue(any("A.esm deletes records (1 KYWD, 3 REFR)" in t or "A.esm deletes records (3 REFR)" in t
                            for t in texts))
        self.assertTrue(any("found 2 error(s) in B.esm" in t for t in texts))
        self.assertTrue(any("couldn't check 1 plugin(s): C.esm" in t for t in texts))

    def test_through_cli(self):
        import io
        from contextlib import redirect_stdout
        from sf_load_order import cli
        root = os.path.join(self.t, "game")
        data, ptxt = fixtures.game(root, {n: {"masters": ["Starfield.esm"]} for n in ("A.esm", "B.esm", "C.esm")})
        cj = fixtures.conflicts_json(os.path.join(self.t, "cli.json"),
                                     [("Starfield.esm", True, []), ("A.esm", False, ["Starfield.esm"]),
                                      ("B.esm", False, ["Starfield.esm"]), ("C.esm", False, ["Starfield.esm"])],
                                     lost=[lost("000100:Starfield.esm", "A.esm", "B.esm", [field("Keywords")])])
        out = os.path.join(self.t, "report")
        args = cli.parser().parse_args(["--plugins-txt", ptxt, "--data", data, "--conflicts", cj, "--out", out,
                                         "--no-state"])  # never the real state folder
        with redirect_stdout(io.StringIO()):
            code, report = cli.run(args)
        self.assertEqual(code, 0)
        self.assertEqual(report.sections["records"]["status"], "done", report.sections["records"])
        self.assertEqual(report.sections["records"]["action_pairs"], 1)
        self.assertTrue(any(f.section == "records" and "patch candidate" in f.text for f in report.findings))

    @unittest.skipUnless(os.name == "nt", "a .cmd wrapper runs only on Windows (through cmd.exe)")
    def test_wrapper_refuses_cmd_metacharacters(self):
        """A .cmd runs through cmd.exe, which would read & in an argument as a second command: the run is refused
        with a plain why, and the fake program is never started for conflict."""
        exe = self.fake()
        out = os.path.join(self.t, "Rock & Roll")
        ctx = ctx_for(self.t, self.lo, sf_mod_check=exe)
        ctx.work = os.path.join(out, "work")
        st = records.run(ctx)
        self.assertEqual(st["status"], "skipped")
        self.assertIn("cmd.exe", st["why"])
        self.assertIn("&", st["why"])
        self.assertEqual(self.calls(), [["--version"]])
        self.assertTrue(any("sf-mod-check.exe itself" in (f.fix or "") for f in ctx.report.findings))
        self.assertEqual(st["why"].count("wasn't run"), 1)  # said once, not twice
        f = next(f for f in ctx.report.findings if "sf-mod-check.exe itself" in (f.fix or ""))
        self.assertEqual(f.text.count("wasn't run"), 1)

    def test_exe_beside_wrapper_gets_a_note(self):
        """When the .exe beside a wrapper is chosen, the run says which .exe it ran."""
        ctx = ctx_for(self.t, self.lo)
        prog = sfmodcheck.Program(os.path.join(self.bin, "sf-mod-check.exe"), "--sf-mod-check")
        prog.note = f"sf-mod-check.cmd was given, and sf-load-order ran {prog.path}, which sits next to it."
        records._exe_note(ctx, prog)
        records._exe_note(ctx, prog)  # once only
        notes = [f for f in ctx.report.findings if f.level == "note" and prog.path in f.text]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].section, "records")

    def test_wrapper_refusal_rules(self):
        cmd = os.path.join(self.t, "x.cmd")
        self.assertIsNone(sfmodcheck.wrapper_refusal([cmd, "conflict", r"C:\Program Files (x86)\Data"]))
        for bad in ("a&b", "a|b", "a<b", "a>b", "a^b", "%PATH%", "a!b", 'a"b'):
            with self.subTest(bad=bad):
                self.assertTrue(sfmodcheck.wrapper_refusal([cmd, bad]))
        self.assertIsNone(sfmodcheck.wrapper_refusal([os.path.join(self.t, "x.exe"), "a&b"]))  # no cmd.exe
        line = sfmodcheck.wrapper_command_line([cmd, "conflict", "C:\\a b\\"], comspec="cmd.exe")
        self.assertEqual(line, f'"cmd.exe" /d /s /c ""{cmd}" "conflict" "C:\\a b\\\\""')

    def test_wrapper_quotes_every_argument(self):
        """Arguments without spaces are quoted too, so the fake gets them unchanged through cmd.exe."""
        exe = self.fake()
        with open(os.path.join(self.t, "p(1).txt"), "w", encoding="utf-8") as f:
            f.write("*A.esm\n")
        r = sfmodcheck._run([exe, "conflict", "--plugins", os.path.join(self.t, "p(1).txt"), "--out",
                             os.path.join(self.t, "o"), "--data", "x"], 60)
        self.assertEqual(r.exit, 0, r.stderr)
        self.assertEqual(self.calls()[-1][2], os.path.join(self.t, "p(1).txt"))

    def test_unwrap_prefers_the_exe(self):
        named = os.path.join(self.bin, "tool")
        os.makedirs(named)
        real = os.path.join(named, "real-check.exe")
        open(real, "wb").close()
        w = os.path.join(named, "sfmodcheck.cmd")
        with open(w, "w", encoding="utf-8", newline="\r\n") as f:
            f.write('@echo off\n@"%~dp0real-check.exe" %*\n')
        self.assertEqual(os.path.normcase(sfmodcheck.unwrap(w)), os.path.normcase(real))  # named in it simply
        beside = os.path.join(self.bin, "beside")
        os.makedirs(beside)
        w2 = os.path.join(beside, "sf-mod-check.cmd")
        with open(w2, "w", encoding="utf-8") as f:
            f.write('@echo off\nsetlocal\nset X=1\ncall "%~dp0sf-mod-check.exe" --quiet %*\n')
        self.assertEqual(sfmodcheck.unwrap(w2), w2)  # nothing to find: stays a wrapper
        open(os.path.join(beside, "sf-mod-check.exe"), "wb").close()
        self.assertEqual(sfmodcheck.unwrap(w2), os.path.join(beside, "sf-mod-check.exe"))  # same name beside it
        prog, _ = sfmodcheck.find(argparse.Namespace(sf_mod_check=w2))
        self.assertTrue(prog.path.endswith("sf-mod-check.exe"))
        self.assertIn(os.path.join(beside, "sf-mod-check.exe"), prog.note)  # the guess is named
        # a wrapper that starts another program: the .exe beside it is not guessed
        w3 = os.path.join(beside, "sfmodcheck.cmd")
        for body in ('@echo off\ncall something-else.bat %*\n', '@echo off\npython "%~dp0smc.py" %*\n',
                     '@echo off\n"C:\\Tools\\other-tool.exe" --x %*\n'):
            with self.subTest(body=body):
                with open(w3, "w", encoding="utf-8") as f:
                    f.write(body)
                self.assertEqual(sfmodcheck.unwrap(w3), w3)
                self.assertIsNone(sfmodcheck.find(argparse.Namespace(sf_mod_check=w3))[0].note)
        # the test fake launches python with extra arguments: not a simple launch, so it stays a wrapper
        self.assertEqual(sfmodcheck.unwrap(self.fake(wrapper="cmd")), os.path.join(self.bin, "sf-mod-check.cmd"))

    def test_parse_version(self):
        self.assertEqual(sfmodcheck.parse_version("sf-mod-check 0.8.0 by X - GPL-3.0"), (0, 8, 0))
        self.assertEqual(sfmodcheck.parse_version("sfmodcheck 0.10.2+abc"), (0, 10, 2))
        self.assertIsNone(sfmodcheck.parse_version("hello"))


if __name__ == "__main__":
    unittest.main()
