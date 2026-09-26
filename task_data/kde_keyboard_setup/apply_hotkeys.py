
"""Give keyboard combinations to named actions of the running daemon.

The client is started with one JSON argument that carries a list of
changes. Every change names a component, one action of that component and
the combinations the action must own; a combination is written in the
portable form KDE stores, and the client turns it into the combined Qt
key code with the Qt bindings, so no caller needs a table of hand written
codes. An empty combination list means the action must own no key at all.

The work happens in two phases. The first phase collects the combinations
of the whole request and takes each of them from every action that holds
it, leaving those actions their other combinations, because the daemon has
no call that frees a combination: freeing is giving the holder its own
list without that combination, so the holder is found first. The second
phase registers every action of the request and gives it exactly the
combinations it was asked for. Registering is the call the KGlobalAccel
client library makes before it sets a shortcut, and measured on Kubuntu
26.04 the daemon refuses a combination for an action it was never told
about, while it stores the combination of a registered action whether or
not that action appears in the list of its component. No entry in a
configuration file is needed for the combination to work in a running
session: kwin, which switches the keyboard layout, reads the combination
of a layout action when it starts.

The reply carries, per change in the order of the request, the codes that
were requested, the codes the action held before and the codes it holds
after, the combinations Qt could not read, and whether the daemon lists
the action. An empty slot of the daemon state and a combination a change
names twice are not keys: the daemon keeps a placeholder for a slot it
granted nothing to, and it grants the same combination once, so both are
left out of the reported codes.

The bus name, the object path and the interface of the daemon arrive as
substitutions of the engine table, so the client names no interface of the
desktop itself.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import dbus
from PyQt6.QtGui import QKeySequence

bus = dbus.SessionBus()
daemon = bus.get_object("$kglobalaccel_bus_name", "$kglobalaccel_object_path")
iface = dbus.Interface(daemon, "$kglobalaccel_interface_name")

# The kind of match the holder query is asked with. Measured on Kubuntu
# 26.04: the kind zero answers with the action that holds the combination,
# the other two answer with nothing.
HOLDER_MATCH_KIND: int = 0

# The fields of one holder as the daemon answers them, measured on Kubuntu
# 26.04: the unique and the friendly name of the action, the unique and the
# friendly name of the component, the unique and the friendly name of the
# context, the combinations the action holds, and its default combinations.
HOLDER_ACTION_UNIQUE: int = 0
HOLDER_ACTION_FRIENDLY: int = 1
HOLDER_COMPONENT_UNIQUE: int = 2
HOLDER_COMPONENT_FRIENDLY: int = 3
HOLDER_KEYS: int = 6

# The action names the daemon knows, per component, remembered because the
# question travels to the daemon and a payload may name one component twice.
known: dict[str, set[str]] = {}


def combined_code(text: str) -> int | None:
    """The combined Qt key code of a portable combination, or None.

    Qt reads the same spellings the KDE files store. A text Qt cannot
    read returns None instead of a guessed code, so an unsupported
    combination is reported and never assigned by mistake.
    """

    sequence = QKeySequence(text)
    if sequence.count() == 0:
        return None
    code: int = sequence[0].toCombined()
    return code if code > 0 else None


def key_sequence(code: int) -> dbus.Struct:
    """One key sequence as the daemon marshals it: a four int array."""

    return dbus.Struct(
        [
            dbus.Array(
                [
                    dbus.Int32(code),
                    dbus.Int32(0),
                    dbus.Int32(0),
                    dbus.Int32(0),
                ],
                signature="i",
            )
        ],
        signature=None,
    )


def lookup_codes(code: int) -> dbus.Array:
    """One combination as the calls that look a holder up take it."""

    return dbus.Array(
        [dbus.Int32(code), dbus.Int32(0), dbus.Int32(0), dbus.Int32(0)], signature="i"
    )


def action_id(change: dict[str, Any], action: str) -> list[str]:
    """The four parts the daemon addresses an action by.

    The unique parts select the action and they are unique inside the
    component, so the unique component name stands in for the friendly
    one as well; a change may name the friendly component explicitly.
    """

    component = str(change["component_unique"])
    return [
        component,
        action,
        str(change.get("component_friendly") or component),
        action,
    ]


def known_actions(component: str) -> set[str]:
    """The unique action names the daemon knows for one component."""

    if component not in known:
        known[component] = {
            str(entry[1]) for entry in iface.allActionsForComponent([component])
        }
    return known[component]


def current_keys(action: list[str]) -> list[int]:
    """The combined codes one action holds now.

    A code of zero is the placeholder of a slot the daemon granted
    nothing to, and a repeated code is one key, so both are left out: the
    caller compares what the action really holds.
    """

    codes: list[int] = []
    for sequence in iface.shortcutKeys(action):
        code = int(sequence[0][0])
        if code and code not in codes:
            codes.append(code)
    return codes


def give_keys(action: list[str], codes: list[int]) -> None:
    """Let one action own exactly the given codes and nothing else."""

    if codes:
        keys = dbus.Array([key_sequence(code) for code in codes], signature="(ai)")
    else:
        keys = dbus.Array([], signature="(ai)")
    iface.setForeignShortcutKeys(action, keys)


def register_action(action: list[str]) -> None:
    """Let the daemon accept combinations for one action.

    The call is the one the KGlobalAccel client library makes before it sets
    a shortcut, and a per-layout action of the keyboard layout switcher needs
    it: measured on Kubuntu 26.04, the daemon refuses a combination for an
    action it was never told about, and it stores the combination of a
    registered action whether or not the action appears in the list of its
    component.
    """

    iface.doRegister(action)


def holder_ids(code: int) -> list[list[str]]:
    """Every action that holds one combination, as daemon action ids.

    The holder query answers with every action that claims the combination,
    so a combination two actions fight over is freed from both. A daemon
    that answers the query with nothing still names one holder through the
    action list, which keeps the freeing working.
    """

    ids: list[list[str]] = []
    for entry in iface.globalShortcutsByKey(
        [lookup_codes(code)], [dbus.Int32(HOLDER_MATCH_KIND)]
    ):
        fields = [str(part) for part in entry]
        held = [int(code) for code in entry[HOLDER_KEYS]]
        if code not in held:
            continue
        candidate = [
            fields[HOLDER_COMPONENT_UNIQUE],
            fields[HOLDER_ACTION_UNIQUE],
            fields[HOLDER_COMPONENT_FRIENDLY],
            fields[HOLDER_ACTION_FRIENDLY],
        ]
        if candidate not in ids:
            ids.append(candidate)
    if ids:
        return ids
    listed = [str(part) for part in iface.actionList([lookup_codes(code)])]
    return [listed] if listed else []


def holders_now(code: int) -> list[str]:
    """The four parts of one action that holds a combination now, or empty.

    The caller reports them when a wanted combination did not reach its
    action: the other action is the reason, and naming it turns a refusal
    into something the user can act on.
    """

    ids = holder_ids(code)
    return ids[0] if ids else []


payload = json.loads(sys.argv[1])
plan: list[dict[str, Any]] = []
claimed: list[int] = []
for change in payload["changes"]:
    action: str = change["action"]
    requested: list[int] = []
    unsupported: list[str] = []
    for text in change["keys"]:
        code = combined_code(text)
        if code is None:
            unsupported.append(text)
        elif code not in requested:
            requested.append(code)
    plan.append(
        {
            "change": change,
            "action": action,
            "target": action_id(change, action),
            "requested": requested,
            "unsupported": unsupported,
        }
    )
    for code in requested:
        if code not in claimed:
            claimed.append(code)

# Phase one: every claimed combination leaves its holders, which keep their
# other combinations.
for code in claimed:
    for holder in holder_ids(code):
        held = current_keys(holder)
        if code in held:
            give_keys(holder, [key for key in held if key != code])

# Phase two: every action of the request owns what it was asked for, and an
# action whose combinations are all unreadable is left as it is.
results: list[dict[str, Any]] = []
for item in plan:
    change = item["change"]
    target: list[str] = item["target"]
    before = current_keys(target)
    if item["requested"] or not change["keys"]:
        register_action(target)
        give_keys(target, item["requested"])
    results.append(
        {
            "action": item["action"],
            "requested": item["requested"],
            "before": before,
            "after": current_keys(target),
            "unsupported": item["unsupported"],
            "held_elsewhere": [
                [code, *holders_now(code)]
                for code in item["requested"]
                if code not in current_keys(target)
            ],
            "missing": item["action"]
            not in known_actions(str(change["component_unique"])),
        }
    )

print(json.dumps({"results": results}))
