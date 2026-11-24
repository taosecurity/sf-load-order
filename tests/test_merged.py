"""Tests for merged.py (merged patches, sf-mod-check patch check). Synthetic only: a fake sf-mod-check (a small .py
behind a .cmd wrapper on Windows or a shell script elsewhere) that prints a version and, for `patch check`, writes the
--json file it is told to and exits with the code it is told to. The --json and exit shapes follow sf-mod-check
0.15.0's README ("Is the patch still up to date?")."""
import argparse
import json
import os
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import cli, merged, model, patches  # noqa: E402
from tests import fixtures  # noqa: E402

FAKE = textwrap.dedent(r'''
    import json, os, sys
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "fake.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    a = sys.argv[1:]
    with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(a) + "\n")
    if a == ["--version"]:
        print(cfg.get("version", "sf-mod-check 0.15.0 by someone - GPL-3.0"))
        sys.exit(0)
    if a[:2] == ["patch", "check"]:
        c = cfg.get("patch", {}).get(os.path.basename(a[2]).lower(), {})
        j = c.get("json", {"patch": os.path.basename(a[2]), "state": "up to date", "result": "RESULT: UP TO DATE",
                           "findings": [], "why": None})
        if j is not None:
            with open(a[a.index("--json") + 1], "w", encoding="utf-8") as f:
                f.write(j if isinstance(j, str) else json.dumps(j))
        print(c.get("result", "RESULT: UP TO DATE"))
        sys.exit(c.get("exit", 0))
    print("RESULT: CANNOT CHECK (fake)")
    sys.exit(2)
''')


def finding(level, rule, text, plugin="ModA.esm", records=("000100:Starfield.esm",)):
    return {"level": level, "rule": rule, "plugin": plugin, "text": text, "records": list(records)}


def check_json(state, findings, result, why=None, patch="Patch.esm"):
    return {"patch": patch, "state": state, "result": result, "findings": findings, "why": why}


def manifest(path, patch="Patch.esm"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"schema": 1, "tool": "sf-mod-check 0.15.0", "patch": {"name": patch, "size": 1, "sha256": "0" * 64},
                   "loadOrder": {"plugins": ["Starfield.esm", "ModA.esm", patch]}}, f)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.t = self._d.name
        self.addCleanup(self._d.cleanup)
        self.bin = os.path.join(self.t, "bin")
        os.makedirs(self.bin)
        self.data, self.ptxt = fixtures.game(os.path.join(self.t, "Game"), {
            "ModA.esm": {"masters": ["Starfield.esm"]},
            "Patch.esm": {"masters": ["Starfield.esm", "ModA.esm"]}})
        self.man = manifest(os.path.join(self.data, merged.MANIFEST))

    def lo(self, names=("ModA.esm", "Patch.esm"), source="plugins.txt", mods=None):
        ps = [model.Plugin("Starfield.esm", True, official=True, path=os.path.join(self.data, "Starfield.esm"))]
        for n in names:
            on = not n.startswith("-")
            n = n.lstrip("-")
            p = os.path.join(self.data, n)
            ps.append(model.Plugin(n, on, path=p if os.path.isfile(p) else None))
        return model.LoadOrder(source=source, data=self.data, plugins=ps, mods=mods)

    def fake(self, **cfg):
        script = os.path.join(self.bin, "fake_smc.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(FAKE)
        with open(os.path.join(self.bin, "fake.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        if os.name == "nt":
            cmd = os.path.join(self.bin, "sf-mod-check.cmd")
            with open(cmd, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(f'@echo off\n"{sys.executable}" -I "{script}" %*\nexit /b %ERRORLEVEL%\n')
            return cmd
        cmd = os.path.join(self.bin, "sf-mod-check")
        with open(cmd, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n')
        os.chmod(cmd, 0o755)
        return cmd

    def calls(self):
        p = os.path.join(self.bin, "calls.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def ctx(self, lo, **opts):
        a = argparse.Namespace(depth=None, sf_mod_check=None, patch_sources=None, out=self.t, no_state=True)
        for k, v in opts.items():
            setattr(a, k, v)
        return cli.Context(a, lo, model.Report("test"))

    def mine(self, ctx):
        return [f for f in ctx.report.findings if f.section == "patches"]


class FindManifests(Base):
    def test_beside_plugins_mod_folders_and_option(self):
        mods = os.path.join(self.t, "Staging")
        in_mod = manifest(os.path.join(mods, "patchmod", merged.MANIFEST), "Other.esm")
        off_mod = manifest(os.path.join(mods, "offmod", merged.MANIFEST), "Off.esm")
        extra = os.path.join(self.t, "extra")
        named = manifest(os.path.join(extra, "third.json"), "Third.esm")
        with open(os.path.join(extra, "not-a-manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"something": 1}, f)
        lone = manifest(os.path.join(self.t, "lone", "x.json"), "Fourth.esm")
        lo = self.lo(mods=[model.Mod("patchmod", True, path=os.path.dirname(in_mod)),
                           model.Mod("offmod", False, path=os.path.dirname(off_mod))])
        got = merged.find_manifests(lo, [extra, lone, self.man])
        names = [(os.path.normcase(p), explicit) for p, explicit in got]
        self.assertIn((os.path.normcase(self.man), True), names)  # the option naming it again doesn't repeat it
        self.assertIn((os.path.normcase(in_mod), False), names)
        self.assertIn((os.path.normcase(named), True), names)
        self.assertIn((os.path.normcase(lone), True), names)
        self.assertNotIn(os.path.normcase(off_mod), [n for n, _ in names])  # a disabled mod's folder isn't read
        self.assertEqual(len(names), 4)

    def test_deployed_copy_counts_once(self):
        """Code review round 2: Vortex deploys a patch mod's manifest into Data, so the staging copy and Data's copy
        are one patch, named by the mod's copy."""
        staged = os.path.join(self.t, "Staging", "patchmod", merged.MANIFEST)
        os.makedirs(os.path.dirname(staged))
        with open(self.man, "rb") as src, open(staged, "wb") as dst:
            dst.write(src.read())
        lo = self.lo(mods=[model.Mod("patchmod", True, path=os.path.dirname(staged))])
        got = merged.find_manifests(lo)
        self.assertEqual([(os.path.normcase(p), e) for p, e in got], [(os.path.normcase(staged), False)])


class Runs(Base):
    def test_up_to_date(self):
        exe = self.fake()
        c = self.ctx(self.lo(), sf_mod_check=exe)
        entries, st = merged.run(c)
        self.assertEqual(st["status"], "done")
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual((e["patch"], e["state"], e["level"], e["rebuild"], e["checked"]),
                         ("Patch.esm", "up to date", "ok", False, True))
        self.assertEqual(e["reasons"], [])
        call = [a for a in self.calls() if a[:2] == ["patch", "check"]][0]
        self.assertEqual(os.path.normcase(call[2]), os.path.normcase(os.path.join(self.data, "Patch.esm")))
        self.assertEqual(os.path.normcase(call[call.index("--manifest") + 1]), os.path.normcase(self.man))
        self.assertEqual(os.path.normcase(call[call.index("--data") + 1]), os.path.normcase(self.data))
        self.assertIn("--plugins", call)
        self.assertIn("--json", call)
        fs = self.mine(c)
        self.assertEqual([f.level for f in fs], ["note"])
        self.assertIn("up to date", fs[0].text)

    def test_rebuild_review_and_note(self):
        j = check_json("rebuild", [finding("REVIEW", "new-editor", "new plugin ModB.esm changes 1 record.",
                                           plugin="ModB.esm"),
                                   finding("NOTE", "updated", "ModA.esm changed, no patched record did.")],
                       "RESULT: REBUILD (1 reasons)")
        exe = self.fake(patch={"patch.esm": {"exit": 1, "json": j, "result": "RESULT: REBUILD (1 reasons)"}})
        c = self.ctx(self.lo(), sf_mod_check=exe)
        entries, st = merged.run(c)
        e = entries[0]
        self.assertEqual((e["state"], e["level"], e["rebuild"], e["checked"]), ("rebuild", "caution", True, True))
        self.assertEqual(e["reasons"], ["new plugin ModB.esm changes 1 record."])
        self.assertEqual(e["smc_state"], "rebuild")
        self.assertEqual(st["rebuild"], 1)
        fs = self.mine(c)
        self.assertEqual(sorted(f.level for f in fs), ["caution", "note"])
        cau = [f for f in fs if f.level == "caution"][0]
        self.assertIn("needs a rebuild", cau.text)
        self.assertIn("[new-editor]", cau.text)
        self.assertIn("ModB.esm", cau.plugins)

    def test_problem_is_a_problem(self):
        j = check_json("rebuild", [finding("PROBLEM", "removed", "master ModA.esm is gone.")], "RESULT: REBUILD (1 reasons)")
        exe = self.fake(patch={"patch.esm": {"exit": 1, "json": j}})
        c = self.ctx(self.lo(), sf_mod_check=exe)
        entries, _ = merged.run(c)
        self.assertEqual(entries[0]["level"], "problem")
        self.assertEqual([f.level for f in self.mine(c)], ["problem"])

    def test_exit_1_with_notes_only_is_not_up_to_date(self):
        j = check_json("rebuild", [finding("NOTE", "updated", "ModA.esm changed, no patched record did.")],
                       "RESULT: REBUILD")
        exe = self.fake(patch={"patch.esm": {"exit": 1, "json": j}})
        c = self.ctx(self.lo(), sf_mod_check=exe)
        entries, _ = merged.run(c)
        e = entries[0]
        self.assertEqual((e["state"], e["level"], e["rebuild"]), ("rebuild", "caution", True))
        self.assertEqual(e["reasons"], [merged.EXIT1_NO_REASON])
        fs = self.mine(c)
        self.assertFalse([f for f in fs if "is up to date" in f.text])
        self.assertIn("may need a rebuild", [f for f in fs if f.level == "caution"][0].text)

    def test_patch_off(self):
        j = check_json("not active", [finding("NOTE", "patch-off", "Patch.esm isn't in the load order.",
                                              plugin="Patch.esm", records=())],
                       "RESULT: UP TO DATE (the patch isn't in the load order; nothing was checked)")
        exe = self.fake(patch={"patch.esm": {"exit": 0, "json": j}})
        c = self.ctx(self.lo(names=("ModA.esm", "-Patch.esm")), sf_mod_check=exe)
        entries, _ = merged.run(c)
        e = entries[0]
        self.assertEqual((e["state"], e["level"], e["rebuild"], e["checked"]), ("not enabled", "note", False, False))
        self.assertEqual([f.level for f in self.mine(c)], ["note"])

    def test_cannot_check(self):
        j = check_json("cannot check", [], "RESULT: CANNOT CHECK (schema 9 isn't known)", why="schema 9 isn't known")
        exe = self.fake(patch={"patch.esm": {"exit": 2, "json": j}})
        c = self.ctx(self.lo(), sf_mod_check=exe)
        entries, _ = merged.run(c)
        e = entries[0]
        self.assertEqual((e["state"], e["level"], e["rebuild"], e["checked"]), ("cannot check", "caution", False, False))
        self.assertEqual(e["reasons"], ["schema 9 isn't known"])
        self.assertIn("Couldn't check", self.mine(c)[0].text)

    def test_no_json_written(self):
        exe = self.fake(patch={"patch.esm": {"exit": 0, "json": None}})
        c = self.ctx(self.lo(), sf_mod_check=exe)
        entries, _ = merged.run(c)
        self.assertEqual(entries[0]["state"], "cannot check")
        self.assertIn("no --json", entries[0]["reasons"][0])

    def test_unreadable_json(self):
        exe = self.fake(patch={"patch.esm": {"exit": 1, "json": "{not json"}})
        c = self.ctx(self.lo(), sf_mod_check=exe)
        entries, _ = merged.run(c)
        self.assertEqual(entries[0]["state"], "cannot check")
        self.assertFalse(entries[0]["rebuild"])

    def test_unknown_level_refused(self):
        j = check_json("rebuild", [finding("FATAL", "x", "?")], "RESULT: REBUILD (1 reasons)")
        exe = self.fake(patch={"patch.esm": {"exit": 1, "json": j}})
        entries, _ = merged.run(self.ctx(self.lo(), sf_mod_check=exe))
        self.assertEqual(entries[0]["state"], "cannot check")

    def test_too_old(self):
        exe = self.fake(version="sf-mod-check 0.14.0 by someone - GPL-3.0")
        c = self.ctx(self.lo(), sf_mod_check=exe)
        value, st = merged.run(c)
        self.assertIn("0.15.0", value["not_checked"])
        self.assertEqual(st["status"], "not checked")
        self.assertFalse([a for a in self.calls() if a[:1] == ["patch"]])
        self.assertEqual([f.level for f in self.mine(c)], ["note"])

    def test_no_program(self):
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = self.bin  # empty: no sf-mod-check
        try:
            value, _ = merged.run(self.ctx(self.lo()))
        finally:
            os.environ["PATH"] = old
        self.assertIn("wasn't found", value["not_checked"])

    def test_missing_patch_sources_file_is_not_counted_as_found(self):
        """0.2.2 bug hunt: with no sf-mod-check, a --patch-sources file that doesn't exist was counted in the note
        "2 merged patch manifest(s) ... were found"; now it gets its own caution and isn't counted."""
        gone = os.path.join(self.t, "no such folder", "patch-sources.json")
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = self.bin  # empty: no sf-mod-check
        try:
            c = self.ctx(self.lo(), patch_sources=[gone])
            merged.run(c)
        finally:
            os.environ["PATH"] = old
        f = self.mine(c)
        self.assertEqual([(x.level, x.data.get("manifest")) for x in f if x.level == "caution"],
                         [("caution", os.path.abspath(gone))])
        note = next(x for x in f if x.level == "note")
        self.assertIn("1 merged patch manifest(s)", note.text)
        self.assertNotIn(os.path.abspath(gone), note.data["manifests"])
        # with sf-mod-check, the same file gets the same caution and the real manifest is still checked
        exe = self.fake()
        c = self.ctx(self.lo(), sf_mod_check=exe, patch_sources=[gone])
        entries, _ = merged.run(c)
        self.assertEqual([(e["patch"], e["state"]) for e in entries], [("Patch.esm", "up to date")])
        self.assertEqual([x.level for x in self.mine(c) if x.level != "note"], ["caution"])

    def test_light_runs_nothing(self):
        exe = self.fake()
        value, _ = merged.run(self.ctx(self.lo(), sf_mod_check=exe, depth="light"))
        self.assertIn("--depth light", value["not_checked"])
        self.assertEqual(self.calls(), [])

    def test_no_manifest_is_an_empty_list(self):
        os.remove(self.man)
        exe = self.fake()
        value, st = merged.run(self.ctx(self.lo(), sf_mod_check=exe))
        self.assertEqual(value, [])
        self.assertEqual(st["manifests"], 0)
        self.assertEqual(self.calls(), [])  # not even --version

    def test_bad_manifest(self):
        with open(self.man, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "patch": {"name": "..\\evil.esm"}}, f)
        exe = self.fake()
        c = self.ctx(self.lo(), sf_mod_check=exe)
        entries, _ = merged.run(c)
        self.assertEqual(entries[0]["state"], "cannot check")
        self.assertIsNone(entries[0]["patch"])
        self.assertFalse([a for a in self.calls() if a[:1] == ["patch"]])
        self.assertEqual([f.level for f in self.mine(c)], ["caution"])

    def old_manifest(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "patch": "ConflictPatch - Old.esm", "profile": "P"}, f)
        return path

    def test_old_manifest_in_a_folder_gets_a_note(self):
        """0.2.2 (B2): an old patch_sources.py file in a --patch-sources folder was skipped with no finding."""
        os.remove(self.man)
        old = self.old_manifest(os.path.join(self.t, "extra", "ConflictPatch-Old.json"))
        exe = self.fake()
        c = self.ctx(self.lo(), sf_mod_check=exe, patch_sources=[os.path.dirname(old)])
        value, st = merged.run(c)
        self.assertEqual(st["manifests"], 0)
        # 0.2.3: the patches list isn't empty when every manifest is old: one "cannot check" entry
        self.assertEqual(len(value), 1)
        self.assertEqual((value[0]["state"], value[0]["patch"], value[0]["checked"]),
                         ("cannot check", "ConflictPatch - Old.esm", False))
        self.assertEqual(value[0]["manifest"], os.path.abspath(old))
        self.assertIn("old patch_sources.py", value[0]["reasons"][0])
        f = self.mine(c)
        self.assertEqual([x.level for x in f], ["note"])
        self.assertEqual(f[0].data["old_manifests"], [os.path.abspath(old)])
        self.assertIn("old patch_sources.py", f[0].text)
        self.assertIn("sf-mod-check 0.15.0 or later", f[0].fix)
        self.assertEqual(self.calls(), [])

    def test_old_manifest_found_in_data_says_found_not_passed(self):
        """Code review round 1, 2026-11-23: an old patch-sources.json found in Data on its own got a note saying it
        was passed with --patch-sources."""
        os.remove(self.man)
        old = self.old_manifest(os.path.join(self.data, merged.MANIFEST))
        c = self.ctx(self.lo(), sf_mod_check=self.fake())
        merged.run(c)
        f = [x for x in self.mine(c) if "old_manifests" in x.data]
        self.assertEqual([x.data["old_manifests"] for x in f], [[os.path.abspath(old)]])
        self.assertNotIn("--patch-sources", f[0].text)
        self.assertIn("merged patch manifest(s) are from the old patch_sources.py", f[0].text)
        # code review round 2, 2026-11-23: the fix said "and pass that file", though a rebuilt one is found by itself
        self.assertNotIn("pass that file", f[0].fix)
        self.assertIn("found by itself", f[0].fix)

    def test_old_manifest_named_directly_gets_the_note(self):
        """0.2.2: an old file named directly gets the same NOTE, not the "isn't a patch-sources.json" caution, and
        the real manifest beside it is still checked."""
        old = self.old_manifest(os.path.join(self.t, "extra", "ConflictPatch-Old.json"))
        exe = self.fake()
        c = self.ctx(self.lo(), sf_mod_check=exe, patch_sources=[old])
        entries, _ = merged.run(c)
        self.assertEqual([(e["patch"], e["state"]) for e in entries], [("Patch.esm", "up to date")])
        f = self.mine(c)
        self.assertEqual([x.data.get("old_manifests") for x in f if x.level != "note" or "old_manifests" in x.data],
                         [[os.path.abspath(old)]])
        self.assertFalse(any(x.level == "caution" for x in f))

    def test_patch_not_installed(self):
        extra = manifest(os.path.join(self.t, "extra", "gone.json"), "Gone.esm")
        os.remove(self.man)
        exe = self.fake()
        c = self.ctx(self.lo(), sf_mod_check=exe, patch_sources=[extra])
        entries, _ = merged.run(c)
        self.assertEqual((entries[0]["patch"], entries[0]["state"]), ("Gone.esm", "not enabled"))
        self.assertFalse([a for a in self.calls() if a[:1] == ["patch"]])

    def test_mo2_hidden_plugins(self):
        os.remove(os.path.join(self.data, "ModA.esm"))
        exe = self.fake()
        value, _ = merged.run(self.ctx(self.lo(source="mo2"), sf_mod_check=exe))
        self.assertIn("MO2", value["not_checked"])

    def test_patches_section_sets_report_key_and_survives_a_bug(self):
        exe = self.fake()
        c = self.ctx(self.lo(), sf_mod_check=exe)
        st = patches.run(c)
        self.assertEqual(c.report.patches[0]["state"], "up to date")
        self.assertEqual(st["merged"]["manifests"], 1)
        with mock.patch.object(merged, "run", side_effect=RuntimeError("boom")):
            c2 = self.ctx(self.lo(), sf_mod_check=exe)
            st2 = patches.run(c2)
        self.assertEqual(st2["status"], "done")
        self.assertEqual(st2["merged"]["status"], "failed")
        self.assertIn("boom", c2.report.patches["not_checked"])


class EndToEnd(Base):
    def test_report_json_patches(self):
        j = check_json("rebuild", [finding("REVIEW", "moved", "ModA.esm now loads after the patch.")],
                       "RESULT: REBUILD (1 reasons)")
        exe = self.fake(patch={"patch.esm": {"exit": 1, "json": j}})
        out = os.path.join(self.t, "out")
        code = cli.main(["--plugins-txt", self.ptxt, "--data", self.data, "--sf-mod-check", exe, "--no-state",
                         "--out", out, "--quiet"])
        with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
            rj = json.load(f)
        with open(os.path.join(out, "report.txt"), encoding="utf-8") as f:
            txt = f.read()
        self.assertEqual(code, 0)
        self.assertEqual(rj["patches"][0]["state"], "rebuild")
        self.assertEqual(rj["patches"][0]["reasons"], ["ModA.esm now loads after the patch."])
        self.assertIn("needs a rebuild", txt)

    def test_not_deployed_profile(self):
        lo = self.lo()
        lo._vortex_profile = {"name": "Other", "deployed": False}
        out = os.path.join(self.t, "out2")
        with mock.patch.object(cli, "read_load_order", return_value=(lo, None)):
            code = cli.main(["--depth", "light", "--no-state", "--out", out, "--quiet"])
        with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
            rj = json.load(f)
        self.assertEqual(code, 0)
        self.assertIn("another profile", rj["patches"]["not_checked"])

    def test_help_names_the_option(self):
        self.assertIn("--patch-sources", cli.parser().format_help())


if __name__ == "__main__":
    unittest.main()
