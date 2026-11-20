"""patches.py tests. Synthetic data only (tests\\fixtures.py)."""
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, patches  # noqa: E402
from sf_load_order.model import LoadOrder, Plugin, Report  # noqa: E402
from tests import fixtures  # noqa: E402


def old_rule_flags(path, lists):
    """The private tracker's first rule (before the fix), kept here to prove the false alarm: an index past the end of
    a master list was flagged, although the engine reads it as the plugin's own record."""
    n = 0
    for fid in patches.record_ids(path):
        k, idx, _ = patches.decode(fid)
        if idx > len(lists[k]):
            n += 1
    return n


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = os.path.join(self.tmp, "Data")
        self.state = os.path.join(self.tmp, "state")
        self.specs = {}  # name -> (official, enabled)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, official=False, enabled=True, **spec):
        fixtures.plugin(os.path.join(self.data, name), **spec)
        self.specs[name] = (official, enabled)

    def ctx(self, names, no_state=True, profile=None):
        """A fresh Context over the given plugins, in order (kinds and masters read from the files)."""
        plugins = []
        for n in names:
            official, enabled = self.specs.get(n, (False, True))
            path = os.path.join(self.data, n)
            plugins.append(Plugin(n, enabled, official=official, path=path if os.path.exists(path) else None))
        args = types.SimpleNamespace(out=os.path.join(self.tmp, "out"), state=self.state, no_state=no_state)
        lo = LoadOrder(source="plugins.txt", profile=profile, files={"plugins": os.path.join(self.tmp, "Plugins.txt")},
                       data=self.data, plugins=plugins)
        return cli.Context(args, lo, Report(tool_version="test"))

    def levels(self, ctx):
        return [f.level for f in ctx.report.findings]


class FormIdScan(Base):
    def setUp(self):
        super().setUp()
        self.write("Starfield.esm", official=True, records=[("WEAP", 0x00000800, "BaseThing")])

    def test_past_list_own_records_are_not_a_problem(self):
        """The 30,527 false alarm: a full plugin with one master that saves its own records at index 02 (one past
        the slot its own records should use). The engine reads any index at or past the master count as the plugin
        itself, so those are its own records; the old rule flagged every one of them."""
        recs = [("WEAP", 0x00000800, "OverrideBase")] + [("MISC", 0x02000800 + i, f"Own{i}") for i in range(3)]
        self.write("Cores.esm", masters=["Starfield.esm"], records=recs)
        ctx = self.ctx(["Starfield.esm", "Cores.esm"])
        lists = patches.master_lists(["Starfield.esm"], lambda m: "F")
        self.assertEqual(old_rule_flags(os.path.join(self.data, "Cores.esm"), lists), 3)  # the old false alarm
        st = patches.run(ctx)
        self.assertEqual(self.levels(ctx), ["note"])  # no problem; a note that the numbering is unusual
        self.assertEqual(ctx.report.findings[0].data["past_list"], 3)
        self.assertEqual(st["flagged"], 0)

    def test_overrides_used_as_own_records(self):
        """A record at a past-the-list index that another plugin overrides is found as the master's own record."""
        self.write("Cores.esm", masters=["Starfield.esm"], records=[("MISC", 0x02000800, "Own")])
        self.write("CoresPatch.esm", masters=["Starfield.esm", "Cores.esm"], records=[("MISC", 0x01000800, "Edit")])
        ctx = self.ctx(["Starfield.esm", "Cores.esm", "CoresPatch.esm"])
        patches.run(ctx)
        self.assertEqual(self.levels(ctx), ["note"])  # only Cores.esm's note; the patch's override is fine

    def _shift_setup(self, a_kind):
        if a_kind == "F":
            self.write("A.esm", masters=["Starfield.esm"], records=[("MISC", 0x01000800, "AThing")])
        else:  # A.esm was re-released as a light plugin: own records in the FE style
            self.write("A.esm", masters=["Starfield.esm"], kind="L", records=[("MISC", 0xFE000800, "AThing")])
        self.write("B.esm", masters=["Starfield.esm"], records=[("MISC", 0x01000900, "BThing")])
        # built while A.esm was full: A at index 01, B at 02, own records at 03
        self.write("Patch.esm", masters=["Starfield.esm", "A.esm", "B.esm"],
                   records=[("MISC", 0x01000800, "EditA"), ("MISC", 0x02000900, "EditB"),
                            ("MISC", 0x03000A00, "Own")])
        return self.ctx(["Starfield.esm", "A.esm", "B.esm", "Patch.esm"])

    def test_built_for_matching_masters_is_clean(self):
        ctx = self._shift_setup("F")
        patches.run(ctx)
        self.assertEqual(ctx.report.findings, [])

    def test_master_changed_kind_shifts_ids(self):
        ctx = self._shift_setup("L")
        st = patches.run(ctx)
        probs = [f for f in ctx.report.findings if f.level == "problem"]
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].plugins, ["Patch.esm"])
        self.assertIn("overrides 0x800 in B.esm", probs[0].text)
        self.assertEqual(st["flagged"], 1)

    def test_own_id_past_light_limit(self):
        self.write("Lite.esm", masters=["Starfield.esm"], kind="L",
                   records=[("MISC", 0xFE000ABC, "Fine"), ("MISC", 0x01001234, "TooBig")])
        ctx = self.ctx(["Starfield.esm", "Lite.esm"])
        patches.run(ctx)
        self.assertEqual(self.levels(ctx), ["problem"])
        self.assertIn("own ID 0x1234 is past the limit of a light plugin", ctx.report.findings[0].text)

    def test_medium_own_records_in_full_style(self):
        """A medium plugin with full, light and medium masters numbering its own records in the full style (01..)
        and in the FD style: both are its own records."""
        self.write("Lmaster.esm", masters=["Starfield.esm"], kind="L", records=[("MISC", 0xFE000801, "L1")])
        self.write("Mmaster.esm", masters=["Starfield.esm"], kind="M", records=[("MISC", 0xFD000801, "M1")])
        self.write("Med.esm", masters=["Starfield.esm", "Lmaster.esm", "Mmaster.esm"], kind="M",
                   records=[("QUST", 0x0100084F, "Own1"), ("MISC", 0xFD010010, "Own2"),
                            ("MISC", 0xFE000801, "EditL"), ("MISC", 0xFD000801, "EditM")])
        ctx = self.ctx(["Starfield.esm", "Lmaster.esm", "Mmaster.esm", "Med.esm"])
        patches.run(ctx)
        self.assertEqual(ctx.report.findings, [])

    def test_missing_master_not_judged(self):
        self.write("P.esm", masters=["Starfield.esm", "Nowhere.esm"], records=[("MISC", 0x05000800, "X")])
        ctx = self.ctx(["Starfield.esm", "P.esm"])
        st = patches.run(ctx)
        self.assertEqual(ctx.report.findings, [])
        self.assertEqual(st["scanned"], 0)

    def test_official_master_not_looked_up(self):
        self.write("P.esm", masters=["Starfield.esm"], records=[("MISC", 0x00999999, "EditsMissingBase")])
        ctx = self.ctx(["Starfield.esm", "P.esm"])
        patches.run(ctx)
        self.assertEqual(ctx.report.findings, [])

    def test_decode(self):
        self.assertEqual(patches.decode(0xFE00A123), ("L", 0x00A, 0x123))
        self.assertEqual(patches.decode(0xFD02ABCD), ("M", 0x02, 0xABCD))
        self.assertEqual(patches.decode(0x03012345), ("F", 0x03, 0x012345))


class KindSnapshot(Base):
    def setUp(self):
        super().setUp()
        self.write("Starfield.esm", official=True)
        self.write("A.esm", masters=["Starfield.esm"])
        self.write("P.esm", masters=["Starfield.esm", "A.esm"])
        self.names = ["Starfield.esm", "A.esm", "P.esm"]

    def run_once(self, **kw):
        ctx = self.ctx(self.names, no_state=False, **kw)
        return ctx, patches.run(ctx)

    def test_first_run_writes_snapshot(self):
        ctx, st = self.run_once()
        self.assertEqual(ctx.report.findings, [])
        self.assertFalse(st["previous"])
        self.assertTrue(os.path.isfile(st["state"]))
        self.assertEqual(os.path.dirname(st["state"]), self.state)
        with open(st["state"], encoding="utf-8") as f:
            snap = json.load(f)
        self.assertEqual(snap["plugins"]["A.esm"]["kind"], "F")
        self.assertEqual(os.listdir(self.state), [os.path.basename(st["state"])])  # no temp files left

    def test_unchanged_patch_is_problem(self):
        self.run_once()
        self.write("A.esm", masters=["Starfield.esm"], kind="L")  # the master is re-released as light
        ctx, st = self.run_once()
        self.assertEqual(self.levels(ctx), ["problem"])
        self.assertIn("built for the full version of its master A.esm, which is now light",
                      ctx.report.findings[0].text)
        self.assertEqual(st["kind_changes"], 1)
        self.assertEqual(st["built_for_old"], 1)
        for _ in range(2):  # still broken: reported on every run while P.esm's file is unchanged
            ctx, st = self.run_once()
            self.assertEqual(self.levels(ctx), ["problem"])
            self.assertEqual(st["kind_changes"], 0)
            self.assertEqual(st["built_for_old"], 1)  # the summary counts the repeating problem too
        with open(st["state"], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["plugins"]["P.esm"]["built_for"], {"starfield.esm": "F", "a.esm": "F"})

    def test_updated_patch_after_problem_is_caution_once(self):
        self.run_once()
        self.write("A.esm", masters=["Starfield.esm"], kind="L")
        self.assertEqual(self.levels(self.run_once()[0]), ["problem"])
        self.assertEqual(self.levels(self.run_once()[0]), ["problem"])
        self.write("P.esm", masters=["Starfield.esm", "A.esm"], records=[("MISC", 0x01000800, "New")])  # updated
        self.assertEqual(self.levels(self.run_once()[0]), ["caution"])  # judged once against the old copy's masters
        self.assertEqual(self.run_once()[0].report.findings, [])        # re-baselined to the light A.esm

    def test_master_back_to_old_kind_clears(self):
        self.run_once()
        self.write("A.esm", masters=["Starfield.esm"], kind="L")
        self.assertEqual(self.levels(self.run_once()[0]), ["problem"])
        self.write("A.esm", masters=["Starfield.esm"])  # full again: what P.esm was built for
        self.assertEqual(self.run_once()[0].report.findings, [])

    def test_old_snapshot_without_built_for(self):
        """A snapshot written before built_for existed: the masters' kinds in it stand in, and are then kept."""
        _, st = self.run_once()
        with open(st["state"], encoding="utf-8") as f:
            snap = json.load(f)
        for v in snap["plugins"].values():
            del v["built_for"]
        with open(st["state"], "w", encoding="utf-8") as f:
            json.dump(snap, f)
        self.write("A.esm", masters=["Starfield.esm"], kind="L")
        self.assertEqual(self.levels(self.run_once()[0]), ["problem"])
        self.assertEqual(self.levels(self.run_once()[0]), ["problem"])

    def test_caution_is_not_counted_as_built_for_old(self):
        self.run_once()
        self.write("A.esm", masters=["Starfield.esm"], kind="L")
        self.write("P.esm", masters=["Starfield.esm", "A.esm"], records=[("MISC", 0x01000800, "New")])
        ctx, st = self.run_once()
        self.assertEqual(self.levels(ctx), ["caution"])
        self.assertEqual(st["built_for_old"], 0)

    def test_changed_patch_is_caution(self):
        self.run_once()
        self.write("A.esm", masters=["Starfield.esm"], kind="L")
        self.write("P.esm", masters=["Starfield.esm", "A.esm"], records=[("MISC", 0x01000800, "New")])  # rebuilt: own index 01
        ctx, _ = self.run_once()
        self.assertEqual(self.levels(ctx), ["caution"])
        self.assertEqual(self.run_once()[0].report.findings, [])  # once only

    def test_no_state(self):
        ctx = self.ctx(self.names, no_state=True)
        st = patches.run(ctx)
        self.assertIn("off", st["state"])
        self.assertFalse(os.path.exists(self.state))

    def test_corrupt_snapshot(self):
        _, st = self.run_once()
        with open(st["state"], "w", encoding="utf-8") as f:
            f.write("{ not json")
        ctx, st2 = self.run_once()
        self.assertIn("state_warning", st2)
        self.assertEqual(ctx.report.findings, [])
        with open(st2["state"], encoding="utf-8") as f:
            self.assertIn("plugins", json.load(f))

    def test_key_stable_and_per_profile(self):
        a = self.ctx(self.names, profile="One").load_order
        b = self.ctx(self.names, profile="One").load_order
        c = self.ctx(self.names, profile="Two").load_order
        self.assertEqual(patches.source_key(a), patches.source_key(b))
        self.assertNotEqual(patches.source_key(a), patches.source_key(c))
        self.assertTrue(patches.source_key(a).startswith("plugins.txt-"))

    def test_key_ignores_vortex_export_name(self):
        """Each Vortex export gets a new file name; the key (and so the history) must not change with it, nor with
        the staging or deployment record paths. Another Data folder is another load order."""
        def vortex(export, data=None, **more):
            files = {"vortex_loadorder": os.path.join(self.tmp, export)}
            files.update(more)
            return LoadOrder(source="vortex", files=files, data=data or self.data)
        a = patches.source_key(vortex("loadorder-2026-01-01.json"))
        b = patches.source_key(vortex("loadorder-2026-02-02.json", vortex_staging=os.path.join(self.tmp, "S"),
                                      vortex_deployment=os.path.join(self.data, "vortex.deployment.x.json")))
        c = patches.source_key(vortex("loadorder-2026-01-01.json", data=os.path.join(self.tmp, "Other", "Data")))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("vortex-"))

    def test_key_mo2_instance_and_plugins_txt_path(self):
        def mo2(instance, profile="Default", modlist=True):
            files = {"plugins_txt": os.path.join(instance, "profiles", profile, "plugins.txt"),
                     "modorganizer_ini": os.path.join(instance, "ModOrganizer.ini")}
            if modlist:
                files["modlist"] = os.path.join(instance, "profiles", profile, "modlist.txt")
            return LoadOrder(source="mo2", profile=profile, files=files, data=self.data)
        one = os.path.join(self.tmp, "MO2")
        self.assertEqual(patches.source_key(mo2(one)), patches.source_key(mo2(one, modlist=False)))
        self.assertNotEqual(patches.source_key(mo2(one)), patches.source_key(mo2(os.path.join(self.tmp, "MO2b"))))
        pa = LoadOrder(source="plugins.txt", files={"plugins_txt": os.path.join(self.tmp, "a.txt")}, data=self.data)
        pb = LoadOrder(source="plugins.txt", files={"plugins_txt": os.path.join(self.tmp, "b.txt")}, data=self.data)
        self.assertNotEqual(patches.source_key(pa), patches.source_key(pb))

    def test_history_survives_new_vortex_export(self):
        def ctx(export):
            c = self.ctx(self.names, no_state=False)
            c.load_order.source = "vortex"
            c.load_order.files = {"vortex_loadorder": os.path.join(self.tmp, export)}
            return c
        patches.run(ctx("export-1.json"))
        self.write("A.esm", masters=["Starfield.esm"], kind="L")
        c = ctx("export-2.json")
        patches.run(c)
        self.assertEqual(self.levels(c), ["problem"])


if __name__ == "__main__":
    unittest.main()
