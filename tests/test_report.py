"""Report tests: report.txt, report.html and report.json. Synthetic data only."""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sf_load_order  # noqa: E402
from sf_load_order import cli, model, report  # noqa: E402
from tests import fixtures  # noqa: E402

EVIL = "<script>alert(1)</script>.esm"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _ctx(out):
    lo = model.LoadOrder(source="plugins.txt", files={"plugins_txt": "C:\\fake\\Plugins.txt"}, data="C:\\fake\\Data",
                         plugins=[model.Plugin("Starfield.esm", True, official=True, kind="F"),
                                  model.Plugin(EVIL, True, kind="L", masters=["Starfield.esm"]),
                                  model.Plugin("B.esm", True, kind="F"),
                                  model.Plugin("Off.esm", False)])
    rep = model.Report(tool_version="9.9.9", load_order=lo)
    rep.add(model.Finding("note", "order", "Note without a fix."))
    rep.add(model.Finding("note", "archives", "Note with a fix.", fix="Do the note thing.",
                          data={"pairs": [{"loser": EVIL, "winner": "B.esm", "advice": "same", "files": 2}]}))
    rep.add(model.Finding("caution", "loose", "Caution without a fix."))
    rep.add(model.Finding("caution", "clashes", f"LOOT says: {EVIL} <b>bold</b> & more.", fix="Read LOOT's note."))
    rep.add(model.Finding("problem", "records", "Problem without a fix."))
    rep.add(model.Finding("problem", "order", f"{EVIL} needs Missing.esm, which isn't installed.",
                          fix="Install Missing.esm.", plugins=[EVIL]))
    for i in range(8):
        rep.add(model.Finding("note", "patches", f"Extra note {i}."))
    rep.sections = {"inputs": {"status": "done"},
                    "order": {"status": "done", "counts": {"F": 2, "M": 0, "L": 1}, "limits": {"F": 253, "M": 256,
                                                                                              "L": 4096}},
                    "patches": {"status": "done", "state": "off (--no-state)"},
                    "clashes": {"status": "done", "parts": {"loot": {"status": "skipped", "why": "no masterlist"}}},
                    "archives": {"status": "done", "archives": 3, "shared_paths": 2, "order_decides": 0},
                    "loose": {"status": "skipped", "why": "a Plugins.txt alone doesn't list mods"},
                    "records": {"status": "failed", "why": "KeyError: 'lost'"},
                    "recommend": {"status": "done"}}
    results = {
        "order": {"effective": ["Starfield.esm", EVIL, "B.esm"], "kinds": {EVIL: "L", "B.esm": "F"}},
        "archives": {"pairs": [{"loser": EVIL, "winner": "B.esm", "advice": "same", "files": 2}],
                     "paths": {"a": [EVIL, "B.esm"]}, "loaded": [(EVIL, "x"), ("B.esm", "y")], "plugins": {},
                     "order_decides": [], "misnamed": [], "orphans": [], "unreadable": [], "lz4_missing": [],
                     "data_checked": True},
        "recommend": {"order": ["Starfield.esm", "B.esm", EVIL],
                      "moves": [{"plugin": EVIL, "after": "B.esm", "reason": "so its changes win over B.esm"}],
                      "score_current": 30, "score_recommended": 3, "estimate": True, "same_as_current": False,
                      "rated": True, "label": "your current order (LOOT's if you just sorted)", "flipped": [],
                      "rejected": [], "picks": {"applied": [], "not_applied": []}, "archive_flips": [],
                      "remaining": [{"fk": "000100:Starfield.esm", "edid": "Loc<1>", "type": "LCTN", "loser": EVIL,
                                     "winner": "B.esm", "weight": 3, "fields": [report.recommend.QUEST],
                                     "official": False}],
                      "vs_loot": {"file": "C:\\fake\\loot.txt", "moved": [EVIL], "moved_count": 1, "score": 12,
                                  "missing": [], "extra": []},
                      "files": {"plugins_txt": os.path.join(out, "plugins.txt"),
                                "how_to": os.path.join(out, "HOW-TO-APPLY.txt")}},
    }
    rep.outputs.update(results["recommend"]["files"])
    rep.recommended = results["recommend"]["order"]
    args = types.SimpleNamespace(out=out, depth="medium")
    return types.SimpleNamespace(args=args, load_order=lo, report=rep, results=results, work=os.path.join(out, "w"))


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.ctx = _ctx(self.t)
        self.paths = report.write(self.ctx)
        self.txt = _read(self.paths["txt"])
        self.html = _read(self.paths["html"])
        self.json = json.loads(_read(self.paths["json"]))

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def test_all_three_written(self):
        self.assertEqual(set(self.paths), {"txt", "html", "json"})

    def test_top_order(self):
        top = report.top_findings(self.ctx.report)[:6]
        self.assertEqual([f.text.split(" ")[0] for f in top][:2], [EVIL.split(" ")[0], "Problem"])
        self.assertEqual([(f.level, bool(f.fix)) for f in top],
                         [("problem", True), ("problem", False), ("caution", True), ("caution", False),
                          ("note", True), ("note", False)])
        top_txt = self.txt.split("Top 10")[1].split("1. What was read")[0]
        i_fix, i_nofix = top_txt.index("Note with a fix."), top_txt.index("Note without a fix.")
        self.assertLess(top_txt.index("Problem without a fix."), top_txt.index("Caution without a fix."))
        self.assertLess(i_fix, i_nofix)
        self.assertIn("more finding", top_txt)  # 14 findings, 10 shown

    def test_html_escaping(self):
        self.assertNotIn("<script", self.html)
        self.assertNotIn("<b>bold</b>", self.html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;.esm", self.html)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; &amp; more", self.html)
        self.assertIn("Loc&lt;1&gt;", self.html)

    def test_no_external_urls(self):
        allowed = {sf_load_order.URL, sf_load_order.URL + "/issues", "https://github.com/taosecurity/sf-mod-check"}
        self.assertEqual(set(report.LINKS), allowed)
        for url in re.findall(r"https?://[^\s\"'<>)]+", self.html):
            self.assertIn(url.rstrip("."), allowed)
        attrs = re.findall(r"""(?:href|src)\s*=\s*["']([^"']*)""", self.html, re.I)
        self.assertEqual(set(attrs), allowed)
        self.assertNotRegex(self.html, r"@import|url\(|<link|<script|\ssrc\s*=")
        with self.assertRaises(ValueError):
            report._link("https://example.com")

    def test_byline_and_footer(self):
        by = (f"sf-load-order {sf_load_order.__version__} by {sf_load_order.AUTHOR} - {sf_load_order.LICENSE} - "
              f"{sf_load_order.URL}")
        self.assertEqual(sf_load_order.BYLINE, by)
        self.assertEqual(self.txt.splitlines()[2], by)  # under the title, in the header
        head, foot = self.html.split("</h1>", 1)[1].split("</p>", 1)[0], self.html.split("<footer>", 1)[1]
        self.assertIn(f'<a href="{sf_load_order.URL}">{sf_load_order.URL}</a>', head)
        self.assertIn(f"sf-load-order {sf_load_order.__version__} by {sf_load_order.AUTHOR}", head)
        self.assertNotIn("9.9.9", head)  # the package's version, not the report's
        for part in ("License: GPL-3.0", f'href="{sf_load_order.URL}"', f'href="{sf_load_order.URL}/issues"',
                     "LOOT's Starfield masterlist (CC0 1.0)", 'href="https://github.com/taosecurity/sf-mod-check"',
                     "It only reads your game and mod manager files and never uploads anything."):
            self.assertIn(part, foot)

    def test_skipped_and_failed_shown(self):
        self.assertIn("Skipped. Why: A Plugins.txt alone doesn't list mods.", self.txt)
        self.assertIn("This check failed, and the other checks still ran. Why: KeyError: 'lost'.", self.txt)
        self.assertIn("These checks failed (see their sections): Record conflicts (sf-mod-check).", self.txt)
        self.assertIn('class="pill failed"', self.html)
        self.assertIn('class="pill skipped"', self.html)
        self.assertEqual(self.json["sections"]["records"]["status"], "failed")

    def test_txt_layout(self):
        for s in cli.MAIN_SECTIONS:
            self.assertIn(report.TITLES[s], self.txt)
        self.assertLess(self.txt.index(f"{len(cli.MAIN_SECTIONS)}. Rating"), self.txt.index("Suggested order"))
        self.assertLess(self.txt.index("Suggested order"), self.txt.index("Files written"))
        self.assertIn("lower is better", self.txt)
        self.assertIn("The recommended score is an estimate", self.txt)
        self.assertIn("1 plugin placed differently, and LOOT's order has a loss score of 12.", self.txt)
        self.assertIn("HOW-TO-APPLY.txt", self.txt)
        self.assertIn(f"Load {EVIL} after B.esm. Why: So its changes win over B.esm.", self.txt)
        self.assertRegex(self.txt, r"Date:\s+\d{4}-\d\d-\d\d \d\d:\d\d")
        long = [ln for ln in self.txt.splitlines() if len(ln) > 120]
        self.assertEqual(long, [])

    def test_json(self):
        j = self.json
        self.assertEqual(j["schema"], model.SCHEMA_VERSION)
        self.assertEqual(j["counts"], {"problem": 2, "caution": 2, "note": 10})
        self.assertEqual(set(j["outputs"]) >= {"txt", "html", "json", "plugins_txt", "how_to"}, True)
        self.assertEqual(j["results"]["recommend"]["remaining"], 1)
        self.assertEqual(j["results"]["archives"]["pairs_by_advice"], {"same": 1})
        self.assertEqual(j["results"]["order"]["loading"], 3)
        self.assertIn("records", j["left_out"])
        self.assertEqual(set(j["sections"]), set(cli.MAIN_SECTIONS))

    def test_same_as_current(self):
        self.ctx.results["recommend"].update(same_as_current=True, moves=[], score_recommended=30, estimate=False)
        self.assertIn("same as your current order", report.render_text(self.ctx, self.paths))

    def test_archive_flip_pointer_names_the_real_section(self):
        self.ctx.results["recommend"]["archive_flips"] = [{"path": "textures\\a.dds", "before": "B.esm",
                                                           "after": EVIL}]
        t = " ".join(report.render_text(self.ctx, self.paths).split())  # lines are wrapped at 118 columns
        self.assertNotIn("the Archives findings", t)
        self.assertIn('"Archived files whose winner changes" below', t)
        self.assertIn(f"under {report.TITLES['recommend']}", t)

    def test_patches_summary_counts_built_for_old(self):
        self.ctx.report.sections["patches"] = {"status": "done", "kind_changes": 0, "built_for_old": 2,
                                               "state": "x.json", "previous": True}
        t = report.render_text(self.ctx, self.paths)
        self.assertIn("0 masters changed kind since the last run.", t)
        self.assertIn("2 plugins are still built for an older kind of a master", t)

    def test_recommend_missing(self):
        del self.ctx.results["recommend"]
        self.ctx.report.sections["recommend"] = {"status": "skipped", "why": "a made-up reason"}
        t = report.render_text(self.ctx, self.paths)
        self.assertIn("No order was worked out. Skipped. Why: A made-up reason.", t)


class OnlyWrittenListed(unittest.TestCase):
    """When one of the three report files can't be written, the other two (and outputs) don't list it."""

    def setUp(self):
        self.t = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def check_without(self, missing):
        os.makedirs(os.path.join(self.t, {"json": report.JSON, "txt": report.TXT, "html": report.HTML}[missing]))
        ctx = _ctx(self.t)
        paths = report.write(ctx)
        self.assertEqual(set(paths), {"json", "txt", "html"} - {missing})
        name = {"json": report.JSON, "txt": report.TXT, "html": report.HTML}[missing]
        for role, p in paths.items():
            body = _read(p)
            if role == "json":
                self.assertNotIn(missing, json.loads(body)["outputs"])
                self.assertEqual(set(json.loads(body)["outputs"]) & {"json", "txt", "html"}, set(paths))
            else:
                files = body.split("Files written")[-1]
                self.assertNotIn(name, files)
                for other in paths.values():
                    self.assertIn(os.path.basename(other), files)

    def test_json_missing(self):
        self.check_without("json")

    def test_txt_missing(self):
        self.check_without("txt")

    def test_html_missing(self):
        self.check_without("html")


class EndToEnd(unittest.TestCase):
    def test_light_run_writes_all_three(self):
        with tempfile.TemporaryDirectory() as t:
            data, ptxt = fixtures.game(os.path.join(t, "Game"), {
                "A.esm": {"masters": ["Starfield.esm"]},
                "B.esm": {"masters": ["Starfield.esm", "Missing.esm"], "kind": "L"}})
            fixtures.ba2(os.path.join(data, "A - Main.ba2"), {"textures\\x.dds": b"a"})
            fixtures.ba2(os.path.join(data, "B - Main.ba2"), {"textures\\x.dds": b"b"})
            out = os.path.join(t, "report")
            args = cli.parser().parse_args(["--plugins-txt", ptxt, "--data", data, "--out", out, "--depth", "light",
                                            "--no-state"])
            with redirect_stdout(io.StringIO()):
                code, rep = cli.run(args)
            self.assertEqual(code, 0)
            for role, name in (("txt", "report.txt"), ("html", "report.html"), ("json", "report.json")):
                self.assertEqual(rep.outputs[role], os.path.join(out, name))
                self.assertTrue(os.path.isfile(rep.outputs[role]))
            j = json.loads(_read(rep.outputs["json"]))
            self.assertEqual(j["sections"]["records"], {"status": "skipped", "why": "--depth light"})
            self.assertGreaterEqual(j["counts"]["problem"], 1)
            txt = _read(rep.outputs["txt"])
            self.assertIn("B.esm needs Missing.esm", txt)
            self.assertIn("Skipped. Why: --depth light.", txt)
            self.assertIn("Suggested order", txt)
            html_ = _read(rep.outputs["html"])
            self.assertTrue(html_.startswith("<!doctype html>"))
            self.assertIn("prefers-color-scheme: dark", html_)
            for url in re.findall(r"https?://[^\s\"'<>)]+", html_):
                self.assertIn(url.rstrip("."), report.LINKS)


class LooseWording(unittest.TestCase):
    """One mod or one pair takes 'ships', more take 'ship' (0.1.1)."""

    def lines(self, mods, pairs):
        ctx = types.SimpleNamespace(
            report=types.SimpleNamespace(sections={"loose": {"status": "done", "mods": mods, "pairs": pairs}}),
            results={}, load_order=None)
        return " ".join(report._summary_lines(ctx, "loose"))

    def test_singular(self):
        text = self.lines(1, 1)
        self.assertIn("1 mod ships loose files", text)
        self.assertIn("1 pair of mods ships the same paths", text)

    def test_plural(self):
        text = self.lines(2, 0)
        self.assertIn("2 mods ship loose files", text)
        self.assertIn("0 pairs of mods ship the same paths", text)


class ClashesWording(unittest.TestCase):
    """0.2.3: a failed --clashes file "couldn't be used", not "was failed"."""

    def lines(self, status):
        parts = {"clashes": {"status": status, "why": "bad json"}}
        ctx = types.SimpleNamespace(
            report=types.SimpleNamespace(sections={"clashes": {"status": "done", "parts": parts}}),
            results={}, load_order=None)
        return " ".join(report._summary_lines(ctx, "clashes"))

    def test_failed_and_skipped(self):
        self.assertIn("Your --clashes file couldn't be used: Bad json.", self.lines("failed"))
        self.assertNotIn("was failed", self.lines("failed"))
        self.assertIn("Your --clashes file was skipped: Bad json.", self.lines("skipped"))


if __name__ == "__main__":
    unittest.main()
