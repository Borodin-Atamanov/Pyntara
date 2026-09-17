"""Values several tasks need.

A value two or more tasks need is written once and read here; a value one task
needs lives in the module of that task. A task that reads this module checks it
together with its own, so a missing shared value is reported in plain words
like any other. If a task ever needs another number than the shared one, it
declares its own value in its own module, and that is a decision, not a
convenience.

A file mode, a path, a command or a record type shared by several tasks belongs
here by the same rule.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SshDirective:
    """One directive of an ssh configuration file: a keyword and its value.

    Both ssh tasks keep their directive lists as tuples of these records, so
    the type lives here rather than in the values module of one of them, which
    would make the other section read a neighbour's module.
    """

    name: str
    value: str


# Seconds the dpkg status query may take while a task checks whether a package
# is installed.
PACKAGE_STATUS_TIMEOUT_SECONDS: int = 30

# Retry attempts after a failed package install; the total number of attempts
# is this count plus one.
PACKAGE_INSTALL_RETRIES: int = 3

# Paths of the source KeePass vaults, relative to the clone root. Two tasks
# resolve them: local_vault_setup builds the runtime vault from the first one
# that opens, and nextdns_setup_system_wide reads the NextDNS profiles from it.
SOURCE_VAULT_PRODUCTION: str = "secrets/production.vault"
SOURCE_VAULT_DEFAULT: str = "secrets/default.vault"

# The names the tasks read. The list lives next to the values it names and is
# read by every task that uses this module.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGE_STATUS_TIMEOUT_SECONDS",
    "PACKAGE_INSTALL_RETRIES",
    "SOURCE_VAULT_PRODUCTION",
    "SOURCE_VAULT_DEFAULT",
)
