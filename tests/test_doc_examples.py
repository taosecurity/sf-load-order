"""The `candidate` Example in README.md and in CHANGELOG.md's newest release must print the RESULT line a real run
prints (older CHANGELOG entries show what their release printed: 0.2.3's record check added a caution).

It rebuilds the made-up files the Example describes (ShipMod 0.3.0 built on the installed CoreTools, an older
ShipMod installed as a mod of its own), runs `candidate`, and compares the RESULT line (without the report path)
with the one in each document. This catches an Example that went stale when a later change added a finding.
"""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tests import fixtures  # noqa: E402
from tests.test_candidate import make_zip, run_cli  # noqa: E402

RESULT_RE = re.compile(r"^\s*(RESULT: \d+ problem\(s\), \d+ caution\(s\), \d+ note\(s\)) \(report: ", re.M)


def doc_results(name):
    """Every candidate Example RESULT line in a document (the Example is the block that runs `candidate ShipMod`)."""
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        text = f.read()
    if name == "CHANGELOG.md":  # only the newest release: an older entry shows what that release printed
        parts = re.split(r"^## ", text, flags=re.M)
        text = parts[1] if len(parts) > 1 else text
    found = []
    for m in re.finditer(r"candidate ShipMod-0\.3\.0\.zip.*\n.*\n(.*)\n", text):
        r = RESULT_RE.match(m.group(1))
        if not r:
            raise AssertionError(f"a candidate Example in {name} has no RESULT line two lines below the command")
        found.append(r.group(1))
    return found


class VersionStrings(unittest.TestCase):
    """The example bylines in README.md and AGENTS.md, and the README's download line, name the version in use."""

    def test_bylines_match_version(self):
        import sf_load_order
        for name in ("README.md", "AGENTS.md"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                text = f.read()
            for m in re.finditer(r"^sf-load-order (\d+\.\d+\.\d+) by ", text, re.M):
                self.assertEqual(m.group(1), sf_load_order.__version__, name)
            self.assertIn(f"sf-load-order {sf_load_order.__version__} by", text, name)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            self.assertIn(f"sf-load-order-{sf_load_order.__version__}.zip", f.read())


class DocExamples(unittest.TestCase):
    def test_candidate_example_result_matches_docs(self):
        with tempfile.TemporaryDirectory() as t:
            data = os.path.join(t, "game", "Data")
            fixtures.plugin(os.path.join(data, "Starfield.esm"))
            mo2 = os.path.join(t, "mo2")
            fixtures.mo2(mo2, "Default", [
                ("CoreTools", True, {"CoreTools.esm": {}, "scripts\\shared.pex": b"x", "meshes\\crate.nif": b"theirs"}),
                ("OldShipMod", True, {"ShipMod.esm": {"masters": ("CoreTools.esm",)}}),
            ])
            z = make_zip(os.path.join(t, "ShipMod-0.3.0.zip"), {
                "Data/ShipMod.esm": ("plugin", ("CoreTools.esm",)),
                "Data/scripts/shared.pex": b"x2", "Data/meshes/crate.nif": b"mine", "Data/readme.txt": b"r"})
            code, out, err = run_cli("candidate", z, "--mo2", mo2, "--data", data, "--no-state",
                                     "--out", os.path.join(t, "out"))
        self.assertEqual(code, 0, err)
        m = RESULT_RE.search(out)
        self.assertTrue(m, out)
        for doc in ("README.md", "CHANGELOG.md"):
            lines = doc_results(doc)
            self.assertTrue(lines, f"no candidate Example found in {doc}")
            for line in lines:
                self.assertEqual(line, m.group(1), f"the Example in {doc} is out of date")


if __name__ == "__main__":
    unittest.main()
