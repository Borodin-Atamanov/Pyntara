"""Release the hotkeys a KWin script owns in the running daemon.

The config rewrite alone applies at the next session start; the running
KGlobalAccel daemon holds the keys in memory, so this client asks it to
release them. It is started with the pairs group and action as arguments,
one pair per hotkey, and an empty shortcut list clears the assignment.
"""

import dbus
import sys

bus = dbus.SessionBus()
obj = bus.get_object("org.kde.kglobalaccel", "/kglobalaccel")
iface = dbus.Interface(obj, "org.kde.KGlobalAccel")
empty = dbus.Array([], signature="(ai)")
for index in range(1, len(sys.argv), 2):
    group = sys.argv[index]
    action = sys.argv[index + 1]
    iface.setForeignShortcutKeys([group, action, group, action], empty)
