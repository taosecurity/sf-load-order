"""order.py tests. Synthetic data only (tests\\fixtures.py)."""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, order  # noqa: E402
from sf_load_order.model import LoadOrder, Plugin, Report  # noqa: E402
from tests import fixtures  # noqa: E402


def make_ctx(tmp, plugins):
    args = types.SimpleNamespace(out=os.path.join(tmp, "out"), state=os.path.join(tmp, "state"), no_state=True)
    lo = LoadOrder(source="plugins.txt", data=os.path.join(tmp, "Data"), plugins=plugins)
    return cli.Context(args, lo, Report(tool_version="test"))


class OrderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = os.path.join(self.tmp, "Data")

    def tearDown(self):
        self._tmp.cleanup()

    def p(self, name, enabled=True, official=False, **spec):
        """A Plugin backed by a real fixture file (kind and masters read by plugin_header.fill)."""
        path = fixtures.plugin(os.path.join(self.data, name), **spec)
        return Plugin(name, enabled, official=official, path=path)

    def base(self):
        return [self.p("Starfield.esm", official=True)]

    def findings(self, ctx, level=None):
        return [f for f in ctx.report.findings if level is None or f.level == level]

    def test_clean_order(self):
        ctx = make_ctx(self.tmp, self.base() + [self.p("A.esm", masters=["Starfield.esm"]),
                                                self.p("B.esm", masters=["Starfield.esm", "A.esm"], kind="L")])
        st = order.run(ctx)
        self.assertEqual(self.findings(ctx), [])
        self.assertEqual(st["counts"], {"F": 2, "M": 0, "L": 1, "?": 0})
        self.assertEqual(ctx.results["order"]["effective"], ["Starfield.esm", "A.esm", "B.esm"])

    def test_missing_disabled_and_late_masters(self):
        ctx = make_ctx(self.tmp, self.base() + [
            self.p("Patch.esm", masters=["Starfield.esm", "Late.esm", "Off.esm", "Gone.esm"]),
            self.p("Late.esm"), self.p("Off.esm", enabled=False)])
        order.run(ctx)
        texts = [f.text for f in self.findings(ctx, "problem")]
        self.assertEqual(len(texts), 3, texts)
        self.assertTrue(any("loads before its master Late.esm" in t for t in texts))
        self.assertTrue(any("Off.esm, which is disabled" in t for t in texts))
        self.assertTrue(any("Gone.esm, which isn't installed" in t for t in texts))
        self.assertTrue(all(f.fix for f in self.findings(ctx)))

    def test_disabled_plugin_not_checked(self):
        ctx = make_ctx(self.tmp, self.base() + [self.p("X.esm", enabled=False, masters=["Nope.esm"])])
        order.run(ctx)
        self.assertEqual(self.findings(ctx), [])

    def test_dropped_master(self):
        drop = self.p("My-ShatteredSpace.esm")
        drop.dropped = True
        ctx = make_ctx(self.tmp, self.base() + [drop, self.p("Y.esm", masters=["My-ShatteredSpace.esm"])])
        order.run(ctx)
        f = self.findings(ctx, "problem")
        self.assertEqual(len(f), 1)
        self.assertIn("removes that name", f[0].text)

    def test_master_file_missing(self):
        gone = Plugin("Gone.esm", True)  # enabled, no file on disk
        ctx = make_ctx(self.tmp, self.base() + [gone, self.p("Z.esm", masters=["Gone.esm"])])
        order.run(ctx)
        f = self.findings(ctx, "problem")
        self.assertEqual(len(f), 1)
        self.assertIn("isn't in the Data", f[0].text)

    def test_official_not_flagged(self):
        # an official file with an odd master order is the game's business
        ctx = make_ctx(self.tmp, self.base() + [self.p("SFBGS050.esm", official=True,
                                                       masters=["Starfield.esm", "SFBGS00D.esm"]),
                                                self.p("SFBGS00D.esm", official=True)])
        order.run(ctx)
        self.assertEqual(self.findings(ctx), [])

    def test_unreadable(self):
        bad = os.path.join(self.data, "Junk.esm")
        os.makedirs(self.data, exist_ok=True)
        with open(bad, "wb") as f:
            f.write(b"not a plugin at all, just text")
        ctx = make_ctx(self.tmp, self.base() + [Plugin("Junk.esm", True, path=bad)])
        st = order.run(ctx)
        f = self.findings(ctx, "caution")
        self.assertEqual(len(f), 1)
        self.assertIn("couldn't be read", f[0].text)
        self.assertEqual(st["counts"]["?"], 1)

    def test_fill_called_when_kind_missing(self):
        ctx = make_ctx(self.tmp, self.base() + [self.p("M.esm", kind="M", masters=["Starfield.esm"])])
        self.assertIsNone(ctx.load_order.plugins[1].kind)
        st = order.run(ctx)
        self.assertEqual(ctx.load_order.plugins[1].kind, "M")
        self.assertEqual(st["counts"]["M"], 1)

    def _many(self, kind, n):
        # header already "read": kind and masters set, so no files are needed
        return [Plugin(f"P{i}.esm", True, path=f"x{i}", kind=kind, masters=[]) for i in range(n)]

    def test_slot_limits(self):
        for kind, n, level in (("F", 254, "problem"), ("F", 253, "caution"), ("F", 240, None),
                               ("F", 241, "caution"), ("M", 257, "problem"), ("M", 244, "caution"),
                               ("L", 4097, "problem"), ("L", 4096, "caution"), ("L", 3891, None)):
            with self.subTest(kind=kind, n=n):
                ctx = make_ctx(self.tmp, self._many(kind, n))
                st = order.run(ctx)
                self.assertEqual(st["counts"][kind], n)
                got = [f.level for f in ctx.report.findings]
                self.assertEqual(got, [level] if level else [])

    def test_blueprint_last_oldest_first(self):
        """The order comes from inputs.build_order (the one BlueprintShips rule); order.py reuses it."""
        from sf_load_order import inputs
        old = self.p("BlueprintShips-SFBGS050.esm", official=True)
        new = self.p("BlueprintShips-Starfield.esm", official=True)
        os.utime(old.path, (1_000_000, 1_000_000))
        os.utime(new.path, (2_000_000, 2_000_000))
        self.p("Starfield.esm", official=True)
        self.p("Ships.esm", masters=["Starfield.esm", "BlueprintShips-Starfield.esm"])
        present = inputs.listing(self.data)
        listed = [("BlueprintShips-Starfield.esm", True), ("Ships.esm", True), ("BlueprintShips-SFBGS050.esm", True)]
        plugins, _ = inputs.build_order(listed, present, lambda n: os.path.join(self.data, present[n]))
        ctx = make_ctx(self.tmp, plugins)
        order.run(ctx)
        self.assertEqual(ctx.results["order"]["effective"],
                         ["Starfield.esm", "Ships.esm", "BlueprintShips-SFBGS050.esm", "BlueprintShips-Starfield.esm"])
        # a mod mastering a BlueprintShips file isn't told to move: the game loads those last whatever the order
        self.assertEqual(self.findings(ctx), [])

    def plain(self, ctx, *names):
        """Read the headers, then clear the master flag of names (the fixture writes it on every plugin)."""
        from sf_load_order import plugin_header
        plugin_header.fill(ctx.load_order)
        for p in ctx.load_order.plugins:
            if p.name in names:
                p.master = False

    def test_plain_plugin_listed_above_its_master_plugin_is_fine(self):
        # 0.3.3 (Round 3 C2, 2b): the game loads master plugins first, so X.esp listed above its master Y.esm still
        # loads after it: no "loads before its master" problem
        ctx = make_ctx(self.tmp, self.base() + [self.p("X.esp", masters=["Starfield.esm", "Y.esm"]),
                                                self.p("Y.esm", masters=["Starfield.esm"])])
        self.plain(ctx, "X.esp")
        order.run(ctx)
        self.assertEqual(self.findings(ctx), [])
        self.assertEqual(ctx.results["order"]["effective"], ["Starfield.esm", "X.esp", "Y.esm"])  # the list's order

    def test_plain_plugins_are_still_checked_among_themselves(self):
        ctx = make_ctx(self.tmp, self.base() + [self.p("X.esp", masters=["Starfield.esm", "Y.esp"]),
                                                self.p("Y.esp", masters=["Starfield.esm"])])
        self.plain(ctx, "X.esp", "Y.esp")
        order.run(ctx)
        texts = [f.text for f in self.findings(ctx, "problem")]
        self.assertEqual(texts, ["X.esp loads before its master Y.esp."])

    def test_master_plugin_needing_a_plain_plugin_is_fine_in_either_order(self):
        # 0.3.3 code-review round 2: the game loads a plain plugin that a master plugin needs just before that master
        # plugin (libloadorder's hoist_masters), so neither list order is a problem
        for first, second in (("Y.esp", "X.esm"), ("X.esm", "Y.esp")):
            ps = {"Y.esp": self.p("Y.esp", masters=["Starfield.esm"]),
                  "X.esm": self.p("X.esm", masters=["Starfield.esm", "Y.esp"])}
            ctx = make_ctx(self.tmp, self.base() + [ps[first], ps[second]])
            self.plain(ctx, "Y.esp")
            order.run(ctx)
            self.assertEqual(self.findings(ctx, "problem"), [], first)

    def test_game_order_hoists_a_plain_master_of_a_master_plugin(self):
        ps = [Plugin("Starfield.esm", True, official=True), Plugin("X.esm", True, masters=["Y.esp"]),
              Plugin("Z.esm", True), Plugin("P.esp", True), Plugin("Y.esp", True)]
        self.assertEqual([p.name for p in order.game_order(ps)],
                         ["Starfield.esm", "Y.esp", "X.esm", "Z.esm", "P.esp"])
        # its own plain master isn't hoisted with it (one level, as libloadorder does)
        ps = [Plugin("X.esm", True, masters=["Y.esp"]), Plugin("W.esp", True), Plugin("Y.esp", True, masters=["W.esp"])]
        self.assertEqual([p.name for p in order.game_order(ps)], ["Y.esp", "X.esm", "W.esp"])

    def test_game_order_keeps_blueprints_last(self):
        ps = [Plugin("Starfield.esm", True, official=True), Plugin("B.esp", True), Plugin("A.esm", True),
              Plugin("BlueprintShips-Starfield.esm", True, official=True)]
        self.assertEqual([p.name for p in order.game_order(ps)],
                         ["Starfield.esm", "A.esm", "B.esp", "BlueprintShips-Starfield.esm"])

    def test_effective_order_is_the_load_order(self):
        """No second ordering rule here: the plugins that load, as lo.plugins has them."""
        a, b = self.p("A.esm"), self.p("B.esm", enabled=False)
        ctx = make_ctx(self.tmp, self.base() + [a, b, self.p("C.esm")])
        self.assertEqual([p.name for p in order.effective_order(ctx.load_order)], ["Starfield.esm", "A.esm", "C.esm"])


if __name__ == "__main__":
    unittest.main()
