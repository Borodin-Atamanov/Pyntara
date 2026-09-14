import dbus

bus = dbus.SessionBus()
obj = bus.get_object('$kwin_bus_name', '$virtual_desktop_manager_object_path')
props = dbus.Interface(obj, '$dbus_properties_interface_name')
data = props.Get('$virtual_desktop_manager_interface_name', '$virtual_desktops_property_name')
for entry in data:
    fields = [getattr(part, 'pyobject', part) for part in entry]
    print(fields[1])
