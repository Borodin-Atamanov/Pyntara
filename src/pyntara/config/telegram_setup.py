"""[telegram_setup] table: the Telegram Desktop client."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TelegramSetupConfig:
    """Telegram Desktop installed for the desktop user.

    username and home_dir identify the user who runs the client; the
    install directory, the launcher entry and the icon are derived under
    that home from install_dir_relative_path, launcher_relative_path and
    icon_relative_path, and binary_file_name, updater_file_name and
    archive_directory_name name the two binaries inside the archive and
    inside the install directory. download_dir is the root cache that
    keeps the archive of the last installed version, whose name doubles as
    the idempotency record; partial_download_file_suffix marks the file a
    download is written to before it is renamed, extract_dir_prefix names
    the temporary directory the archive is unpacked into, and
    launcher_template_file_name is the launcher entry template under
    task_data/telegram_setup/ of the clone, rendered with the binary and
    the icon paths. latest_url is the official download link that
    redirects to the newest tsetup archive and is the single source of the
    latest release; icon_url is the official Telegram icon
    (docs/spec/telegram-setup.md).
    """

    username: str
    home_dir: str
    download_dir: Path
    latest_url: str
    icon_url: str
    install_dir_relative_path: str
    launcher_relative_path: str
    icon_relative_path: str
    binary_file_name: str
    updater_file_name: str
    archive_directory_name: str
    partial_download_file_suffix: str
    extract_dir_prefix: str
    launcher_template_file_name: str
    launcher_file_mode: int
    icon_file_mode: int
    executable_file_mode: int
