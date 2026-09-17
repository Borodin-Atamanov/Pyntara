"""Values of the telegram_setup task.

Telegram Desktop is installed for the desktop user from the official static Linux
build, the only build with the built-in auto-update, so the client updates itself
and the task never chases a version. The install directory, the launcher entry
and the icon live under the home of that user, because the built-in updater
rewrites the two binaries in place; the desktop user pair comes from the shared
module common. The download link answers a redirect to the archive of the newest
release, and the name of that archive is the idempotency record: a rerun that
finds the archive of the release the link points at plus an installed binary
changes nothing.
"""

from __future__ import annotations

from pathlib import Path

# Root cache that keeps the archive of the last installed release.
DOWNLOAD_DIR: Path = Path("/var/cache/pyntara/telegram")

# Official download link that redirects to the newest tsetup archive.
LATEST_URL: str = "https://telegram.org/dl/desktop/linux"

# Command that resolves the download url the link above redirects to. A HEAD
# request follows the redirect chain without transferring the archive and
# reports the final url through --write-out, which the task reads from the
# command output; the retry and timeout flags of the engine curl settings are
# inserted before the URL.
LATEST_URL_COMMAND: tuple[str, ...] = (
    "curl",
    "--fail",
    "--silent",
    "--show-error",
    "--head",
    "--location",
    "--output",
    "/dev/null",
    "--write-out",
    "%{url_effective}",
)

# Single-attempt probe of the download host, run before the redirect is
# resolved, with the url as its last argument. A host that is blocked never
# answers, and the retry budget of the resolve above turns that silence into one
# connect timeout per attempt, which is minutes on a machine whose network drops
# the packets; the probe asks once with a short budget instead. It carries no
# retry flag on purpose: an answer, however slow, means the host is reachable,
# and the resolve then retries as usual, so a slow link is never mistaken for a
# blocked one. The --head keeps the probe from transferring the archive it is
# asking about.
REACHABILITY_PROBE_COMMAND: tuple[str, ...] = (
    "curl",
    "--silent",
    "--show-error",
    "--head",
    "--location",
    "--output",
    "/dev/null",
    "--connect-timeout",
    "{timeout_seconds}",
    "--max-time",
    "{timeout_seconds}",
)

# Seconds that probe may spend on its single attempt: one connection and one
# answer. A slow but working link answers a handshake in a few seconds, so this
# value separates a slow host from a blocked one without calling the slow one
# blocked.
REACHABILITY_PROBE_TIMEOUT_SECONDS: int = 15

# Official Telegram icon downloaded for the launcher entry.
ICON_URL: str = (
    "https://raw.githubusercontent.com/telegramdesktop/tdesktop/dev/"
    "Telegram/Resources/art/icon512.png"
)

# Relative paths under the home of the desktop user: the install directory, the
# launcher entry and the icon. The install directory must be writable by that
# user, because the built-in updater replaces the two binaries in place.
INSTALL_DIR_RELATIVE_PATH: str = ".local/share/Telegram"
LAUNCHER_RELATIVE_PATH: str = ".local/share/applications/telegramdesktop.desktop"
ICON_RELATIVE_PATH: str = ".local/share/icons/telegram-desktop.png"

# Name of the client binary, of the updater binary and of the directory inside
# the extracted archive that holds them.
BINARY_FILE_NAME: str = "Telegram"
UPDATER_FILE_NAME: str = "Updater"
ARCHIVE_DIRECTORY_NAME: str = "Telegram"

# Prefix of the temporary directory the archive is extracted to.
EXTRACT_DIR_PREFIX: str = "pyntara-telegram-"

# Launcher entry template under task_data/telegram_setup/ of the clone, with
# $binary and $icon as its placeholders.
LAUNCHER_TEMPLATE_FILE_NAME: str = "telegramdesktop.desktop"

# Command that extracts the downloaded archive into the temporary directory,
# with the archive path and the directory as its placeholders. tar detects the
# compression of the archive by itself, so neither the name nor the format of
# the release archive is assumed.
TAR_EXTRACT_COMMAND: tuple[str, ...] = (
    "tar",
    "--extract",
    "--file",
    "{archive}",
    "--directory",
    "{extract_dir}",
)

# Modes of the three deployed files: the launcher entry and the icon are read by
# the desktop, the two binaries are executed by the user.
LAUNCHER_FILE_MODE: int = 0o644
ICON_FILE_MODE: int = 0o644
EXECUTABLE_FILE_MODE: int = 0o755

# The names the task reads. The list lives next to the values it names, the task
# reads it from here and reports the names this module does not declare, instead
# of stopping on a Python error. The desktop user pair comes from the shared
# module common.
READ_VALUE_NAMES: tuple[str, ...] = (
    "DOWNLOAD_DIR",
    "LATEST_URL",
    "LATEST_URL_COMMAND",
    "REACHABILITY_PROBE_COMMAND",
    "REACHABILITY_PROBE_TIMEOUT_SECONDS",
    "ICON_URL",
    "INSTALL_DIR_RELATIVE_PATH",
    "LAUNCHER_RELATIVE_PATH",
    "ICON_RELATIVE_PATH",
    "BINARY_FILE_NAME",
    "UPDATER_FILE_NAME",
    "ARCHIVE_DIRECTORY_NAME",
    "EXTRACT_DIR_PREFIX",
    "LAUNCHER_TEMPLATE_FILE_NAME",
    "TAR_EXTRACT_COMMAND",
    "LAUNCHER_FILE_MODE",
    "ICON_FILE_MODE",
    "EXECUTABLE_FILE_MODE",
)
