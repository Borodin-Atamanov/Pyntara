
import json
import sys

import dbus

payload = json.loads(sys.argv[1])
component_unique = payload["component_unique"]
component_friendly = payload["component_friendly"]
assign = payload["assign"]


def combined_array(combined):
    return dbus.Array(
        [dbus.Int32(combined), dbus.Int32(0), dbus.Int32(0), dbus.Int32(0)],
        signature="i",
    )


def set_keys(action_id, combined):
    if combined:
        keys = dbus.Array(
            [dbus.Struct([combined_array(combined)], signature="(ai)")],
            signature="(ai)",
        )
    else:
        keys = dbus.Array([], signature="(ai)")
    iface.setForeignShortcutKeys(action_id, keys)


def owner_of(combined):
    sequence = dbus.Struct([combined_array(combined)], signature=None)
    result = list(iface.actionList(sequence))
    return [str(part) for part in result] if result else None


def read_keys(action_id):
    return [int(seq[0][0]) for seq in iface.shortcutKeys(action_id)]


bus = dbus.SessionBus()
daemon = bus.get_object("org.kde.kglobalaccel", "/kglobalaccel")
iface = dbus.Interface(daemon, "org.kde.KGlobalAccel")

before = {}
for action, combined in assign:
    before[action] = read_keys([component_unique, action, component_friendly, action])

owners = set()
for action, combined in assign:
    if not combined:
        continue
    owner = owner_of(combined)
    if owner and owner[1] != action:
        owners.add(tuple(owner))

for owner in sorted(owners):
    set_keys(list(owner), 0)

for action, combined in assign:
    set_keys([component_unique, action, component_friendly, action], combined)

after = {}
for action, combined in assign:
    after[action] = read_keys([component_unique, action, component_friendly, action])

print(json.dumps({"before": before, "after": after}))
