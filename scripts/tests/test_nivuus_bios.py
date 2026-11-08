#!/usr/bin/env python3
"""Tests for scripts/bios (nivuus-bios). Run: python3 scripts/tests/test_nivuus_bios.py

The menu is synthetic but byte-exact: every record carries the opcode bytes
the UEFI specification defines, because the parser takes structure and
numbers from the bytes. It reproduces the two shapes that matter on the
reference ASUS board: a QuestionRef1 carrying the scope bit inside a
condition, and a typed-in voltage field.
"""
import os
import pathlib
import struct
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "bios"))
import efivars  # noqa: E402
import ifr_eval  # noqa: E402
import ifr_model  # noqa: E402
import nivuus_bios  # noqa: E402
from settings import Refused, Settings  # noqa: E402

GUID = "EC87D643-EBA4-4BB5-A1E5-3F3E36B20DA9"
OC_GUID = "A3A3B874-7636-4182-BA1E-F52C584494E3"
SETUP = "Setup-" + GUID.lower()


def u16(v):
    return struct.pack("<H", v)


class Ifr:
    """Writes IFRExtractor-style records: '0xADDR: text { BYTES }'."""

    def __init__(self):
        self.lines, self.address = ["Program version: 1.6.1, Extraction mode: UEFI"], 0x1000

    def rec(self, text, data, scope=False):
        data = bytes([data[0], len(data) | (0x80 if scope else 0)]) + data[1:]
        self.lines.append("0x%X: %s { %s }" % (self.address, text, " ".join("%02X" % b for b in data)))
        self.address += len(data)

    def end(self):
        self.rec("End", b"\x29")

    def header(self, qid, vs, off, flags=0x00):
        return u16(1) + u16(2) + u16(qid) + u16(vs) + u16(off) + bytes([flags])

    def q_text(self, kind, prompt, rest=""):
        return '%s Prompt: "%s", Help: "help of %s", QuestionFlags: 0x0%s' % (kind, prompt, prompt, rest)

    def oneof(self, prompt, qid, vs, off, options, flags=0x00, conditions=()):
        self.rec(self.q_text("OneOf", prompt), b"\x05" + self.header(qid, vs, off, flags)
                 + b"\x00" + bytes([0, max(v for _, v in options), 0]), scope=True)
        for name, value in options:
            cond = dict(conditions).get(name)
            if cond:
                self.rec("SuppressIf", b"\x0A", scope=True)
                cond(self)
            self.rec('OneOfOption Option: "%s" Value: %d' % (name, value),
                     b"\x09" + u16(9) + b"\x00\x00" + bytes([value]))
            if cond:
                self.end()
        self.end()

    def build(self, path):
        pathlib.Path(path).write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def eq(qid, value):
    return lambda w: w.rec("EqIdVal QuestionId: 0x%X, Value: 0x%X" % (qid, value),
                           b"\x12" + u16(qid) + u16(value))


def asus_le(qid, value):
    """QuestionRef1 WITH the scope bit, as ASUS's compiler emits it, then <=."""
    def emit(w):
        w.rec("QuestionRef1 QuestionId: 0x%X" % qid, b"\x40" + u16(qid), scope=True)
        w.rec("Uint8 Value: 0x%X" % value, b"\x42" + bytes([value]))
        w.rec("LessEqual", b"\x34")
        w.end()
    return emit


def sample_menu(path):
    w = Ifr()
    w.rec('FormSet Guid: 7B59104A-C00D-4158-87FF-F04D6396A915, Title: "Setup", Help: "Setup"',
          b"\x0E" + bytes(20), scope=True)
    for vid, guid, size, name in ((0xF101, GUID, 0x10, "Setup"), (0x33, OC_GUID, 0x40, "OcSettings"),
                                  (0x44, GUID, 0x04, "NotOnLinux")):
        w.rec('VarStore Guid: %s, VarStoreId: 0x%X, Size: 0x%X, Name: "%s"' % (guid, vid, size, name),
              b"\x24" + bytes(16) + u16(vid) + u16(size) + name.encode() + b"\x00")
    w.rec('Form FormId: 0x1, Title: "Setup"', b"\x01" + u16(1) + u16(0), scope=True)
    w.rec(w.q_text("Ref", "Advanced"), b"\x0F" + w.header(0x10, 0, 0xFFFF) + u16(2))
    w.end()
    w.rec('Form FormId: 0x2, Title: "Advanced"', b"\x01" + u16(2) + u16(0), scope=True)
    w.oneof("Restore AC Power Loss", 0x2BB2, 0xF101, 0x9,
            [("Power On", 0), ("Power Off", 1), ("Last State", 2)], flags=0x14)
    w.oneof("Mode", 0x20, 0xF101, 0x2, [("Auto", 0), ("Manual", 1)])
    # Visible only while Mode is Manual (offset 2 == 1); hidden option by ASUS-style <=.
    w.rec("SuppressIf", b"\x0A", scope=True)
    eq(0x20, 0)(w)
    w.oneof("Ratio", 0x21, 0xF101, 0x3, [("Low", 0), ("High", 1), ("Extreme", 2)],
            conditions=[("Extreme", asus_le(0x20, 1))])
    w.end()
    w.rec("GrayOutIf", b"\x19", scope=True)
    w.rec("True", b"\x46")
    w.rec(w.q_text("CheckBox", "Locked Feature"), b"\x06" + w.header(0x22, 0xF101, 0x4) + b"\x00")
    w.end()
    w.rec(w.q_text("CheckBox", "Fan Boost"), b"\x06" + w.header(0x23, 0xF101, 0x5) + b"\x00")
    w.rec(w.q_text("Numeric", "Fan Floor"), b"\x07" + w.header(0x24, 0xF101, 0x6)
          + b"\x01" + u16(10) + u16(100) + u16(5), scope=True)
    w.end()
    w.rec(w.q_text("String", "DRAM VDD Voltage"), b"\x1C" + w.header(0x25, 0x33, 0x10) + b"\x00\x08\x00")
    w.rec("SuppressIf", b"\x0A", scope=True)
    eq(0x30, 1)(w)  # reads a question stored where Linux cannot see
    w.oneof("Hidden Maybe", 0x26, 0xF101, 0x7, [("A", 0), ("B", 1)])
    w.end()
    w.oneof("Not On Linux", 0x30, 0x44, 0x0, [("No", 0), ("Yes", 1)])
    w.oneof("Twin", 0x31, 0xF101, 0x8, [("A", 0), ("B", 1)])
    w.oneof("Twin", 0x32, 0xF101, 0x8, [("A", 0), ("B", 1)])
    w.end()
    w.end()
    w.build(path)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.ifr, self.vars = d / "setup.ifr.txt", d / "efivars"
        sample_menu(self.ifr)
        self.vars.mkdir()
        data = bytearray(0x10)
        data[2], data[5], data[6], data[9] = 1, 1, 30, 1   # Manual, Fan Boost on, floor 30, Power Off
        self.write_var(SETUP, data)
        oc = bytearray(0x40)
        oc[0x10:0x1A] = "1.25000".encode("utf-16-le")[:10]
        self.write_var("OcSettings-" + OC_GUID.lower(), oc)
        self.flags = []
        self.ev = efivars.EfiVars(str(self.vars), immutable=lambda p, on: self.flags.append(on))
        self.s = Settings(ifr_model.load(str(self.ifr)), self.ev)

    def tearDown(self):
        self.tmp.cleanup()

    def write_var(self, name, data, attrs=7):
        (self.vars / name).write_bytes(struct.pack("<I", attrs) + bytes(data))

    def setup_bytes(self):
        return (self.vars / SETUP).read_bytes()[4:]


class Parsing(Base):
    def test_questions_paths_and_storage(self):
        q = self.s.find("Restore AC Power Loss")
        self.assertEqual((q.question_id, q.offset, q.kind), (0x2BB2, 0x9, "oneof"))
        self.assertEqual(q.path, ("Setup", "Advanced"))
        self.assertEqual([o.name for o in q.options], ["Power On", "Power Off", "Last State"])
        self.assertEqual(self.s.display(q), "Power Off")

    def test_numeric_bounds_come_from_the_bytes(self):
        q = self.s.find("Fan Floor")
        self.assertEqual((q.size, q.minimum, q.maximum, q.step), (2, 10, 100, 5))

    def test_unbalanced_scopes_are_refused(self):
        self.ifr.write_text(self.ifr.read_text() + "0x9999: End { 29 02 }\n")
        with self.assertRaises(ifr_model.IfrFormatError):
            ifr_model.load(str(self.ifr))


class Conditions(Base):
    def test_suppressif_follows_the_stored_value(self):
        ratio = self.s.find("Ratio")
        self.assertEqual(self.s.states(ratio.conditions), {})          # Mode is Manual
        data = bytearray(self.setup_bytes())
        data[2] = 0
        self.write_var(SETUP, data)
        fresh = Settings(ifr_model.load(str(self.ifr)), self.ev)
        self.assertEqual(fresh.states(fresh.find("Ratio").conditions), {"suppressed": True})

    def test_scoped_questionref1_still_evaluates(self):
        # Extreme is suppressed while Mode <= 1: ASUS's QuestionRef1 carries the scope bit.
        self.assertEqual(self.s.choices(self.s.find("Ratio")), ["Low", "High"])

    def test_unreadable_operand_is_unknown_not_false(self):
        q = self.s.find("Hidden Maybe")
        self.assertEqual(self.s.states(q.conditions), {"suppressed": ifr_eval.UNKNOWN})

    def test_kleene_logic(self):
        self.assertIs(ifr_eval._logic(ifr_eval.AND, False, ifr_eval.UNKNOWN), False)
        self.assertIs(ifr_eval._logic(ifr_eval.OR, 1, ifr_eval.UNKNOWN), True)
        self.assertIs(ifr_eval._logic(ifr_eval.AND, True, ifr_eval.UNKNOWN), ifr_eval.UNKNOWN)


class Changes(Base):
    def test_valid_change_writes_one_byte_backs_up_and_verifies(self):
        before = self.setup_bytes()
        name, attrs, new, old, shown = self.s.plan(self.s.find("Restore AC Power Loss"), "power on")
        self.assertEqual((old, shown, attrs), ("Power Off", "Power On", 7))
        backup = self.ev.backup([name], os.path.join(self.tmp.name, "backup"))
        self.ev.write(name, attrs, new)
        after = self.setup_bytes()
        self.assertEqual(after[9], 0)
        self.assertEqual(after[:9] + after[10:], before[:9] + before[10:])
        self.assertEqual(self.flags, [False, True])          # immutable lifted, then restored
        self.assertEqual(self.ev.restore(backup), [SETUP])
        self.assertEqual(self.setup_bytes(), before)

    def test_option_not_offered_is_refused(self):
        with self.assertRaisesRegex(Refused, "not offered"):
            self.s.plan(self.s.find("Ratio"), "Extreme")

    def test_grayed_out_is_refused(self):
        with self.assertRaisesRegex(Refused, "grayed-out"):
            self.s.plan(self.s.find("Locked Feature"), "enabled")

    def test_unknown_state_is_refused(self):
        with self.assertRaisesRegex(Refused, "unknown"):
            self.s.plan(self.s.find("Hidden Maybe"), "B")

    def test_typed_in_field_is_refused(self):
        with self.assertRaisesRegex(Refused, "typed-in"):
            self.s.plan(self.s.find("DRAM VDD Voltage"), "1.20")

    def test_numeric_range_and_step(self):
        q = self.s.find("Fan Floor")
        for bad in ("5", "105", "32", "abc"):
            with self.assertRaises(Refused):
                self.s.plan(q, bad)
        self.assertEqual(self.s.plan(q, "35")[2][6:8], u16(35))

    def test_checkbox_keeps_the_other_bits(self):
        data = bytearray(self.setup_bytes())
        data[5] = 0b1011
        self.write_var(SETUP, data)
        s = Settings(ifr_model.load(str(self.ifr)), self.ev)
        self.assertEqual(s.plan(s.find("Fan Boost"), "off")[2][5], 0b1010)

    def test_variable_missing_from_linux_is_refused(self):
        with self.assertRaisesRegex(Refused, "not visible"):
            self.s.plan(self.s.find("Not On Linux"), "Yes")

    def test_menu_of_another_bios_is_refused(self):
        self.write_var(SETUP, bytes(0x12))
        s = Settings(ifr_model.load(str(self.ifr)), self.ev)
        with self.assertRaisesRegex(Refused, "does not belong"):
            s.plan(s.find("Restore AC Power Loss"), "Power On")

    def test_ambiguous_name_lists_the_ids(self):
        with self.assertRaisesRegex(Refused, "0x0031"):
            self.s.find("Twin")
        self.assertEqual(self.s.find("0x32").question_id, 0x32)

    def test_firmware_that_drops_the_write_is_detected(self):
        class Stubborn(efivars.EfiVars):
            def read(self, name):
                content = super().read(name)
                return (content[0], content[1]) if self.reads < 1 else (content[0], b"x")

            reads = 0

            def write(self, name, attributes, data):
                self.reads = 1
                super().write(name, attributes, data)

        ev = Stubborn(str(self.vars), immutable=lambda p, on: None)
        with self.assertRaisesRegex(efivars.EfiVarError, "did not keep"):
            ev.write(SETUP, 7, self.setup_bytes())

    def test_never_creates_a_variable(self):
        with self.assertRaisesRegex(efivars.EfiVarError, "never creates"):
            self.ev.write("Nope-" + GUID.lower(), 7, b"\x00")


class Cli(Base):
    def run_cli(self, *argv):
        return nivuus_bios.main(["--ifr", str(self.ifr), "--efivars", str(self.vars)] + list(argv))

    def test_set_without_confirmation_writes_nothing(self):
        before = self.setup_bytes()
        os.environ["NIVUUS_BIOS_STATE"] = self.tmp.name
        self.assertEqual(self.run_cli("set", "Restore AC Power Loss", "Power On"), 2)
        self.assertEqual(self.setup_bytes(), before)

    def test_refusal_exits_2(self):
        self.assertEqual(self.run_cli("set", "Locked Feature", "on", "--yes"), 2)

    def test_missing_menu_names_the_bios(self):
        dmi = pathlib.Path(self.tmp.name) / "dmi"
        dmi.mkdir()
        (dmi / "board_name").write_text("TEST BOARD\n")
        (dmi / "bios_version").write_text("9999\n")
        nivuus_bios.DMI, nivuus_bios.STATE = str(dmi), self.tmp.name
        with self.assertRaisesRegex(Refused, "TEST BOARD BIOS 9999"):
            nivuus_bios.load(type("A", (), {"ifr": None, "efivars": str(self.vars)})())


if __name__ == "__main__":
    unittest.main()
