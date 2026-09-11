"""Config tests for the [chrome_setup] table."""

from __future__ import annotations

from pathlib import Path

import pytest
from config_helpers import (
    assert_config_error,
    base_config,
    load_checked_config,
    write_config,
)

CHROME_BLOCK = (
    "[chrome_setup]\n"
    'username = "i"\n'
    'home_dir = "/home/i"\n'
    'package_name = "google-chrome-stable"\n'
    'process_name = "chrome"\n'
    'appletsrc_file_name = "plasma-org.kde.plasma.desktop-appletsrc"\n'
    'appletsrc_relative_path = ".config/plasma-org.kde.plasma.desktop-appletsrc"\n'
    'appletsrc_launchers_key = "launchers"\n'
    'taskbar_plugin_names = ["org.kde.plasma.icontasks", "org.kde.plasma.taskmanager"]\n'
    'panel_launcher_id = "applications:google-chrome.desktop"\n'
    'panel_restart_command = ["systemctl", "--user", "--machine", "{username}@.host", "restart", "plasma-plasmashell.service"]\n'
    'settings_repo_url = "https://github.com/Borodin-Atamanov/chromium-default-settings.git"\n'
    'settings_repo_ref = "main"\n'
    'settings_dir = "/var/cache/pyntara/chromium-settings"\n'
    'settings_system_tree_relative_path = "system"\n'
    'preferences_relative_path = "Default/Preferences"\n'
    'profile_dir_relative_path = ".config/google-chrome"\n'
    'keyring_temp_dir_prefix = "pyntara-chrome-"\n'
    'apt_source_template_file_name = "google-chrome.sources"\n'
    'launch_flags = ["--proxy-server={proxy_server}", "--user-data-dir={user_data_dir}", "--remote-debugging-port={cdp_port}", "--remote-debugging-address={cdp_address}"]\n'
    'keyring_dearmor_command = ["gpg", "--dearmor", "--output", "{output}", "{armored}"]\n'
    'settings_clone_command = ["git", "clone", "--quiet", "--depth", "1", "--branch", "{ref}", "{url}", "{dir}"]\n'
    'settings_fetch_command = ["git", "-C", "{dir}", "fetch", "--quiet", "origin", "{ref}"]\n'
    'settings_revision_command = ["git", "-C", "{dir}", "rev-parse", "{revision}"]\n'
    'settings_reset_command = ["git", "-C", "{dir}", "reset", "--hard", "{revision}"]\n'
    'process_check_command = ["pgrep", "-x", "{process_name}"]\n'
    'mount_check_command = ["findmnt", "--noheadings", "--output", "TARGET,FSROOT", "--target", "{path}"]\n'
    'mount_reload_command = ["systemctl", "daemon-reload"]\n'
    'mount_enable_command = ["systemctl", "enable", "--now", "{unit_name}"]\n'
    'menu_refresh_command = ["runuser", "-u", "{username}", "--", "env", "HOME={home_dir}", "XDG_MENU_PREFIX=plasma-", "kbuildsycoca6", "--noincremental"]\n'
    'mount_unit_template_file_name = "mount_chrome_user_dir.service"\n'
    'system_root = "/"\n'
    'apt_source_path = "/etc/apt/sources.list.d/google-chrome.sources"\n'
    'keyring_path = "/usr/share/keyrings/google-chrome.gpg"\n'
    'google_key_url = "https://dl.google.com/linux/linux_signing_key.pub"\n'
    'desktop_source_path = "/usr/share/applications/google-chrome.desktop"\n'
    'desktop_override_path = "/usr/local/share/applications/google-chrome.desktop"\n'
    'profile_mirror_path = "/home/i/.config/google-chrome-cdp"\n'
    'mount_service_unit_name = "mount_chrome_user_dir.service"\n'
    "cdp_port = 19222\n"
    'cdp_address = "127.0.0.1"\n'
)


@pytest.mark.parametrize(
    "content",
    [
        # username is a number, not a string
        base_config().replace('username = "i"\n', "username = 7\n"),
        # username is empty
        base_config().replace('username = "i"\n', 'username = ""\n'),
        # settings_repo_url is a number, not a string
        base_config().replace(
            'settings_repo_url = "https://github.com/Borodin-Atamanov/chromium-default-settings.git"\n',
            "settings_repo_url = 7\n",
        ),
        # settings_repo_ref is empty
        base_config().replace('settings_repo_ref = "main"\n', 'settings_repo_ref = ""\n'),
        # settings_dir is a number, not a string
        base_config().replace(
            'settings_dir = "/var/cache/pyntara/chromium-settings"\n',
            "settings_dir = 7\n",
        ),
        # system_root is a number, not a string
        base_config().replace('system_root = "/"\n', "system_root = 7\n"),
        # apt_source_path is empty
        base_config().replace(
            'apt_source_path = "/etc/apt/sources.list.d/google-chrome.sources"\n',
            'apt_source_path = ""\n',
        ),
        # keyring_path is a number, not a string
        base_config().replace(
            'keyring_path = "/usr/share/keyrings/google-chrome.gpg"\n',
            "keyring_path = 7\n",
        ),
        # google_key_url is empty
        base_config().replace(
            'google_key_url = "https://dl.google.com/linux/linux_signing_key.pub"\n',
            'google_key_url = ""\n',
        ),
        # desktop_source_path is a number, not a string
        base_config().replace(
            'desktop_source_path = "/usr/share/applications/google-chrome.desktop"\n',
            "desktop_source_path = 7\n",
        ),
        # desktop_override_path is empty
        base_config().replace(
            'desktop_override_path = "/usr/local/share/applications/google-chrome.desktop"\n',
            'desktop_override_path = ""\n',
        ),
        # profile_mirror_path is a number, not a string
        base_config().replace(
            'profile_mirror_path = "/home/i/.config/google-chrome-cdp"\n',
            "profile_mirror_path = 7\n",
        ),
        # mount_service_unit_name is empty
        base_config().replace(
            'mount_service_unit_name = "mount_chrome_user_dir.service"\n',
            'mount_service_unit_name = ""\n',
        ),
        # cdp_port is zero
        base_config().replace("cdp_port = 19222\n", "cdp_port = 0\n"),
        # cdp_port is negative
        base_config().replace("cdp_port = 19222\n", "cdp_port = -1\n"),
        # cdp_port is a string, not an integer
        base_config().replace("cdp_port = 19222\n", 'cdp_port = "19222"\n'),
        # cdp_address is a number, not a string
        base_config().replace('cdp_address = "127.0.0.1"\n', "cdp_address = 7\n"),
        # file_mode is a number, not the readable octal string
        base_config().replace('file_mode = "0644"\n', "file_mode = 420\n"),
        # file_mode has not the four digits a mode is written with
        base_config().replace('file_mode = "0644"\n', 'file_mode = "644"\n'),
        # package_name is empty
        base_config().replace(
            'package_name = "google-chrome-stable"', 'package_name = ""'
        ),
        # process_name is a number, not a string
        base_config().replace('process_name = "chrome"', "process_name = 7"),
        # appletsrc_file_name is empty
        base_config().replace(
            'appletsrc_file_name = "plasma-org.kde.plasma.desktop-appletsrc"',
            'appletsrc_file_name = ""',
        ),
        # appletsrc_relative_path is empty
        base_config().replace(
            'appletsrc_relative_path = ".config/plasma-org.kde.plasma.desktop-appletsrc"',
            'appletsrc_relative_path = ""',
        ),
        # appletsrc_launchers_key is empty
        base_config().replace(
            'appletsrc_launchers_key = "launchers"', 'appletsrc_launchers_key = ""'
        ),
        # taskbar_plugin_names is a string, not an array
        base_config().replace(
            'taskbar_plugin_names = ["org.kde.plasma.icontasks", "org.kde.plasma.taskmanager"]',
            'taskbar_plugin_names = "org.kde.plasma.icontasks"',
        ),
        # panel_launcher_id is empty
        base_config().replace(
            'panel_launcher_id = "applications:google-chrome.desktop"',
            'panel_launcher_id = ""',
        ),
        # panel_restart_command is an empty array
        base_config().replace(
            'panel_restart_command = ["systemctl", "--user", "--machine", "{username}@.host", "restart", "plasma-plasmashell.service"]',
            "panel_restart_command = []",
        ),
        # settings_system_tree_relative_path is empty
        base_config().replace(
            'settings_system_tree_relative_path = "system"',
            'settings_system_tree_relative_path = ""',
        ),
        # preferences_relative_path is empty
        base_config().replace(
            'preferences_relative_path = "Default/Preferences"',
            'preferences_relative_path = ""',
        ),
        # profile_dir_relative_path is empty
        base_config().replace(
            'profile_dir_relative_path = ".config/google-chrome"',
            'profile_dir_relative_path = ""',
        ),
        # keyring_temp_dir_prefix is empty
        base_config().replace(
            'keyring_temp_dir_prefix = "pyntara-chrome-"',
            'keyring_temp_dir_prefix = ""',
        ),
        # apt_source_template_file_name is empty
        base_config().replace(
            'apt_source_template_file_name = "google-chrome.sources"',
            'apt_source_template_file_name = ""',
        ),
        # launch_flags is an empty array
        base_config().replace(
            'launch_flags = ["--proxy-server={proxy_server}", "--user-data-dir={user_data_dir}", "--remote-debugging-port={cdp_port}", "--remote-debugging-address={cdp_address}"]',
            "launch_flags = []",
        ),
        # keyring_dearmor_command holds an empty argument
        base_config().replace(
            'keyring_dearmor_command = ["gpg", "--dearmor", "--output", "{output}", "{armored}"]',
            'keyring_dearmor_command = ["gpg", ""]',
        ),
        # settings_clone_command is a string, not an array
        base_config().replace(
            'settings_clone_command = ["git", "clone", "--quiet", "--depth", "1", "--branch", "{ref}", "{url}", "{dir}"]',
            'settings_clone_command = "git clone"',
        ),
        # settings_fetch_command is an empty array
        base_config().replace(
            'settings_fetch_command = ["git", "-C", "{dir}", "fetch", "--quiet", "origin", "{ref}"]',
            "settings_fetch_command = []",
        ),
        # settings_revision_command is an empty array
        base_config().replace(
            'settings_revision_command = ["git", "-C", "{dir}", "rev-parse", "{revision}"]',
            "settings_revision_command = []",
        ),
        # settings_reset_command holds an empty argument
        base_config().replace(
            'settings_reset_command = ["git", "-C", "{dir}", "reset", "--hard", "{revision}"]',
            'settings_reset_command = ["git", ""]',
        ),
        # process_check_command is an empty array
        base_config().replace(
            'process_check_command = ["pgrep", "-x", "{process_name}"]',
            "process_check_command = []",
        ),
        # mount_check_command is an empty array
        base_config().replace(
            'mount_check_command = ["findmnt", "--noheadings", "--output", "TARGET,FSROOT", "--target", "{path}"]',
            "mount_check_command = []",
        ),
        # mount_reload_command is an empty array
        base_config().replace(
            'mount_reload_command = ["systemctl", "daemon-reload"]',
            "mount_reload_command = []",
        ),
        # mount_enable_command is an empty array
        base_config().replace(
            'mount_enable_command = ["systemctl", "enable", "--now", "{unit_name}"]',
            "mount_enable_command = []",
        ),
        # menu_refresh_command is an empty array
        base_config().replace(
            'menu_refresh_command = ["runuser", "-u", "{username}", "--", "env", "HOME={home_dir}", "XDG_MENU_PREFIX=plasma-", "kbuildsycoca6", "--noincremental"]',
            "menu_refresh_command = []",
        ),
        # mount_unit_template_file_name is empty
        base_config().replace(
            'mount_unit_template_file_name = "mount_chrome_user_dir.service"',
            'mount_unit_template_file_name = ""',
        ),
    ],
)
def test_load_config_wrong_types_raise(tmp_path: Path, content: str) -> None:
    assert_config_error(tmp_path, content)


def test_load_config_missing_chrome_section_raises(tmp_path: Path) -> None:
    # The section is mandatory: without it the task has no target user,
    # repository or CDP port (architecture contract, Configuration).
    assert_config_error(
        tmp_path,
        base_config().replace(CHROME_BLOCK, ""),
        match="\\[chrome_setup\\]",
    )


def test_load_config_typed_values(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, base_config())
    config = load_checked_config(config_path)
    assert config.chrome_setup.username == "i"
    assert config.chrome_setup.home_dir == "/home/i"
    assert config.chrome_setup.settings_repo_url == (
        "https://github.com/Borodin-Atamanov/chromium-default-settings.git"
    )
    assert config.chrome_setup.settings_repo_ref == "main"
    assert config.chrome_setup.settings_dir == Path(
        "/var/cache/pyntara/chromium-settings"
    )
    assert config.chrome_setup.system_root == Path("/")
    assert config.chrome_setup.apt_source_path == Path(
        "/etc/apt/sources.list.d/google-chrome.sources"
    )
    assert config.chrome_setup.keyring_path == Path(
        "/usr/share/keyrings/google-chrome.gpg"
    )
    assert config.chrome_setup.google_key_url == (
        "https://dl.google.com/linux/linux_signing_key.pub"
    )
    assert config.chrome_setup.desktop_source_path == Path(
        "/usr/share/applications/google-chrome.desktop"
    )
    assert config.chrome_setup.desktop_override_path == Path(
        "/usr/local/share/applications/google-chrome.desktop"
    )
    assert config.chrome_setup.profile_mirror_path == Path(
        "/home/i/.config/google-chrome-cdp"
    )
    assert config.chrome_setup.mount_service_unit_name == (
        "mount_chrome_user_dir.service"
    )
    assert config.chrome_setup.cdp_port == 19222
    assert config.chrome_setup.cdp_address == "127.0.0.1"
    assert config.chrome_setup.file_mode == 0o644
