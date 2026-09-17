"""Values several tasks need.

A value two or more tasks need is written once and read here; a value one task
needs lives in the module of that task. A task that reads this module checks it
together with its own, so a missing shared value is reported in plain words
like any other. If a task ever needs another number than the shared one, it
declares its own value in its own module, and that is a decision, not a
convenience.

A file mode, a path or a command shared by several tasks belongs here by the
same rule.
"""

from __future__ import annotations

# Seconds the dpkg status query may take while a task checks whether a package
# is installed.
PACKAGE_STATUS_TIMEOUT_SECONDS: int = 30

# Retry attempts after a failed package install; the total number of attempts
# is this count plus one.
PACKAGE_INSTALL_RETRIES: int = 3

# The names the tasks read. The list lives next to the values it names and is
# read by every task that uses this module.
READ_VALUE_NAMES: tuple[str, ...] = (
    "PACKAGE_STATUS_TIMEOUT_SECONDS",
    "PACKAGE_INSTALL_RETRIES",
)
