"""Create the KDE wallet of the desktop user without a dialog.

The task runs this client as the desktop user on the session bus of that user,
because the wallet belongs to the session. The client asks the wallet service
for the name of the wallet of this session and, when the wallet file does not
exist yet, creates the wallet through the entry point of the daemon that takes
a ready key. That entry point is the one the PAM module of the wallet uses; it
creates the wallet without any dialog, and that is the whole point, because
the ordinary open of the daemon asks the user for a password when the wallet
is missing.

The key handed over is the key of an empty password. The salt is a random file
of the length the PAM module of the wallet uses, and the key is the
PBKDF2 of the empty string over that salt with the same algorithm, iteration
count and length. Every later program derives the same key from the empty
password, so the wallet opens without a dialog for them as well.

A wallet that already exists is reported and left untouched: asking it to open
in order to find out whether it carries a password is the call that shows the
dialog. The answer is printed as KEY=VALUE lines, so the task reads a small
vocabulary instead of parsing a sentence.

Every name and every constant of the service below is substituted by the task
from the values module of the section, so none of them stands in this file.
"""

import hashlib
import os
import secrets

from gi.repository import Gio, GLib

WALLET_BUS_NAME = "$wallet_bus_name"
WALLET_OBJECT_PATH = "$wallet_object_path"
WALLET_INTERFACE_NAME = "$wallet_interface_name"
WALLET_NAME_METHOD_NAME = "$wallet_name_method_name"
DAEMON_BUS_NAME = "$daemon_bus_name"
DAEMON_OBJECT_PATH = "$daemon_object_path"
DAEMON_OPEN_METHOD_NAME = "$daemon_open_method_name"
DAEMON_OPEN_SIGNATURE = "$daemon_open_signature"
KEY_ALGORITHM = "$key_algorithm"
KEY_ITERATIONS = int("$key_iterations")
KEY_LENGTH_BYTES = int("$key_length_bytes")
SALT_LENGTH_BYTES = int("$salt_length_bytes")
WALLET_DIRECTORY_RELATIVE_PATH = "$wallet_directory_relative_path"
WALLET_FILE_SUFFIX = "$wallet_file_suffix"
WALLET_SALT_SUFFIX = "$wallet_salt_suffix"
OUTCOME_KEY = "$outcome_key"
DETAIL_KEY = "$detail_key"
OUTCOME_CREATED = "$outcome_created"
OUTCOME_EXISTS = "$outcome_exists"
OUTCOME_ERROR = "$outcome_error"

# Milliseconds one call of the wallet service may take. The daemon answers in
# milliseconds; the bound is here so a daemon that hung cannot hold the run.
CALL_TIMEOUT_MILLISECONDS = 30000

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def print_answer(outcome, detail=""):
    """Print the answer of this client in the protocol the task reads."""

    print(f"{OUTCOME_KEY}={outcome}", flush=True)
    if detail:
        print(f"{DETAIL_KEY}={detail}", flush=True)


def call(destination, path, interface, method, parameters):
    """One call of the wallet service, answered or raised."""

    return bus.call_sync(
        destination,
        path,
        interface,
        method,
        parameters,
        None,
        Gio.DBusCallFlags.NONE,
        CALL_TIMEOUT_MILLISECONDS,
        None,
    )


def wallet_name():
    """The name of the wallet of this session, asked from the wallet service.

    The question itself starts the daemon that creates the wallet, so it comes
    first and the name it answers is the name the daemon will use.
    """

    answer = call(
        WALLET_BUS_NAME,
        WALLET_OBJECT_PATH,
        WALLET_INTERFACE_NAME,
        WALLET_NAME_METHOD_NAME,
        GLib.Variant("()", ()),
    )
    return answer.unpack()[0]


def wallet_file_paths(name):
    """The directory of the wallets and the file of the named wallet."""

    directory = os.path.join(
        os.path.expanduser("~"), WALLET_DIRECTORY_RELATIVE_PATH
    )
    return directory, os.path.join(directory, name + WALLET_FILE_SUFFIX)


def salt_file_path(name):
    """The file that carries the salt of the key of the named wallet."""

    directory, _ = wallet_file_paths(name)
    return os.path.join(directory, name + WALLET_SALT_SUFFIX)


def read_or_write_salt(path):
    """The salt of the wallet, read when it exists and written when it does not.

    The salt of a wallet outlives its file, so an existing one is reused: a
    key derived from another salt would not open the wallet the daemon creates.
    A salt file of an unexpected length is reported instead of replaced,
    because replacing it would destroy a resource of the machine.
    """

    if os.path.exists(path):
        with open(path, "rb") as handle:
            salt = handle.read()
        if len(salt) != SALT_LENGTH_BYTES:
            raise ValueError(
                f"the salt file {path} carries {len(salt)} bytes, "
                f"expected {SALT_LENGTH_BYTES}"
            )
        return salt
    salt = secrets.token_bytes(SALT_LENGTH_BYTES)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(salt)
    os.chmod(path, 0o600)
    return salt


def key_for_empty_password(salt):
    """The key the daemon and every program derive from the empty password."""

    return hashlib.pbkdf2_hmac(
        KEY_ALGORITHM, b"", salt, KEY_ITERATIONS, KEY_LENGTH_BYTES
    )


def create_wallet(name, key):
    """Create the wallet from a ready key, without any dialog."""

    call(
        DAEMON_BUS_NAME,
        DAEMON_OBJECT_PATH,
        WALLET_INTERFACE_NAME,
        DAEMON_OPEN_METHOD_NAME,
        GLib.Variant(DAEMON_OPEN_SIGNATURE, (name, key, 0)),
    )


def main():
    try:
        name = wallet_name()
        _, wallet_path = wallet_file_paths(name)
        if os.path.exists(wallet_path):
            print_answer(OUTCOME_EXISTS, wallet_path)
            return
        salt = read_or_write_salt(salt_file_path(name))
        create_wallet(name, key_for_empty_password(salt))
        if not os.path.exists(wallet_path):
            print_answer(
                OUTCOME_ERROR,
                f"the wallet service did not create the wallet {wallet_path}",
            )
            return
        print_answer(OUTCOME_CREATED, wallet_path)
    except GLib.Error as error:
        print_answer(OUTCOME_ERROR, str(error))
    except (OSError, ValueError) as error:
        print_answer(OUTCOME_ERROR, str(error))


if __name__ == "__main__":
    main()
