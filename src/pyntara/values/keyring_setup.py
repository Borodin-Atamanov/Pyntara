"""Values of the keyring_setup task.

A machine that logs in automatically never types the account password, and
/etc/pam.d/sddm-autologin carries no keyring module at all, so neither
gnome-keyring nor KWallet ever receives a password at login. The login
collection of the Secret Service is then protected by a password nobody
knows, and the first program that stores or reads a secret asks the user for
it. The task removes the question by giving that collection an empty master
password, which a machine under full disk encryption can afford.

The collection is created through the internal interface of gnome-keyring,
because the public CreateCollection of the Secret Service opens a prompt for
the password instead of taking one. The client that speaks the protocol runs
as the desktop user on the session bus of that user, because the secret
service belongs to the session and not to the root process of the run.

The label of the collection is lowercase on purpose: the identifier of a
collection is built from its label, and the login alias of gnome-keyring
points at the identifier "login", so the label "Login" would create a second
keyring named Login.keyring and leave the login collection alone.

A collection the login did not open is replaced when it is empty: its file
goes to the trash and the collection is created again without a password. A
collection that holds items is never touched, because replacing it would
throw them away, and a collection that the login did open is left alone
because its password is then in use and nothing asks for it.
"""

from __future__ import annotations

# The binding the client needs: Gio speaks DBus and GLib carries the variants
# of the calls.
PACKAGES: tuple[str, ...] = ("python3-gi",)

# The wrapper that runs a command as the desktop user, and the interpreter of
# the client. The interpreter is the one of the machine and not the one of the
# run, because the client imports the Gio binding of the system python.
RUNUSER_COMMAND: tuple[str, ...] = ("runuser", "-u", "{username}", "--")
PYTHON_SCRIPT_COMMAND: tuple[str, ...] = ("{python}", "-c")

# The client under task_data/keyring_setup/ of the clone, and the names of the
# secret service it is rendered with.
CLIENT_SCRIPT_FILE_NAME: str = "configure_login_keyring.py"
BUS_NAME: str = "org.freedesktop.secrets"
SERVICE_OBJECT_PATH: str = "/org/freedesktop/secrets"
SERVICE_INTERFACE_NAME: str = "org.freedesktop.Secret.Service"
INTERNAL_INTERFACE_NAME: str = (
    "org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface"
)
LABEL_PROPERTY: str = "org.freedesktop.Secret.Collection.Label"
COLLECTION_INTERFACE_NAME: str = "org.freedesktop.Secret.Collection"

# The login collection of gnome-keyring, the alias that names the default
# collection of a session, and the session and content types of a plain
# secret whose value is a password.
LOGIN_COLLECTION_LABEL: str = "login"
DEFAULT_ALIAS: str = "default"
SESSION_ALGORITHM: str = "plain"
SECRET_CONTENT_TYPE: str = "text/plain"

# The directory gnome-keyring keeps its collections in, below the data
# directory of the user, and the suffix of a collection file. A file carries
# the identifier of its collection, so the file of the collection under work
# is found from the object path the service reports.
KEYRING_DIRECTORY_NAME: str = "keyrings"
KEYRING_FILE_SUFFIX: str = ".keyring"

# The command that moves a file of the desktop user to that user's trash,
# given as its program and its argument. A collection that this run replaces
# may be wanted again, and no resource of a machine is ever removed for good.
TRASH_PROGRAM: str = "gio"
TRASH_SUBCOMMAND: str = "trash"

# The protocol between the client and the task: the client prints
# <outcome_key>=<word> and then <detail_key>=<text>, so the task reads one
# small vocabulary instead of parsing a sentence of the client.
OUTCOME_KEY: str = "outcome"
DETAIL_KEY: str = "detail"
OUTCOME_CREATED: str = "created"
OUTCOME_ALREADY_PASSWORDLESS: str = "already_passwordless"
OUTCOME_RECREATED: str = "recreated"
OUTCOME_OPENED_AT_LOGIN: str = "opened_at_login"
OUTCOME_PROTECTED: str = "protected"
OUTCOME_ERROR: str = "error"

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "RUNUSER_COMMAND",
    "PYTHON_SCRIPT_COMMAND",
    "CLIENT_SCRIPT_FILE_NAME",
    "BUS_NAME",
    "SERVICE_OBJECT_PATH",
    "SERVICE_INTERFACE_NAME",
    "INTERNAL_INTERFACE_NAME",
    "LABEL_PROPERTY",
    "COLLECTION_INTERFACE_NAME",
    "LOGIN_COLLECTION_LABEL",
    "DEFAULT_ALIAS",
    "SESSION_ALGORITHM",
    "SECRET_CONTENT_TYPE",
    "KEYRING_DIRECTORY_NAME",
    "KEYRING_FILE_SUFFIX",
    "TRASH_PROGRAM",
    "TRASH_SUBCOMMAND",
    "OUTCOME_KEY",
    "DETAIL_KEY",
    "OUTCOME_CREATED",
    "OUTCOME_ALREADY_PASSWORDLESS",
    "OUTCOME_RECREATED",
    "OUTCOME_OPENED_AT_LOGIN",
    "OUTCOME_PROTECTED",
    "OUTCOME_ERROR",
)
