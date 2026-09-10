"""[telegram_setup] table: the Telegram Desktop client."""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TelegramSetupConfig:
    """Telegram Desktop installed for the desktop user.

    username and home_dir identify the user who runs the client; the
    install directory, the launcher entry and the icon are derived under
    that home (install directory home_dir/.local/share/Telegram, launcher
    entry home_dir/.local/share/applications/telegramdesktop.desktop, icon
    home_dir/.local/share/icons/telegram-desktop.png). download_dir is the
    root cache that keeps the archive of the last installed version, whose
    name doubles as the idempotency record. latest_url is the official
    download link that redirects to the newest tsetup archive and is the
    single source of the latest release; icon_url is the official Telegram
    icon (docs/spec/telegram-setup.md).
    """

    username: str
    home_dir: str
    download_dir: Path
    latest_url: str
    icon_url: str
