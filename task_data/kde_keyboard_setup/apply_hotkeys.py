
"""Give keyboard combinations to named actions of the running daemon.

The client is started with one JSON argument that carries a list of
changes. Every change names a component, one action of that component and
the combinations the action must own; a combination is written in the
portable form KDE stores, and the client turns it into the combined Qt
key code with the Qt bindings, so no caller needs a table of hand written
codes. An empty combination list means the action must own no key at all.

A combination another action holds is taken from that action first, and
only that combination: the other combinations of the owner stay. The
reply carries, per change in the order of the request, the codes that were
requested, the codes the action held before and the codes it holds after,
plus the combinations Qt could not read and the changes whose action the
daemon does not know, so the caller decides whether the configured state
is reached. An empty slot of the daemon state and a combination a change
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


def owner_of(code: int) -> list[str] | None:
    """The action that holds one combination now, or None."""

    result = list(iface.actionList(key_sequence(code)))
    return [str(part) for part in result] if result else None


def take_from_owner(code: int, action: str) -> None:
    """Free one combination from the action that holds it, if a stranger.

    The owner is addressed by its four parts while the payload names the
    action of the change alone, so the second part of the owner is what
    is compared with that name.
    """

    owner = owner_of(code)
    if not owner or owner[1] == action:
        return
    remaining = [key for key in current_keys(owner) if key != code]
    give_keys(owner, remaining)


payload = json.loads(sys.argv[1])
results: list[dict[str, Any]] = []
for change in payload["changes"]:
    action: str = change["action"]
    target = action_id(change, action)
    report: dict[str, Any] = {
        "action": action,
        "requested": [],
        "before": [],
        "after": [],
        "unsupported": [],
        "missing": False,
    }
    if action not in known_actions(change["component_unique"]):
        report["missing"] = True
        results.append(report)
        continue
    for text in change["keys"]:
        code = combined_code(text)
        if code is None:
            report["unsupported"].append(text)
        elif code not in report["requested"]:
            report["requested"].append(code)
    if change["keys"] and not report["requested"]:
        # Not one combination of this change is readable: leave the
        # action exactly as it is, because clearing it would be wrong.
        report["before"] = current_keys(target)
        report["after"] = list(report["before"])
        results.append(report)
        continue
    for code in report["requested"]:
        take_from_owner(code, action)
    report["before"] = current_keys(target)
    give_keys(target, report["requested"])
    report["after"] = current_keys(target)
    results.append(report)

print(json.dumps({"results": results}))
