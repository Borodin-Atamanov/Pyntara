import dbus

bus = dbus.SessionBus()
obj = bus.get_object('org.kde.KWin', '/VirtualDesktopManager')
props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
data = props.Get('org.kde.KWin.VirtualDesktopManager', 'desktops')
for entry in data:
    fields = [getattr(part, 'pyobject', part) for part in entry]
    print(fields[1])
