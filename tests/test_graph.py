"""Tests for graph.py, the ordering graph `sort` builds on (DESIGN-0.3.0.md 1.4, 1.6, 1.7). Synthetic names only."""
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import graph, recommend  # noqa: E402
from sf_load_order.graph import Edge, Graph  # noqa: E402


def rule(a, b, rank=4, source="rules", entry=None):
    return Edge(a, b, source, rank, f"rule: {b} loads after {a}", "rules.json", entry)


class StableOrder(unittest.TestCase):
    def test_no_edges_keeps_the_current_order(self):
        cur = ["C.esm", "A.esm", "B.esm", "D.esp"]
        self.assertEqual(Graph(cur).order(), cur)

    def test_one_forced_move_moves_one_plugin(self):
        cur = ["A.esm", "B.esm", "C.esm", "D.esm", "E.esm"]
        g = Graph(cur)
        self.assertEqual(g.add(rule("D.esm", "B.esm")), "added")  # B must load after D
        order = g.order()
        self.assertEqual(order, ["A.esm", "C.esm", "D.esm", "B.esm", "E.esm"])
        self.assertEqual(recommend.moved(order, cur), ["B.esm"])

    def test_names_are_case_insensitive_and_keep_their_case(self):
        g = Graph(["A.esm", "B.esm"])
        self.assertEqual(g.add(rule("b.ESM", "a.esm")), "added")
        self.assertEqual(g.order(), ["B.esm", "A.esm"])
        self.assertEqual(g.add(rule("B.esm", "A.esm")), "exists")

    def test_an_edge_naming_a_plugin_not_in_the_graph_is_inactive(self):
        g = Graph(["A.esm", "B.esm"])
        self.assertEqual(g.add(rule("A.esm", "Missing.esm")), "inactive")
        self.assertEqual(len(g.inactive), 1)
        self.assertEqual(g.order(), ["A.esm", "B.esm"])

    def test_the_same_result_as_recommend_topo(self):
        rnd = random.Random(7)
        cur = [f"P{i:02}.esm" for i in range(40)]
        for _ in range(20):
            edges = []
            g = Graph(cur)
            for _ in range(60):
                a, b = rnd.sample(cur, 2)
                if g.add(rule(a, b)) == "added":
                    edges.append((a, b))
            self.assertEqual(g.order(), recommend.topo(cur, edges))


class VirtualNodes(unittest.TestCase):
    def test_type_rule_puts_masters_first_and_never_shows_the_virtual_node(self):
        cur = ["Plain.esp", "A.esm", "Other.esp", "B.esm"]
        g = Graph(cur)
        g.add_type_rule({"Plain.esp": False, "A.esm": True, "Other.esp": False, "B.esm": True})
        order = g.order()
        self.assertEqual(order, ["A.esm", "B.esm", "Plain.esp", "Other.esp"])
        self.assertNotIn(graph.MASTERS_END, order)
        self.assertFalse(any(graph.is_virtual(n.lower()) for n in order))

    def test_no_virtual_node_when_every_plugin_is_a_master(self):
        g = Graph(["A.esm", "B.esm"])
        g.add_type_rule({"A.esm": True, "B.esm": True})
        self.assertFalse(g.has(graph.MASTERS_END))

    def test_master_edge_wins_over_the_type_rule(self):
        # M.esm (master) lists P.esp (not a master) as its master: both rank 1 rules can't hold
        g = Graph(["M.esm", "P.esp"])
        g.add_masters({"M.esm": ["P.esp"]})
        g.add_type_rule({"M.esm": True, "P.esp": False})
        self.assertEqual(g.order(), ["P.esp", "M.esm"])
        self.assertEqual([r["edge"].source for r in g.rejected], ["type"])
        self.assertIn("masters", [e.source for e in g.rejected[0]["path"]])

    def test_groups(self):
        cur = ["Late1.esm", "Early.esm", "Late2.esm", "None.esm"]
        g = Graph(cur)
        g.add_group_after("late", "early", 8, "masterlist", "late loads after early")
        for p, grp in (("Late1.esm", "late"), ("Late2.esm", "late"), ("Early.esm", "early")):
            self.assertEqual(g.add_member(p, grp, 8, "masterlist", f"{p} is in {grp}"), "added")
        order = g.order()
        self.assertEqual(order, ["Early.esm", "Late1.esm", "Late2.esm", "None.esm"])
        self.assertFalse(any(n.startswith("<") for n in order))

    def test_a_rejected_membership_takes_both_edges_out(self):
        g = Graph(["A.esm", "B.esm"])
        g.add_group_after("late", "early", 8, "masterlist", "late loads after early")
        g.add_member("A.esm", "early", 8, "masterlist", "A in early")
        g.add(rule("B.esm", "A.esm", rank=4))  # the player's rule: A after B
        # B in late would need A (early) before B: closes a cycle with the player's rule
        self.assertEqual(g.add_member("B.esm", "late", 8, "masterlist", "B in late"), "rejected")
        b = "b.esm"
        self.assertNotIn((graph.group_start("late").lower(), b), g.edges)
        self.assertNotIn((b, graph.group_end("late").lower()), g.edges)
        self.assertEqual(g.order(), ["B.esm", "A.esm"])


class Contradictions(unittest.TestCase):
    def test_rank_order_decides_and_the_path_is_kept(self):
        g = Graph(["A.esm", "B.esm", "C.esm"])
        g.add_masters({"B.esm": ["A.esm"], "C.esm": ["B.esm"]})
        low = rule("C.esm", "A.esm", rank=8, source="masterlist", entry=3)  # A after C: against two master edges
        self.assertEqual(g.add(low), "rejected")
        rej = g.rejected[0]
        self.assertIs(rej["edge"], low)
        # the blocking path runs from the edge's `after` (A) to its `before` (C)
        self.assertEqual([(e.before, e.after) for e in rej["path"]], [("A.esm", "B.esm"), ("B.esm", "C.esm")])
        self.assertEqual(graph.describe(rej["path"]), "B.esm has A.esm as a master; C.esm has B.esm as a master")
        self.assertEqual(g.order(), ["A.esm", "B.esm", "C.esm"])

    def test_a_higher_source_added_first_wins(self):
        g = Graph(["A.esm", "B.esm"])
        mine = rule("B.esm", "A.esm", rank=4)
        loot = rule("A.esm", "B.esm", rank=8, source="masterlist")
        self.assertEqual(g.add(mine), "added")
        self.assertEqual(g.add(loot), "rejected")
        self.assertEqual(g.rejected[0]["path"], [mine])
        self.assertEqual(g.order(), ["B.esm", "A.esm"])

    def test_a_master_loop_gives_no_order(self):
        g = Graph(["A.esm", "B.esm"])
        g.add_masters({"A.esm": ["B.esm"], "B.esm": ["A.esm"]})
        self.assertEqual(len(g.loops), 1)
        self.assertEqual({(e.before, e.after) for e in g.loops[0]}, {("A.esm", "B.esm"), ("B.esm", "A.esm")})
        self.assertIsNone(g.order())

    def test_long_paths_are_shortened(self):
        names = [f"P{i}.esm" for i in range(8)]
        g = Graph(names)
        for a, b in zip(names, names[1:]):
            g.add(rule(a, b))
        self.assertEqual(g.add(rule("P7.esm", "P0.esm", rank=8)), "rejected")
        text = graph.describe(g.rejected[0]["path"])
        self.assertIn("and 3 more steps", text)
        self.assertEqual(text.count(";"), 4)

    def test_reason_lists_the_edges_into_a_plugin(self):
        g = Graph(["A.esm", "B.esm", "C.esm"])
        g.add_masters({"C.esm": ["A.esm"]})
        g.add(rule("B.esm", "C.esm"))
        self.assertEqual([e.source for e in g.reason("C.esm")], ["masters", "rules"])


class TieBreak(unittest.TestCase):
    def test_name_gives_the_same_order_from_two_shuffled_inputs(self):
        names = [f"Mod{c}.esm" for c in "QWERTYUIOPAS"]
        a, b = list(names), list(names)
        random.Random(1).shuffle(a)
        random.Random(2).shuffle(b)
        self.assertNotEqual(a, b)
        out = []
        for cur in (a, b):
            g = Graph(cur, tie_break="name")
            g.add(rule("ModY.esm", "ModA.esm"))
            out.append(g.order())
        self.assertEqual(out[0], out[1])
        self.assertLess(out[0].index("ModY.esm"), out[0].index("ModA.esm"))

    def test_bad_tie_break(self):
        with self.assertRaises(ValueError):
            Graph([], tie_break="random")


class Timing(unittest.TestCase):
    def test_1000_plugins_20000_edges(self):
        rnd = random.Random(42)
        cur = [f"Plugin{i:04}.esm" for i in range(1000)]
        t = time.perf_counter()
        g = Graph(cur)
        masters = {p: [cur[j] for j in rnd.sample(range(i), min(i, 3))] for i, p in enumerate(cur)}
        g.add_masters(masters)
        g.add_type_rule({p: i % 5 != 0 for i, p in enumerate(cur)})
        statuses = {}
        while len(g.edges) < 20000:
            a, b = rnd.sample(cur, 2)
            st = g.add(rule(a, b, rank=rnd.randint(4, 8)))
            statuses[st] = statuses.get(st, 0) + 1
        order = g.order()
        took = time.perf_counter() - t
        self.assertEqual(len(order), 1000)
        pos = {n.lower(): i for i, n in enumerate(order)}
        for (a, b) in g.edges:
            if not (graph.is_virtual(a) or graph.is_virtual(b)):
                self.assertLess(pos[a], pos[b])
        self.assertLess(took, 30, f"built and sorted in {took:.1f} s ({statuses})")


if __name__ == "__main__":
    unittest.main()
