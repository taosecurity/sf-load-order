"""Tests for plugin_header: master names in UTF-8 and Windows-1252, the XXXX large-subrecord marker, and headers
that can't be read. Synthetic data only: every header is built byte by byte here."""
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sf_load_order import model, plugin_header  # noqa: E402
from tests import fixtures  # noqa: E402


def sub(sig, payload):
    return sig + struct.pack("<H", len(payload)) + payload


def big_sub(sig, payload):
    """A subrecord longer than 65535 bytes: XXXX holds its real size, and its own 16-bit size is 0."""
    return sub(b"XXXX", struct.pack("<I", len(payload))) + sig + struct.pack("<H", 0) + payload


def write_header(path, body, flags=fixtures.FLAG_MASTER):
    with open(path, "wb") as f:
        f.write(b"TES4" + struct.pack("<IIIIHH", len(body), flags, 0, 0, 0, 0) + body)
    return path


HEDR = sub(b"HEDR", struct.pack("<fII", 0.96, 0, 0x800))


class Names(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name

    def tearDown(self):
        self._t.cleanup()

    def masters(self, *raw):
        body = HEDR + b"".join(sub(b"MAST", r + b"\0") for r in raw)
        return plugin_header.header(write_header(os.path.join(self.t, "P.esm"), body))[1]

    def test_utf8_name(self):
        # "Łódź" in UTF-8 holds the byte 0x81, which Windows-1252 has no character for
        self.assertEqual(self.masters("Łódź.esm".encode("utf-8")), ["Łódź.esm"])

    def test_cp1252_name(self):
        self.assertEqual(self.masters("Café.esm".encode("cp1252")), ["Café.esm"])

    def test_undefined_cp1252_bytes_never_raise(self):
        got = self.masters(b"A\x81\x8d\x8f\x90\x9d\xff.esm")
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("A") and got[0].endswith(".esm"))
        self.assertIn("�", got[0])


class LargeSubrecords(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name

    def tearDown(self):
        self._t.cleanup()

    def test_big_onam_after_masters(self):
        onam = struct.pack("<I", 0x01000800) * 20000  # 80,000 bytes: too long for a 16-bit size
        body = HEDR + sub(b"MAST", b"Starfield.esm\0") + sub(b"MAST", b"ModA.esm\0") + big_sub(b"ONAM", onam) + \
            sub(b"INTV", struct.pack("<I", 1))
        p = write_header(os.path.join(self.t, "Big.esm"), body, fixtures.FLAG_MASTER | fixtures.FLAG_MEDIUM)
        self.assertEqual(plugin_header.header(p), ("M", ["Starfield.esm", "ModA.esm"]))

    def test_fake_mast_inside_onam(self):
        # without the XXXX rule, the 0 size makes the reader walk into ONAM's data and find this "MAST"
        fake = sub(b"MAST", b"Fake.esm\0")
        onam = fake + b"\0" * (70000 - len(fake)) + fake
        body = HEDR + sub(b"MAST", b"Starfield.esm\0") + big_sub(b"ONAM", onam)
        p = write_header(os.path.join(self.t, "Fake.esm"), body)
        self.assertEqual(plugin_header.header(p), ("F", ["Starfield.esm"]))

    def test_xxxx_past_the_end(self):
        body = HEDR + sub(b"MAST", b"Starfield.esm\0") + sub(b"XXXX", struct.pack("<I", 0xFFFFFFFF)) + b"ONAM\0\0"
        p = write_header(os.path.join(self.t, "Cut.esm"), body)
        self.assertEqual(plugin_header.header(p), ("F", ["Starfield.esm"]))


class NeverRaises(unittest.TestCase):
    def test_truncated_header(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "Short.esm")
            with open(p, "wb") as f:
                f.write(b"TES4" + struct.pack("<IIIIHH", 5000, 0, 0, 0, 0, 0) + b"MAST\xff\xff abc")
            self.assertEqual(plugin_header.header(p), ("F", []))

    def test_missing_and_bad_paths(self):
        self.assertEqual(plugin_header.header(os.path.join(tempfile.gettempdir(), "no-such-dir-x", "A.esm")),
                         (None, None))
        self.assertEqual(plugin_header.header("bad\0name.esm"), (None, None))

    def test_fill_survives_an_unexpected_error(self):
        lo = model.LoadOrder("plugins.txt")
        lo.plugins = [model.Plugin("A.esm", True, path="A.esm"), model.Plugin("B.esm", True, path="B.esm")]
        with mock.patch.object(plugin_header, "header", side_effect=RuntimeError("boom")):
            plugin_header.fill(lo)
        self.assertEqual([(p.kind, p.masters) for p in lo.plugins], [("?", []), ("?", [])])


class MasterFlag(unittest.TestCase):
    """Plugin.master: the master flag (0x1), or a .esm or .esl name whatever the flags say (esplugin's Starfield
    rule); the light and medium flags don't make a plugin a master."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.t = self._t.name

    def tearDown(self):
        self._t.cleanup()

    def master(self, name, flags):
        lo = model.LoadOrder("plugins.txt")
        lo.plugins = [model.Plugin(name, True, path=write_header(os.path.join(self.t, name), HEDR, flags))]
        plugin_header.fill(lo)
        return lo.plugins[0].master

    def test_flag_read(self):
        self.assertEqual(plugin_header.read(write_header(os.path.join(self.t, "A.esp"), HEDR, 0x401))[2], 0x401)
        self.assertEqual(plugin_header.flags_of(os.path.join(self.t, "A.esp")), 0x401)

    def test_esp_with_and_without_the_flag(self):
        self.assertTrue(self.master("Flagged.esp", plugin_header.FLAG_MASTER))
        self.assertFalse(self.master("Plain.esp", 0))

    def test_light_or_medium_flag_alone_is_not_a_master(self):
        self.assertFalse(self.master("Light.esp", plugin_header.FLAG_LIGHT))
        self.assertFalse(self.master("Medium.esp", plugin_header.FLAG_MEDIUM))

    def test_esm_and_esl_extension_is_a_master_without_the_flag(self):
        self.assertTrue(self.master("NoFlag.esm", 0))
        self.assertTrue(self.master("NoFlag.ESL", 0))

    def test_unreadable_and_missing(self):
        p = os.path.join(self.t, "Junk.esp")
        with open(p, "wb") as f:
            f.write(b"not a plugin")
        lo = model.LoadOrder("plugins.txt")
        lo.plugins = [model.Plugin("Junk.esp", True, path=p), model.Plugin("Gone.esm", True, path=None)]
        plugin_header.fill(lo)
        self.assertFalse(lo.plugins[0].master)  # by name alone
        self.assertIsNone(lo.plugins[1].master)  # not read
        self.assertIsNone(plugin_header.flags_of(p))
        self.assertIsNone(plugin_header.flags_of(os.path.join(self.t, "nothing-here.esm")))


if __name__ == "__main__":
    unittest.main()
