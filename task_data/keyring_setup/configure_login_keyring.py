"""Give the login collection of the Secret Service an empty master password.

The task runs this client as the desktop user on the session bus of that
user. It opens a plain session, reads the alias of the default collection
and then either creates the login collection with an empty master password,
when the session has no default collection yet, or asks whether the existing
one opens with an empty password. The answer is printed as KEY=VALUE lines,
so the task reads a small vocabulary instead of parsing a sentence.

Every name of the secret service below is substituted by the task from the
values module of the section, so no name of the service stands in this file.
"""

from gi.repository import Gio, GLib

BUS_NAME = "$bus_name"
SERVICE_OBJECT_PATH = "$service_object_path"
SERVICE_INTERFACE_NAME = "$service_interface_name"
INTERNAL_INTERFACE_NAME = "$internal_interface_name"
LABEL_PROPERTY = "$label_property"
LOGIN_COLLECTION_LABEL = "$login_collection_label"
DEFAULT_ALIAS = "$default_alias"
SESSION_ALGORITHM = "$session_algorithm"
SECRET_CONTENT_TYPE = "$secret_content_type"
OUTCOME_KEY = "$outcome_key"
DETAIL_KEY = "$detail_key"
OUTCOME_CREATED = "$outcome_created"
OUTCOME_ALREADY_PASSWORDLESS = "$outcome_already_passwordless"
OUTCOME_PROTECTED = "$outcome_protected"
OUTCOME_ERROR = "$outcome_error"

# The path the service answers for an alias that names no collection.
EMPTY_PATH = "/"

# Milliseconds one call of the secret service may take. The daemon answers
# in milliseconds; the bound is here so a daemon that hung cannot hold the
# run.
CALL_TIMEOUT_MILLISECONDS = 30000

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def print_answer(outcome, detail=""):
    """Print the answer of this client in the protocol the task reads."""

    print(f"{OUTCOME_KEY}={outcome}", flush=True)
    if detail:
        print(f"{DETAIL_KEY}={detail}", flush=True)


def call(interface, method, parameters):
    """One call of the secret service, answered or raised."""

    return bus.call_sync(
        BUS_NAME,
        SERVICE_OBJECT_PATH,
        interface,
        method,
        parameters,
        None,
        Gio.DBusCallFlags.NONE,
        CALL_TIMEOUT_MILLISECONDS,
        None,
    )


def open_plain_session():
    """The object path of a plain session, which needs no key exchange."""

    answer = call(
        SERVICE_INTERFACE_NAME,
        "OpenSession",
        GLib.Variant("(sv)", (SESSION_ALGORITHM, GLib.Variant("s", ""))),
    )
    return answer.unpack()[1]


def read_default_collection():
    """The collection path the default alias names, or the empty path."""

    answer = call(
        SERVICE_INTERFACE_NAME, "ReadAlias", GLib.Variant("(s)", (DEFAULT_ALIAS,))
    )
    return answer.unpack()[0]


def empty_password(session_path):
    """The structure of a secret whose value is an empty master password.

    The structure is (session, parameters, value, content type), so an empty
    value is the empty password itself.
    """

    return (session_path, b"", b"", SECRET_CONTENT_TYPE)


def create_login_collection(session_path):
    """Create the login collection with an empty master password."""

    attributes = {LABEL_PROPERTY: GLib.Variant("s", LOGIN_COLLECTION_LABEL)}
    answer = call(
        INTERNAL_INTERFACE_NAME,
        "CreateWithMasterPassword",
        GLib.Variant("(a{sv}(oayays))", (attributes, empty_password(session_path))),
    )
    return answer.unpack()[0]


def unlock_with_empty_password(session_path, collection_path):
    """Ask the collection to open with an empty password.

    An empty password that opens the collection is the state the task wants:
    the collection then opens without a dialog on this and on every later
    start of the daemon. A collection protected by a password answers
    org.gnome.keyring.Error.Denied here, which the caller reports as the
    password that must be left alone.
    """

    try:
        call(
            INTERNAL_INTERFACE_NAME,
            "UnlockWithMasterPassword",
            GLib.Variant(
                "(o(oayays))",
                (collection_path, empty_password(session_path)),
            ),
        )
    except GLib.Error as error:
        return False, str(error)
    return True, ""


def main():
    try:
        session_path = open_plain_session()
        collection_path = read_default_collection()
        if collection_path == EMPTY_PATH:
            created_path = create_login_collection(session_path)
            named_path = read_default_collection()
            if named_path != created_path:
                print_answer(
                    OUTCOME_ERROR,
                    f"created {created_path} but the default alias names {named_path}",
                )
                return
            print_answer(OUTCOME_CREATED, created_path)
            return
        opens, detail = unlock_with_empty_password(session_path, collection_path)
        if opens:
            print_answer(OUTCOME_ALREADY_PASSWORDLESS, collection_path)
            return
        print_answer(OUTCOME_PROTECTED, detail)
    except GLib.Error as error:
        print_answer(OUTCOME_ERROR, str(error))


main()
