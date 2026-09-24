"""Give the login collection of the Secret Service an empty master password.

The task runs this client as the desktop user on the session bus of that
user. It opens a plain session, reads the alias of the default collection
and then either creates the login collection with an empty master password,
when the session has no default collection yet, or asks whether the existing
one opens with an empty password. A collection that is protected, that the
login did not open and that holds no items is replaced: its file goes to the
trash of the user and the collection is created again without a password. A
collection that holds items is left alone. The answer is printed as
KEY=VALUE lines, so the task reads a small vocabulary instead of parsing a
sentence.

Every name of the secret service below is substituted by the task from the
values module of the section, so no name of the service stands in this file.
"""

import os
import subprocess

from gi.repository import Gio, GLib

BUS_NAME = "$bus_name"
SERVICE_OBJECT_PATH = "$service_object_path"
SERVICE_INTERFACE_NAME = "$service_interface_name"
INTERNAL_INTERFACE_NAME = "$internal_interface_name"
LABEL_PROPERTY = "$label_property"
COLLECTION_INTERFACE_NAME = "$collection_interface_name"
LOGIN_COLLECTION_LABEL = "$login_collection_label"
DEFAULT_ALIAS = "$default_alias"
SESSION_ALGORITHM = "$session_algorithm"
SECRET_CONTENT_TYPE = "$secret_content_type"
KEYRING_DIRECTORY_NAME = "$keyring_directory_name"
KEYRING_FILE_SUFFIX = "$keyring_file_suffix"
TRASH_PROGRAM = "$trash_program"
TRASH_SUBCOMMAND = "$trash_subcommand"
OUTCOME_KEY = "$outcome_key"
DETAIL_KEY = "$detail_key"
OUTCOME_CREATED = "$outcome_created"
OUTCOME_ALREADY_PASSWORDLESS = "$outcome_already_passwordless"
OUTCOME_RECREATED = "$outcome_recreated"
OUTCOME_OPENED_AT_LOGIN = "$outcome_opened_at_login"
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
    """Create the login collection with an empty master password.

    The alias is read back, so a creation that did not become the default
    collection of the session is reported instead of being taken for done.
    """

    attributes = {LABEL_PROPERTY: GLib.Variant("s", LOGIN_COLLECTION_LABEL)}
    answer = call(
        INTERNAL_INTERFACE_NAME,
        "CreateWithMasterPassword",
        GLib.Variant("(a{sv}(oayays))", (attributes, empty_password(session_path))),
    )
    created_path = answer.unpack()[0]
    named_path = read_default_collection()
    if named_path != created_path:
        raise GLib.Error(
            f"created {created_path} but the default alias names {named_path}"
        )
    return created_path


def collection_state(collection_path):
    """The locked flag of a collection and the number of its items.

    The flag is read before any unlock attempt, because it answers the
    question that decides the step: did this login give the collection its
    password. A login that did leaves the collection open, a login that did
    not leaves it locked.
    """

    answer = bus.call_sync(
        BUS_NAME,
        collection_path,
        "org.freedesktop.DBus.Properties",
        "GetAll",
        GLib.Variant("(s)", (COLLECTION_INTERFACE_NAME,)),
        None,
        Gio.DBusCallFlags.NONE,
        CALL_TIMEOUT_MILLISECONDS,
        None,
    )
    properties = answer.unpack()[0]
    return bool(properties.get("Locked")), len(properties.get("Items", []))


def keyring_file_path(collection_path):
    """The file gnome-keyring keeps a collection in.

    The identifier of a collection is the last part of its object path and
    the file carries that identifier, so the file of the collection under
    work is found from the object path and not from a name written here.
    """

    identifier = collection_path.rsplit("/", 1)[-1]
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
        "~/.local/share"
    )
    file_name = identifier + KEYRING_FILE_SUFFIX
    return os.path.join(data_home, KEYRING_DIRECTORY_NAME, file_name)


def replace_empty_collection(session_path, collection_path):
    """Move the file of an empty collection to the trash and create it again.

    A collection whose password nobody knows cannot be opened, so an empty
    one is replaced: the file goes to the trash of the user first, so
    nothing is destroyed, and the daemon notices the removal on its own, so
    the next creation with the same label becomes the login collection
    again. Returns (done, path or error text).
    """

    file_path = keyring_file_path(collection_path)
    if not os.path.exists(file_path):
        return False, f"no keyring file at {file_path}"
    try:
        subprocess.run(
            [TRASH_PROGRAM, TRASH_SUBCOMMAND, file_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        return False, f"cannot move {file_path} to the trash: {error.stderr.strip()}"
    except OSError as error:
        return False, f"cannot move {file_path} to the trash: {error}"
    return True, create_login_collection(session_path)


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
            print_answer(OUTCOME_CREATED, created_path)
            return
        locked, item_count = collection_state(collection_path)
        opens, detail = unlock_with_empty_password(session_path, collection_path)
        if opens:
            print_answer(OUTCOME_ALREADY_PASSWORDLESS, collection_path)
            return
        if not locked:
            print_answer(OUTCOME_OPENED_AT_LOGIN, collection_path)
            return
        if item_count:
            print_answer(OUTCOME_PROTECTED, detail)
            return
        replaced, replace_detail = replace_empty_collection(
            session_path, collection_path
        )
        if not replaced:
            print_answer(OUTCOME_ERROR, replace_detail)
            return
        print_answer(OUTCOME_RECREATED, replace_detail)
    except GLib.Error as error:
        print_answer(OUTCOME_ERROR, str(error))


main()
