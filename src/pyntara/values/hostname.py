"""Values of the hostname task.

The task generates a random proquint hostname, writes it into
HOSTNAME_FILE and applies it to the running kernel through
SET_HOSTNAME_COMMAND, so socket.gethostname() returns the new name for
the dependent tasks (nextdns_setup_system_wide reads the hostname from the
kernel).
"""

from __future__ import annotations

# Path of the file that holds the hostname.
HOSTNAME_FILE: str = "/etc/hostname"

# Number of random bytes the generated hostname is encoded from: four bytes
# become two five-letter words joined by a dash.
RANDOM_BYTES: int = 4

# Command that applies the hostname to the running kernel.
SET_HOSTNAME_COMMAND: tuple[str, ...] = ("hostnamectl", "set-hostname")

# The names the task reads. The list lives next to the values it names, the
# task reads it from here and reports the names this module does not
# declare, instead of stopping on a Python error.
READ_VALUE_NAMES: tuple[str, ...] = (
    "HOSTNAME_FILE",
    "RANDOM_BYTES",
    "SET_HOSTNAME_COMMAND",
)
