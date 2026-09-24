"""Values of the keyring_setup task.

A machine that logs in automatically hands no password to PAM: the PAM
service of that path carries no wallet module, and the helper the desktop
starts at login only forwards the environment of a module that never ran. The
KDE wallet is then created by the first program that asks for it, and that
creation is a dialog: the daemon offers a new wallet and asks for a password.
A wallet created that way is protected, so every later program asks again.

The task takes the dialog out of the way by creating the wallet itself, before
any program asks for it. The daemon offers exactly one path for that, the
entry point the PAM module of the wallet uses, and that entry point receives a
ready key instead of asking for a password. The key is what the PAM module
computes from a password, and a machine that never types a password has the
empty string, so the key is PBKDF2-HMAC-SHA512 over a random salt with the
empty string as the password. The algorithm, the salt length, the iteration
count and the key length below are the constants of that PAM module, so the
key computed here is the key the daemon and every later program derive from
the empty password, and a wallet created with it opens without a dialog.

A wallet that already exists is left alone, because it may carry a password
and asking it to open is the very call that shows the dialog. Force mode
replaces it instead: the wallet files go to the trash of the desktop user and
the wallet is created again without a password, which leaves the machine in
the state a fresh installation plus a normal run would reach.
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

# The client under task_data/keyring_setup/ of the clone, and the seconds one
# call of it may take. The wallet service starts on the first question, so the
# bound has to cover that start and not only the call itself.
CLIENT_SCRIPT_FILE_NAME: str = "configure_wallet.py"
CLIENT_TIMEOUT_SECONDS: int = 60

# The service of the wallet, which names the wallet of the session, and the
# daemon behind it with the entry point that takes a ready key. The daemon
# does not answer its own bus name until something asks the wallet service, so
# the client asks the wallet service first, and that question is what starts
# the daemon as well.
WALLET_BUS_NAME: str = "org.kde.kwalletd6"
WALLET_OBJECT_PATH: str = "/modules/kwalletd6"
WALLET_INTERFACE_NAME: str = "org.kde.KWallet"
WALLET_NAME_METHOD_NAME: str = "networkWallet"
DAEMON_BUS_NAME: str = "org.kde.ksecretd"
DAEMON_OBJECT_PATH: str = "/ksecretd"
DAEMON_OPEN_METHOD_NAME: str = "pamOpen"
DAEMON_OPEN_SIGNATURE: str = "(sayi)"

# The key derivation of the PAM module of the wallet (kwallet-pam,
# pam_kwallet.c): PBKDF2-HMAC-SHA512, a random salt of 56 bytes, 50000
# iterations and a key of 56 bytes. The salt stands in the salt file below.
KEY_ALGORITHM: str = "sha512"
KEY_ITERATIONS: int = 50000
KEY_LENGTH_BYTES: int = 56
SALT_LENGTH_BYTES: int = 56

# The directory the daemon keeps its wallets in, below the home of the user,
# and the names of the files of one wallet: the wallet itself, the salt of its
# key and the cache of item attributes. The file of a wallet carries the name
# of the wallet, so force mode finds every file of every wallet by these
# endings.
WALLET_DIRECTORY_RELATIVE_PATH: str = ".local/share/kwalletd"
WALLET_FILE_SUFFIX: str = ".kwl"
WALLET_SALT_SUFFIX: str = ".salt"
WALLET_ATTRIBUTES_SUFFIX: str = "_attributes.json"

# The command that moves a file of the desktop user to that user's trash,
# given as its program and its argument. A wallet that force mode replaces may
# be wanted again, and no resource of a machine is ever removed for good.
TRASH_PROGRAM: str = "gio"
TRASH_SUBCOMMAND: str = "trash"

# The protocol between the client and the task: the client prints
# <outcome_key>=<word> and then <detail_key>=<text>, so the task reads one
# small vocabulary instead of parsing a sentence of the client.
OUTCOME_KEY: str = "outcome"
DETAIL_KEY: str = "detail"
OUTCOME_CREATED: str = "created"
OUTCOME_EXISTS: str = "exists"
OUTCOME_ERROR: str = "error"

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not declare,
# instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGES",
    "RUNUSER_COMMAND",
    "PYTHON_SCRIPT_COMMAND",
    "CLIENT_SCRIPT_FILE_NAME",
    "CLIENT_TIMEOUT_SECONDS",
    "WALLET_BUS_NAME",
    "WALLET_OBJECT_PATH",
    "WALLET_INTERFACE_NAME",
    "WALLET_NAME_METHOD_NAME",
    "DAEMON_BUS_NAME",
    "DAEMON_OBJECT_PATH",
    "DAEMON_OPEN_METHOD_NAME",
    "DAEMON_OPEN_SIGNATURE",
    "KEY_ALGORITHM",
    "KEY_ITERATIONS",
    "KEY_LENGTH_BYTES",
    "SALT_LENGTH_BYTES",
    "WALLET_DIRECTORY_RELATIVE_PATH",
    "WALLET_FILE_SUFFIX",
    "WALLET_SALT_SUFFIX",
    "WALLET_ATTRIBUTES_SUFFIX",
    "TRASH_PROGRAM",
    "TRASH_SUBCOMMAND",
    "OUTCOME_KEY",
    "DETAIL_KEY",
    "OUTCOME_CREATED",
    "OUTCOME_EXISTS",
    "OUTCOME_ERROR",
)
