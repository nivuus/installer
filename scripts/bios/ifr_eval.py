"""Evaluate IFR conditions (suppressif, grayoutif, disableif) the way the
firmware's setup browser does, against the values currently stored.

An IFR expression is a postfix (RPN) sequence: operands push, operators pop.
Three-valued logic is used throughout: an operand that cannot be read from
Linux (a question whose varstore has no efivar, an opcode this module does
not implement) yields UNKNOWN, and UNKNOWN propagates, except where the
other operand decides alone (False AND x, True OR x). A condition that is
UNKNOWN is never treated as false: the caller refuses to write a question
whose state it cannot establish.
"""

UNKNOWN = None

# Opcodes, UEFI 2.10 table 33-27.
EQ_ID_VAL, EQ_ID_ID, EQ_ID_VAL_LIST, AND, OR, NOT = 0x12, 0x13, 0x14, 0x15, 0x16, 0x17
GET, EQUAL, NOT_EQUAL, GREATER, GREATER_EQ, LESS, LESS_EQ = 0x2B, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x34
BIT_AND, BIT_OR, BIT_NOT, SHL, SHR = 0x35, 0x36, 0x37, 0x38, 0x39
ADD, SUB, MUL, DIV, MOD = 0x3A, 0x3B, 0x3C, 0x3D, 0x3E
QUESTION_REF1, UINT8, UINT16, UINT32, UINT64 = 0x40, 0x42, 0x43, 0x44, 0x45
TRUE, FALSE, ZERO, ONE, ONES = 0x46, 0x47, 0x52, 0x53, 0x54

COMPARE = {EQUAL: lambda a, b: a == b, NOT_EQUAL: lambda a, b: a != b,
           GREATER: lambda a, b: a > b, GREATER_EQ: lambda a, b: a >= b,
           LESS: lambda a, b: a < b, LESS_EQ: lambda a, b: a <= b}
ARITH = {BIT_AND: lambda a, b: a & b, BIT_OR: lambda a, b: a | b,
         SHL: lambda a, b: a << b, SHR: lambda a, b: a >> b,
         ADD: lambda a, b: a + b, SUB: lambda a, b: a - b, MUL: lambda a, b: a * b,
         DIV: lambda a, b: a // b if b else UNKNOWN, MOD: lambda a, b: a % b if b else UNKNOWN}
CONSTANTS = {TRUE: True, FALSE: False, ZERO: 0, ONE: 1, ONES: 0xFFFFFFFFFFFFFFFF}
LITERALS = {UINT8: 1, UINT16: 2, UINT32: 4, UINT64: 8}


class Unsupported(Exception):
    """An opcode whose stack effect is unknown: the whole expression is."""


def _num(value):
    if isinstance(value, bool):
        return int(value)
    return value


def evaluate(expression, question_value, raw_value):
    """Value of an RPN expression; UNKNOWN if any needed operand is.

    question_value(qid) -> int | UNKNOWN, the current value of a question.
    raw_value(varstore_id, offset, size) -> int | UNKNOWN, for Get.
    """
    stack = []
    try:
        for r in expression:
            op = r.opcode
            if op == EQ_ID_VAL:
                v = question_value(r.u(2, 2))
                stack.append(UNKNOWN if v is UNKNOWN else v == r.u(4, 2))
            elif op == EQ_ID_ID:
                a, b = question_value(r.u(2, 2)), question_value(r.u(4, 2))
                stack.append(UNKNOWN if UNKNOWN in (a, b) else a == b)
            elif op == EQ_ID_VAL_LIST:
                v = question_value(r.u(2, 2))
                values = [r.u(6 + 2 * i, 2) for i in range(r.u(4, 2))]
                stack.append(UNKNOWN if v is UNKNOWN else v in values)
            elif op == QUESTION_REF1:
                stack.append(question_value(r.u(2, 2)))
            elif op in LITERALS:
                stack.append(r.u(2, LITERALS[op]))
            elif op in CONSTANTS:
                stack.append(CONSTANTS[op])
            elif op == GET:
                size = {0: 1, 1: 2, 2: 4, 3: 8}.get(r.data[6])
                stack.append(raw_value(r.u(2, 2), r.u(4, 2), size) if size else UNKNOWN)
            elif op == NOT:
                a = stack.pop()
                stack.append(UNKNOWN if a is UNKNOWN else not a)
            elif op == BIT_NOT:
                a = stack.pop()
                stack.append(UNKNOWN if a is UNKNOWN else ~_num(a) & 0xFFFFFFFFFFFFFFFF)
            elif op in (AND, OR):
                b, a = stack.pop(), stack.pop()
                stack.append(_logic(op, a, b))
            elif op in COMPARE or op in ARITH:
                b, a = stack.pop(), stack.pop()
                if UNKNOWN in (a, b):
                    stack.append(UNKNOWN)
                else:
                    stack.append((COMPARE.get(op) or ARITH[op])(_num(a), _num(b)))
            else:
                raise Unsupported(op)
    except (Unsupported, IndexError):
        return UNKNOWN
    return stack[-1] if len(stack) == 1 else UNKNOWN


def _truth(value):
    return UNKNOWN if value is UNKNOWN else bool(value)


def _logic(op, a, b):
    """Kleene AND/OR: one known operand can decide without the other."""
    a, b = _truth(a), _truth(b)
    decisive = op == OR   # True decides an OR, False decides an AND
    if a is decisive or b is decisive:
        return decisive
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    return not decisive


def states(conditions, question_value, raw_value):
    """{state: True | UNKNOWN} for every condition that is not plainly false."""
    out = {}
    for state, expression in conditions:
        result = evaluate(expression, question_value, raw_value)
        if result is UNKNOWN:
            out.setdefault(state, UNKNOWN)
        elif result:
            out[state] = True
    return out
