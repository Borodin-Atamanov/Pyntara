"""Release the hotkeys a KWin script owns in the running daemon.

The config rewrite alone applies at the next session start; the running
KGlobalAccel daemon holds the keys in memory, so this client asks it to
release them. It is started with the pairs group and action as arguments,
one pair per hotkey, and an empty shortcut list clears the assignment. The
bus name, the object path and the interface of the daemon arrive as
substitutions of the engine table, so the client names no interface of the
desktop itself.
"""

import sys

import dbus

bus = dbus.SessionBus()
obj = bus.get_object("$kglobalaccel_bus_name", "$kglobalaccel_object_path")
iface = dbus.Interface(obj, "$kglobalaccel_interface_name")
empty = dbus.Array([], signature="(ai)")
for index in range(1, len(sys.argv), 2):
    group = sys.argv[index]
    action = sys.argv[index + 1]
    iface.setForeignShortcutKeys([group, action, group, action], empty)
