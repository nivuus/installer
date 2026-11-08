"""Current values, visibility and validated changes of BIOS setup questions.

Ties the menu model (ifr_model) to the variables the firmware actually holds
(efivars): every value shown is read from the variable, every change is
checked against what the setup menu itself allows before anything is written.
"""

import ifr_eval
import ifr_model as ifr

BLOCKING_STATES = ("suppressed", "grayed-out", "disabled")
CHECKBOX_WORDS = {"enabled": 1, "on": 1, "1": 1, "true": 1,
                  "disabled": 0, "off": 0, "0": 0, "false": 0}


class Refused(ValueError):
    """A change this tool will not make, with the reason a person can act on."""


class Settings:
    def __init__(self, menu, efivars):
        self.menu = menu
        self.efivars = efivars
        self._stores = {}
        self.by_id = {}
        for q in menu.questions:
            self.by_id.setdefault(q.question_id, q)

    # --- reading -----------------------------------------------------------

    def store(self, varstore_id):
        """(VarStore, attributes, bytearray) or None when Linux cannot see it."""
        if varstore_id not in self._stores:
            vs = self.menu.varstores.get(varstore_id)
            content = self.efivars.read(vs.efivar) if vs and vs.name else None
            self._stores[varstore_id] = (vs, content[0], bytearray(content[1])) if content else None
        return self._stores[varstore_id]

    def raw(self, varstore_id, offset, size):
        entry = self.store(varstore_id)
        if not entry or offset + size > len(entry[2]):
            return ifr_eval.UNKNOWN
        return int.from_bytes(entry[2][offset:offset + size], "little")

    def value(self, q):
        """The question's stored value: int, bool, str, or UNKNOWN."""
        entry = self.store(q.varstore_id)
        if not entry:
            return ifr_eval.UNKNOWN
        data = entry[2]
        if q.kind == "string":
            end = q.offset + 2 * q.size
            if end > len(data):
                return ifr_eval.UNKNOWN
            return bytes(data[q.offset:end]).decode("utf-16-le", "replace").split("\x00")[0]
        number = self.raw(q.varstore_id, q.offset, q.size)
        if q.kind == "checkbox" and number is not ifr_eval.UNKNOWN:
            return bool(number & 0x01)
        return number

    def question_value(self, question_id):
        q = self.by_id.get(question_id)
        if not q or q.kind == "string":
            return ifr_eval.UNKNOWN
        return self.value(q)

    def states(self, conditions):
        return ifr_eval.states(conditions, self.question_value, self.raw)

    def display(self, q):
        """The value as the setup menu would print it."""
        v = self.value(q)
        if v is ifr_eval.UNKNOWN:
            return "?"
        if q.kind == "oneof":
            name = next((o.name for o in q.options if o.value == v), None)
            return name if name is not None else "%d (no matching option)" % v
        if q.kind == "checkbox":
            return "Enabled" if v else "Disabled"
        return str(v)

    def choices(self, q):
        """The values the menu offers right now, as text."""
        if q.kind == "oneof":
            return [o.name for o in q.options
                    if not self.states(o.conditions)] or ["(no option visible)"]
        if q.kind == "checkbox":
            return ["Enabled", "Disabled"]
        if q.kind == "numeric":
            step = " step %d" % q.step if q.step > 1 else ""
            return ["%d..%d%s" % (q.minimum, q.maximum, step)]
        return ["text, %d characters max" % q.size]

    # --- finding -----------------------------------------------------------

    def find(self, key):
        """One question by id (0x2BB2) or exact prompt; refuse ambiguity."""
        if key.lower().startswith("0x"):
            q = self.by_id.get(int(key, 16))
            if not q:
                raise Refused("no question with id %s" % key)
            return q
        matches = [q for q in self.menu.questions if q.prompt.lower() == key.lower()]
        if not matches:
            raise Refused("no setting named %r (try: list --filter)" % key)
        if len(matches) > 1:
            lines = ["%#06x  %s" % (q.question_id, " > ".join(q.path)) for q in matches]
            raise Refused("%d settings are named %r; name one by id:\n  %s"
                          % (len(matches), key, "\n  ".join(lines)))
        return matches[0]

    # --- changing ----------------------------------------------------------

    def parse(self, q, text):
        """The stored integer for a value typed by a person, or Refused."""
        if q.kind == "string":
            raise Refused("%r is a typed-in field: the setup menu converts it through "
                          "firmware code this tool cannot run, so it is not written raw"
                          % q.prompt)
        if q.kind == "checkbox":
            if text.lower() not in CHECKBOX_WORDS:
                raise Refused("%r takes Enabled or Disabled" % q.prompt)
            return CHECKBOX_WORDS[text.lower()]
        if q.kind == "oneof":
            visible = [o for o in q.options if not self.states(o.conditions)]
            for o in visible:
                if o.name.lower() == text.lower() or str(o.value) == text:
                    return o.value
            raise Refused("%r is not offered for %r; offered now: %s"
                          % (text, q.prompt, ", ".join(o.name for o in visible) or "none"))
        try:
            number = int(text, 0)
        except ValueError:
            raise Refused("%r takes a number" % q.prompt) from None
        if not q.minimum <= number <= q.maximum:
            raise Refused("%d is outside %d..%d" % (number, q.minimum, q.maximum))
        if q.step > 1 and (number - q.minimum) % q.step:
            raise Refused("%d is not on a step of %d from %d" % (number, q.step, q.minimum))
        return number

    def plan(self, q, text):
        """(variable name, attributes, new bytes, old display, new display)."""
        if q.flags & ifr.QF_READ_ONLY:
            raise Refused("%r is read-only in the setup menu" % q.prompt)
        blocking = {s: v for s, v in self.states(q.conditions).items() if s in BLOCKING_STATES}
        if blocking:
            certain = [s for s, v in blocking.items() if v is True]
            raise Refused("%r is %s in the setup menu right now" % (q.prompt, " and ".join(certain))
                          if certain else "%r may be %s: its condition reads a value Linux cannot "
                          "see, so its state is unknown" % (q.prompt, " or ".join(blocking)))
        entry = self.store(q.varstore_id)
        if not entry:
            raise Refused("the variable holding %r is not visible from Linux" % q.prompt)
        vs, attributes, data = entry
        if len(data) != vs.size:
            raise Refused("%s is %d bytes but this menu describes %d: the IFR does not "
                          "belong to the running BIOS" % (vs.name, len(data), vs.size))
        number = self.parse(q, text)
        new = bytearray(data)
        if q.kind == "checkbox":
            new[q.offset] = (new[q.offset] & 0xFE) | number
        else:
            new[q.offset:q.offset + q.size] = number.to_bytes(q.size, "little")
        old_display = self.display(q)
        shown = next((o.name for o in q.options if o.value == number), None) if q.kind == "oneof" \
            else ("Enabled" if number else "Disabled") if q.kind == "checkbox" else str(number)
        return vs.efivar, attributes, bytes(new), old_display, shown
