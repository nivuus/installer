"""Model of a BIOS setup menu, built from IFRExtractor-RS verbose output.

IFRExtractor-RS (https://github.com/LongSoft/IFRExtractor-RS) decompiles the
HII forms of a firmware's Setup module into text: one record per IFR opcode,
with the resolved strings (prompts, option names, form titles) followed by
the opcode's raw bytes in braces. This module rebuilds the menu from that
text.

Structure comes from the BYTES, never from the indentation. Each opcode's
second byte carries the scope bit (0x80) that opens a nesting closed by an
End opcode, and that is how the firmware's own browser walks the forms. The
text indentation is a rendering of the same thing that goes wrong on real
firmware: ASUS's compiler sets the scope bit on QuestionRef1, which the
extractor then prints as an extra indentation level inside expressions.

Numbers (question ids, offsets, sizes, bounds, option values) are decoded
from the bytes too, with the layouts of the UEFI specification (2.10,
section 33.3.8); the text is used only for the strings.
"""

import re
from dataclasses import dataclass, field

# UEFI IFR opcodes this tool interprets (UEFI 2.10, table 33-27).
FORM, SUBTITLE, TEXT, ONE_OF, CHECKBOX, NUMERIC = 0x01, 0x02, 0x03, 0x05, 0x06, 0x07
ONE_OF_OPTION, SUPPRESS_IF, FORM_SET, REF = 0x09, 0x0A, 0x0E, 0x0F
GRAY_OUT_IF, STRING, DISABLE_IF, VARSTORE, VARSTORE_EFI = 0x19, 0x1C, 0x1E, 0x24, 0x26
END, DEFAULT = 0x29, 0x5B

QUESTION_OPS = {ONE_OF: "oneof", CHECKBOX: "checkbox", NUMERIC: "numeric", STRING: "string"}
CONDITION_OPS = {SUPPRESS_IF: "suppressed", GRAY_OUT_IF: "grayed-out", DISABLE_IF: "disabled"}

# Opcodes that only ever appear inside an expression (UEFI 2.10, 33.3.8.3).
EXPRESSION_OPS = set(range(0x12, 0x19)) | {0x20, 0x21, 0x22, 0x2A, 0x2B} | set(range(0x2F, 0x5B)) \
    | {0x5E, 0x60, 0x64}

# Question flags (UEFI 2.10, 33.3.8.3.1).
QF_READ_ONLY, QF_CALLBACK, QF_RESET_REQUIRED = 0x01, 0x04, 0x10

RECORD_START = re.compile(r"^0x([0-9A-Fa-f]+):\s*([A-Za-z0-9]+)")
BYTES = re.compile(r"\{ ([0-9A-F]{2}(?: [0-9A-F]{2})*) \}\s*$")
PROMPT = re.compile(r'Prompt: "(.*?)", (?:Help|QuestionFlags)', re.S)
HELP = re.compile(r'Help: "(.*?)", QuestionFlags', re.S)
OPTION = re.compile(r'Option: "(.*?)" Value:', re.S)
TITLE = re.compile(r'Title: "(.*?)"', re.S)
NAME = re.compile(r'Name: "(.*?)"')


class IfrFormatError(ValueError):
    """The input is not IFRExtractor verbose output this parser understands."""


@dataclass
class Record:
    """One IFR opcode: its raw bytes, its text, and the opcodes it scopes."""

    address: int
    opcode: int
    data: bytes
    text: str
    children: list = field(default_factory=list)

    def u(self, offset, size):
        return int.from_bytes(self.data[offset:offset + size], "little")


@dataclass
class VarStore:
    varstore_id: int
    guid: str
    name: str
    size: int

    @property
    def efivar(self):
        """The efivarfs file name of this store."""
        return "%s-%s" % (self.name, self.guid.lower())


@dataclass
class Option:
    name: str
    value: int
    default: bool
    conditions: list


@dataclass
class Question:
    kind: str
    prompt: str
    help: str
    question_id: int
    varstore_id: int
    offset: int
    flags: int
    size: int            # bytes; for a string, its capacity in UTF-16 units
    minimum: int = 0
    maximum: int = 0
    step: int = 0
    options: list = field(default_factory=list)
    default: object = None
    conditions: list = field(default_factory=list)   # [(state, expression)]
    path: tuple = ()
    address: int = 0


def split_records(lines):
    """Join continuation lines (multi-line help text) to their record."""
    current = None
    for line in lines:
        line = line.rstrip("\n")
        start = RECORD_START.match(line)
        if start:
            if current:
                yield current
            current = [int(start.group(1), 16), line]
        elif current:
            current[1] += "\n" + line
    if current:
        yield current


def parse_records(lines):
    """Records as a tree, nested by the scope bit of their bytes."""
    root = Record(0, -1, b"", "")
    stack = [root]
    for address, text in split_records(lines):
        found = BYTES.search(text)
        if not found:
            continue  # the program-version header line
        data = bytes.fromhex(found.group(1))
        record = Record(address, data[0], data, text[:found.start()])
        if record.opcode == END:
            if len(stack) == 1:
                raise IfrFormatError("End without an open scope at 0x%X" % address)
            stack.pop()
            continue
        stack[-1].children.append(record)
        if data[1] & 0x80:
            stack.append(record)
    if len(stack) != 1:
        raise IfrFormatError("%d scopes left open at end of input" % (len(stack) - 1))
    return root


def flatten_expression(records):
    """An expression's opcodes in stream order (RPN), however they nest."""
    out = []
    for record in records:
        out.append(record)
        out.extend(flatten_expression(record.children))
    return out


def split_condition(record):
    """A condition opcode's scope holds its expression, then the statements."""
    expression, body = [], []
    for child in record.children:
        if not body and child.opcode in EXPRESSION_OPS:
            expression.append(child)
        else:
            body.append(child)
    return flatten_expression(expression), body


def _value_size(flags):
    return 1 << (flags & 0x03)


def make_question(record, conditions, path):
    d, text = record, record.text
    prompt = PROMPT.search(text)
    help_text = HELP.search(text)
    q = Question(kind=QUESTION_OPS[d.opcode], prompt=prompt.group(1).strip() if prompt else "",
                 help=" ".join(help_text.group(1).split()) if help_text else "",
                 question_id=d.u(6, 2), varstore_id=d.u(8, 2), offset=d.u(10, 2),
                 flags=d.u(12, 1), size=1, conditions=list(conditions), path=path,
                 address=d.address)
    if d.opcode in (ONE_OF, NUMERIC):
        size = _value_size(d.data[13])
        q.size = size
        q.minimum, q.maximum, q.step = (d.u(14 + i * size, size) for i in range(3))
    elif d.opcode == CHECKBOX:
        q.default = bool(d.data[13] & 0x01)
    elif d.opcode == STRING:
        q.minimum, q.size = d.data[13], d.data[14]
    for child in d.children:
        if child.opcode == ONE_OF_OPTION:
            q.options.append(make_option(child, []))
        elif child.opcode == SUPPRESS_IF:
            expression, body = split_condition(child)
            q.options.extend(make_option(c, [("suppressed", expression)])
                             for c in body if c.opcode == ONE_OF_OPTION)
        elif child.opcode == DEFAULT and child.u(2, 2) == 0 and not child.data[1] & 0x80:
            q.default = child.u(5, _value_size(child.data[4]) if child.data[4] < 4 else 1)
    if q.kind == "oneof" and q.default is None:
        q.default = next((o.value for o in q.options if o.default), None)
    return q


def make_option(record, conditions):
    found = OPTION.search(record.text)
    value_type = record.data[5]
    size = _value_size(value_type) if value_type < 4 else 1
    return Option(name=found.group(1) if found else "", value=record.u(6, size),
                  default=bool(record.data[4] & 0x10), conditions=conditions)


@dataclass
class Menu:
    varstores: dict
    questions: list
    form_titles: dict


def walk(records, menu, conditions, path, refs):
    for record in records:
        op = record.opcode
        if op == VARSTORE:
            name = NAME.search(record.text)
            guid = re.search(r"Guid: ([0-9A-F-]{36})", record.text)
            vs = VarStore(record.u(18, 2), guid.group(1), name.group(1) if name else "",
                          record.u(20, 2))
            menu.varstores[vs.varstore_id] = vs
        elif op == FORM:
            title = TITLE.search(record.text)
            menu.form_titles[record.u(2, 2)] = title.group(1).strip() if title else ""
            walk(record.children, menu, conditions, (record.u(2, 2),), refs)
        elif op == REF and len(record.data) >= 15:
            prompt = PROMPT.search(record.text)
            refs.setdefault(record.u(13, 2), (path[-1] if path else None,
                                              prompt.group(1).strip() if prompt else ""))
        elif op in CONDITION_OPS:
            expression, body = split_condition(record)
            walk(body, menu, conditions + [(CONDITION_OPS[op], expression)], path, refs)
        elif op in QUESTION_OPS:
            menu.questions.append(make_question(record, conditions, path))
        elif record.children:
            walk(record.children, menu, conditions, path, refs)


def menu_path(form_id, menu, refs, seen=()):
    """Titles from the top of the menu down to a form, following the Refs."""
    if form_id in seen or form_id not in refs:
        return (menu.form_titles.get(form_id, ""),)
    parent, prompt = refs[form_id]
    above = menu_path(parent, menu, refs, seen + (form_id,)) if parent is not None else ()
    return above + ((prompt or menu.form_titles.get(form_id, "")).strip(),)


def load(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        root = parse_records(fh)
    menu, refs = Menu({}, [], {}), {}
    walk(root.children, menu, [], (), refs)
    if not menu.questions:
        raise IfrFormatError("no question found in %s" % path)
    for q in menu.questions:
        q.path = menu_path(q.path[0], menu, refs) if q.path else ()
    return menu
